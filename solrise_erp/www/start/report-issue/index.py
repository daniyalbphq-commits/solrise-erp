# Copyright (c) 2026, Solrise and contributors

"""The report form's context, for one department.

Every choice the form offers comes from `solrise_erp.portal.catalog`, so the
markup renders whatever buttons the catalogue holds - adding a category is a
one-line change there, and no template edit.

The desk comes from `?department=`: the home page has one tile per department
(Maintenance, HR, IT / Support) and they all open this page with their own
buttons. An unknown or missing value falls back to the default desk, so an old
bookmark still renders a usable form rather than an error.

The one exception is the "Customer" picker, which is not a catalogue: it is the
manager names on record for each store the caller speaks for, read from the
Contacts the importer linked (docs/17 section 14, docs/18 section 4).
"""

import json

import frappe

from solrise_erp.portal import catalog, guard, identity

no_cache = 1


def get_context(context):
	# Which desk. `guard.require_login` is given the full target so a session
	# timeout returns the customer to *this* desk, not the default one.
	desk = _desk()
	target = "{0}?department={1}".format(catalog.REPORT_ROUTE, desk["key"])
	guard.bootstrap(context, title=desk["label"], target=target)

	context.department = desk["key"]
	context.department_label = desk["label"]
	context.categories = catalog.categories_for(desk["key"])
	context.urgencies = catalog.URGENCIES
	context.default_category = desk["default_category"]
	context.default_urgency = catalog.DEFAULT_URGENCY
	# Handed to the page so the AJAX call has one definition, in Python.
	context.api_create = "solrise_erp.api.portal.create_issue"

	# Who is reporting. A station phone is shared, so the account holder is only
	# the default - the picker is how the person actually holding the phone says so.
	# Per store, because a multi-store manager's picker follows the store buttons,
	# and the script rebuilds it whenever those change.
	context.reporter_default = identity.display_name()
	by_store = {}
	for store in context.stores:
		# Never render an empty picker: a store whose contact is missing still has
		# to let somebody report, and the account holder is the honest guess.
		by_store[store["customer"]] = identity.managers(store["customer"]) or [
			context.reporter_default
		]

	context.reporter_json = json.dumps(by_store)
	first = context.stores[0]["customer"] if context.stores else None
	context.reporter_options = by_store.get(first) or [context.reporter_default]
	return context


def _desk():
	"""The department this request is for, or the default desk."""
	requested = ""
	try:
		requested = (frappe.form_dict.get("department") or "").strip()
	except Exception:
		requested = ""
	return catalog.department(requested) or catalog.default_department()
