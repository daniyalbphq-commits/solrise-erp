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
			# Somebody has curated this list. Add anyone new who holds a support role
			# and take nobody out: a rule that quietly dropped a maintainer because a
			# second one joined would be worse than a slightly stale one.
			final = known | wanted
		else:
			# Still the shipped default - `Administrator` alone, or empty. Take it
			# over, because routing tickets to the built-in account is not routing.
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
