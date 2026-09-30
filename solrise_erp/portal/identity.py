# Copyright (c) 2026, Solrise and contributors

"""Which station the logged-in portal user speaks for.

A portal user reaches their ``Customer`` record in more than one way and
different sites settle on different ones, so all of these are tried in order:

1. ``Contact.user`` -> the Contact's ``Customer`` Dynamic Link. This is the
   ERPNext standard: the employee is a Contact under the station's Customer.
2. ``Contact.user`` / ``Contact Email`` -> ``Customer.email_id``, for a site
   where the Customer record itself carries the person's e-mail.
3. A ``User Permission`` for Customer, which is how a user responsible for
   several stations is scoped.

Nothing here raises, and **nothing here denies a report**: a user whose Customer
cannot be resolved may still report a problem. The Issue is then created without
a ``customer``, which is visible in the Desk and fixable, whereas refusing to
take a report from a broken fuel pump is not.
"""

import frappe

LOADED = "loaded"


def _log_error(title):
	"""Write to Error Log without letting the reporting itself raise."""
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def current_user():
	"""The session user, or ``None`` for a Guest."""
	try:
		user = frappe.session.user
	except Exception:
		return None
	if not user or user == "Guest":
		return None
	return user


def _contact_for(user):
	"""The Contact that represents this user, or ``None``."""
	try:
		contact = frappe.db.get_value("Contact", {"user": user}, "name")
	except Exception:
		contact = None
	if contact:
		return contact
	try:
		# A site where the Contact exists but was never linked to the User.
		return frappe.db.get_value("Contact Email", {"email_id": user}, "parent")
	except Exception:
		return None


def _customer_for_contact(contact):
	"""The Customer a Contact is linked to, or ``None``."""
	try:
		return frappe.db.get_value(
			"Dynamic Link",
			{"parenttype": "Contact", "parent": contact, "link_doctype": "Customer"},
			"link_name",
		)
	except Exception:
		return None


def _customer_by_email(user, contact=None):
	"""A Customer whose own e-mail is the user's, or ``None``."""
	try:
		customer = frappe.db.get_value("Customer", {"email_id": user}, "name")
	except Exception:
		customer = None
	if customer:
		return customer
	if not contact:
		return None
	try:
		email = frappe.db.get_value("Contact", contact, "email_id")
	except Exception:
		email = None
	if not email or email.lower() == user.lower():
		return None
	try:
		return frappe.db.get_value("Customer", {"email_id": email}, "name")
	except Exception:
		return None


def _customer_by_user_permission(user):
	"""The Customer from the user's own User Permission, or ``None``."""
	try:
		permissions = frappe.defaults.get_user_permissions(user) or {}
	except Exception:
		return None
	for entry in permissions.get("Customer") or []:
		if isinstance(entry, dict):
			value = entry.get("doc") or entry.get("value")
		else:
			value = entry
		if value:
			return str(value)
	return None


def resolve(user=None):
	"""Return ``{"customer", "customer_name", "contact"}``, or ``None``.

	``customer`` is ``None`` when the record could not be resolved but a Contact
	was; callers pass the dict's ``customer`` straight onto the Issue, which
	accepts an empty value.
	"""
	user = user or current_user()
	if not user:
		return None

	try:
		contact = _contact_for(user)
		customer = None
		if contact:
			customer = _customer_for_contact(contact)
		if not customer:
			customer = _customer_by_email(user, contact)
		if not customer:
			customer = _customer_by_user_permission(user)

		customer_name = None
		if customer:
			try:
				customer_name = frappe.db.get_value("Customer", customer, "customer_name")
			except Exception:
				customer_name = None

		if not (contact or customer):
			return None
		return {
			"customer": customer,
			"customer_name": customer_name or customer,
			"contact": contact,
		}
	except Exception:
		_log_error("Solrise portal: resolve identity")
		return None


def stores(user=None):
	"""Every store this user speaks for, deduplicated, ordered by contact name.

	At least five managers in the source list run more than one site (docs/18
	section 2), so the portal must never assume one store per login. The report form
	asks which store when this returns more than one entry, and `create_issue`
	accepts a store only from this list.
	"""
	user = user or current_user()
	if not user:
		return []
	try:
		contacts = frappe.get_all(
			"Contact", filters={"user": user}, fields=["name"], order_by="name"
		)
	except Exception:
		_log_error("Solrise portal: stores")
		return []

	found = []
	seen = set()
	for row in contacts:
		customer = _customer_for_contact(row.name)
		if not customer or customer in seen:
			continue
		seen.add(customer)
		name = None
		try:
			name = frappe.db.get_value("Customer", customer, "customer_name")
		except Exception:
			name = None
		found.append(
			{"customer": customer, "customer_name": name or customer, "contact": row.name}
		)
	return found


def display_name(user=None):
	"""The user's full name, falling back to the part before the ``@``."""
	user = user or current_user()
	if not user:
		return ""
	try:
		full_name = frappe.utils.get_fullname(user)
	except Exception:
		full_name = None
	if full_name:
		return full_name
	return str(user).split("@")[0]


def station_label(resolved=None):
	"""A human label for the resolved station, or ``""`` when unknown.

	Shown to the user to confirm *where* the report is coming from, which is the
	one thing a picture-free button page cannot otherwise convey.
	"""
	resolved = resolved if resolved is not None else resolve()
	if not resolved:
		return ""
	name = resolved.get("customer_name") or resolved.get("customer")
	return str(name) if name else ""
