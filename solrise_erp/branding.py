# Copyright (c) 2026, Solrise and contributors

"""White-label branding for the Solrise Desk (docs/10-branding.md).

End users see **Solrise** rather than the upstream platform name, and the site
defaults to the United States / USD.

Every function is idempotent, and every write is guarded by
`frappe.db.exists("DocType", ...)` plus `frappe.get_meta(doctype).has_field(...)`,
so a field that does not exist in the installed platform version is skipped with a
log line instead of raising. Anything risky is wrapped in `try/except Exception` +
`frappe.log_error`, so a branding problem can never abort `bench migrate`.

`solrise_erp.install.apply_all()` calls `ensure_all()` from both `after_install`
and `after_migrate`.
"""

import frappe

BRAND = "Solrise"
BRAND_HTML = "<span class='solrise-brand' style='font-weight:600;font-size:1.15rem;letter-spacing:.02em'>Solrise</span>"

COUNTRY = "United States"
CURRENCY = "USD"
LANGUAGE = "en"
TIME_ZONE = "America/New_York"

# (doctype, fieldname, value) - exactly the single-value rows of the
# "What is rebranded" table in docs/10-branding.md. The rest of that table is
# covered by `ensure_company_branding()` / `ensure_workspace_branding()` here,
# by `boot.py` and by the app's client-side boot patch.
SINGLES = [
	("System Settings", "app_name", BRAND),
	("Website Settings", "app_name", BRAND),
	("Website Settings", "brand_html", BRAND_HTML),
	("Website Settings", "copyright", BRAND),
	("Global Defaults", "country", COUNTRY),
	("Global Defaults", "default_currency", CURRENCY),
	("System Settings", "currency", CURRENCY),
	("System Settings", "country", COUNTRY),
	("System Settings", "language", LANGUAGE),
	("System Settings", "time_zone", TIME_ZONE),
]

# Workspace labels/titles the upstream app resyncs on every `bench migrate`.
UPSTREAM_PREFIXES = ("ERPNext", "Frappe")


def _has_field(doctype, fieldname):
	"""A missing DocType or field is a skip, never a crash."""
	try:
		return bool(frappe.db.exists("DocType", doctype)) and bool(
			frappe.get_meta(doctype).has_field(fieldname)
		)
	except Exception:
		return False


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def _rebranded(value):
	"""`ERPNext Integrations` -> `Solrise Integrations`, otherwise unchanged."""
	if not isinstance(value, str):
		return value
	for prefix in UPSTREAM_PREFIXES:
		if value.startswith(prefix):
			return "{0}{1}".format(BRAND, value[len(prefix):])
	return value


def ensure_branding():
	"""Apply the single-value rebrand; return the `"<doctype>.<field>"` changed.

	Fields that do not exist in the installed version, and values that are
	already correct, are skipped silently - the log line names what was written.
	"""
	changed = []
	try:
		for doctype, fieldname, value in SINGLES:
			if not _has_field(doctype, fieldname):
				frappe.logger().info(
					"branding: %s.%s is not in this version; skipped", doctype, fieldname
				)
				continue
			try:
				current = frappe.db.get_single_value(doctype, fieldname)
				if current == value:
					continue
				frappe.db.set_single_value(doctype, fieldname, value)
				changed.append("{0}.{1}".format(doctype, fieldname))
			except Exception:
				# One field is never worth aborting the rest of the rebrand.
				_log_error("Solrise branding: {0}.{1}".format(doctype, fieldname))
		frappe.db.commit()
		frappe.logger().info(
			"branding: %s value(s) written%s",
			len(changed),
			(" (%s)" % ", ".join(changed)) if changed else "",
		)
	except Exception:
		_log_error("Solrise branding: ensure_branding")
	return changed


def ensure_company_branding():
	"""Rename the site's Company to `Solrise` (opt-out: delete/rename by hand).

	The Company name appears on documents and print formats. The abbreviation is
	**not** touched: it stays whatever the site was created with (`@solrise`,
	`SR`, ...), because changing it would rewrite every existing document name
	that uses it.

	Returns the previous name when a rename happened, else `None`.
	"""
	try:
		if not frappe.db.exists("DocType", "Company"):
			return None
		companies = frappe.get_all(
			"Company", fields=["name"], order_by="creation asc", limit_page_length=0
		)
		if not companies:
			frappe.logger().info(
				"branding: no Company yet - run the setup wizard before HR/payroll/accounting"
			)
			return None
		for company in companies:
			old_name = company.get("name")
			if not old_name or old_name == BRAND:
				continue
			try:
				frappe.rename_doc("Company", old_name, BRAND, force=True)
				frappe.db.commit()
				frappe.logger().info(
					"branding: Company renamed %s -> %s (abbr unchanged)", old_name, BRAND
				)
				return old_name
			except Exception:
				# Another Company already holds the name, or a link rewrite was
				# refused; report and leave the data alone.
				_log_error("Solrise branding: rename Company {0}".format(old_name))
				return None
		frappe.logger().info("branding: Company is already %s", BRAND)
	except Exception:
		_log_error("Solrise branding: ensure_company_branding")
	return None


def ensure_workspace_branding():
	"""Rename the upstream `label`/`title` of every affected Workspace.

	Upstream re-syncs its workspaces on every `bench migrate`, which restores the
	original labels; this runs from `after_migrate` (via `apply_all()`) so the
	rebrand is re-applied on the same migrate that would otherwise undo it.

	Returns the names of the workspaces that were changed.
	"""
	renamed = []
	try:
		if not frappe.db.exists("DocType", "Workspace"):
			return renamed
		meta = frappe.get_meta("Workspace")
		fields = [field for field in ("label", "title") if meta.has_field(field)]
		if not fields:
			return renamed

		workspaces = frappe.get_all(
			"Workspace", fields=["name"] + fields, limit_page_length=0
		)
		for row in workspaces:
			changes = {}
			for field in fields:
				value = row.get(field)
				new_value = _rebranded(value)
				if new_value != value:
					changes[field] = new_value
			if not changes:
				continue
			try:
				doc = frappe.get_doc("Workspace", row.get("name"))
				for field, value in changes.items():
					doc.set(field, value)
				doc.save(ignore_permissions=True)
				renamed.append(row.get("name"))
				frappe.logger().info(
					"branding: Workspace %s -> %s", row.get("name"), changes
				)
			except Exception:
				# A single unwritable workspace must not stop the others.
				_log_error("Solrise branding: Workspace {0}".format(row.get("name")))
		if renamed:
			frappe.db.commit()
			frappe.clear_cache()
	except Exception:
		_log_error("Solrise branding: ensure_workspace_branding")
	return renamed


def ensure_all():
	"""Apply every branding step, in order, and return a summary dict.

	Never raises: each step is isolated so one failure cannot stop the others.
	"""
	summary = {"singles": [], "company": None, "workspaces": []}
	for key, step in (
		("singles", ensure_branding),
		("company", ensure_company_branding),
		("workspaces", ensure_workspace_branding),
	):
		try:
			summary[key] = step()
		except Exception:
			_log_error("Solrise branding: {0}".format(step.__name__))
	frappe.logger().info("branding: %s", summary)
	return summary
