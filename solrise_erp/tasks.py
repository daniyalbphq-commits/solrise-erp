# Copyright (c) 2026, Solrise and contributors

"""Scheduled Solrise jobs (docs/06 section 4.7, docs/07 sections 7.1 and 7.6).

| Job | Schedule | What it does |
|-----|----------|--------------|
| `check_sla_breaches` | every 15 min (`*/15 * * * *`) | emails + Desk alerts for breached Issues |
| `escalate_stale_tickets` | hourly (`0 * * * *`) | nudges managers about idle Open Issues |
| `send_support_digest` | daily (`daily_long`) | open / unassigned Issue counts |
| `purge_old_logs` | daily at 03:00 (`0 3 * * *`) | deletes chat / audit / message logs past retention |

Everything here runs as Administrator from the scheduler, so `frappe.get_all` is
used deliberately: `frappe.get_list` would be filtered by the *calling* user's
permissions, which is not what a site-wide housekeeping job wants.

Every job:

* returns quietly when there is nothing to do,
* guards every query with `frappe.db.exists("DocType", ...)`, so a site missing
  one of the app DocTypes cannot break the scheduler,
* wraps its body in `try/except Exception` + `frappe.log_error`, so a failure is
  visible in Error Log instead of killing the queue,
* uses a Redis cooldown key where a *persistent* condition could otherwise email
  on every scan (`solrise_sla_notified:*`, `solrise_stale_notified:*`). The digest
  is bounded by its own daily cooldown, and the purge sends no mail at all, so
  neither can cause a mail storm.
"""

import frappe

ISSUE = "Issue"
SUPPORT_MANAGER_ROLE = "Support Manager"
SETTINGS_SINGLE = "Solrise Settings"

# Issues in these states are finished; everything else counts as open.
CLOSED_STATUSES = ("Closed", "Resolved")

STALE_AFTER_DAYS = 3
COOLDOWN_HOURS = 24
DIGEST_COOLDOWN_HOURS = 20

# A first run on a site with a backlog must not email the whole backlog at once;
# the next scan (15 min / 1 h later) picks up the rest.
ALERT_BATCH_SIZE = 25

# Fields read from an Issue, filtered against the installed version's meta.
ISSUE_CANDIDATE_FIELDS = (
	"name", "subject", "status", "priority", "owner", "_assign", "modified",
	"resolution_by", "agreement_status",
)

# (doctype, Solrise Settings retention field, default days)
LOG_RETENTION = (
	("Solrise Chat Log", "chat_log_retention_days", 90),
	("Solrise AI Audit Log", "audit_log_retention_days", 180),
	("Solrise Message Log", "message_log_retention_days", 180),
)


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def _cache():
	"""The Redis wrapper, whether `frappe.cache` is a callable or an object."""
	try:
		cache = getattr(frappe, "cache", None)
		if cache is None:
			return None
		return cache() if callable(cache) else cache
	except Exception:
		_log_error("Solrise task: cache")
		return None


def _acquire_cooldown(key, hours):
	"""`True` the first time `key` is claimed within `hours`, `False` after.

	Fails **closed**: if Redis cannot be reached the notification is skipped
	rather than repeated on every scan (the whole point of the key is to prevent a
	mail storm).
	"""
	try:
		cache = _cache()
		if cache is None:
			return False
		if cache.get_value(key):
			return False
		cache.set_value(key, 1, expires_in_sec=int(hours * 3600))
		return True
	except Exception:
		_log_error("Solrise task: cooldown {0}".format(key))
		return False


def _support_manager_recipients():
	"""Enabled Support Manager users, deduplicated."""
	try:
		recipients = frappe.get_all(
			"Has Role",
			filters={"role": SUPPORT_MANAGER_ROLE, "parenttype": "User"},
			pluck="parent",
		)
	except Exception:
		_log_error("Solrise task: Support Manager recipients")
		return []
	seen = set()
	users = []
	for user in recipients or []:
		if not user or user in seen:
			continue
		seen.add(user)
		users.append(user)
	return users


def _issue_fields():
	"""The Issue fields this module reads, minus the ones this version lacks."""
	try:
		meta = frappe.get_meta(ISSUE)
		fields = [field for field in ISSUE_CANDIDATE_FIELDS if meta.has_field(field)]
		return fields or ["name"]
	except Exception:
		_log_error("Solrise task: Issue meta")
		return ["name"]


def _text(value):
	"""A `None`-safe string for the email body."""
	return "" if value is None else str(value)


def _assignee_text(value):
	"""A readable assignee list for the email body."""
	raw = _text(value).strip()
	return raw if raw else "unassigned"


def _issue_email_body(issue, intro):
	"""A small HTML body: what happened, and the ticket's key facts."""
	rows = [
		("Ticket", issue.get("name")),
		("Subject", issue.get("subject")),
		("Status", issue.get("status")),
		("Priority", issue.get("priority")),
		("Owner", issue.get("owner")),
		("Assigned to", _assignee_text(issue.get("_assign"))),
		("SLA due", issue.get("resolution_by")),
		("Last modified", issue.get("modified")),
	]
	items = "".join(
		"<li><b>{0}:</b> {1}</li>".format(
			frappe.utils.escape_html(label), frappe.utils.escape_html(_text(value))
		)
		for label, value in rows
		if value
	)
	name = issue.get("name")
	link = frappe.utils.get_url_to_form(ISSUE, name) if name else ""
	return (
		"<p>{0}</p><ul>{1}</ul>".format(frappe.utils.escape_html(intro), items)
		+ ("<p><a href=\"{0}\">Open the ticket</a></p>".format(link) if link else "")
	)


def _notify_in_app(recipients, subject, message, issue_name):
	"""Desk notification for the same recipients; best effort (docs/07 section 7.1)."""
	try:
		if not frappe.db.exists("DocType", "Notification Log"):
			return
		for user in recipients:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"document_type": ISSUE,
					"document_name": issue_name,
					"subject": subject,
					"email_content": message,
				}
			).insert(ignore_permissions=True)
	except Exception:
		_log_error("Solrise task: in-app notification")


def _send_issue_alert(recipients, issue, subject, intro):
	"""Email the Support Manager role and, best effort, raise a Desk alert."""
	message = _issue_email_body(issue, intro)
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			reference_doctype=ISSUE,
			reference_name=issue.get("name"),
		)
	except Exception:
		_log_error("Solrise task: alert email {0}".format(issue.get("name")))
	_notify_in_app(recipients, subject, message, issue.get("name"))


def _issue_rows(filters):
	"""One page of Issue rows, oldest SLA deadline first."""
	try:
		return frappe.get_all(
			ISSUE,
			filters=filters,
			fields=_issue_fields(),
			order_by="resolution_by asc",
			limit_page_length=ALERT_BATCH_SIZE,
		)
	except Exception:
		_log_error("Solrise task: query Issue")
		return []


def _stamp_breached(issue):
	"""Mark the breach when the version carries the field (docs/06 section 4.7)."""
	try:
		frappe.db.set_value(
			ISSUE, issue.get("name"), "agreement_status", "Breached", update_modified=False
		)
		return True
	except Exception:
		_log_error("Solrise task: stamp agreement_status {0}".format(issue.get("name")))
		return False


def _collect_breached_issues(meta, open_filters):
	"""Open Issues whose SLA deadline has passed, newest breach last.

	Where the platform tracks `agreement_status` it stamps the breach itself, so
	those rows are used as-is; rows it never touched (an older or simpler site) are
	stamped here so the breach is visible either way.
	"""
	has_status_field = meta.has_field("agreement_status")
	base = dict(open_filters)
	base["resolution_by"] = ["<", frappe.utils.now()]

	collected = []
	if has_status_field:
		breached = dict(base)
		breached["agreement_status"] = "Breached"
		collected.extend(_issue_rows(breached))

	for row in _issue_rows(base):
		if has_status_field and row.get("agreement_status"):
			# The platform is tracking this one its own way; do not overwrite it.
			continue
		if has_status_field and _stamp_breached(row):
			row["agreement_status"] = "Breached"
		collected.append(row)

	seen = set()
	issues = []
	for row in collected:
		name = row.get("name")
		if not name or name in seen:
			continue
		seen.add(name)
		issues.append(row)
	return issues[:ALERT_BATCH_SIZE]


def check_sla_breaches():
	"""Every 15 minutes (`*/15 * * * *`): alert on breached SLAs.

	Notifies the Support Manager role once per Issue per 24 hours, and stamps
	`Issue.agreement_status` when this version has the field.
	"""
	try:
		if not frappe.db.exists("DocType", ISSUE):
			return
		meta = frappe.get_meta(ISSUE)
		if not meta.has_field("resolution_by"):
			# No SLA fields in this version: there is nothing to scan.
			return

		issues = _collect_breached_issues(meta, {"status": ["not in", CLOSED_STATUSES]})
		if not issues:
			return

		recipients = _support_manager_recipients()
		if not recipients:
			return

		notified = []
		for issue in issues:
			key = "solrise_sla_notified:{0}".format(issue.get("name"))
			if not _acquire_cooldown(key, COOLDOWN_HOURS):
				continue
			_send_issue_alert(
				recipients,
				issue,
				"[Solrise] SLA breached: {0}".format(issue.get("name")),
				"The service level agreement on this ticket has been breached.",
			)
			notified.append(issue.get("name"))
		if notified:
			frappe.db.commit()
			frappe.logger().info("tasks.check_sla_breaches: notified %s", notified)
	except Exception:
		frappe.db.rollback()
		_log_error("Solrise task: check_sla_breaches")


def escalate_stale_tickets():
	"""Hourly (`0 * * * *`): nudge managers about Open Issues idle for 3+ days."""
	try:
		if not frappe.db.exists("DocType", ISSUE):
			return
		cutoff = frappe.utils.add_to_date(frappe.utils.now(), days=-STALE_AFTER_DAYS)
		issues = frappe.get_all(
			ISSUE,
			filters={"status": ["not in", CLOSED_STATUSES], "modified": ["<", cutoff]},
			fields=_issue_fields(),
			order_by="modified asc",
			limit_page_length=ALERT_BATCH_SIZE,
		)
		if not issues:
			return

		recipients = _support_manager_recipients()
		if not recipients:
			return

		notified = []
		for issue in issues:
			key = "solrise_stale_notified:{0}".format(issue.get("name"))
			if not _acquire_cooldown(key, COOLDOWN_HOURS):
				continue
			_send_issue_alert(
				recipients,
				issue,
				"[Solrise] Ticket idle for {0} days: {1}".format(
					STALE_AFTER_DAYS, issue.get("name")
				),
				"This open ticket has not been touched for {0} days.".format(
					STALE_AFTER_DAYS
				),
			)
			notified.append(issue.get("name"))
		if notified:
			frappe.db.commit()
			frappe.logger().info("tasks.escalate_stale_tickets: escalated %s", notified)
	except Exception:
		frappe.db.rollback()
		_log_error("Solrise task: escalate_stale_tickets")


def _open_issue_count():
	"""Issues that are not Resolved/Closed."""
	return frappe.db.count(ISSUE, {"status": ["not in", CLOSED_STATUSES]}) or 0


def _unassigned_open_issue_count():
	"""Open Issues with nobody in `_assign` (NULL, "" or "[]")."""
	result = frappe.db.sql(
		"select count(`name`) from `tabIssue` "
		"where `status` not in ('Closed', 'Resolved') "
		"and ifnull(`_assign`, '') in ('', '[]')"
	)
	return (result[0][0] or 0) if result else 0


def send_support_digest():
	"""Daily (`daily_long`): open / unassigned Issue counts to the managers."""
	try:
		if not frappe.db.exists("DocType", ISSUE):
			return
		open_count = _open_issue_count()
		unassigned_count = _unassigned_open_issue_count()
		if not open_count and not unassigned_count:
			return

		recipients = _support_manager_recipients()
		if not recipients:
			return
		# A daily job that keeps finding open tickets is exactly the persistent
		# condition a cooldown key is for: it also absorbs manual re-runs.
		if not _acquire_cooldown("solrise_digest_sent", DIGEST_COOLDOWN_HOURS):
			return

		try:
			frappe.sendmail(
				recipients=recipients,
				subject="[Solrise] Support digest: {0} open, {1} unassigned".format(
					open_count, unassigned_count
				),
				message=(
					"<p>Solrise support digest for {0}.</p>"
					"<ul><li><b>Open tickets:</b> {1}</li>"
					"<li><b>Open and unassigned:</b> {2}</li></ul>"
				).format(frappe.utils.nowdate(), open_count, unassigned_count),
			)
		except Exception:
			_log_error("Solrise task: support digest email")
		frappe.logger().info(
			"tasks.send_support_digest: %s open, %s unassigned", open_count, unassigned_count
		)
	except Exception:
		frappe.db.rollback()
		_log_error("Solrise task: send_support_digest")


def _retention_days(fieldname, default_days):
	"""The configured window, falling back to the documented default."""
	try:
		meta = frappe.get_meta(SETTINGS_SINGLE)
		if not meta.has_field(fieldname):
			return default_days
		value = frappe.db.get_single_value(SETTINGS_SINGLE, fieldname)
		days = int(value) if value not in (None, "") else default_days
		return days if days > 0 else default_days
	except Exception:
		return default_days


def purge_old_logs():
	"""Daily at 03:00 (`0 3 * * *`): delete log rows past their retention.

	Returns a dict of `{doctype: rows deleted}`. Runs only when
	`Solrise Settings.enable_log_purge` is on (docs/07 section 7.6).
	"""
	counts = {}
	try:
		if not frappe.db.exists("DocType", SETTINGS_SINGLE):
			return counts
		if not frappe.db.get_single_value(SETTINGS_SINGLE, "enable_log_purge"):
			frappe.logger().info("tasks.purge_old_logs: disabled in Solrise Settings")
			return counts

		for doctype, fieldname, default_days in LOG_RETENTION:
			if not frappe.db.exists("DocType", doctype):
				continue
			days = _retention_days(fieldname, default_days)
			cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -days)
			# `creation` is the framework timestamp every DocType carries, including
			# a log row written by a background job, so it works for all three logs.
			filters = {"creation": ["<", cutoff]}
			deleted = frappe.db.count(doctype, filters) or 0
			if deleted:
				frappe.db.delete(doctype, filters)
			counts[doctype] = deleted

		if any(counts.values()):
			frappe.db.commit()
		frappe.logger().info("tasks.purge_old_logs: deleted %s", counts)
	except Exception:
		frappe.db.rollback()
		_log_error("Solrise task: purge_old_logs")
	return counts
