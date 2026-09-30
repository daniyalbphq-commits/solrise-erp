# Copyright (c) 2026, Solrise and contributors

"""The way out: end the session, then land on the login form.

A station phone is a shared device, so the end of the report flow is not "your
report was filed" - it is "the next person can log in".

**Why a website route and not an `/api/method` call.** Both were measured on a
live site. Returned from a whitelisted method, Frappe answers with a 301 that has
no `Location` header and a JSON body: the session *is* ended (the `sid` cookie is
cleared), but the browser is left on a blank page instead of the login form. A
website route goes through `frappe.local.flags.redirect_location`, which is the
same mechanism `portal.guard.require_login` already relies on, and it produces a
correct `Location`. `docs/17` section 10 records the measurement.

The page itself never renders - the redirect is raised from `get_context`. The
template exists because a `www/` route needs one, and it doubles as a sane page if
the redirect is ever bypassed.
"""

import frappe

no_cache = 1


def get_context(context):
	"""Log out if anyone is logged in, then redirect to the login form."""
	if frappe.session.user != "Guest":
		try:
			frappe.local.login_manager.logout()
			frappe.db.commit()
		except Exception:
			try:
				frappe.log_error(title="Solrise portal: logout", message=frappe.get_traceback())
			except Exception:
				pass

	# Unconditional, so a second press, an expired session or a Guest all end up on
	# the login form rather than on an error.
	frappe.local.flags.redirect_location = "/login"
	raise frappe.Redirect
