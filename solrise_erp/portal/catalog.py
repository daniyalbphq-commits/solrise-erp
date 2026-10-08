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
# One tile per *department*: Maintenance, HR and IT / Support. Each opens the same
# report form with that department's buttons, so the customer picks the desk
# first and the problem second. `TILES` is derived from `DEPARTMENTS` further
# down, because it needs the category sets first. `roles` is a tuple of roles
# allowed to see a tile; an empty tuple means every logged-in user.

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

# --- HR and IT / Support choices ---------------------------------------------
# The maintenance list above is transcribed from the client's document. These two
# are the same shape - an icon first, a short label second - and are the starting
# sets for the other two desks. They are content, not code: rename, reorder and
# add freely. Keep keys *and* labels unique across every department, because
# `category_by_label` recovers the button's icon from an Issue subject and
# `reports.py` reads the label before the first " - ".
HR_CATEGORIES = (
	{"key": "paycheck", "label": "Paycheck Problem", "icon": "\U0001F4B5"},  # U+1F4B5 dollar
	{"key": "leave", "label": "Leave & Time Off", "icon": "\U0001F5D3\uFE0F"},  # U+1F5D3 calendar
	{"key": "attendance", "label": "Attendance", "icon": "\u23F0"},  # U+23F0 alarm clock
	{"key": "benefits", "label": "Benefits & Insurance", "icon": "\U0001F6E1\uFE0F"},  # U+1F6E1 shield
	{"key": "onboarding", "label": "New Hire Setup", "icon": "\U0001F9D1"},  # U+1F9D1 person
	{"key": "employee-info", "label": "My Info is Wrong", "icon": "\U0001F4C7"},  # U+1F4C7 card index
	{"key": "conduct", "label": "Manager or Conduct", "icon": "\u2696\uFE0F"},  # U+2696 scales
	{"key": "hr-other", "label": "Something else (HR)", "icon": "\u2757"},  # U+2757 exclamation
)

IT_CATEGORIES = (
	{"key": "login-access", "label": "Login & Access", "icon": "\U0001F511"},  # U+1F511 key
	{"key": "register-pos", "label": "Register / POS", "icon": "\U0001F9FE"},  # U+1F9FE receipt
	{"key": "printer", "label": "Printer", "icon": "\U0001F5A8\uFE0F"},  # U+1F5A8 printer
	{"key": "network", "label": "Internet & Wi-Fi", "icon": "\U0001F4F6"},  # U+1F4F6 antenna
	{"key": "computer", "label": "Computer", "icon": "\U0001F4BB"},  # U+1F4BB laptop
	{"key": "phone", "label": "Phone & Radio", "icon": "\U0001F4DE"},  # U+1F4DE telephone
	{"key": "email", "label": "Email", "icon": "\u2709\uFE0F"},  # U+2709 envelope
	{"key": "software", "label": "Software & Apps", "icon": "\U0001F9E9"},  # U+1F9E9 puzzle
	{"key": "it-other", "label": "Something else (IT)", "icon": "\u2757"},  # U+2757 exclamation
)

# --- departments -------------------------------------------------------------
# The desk a report goes to. `assignees` are the people the portal assigns the
# Issue to and emails; the form's "Customer" picker is still the store's own
# managers. `default_category` is the escape hatch pre-selected on the form.
DEPARTMENTS = (
	{
		"key": "maintenance",
		"label": "Maintenance",
		"caption": "Something is broken",
		# U+1F6E0 (hammer and wrench) + U+FE0F (render as emoji)
		"icon": "\U0001F6E0\uFE0F",
		"categories": CATEGORIES,
		"default_category": DEFAULT_CATEGORY,
		"assignees": ("umair.nawaz@solrisestores.com",),
	},
	{
		"key": "hr",
		"label": "HR",
		"caption": "Pay, leave, people",
		"icon": "\U0001F465",  # U+1F465 busts in silhouette
		"categories": HR_CATEGORIES,
		"default_category": "hr-other",
		"assignees": ("daniyal@solrise.com",),
	},
	{
		"key": "it",
		"label": "IT / Support",
		"caption": "Computer, printer, network",
		"icon": "\U0001F4BB",  # U+1F4BB laptop
		"categories": IT_CATEGORIES,
		"default_category": "it-other",
		"assignees": ("umair.nawaz@solrisestores.com",),
	},
)

#: The desk a request goes to when it does not name one. Deliberately the one the
#: portal shipped with, so an older client keeps working.
DEFAULT_DEPARTMENT = "maintenance"

#: Every button on every desk - what `category()` and `category_by_label()` search.
ALL_CATEGORIES = CATEGORIES + HR_CATEGORIES + IT_CATEGORIES

#: The one page every desk tile opens; the desk rides along as `?department=`.
REPORT_ROUTE = "/start/report-issue"

# --- home page tiles (derived from DEPARTMENTS) ------------------------------
# A tile's `route` must have a matching ``www/<route>/index.html`` in this app; the
# test enforces that, so a button can never point at a page nobody wrote.
TILES = tuple(
	{
		"key": department["key"],
		"label": department["label"],
		"caption": department["caption"],
		"icon": department["icon"],
		"route": REPORT_ROUTE,
		"department": department["key"],
		"roles": (),
	}
	for department in DEPARTMENTS
)

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


# --- "where is my report?" ---------------------------------------------------
# The customer's vocabulary, not the ERP's. `Issue.status` is `Open`, `Replied`,
# `On Hold`, `Resolved`, `Closed` - five words about a *workflow*, shown to
# someone who may not read comfortably and who only ever wants to know one
# thing: is anyone coming?
#
# So the states are four, they are carried by an icon and a colour first and a
# word second, and `sentence` is what the detail page says out loud. `tone` names
# a colour in the stylesheet (`.sl-state--<tone>`), which keeps the palette in the
# CSS rather than in Python.
#
# `Statuses` maps the ERP's answers onto them. Every value of the DocType's
# `status` field must appear here: an unmapped status falls back to `waiting`,
# which understates progress rather than inventing it.
STATES = (
	{
		"key": "waiting",
		"label": "Waiting",
		"sentence": "We have your report",
		"help": "Nobody has started on it yet",
		"icon": "\u23F3",  # U+23F3 hourglass
		"tone": "calm",
	},
	{
		"key": "replied",
		"label": "In progress",
		"sentence": "We replied to you",
		"help": "Read the update below",
		"icon": "\U0001F4AC",  # U+1F4AC speech balloon
		"tone": "busy",
	},
	{
		"key": "held",
		"label": "On hold",
		"sentence": "Waiting on something",
		"help": "A part or a person - we have not forgotten",
		"icon": "\u23F8\uFE0F",  # U+23F8 pause
		"tone": "held",
	},
	{
		"key": "done",
		"label": "Fixed",
		"sentence": "This one is done",
		"help": "Report it again if it comes back",
		"icon": "\u2705",  # U+2705 check mark button
		"tone": "good",
	},
)

#: `Issue.status` -> one of the keys above.
STATUS_TO_STATE = {
	"Open": "waiting",
	"Replied": "replied",
	"On Hold": "held",
	"Resolved": "done",
	"Closed": "done",
}

#: What an unrecognised status shows. See the note on `STATES`.
DEFAULT_STATE = "waiting"


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
	"""The category for `key`, or `None` when it is not one we offer.

	Searches every department's buttons, so a report from any desk resolves its
	icon.
	"""
	return _by_key(ALL_CATEGORIES, key)


def category_by_label(label):
	"""The category whose label is `label`, case-insensitively.

	The portal does not store which button was pressed - the label in the Issue
	subject is the only trace. Matching it back is what lets a report show the same
	icon the customer tapped; no match means no icon, never a guessed one. Searches
	every department, and the labels are unique across all of them.
	"""
	wanted = str(label or "").strip().casefold()
	if not wanted:
		return None
	for entry in ALL_CATEGORIES:
		if str(entry["label"]).casefold() == wanted:
			return dict(entry)
	return None


def department(key):
	"""The desk for `key`, or `None` when it is not one we offer."""
	return _by_key(DEPARTMENTS, key)


def default_department():
	return dict(_by_key(DEPARTMENTS, DEFAULT_DEPARTMENT) or DEPARTMENTS[0])


def department_for_category(key):
	"""The desk a category belongs to, or `None`."""
	wanted = str(key or "").strip()
	if not wanted:
		return None
	for entry in DEPARTMENTS:
		if any(category["key"] == wanted for category in entry["categories"]):
			return dict(entry)
	return None


def categories_for(key):
	"""The choice buttons for a desk, in catalogue order.

	An unknown desk answers the default desk's buttons rather than nothing, so a
	stale link still renders a usable form.
	"""
	entry = department(key) or default_department()
	return [dict(category) for category in entry["categories"]]


def assignees_for(key):
	"""The people a report to this desk is assigned to and emailed."""
	entry = department(key) or default_department()
	return tuple(entry.get("assignees") or ())


def default_category():
	entry = default_department()
	return dict(_by_key(entry["categories"], entry["default_category"]) or entry["categories"][-1])


def default_category_for(key):
	"""The pre-selected button for a desk: its own escape hatch."""
	entry = department(key) or default_department()
	return dict(
		_by_key(entry["categories"], entry["default_category"]) or entry["categories"][-1]
	)


def urgency(key):
	return _by_key(URGENCIES, key)


def default_urgency():
	return dict(_by_key(URGENCIES, DEFAULT_URGENCY) or URGENCIES[0])


def state(key):
	"""The state for `key`, or `None` when it is not one we render."""
	return _by_key(STATES, key)


def state_for_status(status):
	"""The state to show for an `Issue.status` value.

	An unknown status - a future upstream addition, a hand-edited row - answers
	`waiting`: telling someone their report is *less* far along than it is costs a
	phone call, while telling them it is finished when it is not costs a breakdown
	at the pump.
	"""
	key = STATUS_TO_STATE.get(str(status or "").strip(), DEFAULT_STATE)
	return dict(_by_key(STATES, key) or STATES[0])
