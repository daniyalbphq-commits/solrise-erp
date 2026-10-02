# Copyright (c) 2026, Solrise and contributors

"""Whitelisted endpoints for the customer portal.

Both refuse Guests, and ``create_issue()`` is the only write path a customer has.
The Issue is assembled server-side from four inputs, so the form cannot set
``status``, ``customer``, the assignment, the Service Level fields or anything
else the support team depends on. A portal user gets to say *what* and *how
urgent*, and nothing more.

This is the one place that writes, which is also why the permission it needs -
the standard ``Customer`` role on ``Issue`` - is granted in ``install.py`` rather
than assumed. ERPNext ships no such grant (see ``docs/17``).
"""

import base64
import binascii

import frappe
from frappe import _

from solrise_erp.portal import catalog, identity
from solrise_erp.portal.guard import PORTAL_HOME

ISSUE = "Issue"

#: Free text the customer may type, and the subject built from the category.
DESCRIPTION_LIMIT = 2000
SUBJECT_LIMIT = 140

#: The "Customer" picker value: a person name, chosen from the store manager
#: list. Long enough for a full name, short enough that it cannot be used to
#: stuff the record line.
REPORTER_LIMIT = 80

#: A downscaled phone photo is a few hundred KB. This is the post-decode ceiling,
#: so a client that skips the resize still cannot post an unbounded body.
PHOTO_BYTES_LIMIT = 6 * 1024 * 1024

#: Only these are accepted, and the extension is derived from the declared type
#: rather than from anything the client names.
PHOTO_TYPES = {
	"image/jpeg": "jpg",
	"image/jpg": "jpg",
	"image/png": "png",
	"image/webp": "webp",
}


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def _session_user():
	"""The logged-in user, or a permission error for a Guest."""
	user = identity.current_user()
	if not user:
		frappe.throw(_("Please log in again to send this report."), frappe.PermissionError)
	return user


def _text(value, limit):
	value = (value or "")
	if not isinstance(value, str):
		value = str(value)
	value = value.strip()
	if len(value) > limit:
		value = value[:limit]
	return value


def _exists(doctype, name):
	"""True when `name` is a record of `doctype`; False on any failure."""
	try:
		return bool(frappe.db.exists(doctype, name))
	except Exception:
		return False


def _safe_priority(value):
	"""`value` when the Issue DocType accepts it, else Medium, else nothing.

	`Issue.priority` is a **Select** on older ERPNext and a **Link to
	`Issue Priority`** on newer ones. This asks the field rather than assuming,
	because that difference is a trap: `get_options()` returns the option list
	for a Select but the *target DocType* for a Link, and reading the latter as a
	list sets the priority to the literal string "Issue Priority" and fails the
	insert with `LinkValidationError`.

	Returns `None` for "set nothing, let the DocType default apply", which is the
	only safe answer on a site that has no `Issue Priority` records at all.
	"""
	try:
		field = frappe.get_meta(ISSUE).get_field("priority")
	except Exception:
		field = None
	if not field:
		return None

	fallback = "Medium"
	if (field.fieldtype or "") == "Link":
		target = field.options
		if value and target and _exists(target, value):
			return value
		if target and _exists(target, fallback):
			return fallback
		return None

	options = [option.strip() for option in (field.options or "").split("\n") if option.strip()]
	if value and value in options:
		return value
	if not options or fallback in options:
		return fallback
	return options[0]


def _subject(category_label, station, person):
	"""The single line the support team reads in the list view."""
	where = station or person
	return _text("{0} - {1}".format(category_label, where), SUBJECT_LIMIT)


def _description_html(category_label, urgency_label, note, reporter=""):
	"""The Issue body: the customer's own words, then the form's own record.

	The note is HTML-escaped before it is stored: Issue.description is a Text
	Editor field that the Desk renders as HTML, so unescaped input would be a
	stored-XSS path from the portal into a manager's browser.

	The record line carries who reported it as well as what and how urgent, and it
	is a small paragraph of its own so portal.reports can drop it when showing the
	customer what they wrote - otherwise every detail page quotes the form's
	bookkeeping back at them.
	"""
	parts = []
	note = _text(note, DESCRIPTION_LIMIT)
	if note:
		parts.append("<p>{0}</p>".format(frappe.utils.escape_html(note).replace(chr(10), "<br>")))

	record = _("Reported from the customer portal - {0} / {1}").format(category_label, urgency_label)
	reporter = _text(reporter, REPORTER_LIMIT)
	if reporter:
		record = "{0}: {1} - {2}".format(_("Customer"), reporter, record)
	parts.append("<p><small>{0}</small></p>".format(frappe.utils.escape_html(record)))
	return "".join(parts)

def _resolve_store(user, store=None):
	"""The (customer, name) a report belongs to, from the user's own links.

	One store needs no input; several require a choice, and a choice that is not
	among the caller's stores is rejected rather than honoured - otherwise anybody
	could file a report against any station by hand-crafting the request.
	"""
	allowed = identity.stores(user)
	if len(allowed) == 1:
		return allowed[0]["customer"], allowed[0]["customer_name"]
	if len(allowed) > 1:
		for candidate in allowed:
			if candidate["customer"] == store:
				return candidate["customer"], candidate["customer_name"]
		frappe.throw(_("Please choose which store this is about."), frappe.ValidationError)

	# No linked store: fall back to whatever identity can infer, and file anyway.
	resolved = identity.resolve(user)
	return (resolved or {}).get("customer"), identity.station_label(resolved)


def _attach_photo(data_url, issue):
	"""Store a data-URL photo against `issue`; return the file_url, or `None`.

	Every failure returns `None` on purpose: the report itself is worth more than
	its photograph, so a photo that is too big, in the wrong format or blocked by
	permissions must never cost the customer their submission.
	"""
	if not data_url or not isinstance(data_url, str):
		return None

	header, separator, payload = data_url.partition(",")
	if not separator or "base64" not in header.lower():
		return None

	mimetype = header[5:].split(";", 1)[0].strip().lower() if header.lower().startswith("data:") else ""
	extension = PHOTO_TYPES.get(mimetype)
	if not extension:
		return None

	try:
		content = base64.b64decode(payload, validate=False)
	except (binascii.Error, ValueError):
		return None
	if not content or len(content) > PHOTO_BYTES_LIMIT:
		return None

	try:
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "{0}-photo.{1}".format(issue.name, extension),
				"attached_to_doctype": ISSUE,
				"attached_to_name": issue.name,
				"is_private": 1,
				"content": content,
			}
		)
		file_doc.insert()
		return file_doc.file_url
	except Exception:
		_log_error("Solrise portal: attach photo")
		return None


@frappe.whitelist()
def create_issue(category=None, urgency=None, description=None, attachment=None, store=None, reporter=None):
	"""Create an Issue from the report form.

	Returns the new ticket's name, subject, priority and resolved category so the
	page can confirm what was filed without a second round trip.

	`store` is only consulted when the caller is linked to more than one store, and
	even then it must name one of *their* stores - the form is never trusted to say
	which station a report belongs to.

	`reporter` is who actually filed it, chosen from that store's own manager list:
	a station phone is shared, so it is not always the account holder. Both
	`description` and `reporter` are required - the form blocks first, and this is
	the second gate. See docs/17 section 14.
	"""
	# Both are required, and the form enforces it before posting (docs/17
	# section 14); checked here too because a phone is not the only client.
	note = _text(description, DESCRIPTION_LIMIT)
	if not note.strip():
		frappe.throw(_("Please say what is wrong."), frappe.ValidationError)
	reporter = _text(reporter, REPORTER_LIMIT)
	if not reporter.strip():
		frappe.throw(_("Please choose your name."), frappe.ValidationError)
	user = _session_user()

	chosen_category = catalog.category(category) or catalog.default_category()
	chosen_urgency = catalog.urgency(urgency) or catalog.default_urgency()

	customer, station = _resolve_store(user, store)
	person = identity.display_name(user)

	meta = frappe.get_meta(ISSUE)
	doc = frappe.new_doc(ISSUE)
	doc.subject = _subject(chosen_category["label"], station, person)
	doc.description = _description_html(chosen_category["label"], chosen_urgency["label"], note, reporter)
	# `_safe_priority` answers None when the field's values are not available on
	# this site, which is not the same as "set it to nothing".
	priority = _safe_priority(chosen_urgency.get("priority"))
	if priority and meta.has_field("priority"):
		doc.priority = priority
	if meta.has_field("raised_by"):
		doc.raised_by = user
	if meta.has_field("via_customer_portal"):
		doc.via_customer_portal = 1
	if customer and meta.has_field("customer"):
		doc.customer = customer

	# Inserted as the session user - no `ignore_permissions` - so the Customer
	# DocPerm granted in install.py is what decides, and the record's owner is
	# the customer who reported it.
	doc.insert()

	return {
		"name": doc.name,
		"subject": doc.subject,
		"priority": doc.get("priority"),
		"category": chosen_category["label"],
		"urgency": chosen_urgency["label"],
		"reporter": reporter,
		"customer": customer,
		"store": customer,
		"photo": _attach_photo(attachment, doc),
	}


@frappe.whitelist()
def my_issues(limit=20):
	"""The caller's own reports, newest first.

	The explicit ``owner`` filter is the same rule the Customer DocPerm applies
	(`if_owner`), stated twice on purpose: this must not leak another station's
	tickets if the permission row is ever edited away.
	"""
	user = _session_user()
	try:
		limit = max(1, min(int(limit or 20), 50))
	except (TypeError, ValueError):
		limit = 20

	try:
		return frappe.get_list(
			ISSUE,
			filters={"owner": user},
			fields=["name", "subject", "status", "priority", "creation"],
			order_by="creation desc",
			limit_page_length=limit,
		)
	except Exception:
		_log_error("Solrise portal: my_issues")
		return []


@frappe.whitelist(allow_guest=True)
def portal_home():
	"""Where this session's user belongs, if they are a portal customer.

	Exists for one situation: a station login that wanders onto a Desk URL.
	Frappe refuses the whole ``/app`` tree to a `Website User` with a "Not
	Permitted" page, which is the right answer and a dead end - the customer has
	nowhere to go and no way to read why. The unauthenticated door is worse: a
	Guest bounced from ``/app/issue`` is sent to
	``/login?redirect-to=/app/issue``, and after a successful login that lands them
	back on the refusal (docs/17 section 12).

	The portal's scripts run on that page too (they are declared in
	``web_include_js``), so they ask this and move on. Deliberately returns the
	route rather than redirecting: the refusal itself is Frappe's, stays a 403, and
	is not something this app should be able to weaken.

	Guests and desk users get ``None`` - an empty answer, never an error, so a
	hook that cannot decide leaves the page exactly as Frappe rendered it.
	"""
	try:
		user = identity.current_user()
		if not user:
			return {"home": None}
		if not identity.resolve(user=user):
			return {"home": None}
		return {"home": PORTAL_HOME, "name": identity.display_name(user)}
	except Exception:
		_log_error("Solrise portal: portal_home")
		return {"home": None}
