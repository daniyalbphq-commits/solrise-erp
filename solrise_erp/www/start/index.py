# Copyright (c) 2026, Solrise and contributors

"""The customer home page: buttons, and nothing else.

`hooks.role_home_page` sends the standard `Customer` role here after login, so
this replaces ERPNext's stock portal landing for those users. The page carries
exactly two pieces of information besides the tiles - who you are, and which
station you are reporting from - because the reader may not read comfortably.

Adding a button means editing `solrise_erp.portal.catalog`, not this file.
"""

from solrise_erp.api import portal
from solrise_erp.portal import guard

no_cache = 1


def get_context(context):
	guard.bootstrap(context, title="Solrise", target=guard.PORTAL_HOME)
	# One reassuring line. `None` (could not count) renders as nothing at all.
	context.open_issues = portal.open_issue_count()
	return context
