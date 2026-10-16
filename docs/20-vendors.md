# 20 - Vendors, and which store uses which

The operator's combined vendor list, and the vendors each store actually buys from -
the second part being the point: a store deals with **many** vendors, its account
number with each differs from the next store's, and today that lives only in a
spreadsheet.

| | |
|---|---|
| Source | `docs/Invoices-Acct-AP-for-Excel-Sep-21-26 (2).xlsx` |
| Committed copies | `fixtures/solrise_erp/vendors.csv` (master), `fixtures/solrise_erp/store_vendors.csv` (links) |
| Parser | `scripts/parse_vendors.py` (workbook -> the two CSVs) |
| Importer | `scripts/import_vendors.py`, wrapped by `scripts/import_vendors.sh` |
| DocTypes | `Vendor`, `Vendor Category` (master); `Store Vendor` (child table on `Customer`) |
| Vendors | 72 |
| Store links | 432, across 36 stores |

Read alongside [`docs/18-stores.md`](18-stores.md): a store **is** a `Customer`, so
"the vendors connected to this customer" hangs off that same `Customer`.

---

## 1. Why a custom `Vendor`, not `Supplier`

This deployment has no accounts-payable ledger: no `Company`, no fiscal year, no
purchase invoices against these parties. ERPNext's `Supplier` exists to feed that
machinery, and adopting it would drag in a Company, ledgers, payment terms and
permissions that nothing here uses. The operator asked for a small standalone
master instead, and got one: `Vendor` is a two-screen DocType in the `Solrise ERP`
module, and `Supplier` is left untouched.

If purchasing is ever added, a `Vendor` can be linked to (or promoted into) a
`Supplier` without changing what the stores already record.

## 2. What a row becomes

```
Vendor Name          -> Vendor.vendor_name      (the DocType name)
Default Terms        -> Vendor.default_terms
Payment Method       -> Vendor.payment_method
Contact Info/Phone   -> Vendor.phone
Email                -> Vendor.email
Address              -> Vendor.address
Account Number       -> Vendor.account_number   (the master list's default)
```

`Vendor.category` links to **`Vendor Category`**, an ordinary master the operator
owns. The importer seeds a starting set - `Beverage`, `Ice & Frozen`,
`Uniform & Laundry`, `Propane & Gas`, `Maintenance & Repair`, `Equipment & Service`
and so on - but **never assigns a category to a vendor**: the sheet has no category
column, and guessing "AC vs maintenance" from a vendor's name would put wrong data
in front of an accountant. Assign them in the Desk; the field is there for it.

## 3. The store ↔ vendor table

Every store tab repeats the vendor list with **that store's** account number and
terms - `Sol 51` carries 18 vendors, `Sol 53` carries 3. Those per-store values are
the relationship worth keeping, so they are stored on the store:

```
Customer.service_vendors  (child table of Store Vendor)
    vendor           -> Link to Vendor
    vendor_category  -> fetched from the Vendor
    account_number   -> this store's number with that vendor
    terms            -> this store's terms
    active           -> Check
    service_notes    -> free text ("AC", "grease trap", ...)
```

Open a **Customer** and the list of vendors connected to it is on the form - the
operator's "when I go to a customer, show me which vendors are connected". (The
operator's shorthand for this was "CTE"; it is implemented as a standard Frappe
child table, which is the mechanism that carries per-row detail like the account
number.)

`Customer.service_vendors` is a **Custom Field created in code** by
`solrise_erp.vendors.ensure_customer_vendor_field()` on every migrate - the same
reason `Customer.store_code` is created by its importer (docs/18 section 1): the
app's fixture directory carries no `custom_field.json`.

## 4. How to run it

```bash
# regenerate the CSVs from the workbook (dev machine, no site needed)
python3 scripts/parse_vendors.py

# load them (idempotent; safe after every deploy)
SITE_ENV=aws ./scripts/import_vendors.sh
SITE_ENV=local ./scripts/import_vendors.sh
```

The importer **creates only what is missing and fills only blanks**, so a category,
account number or note typed in the Desk survives a re-run.

## 5. Data notes, as found

* **72 vendors** in the committed master list: the 71 on `Setup - Vendors`, plus
  `Tour Ice`, which three store tabs buy from but the master list omits.
* **One alias folded.** `Sol 18` spells the master's `Grundy Gal's Honey Taffy` as
  `Grandy Gal's Honey Taffy`; the parser maps it (`VENDOR_ALIASES`) rather than
  creating a second vendor. Kept explicit, not fuzzy-matched, so a genuinely new
  vendor is never silently merged.
* **`Carls Junior` is not a store.** It is store 21's second tab (the same pairing
  the payroll register uses for `SOLRISE 21 (Carl Jr.)`), so its vendors land on
  `Solrise 21`.
* **The sheet also carries `Setup - Locations`** (38 stores with bank account,
  DBA e-mail and corporation - including four TX stores, 32-35, that the payroll
  register does not cover) and `Setup - Terms`. Not imported here; they are the
  next obvious enrichment of the store records.
* Vendor account numbers are **not unique** and are **not** the identity of a
  vendor - the same vendor has a different number at every store, and some numbers
  repeat across vendors. Identity is the name.

## 6. What was verified

| Check | Result |
|---|---|
| `parse_vendors.py` | 72 vendors, 432 links, 36 stores; one alias folded; `Tour Ice` flagged |
| `import_vendors.py`, first run | vendors created, links added |
| `import_vendors.py`, re-run | complete no-op |
| `verify_app_layer.py` | `DocType 'Vendor'`, `'Vendor Category'`, `'Store Vendor'`, `Customer.service_vendors field`, vendor count and link count all OK |
