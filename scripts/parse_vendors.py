"""Parse the operator's AP workbook into reviewable vendor fixtures.

Source: ``docs/Invoices-Acct-AP-for-Excel-Sep-21-26 (2).xlsx``.

Two sheets matter:

* ``Setup - Vendors`` - the master list. One row per vendor with its default
  payment term, payment method, contact details and a default account number.
* one tab per store (``Sol 16`` ... ``Sol 55``, plus ``Carls Junior``, which is
  store 21's second outlet). Each store tab repeats the vendor list with the
  **account number and terms that store actually has with that vendor** - the
  number differs per location, which is exactly the relationship the ERP needs
  to keep.

Outputs:

* ``fixtures/solrise_erp/vendors.csv``        - the master vendor list
* ``fixtures/solrise_erp/store_vendors.csv``  - one row per store x vendor link

Run:

    python3 scripts/parse_vendors.py [path/to/AP.xlsx]
"""

import csv
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(ROOT, "fixtures", "solrise_erp")
VENDORS_CSV = os.path.join(FIXTURES, "vendors.csv")
LINKS_CSV = os.path.join(FIXTURES, "store_vendors.csv")

DEFAULT_XLSX = os.path.join(ROOT, "docs", "Invoices-Acct-AP-for-Excel-Sep-21-26 (2).xlsx")
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
COLRE = re.compile(r"([A-Z]+)")

# Store tabs whose name does not carry the store number: the Carl's Jr outlet is
# inside store 21 (the same pairing the payroll register uses for "SOLRISE 21
# (Carl Jr.)"), not a store of its own.
TAB_STORE = {"Carls Junior": "21"}

# Store-tab spellings that differ from the master list, keyed by normalised name.
# Kept explicit and short rather than fuzzy-matched, so a genuine new vendor can
# never be silently merged into an existing one.
VENDOR_ALIASES = {
    "grandygalshoneytaffy": "Grundy Gal's Honey Taffy",
}

# Rows that sit under the vendor block but are not vendors.
NON_VENDOR = re.compile(r"^total\b", re.IGNORECASE)


def _col_index(ref):
    n = 0
    for ch in COLRE.match(ref).group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_workbook(path):
    """Return ``{sheet_name: grid}`` where a grid is a list of row lists."""
    with zipfile.ZipFile(path) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{NS}t"))
                      for si in root.findall(f"{NS}si")]
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        names = [s.get("name") for s in wb.iter(f"{NS}sheet")]
        files = sorted(
            (n for n in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
            key=lambda n: int(re.search(r"sheet(\d+)\.xml", n).group(1)),
        )
        out = {}
        for i, name in enumerate(names):
            if i >= len(files):
                break
            root = ET.fromstring(zf.read(files[i]))
            sd = root.find(f"{NS}sheetData")
            grid = []
            for row in list(sd):
                cells = {}
                for cell in row.findall(f"{NS}c"):
                    v = cell.find(f"{NS}v")
                    t = cell.get("t")
                    inline = cell.find(f"{NS}is")
                    if t == "inlineStr" and inline is not None:
                        val = "".join(x.text or "" for x in inline.iter(f"{NS}t"))
                    elif v is None:
                        val = None
                    elif t == "s":
                        val = shared[int(v.text)]
                    else:
                        val = v.text
                    cells[_col_index(cell.get("r") or "A")] = val
                grid.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
            out[name] = grid
    return out


def cell(grid, row, col):
    if row < 0 or row >= len(grid):
        return ""
    line = grid[row]
    if col >= len(line):
        return ""
    return (line[col] or "").strip()


def norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def master_vendors(wb):
    """The ``Setup - Vendors`` list as ``{normalised_name: row_dict}``."""
    grid = wb.get("Setup - Vendors", [])
    # header is the row whose first cell is "Vendor Name"
    hi = next((i for i, r in enumerate(grid) if r and (r[0] or "").strip() == "Vendor Name"), None)
    if hi is None:
        return {}
    out = {}
    for i in range(hi + 1, len(grid)):
        name = cell(grid, i, 0)
        if not name:
            continue
        rec = {
            "vendor_name": name,
            "category": "",
            "default_terms": cell(grid, i, 1),
            "payment_method": cell(grid, i, 2),
            "contact_info": cell(grid, i, 3),
            "address": cell(grid, i, 4),
            "phone": cell(grid, i, 5),
            "email": cell(grid, i, 6),
            "account_number": cell(grid, i, 7),
        }
        out[norm(name)] = rec
    return out


def store_vendor_links(wb, master):
    """One row per store x vendor, from every store tab."""
    links = []
    unmatched = {}
    for sheet, grid in wb.items():
        number = None
        if sheet.startswith("Sol "):
            number = sheet[4:].strip()
        elif sheet in TAB_STORE:
            number = TAB_STORE[sheet]
        if not number or not number.isdigit():
            continue
        store = "Solrise %s" % number
        hi = next((i for i, r in enumerate(grid) if r and (r[0] or "").strip() == "Vendor"), None)
        if hi is None:
            continue
        for i in range(hi + 1, len(grid)):
            name = cell(grid, i, 0)
            if not name or NON_VENDOR.match(name):
                break  # the vendor block ends at the first blank or the TOTAL row
            key = norm(name)
            if key in VENDOR_ALIASES:
                key = norm(VENDOR_ALIASES[key])
            if key not in master:
                unmatched.setdefault(name, set()).add(store)
                # A vendor a store buys from but the master list forgot: keep it
                # rather than drop the link, and report it below.
                master[key] = {k: "" for k in (
                    "vendor_name", "category", "default_terms", "payment_method",
                    "contact_info", "phone", "email", "address", "account_number")}
                master[key]["vendor_name"] = name
            links.append({
                "store": store,
                "vendor": master[key]["vendor_name"],
                "account_number": cell(grid, i, 1),
                "terms": cell(grid, i, 2),
            })
    return links, unmatched


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_XLSX
    if not os.path.isfile(path):
        raise SystemExit("workbook not found: %s" % path)
    wb = read_workbook(path)
    master = master_vendors(wb)
    links, unmatched = store_vendor_links(wb, master)
    os.makedirs(FIXTURES, exist_ok=True)

    fields = ["vendor_name", "category", "default_terms", "payment_method",
              "contact_info", "phone", "email", "address", "account_number"]
    with open(VENDORS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(master.values(), key=lambda r: r["vendor_name"].lower()))

    with open(LINKS_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["store", "vendor", "account_number", "terms"])
        w.writeheader()
        w.writerows(sorted(links, key=lambda r: (int(r["store"].split()[1]), r["vendor"].lower())))

    print("write : %s (%d vendors)" % (VENDORS_CSV, len(master)))
    print("write : %s (%d links)" % (LINKS_CSV, len(links)))
    print("stores: %d" % len({l["store"] for l in links}))
    if unmatched:
        print("\n!! vendor names in a store tab but NOT in the master list:")
        for name, stores in sorted(unmatched.items()):
            print("   %-32s %s" % (name, sorted(stores)))


if __name__ == "__main__":
    main()
