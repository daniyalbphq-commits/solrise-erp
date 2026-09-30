# Copyright (c) 2026, Solrise and contributors

"""The buttons the customer portal renders. Adding a button is adding an entry.

The audience is a station employee who may not read comfortably, so every entry
carries an icon - the primary affordance - *and* a short label, which is the
confirmation rather than the instruction. Keep labels under 24 characters so
they still fit on two lines on a phone.

Icons are written as escapes so this file stays pure ASCII no matter what
encoding the checkout is read with.

This module deliberately imports nothing from Frappe: that is what lets
``tests/test_portal_catalog.py`` check the buttons - including that every route
has a page behind it - in CI, with no bench and no site.

Routes are absolute site paths. The portal has no Desk.
"""

# --- home page buttons -------------------------------------------------------
# `roles` is a tuple of roles allowed to see the tile. An empty tuple means
# "every logged-in user", which is all the single tile below needs. A tile's
# `route` must have a matching ``www/<route>/index.html`` in this app; the test
# enforces that, so a button can never point at a page nobody wrote.
TILES = (
	{
		"key": "report-issue",
		"label": "Report a problem",
		"caption": "Something is broken",
		# U+1F6E0 (hammer and wrench) + U+FE0F (render as emoji)
		"icon": "\U0001F6E0\uFE0F",
		"route": "/start/report-issue",
		"roles": (),
	},
)

# --- "what is wrong?" choices on the report form -----------------------------
# Transcribed from the maintenance category list ("Maintenance Category.docx"):
# the 20 entries of its proposed main dropdown, in its order, plus "Car Wash"
# from the detailed spec (section 21, absent from the source table) and a final
# escape hatch. Two table entries - "Gutters & Concrete" and "Intercom & Air
# Equipment" - were dropped by that document's own dropdown and are therefore
# not here either.
#
# Icons are the source document's, written as escapes to keep this file ASCII.
# Labels are its dropdown wording, which is the shorter of the two forms it uses;
# `tests/test_portal_catalog.py` holds them to a length a phone can render.
CATEGORIES = (
	{"key": "building", "label": "Building & Structure", "icon": "\U0001F3E2"},  # U+1F3E2 office
	{"key": "fire-safety", "label": "Fire & Safety", "icon": "\U0001F525"},  # U+1F525 fire
	{"key": "doors-glass", "label": "Doors & Glass", "icon": "\U0001F6AA"},  # U+1F6AA door
	{"key": "painting", "label": "Painting", "icon": "\U0001F3A8"},  # U+1F3A8 palette
	{"key": "hvac", "label": "HVAC", "icon": "\u2744\uFE0F"},  # U+2744 snowflake
	{"key": "refrigeration", "label": "Refrigeration", "icon": "\U0001F9CA"},  # U+1F9CA ice
	{"key": "restrooms", "label": "Restrooms", "icon": "\U0001F6BB"},  # U+1F6BB restroom
	{"key": "plumbing", "label": "Plumbing", "icon": "\U0001F4A7"},  # U+1F4A7 droplet
	{"key": "drainage", "label": "Drainage & Sewer", "icon": "\U0001F573\uFE0F"},  # U+1F573 hole
	{"key": "electrical", "label": "Electrical", "icon": "\u26A1"},  # U+26A1 high voltage
	{"key": "electronic", "label": "Electronic Equipment", "icon": "\U0001F4B3"},  # U+1F4B3 card
	{"key": "store-equipment", "label": "Store Equipment", "icon": "\U0001F6E0\uFE0F"},  # U+1F6E0 tools
	{"key": "security", "label": "Security", "icon": "\U0001F510"},  # U+1F510 locked
	{"key": "signage", "label": "Signage & Lighting", "icon": "\U0001F4A1"},  # U+1F4A1 bulb
	{"key": "pumps", "label": "Pumps & Fuel", "icon": "\u26FD"},  # U+26FD fuel pump
	{"key": "fuel-storage", "label": "Oil/Fuel Storage", "icon": "\U0001F6E2\uFE0F"},  # U+1F6E2 barrel
	{"key": "parking", "label": "Parking & Driveways", "icon": "\U0001F697"},  # U+1F697 car
	{"key": "landscaping", "label": "Landscaping", "icon": "\U0001F333"},  # U+1F333 tree
	{"key": "irrigation", "label": "Irrigation", "icon": "\U0001F4A6"},  # U+1F4A6 spray
	{"key": "fencing", "label": "Fencing & Exterior", "icon": "\U0001F6A7"},  # U+1F6A7 barrier
	{"key": "car-wash", "label": "Car Wash", "icon": "\U0001F9FC"},  # U+1F9FC soap
	{"key": "other", "label": "Something else", "icon": "\u2757"},  # U+2757 exclamation
)

#: The category chosen when the request omits one, and the one pre-selected in
#: the form.
DEFAULT_CATEGORY = "other"

# --- "is it urgent?" choices -------------------------------------------------
# `priority` is the Issue `priority` value the choice maps to. The API re-checks
# it against the DocType's own options, so a future upstream rename degrades to
# the default instead of failing the submission.
URGENCIES = (
	{
		"key": "normal",
		"label": "Normal",
		"help": "It can wait",
		"icon": "\U0001F44C",  # U+1F44C OK hand
		"priority": "Medium",
	},
	{
		"key": "urgent",
		"label": "Urgent",
		"help": "Unsafe or closed",
		"icon": "\U0001F6A8",  # U+1F6A8 police light
		"priority": "High",
	},
)

#: The urgency chosen when the request omits one. Deliberately the calm one:
#: guessing "urgent" would train the support team to ignore the flag.
DEFAULT_URGENCY = "normal"


# --- lookups -----------------------------------------------------------------
def _by_key(entries, key):
	"""The entry whose `key` matches, or `None`. `key` may be None."""
	if key is None:
		return None
	wanted = str(key).strip()
	if not wanted:
		return None
	for entry in entries:
		if entry["key"] == wanted:
			return dict(entry)
	return None


def visible_tiles(roles=()):
	"""The tiles this role set may see, in catalogue order.

	`roles` is anything iterable of role names; ``None`` and ``"Guest"`` produce
	no role-matched tiles, which is the safe direction - a tile restricted to a
	role disappears rather than leaking.
	"""
	held = {str(role) for role in (roles or ()) if str(role or "").strip()}
	tiles = []
	for tile in TILES:
		allowed = tile.get("roles") or ()
		if allowed and not held.intersection(set(allowed)):
			continue
		tiles.append(dict(tile))
	return tiles


def tile(key):
	return _by_key(TILES, key)


def category(key):
	"""The category for `key`, or `None` when it is not one we offer."""
	return _by_key(CATEGORIES, key)


def default_category():
	return dict(_by_key(CATEGORIES, DEFAULT_CATEGORY) or CATEGORIES[-1])


def urgency(key):
	return _by_key(URGENCIES, key)


def default_urgency():
	return dict(_by_key(URGENCIES, DEFAULT_URGENCY) or URGENCIES[0])
