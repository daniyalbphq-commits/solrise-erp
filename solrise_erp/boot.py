# Copyright (c) 2026, Solrise and contributors

"""Rewrite the Desk boot payload so the upstream app name never shows.

Registered as the `boot_session` hook in `hooks.py`. Part of the payload (the
app-switcher entries and `app_logo_url`) is attached by the upstream app *after*
this hook has run, which is why `public/js/solrise_erp.js` repeats the same
replacements client-side - either side alone leaves a surface that still reads
"ERPNext"/"Frappe".

The whole body is a single `try/except`: a surprise in the Desk payload must
never break login.
"""

import frappe

APP_NAME = "Solrise"
APP_TITLE = "Solrise ERP"
LOGO_URL = "/assets/solrise_erp/images/solrise-logo.png"
UPSTREAM_NAMES = ("ERPNext", "Frappe")


def boot_session(bootinfo):
	"""Hook: rebrand `app_name`, the app-switcher entries and the navbar logo."""
	try:
		if not isinstance(bootinfo, dict):
			return

		if "app_name" in bootinfo:
			bootinfo["app_name"] = APP_NAME

		apps_data = bootinfo.get("apps_data")
		apps = apps_data.get("apps") if isinstance(apps_data, dict) else None
		if isinstance(apps, (list, tuple)):
			for app in apps:
				if not isinstance(app, dict):
					continue
				title = app.get("title") or ""
				if isinstance(title, str) and any(name in title for name in UPSTREAM_NAMES):
					app["title"] = APP_TITLE
					if "app_logo_url" in app:
						app["app_logo_url"] = LOGO_URL

		if "app_logo_url" in bootinfo:
			bootinfo["app_logo_url"] = LOGO_URL
	except Exception:
		try:
			frappe.log_error(title="Solrise boot_session", message=frappe.get_traceback())
		except Exception:
			# Logging must not be the thing that breaks the login payload.
			pass
