# Copyright (c) 2026, Solrise and contributors

"""Row-level permission rules (docs/11-rbac.md sections 4 and 5.3).

The functions here are registered as hooks in `hooks.py`:

* `permission_query_conditions` **restricts** list / report / link queries by
  adding a WHERE fragment. Returning `None` means "no opinion" - the DocType's
  role permissions then decide what is visible.
* `has_permission` **grants or denies** a single document. Returning `None` means
  "no opinion", so Frappe's normal rules still apply; it never grants anything by
  itself.

None of this is a security boundary on its own: the role/DocPerm matrix is layer 1
(docs/11 section 0), and raw SQL or `frappe.db.get_value` bypasses these hooks
entirely (docs/11 section 4.2).

Every function returns quietly - `None` - instead of raising, because a hook that
raises breaks the caller's page rather than just narrowing it.
"""

import frappe

ISSUE = "Issue"
EMPLOYEE = "Employee"
DEPARTMENT_HEAD = "Department Head"
SUPPORT_AGENT = "Support Agent"

# Roles that already see every Issue row; the row rule must never narrow these,
# and `issue_has_permission()` answers `True` for them rather than denying.
ISSUE_UNRESTRICTED_ROLES = (
	"System Manager",
	"Support Manager",
	"Solrise Admin",
	"Director",
	"Solrise Super Admin",
	"Department Head",
)

# Roles that already see every Leave Application / Expense Claim row.
HR_UNRESTRICTED_ROLES = (
	"System Manager",
	"HR Manager",
	"HR User",
	"Solrise Admin",
	"Solrise Super Admin",
)

# Frappe passes `ptype` to a `has_permission` hook; used only to tell a positional
# user name apart from a positional permission type.
PERMISSION_TYPES = (
	"read", "write", "create", "delete", "submit", "cancel", "amend",
	"report", "export", "import", "share", "print", "email",
)


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def _roles(user):
	"""The user's roles, or an empty set when they cannot be read."""
	try:
		return set(frappe.get_roles(user))
	except Exception:
		_log_error("Solrise permissions: get_roles")
		return set()


def _is_admin(user):
	"""True for the built-in Administrator account."""
	return (user or "").lower() == "administrator"


def _doc_value(doc, fieldname):
	"""Read a field from a Document, a dict or anything duck-typed like them."""
	try:
		if hasattr(doc, "get"):
			return doc.get(fieldname)
		return getattr(doc, fieldname, None)
	except Exception:
		return None


def _assignees(doc):
	"""The user names in `_assign`, lowercased.

	Frappe stores `_assign` as a JSON list, older versions as a comma string, so
	both shapes are handled here.
	"""
	raw = _doc_value(doc, "_assign")
	values = []
	if isinstance(raw, (list, tuple, set)):
		values = [str(value) for value in raw]
	elif isinstance(raw, str) and raw.strip():
		text = raw.strip()
		try:
			import json

			parsed = json.loads(text)
			if isinstance(parsed, list):
				values = [str(value) for value in parsed]
		except Exception:
			values = (
				text.replace("[", " ")
				.replace("]", " ")
				.replace('"', " ")
				.replace("'", " ")
				.split(",")
			)
	return {str(value).strip().lower() for value in values if str(value).strip()}


def _owner_or_assignee_sql(user):
	"""`own or assigned to me`, safely quoted for Frappe's query template."""
	escaped_user = frappe.db.escape(user)
	# frappe.db.escape() also doubles "%" (the fragment is spliced into a
	# %-formatted query template), so the LIKE wildcards survive as wildcards.
	escaped_pattern = frappe.db.escape("%{0}%".format(user))
	return (
		"(`tab{0}`.`owner` = {1} or `tab{0}`.`_assign` like {2})".format(
			ISSUE, escaped_user, escaped_pattern
		)
	)


def _user_permission_docs(user, allow):
	"""The values of a `User Permission` rule, e.g. the user's departments.

	`frappe.defaults.get_user_permissions()` has returned both
	`{"Department": ["Engineering"]}` and
	`{"Department": [{"doc": "Engineering", ...}]}` across releases, so both are
	accepted. An empty list means "no rule", which callers treat as "no opinion".
	"""
	values = []
	try:
		permissions = frappe.defaults.get_user_permissions(user) or {}
	except Exception:
		_log_error("Solrise permissions: get_user_permissions")
		return values
	for entry in permissions.get(allow) or []:
		if isinstance(entry, dict):
			value = entry.get("doc") or entry.get("value")
		else:
			value = entry
		if value:
			values.append(str(value))
	return values


def issue_query_conditions(user=None, doctype=None):
	"""Hook: a Support Agent lists only the tickets they own or are assigned.

	`doctype` is accepted because Frappe calls the hook as
	`frappe.call(method, user, doctype=doctype)`.

	Administrator, System Manager, Support Manager, Solrise Admin and Department
	Head see everything (docs/11 section 2.3), and so does every other role - the
	DocType role matrix has already decided what they may read at all.
	"""
	try:
		user = user or frappe.session.user
		roles = _roles(user)
		if _is_admin(user) or roles.intersection(ISSUE_UNRESTRICTED_ROLES):
			return None
		if SUPPORT_AGENT in roles:
			return _owner_or_assignee_sql(user)
		return None
	except Exception:
		# Layer 1 still applies, so an unreadable role set narrows nothing.
		_log_error("Solrise permissions: issue_query_conditions")
		return None


def issue_has_permission(doc, ptype=None, user=None, debug=False, permission_type=None):
	"""Hook: `True` for the owner, an assignee or a manager role, `None` otherwise.

	Returning `None` means "no opinion": Frappe then applies its normal rules for
	the document, which is the right answer for managers, for Administrator and for
	a user whose roles this app has no explicit row rule for.

	A Support Agent who is neither the owner nor an assignee is **denied** (not
	`None`) - see the comment on that branch. `permission_type` is accepted as an
	alias for `ptype`, and the two are told apart when they arrive positionally.
	"""
	try:
		if user is None and ptype and ptype not in PERMISSION_TYPES:
			user, ptype = ptype, None
		permission_type = permission_type or ptype or "read"
		user = user or frappe.session.user

		if permission_type == "create":
			# The owner does not exist yet, and a create is decided by layer 1;
			# denying here would block `doc.insert()` for everyone.
			return True

		roles = _roles(user)
		if _is_admin(user) or roles.intersection(ISSUE_UNRESTRICTED_ROLES):
			return True
		if doc is None:
			return None

		owner = (_doc_value(doc, "owner") or "").lower()
		if owner and owner == user.lower():
			return True
		if user.lower() in _assignees(doc):
			return True

		if SUPPORT_AGENT in roles:
			# Deliberate deny (docs/11 sections 3.1 and 10,
			# `test_agent_cannot_read_unassigned_issue`). The Support Agent role
			# holds a broad read at the DocPerm level so that *list* views can be
			# narrowed by `issue_query_conditions`; without this branch a
			# hand-typed `/app/issue/ISS-00001` URL - which does not go through a
			# list query - would still open a ticket the agent neither owns nor is
			# assigned. Manager roles returned `True` above, so they never reach
			# here.
			return False

		# No opinion for everyone else: the DocType role rules decide.
		return None
	except Exception:
		_log_error("Solrise permissions: issue_has_permission")
		return None


def _department_query_conditions(doctype, user=None):
	"""A Department Head sees only their department's rows, else `None`.

	`Leave Application` and `Expense Claim` have no `department` field to hook a
	User Permission onto, so the rule is expressed as the subquery docs/11 section
	2.7 describes:
	`employee IN (SELECT name FROM tabEmployee WHERE department IN (...))`.
	Without a Department User Permission for the user there is no opinion to add.
	"""
	try:
		user = user or frappe.session.user
		roles = _roles(user)
		if _is_admin(user) or roles.intersection(HR_UNRESTRICTED_ROLES):
			return None
		if DEPARTMENT_HEAD not in roles:
			return None
		if not frappe.db.exists("DocType", doctype) or not frappe.db.exists("DocType", EMPLOYEE):
			return None
		if not frappe.get_meta(doctype).has_field("employee"):
			return None

		departments = _user_permission_docs(user, "Department")
		if not departments:
			return None
		escaped = ", ".join(frappe.db.escape(value) for value in departments)
		return (
			"`tab{0}`.`employee` in (select `name` from `tab{1}` "
			"where `department` in ({2}))"
		).format(doctype, EMPLOYEE, escaped)
	except Exception:
		_log_error("Solrise permissions: {0} query conditions".format(doctype))
		return None


def leave_application_query_conditions(user=None, doctype=None):
	"""Hook: a Department Head lists only their department's Leave Applications."""
	return _department_query_conditions("Leave Application", user)


def expense_claim_query_conditions(user=None, doctype=None):
	"""Hook: a Department Head lists only their department's Expense Claims."""
	return _department_query_conditions("Expense Claim", user)


# docs/11 section 5.1: `hooks.py` may import these maps instead of inlining them.
# They cover the DocTypes this module implements rules for; add an entry here and
# a function above in the same commit.
permission_query_conditions = {
	"Issue": "solrise_erp.permissions.issue_query_conditions",
	"Leave Application": "solrise_erp.permissions.leave_application_query_conditions",
	"Expense Claim": "solrise_erp.permissions.expense_claim_query_conditions",
}

has_permission = {
	"Issue": "solrise_erp.permissions.issue_has_permission",
}
