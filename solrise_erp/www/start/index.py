# Copyright (c) 2026, Solrise and contributors

"""The customer home page: buttons, and what happened to what you sent.

`hooks.role_home_page` sends the standard `Customer` role here after login, so
this replaces ERPNext's stock portal landing for those users. The page carries
exactly three pieces of information besides the tiles - who you are, which
station you are reporting from, and the state of your own reports - because the
reader may not read comfortably.

Adding a button means editing `solrise_erp.portal.catalog`, not this file. The
reports list reads through `solrise_erp.portal.reports`, which scopes every query
to the caller's own Issues.
"""

from solrise_erp.portal import guard, reports

no_cache = 1


def get_context(context):
	guard.bootstrap(context, title="Solrise", target=guard.PORTAL_HOME)
	# The list replaces the old "you have N reports with us" line: the reports
	# themselves say it better, and each one is a tap away from its updates.
	context.reports = reports.rows()
	return context
