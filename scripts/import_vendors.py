"""Seed the vendor master and each store's vendor links (docs/20).

Run inside the backend container:

    VENDORS_CSV=/tmp/solrise-fixtures/vendors.csv \\
    STORE_VENDORS_CSV=/tmp/solrise-fixtures/store_vendors.csv \\
      ./scripts/run-python.sh scripts/import_vendors.py

or through the wrapper, which stages both CSVs for you:

    SITE_ENV=aws ./scripts/import_vendors.sh

**Idempotent and non-fatal**, in the style of ``import_stores.py``: re-running
updates in place, creates only what is missing, fills only blanks, and logs rather
than raising when a field is absent from this platform version. Safe to run on
every deploy.

What it creates:

* a **Vendor** per row of ``vendors.csv`` - the operator's combined master list
  (name, category, terms, payment method, contact details, default account number);
* on each store's **Customer**, a row in the ``service_vendors`` child table per
  row of ``store_vendors.csv`` - the vendor, plus the account number and terms
  **that store** holds, because those differ per location.

The store↔vendor link is what the operator asked for: open a Customer and the
vendors connected to it are right there (docs/20).
"""

import csv
import os
import re

# Frappe's logger resolves <cwd>/../logs, so a script that is not run from the
# sites directory fails on /home/frappe/logs/database.log (docs/16 section 4).
_sites = os.environ.get("SITES_PATH", "/home/frappe/frappe-bench/sites")
if os.path.isdir(_sites):
	os.chdir(_sites)

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
VENDORS_CSV = os.environ.get("VENDORS_CSV", "/tmp/solrise-fixtures/vendors.csv")
LINKS_CSV = os.environ.get("STORE_VENDORS_CSV", "/tmp/solrise-fixtures/store_vendors.csv")

CUSTOMER_FIELD = "service_vendors"
#: Vendor fields copied straight from the CSV, in the order they appear in the file.
VENDOR_FIELDS = (
	"vendor_name",
	"vendor_category",
	"default_terms",
	"payment_method",
	"contact_person",
	"phone",
	"email",
	"address",
	"account_number",
)


def _report(title):
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def load_rows(path, required):
	if not os.path.isfile(path):
		raise SystemExit("file not found: %s" % path)
	with open(path, newline="", encoding="utf-8") as handle:
		return [row for row in csv.DictReader(handle) if (row.get(required) or "").strip()]


def ensure_vendor_area():
	"""Make sure the Vendor DocTypes and the Customer child field exist.

	The app installs these on migrate, but the importer may be handed a site where
	the version predates them; ensure_all() is idempotent and never raises.
	"""
	try:
		from solrise_erp import vendors

		return vendors.ensure_all()
	except Exception:
		_report("Solrise vendors: ensure area")
		return {}


def ensure_vendor(row):
	"""Upsert one Vendor; returns "created" or "updated" or "existing"."""
	name = row["vendor_name"].strip()
	if frappe.db.exists("Vendor", name):
		vendor = frappe.get_doc("Vendor", name)
		status = "existing"
	else:
		vendor = frappe.new_doc("Vendor")
		vendor.vendor_name = name
		status = "created"

	changed = False
	for field in VENDOR_FIELDS:
		if field == "vendor_name":
			continue
		if not frappe.get_meta("Vendor").has_field(field):
			continue
		value = (row.get(field) or "").strip()
		if not value:
			continue
		if field == "email" and "@" not in value:
			# The sheet sometimes holds a website where an e-mail is expected
			# (``order.sumitbeverage.com``). Keep it, but out of the validated
			# field - Frappe would otherwise refuse the whole vendor.
			note = vendor.get("notes") or ""
			line = "E-mail in source: %s" % value
			if line not in note:
				vendor.set("notes", (note + "\n" + line).strip())
				changed = True
			continue
		# Only ever fill blanks: an operator's edit must survive a re-run.
		if not vendor.get(field):
			vendor.set(field, value)
			changed = True

	if status == "created":
		vendor.insert(ignore_permissions=True)
	elif changed:
		vendor.save(ignore_permissions=True)
	return status


def link_store(store, rows):
	"""Add this store's vendor rows to its Customer; returns (added, topped_up)."""
	if not frappe.db.exists("Customer", store):
		return None, None
	if not frappe.get_meta("Customer").has_field(CUSTOMER_FIELD):
		return None, None

	customer = frappe.get_doc("Customer", store)
	by_vendor = {}
	for child in customer.get(CUSTOMER_FIELD) or []:
		by_vendor.setdefault(child.vendor, []).append(child)

	added = 0
	topped_up = 0
	for row in rows:
		vendor = row["vendor"].strip()
		if not frappe.db.exists("Vendor", vendor):
			continue
		existing = by_vendor.get(vendor)
		if existing:
			# Fill only blanks on the store's own row, so a corrected account number
			# typed in the Desk is not clobbered by a stale sheet.
			for child in existing:
				touched = False
				if not (child.account_number or "").strip() and (row.get("account_number") or "").strip():
					child.account_number = row["account_number"].strip()
					touched = True
				if not (child.terms or "").strip() and (row.get("terms") or "").strip():
					child.terms = row["terms"].strip()
					touched = True
				if touched:
					topped_up += 1
			continue
		customer.append(CUSTOMER_FIELD, {
			"vendor": vendor,
			"account_number": (row.get("account_number") or "").strip(),
			"terms": (row.get("terms") or "").strip(),
			"active": 1,
		})
		added += 1

	if added or topped_up:
		customer.save(ignore_permissions=True)
	return added, topped_up


def main():
	print("Seeding vendors on %s\n" % SITE)
	frappe.init(site=SITE, sites_path=_sites)
	frappe.connect()

	ensure_vendor_area()

	status = {"created": 0, "updated": 0, "existing": 0}
	skipped = []
	for row in load_rows(VENDORS_CSV, "vendor_name"):
		try:
			outcome = ensure_vendor(row)
			status[outcome] = status.get(outcome, 0) + 1
		except Exception:
			_report("Solrise vendors: vendor %s" % row.get("vendor_name"))
			skipped.append(row.get("vendor_name"))
	frappe.db.commit()

	links = load_rows(LINKS_CSV, "store")
	by_store = {}
	for row in links:
		by_store.setdefault(row["store"].strip(), []).append(row)

	added = 0
	topped_up = 0
	missing = []
	for store, rows in sorted(by_store.items()):
		try:
			a, t = link_store(store, rows)
			if a is None:
				missing.append(store)
				continue
			added += a
			topped_up += t
		except Exception:
			_report("Solrise vendors: links for %s" % store)
			missing.append(store)
	frappe.db.commit()

	print("  + vendors created : %d" % status["created"])
	print("  + vendors filled  : %d" % status["updated"])
	print("  + vendors unchanged: %d" % status["existing"])
	print("  + store links added: %d" % added)
	print("  + store links topped up: %d" % topped_up)
	if missing:
		print("\n  stores not found or missing the vendor field:")
		for store in missing:
			print("    - %s" % store)
	if skipped:
		print("\n  vendors skipped:")
		for name in skipped:
			print("    - %s" % name)
	print("\nimport_vendors.py complete. Re-running changes nothing.")
	frappe.destroy()


if __name__ == "__main__":
	main()
