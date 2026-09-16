# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Deterministic intent parsing (docs/12 section 5.2).

**No Frappe import anywhere in this file.** The vocabulary arrives as arguments
(the defaults come from ``chat.registry``), which is why
``tests/test_chat_nlp.py`` can exercise it with plain python - no site, no bench.

What it does: turn one line of user text into a closed, validated intent
``{doctype, action, record, urgency, transition, fields, confidence}``. It never
executes anything and never decides permissions; ``api/chat.py`` does the gating.
Low confidence is a first-class answer - the caller then asks a clarifying
question (or, with ``chat_enable_llm_fallback`` on, asks the model for a proposal
and validates it with :func:`validate_proposal`).
"""

from __future__ import annotations

import re

from solrise_erp.chat import registry

READ_VERBS = ("show", "get", "find", "view", "display", "look up", "lookup", "list", "search", "status")
CREATE_PHRASES = ("raise a ticket", "create a ticket", "open a ticket", "new ticket", "log a ticket",
                  "open a ticket:", "raise an issue", "create", "raise", "new", "add", "log", "file")
UPDATE_VERBS = ("update", "change", "set", "edit", "modify", "rename")
APPROVE_VERBS = ("approve", "accept")
REJECT_VERBS = ("reject", "deny", "decline")
DELETE_VERBS = ("delete", "remove", "discard")

URGENCY_WORDS = {
	"urgent": "Urgent",
	"asap": "Urgent",
	"immediately": "Urgent",
	"high": "High",
	"medium": "Medium",
	"normal": "Medium",
	"low": "Low",
}

TRANSITIONS = {"reject": "Reject", "deny": "Reject", "decline": "Reject",
               "approve": "Approve", "accept": "Approve", "escalate": "Escalate"}

PRIORITY_HINT = re.compile(r"priority[\s:=]+(low|medium|high|urgent)|(low|medium|high|urgent)\s+priority")


def normalize(text):
	"""Lowercase, collapse whitespace, keep ``-`` (naming series need it)."""
	text = (text or "").strip().lower()
	text = re.sub(r"[^\w\s\-\.:]", " ", text)
	return re.sub(r"\s+", " ", text).strip()


def action_from_text(text):
	"""Verb -> action. Create phrases win over the read verbs ('open a ticket')."""
	if any(phrase in text for phrase in CREATE_PHRASES):
		return "create"
	if any(word in text.split() or f" {word} " in f" {text} " for word in READ_VERBS):
		return "read"
	if any(word in text for word in UPDATE_VERBS):
		return "update"
	if any(word in text for word in REJECT_VERBS) or any(word in text for word in APPROVE_VERBS):
		return "approve"
	if any(word in text for word in DELETE_VERBS):
		return "delete"
	return None


def doctype_from_text(text, synonyms=None):
	synonyms = synonyms or registry.SYNONYMS
	for key in sorted(synonyms, key=len, reverse=True):
		if re.search(rf"\b{re.escape(key)}\b", text):
			return synonyms[key]
	return None


def record_from_text(text, prefixes=None):
	"""Longest prefix first, so HR-LAP- beats HR-."""
	prefixes = prefixes or registry.PREFIXES
	for prefix in sorted(prefixes, key=len, reverse=True):
		match = re.search(rf"\b{re.escape(prefix.lower())}(\d+)\b", text)
		if match:
			return prefixes[prefix], f"{prefix}{match.group(1)}"
	return None, None


def urgency_from_text(text):
	for word, urgency in URGENCY_WORDS.items():
		if re.search(rf"\b{word}\b", text):
			return urgency
	return None


def transition_from_text(text):
	for word in sorted(TRANSITIONS, key=len, reverse=True):
		if re.search(rf"\b{word}\b", text):
			return TRANSITIONS[word]
	return None


def fields_from_text(text):
	"""The few field hints worth reading off a sentence."""
	fields = {}
	match = PRIORITY_HINT.search(text)
	if match:
		value = (match.group(1) or match.group(2)).capitalize()
		fields["priority"] = value
	return fields


def subject_from_text(raw):
	"""'Open a ticket: printer offline' -> 'printer offline'."""
	if ":" in raw:
		tail = raw.split(":", 1)[1].strip()
		if tail:
			return tail
	return None


def parse(text, *, synonyms=None, prefixes=None, actions=None, my_tasks=None):
	"""Turn one line into an intent dict. ``confidence`` is 0..1."""
	actions = actions or registry.ACTION_REGISTRY
	raw = (text or "").strip()
	lower = normalize(raw)
	intent = {
		"raw": raw,
		"doctype": None,
		"action": None,
		"record": None,
		"scope": None,
		"urgency": urgency_from_text(lower),
		"transition": transition_from_text(lower),
		"fields": fields_from_text(lower),
		"confidence": 0.0,
	}
	if not lower:
		return intent

	record_doctype, record = record_from_text(lower, prefixes)
	doctype = record_doctype or doctype_from_text(lower, synonyms)
	action = action_from_text(lower)

	# "my tasks" / "my tickets" narrows the read to what the user owns. It is set
	# whether the DocType came from a synonym ("my tickets") or from the fallback
	# list, so the executor can add the owner/assignee filter either way.
	if re.search(r"\bmy (tickets|tasks|issues)\b", lower):
		intent["scope"] = "mine"
		if doctype is None:
			doc_types = my_tasks or registry.MY_TASKS_DOCTYPES
			doctype = doc_types[0] if doc_types else None
			action = action or "read"

	intent["doctype"] = doctype
	intent["action"] = action
	intent["record"] = record
	if action == "create" and doctype:
		subject = subject_from_text(raw)
		if subject:
			intent["fields"]["subject"] = subject

	if doctype and action and doctype in actions and action in actions[doctype]:
		intent["confidence"] = 0.95 if record else 0.85
	elif doctype and doctype in actions:
		intent["confidence"] = 0.6
	elif action:
		intent["confidence"] = 0.4
	else:
		intent["confidence"] = 0.2
	return intent


def quick_action(key, *, modules=None, actions=None):
	"""A menu key becomes an intent without parsing anything.

	A module that declares a `create` DocType is a *create* shortcut (clicking
	"Tickets" prompts for the missing fields and then creates an `Issue`, docs/12
	section 8.2); a module that does not is a *read* of its first addressable
	DocType ("My Tasks", "Knowledge Base").
	"""
	modules = modules or registry.MODULES
	actions = actions if actions is not None else registry.ACTION_REGISTRY
	module = modules.get(key or "")
	if not module:
		return None
	doctypes = [d for d in module.get("doctypes", []) if d in actions]
	if not doctypes:
		return None
	doctype = doctypes[0]
	return {
		"raw": key,
		"doctype": doctype,
		"action": "create" if module.get("create") == doctype else "read",
		"record": None,
		"urgency": None,
		"transition": None,
		"fields": {},
		"confidence": 0.9,
		"module": key,
	}


def validate_proposal(proposal, *, synonyms=None, prefixes=None, actions=None):
	"""Validate a model-proposed intent field by field, or return None.

	The LLM is an untrusted *hint*: an unknown action, an unknown DocType or an
	action the registry does not allow for that DocType all mean "no intent"
	(docs/12 section 1.2, R1 and R2).
	"""
	actions = actions or registry.ACTION_REGISTRY
	if not isinstance(proposal, dict):
		return None
	doctype = (proposal.get("doctype") or "").strip()
	if not doctype:
		doctype = doctype_from_text(normalize(str(proposal.get("raw") or "")), synonyms) or ""
	if doctype not in actions:
		return None
	action = (proposal.get("action") or "").strip().lower()
	if action not in actions[doctype]:
		return None
	record = proposal.get("record")
	if record is not None:
		record = str(record).strip() or None
		if record and record.lower() in ("null", "none", "unknown"):
			record = None
	urgency = proposal.get("urgency")
	urgency = urgency if urgency in ("Low", "Medium", "High", "Urgent") else None
	fields = proposal.get("fields")
	if not isinstance(fields, dict):
		fields = {}
	return {
		"raw": str(proposal.get("raw") or ""),
		"doctype": doctype,
		"action": action,
		"record": record,
		"urgency": urgency,
		"transition": None,
		"fields": {k: v for k, v in fields.items() if isinstance(k, str)},
		"confidence": 0.7,
		"proposed": True,
	}
