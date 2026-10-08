# Copyright (c) 2026, Solrise and contributors

"""Checks for the customer portal, runnable without Frappe, bench or a site.

    cd apps/solrise_erp && python3 -m unittest solrise_erp.tests.test_portal_catalog

Why these checks rather than a rendering test: the portal is a button catalogue
plus two templates, and the failure modes that actually bite are structural -

* a tile pointing at a page nobody wrote (a dead button is the worst outcome for
  a user who cannot read the error);
* two buttons sharing a key, so one of them is unreachable;
* a label too long for a phone, which pushes the icon off the fold;
* the landing route in `hooks.py` drifting away from the page that exists;
* the portal CSS/JS not being declared, so the page renders as unstyled markup -
  which is exactly the "broken CSS" failure this deployment has hit before.

All of those are caught here in a second with no bench. Whether the page looks
right still needs a human (docs/17).
"""

import os
import re
import unittest

# Imported at module level for the state and category checks below, which are
# pure catalogue questions. The older test cases import it inside `setUp`.
from solrise_erp.portal import catalog

#: Longest label the big-button layout can show without wrapping past two lines
#: on a 360px-wide phone.
LABEL_LIMIT = 24


def _app_dir():
	"""The app package directory, found by walking up from this file."""
	directory = os.path.dirname(os.path.abspath(__file__))
	while directory and directory != os.path.dirname(directory):
		if os.path.isdir(os.path.join(directory, "portal")):
			return directory
		directory = os.path.dirname(directory)
	return ""


def _read(*parts):
	path = os.path.join(_app_dir(), *parts)
	with open(path, encoding="utf-8") as handle:
		return handle.read()


class CatalogShapeTest(unittest.TestCase):
	"""The catalogue itself: every entry complete, unique and phone-sized."""

	def setUp(self):
		from solrise_erp.portal import catalog

		self.catalog = catalog

	def _unique(self, entries, field, what):
		seen = [entry.get(field) for entry in entries]
		missing = [index for index, value in enumerate(seen) if not value]
		self.assertEqual([], missing, "{0} entries missing `{1}`: {2}".format(what, field, missing))
		self.assertEqual(
			len(set(seen)),
			len(seen),
			"{0} keys are not unique: {1}".format(what, seen),
		)

	def test_every_choice_belongs_to_exactly_one_desk(self):
		"""Keys and labels are unique across *all* desks.

		`category_by_label` recovers a button's icon from an Issue subject, so two
		desks sharing a label would show the wrong icon on one of them.
		"""
		seen = {}
		labels = {}
		for department in self.catalog.DEPARTMENTS:
			for category in department["categories"]:
				self.assertNotIn(
					category["key"], seen, "{0} is on two desks".format(category["key"])
				)
				self.assertNotIn(
					category["label"], labels, "{0} is on two desks".format(category["label"])
				)
				seen[category["key"]] = department["key"]
				labels[category["label"]] = department["key"]
		self.assertEqual(len(seen), len(self.catalog.ALL_CATEGORIES))

	def test_departments_are_complete_and_addressable(self):
		self._unique(self.catalog.DEPARTMENTS, "key", "department")
		self._unique(self.catalog.DEPARTMENTS, "label", "department")
		for department in self.catalog.DEPARTMENTS:
			key = department["key"]
			self.assertTrue(department.get("caption"), key)
			self.assertTrue(department.get("icon"), key)
			self.assertTrue(department["assignees"], "{0} has nobody to route to".format(key))
			self.assertTrue(self.catalog.categories_for(key), key)
			self.assertEqual(
				self.catalog.department_for_category(department["categories"][0]["key"])["key"],
				key,
			)
			self.assertEqual(
				self.catalog.department_for_category(department["default_category"])["key"],
				key,
				"{0}: the default button is not on this desk".format(key),
			)
		self.assertIsNone(self.catalog.department_for_category("not-a-category"))
		self.assertIsNone(self.catalog.department("not-a-department"))
		self.assertIsNone(self.catalog.category(f"{self.catalog.DEFAULT_DEPARTMENT}-other"))

	def test_every_tile_opens_a_desk(self):
		for tile in self.catalog.TILES:
			self.assertEqual(
				tile.get("department"),
				tile["key"],
				"the tile at {0} does not name the desk it opens".format(tile["route"]),
			)

	def test_tiles_are_complete_and_unique(self):
		self._unique(self.catalog.TILES, "key", "tile")
		for tile in self.catalog.TILES:
			for field in ("label", "icon", "route"):
				self.assertTrue(tile.get(field), "tile {0} has no {1}".format(tile.get("key"), field))

	def test_tile_labels_fit_a_phone(self):
		for tile in self.catalog.TILES:
			self.assertLessEqual(
				len(tile["label"]),
				LABEL_LIMIT,
				"tile label is too long for the big-button layout: {0!r}".format(tile["label"]),
			)

	def test_every_tile_route_has_a_page(self):
		"""A button must never point at a route with no template behind it."""
		for tile in self.catalog.TILES:
			route = tile["route"].strip("/")
			self.assertTrue(route, "tile {0} has an empty route".format(tile.get("key")))
			for name in ("index.html", "index.py"):
				path = os.path.join(_app_dir(), "www", route, name)
				self.assertTrue(
					os.path.isfile(path),
					"tile {0} points at /{1} but {1}/{2} does not exist".format(
						tile.get("key"), route, name
					),
				)

	def test_tiles_are_role_scoped_or_open(self):
		for tile in self.catalog.TILES:
			roles = tile.get("roles")
			self.assertTrue(
				roles is None or isinstance(roles, (list, tuple, set)),
				"tile {0} has a roles value that cannot be checked: {1!r}".format(
					tile.get("key"), roles
				),
			)

	def test_a_restricted_tile_fails_closed(self):
		"""Unrestricted tiles are for everyone; restricted ones need the role.

		Everything shipped today is unrestricted, so the rule itself is checked
		against a temporary tile that is *not* for the customer role - otherwise
		this test would pass whether or not the filter worked.
		"""
		self.assertEqual(
			len(self.catalog.TILES),
			len(self.catalog.visible_tiles(("Customer",))),
			"the unrestricted tiles disappeared for a logged-in role",
		)

		original = self.catalog.TILES
		try:
			self.catalog.TILES = (
				{
					"key": "managers-only",
					"label": "Managers only",
					"icon": "*",
					"route": "/start",
					"roles": ("Support Manager",),
				},
			)
			for wrong_role in (("Customer",), ("Guest",), (), None):
				self.assertEqual(
					[],
					self.catalog.visible_tiles(wrong_role),
					"a restricted tile leaked to {0!r}".format(wrong_role),
				)
			self.assertEqual(
				1,
				len(self.catalog.visible_tiles(("Customer", "Support Manager"))),
				"a restricted tile is missing for a role that holds it",
			)
		finally:
			self.catalog.TILES = original

	def test_categories_are_complete_and_unique(self):
		self._unique(self.catalog.CATEGORIES, "key", "category")
		for category in self.catalog.CATEGORIES:
			for field in ("label", "icon"):
				self.assertTrue(
					category.get(field), "category {0} has no {1}".format(category.get("key"), field)
				)

	def test_urgencies_map_to_a_priority(self):
		self._unique(self.catalog.URGENCIES, "key", "urgency")
		for urgency in self.catalog.URGENCIES:
			self.assertTrue(
				urgency.get("priority"),
				"urgency {0} does not say which Issue priority it means".format(urgency.get("key")),
			)

	def test_defaults_exist(self):
		self.assertIsNotNone(self.catalog.category(self.catalog.DEFAULT_CATEGORY))
		self.assertIsNotNone(self.catalog.urgency(self.catalog.DEFAULT_URGENCY))
		# The API falls back to these, so they must never return None.
		self.assertTrue(self.catalog.default_category().get("key"))
		self.assertTrue(self.catalog.default_urgency().get("key"))

	def test_lookup_is_forgiving(self):
		"""A junk or missing value must degrade, never raise: the API relies on it."""
		for value in (None, "", "   ", "not-a-category", 17):
			self.assertIsNone(self.catalog.category(value))
			self.assertTrue(self.catalog.category(value) or self.catalog.default_category())
		self.assertIsNone(self.catalog.urgency("not-an-urgency"))
		self.assertIsNone(self.catalog.tile("nope"))

	def test_catalog_does_not_import_frappe(self):
		source = _read("portal", "catalog.py")
		self.assertNotIn(
			"import frappe",
			source,
			"the catalogue must stay importable without Frappe - that is what lets this test run in CI",
		)


class ReportStatesTest(unittest.TestCase):
	"""The customer's "where is my report?" vocabulary.

	The states are what a non-reading user actually gets: an icon, a colour and
	two words. Getting the *mapping* wrong is worse than getting the wording wrong,
	so both halves are pinned here.
	"""

	def test_statuses_cover_every_value_of_the_doctype(self):
		"""Every `Issue.status` maps to a state - the five this ERPNext ships.

		`verify_portal.py` re-checks this against the live DocType meta, because
		the list below is a transcription and an upstream release can add to it.
		"""
		shipped = ["Open", "Replied", "On Hold", "Resolved", "Closed"]
		for status in shipped:
			self.assertIn(status, catalog.STATUS_TO_STATE, "{0} has no state".format(status))
		self.assertIn(catalog.DEFAULT_STATE, {s["key"] for s in catalog.STATES})

	def test_every_state_is_renderable(self):
		keys = [state["key"] for state in catalog.STATES]
		self.assertEqual(len(keys), len(set(keys)), "two states share a key")
		for state in catalog.STATES:
			for field in ("key", "label", "sentence", "icon", "tone"):
				self.assertTrue(state.get(field), "{0} has no {1}".format(state.get("key"), field))
			self.assertLessEqual(
				len(state["label"]),
				LABEL_LIMIT,
				'"{0}" will not fit the row on a phone'.format(state["label"]),
			)

	def test_tone_names_a_stylesheet_class(self):
		"""`.sl-state--<tone>` has to exist, or the chip renders uncoloured."""
		css = _read("public", "css", "solrise_portal.css")
		for state in catalog.STATES:
			self.assertIn(
				".sl-state--{0}".format(state["tone"]),
				css,
				"no colour for tone {0}".format(state["tone"]),
			)

	def test_every_catalogue_category_is_recoverable_from_its_label(self):
		"""A report shows the icon the customer tapped, recovered from the subject.

		`api.portal._subject()` writes the label into the Issue subject, so the
		lookup is by label and must round-trip for every category - otherwise the
		one that fails silently shows no icon at all.
		"""
		for category in catalog.CATEGORIES:
			found = catalog.category_by_label(category["label"])
			self.assertIsNotNone(found, "no category for {0}".format(category["label"]))
			self.assertEqual(found["key"], category["key"])
		self.assertIsNone(catalog.category_by_label("Not a category"))

	def test_no_status_falls_through_to_finished(self):
		"""An unknown status must understate, never overstate.

		Telling a station "it is fixed" when it is not is the one wrong answer that
		cannot be walked back, so the fallback is `waiting`.
		"""
		state = catalog.state_for_status("Something New Upstream")
		self.assertNotEqual(state["key"], "done")
		self.assertEqual(state["key"], catalog.DEFAULT_STATE)


class ReportPageTest(unittest.TestCase):
	"""The pages that show a customer their own reports."""

	def test_the_home_page_lists_reports_and_links_to_the_detail(self):
		home = _read("www", "start", "index.html")
		self.assertIn("reports", home, "the home page does not render the report list")
		match = re.search(r'href="(/start/[a-z-]+)\?name=', home)
		self.assertIsNotNone(match, "no row links to a report detail route")
		route = match.group(1).strip("/")
		for name in ("index.html", "index.py"):
			self.assertTrue(
				os.path.isfile(os.path.join(_app_dir(), "www", route, name)),
				"rows link to /{0} but {0}/{1} does not exist".format(route, name),
			)

	def test_the_detail_page_only_asks_for_its_own_report(self):
		"""The lookup must go through `reports.detail`, which enforces ownership.

		The page reading `frappe.get_doc` itself would hand any customer any report
		whose name they could guess.
		"""
		page = _read("www", "start", "my-report", "index.py")
		self.assertIn("reports.detail(", page)
		self.assertNotIn("frappe.get_doc", page)

	def test_the_reader_scopes_every_query_to_the_caller(self):
		source = _read("portal", "reports.py")
		self.assertIn('"owner": user', source, "the list is not scoped to the owner")
		self.assertIn("!= user.lower()", source, "the detail does not re-check the owner")

	def test_the_list_shows_open_reports_and_the_detail_does_not(self):
		"""The list is scoped to open reports; a link to a finished one still works.

		Both halves are deliberate, and both are the kind of thing a later tidy-up
		would "unify" the wrong way: filtering `detail()` would make a fix invisible
		to the person who reported it, and un-filtering `rows()` would fill the
		screen with work that is already done.
		"""
		source = _read("portal", "reports.py")
		self.assertIn("FINISHED_STATUSES = (", source)
		self.assertIn("SHOW_FINISHED = False", source)
		self.assertIn('["not in", list(FINISHED_STATUSES)]', source)

		block = source.split("def detail(", 1)[-1]
		self.assertNotIn("FINISHED_STATUSES", block, "detail() must not filter finished reports")

		for status in ("Resolved", "Closed"):
			self.assertIn(status, source, "{0} is not treated as finished".format(status))


class ReportFormTest(unittest.TestCase):
	"""The form every report travels through: the required note, the name picker.

	Both are asked of a station employee, and both are the kind of thing that is
	easy to *look* present and not be wired: a label that says required without a
	check, or a select whose value nobody submits. So the assertions run from the
	markup down to the payload.
	"""

	def test_say_more_is_required(self):
		form = _read("www", "start", "report-issue", "index.html")
		self.assertNotIn("(optional)", form, "the note is still labelled optional")
		self.assertIn("required aria-required", form, "the note is not marked required")

		# And the server refuses it too: a phone is not the only client that can
		# post to a whitelisted method.
		api = _read("api", "portal.py")
		self.assertIn('frappe.throw(_("Please say what is wrong.")', api)
		self.assertIn('frappe.throw(_("Please choose your name.")', api)

	def test_a_missing_name_falls_back_to_the_top_option(self):
		"""A stale picker must not cost a report.

		The form sends the chosen name; when it does not - an older cached script, or
		a request made by hand - the server files the picker's top option (the store's
		first manager) instead of refusing. Pinned structurally because the resolver
		needs a database; `docs/17` section 14 has the reasoning.
		"""
		api = _read("api", "portal.py")
		self.assertIn("def _reporter_default(", api, "the name default resolver is gone")
		self.assertIn(
			"or _reporter_default(customer, person)",
			api,
			"create_issue does not fall back to the picker's top option",
		)
		# The refusal stays as the last resort, for a caller with no name at all.
		self.assertIn('frappe.throw(_("Please choose your name.")', api)

	def test_the_form_carries_the_desk(self):
		"""The desk decides the buttons, the assignee and the email - so it is sent."""
		form = _read("www", "start", "report-issue", "index.html")
		self.assertIn('name="department"', form, "the desk is not submitted with the form")

		js = _read("public", "js", "solrise_portal.js")
		self.assertIn('department: value_of("sl-department")', js)

		api = _read("api", "portal.py")
		self.assertIn("department=None", api, "create_issue does not accept a department")
		self.assertIn("_assign_desk(", api, "the report is not assigned to its desk")
		self.assertIn("_email_desk(", api, "the desk is not emailed")

		page = _read("www", "start", "report-issue", "index.py")
		self.assertIn("catalog.categories_for(", page, "the form shows every desk's buttons")

	def test_the_customer_picker_is_wired_from_markup_to_payload(self):
		form = _read("www", "start", "report-issue", "index.html")
		self.assertIn('id="sl-customer"', form)
		self.assertIn('name="reporter"', form)
		# The per-store lists ride along as data, so changing store is not a round
		# trip; the script rebuilds the select from this.
		self.assertIn('data-stores="{{ reporter_json|e }}"', form)
		self.assertIn("{% for name in reporter_options %}", form)

		js = _read("public", "js", "solrise_portal.js")
		self.assertIn("function wire_reporter", js)
		self.assertIn("wire_reporter();", js, "the picker is never wired")
		self.assertIn("reporter: reporter,", js, "the chosen name is not submitted")

		api = _read("api", "portal.py")
		self.assertIn("reporter=None", api, "create_issue does not accept a reporter")
		self.assertIn('"reporter": reporter,', api, "the response omits the reporter")

	def test_the_reporter_rides_with_the_note(self):
		"""Filed with the report, in the record line - not as prose in the note.

		Keeping it out of the note is what lets the portal show the customer their
		own words without the form's bookkeeping - pinned from the other side by
		`ReportFormTest.test_the_note_has_the_record_line_stripped`.
		"""
		api = _read("api", "portal.py")
		self.assertIn('note, reporter=""', api, "the composer does not take the reporter")
		self.assertIn('_("Customer"), reporter', api)
		self.assertIn("<p><small>", api, "the record line is no longer its own paragraph")

	def test_the_note_has_the_record_line_stripped(self):
		source = _read("portal", "reports.py")
		self.assertIn("RECORD_RE = re.compile", source)
		self.assertIn("def _strip_record(", source)
		self.assertIn('_strip_record(issue.get("description"))', source)

	def test_the_picker_names_come_from_the_linked_contacts(self):
		"""The list is the store's own contacts, not a second copy of the sheet.

		The sheet writes names both "Last, First" and "First Last", and its commas
		are the surname separator rather than a list of people - so re-parsing it
		here would be a way to invent colleagues.
		"""
		source = _read("portal", "identity.py")
		self.assertIn("def managers(", source)
		self.assertIn('"link_doctype": "Customer"', source)
		self.assertIn("first_name", source)
		form = _read("www", "start", "report-issue", "index.py")
		self.assertIn("identity.managers(", form)


class HookWiringTest(unittest.TestCase):
	"""The wiring that makes the portal reachable at all."""

	def test_landing_route_has_a_page(self):
		"""`role_home_page` must map the Customer role to a route that exists.

		Also pins the *shape*: Frappe's `get_home_page_via_hooks()` indexes the
		value with `[-1]`, so the route has to be a one-element list. A bare
		string would silently resolve to its last letter.
		"""
		hooks = _read("hooks.py")
		self.assertIn("role_home_page", hooks, "nothing sends the Customer role to the portal")
		match = re.search(r'"Customer"\s*:\s*\[\s*"([^"]+)"\s*\]', hooks)
		self.assertIsNotNone(
			match,
			'role_home_page must look like {"Customer": ["start"]} - a list, not a string',
		)
		route = match.group(1).strip("/")
		for name in ("index.html", "index.py"):
			self.assertTrue(
				os.path.isfile(os.path.join(_app_dir(), "www", route, name)),
				"the Customer role lands on /{0} but {0}/{1} does not exist".format(route, name),
			)

	def test_portal_assets_exist_and_are_declared(self):
		hooks = _read("hooks.py")
		for relative, declared in (
			(("public", "css", "solrise_portal.css"), "css/solrise_portal.css"),
			(("public", "js", "solrise_portal.js"), "js/solrise_portal.js"),
		):
			self.assertTrue(os.path.isfile(os.path.join(_app_dir(), *relative)), os.path.join(*relative))
			self.assertIn(
				'_asset_url("{0}")'.format(declared),
				hooks,
				"{0} is not declared in hooks.py".format(declared),
			)

	def test_asset_urls_are_cache_busted(self):
		"""Every asset URL carries a `?v=` stamp.

		Without it a browser keeps the copy it cached across a deploy, and a stale
		form then posts to a server that expects fields the old script never sent -
		which is exactly how the reporter picker broke (docs/16 section 7).
		"""
		hooks = _read("hooks.py")
		self.assertIn("def _asset_url(", hooks, "the cache-bust helper is gone")
		self.assertIn('?v={1}', hooks, "the cache-bust stamp is gone")
		for relative in (
			"js/solrise_erp.js",
			"js/solrise_chat.js",
			"js/solrise_portal.js",
			"css/solrise_portal.css",
		):
			self.assertIn('_asset_url("{0}")'.format(relative), hooks, relative)

	def test_a_stray_customer_is_sent_back_to_the_portal(self):
		"""A station login refused at /app must be offered a way back.

		Frappe answers a `Website User` on a Desk URL with a 403 "Not Permitted"
		page, which is correct and, for someone who cannot read it, a dead end. The
		portal scripts are loaded on that page, so they ask `portal_home` and move
		on. This is structural because the failure it prevents is silent: a renamed
		endpoint or a dropped call leaves the customer staring at the refusal again,
		with no test failing anywhere (docs/17 section 12).
		"""
		api = _read(os.path.join("api", "portal.py"))
		self.assertIn("def portal_home(", api)
		self.assertRegex(
			api,
			r'@frappe\.whitelist\(allow_guest=True\)\s*\ndef portal_home\(',
			"portal_home must admit Guests: the page it rescues may have no session",
		)

		js = _read(os.path.join("public", "js", "solrise_portal.js"))
		self.assertIn("function rescue_stray_customer", js)
		self.assertIn("rescue_stray_customer();", js, "the rescue is defined but never called")
		self.assertIn("solrise_erp.api.portal.portal_home", js, "the rescue does not ask the server")
		# Guards, both load-bearing: without the path test this fires on every
		# page, and without the renderer test it would fight the Desk itself.
		self.assertIn('data-path") !== "message"', js)
		self.assertIn("frappe-session-status", js)

	def test_report_template_renders_the_catalogue(self):
		"""The form must be driven by the catalogue, not by hard-coded buttons."""
		template = _read("www", "start", "report-issue", "index.html")
		for token in ("categories", "urgencies", "api_create"):
			self.assertIn(token, template, "the report form no longer renders {0}".format(token))

	def test_only_the_api_module_writes(self):
		"""The pages read; `api/portal.py` is the single write path (docs/17)."""
		for name in ("index.py",):
			source = _read("www", "start", name)
			self.assertNotIn("insert()", source)
			self.assertNotIn("ignore_permissions", source)
		report_source = _read("www", "start", "report-issue", "index.py")
		self.assertNotIn("insert()", report_source)
		self.assertNotIn("ignore_permissions", report_source)

	def test_api_escapes_customer_text(self):
		"""The note is rendered as HTML in the Desk, so it must be escaped here."""
		source = _read("api", "portal.py")
		self.assertIn("escape_html", source, "customer text reaches a Text Editor field unescaped")

	def test_both_ends_of_the_flow_offer_a_logout(self):
		"""A station phone is shared, so logging out has to be reachable.

		The confirmation panel is the one the requirement named; the button page
		carries it too, because a user who opens the portal and changes their mind
		must not leave the session open for the next person.
		"""
		for page in (("www", "start", "report-issue", "index.html"), ("www", "start", "index.html")):
			self.assertIn(
				"logout_url",
				_read(*page),
				"{0} offers no way out".format("/".join(page)),
			)

		self.assertIn(
			"logout_url", _read("portal", "guard.py"), "no page context carries the logout route"
		)

		# The route must be a website page, not an /api/method call: on the API path
		# Frappe answers with a Location-less 301 and strands the browser. Measured on
		# a live site, not assumed (docs/17 section 10).
		self.assertIn('LOGOUT_URL = "/start/logout"', _read("portal", "guard.py"))
		route = _read("www", "start", "logout", "index.py")
		self.assertIn('redirect_location = "/login"', route, "logout must end on the login form")
		self.assertNotIn(
			"@frappe.whitelist",
			route,
			"a whitelisted method cannot redirect a browser - see docs/17 section 10",
		)

	def test_a_store_comes_from_the_users_own_links(self):
		"""A manager can cover several stores, so a posted store is never trusted.

		Five of the source list's managers run more than one site (docs/18 section 2),
		which is why the report form can ask and why `create_issue` has to check the
		answer against the caller's own linked Contacts.
		"""
		api = _read("api", "portal.py")
		self.assertIn("def _resolve_store", api, "the store is no longer resolved server-side")
		self.assertIn("store=None", api, "create_issue no longer accepts a store")
		self.assertIn("identity.stores(", api, "the caller's own stores are not consulted")

		self.assertIn(
			"def stores(", _read("portal", "identity.py"), "there is no way to list a user's stores"
		)

		report = _read("www", "start", "report-issue", "index.html")
		self.assertIn("store_count > 1", report, "the report form never asks which store")
		self.assertIn('id="sl-store"', report, "the store choice is not submitted with the report")

	def test_api_handles_both_priority_shapes(self):
		"""`Issue.priority` is a Select upstream and a Link to `Issue Priority`
		downstream (confirmed on ERPNext 15.121).

		Reading `get_options()` as an option list works for the Select and silently
		yields the literal priority "Issue Priority" for the Link, which fails the
		insert with `LinkValidationError`. Invisible until somebody files a real
		report, so it is pinned here.
		"""
		source = _read("api", "portal.py")
		self.assertIn(
			"fieldtype",
			source,
			"the priority field's declared type is no longer consulted",
		)
		self.assertIn(
			'"Link"',
			source,
			"the Link form of Issue.priority is not handled - a report would not save",
		)


if __name__ == "__main__":
	unittest.main()
