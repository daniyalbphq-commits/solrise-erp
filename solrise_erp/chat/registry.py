# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The closed vocabulary the chat may act on (docs/12 section 5.0).

Deliberately plain dicts and no Frappe import: ``nlp``, ``intent`` and
``tests/test_chat_nlp.py`` all work without a site.

Two rules from the design review live here:

* ``ACTION_REGISTRY`` is the **first** gate. An action that is not in it is
  rejected outright - the model can propose anything, but only these are real.
* ``ACTION_PTYPE`` maps an action to the Frappe permission type it needs. Nothing
  else may widen permissions, and ``approve`` never maps to a write.
"""

ACTION_REGISTRY = {
	"Issue": ["read", "create", "update", "delete", "approve"],
	"Leave Application": ["read", "create", "update", "approve"],
	"Expense Claim": ["read", "create", "update", "approve"],
	"Quotation": ["read", "create", "update", "approve"],
	"Purchase Order": ["read", "create", "update", "approve"],
	"Payment Entry": ["read", "create", "update", "approve"],
	"Customer": ["read", "create", "update"],
	"Contact": ["read", "create", "update"],
	"Lead": ["read", "create", "update"],
	"Opportunity": ["read", "create", "update"],
	"Employee": ["read"],
	"Solrise FAQ": ["read"],
	# The Reports quick action browses the report list, which is metadata the user
	# already sees in the Desk; there is no write action to pair with it.
	"Report": ["read"],
}

ACTION_PTYPE = {
	"read": "read",
	"create": "create",
	"update": "write",
	"submit": "submit",
	"cancel": "cancel",
	"approve": "submit",
	"delete": "delete",
}

# The quick-action menu. `hint` is what the user reads; the key is what the widget
# sends back. `doctypes` decide whether the action is offered at all - the menu is
# built from permissions, not from this list.
MODULES = {
	"tickets": {
		"label": "Tickets",
		"hint": "find or raise a support ticket",
		"doctypes": ["Issue"],
		"create": "Issue",
	},
	"crm": {
		"label": "CRM",
		"hint": "customers, leads, opportunities, quotations",
		"doctypes": ["Customer", "Lead", "Opportunity", "Quotation"],
		"create": "Lead",
	},
	"hr": {
		"label": "HR",
		"hint": "leave applications and employee records",
		"doctypes": ["Leave Application", "Employee"],
		"create": "Leave Application",
	},
	"approvals": {
		"label": "Approvals",
		"hint": "what is waiting for your approval",
		"doctypes": ["Leave Application", "Expense Claim", "Purchase Order", "Payment Entry"],
		"create": None,
	},
	"knowledge": {
		"label": "Knowledge Base",
		"hint": "answers from the curated FAQ",
		"doctypes": ["Solrise FAQ"],
		"create": None,
	},
	"tasks": {
		"label": "My Tasks",
		"hint": "tickets you own or are assigned",
		"doctypes": ["Issue"],
		"create": None,
	},
	"reports": {
		"label": "Reports",
		"hint": "the Solrise report list",
		"doctypes": ["Report"],
		"create": None,
	},
}

# Words a user types -> the DocType they mean.
SYNONYMS = {
	"ticket": "Issue",
	"tickets": "Issue",
	"issue": "Issue",
	"issues": "Issue",
	"complaint": "Issue",
	"leave": "Leave Application",
	"leave application": "Leave Application",
	"leave request": "Leave Application",
	"expense": "Expense Claim",
	"expense claim": "Expense Claim",
	"claim": "Expense Claim",
	"purchase order": "Purchase Order",
	"payment": "Payment Entry",
	"payment entry": "Payment Entry",
	"quotation": "Quotation",
	"quote": "Quotation",
	"customer": "Customer",
	"client": "Customer",
	"contact": "Contact",
	"lead": "Lead",
	"opportunity": "Opportunity",
	"deal": "Opportunity",
	"employee": "Employee",
	"staff": "Employee",
	"faq": "Solrise FAQ",
	"knowledge base": "Solrise FAQ",
}

# Naming-series prefixes this deployment actually uses. The chat resolves
# "ISS-00042" to an Issue without asking the model anything.
PREFIXES = {
	"ISS-": "Issue",
	"CRM-LEAD-": "Lead",
	"OPTY-": "Opportunity",
	"SAL-QTN-": "Quotation",
	"HR-LAP-": "Leave Application",
	"HR-EMP-": "Employee",
	"HR-EXP-": "Expense Claim",
	"PUR-ORD-": "Purchase Order",
	"ACC-PAY-": "Payment Entry",
	"CUST-": "Customer",
	"EMP-": "Employee",
}

# Doctypes the My Tasks quick action searches (Solrise Settings can override).
MY_TASKS_DOCTYPES = ["Issue"]
