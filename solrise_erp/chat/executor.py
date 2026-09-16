# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Run a fully-resolved, fully-authorised intent (docs/12 section 6.5).

This is the only module in ``chat/`` that touches records. It never decides
whether the user *may* act - ``chat.permissions`` did that one gate earlier - and
it never widens anything either: every write goes through Frappe's own Document
API (``insert`` / ``save`` / ``submit`` / ``cancel``, and ``frappe.delete_doc``),
each of which re-checks permission as the current user. A row or field rule that
changed between the gate and the write therefore still fails closed.

Lists and record lookups go through ``frappe.get_list``, which applies the
DocType's ``permission_query_conditions`` and the user's User Permissions. A
record is always resolved through that filtered query before it is touched, so a
name the user cannot see is answered as a permission failure rather than being
looked up anyway (docs/12 section 1.2, R4).

The result of a turn is ``{name, message, link, latency_ms}`` - plus ``records``
for a list - where ``link["name"]`` is the button label and ``link["route"]`` the
Desk path.
"""

from __future__ import annotations

import time

import frappe
from frappe import _

from solrise_erp.chat import registry, schema

LIST_LIMIT = 10
PREVIEW_NAMES = 5

# The registry owns the closed action enum (docs/12 section 1.2, R2); ``list`` is
# this module's read variant and carries no permission type of its own.
EXECUTABLE_ACTIONS = tuple(registry.ACTION_PTYPE) + ("list",)


def run(parsed, settings=None):
	"""Execute one intent and return the turn's result.

	``parsed`` is what the chat layer resolved: ``doctype``, ``action``,
	``record``, ``fields``, ``urgency``, ``transition`` and ``scope``. The return
	value is ``{name, message, link, latency_ms}``, plus ``records`` for a list.
	"""
	started = time.time()
	parsed = parsed or {}
	doctype = (parsed.get("doctype") or "").strip()
	action_name = (parsed.get("action") or "").strip()
	record = parsed.get("record")
	fields = dict(parsed.get("fields") or {})

	if action_name not in EXECUTABLE_ACTIONS:
		frappe.throw(_("Unsupported action '{0}'.").format(action_name))

	if action_name in ("create", "update"):
		# Urgency is mapped onto the configured field just before the write, and
		# only where the DocType can hold the value (docs/12 section 10).
		fields = schema.apply_urgency(doctype, fields, parsed.get("urgency"), settings)

	if action_name in ("read", "list"):
		return _read(doctype, record, parsed.get("scope"), started)
	if action_name == "create":
		return _create(doctype, fields, started)
	if action_name == "update":
		return _update(doctype, record, fields, started)
	if action_name in ("submit", "cancel"):
		return _transition(doctype, record, action_name, started)
	if action_name == "delete":
		return _delete(doctype, record, started)
	if action_name == "approve":
		return _approve(doctype, record, parsed.get("transition"), started)
	# Unreachable while EXECUTABLE_ACTIONS and the branches above agree; kept so an
	# action added to the registry cannot quietly do nothing.
	frappe.throw(_("Unsupported action '{0}'.").format(action_name))


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def _read(doctype, record, scope, started):
	"""One record when the intent named it, otherwise a bounded list."""
	if record:
		name = _probe(doctype, record)
		return _reply(
			name, _("Here is {0}.").format(name), _form_link(doctype, name), started
		)
	return _list(doctype, scope, started)


def _list(doctype, scope, started):
	"""Up to ``LIST_LIMIT`` rows, newest first, narrowed to "mine" when asked.

	``scope == "mine"`` means what the user owns *or* is assigned - the row rule
	the Issue list already uses (docs/11). The ``_assign`` half is added only when
	the DocType actually carries that field, so the filter never depends on a
	column that may not exist.
	"""
	or_filters = None
	if scope == "mine":
		user = frappe.session.user
		or_filters = [["owner", "=", user]]
		if frappe.get_meta(doctype).has_field("_assign"):
			or_filters.append(["_assign", "like", "%{0}%".format(user)])

	rows = frappe.get_list(
		doctype,
		fields=["name", "modified"],
		order_by="modified desc",
		limit_page_length=LIST_LIMIT,
		or_filters=or_filters,
	)
	names = [row["name"] for row in rows]
	message = _("Found {0} {1}.").format(len(names), _plural(doctype, len(names)))
	if names:
		message = "{0} {1}".format(message, ", ".join(names[:PREVIEW_NAMES]))
	return _reply(
		None,
		message,
		_link(_("View all {0}").format(_plural(doctype, 2)), _route(doctype)),
		started,
		records=names,
	)


def _create(doctype, fields, started):
	"""Insert a new record as the current user.

	``insert()`` is where Frappe enforces create permission, so a DocType the
	user's roles do not allow fails here instead of being worked around.
	"""
	doc = frappe.get_doc(dict(fields, doctype=doctype))
	doc.insert()
	return _reply(
		doc.name,
		_("Created {0} {1}.").format(doctype, doc.name),
		_form_link(doctype, doc.name),
		started,
	)


def _update(doctype, record, fields, started):
	"""Set fields on one existing record and save it as the current user."""
	name = _probe(doctype, record)
	doc = frappe.get_doc(doctype, name)
	for fieldname, value in fields.items():
		doc.set(fieldname, value)
	doc.save()
	return _reply(
		name, _("Updated {0} {1}.").format(doctype, name), _form_link(doctype, name), started
	)


def _transition(doctype, record, action_name, started):
	"""Submit or cancel one record; both re-check permission in the ORM call."""
	name = _probe(doctype, record)
	doc = frappe.get_doc(doctype, name)
	if action_name == "submit":
		doc.submit()
		verb = _("Submitted")
	else:
		doc.cancel()
		verb = _("Cancelled")
	return _reply(
		name,
		_("{0} {1} {2}.").format(verb, doctype, name),
		_form_link(doctype, name),
		started,
	)


def _delete(doctype, record, started):
	"""Delete one record; ``frappe.delete_doc`` re-checks delete permission."""
	name = _probe(doctype, record)
	frappe.delete_doc(doctype, name)
	return _reply(
		name, _("Deleted {0} {1}.").format(doctype, name), None, started
	)


def _approve(doctype, record, transition, started):
	"""Hand a state change to the workflow bridge, imported lazily.

	``workflow.apply`` checks the user's own available transitions and raises
	``frappe.PermissionError`` when the transition is not theirs to make; that
	exception is deliberately not caught here.
	"""
	from solrise_erp.chat import workflow

	result = dict(workflow.apply(doctype, record, transition, started) or {})
	# The bridge returns the same shape; fill in anything it left out so a turn's
	# contract holds whatever the workflow layer reports.
	result["name"] = result.get("name") or record
	result["message"] = result.get("message") or _("Applied {0} to {1} {2}.").format(
		transition or _("the transition"), doctype, record
	)
	result.setdefault("link", None)
	if not result.get("latency_ms"):
		result["latency_ms"] = int((time.time() - started) * 1000)
	return result


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _probe(doctype, record):
	"""Resolve a record through a permission-filtered query, or deny.

	``frappe.get_list`` runs ``permission_query_conditions`` and User Permissions;
	a record the user may not read is simply not returned, and that is reported as
	a permission failure rather than as a missing record.
	"""
	rows = frappe.get_list(
		doctype, filters={"name": record}, fields=["name"], limit_page_length=1
	)
	if not rows:
		frappe.throw(
			_("You don't have permission to perform this action."), frappe.PermissionError
		)
	return rows[0]["name"]


def _reply(name, message, link, started, records=None):
	"""The turn's return shape; ``records`` is present only for a list."""
	result = {
		"name": name,
		"message": message,
		"link": link,
		"latency_ms": int((time.time() - started) * 1000),
	}
	if records is not None:
		result["records"] = records
	return result


def _route(doctype, name=None):
	"""The Desk route of a DocType list, or of one of its forms."""
	base = "/app/" + frappe.scrub(doctype)
	return (base + "/" + name) if name else base


def _link(label, route):
	"""A result link: ``name`` is the button label, ``route`` the Desk path."""
	return {"name": label, "route": route}


def _form_link(doctype, name):
	"""A link that opens one record."""
	return _link(_("Open {0}").format(name), _route(doctype, name))


# The registry has exactly one DocType whose plural is not its name plus an `s`;
# naming it here keeps "Found 2 Payment Entries." out of the message templates.
IRREGULAR_PLURALS = {"Payment Entry": "Payment Entries"}


def _plural(doctype, count):
	"""The DocType name a count refers to, for a message the user reads."""
	if count == 1:
		return doctype
	return IRREGULAR_PLURALS.get(doctype, doctype + "s")
