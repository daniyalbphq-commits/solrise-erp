# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Who is asking, and on which surface (docs/12 sections 5.1 and 7.6).

The chat flow is one widget on three surfaces - Desk, Portal and a bare API call -
and everything downstream starts from the identity loaded here: the quick-action
menu is built from these roles and permissions, and the audit row's ``channel``
comes from :func:`channel`.

Nothing in this module raises. It runs on the first paint of the widget, where an
unreadable User Permission or a User document without a time zone must cost a
detail in the panel, not the panel itself.
"""

from __future__ import annotations

import frappe
from frappe.utils import get_fullname

DESK = "Desk"
PORTAL = "Portal"
API = "API"

DEPARTMENT = "Department"
WEBSITE_USER = "Website User"
SYSTEM_MANAGER = "System Manager"
GUEST = "Guest"

# Returned when the context cannot be assembled at all. Copies of it are handed
# out so a caller mutating the dict cannot change the fallback for the next one.
GUEST_CONTEXT = {
	"user": GUEST,
	"full_name": GUEST,
	"roles": [],
	"department": None,
	"timezone": None,
	"is_admin": False,
	"user_permissions": {},
}


def channel() -> str:
	"""'Desk' | 'Portal' | 'API' - one of the three values the audit DocType allows."""
	try:
		request = getattr(frappe.local, "request", None)
		if request is None:
			# A console, `bench execute` or a scheduler job has no HTTP caller;
			# guessing "Desk" here would make the audit row lie about its origin.
			return API
		if "/app" in _path(request) or "/app" in _referer(request):
			# Both surfaces post to `/api/method/...`, so the path alone cannot tell
			# them apart - the browser's Referer is what names the surface.
			return DESK
		return PORTAL if _is_website_only() else DESK
	except Exception:
		_log_error("channel detection")
		return DESK


def load(settings=None) -> dict:
	"""Everything the menu and the controller need to know about the caller.

	``settings`` is accepted for signature symmetry with the other chat entry
	points; this context is deliberately settings-free, so it stays loadable while
	Solrise Settings is unreadable.
	"""
	try:
		user = _session_user()
		roles = _roles(user)
		user_permissions = _user_permissions(user)
		return {
			"user": user,
			"full_name": _full_name(user),
			"roles": roles,
			"department": _department(user_permissions),
			"timezone": _timezone(user),
			"is_admin": SYSTEM_MANAGER in roles,
			"user_permissions": user_permissions,
		}
	except Exception:
		_log_error("context load")
		return dict(GUEST_CONTEXT)


# --------------------------------------------------------------------------- #
# Surface
# --------------------------------------------------------------------------- #
def _path(request) -> str:
	"""The request path, whichever attribute this release exposes it on."""
	for attribute in ("path", "full_path", "url"):
		value = getattr(request, attribute, None)
		if value:
			return str(value)
	return ""


def _referer(request) -> str:
	"""The browser's ``Referer`` header, or ``""`` when the request has none."""
	headers = getattr(request, "headers", None)
	if headers is not None:
		value = headers.get("Referer") or headers.get("referer")
		if value:
			return str(value)
	environ = getattr(request, "environ", None)
	if isinstance(environ, dict):
		return str(environ.get("HTTP_REFERER") or "")
	return ""


def _is_website_only(user=None) -> bool:
	"""True for a Portal visitor: a Website User who is not a System Manager."""
	roles = set(_roles(user or _session_user()))
	return WEBSITE_USER in roles and SYSTEM_MANAGER not in roles


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #
def _session_user() -> str:
	"""The signed-in user, or ``"Guest"`` when the session cannot be read."""
	try:
		return str(getattr(frappe.session, "user", None) or GUEST)
	except Exception:
		_log_error("session user")
		return GUEST


def _roles(user) -> list:
	"""The user's roles as a list, or ``[]`` when they cannot be read."""
	try:
		return [str(role) for role in frappe.get_roles(user) or []]
	except Exception:
		_log_error("roles for {0}".format(user))
		return []


def _full_name(user) -> str:
	"""The user's full name, falling back to the user id."""
	try:
		return str(get_fullname(user) or user)
	except Exception:
		_log_error("full name for {0}".format(user))
		return str(user)


def _user_permissions(user) -> dict:
	"""The raw User Permission map, which ``chat/menu.py`` also reads.

	It is handed on untransformed rather than reduced to this module's interests:
	the menu and any later gate must see exactly what Frappe granted the user.
	"""
	try:
		permissions = frappe.defaults.get_user_permissions(user)
		return permissions if isinstance(permissions, dict) else {}
	except Exception:
		_log_error("user permissions for {0}".format(user))
		return {}


def _department(user_permissions):
	"""The first Department User Permission, or ``None`` when there is none.

	``get_user_permissions`` has answered both ``{"Department": ["Sales"]}`` and
	``{"Department": [{"doc": "Sales", ...}]}`` across releases, so a plain string
	and a document reference are both accepted (docs/11 section 4.2).
	"""
	for entry in (user_permissions or {}).get(DEPARTMENT) or []:
		if isinstance(entry, dict):
			value = entry.get("doc") or entry.get("value")
		else:
			value = entry
		if value:
			return str(value)
	return None


def _timezone(user):
	"""The user's configured time zone, or ``None``."""
	try:
		return frappe.get_cached_doc("User", user).get("time_zone") or None
	except Exception:
		_log_error("time zone for {0}".format(user))
		return None


def _log_error(subject):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(
			title="Solrise chat context: {0}".format(subject),
			message=frappe.get_traceback(),
		)
	except Exception:
		# Logging must not become the failure it is reporting.
		pass
