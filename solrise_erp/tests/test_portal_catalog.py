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
		for relative, needle in (
			(("public", "css", "solrise_portal.css"), "/assets/solrise_erp/css/solrise_portal.css"),
			(("public", "js", "solrise_portal.js"), "/assets/solrise_erp/js/solrise_portal.js"),
		):
			self.assertTrue(os.path.isfile(os.path.join(_app_dir(), *relative)), os.path.join(*relative))
			self.assertIn(needle, hooks, "{0} is not declared in hooks.py".format(needle))

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
