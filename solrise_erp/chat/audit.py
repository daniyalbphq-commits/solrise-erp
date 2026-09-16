# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The audit writer (docs/12 sections 4.1, 6.6 and 7.5).

One row per decision - Intent, Allowed, Denied, Executed, Error - so that a denial
is as visible as a success (R8) and an abuse pattern leaves a trail instead of a
gap. Rows are insert-only: the DocType grants no ``create`` to any role, and the
insert itself passes ``ignore_permissions=True``, which is the single documented
exception to the no-bypass rule in docs/12 section 7.3 and the reason this file,
and only this file, may do it.

Two things never happen here. An exception never leaves the module: losing an
answer the user is waiting for because a log line failed would trade a small
audit gap for a broken conversation. And a credential-like slot value never
reaches the database: see :func:`redact`.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import cint, now_datetime

DOCTYPE = "Solrise AI Audit Log"

# The columns the DocType declares (docs/12 section 4.1). A caller's unknown keys
# are dropped rather than forwarded, so a typo cannot fail the insert.
#
# The target column is `target_doctype`, not `doctype`: `doctype` is one of the
# reserved keywords a Frappe `Document` refuses to set, so a free-text column with
# that name can never hold anything but the row's own DocType. Callers still pass
# `doctype=` (that is the name the design uses for the intent's DocType), and
# :func:`_clean` maps it onto the real column.
FIELDS = (
	"session_id",
	"user",
	"timestamp",
	"channel",
	"event_type",
	"target_doctype",
	"docname",
	"action",
	"urgency",
	"intent_raw",
	"slots",
	"permission_result",
	"result",
	"latency_ms",
	"ip_address",
)

# Set by this writer, never by the caller: the audit row must state the user who
# acted, the server's clock and the request's own address, whatever it was told.
WRITER_FIELDS = ("user", "timestamp", "ip_address")

CHANNELS = ("Desk", "Portal", "API")
EVENTS = ("Intent", "Allowed", "Denied", "Executed", "Error")
ACTIONS = ("read", "create", "update", "submit", "cancel", "delete", "approve")
URGENCIES = ("Low", "Medium", "High", "Urgent")
PERMISSION_RESULTS = ("allowed", "denied", "na")

ERROR = "Error"
NA = "na"

# A slot name matched case-insensitively as a substring, so `api_key`, `api-key`
# and `X-API-KEY` are all caught. `session_id` is deliberately absent: it is an
# identifier, and redacting it would break correlation with Solrise Chat Log.
REDACT_KEYS = (
	"password",
	"passwd",
	"passphrase",
	"secret",
	"token",
	"api_key",
	"apikey",
	"authorization",
	"auth",
	"bearer",
	"otp",
	"pin",
	"cvv",
	"card",
	"iban",
	"credential",
	"private_key",
)

REDACTED = "***"
REDACT_MAX_DEPTH = 4

SLOTS_LIMIT = 4000
RESULT_LIMIT = 2000
INTENT_LIMIT = 2000
NAME_LIMIT = 140

# `session_id` is mandatory, so a turn that arrives without one would otherwise
# lose its audit row entirely; a placeholder keeps the row and still tells the
# reader it belongs to no session.
NO_SESSION = "no-session"


def redact(value, depth=0):
	"""Recursively replace credential-like values with '***' before they are stored.

	Matching is on the *key*, not the value, so ``{"api_key": "sk-live-..."}``
	becomes ``{"api_key": "***"}`` while ``{"subject": "reset my password"}`` is
	left alone. Past ``REDACT_MAX_DEPTH`` a container is replaced whole rather
	than stringified: echoing a deeper sub-tree as text would put back exactly the
	credential this function exists to remove.
	"""
	if isinstance(value, dict):
		if depth >= REDACT_MAX_DEPTH:
			return REDACTED
		return {
			key: REDACTED if _is_secret(key) else redact(item, depth + 1)
			for key, item in value.items()
		}
	if isinstance(value, (list, tuple, set, frozenset)):
		if depth >= REDACT_MAX_DEPTH:
			return REDACTED
		return [redact(item, depth + 1) for item in value]
	if value is None or isinstance(value, (bool, int, float)):
		return value
	return str(value)


def log_event(**kwargs) -> str | None:
	"""Append one audit row. Logging must never break the conversation.

	Returns the new row's name, or ``None`` when nothing could be written - the
	caller is told nothing else, because there is nothing it could usefully do.
	"""
	try:
		values = _clean(kwargs)
		doc = frappe.get_doc(dict(values, doctype=DOCTYPE))
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		return doc.name
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			title="Solrise chat: could not write the audit log",
			message=frappe.get_traceback(),
		)
		return None


# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
def _is_secret(key) -> bool:
	"""True when a slot name looks like a credential."""
	name = str(key or "").lower()
	return any(marker in name for marker in REDACT_KEYS)


# --------------------------------------------------------------------------- #
# Row building
# --------------------------------------------------------------------------- #
def _clean(raw) -> dict:
	"""Coerce caller kwargs into the row's own columns.

	Every Select is narrowed to its vocabulary and every free-text column is
	truncated to what the design allows, so a value that reached a caller - from
	user text, from a record or from a model - cannot widen a column's meaning.
	"""
	values = {
		name: raw.get(name)
		for name in FIELDS
		if name in raw and name not in WRITER_FIELDS
	}
	# `doctype` is what callers pass (the intent's DocType); the column is
	# `target_doctype`. Accepting both means a caller can use the column name too.
	values["target_doctype"] = raw.get("target_doctype") or raw.get("doctype")
	return {
		"session_id": _text(values.get("session_id"), NAME_LIMIT) or NO_SESSION,
		"user": _text(getattr(frappe.session, "user", None), NAME_LIMIT) or "Guest",
		"timestamp": now_datetime(),
		"channel": _choice(values.get("channel"), CHANNELS),
		"event_type": _choice(values.get("event_type"), EVENTS, ERROR),
		"target_doctype": _text(values.get("target_doctype"), NAME_LIMIT),
		"docname": _text(values.get("docname"), NAME_LIMIT),
		"action": _choice(values.get("action"), ACTIONS),
		"urgency": _choice(values.get("urgency"), URGENCIES),
		"intent_raw": _text(values.get("intent_raw"), INTENT_LIMIT),
		"slots": _slots(values.get("slots")),
		"permission_result": _choice(
			values.get("permission_result"), PERMISSION_RESULTS, NA
		),
		"result": _text(values.get("result"), RESULT_LIMIT),
		"latency_ms": cint(values.get("latency_ms")),
		"ip_address": _text(getattr(frappe.local, "request_ip", None), NAME_LIMIT),
	}


def _choice(value, allowed, default=None):
	"""Narrow a value to a Select field's vocabulary, else ``default``."""
	text = str(value).strip() if value is not None else ""
	return text if text in allowed else default


def _text(value, limit, default=None):
	"""A string fit for a text column, truncated to ``limit`` characters.

	Mappings and lists are JSON-encoded rather than rejected, so passing an
	executor result or a slot mapping where text is expected is not an error.
	"""
	if value is None:
		return default
	if not isinstance(value, str):
		try:
			value = json.dumps(value, default=str)
		except Exception:
			value = str(value)
	return value[:limit]


def _slots(value):
	"""The ``slots`` Code column: redacted, encoded as JSON, then truncated.

	A string is parsed first - the parser and the executor pass a mapping, while a
	caller holding a stored intent may hand over JSON that is already encoded -
	and the result is redacted after parsing, so a credential cannot hide inside
	the encoding.
	"""
	if value is None:
		return None
	if isinstance(value, str):
		loaded = _load_json(value)
		if loaded is not None:
			value = loaded
	try:
		return json.dumps(redact(value), default=str)[:SLOTS_LIMIT]
	except Exception:
		return json.dumps({"raw": str(value)})[:SLOTS_LIMIT]


def _load_json(text):
	"""The value encoded in ``text``, or ``None`` when it is not JSON."""
	try:
		return json.loads(text)
	except Exception:
		return None
