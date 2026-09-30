# Copyright (c) 2026, Solrise and contributors

"""One report, and everything that happened to it.

The "is anyone coming?" screen (docs/17 section 13). Reached by tapping a row on
the home page, and read through `solrise_erp.portal.reports.detail()`, which
answers `None` for a report that is not the caller's - so this page never has to
decide whether to trust the `?name=` it was handed.

`guard.bootstrap()` has already sent a Guest to the login form by the time the
report is looked up.
"""

import frappe

from solrise_erp.portal import guard, reports

no_cache = 1


def get_context(context):
	guard.bootstrap(context, title="Your report", target="/start/my-report")
	# `None` covers both "no such report" and "not yours", and the page answers
	# the same way to each: a customer who edits the URL learns nothing. The
	# template shows a friendly line rather than a redirect or a 404, because a
	# tap that appears to do nothing is the worst outcome for this reader.
	context.report = reports.detail(name=frappe.form_dict.get("name"))
	return context
