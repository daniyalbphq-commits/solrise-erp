# Copyright (c) 2026, Solrise and contributors

"""The gate every portal page passes through, in one place.

Keeping it in one function is the point: a page cannot forget the login check,
and every page gets the same context (title, buttons, station) whether or not its
own author remembered to ask for it.

A Guest is sent to the login form with ``redirect-to``, which is what makes a
bookmark and a session timeout behave: they come back to the page they wanted.
"""

from urllib.parse import quote

import frappe

from solrise_erp.portal import catalog, identity

#: Where a customer lands after login. `hooks.role_home_page` points the standard
#: `Customer` role at the same route, so this is the one place to change it.
PORTAL_HOME = "/start"

#: One hop that ends the session and lands on the login form. Every portal page
#: carries it, because a station phone is a shared device. A *website* route, not
#: `/api/method`: returned from a whitelisted method, Frappe answers with a
#: Location-less 301 and the browser is left on a blank page (docs/17 section 10).
LOGOUT_URL = "/start/logout"


def require_login(target=PORTAL_HOME):
	"""Raise `frappe.Redirect` to the login form unless somebody is logged in.

	Mirrors what `frappe/www/login.py` itself does, so the round trip keeps the
	destination instead of dumping the user on the website home page.
	"""
	if identity.current_user():
		return
	frappe.local.flags.redirect_location = "/login?redirect-to={0}".format(quote(target or PORTAL_HOME))
	raise frappe.Redirect


def bootstrap(context, title, target=PORTAL_HOME):
	"""Fill `context` for a portal page. Call this first in ``get_context``.

	`no_cache` is set because every one of these pages renders the session user's
	name, station and reports: a cached copy would show one customer another
	customer's page.
	"""
	require_login(target)

	context.no_cache = 1
	context.title = title
	context.portal_home = PORTAL_HOME
	context.logout_url = LOGOUT_URL
	context.full_name = identity.display_name()

	try:
		context.tiles = catalog.visible_tiles(frappe.get_roles())
	except Exception:
		# A role set that cannot be read must not take the page down; the
		# catalogue's own default is the unrestricted tiles.
		context.tiles = catalog.visible_tiles(())

	resolved = identity.resolve()
	context.identity = resolved

	# A manager can cover several stores, so the pages work from the list and use the
	# count to decide whether to show a name or a number (docs/17 section 11).
	context.stores = identity.stores()
	context.store_count = len(context.stores)
	context.station = (
		context.stores[0]["customer_name"]
		if context.store_count == 1
		else identity.station_label(resolved)
	)
	return context
