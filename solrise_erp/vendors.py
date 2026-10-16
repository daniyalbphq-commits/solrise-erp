# Copyright (c) 2026, Solrise and contributors

"""Vendor management (docs/20).

Stores are `Customer`s and their managers are `Contact`s (docs/18). The vendors a
store buys from are a third kind of party, and they are deliberately **not** the
ERPNext `Supplier`: this deployment has no accounts-payable ledger behind them, so
a Supplier would drag in Company, ledger and payment machinery that nothing here
uses. `Vendor` is a small, standalone master, and each store carries the vendors it
uses in a `Store Vendor` child table - the store's own account number and terms
included, because those differ per location.

This module owns the three pieces the app must re-assert on every migrate:

* the starter `Vendor Category` list,
* the `Customer.service_vendors` child-table field, and
* a `Solrise Vendors` workspace to find the master in.

Every function is idempotent and never overwrites an operator's edit.
"""

import json

import frappe

MODULE = "Solrise ERP"
VENDOR_DOCTYPE = "Vendor"
CATEGORY_DOCTYPE = "Vendor Category"
TABLE_DOCTYPE = "Store Vendor"
CUSTOMER_DOCTYPE = "Customer"
CUSTOMER_FIELD = "service_vendors"
CUSTOMER_FIELD_LABEL = "Service Vendors"
WORKSPACE = "Solrise Vendors"

#: A starting taxonomy, not a fixed one. The operator adds, renames or disables
#: these freely; the importer never assigns a category, it only seeds the list so
#: the field has something to point at on a fresh site.
CATEGORIES = (
	"Beverage",
	"Food & Convenience",
	"Ice & Frozen",
	"Distributor / Wholesale",
	"Uniform & Laundry",
	"Propane & Gas",
	"Equipment & Service",
	"Maintenance & Repair",
	"Cleaning & Sanitation",
	"Other",
)

WORKSPACE_ROLES = ("Solrise Admin", "Solrise Super Admin", "Support Manager")

SHORTCUTS = (
	{"type": "DocType", "label": "Vendor", "link_to": VENDOR_DOCTYPE, "doc_view": "List", "color": "Blue"},
	{
		"type": "DocType",
		"label": "Vendor Category",
		"link_to": CATEGORY_DOCTYPE,
		"doc_view": "List",
		"color": "Green",
	},
	{
		"type": "DocType",
		"label": "Customers",
		"link_to": CUSTOMER_DOCTYPE,
		"doc_view": "List",
		"color": "Orange",
	},
)

LINKS = (
	{"type": "Card Break", "label": "Vendors"},
	{"type": "Link", "label": "Vendor", "link_to": VENDOR_DOCTYPE, "link_type": "DocType"},
	{"type": "Link", "label": "Vendor Category", "link_to": CATEGORY_DOCTYPE, "link_type": "DocType"},
	{"type": "Card Break", "label": "Stores"},
	{"type": "Link", "label": "Customer", "link_to": CUSTOMER_DOCTYPE, "link_type": "DocType"},
)


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def ensure_vendor_categories():
	"""Insert any starter `Vendor Category` that is missing; return the new names.

	Never re-enables or rewrites an existing row, so disabling one is a decision
	that survives a migrate.
	"""
	created = []
	try:
		if not frappe.db.exists("DocType", CATEGORY_DOCTYPE):
			return created
		for name in CATEGORIES:
			if frappe.db.exists(CATEGORY_DOCTYPE, name):
				continue
			doc = frappe.new_doc(CATEGORY_DOCTYPE)
			doc.category_name = name
			doc.enabled = 1
			doc.insert(ignore_permissions=True)
			created.append(name)
		if created:
			frappe.db.commit()
			frappe.logger().info("vendors: seeded categories %s", created)
	except Exception:
		_log_error("Solrise vendors: ensure_vendor_categories")
	return created


def ensure_customer_vendor_field():
	"""Create the `Customer.service_vendors` child-table field; return its name.

	This is the "table of vendors" a store shows (docs/20): a `Table` field whose
	`options` is the `Store Vendor` child DocType, so opening a Customer lists the
	vendors it uses with the store's account number and terms.

	Created in code rather than shipped as a fixture because the app's fixture
	directory carries no `custom_field.json` - the same reason `Customer.store_code`
	is created by its importer (docs/18 section 1).
	"""
	try:
		if not frappe.db.exists("DocType", CUSTOMER_DOCTYPE):
			return None
		if not frappe.db.exists("DocType", TABLE_DOCTYPE):
			return None
		if frappe.get_meta(CUSTOMER_DOCTYPE).has_field(CUSTOMER_FIELD):
			return CUSTOMER_FIELD

		doc = frappe.new_doc("Custom Field")
		doc.dt = CUSTOMER_DOCTYPE
		doc.fieldname = CUSTOMER_FIELD
		doc.label = CUSTOMER_FIELD_LABEL
		doc.fieldtype = "Table"
		doc.options = TABLE_DOCTYPE
		doc.insert_after = "customer_name"
		doc.description = (
			"The vendors this store buys from, with the account number and terms this "
			"location holds with each."
		)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.clear_cache(doctype=CUSTOMER_DOCTYPE)
		frappe.logger().info("vendors: custom field %s.%s created", CUSTOMER_DOCTYPE, CUSTOMER_FIELD)
		return CUSTOMER_FIELD
	except Exception:
		_log_error("Solrise vendors: ensure_customer_vendor_field")
		return None


def _content():
	"""The workspace layout, in the block format the Desk renders."""
	blocks = []

	def add(kind, data):
		blocks.append({"id": frappe.generate_hash(length=10), "type": kind, "data": data})

	add("header", {"text": '<span class="h4"><b>Vendors</b></span>', "col": 12})
	for shortcut in SHORTCUTS:
		add("shortcut", {"shortcut_name": shortcut["label"], "col": 4})
	add("spacer", {"col": 12})
	add("header", {"text": '<span class="h4"><b>Documents</b></span>', "col": 12})
	for link in LINKS:
		if link["type"] == "Card Break":
			add("card", {"card_name": link["label"], "col": 4})
	return json.dumps(blocks)


def ensure_vendor_workspace():
	"""Create or refresh the `Solrise Vendors` workspace; return its name."""
	try:
		if not frappe.db.exists("DocType", "Workspace"):
			return None
		if not frappe.db.exists("Module Def", MODULE):
			return None
		if not frappe.db.exists("DocType", VENDOR_DOCTYPE):
			return None

		if frappe.db.exists("Workspace", WORKSPACE):
			doc = frappe.get_doc("Workspace", WORKSPACE)
		else:
			doc = frappe.new_doc("Workspace")
			doc.label = WORKSPACE

		doc.title = WORKSPACE
		doc.public = 1
		doc.is_hidden = 0
		doc.for_user = ""
		doc.module = MODULE
		doc.icon = "sell"
		doc.indicator_color = "blue"
		doc.sequence_id = 2.0

		doc.set("shortcuts", [])
		for shortcut in SHORTCUTS:
			doc.append("shortcuts", dict(shortcut))

		doc.set("links", [])
		for link in LINKS:
			doc.append("links", dict(link))

		doc.set("roles", [])
		for role in WORKSPACE_ROLES:
			if frappe.db.exists("Role", role):
				doc.append("roles", {"role": role})

		doc.content = _content()

		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info("vendors: workspace %s ready", WORKSPACE)
		return doc.name
	except Exception:
		_log_error("Solrise vendors: ensure_vendor_workspace")
		return None


def ensure_all():
	"""Apply everything this module owns; return a summary dict. Never raises."""
	summary = {"categories": [], "customer_field": None, "workspace": None}
	try:
		summary["categories"] = ensure_vendor_categories()
	except Exception:
		_log_error("Solrise vendors: ensure_all categories")
	try:
		summary["customer_field"] = ensure_customer_vendor_field()
	except Exception:
		_log_error("Solrise vendors: ensure_all customer field")
	try:
		summary["workspace"] = ensure_vendor_workspace()
	except Exception:
		_log_error("Solrise vendors: ensure_all workspace")
	return summary
