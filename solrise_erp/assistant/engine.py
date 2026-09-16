# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""The free-form assistant (docs/06).

One turn is: rate limit -> load the last few turns -> call the model with the tool
schemas -> run the tools it names -> reply -> persist every turn to
``Solrise Chat Log``.

The model holds no database handle. It can only *name* a tool, and a tool runs only
when it passes both gates in ``assistant.tools``, always as the requesting user, so
Frappe's own permission matrix decides what it may see.
"""

from __future__ import annotations

import json
import time

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from solrise_erp.assistant import tools

MAX_TOOL_ROUNDS = 4
MAX_TOOL_OUTPUT = 4000
HISTORY_TURNS = 12
REQUEST_TIMEOUT = 60

DEFAULT_SYSTEM_PROMPT = (
	"You are the Solrise ERP assistant. Only use the provided tools. Never invent "
	"record names. Ask a clarifying question when the request is ambiguous."
)


def settings():
	return frappe.get_cached_doc("Solrise Settings")


def log_turn(session_id, role, message, **kwargs):
	"""Append one turn to Solrise Chat Log. Never raises - a log failure must not
	break the answer the user is waiting for."""
	try:
		doc = frappe.get_doc({
			"doctype": "Solrise Chat Log",
			"session_id": session_id,
			"user": kwargs.get("user") or frappe.session.user,
			"role": role,
			"channel": kwargs.get("channel") or "Desk",
			"message": message,
			"tool_name": kwargs.get("tool_name"),
			"tool_args": kwargs.get("tool_args"),
			"tool_result": kwargs.get("tool_result"),
			"tokens": cint(kwargs.get("tokens")),
			"latency_ms": cint(kwargs.get("latency_ms")),
			"timestamp": now_datetime(),
		})
		doc.insert(ignore_permissions=True)
		return doc.name
	except Exception:
		frappe.log_error(title="Solrise assistant: could not write Solrise Chat Log")
		return None


def rate_limited(user):
	"""Per-user hourly cap, counted in Redis (docs/06 section 4.3)."""
	limit = cint(settings().rate_limit_per_hour)
	if limit <= 0:
		return False
	key = f"solrise_assistant_rl:{user}:{now_datetime().strftime('%Y%m%d%H')}"
	cache = frappe.cache()
	used = cint(cache.get_value(key) or 0)
	if used >= limit:
		return True
	cache.set_value(key, used + 1, expires_in_sec=3700)
	return False


def history(session_id):
	rows = frappe.get_all(
		"Solrise Chat Log",
		filters={"session_id": session_id, "role": ["in", ("user", "assistant")]},
		fields=["role", "message"],
		order_by="creation desc",
		limit=HISTORY_TURNS,
	)
	return [{"role": row.role, "content": row.message or ""} for row in reversed(rows)]


def provider_headers(doc):
	"""Azure sends the key as ``api-key``; everything else uses a bearer token."""
	headers = {"Content-Type": "application/json"}
	key = None
	if doc.api_key:
		try:
			key = doc.get_password("api_key")
		except Exception:
			key = None
	if not key:
		# Ollama and many self-hosted gateways need no key at all.
		return headers
	if (doc.provider or "") == "Azure OpenAI":
		headers["api-key"] = key
	else:
		headers["Authorization"] = f"Bearer {key}"
	return headers


def complete(messages, tool_schemas=None):
	"""One OpenAI-compatible chat completion. Returns (message dict, usage dict)."""
	import requests

	doc = settings()
	base = (doc.api_base_url or "").rstrip("/")
	if not base:
		frappe.throw(_("Set the provider API base URL in Solrise Settings."))
	payload = {
		"model": doc.model or "gpt-4o-mini",
		"messages": messages,
		"max_tokens": cint(doc.max_tokens) or 1024,
		"temperature": flt(doc.temperature),
	}
	if tool_schemas:
		payload["tools"] = tool_schemas
	try:
		response = requests.post(
			f"{base}/chat/completions",
			headers=provider_headers(doc),
			json=payload,
			timeout=REQUEST_TIMEOUT,
		)
	except Exception as exc:
		frappe.throw(_("Could not reach the model provider: {0}").format(exc))
	if response.status_code >= 400:
		frappe.throw(
			_("The model provider answered {0}: {1}").format(
				response.status_code, (response.text or "")[:300]
			)
		)
	data = response.json()
	choices = data.get("choices") or []
	if not choices:
		frappe.throw(_("The model provider returned no choices."))
	return choices[0].get("message") or {}, data.get("usage") or {}


def ask(message, session_id=None, channel="Desk", user=None):
	"""Handle one assistant turn and return a dict the API layer hands back."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	message = (message or "").strip()
	if not message:
		frappe.throw(_("Ask a question first."))

	doc = settings()
	session_id = (session_id or "").strip() or frappe.generate_hash(length=12)
	started = time.time()

	if not cint(doc.enabled):
		return {
			"ok": False,
			"session_id": session_id,
			"error": _("The assistant is not enabled. Turn it on in Solrise Settings."),
		}
	if rate_limited(user):
		return {
			"ok": False,
			"session_id": session_id,
			"error": _("Rate limit reached for this hour. Try again later."),
		}

	log_turn(session_id, "user", message, user=user, channel=channel)
	messages = [
		{"role": "system", "content": doc.system_prompt or DEFAULT_SYSTEM_PROMPT},
		*history(session_id),
		{"role": "user", "content": message},
	]
	schemas = tools.schemas(user=user)
	used_tools = []
	reply = None
	tokens = 0

	# Bounded loop: the model may ask for tools, but never indefinitely.
	for _round in range(MAX_TOOL_ROUNDS):
		assistant_message, usage = complete(messages, schemas or None)
		tokens += cint(usage.get("total_tokens"))
		calls = assistant_message.get("tool_calls") or []
		if not calls:
			reply = (assistant_message.get("content") or "").strip()
			break

		messages.append(assistant_message)
		for call in calls:
			function = call.get("function") or {}
			name = function.get("name")
			try:
				args = json.loads(function.get("arguments") or "{}")
			except ValueError:
				args = {}
			result = tools.dispatch(name, args, user=user)
			used_tools.append(name)
			# Tool output is truncated before it re-enters the context, so a large
			# record cannot leak wholesale into a third-party model.
			log_turn(
				session_id,
				"tool",
				json.dumps(result, default=str)[:MAX_TOOL_OUTPUT],
				user=user,
				channel=channel,
				tool_name=name,
				tool_args=json.dumps(args, default=str)[:MAX_TOOL_OUTPUT],
			)
			messages.append({
				"role": "tool",
				"tool_call_id": call.get("id"),
				"content": json.dumps(result, default=str)[:MAX_TOOL_OUTPUT],
			})
	else:
		reply = _("I could not finish that in a reasonable number of steps. Try narrowing it down.")

	latency = int((time.time() - started) * 1000)
	log_turn(
		session_id, "assistant", reply or "", user=user, channel=channel, tokens=tokens, latency_ms=latency
	)
	return {
		"ok": True,
		"session_id": session_id,
		"reply": reply or "",
		"tools": used_tools,
		"tokens": tokens,
		"latency_ms": latency,
	}


def propose_intent(message, user=None):
	"""Ask the model for a structured intent as JSON - the chat's optional fallback.

	Returns a dict or None. The model still gets no database handle: the proposal is
	validated field by field against the action registry before the permission gate
	ever sees it (docs/12 section 1.3).
	"""
	doc = settings()
	if not cint(doc.enabled) or not cint(doc.chat_enable_llm_fallback):
		return None
	prompt = (
		"You translate a request into JSON only. Answer with an object with the keys "
		"doctype, action (read|create|update|submit|cancel|delete|approve), record "
		"(an existing document name or null), urgency (Low|Medium|High|Urgent or null) "
		"and fields (an object of values). No explanation, JSON only."
	)
	try:
		assistant_message, _usage = complete([
			{"role": "system", "content": prompt},
			{"role": "user", "content": message},
		])
		return json.loads((assistant_message.get("content") or "").strip())
	except Exception:
		frappe.log_error(title="Solrise assistant: intent proposal failed")
		return None


def health():
	"""Cheap status probe used by monitoring and by ``make verify``."""
	doc = settings()
	has_key = False
	if doc.api_key:
		try:
			has_key = bool(doc.get_password("api_key"))
		except Exception:
			has_key = True
	return {
		"app": "solrise_erp",
		"assistant_enabled": bool(cint(doc.enabled)),
		"provider": doc.provider,
		"model": doc.model,
		"api_base_url": doc.api_base_url,
		"api_key_set": has_key,
		"rate_limit_per_hour": cint(doc.rate_limit_per_hour),
		"tools": sorted(tools.TOOL_REGISTRY),
		"universal_chat": bool(cint(doc.enable_universal_chat)),
		"llm_fallback": bool(cint(doc.chat_enable_llm_fallback)),
	}
