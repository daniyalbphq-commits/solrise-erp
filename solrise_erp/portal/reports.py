# Copyright (c) 2026, Solrise and contributors

"""What the customer sees about their own reports: the list, and the updates.

The portal's second half (docs/17 section 13). A station manager could file a
report and then never learn anything about it: the support team's reply is an
e-mail, and these logins have no deliverable address (``@stores.invalid``,
docs/18 section 5), so the portal is the *only* channel that can answer "is
anyone coming?".

Two rules shape everything here:

* **Only your own.** Every query is scoped by the caller's own Issue names -
  fetched with an explicit ``owner`` filter and then re-checked - rather than by
  trusting permissions to hold. Same reasoning as ``api.portal.my_issues``, which
  states the rule twice on purpose.
* **Only the ones still open.** `rows()` excludes `FINISHED_STATUSES`, because
  this screen exists to answer "is anyone coming?". `detail()` does not, so a
  customer holding a link can still read a finished report and see how it was
  fixed - see the note on `SHOW_FINISHED`.
* **Never a traceback.** A customer's phone must not show a stack trace, so a
  failure to read one report costs that report and nothing else.

Updates come from `Communication`, not from the Issue timeline: a Communication
is a message between the two parties, while a Comment is an internal note the
support team writes to each other. Showing the first and not the second is the
whole privacy rule, and it is why this reads messages rather than the timeline.

Message bodies are reduced to plain text and addresses are dropped - only the
body, the direction and the time survive - so a reply that happened to CC a
vendor cannot leak that address onto a station phone.
"""

import frappe
from frappe.utils import pretty_date, strip_html_tags

from solrise_erp.portal import catalog

ISSUE = "Issue"
COMMUNICATION = "Communication"

#: Newest reports the home page shows. A station phone rarely has more; the
#: detail page is one tap away for the rest of the history.
LIST_LIMIT = 20

#: Statuses that mean the report is finished with. The list shows the caller's
#: reports that are **not** in here: a customer opens this screen to find out
#: whether anyone is coming, and a finished report has already answered that.
#:
#: A deny-list rather than a list of "open" statuses, deliberately. If an upstream
#: release adds a status, an allow-list would silently hide real reports - a
#: customer unable to see a report they filed - while this shows it under
#: `catalog.DEFAULT_STATE`, which understates rather than hides.
FINISHED_STATUSES = ("Resolved", "Closed")

#: Flip to also list finished reports. They leave the list on purpose, but a
#: customer who keeps a link can still open one: `detail()` does not apply this
#: filter, so a fix never becomes invisible to the person who reported it.
SHOW_FINISHED = False

#: Nothing a customer needs to read is this long, and an unbounded field is a
#: way for a mail loop to make the screen unusable.
MESSAGE_LIMIT = 1200

#: The kind of Communication worth showing. `Automated Message` rows are the
#: platform telling itself a ticket exists, and are pure noise here.
MESSAGE_TYPES = ("Communication",)

#: `sent_or_received` -> who it looks like to the customer.
SENDER_ME = "You"
SENDER_THEM = "Solrise"


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def _plain(text, limit=MESSAGE_LIMIT):
	"""HTML to one readable block of text, trimmed to `limit` characters."""
	try:
		text = strip_html_tags(str(text or ""))
	except Exception:
		text = str(text or "")
	text = " ".join(text.split())
	if len(text) > limit:
		text = text[: limit - 1].rstrip() + "\u2026"
	return text


def _age(value):
	"""`3 hours ago`, or an empty string when the value is unusable."""
	try:
		return pretty_date(value) or ""
	except Exception:
		return ""


def _state(status):
	"""The customer-facing state for an `Issue.status`."""
	try:
		return catalog.state_for_status(status)
	except Exception:
		return dict(catalog.state_for_status(None))


def _category(subject):
	"""The catalogue entry behind an Issue, from the subject the portal wrote.

	`api.portal._subject()` builds ``"<category label> - <station>"``, so the
	label is recoverable without a schema change. An Issue that did not come from
	the portal simply has no match, and gets no icon rather than the wrong one.
	"""
	try:
		label = str(subject or "").split(" - ", 1)[0].strip()
	except Exception:
		return None
	return catalog.category_by_label(label)


def _photo(issue_name):
	"""The photo the customer attached, if there is one."""
	try:
		return frappe.db.get_value(
			"File",
			{"attached_to_doctype": ISSUE, "attached_to_name": issue_name},
			"file_url",
		)
	except Exception:
		return None


def _messages(issue_name):
	"""The messages between this customer and the support team, oldest first."""
	try:
		rows = frappe.get_all(
			COMMUNICATION,
			filters={
				"reference_doctype": ISSUE,
				"reference_name": issue_name,
				"communication_type": ["in", list(MESSAGE_TYPES)],
			},
			fields=["sent_or_received", "content", "creation"],
			order_by="creation asc",
			limit_page_length=50,
			ignore_permissions=True,
		)
	except Exception:
		_log_error("Solrise portal: messages")
		return []

	messages = []
	for row in rows:
		# The first message of a report filed from the portal is the report
		# itself, which the detail page already shows in full - showing it twice
		# reads like someone repeated themselves.
		text = _plain(row.get("content"))
		if not text:
			continue
		messages.append(
			{
				"from": SENDER_ME if row.get("sent_or_received") == "Received" else SENDER_THEM,
				"mine": row.get("sent_or_received") == "Received",
				"text": text,
				"when": row.get("creation"),
				"age": _age(row.get("creation")),
			}
		)
	return messages


def row(issue, has_update=False, store_label=""):
	"""One dict per report, for the home page list."""
	state = _state(issue.get("status"))
	return {
		"name": issue.get("name"),
		"subject": issue.get("subject"),
		"store": issue.get("customer") or "",
		"store_label": store_label,
		"status": issue.get("status"),
		"state": state,
		"category": _category(issue.get("subject")),
		"when": issue.get("creation"),
		"age": _age(issue.get("creation")),
		# The list says *that* there is an update, not what it says: a station
		# phone shows a handful of rows, and the customer taps the one they want
		# to read. `has_update` is passed in - see `rows()` - so listing twenty
		# reports stays one query for the reports and one for the messages.
		"has_update": bool(has_update),
	}


def _store_names(customers):
	"""Customer name -> its display name, in one query."""
	wanted = [name for name in set(customers) if name]
	if not wanted:
		return {}
	try:
		found = frappe.get_all(
			"Customer",
			filters={"name": ["in", wanted]},
			fields=["name", "customer_name"],
			limit_page_length=0,
		)
	except Exception:
		return {}
	return {row.get("name"): row.get("customer_name") or row.get("name") for row in found}


def _names_with_messages(issue_names):
	"""Which of these reports have something **from Solrise**. One query, not one each.

	Only `Sent` counts. Every report carries a `Received` copy of what the
	customer typed - it is how the support team sees the report - so counting any
	message would put "There is an update" on every row and mean nothing.
	"""
	if not issue_names:
		return set()
	try:
		found = frappe.get_all(
			COMMUNICATION,
			filters={
				"reference_doctype": ISSUE,
				"reference_name": ["in", list(issue_names)],
				"communication_type": ["in", list(MESSAGE_TYPES)],
				"sent_or_received": "Sent",
			},
			fields=["reference_name"],
			distinct=True,
			limit_page_length=0,
			ignore_permissions=True,
		)
	except Exception:
		_log_error("Solrise portal: reports with messages")
		return set()
	return {row.get("reference_name") for row in found}


def rows(user=None, limit=LIST_LIMIT):
	"""The caller's own **open** reports, newest first. Empty list on any trouble.

	Two scopes, and both matter: the reports are the caller's own (`owner`), and
	they are the ones still open (`FINISHED_STATUSES` excluded). See the note on
	`SHOW_FINISHED` for the second one.
	"""
	from solrise_erp.portal import identity

	user = user or identity.current_user()
	if not user:
		return []

	# The rule, stated here as well as in the permission row.
	filters = {"owner": user}
	if not SHOW_FINISHED:
		filters["status"] = ["not in", list(FINISHED_STATUSES)]

	try:
		found = frappe.get_list(
			ISSUE,
			filters=filters,
			fields=["name", "subject", "status", "customer", "creation", "modified"],
			order_by="modified desc",
			limit_page_length=limit,
		)
	except Exception:
		_log_error("Solrise portal: report rows")
		return []

	with_messages = _names_with_messages([issue.get("name") for issue in found])
	store_names = _store_names([issue.get("customer") for issue in found])
	out = []
	for issue in found:
		try:
			out.append(
				row(
					issue,
					has_update=issue.get("name") in with_messages,
					store_label=store_names.get(issue.get("customer"), ""),
				)
			)
		except Exception:
			_log_error("Solrise portal: report row")
	return out


def detail(user=None, name=None):
	"""One report with its updates, or `None` when it is not the caller's.

	`None` and not an exception, for both "no such report" and "not yours": a
	customer who edits the URL learns nothing either way.
	"""
	from solrise_erp.portal import identity

	user = user or identity.current_user()
	name = str(name or "").strip()
	if not user or not name:
		return None

	try:
		issue = frappe.get_doc(ISSUE, name)
	except Exception:
		return None

	# The check that matters. `frappe.get_doc` does not enforce permissions, and
	# the DocPerm only covers reads that go through it.
	try:
		if (issue.get("owner") or "").lower() != user.lower():
			return None
	except Exception:
		return None

	state = _state(issue.get("status"))
	thread = _messages(name)

	# The first message on a report is the report itself: the platform copies the
	# customer's own words onto the ticket so the support team can see them. That
	# text is the note - it is what they actually typed, where `description` is the
	# portal's composed HTML - and leaving it in the thread would print it twice on
	# one screen and make "Updates" mean "everything".
	note = ""
	if thread and thread[0].get("mine"):
		note = thread[0].get("text") or ""
		thread = thread[1:]

	return {
		"name": issue.get("name"),
		"subject": issue.get("subject"),
		"status": issue.get("status"),
		"state": state,
		"category": _category(issue.get("subject")),
		"store": issue.get("customer") or "",
		"store_label": _store_label(user, issue.get("customer")),
		"priority": issue.get("priority") or "",
		"urgent": str(issue.get("priority") or "").lower() in ("high", "urgent"),
		"when": issue.get("creation"),
		"age": _age(issue.get("creation")),
		"updated": issue.get("modified"),
		"updated_age": _age(issue.get("modified")),
		"note": note or _plain(issue.get("description"), limit=2000),
		"resolution": _plain(issue.get("resolution_details"), limit=1000),
		"photo": _photo(name),
		"messages": thread,
		"has_update": bool(thread),
	}


def _store_label(user, customer):
	"""The store as the customer knows it, when there is more than one to tell

	apart. A single-store manager never needs the name repeated back; a
	four-store one does, because the row has to say *which* station.
	"""
	if not customer:
		return ""
	try:
		from solrise_erp.portal import identity

		if len(identity.stores(user=user)) <= 1:
			return ""
		return frappe.db.get_value("Customer", customer, "customer_name") or customer
	except Exception:
		return ""
