# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The single authorization choke-point for the chat entry flow (docs/12
sections 5.3, 6.3 and 7.3).

Nothing in ``chat/`` may touch a DocType record without passing through here. The
gate never grants more access than Frappe already allows: every branch either
denies or defers to ``frappe.has_permission``, and a refusal is a return value
rather than an exception.

Two closed vocabularies decide what may even be asked:

* ``registry.ACTION_REGISTRY`` - the DocTypes and actions the chat knows about.
* ``ACTION_TO_PTYPE`` - the only actions that may become a Frappe permission
  type. User or model text never reaches ``frappe.has_permission`` as a ``ptype``.

Row rules are never re-implemented here: records are resolved through
``frappe.get_list`` (which applies ``permission_query_conditions``) and documents
are checked with ``frappe.has_permission(doc=...)`` so this app's own
``has_permission`` hooks run (docs/12 section 7.4).
"""

import frappe
from frappe import _
from frappe.utils import cint

from solrise_erp.chat import registry

# Closed vocabulary: chat action -> Frappe permission type. This is the twin of
# `registry.ACTION_PTYPE`; `approve` is deliberately absent from both because it
# resolves through `is_submittable` below and must never collapse into a write.
ACTION_TO_PTYPE = {
	"read": "read",
	"list": "read",
	"create": "create",
	"update": "write",
	"submit": "submit",
	"cancel": "cancel",
	"delete": "delete",
}

# State transitions and irreversible actions always get a confirmation turn.
CONFIRM_ACTIONS = {"delete", "cancel", "submit", "approve"}

# Administrative DocTypes are refused outright: a permission check on them is a
# privilege-escalation path, not a business action the chat may broker.
ADMIN_DOCTYPES = (
	"DocType",
	"Role",
	"User",
	"Custom DocPerm",
	"User Permission",
	"User Role",
	"Role Profile",
)


def _flag(settings, fieldname):
	"""Read a Check field from Solrise Settings, from a Document or a mapping.

	An unreadable or absent setting answers `False`, so a settings failure narrows
	access instead of widening it.
	"""
	if settings is None:
		return False
	try:
		if hasattr(settings, "get"):
			return bool(cint(settings.get(fieldname)))
		return bool(cint(getattr(settings, fieldname, 0)))
	except Exception:
		frappe.log_error(
			title="Solrise chat permissions: could not read {0}".format(fieldname),
			message=frappe.get_traceback(),
		)
		return False


def resolve_record(doctype, name):
	"""Return the record name if the user may read it, else raise PermissionError.

	``frappe.get_list`` applies ``permission_query_conditions``, so a row the user
	may not see simply does not come back, while ``frappe.get_doc`` on its own
	would raise a raw ``DoesNotExistError`` or hand over a record that was never
	theirs to read (docs/12 section 7.4).
	"""
	if not name:
		raise frappe.PermissionError(_("You don't have permission to perform this action."))
	rows = frappe.get_list(
		doctype, filters={"name": name}, pluck="name", limit_page_length=1
	)
	if not rows:
		raise frappe.PermissionError(_("You don't have permission to perform this action."))
	return rows[0]


def check(doctype, action_name, docname=None, settings=None):
	"""Decide whether ``action_name`` on ``doctype`` is allowed for this user.

	Returns ``{"allowed": True, "ptype": ...}`` only when every gate passes, and
	``{"allowed": False, "reason": ...}`` otherwise - a normal denial is never an
	exception. ``ptype`` always comes from the closed vocabularies above, so an
	injected action string cannot expand the permission set. This function is free
	of side effects; :func:`authorize` is the audited entry point.
	"""
	if not doctype or not frappe.db.exists("DocType", doctype):
		return {"allowed": False, "reason": _("Unknown DocType {0}.").format(doctype)}
	if doctype in ADMIN_DOCTYPES:
		return {
			"allowed": False,
			"reason": _("Administrative DocTypes are not chat-addressable."),
		}
	# The registry is the first gate (docs/12 section 1.3): a DocType or action it
	# does not list is never chat-addressable, whatever Frappe would allow.
	if action_name not in registry.ACTION_REGISTRY.get(doctype, []):
		return {
			"allowed": False,
			"reason": _("{0} is not a chat action for {1}.").format(action_name, doctype),
		}

	if action_name == "approve":
		if not _flag(settings, "chat_allow_approve"):
			return {"allowed": False, "reason": _("Approvals are disabled in chat.")}
	elif action_name == "delete":
		if not _flag(settings, "chat_allow_delete"):
			return {"allowed": False, "reason": _("Deletion is disabled in chat.")}

	try:
		if action_name == "approve":
			# The transition itself is authorised in `chat/workflow.py` against the
			# user's workflow roles; this coarse gate only asks for submit-level
			# access to the DocType, never a plain write.
			ptype = "submit" if frappe.get_meta(doctype).is_submittable else "write"
		elif action_name == "delete":
			ptype = "delete"
		else:
			ptype = ACTION_TO_PTYPE.get(action_name)

		if not ptype:
			return {
				"allowed": False,
				"reason": _("Unsupported action '{0}'.").format(action_name),
			}
		# Meta-level (role matrix) check first: cheap, and it fails closed for a
		# role that may only create, for example.
		if not frappe.has_permission(doctype, ptype=ptype):
			return {
				"allowed": False,
				"reason": _("No {0} permission on {1}.").format(ptype, doctype),
			}
		# Document-level check for row rules (this app's `has_permission` hooks).
		# `create` is skipped: the owner field does not exist yet.
		if docname and ptype != "create":
			allowed = frappe.has_permission(
				doctype, ptype=ptype, doc=frappe.get_doc(doctype, docname)
			)
			if not allowed:
				return {
					"allowed": False,
					"reason": _("Row-level permission denied for {0}.").format(docname),
				}
	except frappe.DoesNotExistError:
		return {"allowed": False, "reason": _("Record does not exist.")}
	except frappe.PermissionError:
		return {"allowed": False, "reason": _("Permission denied.")}
	except Exception:
		# An unexpected failure is a denial, never an allow: the gate may only ever
		# narrow what Frappe already permits.
		frappe.log_error(
			title="Solrise chat permissions: check failed for {0}".format(doctype),
			message=frappe.get_traceback(),
		)
		return {"allowed": False, "reason": _("Permission could not be verified.")}

	return {"allowed": True, "ptype": ptype}


def can(doctype, action_name, settings=None):
	"""Boolean form of :func:`check` for callers that only need the verdict.

	The answer is meta-level: pass the record to :func:`check` (or
	:func:`authorize`) when it has to be row-specific.
	"""
	return bool(check(doctype, action_name, settings=settings).get("allowed"))


def needs_confirmation(action_name, settings):
	"""True when this action needs an explicit confirmation turn first.

	``delete`` and ``approve`` are opt-in features, so they ask for a confirmation
	only when Solrise Settings has them enabled; the actions in
	``CONFIRM_ACTIONS`` that are always available do so unconditionally.
	"""
	if action_name not in CONFIRM_ACTIONS:
		return False
	if action_name == "delete":
		return _flag(settings, "chat_allow_delete")
	if action_name == "approve":
		return _flag(settings, "chat_allow_approve")
	return True


def authorize(doctype, action_name, docname=None, settings=None, session_id=None,
              channel=None, raw=None, urgency=None, slots=None):
	"""Gate one action and record the decision, in a single call.

	This is the audit-and-gate entry point from docs/12 section 5.3: the verdict
	from :func:`check` is returned unchanged, and the Allowed/Denied row is written
	either way so that a denial is as visible as a success (docs/12 section 1.2,
	R8). A failing audit write narrows nothing - it is logged and swallowed.
	"""
	decision = check(doctype, action_name, docname=docname, settings=settings)
	_log_decision(
		decision,
		doctype=doctype,
		action_name=action_name,
		docname=docname,
		session_id=session_id,
		channel=channel,
		raw=raw,
		urgency=urgency,
		slots=slots,
	)
	return decision


def _log_decision(decision, doctype, action_name, docname, session_id, channel, raw,
                  urgency, slots):
	"""Append the gate's verdict to the audit log, never raising if that fails.

	``log_event`` is imported here rather than at module level because
	``chat/audit.py`` belongs to the audit writer: a problem there must not stop
	the gate from loading (docs/12 section 6.6).
	"""
	try:
		from solrise_erp.chat.audit import log_event

		log_event(
			session_id=session_id,
			channel=channel,
			event_type="Allowed" if decision.get("allowed") else "Denied",
			doctype=doctype,
			docname=docname,
			action=action_name,
			urgency=urgency,
			slots=slots,
			permission_result="allowed" if decision.get("allowed") else "denied",
			result=decision.get("reason"),
			intent_raw=raw,
		)
	except Exception:
		frappe.log_error(
			title="Solrise chat: could not write the audit log",
			message=frappe.get_traceback(),
		)
