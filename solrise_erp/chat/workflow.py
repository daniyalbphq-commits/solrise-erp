# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The approval bridge: workflow transitions instead of a raw save (docs/12
sections 5.5 and 6.5, and gate 7 of section 7.1).

The chat action ``approve`` means "apply one transition of the DocType's active
workflow". The transitions offered are exactly the ones
``frappe.model.workflow.get_transitions`` offers the signed-in user for that
document, and ``apply_workflow`` performs Frappe's own check on the way in, so a
role that may not approve never gets the transition and a rule that changed since
the gate still fails closed. Nothing here widens access and no transition is ever
chosen on the user's behalf.
"""

import re
import time

import frappe
from frappe import _


def transitions(doctype, docname, settings=None):
	"""The workflow transitions this user may consider for one document.

	Returns an empty list when the DocType is outside ``chat_allowed_workflows``,
	has no active workflow, or offers no transition from its current state. A row
	the user may not read raises ``frappe.PermissionError``.
	"""
	if not doctype or not docname:
		return []
	if not _workflow_allowed(doctype, settings):
		return []
	return _available(_raw_transitions(_load(doctype, docname)))


def find(doctype, docname, transition, settings=None):
	"""The normalised entry for ``transition``, or ``None``.

	Matching is case-insensitive because the user and ``chat/nlp.py`` supply
	"Approve"/"Reject" while a workflow may spell its action differently.
	"""
	if not transition or not _workflow_allowed(doctype, settings):
		return None
	_raw, entry = _match(_raw_transitions(_load(doctype, docname)), transition)
	return entry


def apply(doctype, docname, transition, started=None, settings=None):
	"""Apply one transition and describe the result for the chat reply.

	Raises ``frappe.PermissionError`` - never a guess - when the DocType is not in
	``chat_allowed_workflows``, when no transition was named, or when the named
	transition is not available to this user; the message names the transitions
	that are. ``started`` is a ``time.time()`` value used for ``latency_ms``.
	"""
	if not _workflow_allowed(doctype, settings):
		raise frappe.PermissionError(
			_("Approvals are not enabled in chat for {0}.").format(doctype)
		)
	doc = _load(doctype, docname)
	raws = _raw_transitions(doc)
	available = _available(raws)

	label = (transition or "").strip()
	if not label:
		raise frappe.PermissionError(
			_("Which transition should I apply? Available: {0}.").format(_labels(available))
		)
	raw, entry = _match(raws, label)
	if entry is None or not entry["allowed"]:
		raise frappe.PermissionError(
			_("No '{0}' transition is available to you for {1} {2}. Available: {3}.").format(
				label, doctype, docname, _labels(available)
			)
		)

	_apply_transition(doc, raw)

	state = _human_state(doc)
	return {
		"name": docname,
		"message": (
			_("{0} {1} is now {2}.").format(doctype, docname, state)
			if state
			else _("{0} {1} was updated.").format(doctype, docname)
		),
		"link": {"name": _("Open {0}").format(docname), "route": _route(doctype, docname)},
		"latency_ms": int((time.time() - started) * 1000) if started else 0,
	}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _settings(settings):
	"""The Solrise Settings single, loaded only when the caller has none."""
	return settings if settings is not None else frappe.get_cached_doc("Solrise Settings")


def _allowed_workflows(settings):
	"""The DocTypes ``chat_allowed_workflows`` names, lowercased.

	The setting accepts newlines or commas; an empty value means every DocType
	with a workflow is addressable (docs/12 section 10).
	"""
	doc = _settings(settings)
	# A Document and a plain mapping are both accepted: a mapping that answered
	# `None` here would read as "every workflow allowed" and widen access.
	if hasattr(doc, "get"):
		raw = doc.get("chat_allowed_workflows")
	else:
		raw = getattr(doc, "chat_allowed_workflows", None)
	if not isinstance(raw, str):
		raw = str(raw or "")
	return {value.strip().lower() for value in re.split(r"[\n,]+", raw) if value.strip()}


def _workflow_allowed(doctype, settings):
	"""True when approvals are enabled for this DocType in chat."""
	allowed = _allowed_workflows(settings)
	return not allowed or (doctype or "").lower() in allowed


# --------------------------------------------------------------------------- #
# Frappe workflow
# --------------------------------------------------------------------------- #
def _load(doctype, docname):
	"""The document, after a permission-filtered existence probe.

	``frappe.get_list`` applies ``permission_query_conditions``, so a row the user
	may not read stops here with ``PermissionError`` rather than as a traceback
	from ``frappe.get_doc`` - or as a record they were never meant to see
	(docs/12 section 1.2, R4).
	"""
	rows = frappe.get_list(
		doctype, filters={"name": docname}, pluck="name", limit_page_length=1
	)
	if not rows:
		raise frappe.PermissionError(_("You don't have permission to perform this action."))
	return frappe.get_doc(doctype, docname)


def _raw_transitions(doc):
	"""The transitions ``frappe.model.workflow`` offers this user for ``doc``.

	The import is local so that importing this module never depends on the
	framework's model layer being ready yet.
	"""
	from frappe.model import workflow as workflow_api

	return workflow_api.get_transitions(doc) or []


def _apply_transition(doc, raw):
	"""Hand one transition to Frappe's workflow engine, which re-checks it.

	The requested identifier is the transition's action label, as Frappe's own UI
	passes it; the document name is the fallback for a release that matches on
	``Workflow Transition.name`` instead.
	"""
	from frappe.model import workflow as workflow_api

	identifier = _read(raw, "action") or _read(raw, "name")
	workflow_api.apply_workflow(doc, identifier)


def _read(source, key):
	"""Read ``key`` from a transition, which may be a dict or a Frappe Document."""
	if hasattr(source, "get"):
		return source.get(key)
	return getattr(source, key, None)


def _is_allowed(value):
	"""Normalise Frappe's availability flag: ``"Yes"``/``"No"``, or a boolean."""
	if value is None:
		return True
	if isinstance(value, str):
		return value.strip().lower() not in ("no", "false", "0")
	if isinstance(value, (bool, int)):
		return bool(value)
	return True


def _normalise(raw):
	"""One Frappe transition -> the keys the chat layer speaks.

	Frappe calls the transition's label ``action``; the caller never sees that
	name, and never has to know what Frappe put in ``allowed``.
	"""
	return {
		"transition": _read(raw, "action") or _read(raw, "name"),
		"state": _read(raw, "state"),
		"next_state": _read(raw, "next_state"),
		"allowed": _is_allowed(_read(raw, "allowed")),
	}


def _available(raws):
	"""Normalise the raw transitions, dropping any Frappe left unlabelled."""
	entries = []
	for raw in raws:
		entry = _normalise(raw)
		if entry["transition"]:
			entries.append(entry)
	return entries


def _match(raws, transition):
	"""The ``(raw, normalised)`` transition whose label matches, or ``(None, None)``."""
	wanted = (transition or "").strip().lower()
	if not wanted:
		return None, None
	for raw in raws:
		entry = _normalise(raw)
		if (entry["transition"] or "").strip().lower() == wanted:
			return raw, entry
	return None, None


def _labels(entries):
	"""The transition labels, for the message that lists what is possible."""
	return ", ".join(str(entry["transition"]) for entry in entries) or _("none")


def _human_state(doc):
	"""The state a person should read: the workflow state, else the DocType status."""
	state = doc.get("workflow_state")
	if state:
		return state
	if frappe.get_meta(doc.doctype).has_field("status"):
		return doc.get("status")
	return None


def _route(doctype, docname):
	"""The Desk URL for the record.

	``frappe.utils.get_url_to_form`` answers an absolute URL or a site-relative
	path depending on release and hook configuration, so it is used only when it
	really is a path; otherwise the default Desk route is built directly.
	"""
	try:
		path = frappe.utils.get_url_to_form(doctype, docname)
	except Exception:
		frappe.log_error(
			title="Solrise chat workflow: could not build a URL for {0}".format(doctype),
			message=frappe.get_traceback(),
		)
		path = None
	if isinstance(path, str) and path.startswith("/"):
		return path
	return "/app/{0}/{1}".format(frappe.scrub(doctype), docname)
