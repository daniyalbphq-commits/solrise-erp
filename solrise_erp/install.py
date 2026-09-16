# Copyright (c) 2026, Solrise and contributors

"""Idempotent install / migrate entry point for the Solrise app.

`hooks.py` points both `after_install` and `after_migrate` at this module, so
`apply_all()` runs on every fresh install and on every `bench migrate`. It is the
one place that re-applies what the app owns and:

* **never raises** - every step is isolated, logged and skipped on failure, so a
  platform version difference cannot abort a migrate,
* **reports what it changed** so the migrate log is a usable audit trail.

Manual re-apply on a running site:

    bench --site <site> execute solrise_erp.install.apply_all
"""

import frappe

# Fields of the `Solrise Settings` single, and the value used when the field is
# empty. A pre-existing Single has no row for a field added by a later release,
# so these are backfilled from `after_migrate` instead of relying on the
# DocType's own default (which only applies to a freshly created single).
SETTINGS_DEFAULTS = [
	("enabled", 0),
	("provider", "OpenAI"),
	("api_base_url", "https://api.openai.com/v1"),
	("model", "gpt-4o-mini"),
	("max_tokens", 1024),
	("temperature", 0.2),
	("rate_limit_per_hour", 60),
	("allow_record_lookup", 1),
	("allow_ticket_creation", 1),
	("allow_navigation", 1),
	("allow_status_update", 0),
	("enable_universal_chat", 1),
	("chat_enable_llm_fallback", 0),
	("chat_confidence_threshold", 0.6),
	("chat_allow_delete", 0),
	("chat_allow_approve", 0),
	("chat_max_slot_turns", 4),
	("chat_enforce_permlevel", 1),
	("chat_knowledge_base_doctype", "Solrise FAQ"),
	("chat_my_tasks_doctypes", "Issue"),
	("chat_urgency_field", "priority"),
	("enable_log_purge", 1),
	("chat_log_retention_days", 90),
	("audit_log_retention_days", 180),
	("message_log_retention_days", 180),
]

# The curated knowledge base ships with one answer so the assistant has a
# non-empty source on a fresh site (docs/06 section 4.4).
FAQ_SEED = {
	"question": "How do I reset my password?",
	"answer": (
		"Use the \"Forgot password\" link on the login page to receive a reset link, "
		"or ask an administrator to reset it for you from User → your user."
	),
	"category": "General",
	"enabled": 1,
}

SETTINGS_SINGLE = "Solrise Settings"
FAQ_DOCTYPE = "Solrise FAQ"


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def ensure_settings_defaults():
	"""Backfill the empty `Solrise Settings` fields; return the filled names.

	Only fields that exist in this version and are empty/`None` are written, so an
	operator's deliberate values (`provider = Ollama`, purge turned off, ...) are
	never overwritten.
	"""
	filled = []
	try:
		if not frappe.db.exists("DocType", SETTINGS_SINGLE):
			frappe.logger().info(
				"install: %s is not installed; no defaults applied", SETTINGS_SINGLE
			)
			return filled
		meta = frappe.get_meta(SETTINGS_SINGLE)
		doc = frappe.get_doc(SETTINGS_SINGLE)
		for fieldname, value in SETTINGS_DEFAULTS:
			if not meta.has_field(fieldname):
				continue
			try:
				current = doc.get(fieldname)
				if current not in (None, ""):
					continue
				doc.set(fieldname, value)
				filled.append(fieldname)
			except Exception:
				_log_error("Solrise install: default {0}".format(fieldname))
		if filled:
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		frappe.logger().info(
			"install: %s default(s) filled%s",
			len(filled),
			(" (%s)" % ", ".join(filled)) if filled else "",
		)
	except Exception:
		_log_error("Solrise install: ensure_settings_defaults")
	return filled


def ensure_faq_seed():
	"""Insert the first `Solrise FAQ` row when the table is empty.

	Returns the created row's name, or `None` when a row already exists (or the
	DocType is not installed).
	"""
	try:
		if not frappe.db.exists("DocType", FAQ_DOCTYPE):
			return None
		if frappe.db.count(FAQ_DOCTYPE):
			return None

		meta = frappe.get_meta(FAQ_DOCTYPE)
		payload = {"doctype": FAQ_DOCTYPE}
		for fieldname, value in FAQ_SEED.items():
			if meta.has_field(fieldname):
				payload[fieldname] = value
		# Without these two a seed row would answer nothing; leave the table empty
		# rather than write a half row.
		if not payload.get("question") or payload.get("answer") is None:
			frappe.logger().info("install: %s has no question/answer field", FAQ_DOCTYPE)
			return None

		doc = frappe.get_doc(payload)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.logger().info("install: seeded %s with %s", FAQ_DOCTYPE, doc.name)
		return doc.name
	except Exception:
		_log_error("Solrise install: ensure_faq_seed")
		return None


def apply_all():
	"""Re-apply everything the app owns; return a summary dict.

	Safe to call at any time, from `after_install`, from `after_migrate` or by
	hand with `bench execute`. Never raises.
	"""
	summary = {"branding": {}, "settings_defaults": [], "faq_seed": None}

	try:
		from solrise_erp import branding

		try:
			summary["branding"] = branding.ensure_all()
			frappe.logger().info("install: branding %s", summary["branding"])
			# app_name / workspace labels are cached in the boot payload.
			try:
				frappe.clear_cache()
			except Exception:
				_log_error("Solrise install: clear_cache")
		except Exception:
			_log_error("Solrise install: branding.ensure_all")
	except Exception:
		_log_error("Solrise install: import branding")

	try:
		summary["settings_defaults"] = ensure_settings_defaults()
	except Exception:
		_log_error("Solrise install: settings defaults")
	try:
		summary["faq_seed"] = ensure_faq_seed()
	except Exception:
		_log_error("Solrise install: faq seed")

	frappe.logger().info("install: apply_all %s", summary)
	return summary


def after_install():
	"""Hook: first install of the app on a site."""
	try:
		return apply_all()
	except Exception:
		# apply_all() is already non-fatal; this is belt and braces so an install
		# is never left half-done by a branding or settings problem.
		_log_error("Solrise install: after_install")
		return {}


def after_migrate():
	"""Hook: every `bench migrate`, including the ones that resync upstream data."""
	try:
		return apply_all()
	except Exception:
		_log_error("Solrise install: after_migrate")
		return {}
