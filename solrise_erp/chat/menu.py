# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The quick-action menu (docs/12 sections 5.1 and 7.6).

The menu is the cheapest part of the flow and the one that removes most ambiguity:
clicking "Tickets" is not a sentence a model has to interpret. It is therefore
built from *permissions* - a module is offered when the user may actually read or
create one of the DocTypes it addresses - never from a role list, so a Customer
never sees Approvals and a Support Agent never sees HR (docs/12 section 7.6).

The module catalogue itself lives in ``chat/registry.py``; this file only decides
which entries survive and how their DocTypes are resolved against Solrise Settings.
"""

from __future__ import annotations

import frappe

from solrise_erp.chat import permissions, registry

HELP = {"key": "help", "label": "Help", "hint": "what I can do"}

KNOWLEDGE = "knowledge"
TASKS = "tasks"
REPORTS = "reports"

# `reports` addresses no transactional DocType: `Report` is the DocType whose read
# permission decides whether the entry is worth showing at all.
REPORTS_DOCTYPE = "Report"

# The knowledge module's own default, used only when neither Solrise Settings nor
# the registry names a DocType.
KNOWLEDGE_FALLBACK = "Solrise FAQ"

ACTIONS = ("read", "create")


def module_doctypes(key, settings=None) -> list:
	"""The DocTypes a module key addresses, honouring the Solrise Settings overrides.

	Three modules are configurable rather than fixed: the knowledge base source, the
	DocTypes "My Tasks" searches, and the report list (docs/12 section 4.2).
	"""
	module = registry.MODULES.get((key or "").strip())
	if not module:
		return []

	if key == KNOWLEDGE:
		configured = _setting(settings, "chat_knowledge_base_doctype")
		if configured:
			return [str(configured).strip()]
		return _registry_doctypes(module) or [KNOWLEDGE_FALLBACK]

	if key == TASKS:
		configured = _split(_setting(settings, "chat_my_tasks_doctypes"))
		if configured:
			return configured
		return [str(name) for name in registry.MY_TASKS_DOCTYPES]

	if key == REPORTS:
		return [REPORTS_DOCTYPE]

	return _registry_doctypes(module)


def build(ctx=None, settings=None) -> list:
	"""The quick-action menu for this user: [{"key", "label", "hint"}].

	``ctx`` is accepted because ``api/chat.py`` has it to hand, but the decision is
	made from DocType permissions rather than from the identity it carries - a menu
	built from a client-supplied context would be a menu a client could widen.

	Exactly three keys are returned per entry: the widget validates ``key`` and
	``label`` and drops anything else, and drops an entry missing either.
	"""
	items = []
	try:
		for key, module in registry.MODULES.items():
			if not _addressable(module_doctypes(key, settings), settings):
				continue
			items.append({
				"key": key,
				"label": str(module.get("label") or key),
				"hint": str(module.get("hint") or ""),
			})
	except Exception:
		_log_error("could not build the menu")
		items = []
	items.append(dict(HELP))
	return items


# --------------------------------------------------------------------------- #
# Permission test
# --------------------------------------------------------------------------- #
def _addressable(doctypes, settings) -> bool:
	"""True when the user may read or create at least one of these DocTypes."""
	for doctype in doctypes:
		for action in ACTIONS:
			if _may_use(doctype, action, settings):
				return True
	return False


def _may_use(doctype, action, settings) -> bool:
	"""True when one DocType may be used with one action, according to Frappe.

	Chat-addressable DocTypes are decided by the shared permission gate, which is
	the single authorization choke-point (docs/12 section 7.3). ``Report`` is not
	one of them - the registry lists no action for it - and asking the gate would
	answer "not chat-addressable" for every user, so its plain read permission
	decides instead. Either way the answer is Frappe's, never a role list.
	"""
	if doctype not in registry.ACTION_REGISTRY:
		return _readable(doctype)
	try:
		return bool(permissions.check(doctype, action, settings=settings).get("allowed"))
	except Exception:
		_log_error("permission check failed for {0}".format(doctype))
		return False


def _readable(doctype) -> bool:
	"""Frappe's own read check on a DocType the chat registry does not list."""
	try:
		if not frappe.db.exists("DocType", doctype):
			return False
		return bool(frappe.has_permission(doctype, ptype="read"))
	except Exception:
		_log_error("read check failed for {0}".format(doctype))
		return False


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _setting(settings, fieldname):
	"""One Solrise Settings value, or ``None`` when it cannot be read.

	An unreadable setting falls back to the registry default for that module: the
	menu may offer less than the deployment configured, never more.
	"""
	try:
		if settings is None:
			settings = frappe.get_cached_doc("Solrise Settings")
		return settings.get(fieldname)
	except Exception:
		_log_error("could not read {0}".format(fieldname))
		return None


def _split(value) -> list:
	"""Newline- or comma-separated DocType names, in order and without repeats."""
	if not value:
		return []
	names = []
	for part in str(value).replace(",", "\n").splitlines():
		name = part.strip()
		if name and name not in names:
			names.append(name)
	return names


def _registry_doctypes(module) -> list:
	"""The DocTypes a registry module declares."""
	return [str(name) for name in module.get("doctypes") or []]


def _log_error(subject):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(
			title="Solrise chat menu: {0}".format(subject),
			message=frappe.get_traceback(),
		)
	except Exception:
		# Logging must not become the failure it is reporting.
		pass
