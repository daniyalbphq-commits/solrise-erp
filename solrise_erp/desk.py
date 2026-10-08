# Copyright (c) 2026, Solrise and contributors

"""The Desk surface a maintenance manager lands on (docs/19).

Two things have to be true before "the manager sees the reports" means anything.

**The reports must be routed to somebody.** The shipped Assignment Rule names
`Administrator` alone, so a maintainer holding only the Support Agent role sees an
empty list: `permissions.issue_query_conditions` narrows that role to tickets they
own or are assigned, and nothing was ever assigned to them. Support *Manager* is in
`ISSUE_UNRESTRICTED_ROLES` and sees everything, but relying on that is relying on
every maintainer being given the manager role.

**There must be somewhere obvious to look.** ERPNext ships a `Support` workspace, so
nobody is locked out, but it is generic and upstream re-syncs it on every migrate.
This module owns a workspace of our own instead, which upstream will not touch.

Both functions are idempotent, and neither overwrites a deliberate operator edit.
"""

import json

import frappe

WORKSPACE = "Solrise Maintenance"
MODULE = "Solrise ERP"
ASSIGNMENT_RULE = "Solrise Support Routing"

#: The Custom Field the portal stamps with the desk a report came from
#: (`api.portal.DEPARTMENT_FIELD`). Only a customer portal report carries it.
DEPARTMENT_FIELD = "solrise_department"

#: The rule's condition once that field exists. The shipped fixture is
#: `status == 'Open'`; the guard keeps the rotation for reports raised *in the
#: Desk* while leaving a customer's report to the desk it was sent to, because
#: the portal assigns and emails those itself (docs/17 section 15).
ASSIGN_CONDITION = "status == 'Open' and not solrise_department"

#: The Desk-facing notification that tells the support role about a new Issue.
NOTIFICATION = "Solrise New Ticket"

#: Its condition once the department field exists: a portal report is emailed by
#: the portal, to the desk it was sent to, so this one must skip it.
NOTIFY_CONDITION = "not doc.solrise_department"


#: Role holders who should be in the assignment rotation.
SUPPORT_ROLES = ("Support Manager", "Support Agent")

#: Who may see the workspace. Left as roles rather than public because a
#: maintainer's landing page is not the accountant's business.
WORKSPACE_ROLES = ("Support Manager", "Support Agent", "Solrise Admin", "Solrise Super Admin")

# `type` and `link_to` follow ERPNext's `Workspace Shortcut`; the URL shortcut is
# what makes the store reports one click away rather than a filter to be rebuilt.
SHORTCUTS = (
	{
		"type": "URL",
		"label": "Store reports",
		"url": "/app/issue?via_customer_portal=1",
		"color": "Blue",
	},
	{"type": "DocType", "label": "Issue", "link_to": "Issue", "doc_view": "List", "color": "Orange"},
	{
		"type": "DocType",
		"label": "Service Level Agreement",
		"link_to": "Service Level Agreement",
		"doc_view": "List",
	},
)

LINKS = (
	{"type": "Card Break", "label": "Reports"},
	{"type": "Link", "label": "Issue", "link_to": "Issue", "link_type": "DocType"},
	{"type": "Link", "label": "Customer", "link_to": "Customer", "link_type": "DocType"},
	{"type": "Card Break", "label": "Setup"},
	{"type": "Link", "label": "Issue Priority", "link_to": "Issue Priority", "link_type": "DocType"},
	{"type": "Link", "label": "Assignment Rule", "link_to": "Assignment Rule", "link_type": "DocType"},
	{
		"type": "Link",
		"label": "Service Level Agreement",
		"link_to": "Service Level Agreement",
		"link_type": "DocType",
	},
)


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def support_users():
	"""Enabled users who hold a role that can work a ticket, `Administrator` aside."""
	users = set()
	for role in SUPPORT_ROLES:
		try:
			rows = frappe.get_all(
				"Has Role",
				filters={"role": role, "parenttype": "User"},
				fields=["parent"],
			)
		except Exception:
			_log_error("Solrise desk: support users for %s" % role)
			continue
		for row in rows:
			users.add(row.parent)

	enabled = []
	for user in sorted(users):
		if user in ("Administrator", "Guest"):
			continue
		try:
			if frappe.db.get_value("User", user, "enabled"):
				enabled.append(user)
		except Exception:
			continue
	return enabled


def ensure_support_routing():
	"""Point the support Assignment Rule at everyone who can work a ticket.

	Returns the users it set, or `None` when it left the rule alone. The rule is only
	rewritten while it still holds the shipped default, so a rotation an operator has
	curated by hand survives every `bench migrate`.
	"""
	try:
		if not frappe.db.exists("Assignment Rule", ASSIGNMENT_RULE):
			return None

		rule = frappe.get_doc("Assignment Rule", ASSIGNMENT_RULE)
		known = {row.user for row in (rule.users or [])}
		wanted = set(support_users())
		if not wanted:
			# Nobody holds the role yet. Leaving `Administrator` in place is better
			# than emptying the rule and routing nothing anywhere.
			return None

		if known - {"Administrator"}:
			# Somebody has curated this list - typically the operator, via
			# `scripts/configure_support.py`. Leave it exactly as it is. Adding every
			# holder of a support role back would undo that, which is how the
			# administrators ended up back in the rotation once before; and quietly
			# dropping a maintainer would be worse than a slightly stale list.
			return None

		# Still the shipped default - `Administrator` alone, or empty. Take it over,
		# because routing tickets to the built-in account is not routing.
		final = set(wanted)

		if final == known:
			return None

		rule.set("users", [])
		for user in sorted(final):
			rule.append("users", {"user": user, "weight": 1})
		rule.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info("desk: support routing -> %s", sorted(final))
		return sorted(final)
	except Exception:
		_log_error("Solrise desk: ensure_support_routing")
		return None


def ensure_issue_department_field():
	"""Create the `Issue.solrise_department` Custom Field; return its name.

	Created in code rather than shipped as a fixture so it exists on every site the
	app runs on, and so the Select options stay in step with the catalogue.
	"""
	try:
		if not frappe.db.exists("DocType", "Issue"):
			return None
		name = "Issue-{0}".format(DEPARTMENT_FIELD)
		if frappe.db.exists("Custom Field", name):
			return name

		from solrise_erp.portal import catalog

		doc = frappe.new_doc("Custom Field")
		doc.dt = "Issue"
		doc.fieldname = DEPARTMENT_FIELD
		doc.label = "Department"
		doc.fieldtype = "Select"
		doc.options = "\n".join(entry["label"] for entry in catalog.DEPARTMENTS)
		doc.insert_after = "via_customer_portal"
		doc.read_only = 1
		doc.description = "Which desk a customer portal report was sent to."
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info("desk: custom field %s created", name)
		return name
	except Exception:
		_log_error("Solrise desk: ensure_issue_department_field")
		return None


def ensure_department_routing():
	"""Keep customer portal reports out of the shared support rotation.

	Returns the condition it set, or `None` when it was already right. The portal
	assigns a report to its own desk and emails that desk, so the Assignment Rule
	must not also hand an HR report to the maintenance rotation - it is excluded by
	`solrise_department`, which only a portal report carries.
	"""
	try:
		if not frappe.db.exists("Assignment Rule", ASSIGNMENT_RULE):
			return None
		if not frappe.get_meta("Issue").has_field(DEPARTMENT_FIELD):
			return None
		rule = frappe.get_doc("Assignment Rule", ASSIGNMENT_RULE)
		if (rule.assign_condition or "").strip() == ASSIGN_CONDITION:
			return None
		rule.assign_condition = ASSIGN_CONDITION
		rule.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info("desk: assignment condition -> %s", ASSIGN_CONDITION)
		return ASSIGN_CONDITION
	except Exception:
		_log_error("Solrise desk: ensure_department_routing")
		return None


def ensure_notification_scope():
	"""Keep the new-ticket notification off customer portal reports.

	`Solrise New Ticket` addresses the `Support Manager` role. That is right for a
	report raised in the Desk, but a customer portal report is emailed by the portal
	to the desk it was sent to - so without this guard an HR report would land in
	the maintenance inbox as well. Returns the condition it set, or `None`.
	"""
	try:
		if not frappe.db.exists("Notification", NOTIFICATION):
			return None
		if not frappe.get_meta("Issue").has_field(DEPARTMENT_FIELD):
			return None
		doc = frappe.get_doc("Notification", NOTIFICATION)
		if (doc.condition or "").strip() == NOTIFY_CONDITION:
			return None
		doc.condition = NOTIFY_CONDITION
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info("desk: %s condition -> %s", NOTIFICATION, NOTIFY_CONDITION)
		return NOTIFY_CONDITION
	except Exception:
		_log_error("Solrise desk: ensure_notification_scope")
		return None


def _content():
	"""The workspace layout, in the block format the Desk renders.

	Only the block types read out of a stock workspace are emitted: `header`,
	`shortcut`, `spacer` and `card`. A malformed block would break the page for the
	people who need it most, so nothing here is invented.
	"""
	blocks = []

	def add(kind, data):
		blocks.append({"id": frappe.generate_hash(length=10), "type": kind, "data": data})

	add("header", {"text": '<span class="h4"><b>Reports</b></span>', "col": 12})
	for shortcut in SHORTCUTS:
		add("shortcut", {"shortcut_name": shortcut["label"], "col": 4})
	add("spacer", {"col": 12})
	add("header", {"text": '<span class="h4"><b>Documents</b></span>', "col": 12})
	for link in LINKS:
		if link["type"] == "Card Break":
			add("card", {"card_name": link["label"], "col": 4})
	return json.dumps(blocks)


def ensure_maintenance_workspace():
	"""Create or refresh the `Solrise Maintenance` workspace; return its name.

	Our own workspace rather than an edit of ERPNext's `Support`: upstream re-syncs
	its own on every migrate and would undo the change, the same reason
	`branding.ensure_workspace_branding()` exists.
	"""
	try:
		if not frappe.db.exists("DocType", "Workspace"):
			return None
		if not frappe.db.exists("Module Def", MODULE):
			return None

		if frappe.db.exists("Workspace", WORKSPACE):
			doc = frappe.get_doc("Workspace", WORKSPACE)
		else:
			doc = frappe.new_doc("Workspace")
			doc.label = WORKSPACE

		doc.title = WORKSPACE
		doc.public = 1
		doc.is_hidden = 0
		doc.for_user = ""
		doc.module = MODULE
		doc.icon = "support"
		doc.indicator_color = "blue"
		# Ahead of the stock workspaces, so it is not buried at the bottom.
		doc.sequence_id = 1.0

		doc.set("shortcuts", [])
		for shortcut in SHORTCUTS:
			doc.append("shortcuts", dict(shortcut))

		doc.set("links", [])
		for link in LINKS:
			doc.append("links", dict(link))

		doc.set("roles", [])
		for role in WORKSPACE_ROLES:
			doc.append("roles", {"role": role})

		doc.content = _content()

		if doc.get("__islocal"):
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info("desk: workspace %s ready", WORKSPACE)
		return doc.name
	except Exception:
		_log_error("Solrise desk: ensure_maintenance_workspace")
		return None
