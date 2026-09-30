# Copyright (c) 2026, Solrise and contributors

"""The report form's context.

Every choice the form offers comes from `solrise_erp.portal.catalog`, so the
markup renders whatever buttons the catalogue holds - adding a category is a
one-line change there, and no template edit.
"""

from solrise_erp.portal import catalog, guard

no_cache = 1


def get_context(context):
	guard.bootstrap(context, title="Report a problem", target="/start/report-issue")

	context.categories = catalog.CATEGORIES
	context.urgencies = catalog.URGENCIES
	context.default_category = catalog.DEFAULT_CATEGORY
	context.default_urgency = catalog.DEFAULT_URGENCY
	# Handed to the page so the AJAX call has one definition, in Python.
	context.api_create = "solrise_erp.api.portal.create_issue"
	return context
