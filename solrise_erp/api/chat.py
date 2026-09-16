# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The universal chat entry flow's HTTP surface (docs/12 sections 5.1 and 6.2).

Two endpoints, both authenticated - there is no ``allow_guest`` anywhere in this
app, so an anonymous visitor gets a 403 rather than a conversation:

* ``bootstrap`` paints the first turn: identity, greeting, quick actions and the
  non-secret feature flags.
* ``turn`` handles one user turn.

The controller is thin on purpose. It validates the envelope, delegates the
conversation to ``solrise_erp.chat.intent``, and then runs the gates in the
documented order (docs/12 section 7.1): closed registry -> DocType allowlist ->
role and row permission -> schema -> confirmation -> the DocType API, which
re-checks every write itself. User text is never trusted: values reach a record
only after the schema validator has accepted them, and the DocType and action a
model proposed were validated against the registry before either gate ran.

One deliberate deviation from the sketch in docs/12 section 6.2: the missing-field
prompt is discovered inside ``intent.resolve`` but *asked* here, after the
permission gate. That preserves the gate order of the sketch itself (4 before 5) -
prompting for a field of a DocType the user may not write would leak DocType
metadata ahead of the decision that matters.
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, strip_html

from solrise_erp.chat import context, executor, intent, menu, permissions, schema
from solrise_erp.chat.audit import log_event

SETTINGS = "Solrise Settings"

# The verbs a confirmation sentence may use. The chat's own action vocabulary is
# the closed one in `chat.registry`; this is only how it reads in a sentence.
CONFIRM_VERBS = {"delete": "delete", "cancel": "cancel", "submit": "submit", "approve": "approve"}

# Feature flags the widget may know. Never the provider key, the base URL or the
# system prompt: the Desk is not the place for a site's model credentials
# (docs/12 section 7.6).
PUBLIC_SETTINGS = (
	"chat_max_slot_turns",
	"chat_confidence_threshold",
	"chat_allow_delete",
	"chat_allow_approve",
	"chat_enforce_permlevel",
)

# Read by nobody in this package, so they are never even loaded into memory here.
NEVER_LOADED = ("api_key", "system_prompt")


@frappe.whitelist()
def bootstrap():
	"""First paint: identity, quick actions and feature flags for this user."""
	_require_session()
	settings = _settings()
	ctx = context.load(settings)
	enabled = bool(cint(settings.get("enable_universal_chat")))

	body = {
		"ok": True,
		"enabled": enabled,
		"session_id": frappe.generate_hash(length=12),
		"user": ctx["user"],
		"full_name": ctx["full_name"],
		"roles": ctx["roles"],
		"department": ctx["department"],
		"channel": context.channel(),
		"greeting": _("The chat assistant is currently disabled."),
		"menu": [],
		"settings": _public_settings(settings),
	}
	if enabled:
		body["greeting"] = _("Hi {0}, what would you like to do?").format(ctx["full_name"])
		body["menu"] = _menu(settings)
	return body


@frappe.whitelist()
def turn(message=None, action=None, session_id=None, payload=None):
	"""Handle one user turn and return what the widget renders next.

	``message`` is free text and ``action`` a quick-action key or one of the two
	control actions (``__confirm__`` / ``__cancel__``) - the widget sends at most
	one of them. ``payload`` carries the answers collected for a pending intent.
	"""
	user = _require_session()
	settings = _settings()
	session_id = cstr(session_id).strip() or frappe.generate_hash(length=12)
	channel = context.channel()

	if not cint(settings.get("enable_universal_chat")):
		return _reply(session_id, channel, error=_("The chat assistant is disabled."))

	# The model is the only part of this flow that costs money, so the Phase 4
	# hourly limit is consulted only when this turn could reach it (docs/12
	# section 7.5). The deterministic path is permission-gated and free.
	if cint(settings.get("chat_enable_llm_fallback")) and _rate_limited(user):
		return _reply(
			session_id, channel, menu=_menu(settings),
			error=_("Rate limit reached for this hour. Please try again later."),
		)

	parsed = intent.resolve(
		message=strip_html(cstr(message or "")).strip(),
		action=cstr(action or "").strip(),
		payload=_as_dict(payload),
		session_id=session_id,
		settings=settings,
		user=user,
	)
	doctype = parsed.get("doctype")
	action_name = parsed.get("action")

	# -- the user said "no" ----------------------------------------------------
	if parsed.get("cancelled"):
		intent.clear_pending(session_id)
		return _reply(session_id, channel, parsed, reply=_("Okay, cancelled."),
		              menu=_menu(settings))

	# -- gate 1/2: nothing to act on, or an intent that needs words first ------
	if parsed.get("needs_clarification"):
		_audit(channel, session_id, "Intent", parsed)
		field = parsed.get("question")
		return _reply(
			session_id, channel, parsed,
			reply=parsed.get("question_sentence") or "",
			kind="question" if field else None,
			question=field,
			expects=["field"] if field else None,
			menu=None if field else _menu(settings),
		)

	# -- gate 3: the operator's DocType allowlist ------------------------------
	if not _allowlisted(settings, doctype):
		return _denied(session_id, channel, settings, parsed,
		               "doctype not in chat_allowed_doctypes")

	# -- gate 4: role access, then the row rules our own hooks enforce ---------
	decision = permissions.check(
		doctype, action_name, docname=parsed.get("record"), settings=settings
	)
	if not decision.get("allowed"):
		return _denied(session_id, channel, settings, parsed,
		               decision.get("reason") or "permission denied")

	_audit(channel, session_id, "Allowed", parsed, permission_result="allowed")

	# -- gate 5: one answerable question per missing field ---------------------
	missing = parsed.get("missing") or []
	if missing:
		intent.remember_pending(session_id, parsed)
		field = missing[0]
		return _reply(
			session_id, channel, parsed,
			reply=intent.question_reply(field),
			kind="question",
			question=schema.question_for(field),
			expects=["field"],
		)

	# -- gate 6: an irreversible or state-changing action gets a second look ---
	if permissions.needs_confirmation(action_name, settings) and not parsed.get("confirmed"):
		intent.remember_pending(session_id, parsed)
		return _reply(
			session_id, channel, parsed,
			kind="confirm",
			confirm={"summary": _confirmation(parsed)},
			expects=["confirm"],
		)

	# -- gate 7: the DocType API, which re-checks permission on its own --------
	try:
		result = executor.run(parsed, settings=settings)
	except frappe.PermissionError:
		return _denied(session_id, channel, settings, parsed,
		               "secondary permission check failed")
	except Exception as exc:
		_audit(channel, session_id, "Error", parsed, result=str(exc))
		frappe.log_error(title="Solrise chat: the executor failed",
		                 message=frappe.get_traceback())
		return _reply(
			session_id, channel, parsed, menu=_menu(settings),
			reply=_("I could not complete that: {0}").format(str(exc)),
		)

	intent.clear_pending(session_id)
	_audit(
		channel, session_id, "Executed", parsed,
		docname=result.get("name"),
		permission_result="allowed",
		result=json.dumps(result, default=str)[:2000],
		latency_ms=result.get("latency_ms"),
	)
	return _reply(
		session_id, channel, parsed,
		reply=result.get("message") or "",
		kind="result",
		link=result.get("link"),
		menu=_menu(settings),
	)


# --------------------------------------------------------------------------- #
# The reply envelope the widget renders
# --------------------------------------------------------------------------- #
def _reply(session_id, channel, parsed=None, reply=None, kind=None, question=None,
           confirm=None, link=None, expects=None, error=None, menu=None):
	"""One turn's body. ``ok`` is false only when the assistant could not answer.

	A denial is *not* an error: it is an answer, and the widget shows the same
	message plus the menu for it (docs/12 requirement 4).
	"""
	parsed = parsed or {}
	body = {
		"ok": error is None,
		"session_id": session_id,
		"channel": channel,
		"reply": reply or "",
		"kind": kind,
		"question": question,
		"confirm": confirm,
		"link": link,
		"error": error,
		"expects": expects or [],
		"doctype": parsed.get("doctype"),
		"action": parsed.get("action"),
		"record": parsed.get("record"),
		"urgency": parsed.get("urgency"),
	}
	if menu:
		body["menu"] = menu
	return body


def _denied(session_id, channel, settings, parsed, reason):
	"""The one message a denial gets, audited whichever gate produced it."""
	_audit(channel, session_id, "Denied", parsed, permission_result="denied", result=reason)
	return _reply(
		session_id, channel, parsed, menu=_menu(settings),
		reply=_("You don't have permission to perform this action."),
	)


def _confirmation(parsed):
	"""The sentence the widget prints above its Yes/No pair."""
	verb = parsed.get("transition") or CONFIRM_VERBS.get(parsed.get("action")) or parsed.get("action")
	words = [str(part) for part in (verb, parsed.get("doctype"), parsed.get("record")) if part]
	return _("This will {0}.").format(" ".join(words))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _require_session():
	"""No session, no chat. Raises 403 for a Guest, as docs/12 section 5.1 requires."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return user


def _settings():
	"""The config Single, or a plain mapping of it when this user may not read it.

	A Portal user has no read permission on the config Single and the chat still
	has to work there (docs/12 section 7.6). The fallback reads the row directly -
	the same trust level as any other server-side configuration access - and the
	provider key and the prompt are left out of it entirely, so nothing in this
	package can leak them even by accident.
	"""
	try:
		return frappe.get_cached_doc(SETTINGS)
	except Exception:
		values = {}
		try:
			values = dict(frappe.db.get_singles_dict(SETTINGS) or {})
		except Exception:
			_log_error("could not read {0}".format(SETTINGS))
		for fieldname in NEVER_LOADED:
			values.pop(fieldname, None)
		return values


def _public_settings(settings):
	"""The few flags the widget is told about; nothing secret leaves here."""
	body = {fieldname: settings.get(fieldname) for fieldname in PUBLIC_SETTINGS}
	body["llm_fallback"] = bool(cint(settings.get("chat_enable_llm_fallback")))
	return body


def _menu(settings=None):
	"""The quick actions to offer. Never rendered while a question is pending."""
	try:
		return menu.build(None, settings)
	except Exception:
		_log_error("could not build the menu")
		return []


def _allowlisted(settings, doctype):
	"""Gate 3: the operator's allowlist. Empty means every addressable DocType."""
	raw = cstr(settings.get("chat_allowed_doctypes") or "")
	names = [name.strip() for name in re.split(r"[\n,]+", raw) if name.strip()]
	return not names or doctype in names


def _audit(channel, session_id, event_type, parsed=None, **extra):
	"""Write one audit row. A failure here must never change the answer."""
	parsed = parsed or {}
	payload = {
		"channel": channel,
		"session_id": session_id,
		"event_type": event_type,
		"doctype": parsed.get("doctype"),
		"docname": parsed.get("record"),
		"action": parsed.get("action"),
		"urgency": parsed.get("urgency"),
		"intent_raw": parsed.get("raw"),
		"slots": parsed.get("slots"),
	}
	payload.update(extra)
	try:
		log_event(**payload)
	except Exception:
		_log_error("could not write the audit log")


def _rate_limited(user):
	"""The Phase 4 per-user hourly cap, reused for the model path."""
	try:
		from solrise_erp.assistant import engine

		return bool(engine.rate_limited(user))
	except Exception:
		return False


def _as_dict(value):
	"""The widget sends its collected answers as a JSON string; accept either.

	A malformed payload is treated as no answers rather than as a failure: the
	alternative is a turn that dies for a reason the user cannot act on.
	"""
	if not value:
		return {}
	if isinstance(value, dict):
		return value
	if isinstance(value, str):
		try:
			loaded = json.loads(value)
		except ValueError:
			return {}
		if isinstance(loaded, dict):
			return loaded
	return {}


def _log_error(subject):
	try:
		frappe.log_error(title="Solrise chat: {0}".format(subject), message=frappe.get_traceback())
	except Exception:
		pass
