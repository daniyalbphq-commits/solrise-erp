"""
Solrise ERP - the store login hand-out sheet.

`import_stores.py` turns the store sheet into Customers, Contacts and (when it
is given a password) one login per manager. This prints what that produced, in
the form you hand to a station: which store, who runs it, and what they type to
sign in.

    SITE_ENV=aws ./scripts/run-python.sh scripts/store_logins.py
    STORE_LOGINS_CSV=/tmp/logins.csv SITE_ENV=aws ./scripts/run-python.sh scripts/store_logins.py

Read-only. It reports the *current* state, so logins the importer has not
created yet show as `-`, which is the point: the sheet is only usable once the
`password` column is real.

The identity rules are `import_stores.py`'s, deliberately duplicated rather than
imported: that module is a script, not a library, and this must never disagree
with what it created. See docs/18-stores.md section 5.

Set STORE_LOGINS_PASSWORD in the environment to print a password column,
matching what was passed to `import_stores.sh` as STORES_DEFAULT_PASSWORD.
"""
import csv
import os
import re
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"
USER_DOMAIN = os.environ.get("STORES_USER_DOMAIN", "stores.invalid")
PASSWORD = os.environ.get("STORE_LOGINS_PASSWORD", "")
CSV_OUT = os.environ.get("STORE_LOGINS_CSV", "")

# Only a store carries a code (docs/18 section 4); this keeps the sheet to the
# stores the importer made rather than every Customer on the site.
STORE_FILTER = {"customer_type": "Company"}


def slugify(value):
	value = re.sub(r"[^\w\s-]", "", (value or "").strip().lower())
	return re.sub(r"[-\s]+", "-", value).strip("-")


def user_username(first, last, phone):
	"""The national digits - what a keypad produces without hunting for `+`."""
	digits = re.sub(r"\D", "", phone or "")
	if len(digits) == 11 and digits.startswith("1"):
		digits = digits[1:]
	if len(digits) == 10:
		return digits
	return slugify("%s.%s" % (first or "", last or ""))


def placeholder_login(first, last):
	return "%s@%s" % (slugify("%s.%s" % (first or "", last or "")), USER_DOMAIN)


def store_sort_key(store):
	match = re.search(r"(\d+)", store or "")
	return (int(match.group(1)) if match else 9999, store or "")


def primary_phone(contact):
	try:
		return frappe.db.get_value(
			"Contact Phone", {"parent": contact, "is_primary_mobile_no": 1}, "phone"
		) or ""
	except Exception:  # noqa: BLE001 - a missing table is not worth failing over
		return ""


def contacts_for(customer):
	"""Contacts linked to this Customer, via the link the importer writes."""
	names = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
	)
	return sorted(set(names))


def collect():
	stores = frappe.get_all(
		"Customer",
		filters=STORE_FILTER,
		fields=["name", "customer_name", "store_code"],
		order_by="customer_name",
	)
	rows = []
	for store in stores:
		for contact_name in contacts_for(store.name):
			contact = frappe.get_doc("Contact", contact_name)
			phone = primary_phone(contact_name)
			first, last = contact.first_name or "", contact.last_name or ""
			login_id = contact.get("user") or ""
			email = ""
			username = user_username(first, last, phone)
			if login_id:
				email = frappe.db.get_value("User", login_id, "email") or ""
				username = frappe.db.get_value("User", login_id, "username") or username
			rows.append(
				{
					"store": store.customer_name or store.name,
					"store_code": store.get("store_code") or "",
					"manager": ("%s %s" % (first, last)).strip(),
					"mobile": phone,
					"username": username,
					"login": login_id or "not created",
					"email": email or placeholder_login(first, last),
					"password": PASSWORD if login_id else "",
				}
			)
	return rows


def orphan_customer_users(known):
	"""Customer-role logins that no store in the sheet claims."""
	users = frappe.get_all(
		"Has Role",
		filters={"role": "Customer", "parenttype": "User"},
		pluck="parent",
	)
	out = []
	for name in sorted(set(users)):
		if name in known:
			continue
		if not frappe.db.get_value("User", name, "enabled"):
			continue
		out.append(
			{
				"name": name,
				"email": frappe.db.get_value("User", name, "email") or "",
				"username": frappe.db.get_value("User", name, "username") or "",
			}
		)
	return out


def print_table(rows):
	headers = ["Store", "Code", "Manager", "Signs in as", "Mobile", "Login id"]
	if PASSWORD:
		headers.append("Password")
	widths = [len(h) for h in headers]
	cells = []
	for row in rows:
		line = [
			row["store"],
			row["store_code"],
			row["manager"],
			row["username"],
			row["mobile"],
			row["login"],
		]
		if PASSWORD:
			line.append(row["password"])
		cells.append(line)
		widths = [max(w, len(c)) for w, c in zip(widths, line)]

	print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
	print("  ".join("-" * w for w in widths))
	for line in cells:
		print("  ".join(c.ljust(w) for c, w in zip(line, widths)))


def main():
	frappe.init(site=SITE, sites_path=SITES_PATH)
	frappe.connect()
	try:
		rows = collect()
		users = {r["login"] for r in rows if r["login"] != "not created"}
		orphans = orphan_customer_users(users)
	finally:
		frappe.destroy()

	if CSV_OUT:
		with open(CSV_OUT, "w", newline="", encoding="utf-8") as handle:
			writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
			writer.writeheader()
			writer.writerows(rows)
		print("wrote %d row(s) to %s" % (len(rows), CSV_OUT))

	print("\nStore logins on %s\n" % SITE)
	print_table(rows)

	created = sum(1 for r in rows if r["login"] != "not created")
	people = {r["username"] for r in rows}
	print()
	print("  stores      : %d" % len({r["store"] for r in rows}))
	print("  managers    : %d  (some run more than one store)" % len(people))
	print("  logins      : %d of %d manager row(s) have a User" % (created, len(rows)))
	if orphans:
		print("  other Customer-role logins (not from the sheet):")
		for row in orphans:
			print("      %s  username=%s" % (row["email"] or row["name"], row["username"] or "-"))
	if created and not PASSWORD:
		print()
		print("  Set STORE_LOGINS_PASSWORD in the environment to print the password column.")
	if not created:
		print()
		print("  No logins exist yet - run import_stores.sh with STORES_DEFAULT_PASSWORD")
		print("  set, then run this again to hand the sheet out (docs/18 section 5).")
	return 0


if __name__ == "__main__":
	sys.exit(main())
