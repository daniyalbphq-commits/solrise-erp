"""
Solrise ERP - point support email at one person, without taking anyone's access
away.

Support mail on this site comes from four places, and every one of them keys off
the `Support Manager` role or the `Solrise Support Routing` Assignment Rule:

* `Solrise New Ticket`        - notification, recipient = role
* `Solrise SLA Breach Alert`  - notification, recipient = role
* the hourly stale-ticket nudge and the daily digest (`tasks.py`) - role
* the Assignment Rule, which round-robins a new Issue across a named user list
  and emails whoever it picks

So one role is doing two jobs - permission and notification - and an
administrator who also works tickets receives every support mail. Permission
does not have to be dropped to fix that: `Support Team` and `Support Agent`
grant the same `Issue` permissions as `Support Manager` (read/write/create/
share - see `install.py` and the Custom DocPerm on `Issue`). This leaves the
`Support Manager` role to the one person who answers the mail, and the
Assignment Rule routed to that same person.

Idempotent, and it only ever touches the users named here:

    SUPPORT_ROUTING_USERS='umair.nawaz@solrisestores.com' \
    SUPPORT_ACCESS_ONLY_USERS='daniyalbphq@gmail.com,daniyal@solrise.com' \
        SITE_ENV=aws ./scripts/run-python.sh scripts/configure_support.py

Inputs come from the environment (defaults match the live site):
    SUPPORT_ROUTING_USERS      comma-separated; hold `Support Manager`, get the
                               mail, and are the Assignment Rule's users
    SUPPORT_ACCESS_ONLY_USERS  comma-separated; keep their other roles (so they
                               keep access) and lose `Support Manager`
    SUPPORT_MANAGER_ROLE       the notification role (default Support Manager)
    SUPPORT_RULE               the Assignment Rule (default Solrise Support
                               Routing; created only if it is missing)
"""
import os
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"

ROLE = os.environ.get("SUPPORT_MANAGER_ROLE", "Support Manager").strip() or "Support Manager"
RULE = os.environ.get("SUPPORT_RULE", "Solrise Support Routing").strip() or "Solrise Support Routing"

DEFAULT_ROUTING = "umair.nawaz@solrisestores.com"
DEFAULT_ACCESS_ONLY = "daniyalbphq@gmail.com,daniyal@solrise.com"

ISSUE = "Issue"


def people(raw, default):
    seen, out = set(), []
    for part in (raw if raw is not None else default).split(","):
        email = part.strip()
        if email and email.lower() not in seen:
            seen.add(email.lower())
            out.append(email)
    return out


def user_exists(email):
    return bool(frappe.db.exists("User", email))


def holds_role(email):
    return bool(
        frappe.db.exists("Has Role", {"parent": email, "parenttype": "User", "role": ROLE})
    )


def strip_role_from_profile(email):
    """Remove ROLE from the user's Role Profile, if they have one.

    Frappe re-applies `role_profile_name` on every User save, which silently
    undoes a child-table edit - exactly what happened here: `daniyal@solrise.com`
    is on the site-local `HR` profile, which lists `Support Manager`, so removing
    the role from the user alone did nothing. Returns the profile name when it
    was changed, else None.
    """
    profile = None
    try:
        profile = frappe.db.get_value("User", email, "role_profile_name")
    except Exception:  # noqa: BLE001
        return None
    if not profile:
        return None
    doc = frappe.get_doc("Role Profile", profile)
    if not any(row.role == ROLE for row in doc.roles):
        return None
    kept = [row.role for row in doc.roles if row.role != ROLE]
    doc.set("roles", [])
    for role in kept:
        doc.append("roles", {"role": role})
    doc.save(ignore_permissions=True)
    return profile


def set_role(email, want):
    """Add or remove ROLE on `email`, leaving every other role alone."""
    if holds_role(email) == want:
        return False
    doc = frappe.get_doc("User", email)
    kept = [row.role for row in doc.roles if row.role != ROLE]
    doc.set("roles", [])
    for role in kept:
        doc.append("roles", {"role": role})
    if want:
        doc.append("roles", {"role": ROLE})
    doc.save(ignore_permissions=True)
    frappe.clear_cache(user=email)
    return True


def check_access_kept(email):
    """The point of the whole exercise: access must survive losing the role.

    Any of these grants `Issue` read/write, so the person can still open and work
    a ticket without holding the notification role.
    """
    try:
        roles = set(
            frappe.get_all(
                "Has Role",
                filters={"parent": email, "parenttype": "User"},
                pluck="role",
                limit_page_length=0,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return None, "could not read roles: {0}".format(exc)
    for candidate in ("Support Team", "Support Agent", "System Manager", "Department Head"):
        if candidate in roles:
            return candidate, None
    return None, "no Issue-access role left: {0}".format(sorted(roles))


def set_rule_users(emails):
    """Make the Assignment Rule route to exactly `emails`."""
    desired = [{"user": email} for email in emails]
    if not frappe.db.exists("Assignment Rule", RULE):
        print("  ! {0} does not exist - skipping the routing change".format(RULE))
        return False
    doc = frappe.get_doc("Assignment Rule", RULE)
    current = [row.user for row in doc.users]
    if current == [email for email in emails]:
        return False
    doc.set("users", [])
    for row in desired:
        doc.append("users", row)
    doc.save(ignore_permissions=True)
    return True


def main():
    routing = people(os.environ.get("SUPPORT_ROUTING_USERS"), DEFAULT_ROUTING)
    access_only = people(os.environ.get("SUPPORT_ACCESS_ONLY_USERS"), DEFAULT_ACCESS_ONLY)
    if not routing:
        print("cannot route support mail: no routing users given")
        return 1

    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    try:
        unknown = [email for email in routing if not user_exists(email)]
        if unknown:
            print("cannot route support mail: unknown user(s) {0}".format(unknown))
            return 1

        print("support routing on {0}".format(SITE))
        print("  notification role: {0}".format(ROLE))
        print("  mail goes to:      {0}".format(routing))

        for email in routing:
            if set_role(email, True):
                print("  + {0}: granted {1}".format(email, ROLE))
            else:
                print("  = {0}: already holds {1}".format(email, ROLE))

        for email in access_only:
            if not user_exists(email):
                print("  ? {0}: no such user - skipped".format(email))
                continue
            if not holds_role(email):
                print("  = {0}: does not hold {1}".format(email, ROLE))
                continue
            # A Role Profile re-applies its roles on save, so clean it first and
            # re-save the user; only then does the child-table edit stick.
            profile = strip_role_from_profile(email)
            if profile:
                frappe.get_doc("User", email).save(ignore_permissions=True)
                frappe.clear_cache(user=email)
            set_role(email, False)
            if holds_role(email):
                print("  ! {0}: still holds {1} after the edit".format(email, ROLE))
                continue
            kept, problem = check_access_kept(email)
            if problem:
                print("  ! {0}: removed {1} but {2}".format(email, ROLE, problem))
            else:
                note = " (via the {0} profile)".format(profile) if profile else ""
                print("  - {0}: {1} removed; access kept via {2}{3}".format(
                    email, ROLE, kept, note))

        if set_rule_users(routing):
            print("  ~ {0}: routed to {1}".format(RULE, routing))
        else:
            print("  = {0}: already routed to {1}".format(RULE, routing))

        frappe.db.commit()

        holders = sorted(
            frappe.get_all(
                "Has Role",
                filters={"role": ROLE, "parenttype": "User"},
                pluck="parent",
                limit_page_length=0,
            )
        )
        print("  {0} holders now: {1}".format(ROLE, holders))
        extra = [email for email in holders if email not in routing]
        if extra:
            print("  ! still holding {0} and will still be mailed: {1}".format(ROLE, extra))
    finally:
        frappe.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
