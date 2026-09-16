# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Whitelisted assistant endpoints (docs/06 section 4.5).

All of them require an authenticated session - there is no ``allow_guest``
anywhere in this file. Each one delegates to the assistant engine or the tool
registry, so the guardrails and the permission matrix apply exactly once, in one
place.
"""

from __future__ import annotations

import frappe

from solrise_erp.assistant import engine, tools


@frappe.whitelist()
def ask(message, session_id=None):
	"""The free-form assistant turn the widget and the API use."""
	return engine.ask(message, session_id=session_id, channel="Desk")


@frappe.whitelist()
def health():
	"""Provider/feature status. Safe: it never returns the API key itself."""
	return engine.health()


@frappe.whitelist()
def lookup(entity=None, query=None, limit=None):
	return tools.dispatch("lookup", {"entity": entity, "query": query, "limit": limit})


@frappe.whitelist()
def ticket_status(ticket=None):
	return tools.dispatch("ticket_status", {"ticket": ticket})


@frappe.whitelist()
def create_ticket(subject=None, description=None, priority=None, customer=None):
	return tools.dispatch(
		"create_ticket",
		{"subject": subject, "description": description, "priority": priority, "customer": customer},
	)


@frappe.whitelist()
def search_faq(query=None, limit=None):
	return tools.dispatch("search_faq", {"query": query, "limit": limit})


@frappe.whitelist()
def navigation_hint(target=None):
	return tools.dispatch("navigation_hint", {"target": target})
