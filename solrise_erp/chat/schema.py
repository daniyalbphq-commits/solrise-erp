# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Schema-driven conversation: what is missing, and how to ask for it (docs/12 6.4).

Four jobs, all read-only:

* :func:`missing_required` walks ``frappe.get_meta(doctype).fields`` and reports
  the user-fillable required fields the intent has not answered, in form order.
  Layout fields, framework-owned fields and fields Frappe fills itself are
  skipped, and so are conditionally hidden ones and ``permlevel > 0`` fields
  while ``chat_enforce_permlevel`` is on (docs/12 section 10).
* :func:`question_for` / :func:`next_question` turn one such field into the
  payload the widget renders and the sentence the user reads.
* :func:`validate_value` checks one answer against the field's own rules, and
  reports a bad one as a value - never as a traceback.
* :func:`apply_urgency` maps the parsed urgency onto the configured field, but
  only where the DocType can actually hold the value.

``depends_on`` and ``mandatory_depends_on`` are evaluated with
``frappe.safe_eval`` only, with the intent's own slots as locals: a restricted
evaluator, so user text can never name a callable here. An expression it cannot
resolve - the Desk writes ``eval:doc.x`` - counts as *met*, so the chat asks one
question too many rather than writing data the form would have hidden.

Nothing here reads or writes a record and nothing here decides whether the user
may act; that is ``chat.permissions``.
"""

from __future__ import annotations

import math

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, get_datetime, getdate

# Fields that carry no user input at all.
LAYOUT_FIELDTYPES = {
	"Section Break",
	"Column Break",
	"Tab Break",
	"Fold",
	"Heading",
	"Button",
	"HTML",
}

# Auto-filled by the framework, by naming or by a fetch - never ask the user.
AUTO_FIELDS = {
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"idx",
	"docstatus",
	"parent",
	"parentfield",
	"parenttype",
	"naming_series",
	"_user_tags",
	"_comments",
	"_assign",
	"_liked_by",
}

# Answers that have to parse before Frappe will accept them.
NUMBER_FIELDTYPES = ("Int", "Float", "Currency", "Percent")
OPTION_FIELDTYPES = ("Select", "Autocomplete")
CHECK_TRUE = {"1", "true", "yes", "y", "on", "checked"}

# The only urgencies the parser can produce (chat.registry / chat.nlp).
URGENCIES = ("Low", "Medium", "High", "Urgent")


def missing_required(doctype, values=None, settings=None):
	"""The user-fillable required fields still missing, in form order.

	Each item is ``{fieldname, label, fieldtype, options, reqd, permlevel,
	description}``; :func:`question_for` trims it to what the widget renders. A
	DocType outside ``chat_allowed_doctypes`` is addressable by nothing and so
	reports no fields, and an unreadable DocType does the same instead of raising -
	the executor's own Frappe call is what ultimately refuses.
	"""
	try:
		values = dict(values or {})
		settings = _config(settings)
		if not _allowlisted(doctype, settings):
			return []
		meta = frappe.get_meta(doctype)
	except Exception:
		frappe.log_error(
			title="Solrise chat schema: could not inspect {0}".format(doctype),
			message=frappe.get_traceback(),
		)
		return []

	enforce_permlevel = _enforce_permlevel(settings)
	missing = []

	for df in meta.fields:
		fieldname = cstr(df.fieldname or "")
		if not fieldname:
			continue
		if cstr(df.fieldtype) in LAYOUT_FIELDTYPES or fieldname in AUTO_FIELDS:
			continue
		if df.read_only or df.fetch_from or df.default:
			# Filled by the framework, by a fetch, or by the field's own default.
			continue
		if not df.reqd and not df.mandatory_depends_on:
			continue
		if df.get("depends_on") and not _condition_met(df.depends_on, values):
			continue  # hidden in the state the intent is building
		if df.mandatory_depends_on and not _condition_met(df.mandatory_depends_on, values):
			continue
		if enforce_permlevel and cint(df.permlevel) > 0:
			continue
		if not _is_empty(values.get(fieldname)):
			continue
		missing.append(
			{
				"fieldname": fieldname,
				"label": cstr(df.label or "") or fieldname,
				"fieldtype": cstr(df.fieldtype or "Data"),
				"options": cstr(df.options or ""),
				"reqd": cint(df.reqd),
				"permlevel": cint(df.permlevel),
				"description": cstr(df.description or ""),
			}
		)

	return missing


def question_for(field):
	"""The payload the widget renders for one missing field.

	Only four keys are understood client-side and they keep their real names and
	types: ``Select`` becomes a ``<select>``, ``Date`` a date input,
	``Int``/``Float`` a number input and anything else a text input, so the
	Frappe ``fieldtype`` is passed through unchanged and ``options`` is always a
	string (``""`` when the field has none). ``description`` is added only when the
	DocType documents the field, and the widget shows it as help text.
	"""
	field = field or {}
	fieldname = cstr(field.get("fieldname") or "")
	payload = {
		"fieldname": fieldname,
		"label": cstr(field.get("label") or "") or fieldname,
		"fieldtype": cstr(field.get("fieldtype") or "Data"),
		"options": cstr(field.get("options") or ""),
	}
	description = cstr(field.get("description") or "")
	if description:
		payload["description"] = description
	return payload


def next_question(field):
	"""One answerable question for a missing field (docs/12 section 6.4)."""
	field = field or {}
	fieldtype = cstr(field.get("fieldtype") or "")
	options = cstr(field.get("options") or "")
	label = (cstr(field.get("label") or "") or cstr(field.get("fieldname") or "")).lower()

	if fieldtype == "Link":
		return _("{0}? (enter the exact {1} ID)").format(label.capitalize(), options)
	if fieldtype in OPTION_FIELDTYPES:
		return _("{0}? (one of: {1})").format(label.capitalize(), " / ".join(_options(options)))
	if fieldtype == "Date":
		return _("What {0}? (YYYY-MM-DD)").format(label)
	return _("Please provide {0}.").format(label)


def validate_value(doctype, fieldname, value):
	"""Check one answer against the field's own rules, without ever raising.

	Returns ``{"ok": bool, "value": <coerced>, "error": str|None}``. Link and
	Dynamic Link answers are checked for existence, Select / Autocomplete against
	the field's choices, numbers numerically, dates with ``frappe.utils`` and Check
	fields to 0/1; every other type comes back stripped if it was a string. A blank
	answer is an error rather than a value: the flow only ever asks for a required
	slot, and a blank Date would otherwise quietly become today.
	"""
	fieldname = cstr(fieldname or "")
	try:
		meta = frappe.get_meta(doctype)
		df = meta.get_field(fieldname)
		if not df:
			return _error(_("Unknown field {0}.").format(fieldname))
		if value is None or (isinstance(value, str) and not value.strip()):
			return _error(_("Please provide {0}.").format(cstr(df.label or "") or fieldname))
		return _coerce(df, value)
	except Exception:
		frappe.log_error(
			title="Solrise chat schema: could not validate {0}.{1}".format(doctype, fieldname),
			message=frappe.get_traceback(),
		)
		return _error(_("'{0}' could not be validated.").format(fieldname))


def apply_urgency(doctype, values, urgency, settings=None):
	"""A copy of ``values`` with the parsed urgency on ``chat_urgency_field``.

	The urgency is a business signal, never an authorisation: when the setting is
	empty, names a field the DocType does not have, or names one that cannot hold
	the value (a Select without it, a Link target that does not exist, a number, a
	date), the dict comes back untouched and the urgency is only audited.
	``urgency`` itself must be one of the four the parser produces. Nothing here
	widens permissions (docs/12 section 10).
	"""
	values = dict(values or {})
	if urgency not in URGENCIES:
		return values
	try:
		fieldname = cstr(_setting(_config(settings), "chat_urgency_field") or "").strip()
		if not fieldname:
			return values
		answer = validate_value(doctype, fieldname, urgency)
		if not answer["ok"]:
			return values
		values[fieldname] = answer["value"]
		return values
	except Exception:
		frappe.log_error(
			title="Solrise chat schema: could not apply urgency to {0}".format(doctype),
			message=frappe.get_traceback(),
		)
		return values


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #
def _coerce(df, value):
	"""The type-specific half of :func:`validate_value`."""
	fieldname = cstr(df.fieldname or "")
	fieldtype = cstr(df.fieldtype or "Data")
	text = value.strip() if isinstance(value, str) else value

	if fieldtype == "Check":
		if isinstance(text, str):
			return _ok(1 if text.lower() in CHECK_TRUE else 0)
		return _ok(1 if cint(text) else 0)

	if fieldtype in NUMBER_FIELDTYPES:
		try:
			number = float(text)
		except (TypeError, ValueError):
			return _error(_("'{0}' should be a number.").format(fieldname))
		if not math.isfinite(number):
			# A NaN would reach the database as a broken float.
			return _error(_("'{0}' should be a number.").format(fieldname))
		return _ok(cint(number) if fieldtype == "Int" else flt(text))

	if fieldtype == "Date":
		return _ok(cstr(getdate(text)))

	if fieldtype == "Datetime":
		return _ok(cstr(get_datetime(text)))

	if fieldtype == "Select":
		return _choice_answer(_options(df.options), text)

	if fieldtype == "Autocomplete":
		target = _link_target(df)
		if target:
			return _link_answer(target, text)
		return _choice_answer(_options(df.options), text)

	if fieldtype in ("Link", "Dynamic Link"):
		return _link_answer(_link_target(df), text)

	return _ok(text.strip() if isinstance(text, str) else text)


def _choice_answer(allowed, value):
	"""Accept ``value`` when it is one of a Select's literal choices."""
	if allowed and cstr(value) not in allowed:
		return _error(_("Choose one of: {0}").format(", ".join(allowed)))
	return _ok(cstr(value))


def _link_answer(target, value):
	"""Accept ``value`` when it names an existing document of ``target``.

	A Dynamic Link stores the *name of the field* that holds its target DocType,
	which no answer can be resolved against, so an unresolvable target is left to
	Frappe's own link validation on insert/save (docs/12 section 7.4).
	"""
	if target and not frappe.db.exists(target, cstr(value)):
		return _error(_("No {0} named '{1}' exists.").format(target, cstr(value)))
	return _ok(cstr(value))


def _link_target(df):
	"""The DocType a Link / Autocomplete / Dynamic Link points at, or ``""``.

	A ``Link`` names its target in ``options``. An ``Autocomplete`` may offer a
	Link's documents instead of literal choices, in which case ``options`` names a
	DocType too. A ``Dynamic Link`` stores the name of the field that holds its
	target, which no single answer can be resolved against.
	"""
	options = cstr(df.options or "")
	if cstr(df.fieldtype) == "Link":
		return options
	if options and frappe.db.exists("DocType", options):
		return options
	return ""


def _options(options):
	"""The literal choices of a Select / Autocomplete field, trimmed."""
	return [line.strip() for line in cstr(options or "").splitlines() if line.strip()]


def _ok(value):
	return {"ok": True, "value": value, "error": None}


def _error(message):
	return {"ok": False, "value": None, "error": message}


def _is_empty(value):
	"""True for the shapes an unanswered slot takes.

	``0`` and ``False`` are answers (a Check the user cleared), not blanks.
	"""
	if value is None:
		return True
	if isinstance(value, (str, list, tuple, dict, set)):
		return len(value) == 0
	return False


def _condition_met(expression, values):
	"""Evaluate a server-authored ``depends_on`` / ``mandatory_depends_on``.

	Only expressions that live in DocType metadata reach this function - never
	user text - and they are evaluated with ``frappe.safe_eval``, the restricted
	evaluator Assignment Rules use; there is no server-side ``depends_on``
	evaluator to borrow. An expression that cannot be evaluated counts as *met*:
	asking one extra question is the safe side of getting this wrong.
	"""
	if not expression:
		return True
	try:
		return bool(frappe.safe_eval(expression, None, dict(values or {})))
	except Exception:
		frappe.log_error(
			title="Solrise chat schema: could not evaluate a depends_on expression",
			message=frappe.get_traceback(),
		)
		return True


def _config(settings):
	"""The caller's settings document, or the cached Single when none was passed."""
	if settings is not None:
		return settings
	try:
		return frappe.get_cached_doc("Solrise Settings")
	except Exception:
		frappe.log_error(
			title="Solrise chat schema: could not read Solrise Settings",
			message=frappe.get_traceback(),
		)
		return None


def _setting(settings, fieldname):
	"""One field of the settings, or ``None`` when it is unset or unavailable."""
	return settings.get(fieldname) if settings is not None else None


def _enforce_permlevel(settings):
	"""``chat_enforce_permlevel``, on unless the setting says otherwise."""
	value = _setting(settings, "chat_enforce_permlevel")
	return True if value is None else bool(cint(value))


def _allowlisted(doctype, settings):
	"""``chat_allowed_doctypes``: newline separated, empty means every DocType."""
	allowed = [
		line.strip()
		for line in cstr(_setting(settings, "chat_allowed_doctypes") or "").splitlines()
		if line.strip()
	]
	return not allowed or doctype in allowed
