# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Assistant tools (docs/06 section 4.4) and the two-key gate.

A tool runs only when all of these hold:

* it is named in ``TOOL_REGISTRY`` below, and
* its method path appears in ``assistant_allowed_methods`` (``hooks.py``), and
* the guardrail switch it declares is on in ``Solrise Settings``.

Handlers use ``frappe.get_list`` / ``doc.insert`` and never
``frappe.get_all``/``ignore_permissions``, so the requesting user's own permission
matrix decides what comes back. Nothing here runs as Administrator.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint

# entity -> (DocType, the field a human would search by)
ENTITIES = {
	"customer": ("Customer", "customer_name"),
	"item": ("Item", "item_name"),
	"employee": ("Employee", "employee_name"),
	"lead": ("Lead", "lead_name"),
	"opportunity": ("Opportunity", "customer_name"),
	"ticket": ("Issue", "subject"),
	"issue": ("Issue", "subject"),
	"quotation": ("Quotation", "customer_name"),
	"contact": ("Contact", "full_name"),
	"supplier": ("Supplier", "supplier_name"),
}

DEFAULT_LIMIT = 5
MAX_LIMIT = 20


def _limit(value):
	return max(1, min(cint(value) or DEFAULT_LIMIT, MAX_LIMIT))


def _lookup(entity=None, query=None, limit=None):
	"""Find records by partial name, as the requesting user."""
	entity = (entity or "").strip().lower()
	if entity not in ENTITIES:
		return {"error": _("I can look up: {0}.").format(", ".join(sorted(ENTITIES)))}
	doctype, search_field = ENTITIES[entity]
	if not query:
		return {"error": _("What should I search for?")}
	rows = frappe.get_list(
		doctype,
		or_filters=[[search_field, "like", f"%{query}%"], ["name", "like", f"%{query}%"]],
		fields=[name_field(doctype)],
		limit_page_length=_limit(limit),
	)
	return {"doctype": doctype, "query": query, "results": rows, "count": len(rows)}


def name_field(doctype):
	"""A display field that exists on this DocType (versions differ)."""
	meta = frappe.get_meta(doctype)
	for candidate in ("name", "title", "subject", "customer_name", "employee_name", "supplier_name"):
		if candidate == "name" or meta.has_field(candidate):
			return candidate
	return "name"


def _ticket_status(ticket=None):
	"""Status, priority and SLA due dates for one Issue the user may read."""
	if not ticket:
		return {"error": _("Which ticket?")}
	rows = frappe.get_list(
		"Issue",
		filters={"name": ticket},
		fields=["name", "subject", "status", "priority", "agreement_status", "resolution_by", "response_by", "_assign"],
		limit_page_length=1,
	)
	if not rows:
		return {"error": _("I could not find {0}, or you do not have access to it.").format(ticket)}
	return rows[0]


def _create_ticket(subject=None, description=None, priority=None, customer=None):
	"""Raise an Issue as the calling user (never as Administrator)."""
	if not subject:
		return {"error": _("What should the ticket say?")}
	payload = {"doctype": "Issue", "subject": subject, "description": description or ""}
	if priority and frappe.db.exists("Issue Priority", priority):
		payload["priority"] = priority
	if customer and frappe.db.exists("Customer", customer):
		payload["customer"] = customer
	doc = frappe.get_doc(payload)
	doc.flags.from_assistant = True
	doc.insert()
	return {"doctype": "Issue", "name": doc.name, "status": doc.status, "url": f"/app/issue/{doc.name}"}


def _search_faq(query=None, limit=None):
	"""Answer from the curated Solrise FAQ table (never invented)."""
	if not frappe.db.exists("DocType", "Solrise FAQ"):
		return {"error": _("There is no Solrise FAQ table on this site.")}
	if not query:
		return {"error": _("What is your question?")}
	fields = ["question", "answer"]
	# `keywords` carries the synonyms an author curates ("reset password" for a
	# question about the Forgot-password link), so a search that hits one word
	# finds the answer even when neither the question nor the answer contains it.
	or_filters = [["question", "like", f"%{query}%"], ["answer", "like", f"%{query}%"]]
	if frappe.get_meta("Solrise FAQ").has_field("keywords"):
		or_filters.append(["keywords", "like", f"%{query}%"])
	rows = frappe.get_list(
		"Solrise FAQ",
		filters={"enabled": 1},
		or_filters=or_filters,
		fields=fields,
		limit_page_length=_limit(limit),
	)
	if not rows:
		return {"error": _("Nothing in the knowledge base matches that.")}
	return {"matches": rows}


def _navigation_hint(target=None):
	"""Map a word the user said to a Desk route."""
	routes = {
		"leave": ("Leave Application", "/app/leave-application"),
		"leave application": ("Leave Application", "/app/leave-application"),
		"customers": ("Customer", "/app/customer"),
		"customer": ("Customer", "/app/customer"),
		"tickets": ("Issue", "/app/issue"),
		"ticket": ("Issue", "/app/issue"),
		"issues": ("Issue", "/app/issue"),
		"employees": ("Employee", "/app/employee"),
		"employee": ("Employee", "/app/employee"),
		"quotations": ("Quotation", "/app/quotation"),
		"quotation": ("Quotation", "/app/quotation"),
		"leads": ("Lead", "/app/lead"),
		"opportunities": ("Opportunity", "/app/opportunity"),
		"reports": (None, "/app/query-report"),
		"dashboard": (None, "/app/dashboard-view/Solrise%20Operations"),
	}
	key = (target or "").strip().lower()
	match = routes.get(key)
	if not match:
		return {"error": _("I do not know that page. Try: tickets, customers, leave, employees, quotations.")}
	doctype, route = match
	return {"doctype": doctype, "route": route}


def _refused(name, reason):
	return {"ok": False, "tool": name, "error": reason, "refused": True}


TOOL_REGISTRY = {
	"lookup": {
		"method": "solrise_erp.api.v1.lookup",
		"guardrail": "allow_record_lookup",
		"description": "Find customers, items, employees, leads, opportunities, tickets, quotations, contacts or suppliers by partial name.",
		"parameters": {
			"type": "object",
			"properties": {
				"entity": {"type": "string", "description": "one of: " + ", ".join(sorted(ENTITIES))},
				"query": {"type": "string", "description": "part of the name to search for"},
				"limit": {"type": "integer", "description": "how many results, default 5"},
			},
			"required": ["entity", "query"],
		},
		"handler": _lookup,
	},
	"ticket_status": {
		"method": "solrise_erp.api.v1.ticket_status",
		"guardrail": "allow_record_lookup",
		"description": "Status, priority and SLA due dates for one Issue.",
		"parameters": {
			"type": "object",
			"properties": {"ticket": {"type": "string", "description": "the Issue name, e.g. ISS-00001"}},
			"required": ["ticket"],
		},
		"handler": _ticket_status,
	},
	"create_ticket": {
		"method": "solrise_erp.api.v1.create_ticket",
		"guardrail": "allow_ticket_creation",
		"description": "Raise an Issue as the calling user.",
		"parameters": {
			"type": "object",
			"properties": {
				"subject": {"type": "string"},
				"description": {"type": "string"},
				"priority": {"type": "string", "description": "Low, Medium, High or Urgent"},
				"customer": {"type": "string"},
			},
			"required": ["subject"],
		},
		"handler": _create_ticket,
	},
	"search_faq": {
		"method": "solrise_erp.api.v1.search_faq",
		"guardrail": "allow_record_lookup",
		"description": "Answer from the curated Solrise FAQ table.",
		"parameters": {
			"type": "object",
			"properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
			"required": ["query"],
		},
		"handler": _search_faq,
	},
	"navigation_hint": {
		"method": "solrise_erp.api.v1.navigation_hint",
		"guardrail": "allow_navigation",
		"description": "Tell the user which Desk page to open.",
		"parameters": {
			"type": "object",
			"properties": {"target": {"type": "string"}},
			"required": ["target"],
		},
		"handler": _navigation_hint,
	},
}


def _allowed_methods():
	try:
		return set(frappe.get_hooks("assistant_allowed_methods") or [])
	except Exception:
		return set()


def _guardrail_on(tool):
	guardrail = tool.get("guardrail")
	if not guardrail:
		return True
	try:
		return bool(cint(frappe.get_cached_doc("Solrise Settings").get(guardrail)))
	except Exception:
		return False


def available(user=None):
	"""Tools whose two keys are both present. Used to build the model's schema list."""
	allowed = _allowed_methods()
	return {
		name: tool
		for name, tool in TOOL_REGISTRY.items()
		if tool["method"] in allowed and _guardrail_on(tool)
	}


def schemas(user=None):
	return [
		{
			"type": "function",
			"function": {
				"name": name,
				"description": tool["description"],
				"parameters": tool["parameters"],
			},
		}
		for name, tool in available(user).items()
	]


def dispatch(name, args=None, user=None):
	"""Run one tool call through both gates, then as the requesting user."""
	tool = TOOL_REGISTRY.get(name)
	if not tool:
		return _refused(name, _("Unknown tool."))
	if tool["method"] not in _allowed_methods():
		return _refused(name, _("{0} is not in assistant_allowed_methods.").format(tool["method"]))
	if not _guardrail_on(tool):
		return _refused(name, _("That action is switched off in Solrise Settings."))
	try:
		return {"ok": True, "tool": name, "result": tool["handler"](**(args or {}))}
	except frappe.PermissionError:
		return _refused(name, _("Not permitted for {0}.").format(user or frappe.session.user))
	except Exception as exc:
		frappe.log_error(title=f"Solrise assistant: tool {name} failed")
		return {"ok": False, "tool": name, "error": str(exc)[:300]}
