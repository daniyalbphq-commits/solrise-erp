"""Seed the store list from ``fixtures/solrise_erp/stores.csv`` (docs/18).

Run inside the backend container:

    STORES_CSV=/tmp/solrise-fixtures/stores.csv \
      ./scripts/run-python.sh scripts/import_stores.py

or through the wrapper, which stages the CSV for you:

    SITE_ENV=aws ./scripts/import_stores.sh

**Idempotent and non-fatal**, in the style of ``scripts/setup_erp.py``: re-running
updates in place, creates only what is missing, and logs rather than raising when a
field is absent from this platform version. Safe to run on every deploy.

What it creates per row:

* a **Customer** named after the store - `Solrise 39` - carrying the sheet's state
  store number (`ND-9`) in a `store_code` custom field it ensures exists;
* a linked **Address** with the street, city, state and ZIP, so the location is a
  real record rather than a line of text;
* a linked **Contact** for the manager, name and mobile number;
* a **User login** for the manager, at most one per person, with their mobile number
  as the credential - managers *are* the customers in this deployment (docs/18
  section 5).

The portal resolves a reporting user as Contact -> Customer (docs/17), so it is the
Customer half that makes a report say *which store it came from*.

**A login needs an e-mail address** - it is the User record's primary key - and the
sheet has none. `.invalid` placeholders are used instead (never routable, so they
cannot reach a stranger) and staff sign in with their mobile number. Filling the
CSV's `email` column replaces a placeholder on the next run. One consequence is
unavoidable until real addresses exist: **a forgotten password has to be reset in
 the Desk**, because nothing can be e-mailed to a placeholder.
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
CSV_PATH = os.environ.get("STORES_CSV", "/tmp/solrise-fixtures/stores.csv")

# Where the seeded records hang in ERPNext's trees. Overridable so a site with its
# own taxonomy does not have to edit this file.
CUSTOMER_GROUP = os.environ.get("STORES_CUSTOMER_GROUP", "Commercial")
TERRITORY = os.environ.get("STORES_TERRITORY", "All Territories")
COUNTRY = os.environ.get("STORES_COUNTRY", "United States")

STORE_CODE_FIELD = "store_code"

# The sheet carries no e-mail addresses, and a Frappe user *is* an e-mail address, so
# each login needs one. `.invalid` is reserved by RFC 2606 and can never resolve, so
# a placeholder cannot accidentally reach a real stranger - which is why it is used
# instead of guessing `first.last@solrisestores.com`. Staff log in with their mobile
# number instead (see `ensure_mobile_login`), and supplying a real address in the
# CSV `email` column replaces the placeholder on the next run.
USER_DOMAIN = os.environ.get("STORES_USER_DOMAIN", "stores.invalid")

# Without a password a new login cannot be used at all, so user creation is skipped
# (with a warning) rather than half-done when this is unset.
DEFAULT_PASSWORD = os.environ.get("STORES_DEFAULT_PASSWORD", "")

# One login per *person*, not per store: several managers in this sheet run more than
# one site, and `User.mobile_no` is unique. Keyed by phone digits, then by name.
_users_by_manager = {}


def _report(title):
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def ensure_store_code_field():
	"""Make sure `Customer.store_code` exists; return True when it does.

	Created from code rather than shipped as a fixture because the app's fixture
	directory does not carry `custom_field.json` (docs/17 section 9.2 has the same
	story for permissions), and because this data is useful whether or not the app
	is installed.
	"""
	try:
		if frappe.get_meta("Customer").has_field(STORE_CODE_FIELD):
			return True
		field = frappe.new_doc("Custom Field")
		field.dt = "Customer"
		field.fieldname = STORE_CODE_FIELD
		field.label = "Store Code"
		field.fieldtype = "Data"
		field.insert_after = "customer_name"
		field.description = (
			"Operator's own store number, as scoped by state (e.g. ND-9). Blank for "
			"stores with no number in the source list."
		)
		field.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache(doctype="Customer")
		print("  + created custom field Customer.%s" % STORE_CODE_FIELD)
		return True
	except Exception:
		_report("Solrise stores: ensure Customer.store_code")
		print("  ! could not ensure Customer.%s - the code will not be stored" % STORE_CODE_FIELD)
		return False


def split_manager(raw):
	"""('Jessica N.', 'Weldon') from the sheet's 'Weldon, Jessica N.' or 'Mary Johnson'."""
	raw = re.sub(r"\s+", " ", (raw or "")).strip().strip(",")
	if not raw:
		return "", ""
	if "," in raw:
		last, _, first = raw.partition(",")
		return first.strip(), last.strip()
	parts = raw.split(" ")
	if len(parts) == 1:
		return parts[0], ""
	return " ".join(parts[:-1]), parts[-1]


def ensure_customer(row, has_code_field):
	"""The store as a Customer; returns (name, created)."""
	name = row["store"]
	if frappe.db.exists("Customer", name):
		customer = frappe.get_doc("Customer", name)
		created = False
	else:
		customer = frappe.new_doc("Customer")
		customer.customer_name = name
		customer.customer_type = "Company"
		created = True

	# Only ever fill blanks: an operator's edit in the Desk must survive a re-run.
	if not customer.get("customer_group"):
		customer.customer_group = CUSTOMER_GROUP
	if not customer.get("territory"):
		customer.territory = TERRITORY
	if has_code_field and row["store_code"] and not customer.get("store_code"):
		customer.set(STORE_CODE_FIELD, row["store_code"])

	if created:
		customer.insert(ignore_permissions=True)
	else:
		customer.save(ignore_permissions=True)
	return customer.name, created


def linked_records(doctype, customer):
	"""Names of `doctype` records linked to `customer`."""
	try:
		rows = frappe.get_all(
			"Dynamic Link",
			filters={"parenttype": doctype, "link_doctype": "Customer", "link_name": customer},
			fields=["parent"],
		)
		return [r.parent for r in rows]
	except Exception:
		return []


def ensure_address(row, customer):
	"""The store's location as a linked Address; returns (name, created)."""
	for name in linked_records("Address", customer):
		# An operator's corrected address wins over the sheet.
		return name, False

	address_type = "Billing"
	try:
		options = frappe.get_meta("Address").get_field("address_type").options or ""
		if "Shop" in [o.strip() for o in options.split("\n")]:
			address_type = "Shop"
	except Exception:
		pass

	address = frappe.new_doc("Address")
	address.address_title = row["store"]
	address.address_type = address_type
	address.address_line1 = row["street"]
	address.city = row["city"] or "-"
	if row["region"]:
		address.state = row["region"]
	if row["zip"]:
		address.pincode = row["zip"]
	address.country = COUNTRY
	address.append("links", {"link_doctype": "Customer", "link_name": customer})
	address.insert(ignore_permissions=True)
	return address.name, True


def find_manager(customer, first, last):
	"""An existing Contact for the same person under this store, or None."""
	for name in linked_records("Contact", customer):
		existing = frappe.db.get_value("Contact", name, ["first_name", "last_name"], as_dict=True) or {}
		if (existing.get("first_name") or "").strip().lower() == (first or "").lower() and \
		   (existing.get("last_name") or "").strip().lower() == (last or "").lower():
			return name
	return None


def add_phone(contact, number):
	"""Record `number` on the contact.

	It has to go on the `phone_nos` child table: `Contact.phone` and
	`Contact.mobile_no` are `read_only` and *derived* from those rows, so assigning
	them directly is accepted without complaint and then silently discarded - which
	is how the sheet's manager numbers went missing the first time this ran.
	"""
	contact.append(
		"phone_nos",
		{"phone": number, "is_primary_phone": 1, "is_primary_mobile_no": 1},
	)


def ensure_contact(row, customer):
	"""The manager as a linked Contact.

	Returns `(name, status, reason)` where status is one of "created",
	"backfilled" or "existing", and `reason` is set instead when there is nothing
	to do because the sheet has no manager for this store.
	"""
	first, last = split_manager(row["manager_name"])
	if not (first or last):
		return None, None, "no manager in the sheet"

	name = find_manager(customer, first, last)
	if name:
		# Top up a contact that predates the `phone_nos` fix rather than leaving the
		# sheet's number unrecorded.
		if row["manager_phone"] and not frappe.db.exists("Contact Phone", {"parent": name}):
			contact = frappe.get_doc("Contact", name)
			add_phone(contact, row["manager_phone"])
			contact.save(ignore_permissions=True)
			return name, "backfilled", None
		return name, "existing", None

	contact = frappe.new_doc("Contact")
	contact.first_name = first or row["store"]
	contact.last_name = last
	if row["manager_phone"]:
		add_phone(contact, row["manager_phone"])
	contact.append("links", {"link_doctype": "Customer", "link_name": customer})
	contact.insert(ignore_permissions=True)
	return contact.name, "created", None


def slugify(text):
	"""'Van Dam, Brett' -> 'van.dam.brett'. Used for the placeholder login and username."""
	return re.sub(r"[^a-z0-9]+", ".", (text or "").lower()).strip(".") or "store"


def placeholder_login(contact):
	"""A never-routable address for a manager with no e-mail in the sheet."""
	return "%s@%s" % (slugify("%s.%s" % (contact.first_name or "", contact.last_name or "")), USER_DOMAIN)


def manager_key(contact, phone):
	"""Identify the person across rows: their number first, then their name."""
	digits = re.sub(r"\D", "", phone or "")
	if digits:
		return digits
	return slugify("%s %s" % (contact.first_name or "", contact.last_name or ""))


def user_username(contact, phone):
	"""What the manager types to sign in.

	The national digits, not the stored `+1...` form. Frappe's login page has no
	country-code picker, so the `+` and the country code are two extra characters to
	get wrong on a keypad - and one was duly left off the first time this was tried
	by hand. Digits only is what a phone keyboard does best.

	Falls back to the name when there is no number.
	"""
	digits = re.sub(r"\D", "", phone or "")
	if len(digits) == 11 and digits.startswith("1"):
		digits = digits[1:]
	if len(digits) == 10:
		return digits
	return slugify("%s.%s" % (contact.first_name or "", contact.last_name or ""))


def ensure_login_options():
	"""Allow signing in with a mobile number or a username.

	Needed because the managers have no e-mail: the placeholder IDs cannot receive
	anything, so what staff can type is the credential. Both extra forms are enabled
	together - the number for anyone who remembers it, the username (the national
	digits) for anyone who does not want to find the `+`. These are site-wide settings,
	so agents and admins may use them too.
	"""
	try:
		settings = frappe.get_doc("System Settings")
		wanted = (
			("allow_login_using_mobile_number", "login by mobile number"),
			("allow_login_using_user_name", "login by user name"),
		)
		enabled = []
		for fieldname, label in wanted:
			if not settings.get(fieldname):
				settings.set(fieldname, 1)
				enabled.append(label)
		if enabled:
			settings.save(ignore_permissions=True)
			frappe.db.commit()
			print("  + System Settings: enabled %s" % ", ".join(enabled))
		return True
	except Exception:
		_report("Solrise stores: enable login options")
		print("  ! could not enable the extra login options")
		return False


def ensure_user(contact_name, row):
	"""The manager's portal login.

	Returns `(user, status, reason)`; status is "created" or "existing", or None with
	a reason when there is nothing to create. One user covers every store the same
	person manages, so the portal knows all of their sites (docs/17 section 11).
	"""
	if not contact_name:
		return None, None, "no manager contact"
	if not DEFAULT_PASSWORD:
		return None, None, "STORES_DEFAULT_PASSWORD is not set"

	contact = frappe.get_doc("Contact", contact_name)
	phone = ""
	try:
		phone = frappe.db.get_value(
			"Contact Phone", {"parent": contact_name, "is_primary_mobile_no": 1}, "phone"
		) or ""
	except Exception:
		pass
	phone = phone or (row.get("manager_phone") or "")

	key = manager_key(contact, phone)
	user_name = _users_by_manager.get(key)
	status = "existing"

	if not user_name:
		login = (row.get("email") or "").strip() or placeholder_login(contact)
		if frappe.db.exists("User", login):
			user = frappe.get_doc("User", login)
		else:
			user = frappe.new_doc("User")
			user.email = login
			user.first_name = contact.first_name
			user.last_name = contact.last_name
			user.user_type = "Website User"
			# The address cannot receive mail; a welcome e-mail would only fail.
			user.send_welcome_email = 0
			user.new_password = DEFAULT_PASSWORD
			status = "created"

		if not user.get("username") or user.get("username") == slugify(
			"%s.%s" % (contact.first_name or "", contact.last_name or "")
		):
			# Also migrates the name-slug seeded before the keystroke problem was seen.
			user.username = user_username(contact, phone)
		if phone and not user.get("mobile_no"):
			user.mobile_no = phone
		if "Customer" not in [r.role for r in (user.get("roles") or [])]:
			user.append("roles", {"role": "Customer"})

		if status == "created":
			user.insert(ignore_permissions=True)
		else:
			user.save(ignore_permissions=True)
		user_name = user.name
		_users_by_manager[key] = user_name

	# Linked on **every** pass, not only where the login is created. One person
	# appears once per store they manage, and each of those Contacts has to point at
	# the login or the portal cannot see the store: returning early from the cache
	# left Tammy Hoeppner, who runs four sites, attached to a single one.
	if contact.get("user") != user_name:
		contact.user = user_name
		contact.save(ignore_permissions=True)

	return user_name, status, None


def load_rows():
	if not os.path.isfile(CSV_PATH):
		raise SystemExit("store list not found: %s" % CSV_PATH)
	with open(CSV_PATH, newline="", encoding="utf-8") as handle:
		return [row for row in csv.DictReader(handle) if (row.get("store") or "").strip()]


def main():
	print("Seeding stores on %s from %s\n" % (SITE, CSV_PATH))
	frappe.init(site=SITE, sites_path=_sites)
	frappe.connect()

	created = {"customer": 0, "address": 0, "contact": 0, "user": 0}
	backfilled = []
	skipped = []
	linked_users = set()
	has_code_field = ensure_store_code_field()
	if DEFAULT_PASSWORD:
		ensure_login_options()
	else:
		print("  ! STORES_DEFAULT_PASSWORD is not set - no logins will be created\n")

	for row in load_rows():
		store = row["store"]
		try:
			customer, made = ensure_customer(row, has_code_field)
			created["customer"] += 1 if made else 0

			_, made = ensure_address(row, customer)
			created["address"] += 1 if made else 0

			contact, status, reason = ensure_contact(row, customer)
			if reason:
				skipped.append("%s: %s" % (store, reason))
				continue
			if status == "created":
				created["contact"] += 1
			elif status == "backfilled":
				backfilled.append(store)

			user, user_status, user_reason = ensure_user(contact, row)
			if user_reason:
				skipped.append("%s: %s" % (store, user_reason))
			elif user_status == "created" and user not in linked_users:
				created["user"] += 1
				linked_users.add(user)
			elif user:
				linked_users.add(user)
		except Exception:
			_report("Solrise stores: %s" % store)
			skipped.append("%s: failed - see Error Log" % store)

	frappe.db.commit()

	print("  + customers created: %d" % created["customer"])
	print("  + addresses created: %d" % created["address"])
	print("  + contacts created : %d" % created["contact"])
	print("  + logins created   : %d" % created["user"])
	print("  + stores covered by a login: %d" % len(linked_users))
	if backfilled:
		print("  + phones backfilled: %d" % len(backfilled))
	if skipped:
		print("\n  skipped:")
		for line in skipped:
			print("    - %s" % line)
	print("\nimport_stores.py complete. Re-running changes nothing.")
	frappe.destroy()


if __name__ == "__main__":
	main()
