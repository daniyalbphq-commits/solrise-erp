"""
Solrise ERP - verify the customer portal against a deployed site.

`verify_app_layer.py` proves the app layer is installed; `verify_chat.py` proves
the chat answers. Neither proves the thing the station staff actually touch: that
a Customer-role user lands on the button page, may file an Issue, and sees only
their own.

This does, and it is deliberately read-only. It inspects permissions, the
resolved home page, the CRM wiring the portal depends on (Contact -> Customer),
the store master data, and the files that have to be on disk for the pages to
render at all. Filing a real report is left to a human.

    SITE_ENV=aws ./scripts/run-python.sh scripts/verify_portal.py

Exit code 0 means the portal is wired. Failures are counted and re-listed; the
warnings are operator decisions (no store data yet, no portal logins yet).

See docs/17-customer-portal.md and docs/18-stores.md.
"""
import os
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"
BENCH = os.path.dirname(SITES_PATH)

# A Customer must be able to file and read back their own Issues, and nothing
# else's - that is `if_owner`.
#
# `share` is in here because Frappe's assignment path shares a newly assigned
# document with its assignee *as the user who created it*: with `share: 0` the
# whole submission is rolled back with "No permission to share Issue". See
# install.PORTAL_PERMISSIONS.
EXPECTED_ISSUE_PERMS = {
	"read": 1,
	"write": 1,
	"create": 1,
	"share": 1,
}

failures = []
warnings = []


def ok(label, detail=""):
    print(f"  ok    {label}{f' - {detail}' if detail else ''}")


def fail(label, detail=""):
    failures.append(label)
    print(f"  FAIL  {label}{f' - {detail}' if detail else ''}")


def warn(label, detail=""):
    warnings.append(label)
    print(f"  warn  {label}{f' - {detail}' if detail else ''}")


def check(label, condition, detail=""):
    if condition:
        ok(label, detail)
    else:
        fail(label, detail)
    return bool(condition)


def check_permissions():
    """The Customer's door into Issue, and the wall around other people's."""
    print("permissions:")
    rows = frappe.get_all(
        "Custom DocPerm",
        filters={"parent": "Issue", "role": "Customer", "permlevel": 0},
        # Must list every flag EXPECTED_ISSUE_PERMS asserts: a field that is not
        # selected comes back None and reports as a failure that is not there.
        fields=["read", "write", "create", "share", "if_owner"],
    )
    if not rows:
        fail("Customer may use Issue", "no Custom DocPerm for Customer on Issue")
        return
    row = rows[0]
    for field, expected in EXPECTED_ISSUE_PERMS.items():
        check(
            f"Customer may {field} an Issue",
            int(row.get(field) or 0) == expected,
            repr(row.get(field)),
        )
    check(
        "a Customer only reaches their own Issues",
        int(row.get("if_owner") or 0) == 1,
        f"if_owner={row.get('if_owner')}",
    )


def check_landing():
    """The `role_home_page` hook, which is what puts a customer on /start."""
    print("landing page:")
    hooks = frappe.get_hooks("role_home_page") or {}
    route = hooks.get("Customer")
    check("role_home_page maps Customer", bool(route), repr(route))
    # It must be a list: Frappe indexes the value with [-1], so a bare string
    # resolves to the route "t" (frappe/website/utils.py).
    check("the mapping is a list, not a string", isinstance(route, list), type(route).__name__)
    if isinstance(route, list) and route:
        check("the route is /start", route[-1] == "start", repr(route[-1]))

    # The route has to have a page behind it in the app.
    page = os.path.join(
        BENCH, "apps", "solrise_erp", "solrise_erp", "www", "start", "index.py"
    )
    check("the /start page exists in the app", os.path.exists(page), page)
    logout = os.path.join(
        BENCH, "apps", "solrise_erp", "solrise_erp", "www", "start", "logout", "index.py"
    )
    check("the logout page exists in the app", os.path.exists(logout), logout)


def check_widgets():
    """Assets the pages need, declared in hooks and served under /assets."""
    print("portal assets:")
    for hook in ("web_include_js", "web_include_css"):
        declared = list(frappe.get_hooks(hook) or [])
        portal = [p for p in declared if "solrise_portal" in p]
        check(f"{hook} declares the portal asset", bool(portal), f"{portal}")

    for relative in (
        "assets/solrise_erp/js/solrise_portal.js",
        "assets/solrise_erp/css/solrise_portal.css",
    ):
        path = os.path.join(BENCH, relative)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        # Size, not content: "served but empty" is the failure that renders an
        # unstyled page, which is what happened here before.
        check(f"served: /{relative}", size > 0, f"{size} bytes")


def check_store_data():
    """The master data the portal resolves a store from."""
    print("store data (docs/18):")
    customers = frappe.db.count("Customer", {"customer_type": "Company"})
    companies = frappe.db.count("Customer")
    check("store Customers exist", companies > 0, f"{companies} Customer record(s)")
    if companies and customers != companies:
        warn("some Customers are not Companies", f"{companies - customers}")

    with_code = 0
    if frappe.get_meta("Customer").has_field("store_code"):
        with_code = frappe.db.count("Customer", {"store_code": ["!=", ""]})
        check("Customer.store_code is populated", with_code > 0, f"{with_code} with a code")
    else:
        fail("Customer.store_code exists", "the importer did not add the field")

    contacts = frappe.db.count("Contact")
    check("manager Contacts exist", contacts > 0, f"{contacts} Contact record(s)")

    # The portal resolves identity through the Contact -> Customer link, so an
    # unlinked Contact is invisible to the portal.
    links = frappe.db.count("Dynamic Link", {"parenttype": "Contact", "link_doctype": "Customer"})
    check("Contacts are linked to Customers", links > 0, f"{links} link(s)")


def check_portal_api():
    """The two whitelisted calls the pages use, and the identity resolver."""
    print("portal API:")
    from solrise_erp.api import portal as api
    from solrise_erp.portal import catalog, guard, identity

    check("api.portal.create_issue exposed", callable(getattr(api, "create_issue", None)))
    check("api.portal.my_issues exposed", callable(getattr(api, "my_issues", None)))
    check("portal.identity.resolve exposed", callable(getattr(identity, "resolve", None)))
    check("portal.guard.PORTAL_HOME is /start", guard.PORTAL_HOME == "/start",
          guard.PORTAL_HOME)

    categories = list(catalog.CATEGORIES)
    check("the category catalogue is loaded", len(categories) >= 20, f"{len(categories)} categories")
    tiles = catalog.visible_tiles(roles=("Customer",))
    check("the Customer sees a tile", bool(tiles), f"{len(tiles)} tile(s)")

    # A Customer-role user must pass the two checks the pages rely on.
    sample = _sample_customer_user()
    if not sample:
        warn("no portal login to test with", "run scripts/import_stores.sh with "
                                             "STORES_DEFAULT_PASSWORD set (docs/18 section 5)")
        return
    ok("a portal login exists", sample)
    check(
        f"{sample} may create an Issue",
        bool(frappe.has_permission("Issue", "create", user=sample)),
    )
    check(
        f"{sample} may read an Issue",
        bool(frappe.has_permission("Issue", "read", user=sample)),
    )
    check(
        f"{sample} resolves to a Customer",
        bool(identity.resolve(user=sample)),
    )
    check(
        f"{sample} is not a System Manager",
        "System Manager" not in (frappe.get_roles(sample) or []),
    )


def _sample_customer_user():
    """One enabled *portal* user carrying the Customer role, for the checks.

    A `Website User` specifically: a desk account that happens to hold the
    Customer role too (a support manager who also runs a store) would pass every
    check here while proving nothing about what a station phone can reach.
    """
    users = frappe.get_all(
        "Has Role",
        filters={"role": "Customer", "parenttype": "User"},
        pluck="parent",
    )
    portal = []
    other = []
    for name in sorted(set(users)):
        if not frappe.db.get_value("User", name, "enabled"):
            continue
        if frappe.db.get_value("User", name, "user_type") == "Website User":
            portal.append(name)
        else:
            other.append(name)
    return (portal or other or [None])[0]


def check_my_reports():
    """The customer's own reports, and the updates on them (docs/17 section 13)."""
    print("your reports:")
    from solrise_erp.portal import catalog, reports

    # The state vocabulary is a transcription of the DocType's Select options, so
    # ask the DocType. An upstream release that adds a status would otherwise show
    # it as "Waiting" forever, and nothing would say so.
    options = [
        option.strip()
        for option in str(frappe.get_meta("Issue").get_field("status").options or "").splitlines()
        if option.strip()
    ]
    unmapped = [option for option in options if option not in catalog.STATUS_TO_STATE]
    check(
        f"every Issue status has a customer state ({len(options)} of them)",
        bool(options) and not unmapped,
        f"unmapped: {unmapped}",
    )

    app_root = os.path.join(BENCH, "apps", "solrise_erp", "solrise_erp")
    for relative in ("www/start/my-report/index.html", "www/start/my-report/index.py"):
        path = os.path.join(app_root, relative)
        check(f"the detail page exists: {relative}", os.path.exists(path), path)

    home = _app_file("www/start/index.html")
    check(
        "the home page lists the reports and links to them",
        "reports" in home and "/start/my-report" in home,
    )

    sample = _portal_user_with_a_report() or _sample_customer_user()
    if not sample:
        warn("no portal login to read reports for", "see docs/18 section 5")
        return

    mine = reports.rows(user=sample)
    check(f"{sample} can list their own reports", isinstance(mine, list), f"{len(mine)} row(s)")

    # Scope one: only their own. Every row must be theirs - a list that leaked
    # another station's report would still be a list of the right length.
    stranger = [row for row in mine if str(row.get("name")) and not _owned_by(row.get("name"), sample)]
    check(f"every row is {sample}'s own report", not stranger, f"{stranger[:2]}")

    # Scope two: only the open ones. Counted independently of the code under test.
    if reports.SHOW_FINISHED:
        warn("finished reports are listed too", "reports.SHOW_FINISHED is True")
    else:
        expected = frappe.db.count(
            "Issue",
            {
                "owner": sample,
                "status": ["not in", list(reports.FINISHED_STATUSES)],
            },
        )
        check(
            f"the list holds only {sample}'s open reports",
            len(mine) == min(expected, reports.LIST_LIMIT),
            f"{len(mine)} shown, {expected} open",
        )
        finished = frappe.db.count(
            "Issue",
            {"owner": sample, "status": ["in", list(reports.FINISHED_STATUSES)]},
        )
        if finished:
            check(
                f"the {finished} finished report(s) are not listed",
                not any(row.get("status") in reports.FINISHED_STATUSES for row in mine),
            )

    # The check that matters: a report belonging to somebody else must not be
    # readable by name. This is the whole reason `reports.detail()` exists rather
    # than the page fetching a doc itself.
    other = frappe.db.get_value(
        "Issue",
        {"owner": ["not in", ["", sample]]},
        ["name", "owner"],
        as_dict=True,
    )
    if not other:
        warn(
            "no other customer's report to try",
            "file one from another station to exercise the isolation check",
        )
    else:
        check(
            f"another station's report is invisible to {sample}",
            reports.detail(user=sample, name=other.name) is None,
            f"{other.name} belongs to {other.owner}",
        )

    own = frappe.db.get_value("Issue", {"owner": sample}, ["name"], as_dict=True)
    if not own:
        warn(f"{sample} has no report of their own", "file one from the portal to exercise this")
        return
    detail = reports.detail(user=sample, name=own.name)
    check(f"{sample} can read their own report", bool(detail), own.name)
    if not detail:
        return
    for field in ("name", "state", "messages", "has_update", "note", "store_label"):
        check(f"the detail carries {field}", field in detail)
    check(
        "its state is one the catalogue renders",
        detail.get("state", {}).get("key") in {state["key"] for state in catalog.STATES},
        str(detail.get("state", {}).get("key")),
    )
    # A message is a Communication and never a Comment, which is the line that
    # keeps the support team's internal notes off a station phone.
    leaked = [m for m in detail.get("messages") or [] if "@" in str(m.get("text", ""))]
    check("no addresses are shown to the customer", not leaked, f"{len(leaked)} message(s)")


def _portal_user_with_a_report():
    """The owner of some report, if that owner is a real portal login.

    Preferred over an arbitrary Customer-role user so the positive half of the
    checks - "can read their own report, with its updates" - is actually
    exercised rather than skipped on a site where the first customer alphabetically
    has not filed anything.
    """
    try:
        owners = frappe.get_all("Issue", pluck="owner", distinct=True, limit_page_length=50)
    except Exception:  # noqa: BLE001
        return None
    for name in sorted({owner for owner in owners if owner}):
        if not frappe.db.get_value("User", name, "enabled"):
            continue
        if frappe.db.get_value("User", name, "user_type") == "Website User":
            return name
    return None


def check_report_form():
    """The form's two required answers, and where the name list comes from.

    Read-only: both refusals throw before anything is written, so this never
    leaves a report behind (docs/17 section 14).
    """
    print("report form:")
    from solrise_erp.api import portal as api
    from solrise_erp.portal import identity

    refusals = (
        ("an empty note", {"description": "   ", "reporter": "Someone"}, "say what is wrong"),
        ("a missing name", {"description": "the pump is dead", "reporter": "  "}, "choose your name"),
    )
    for label, payload, expected in refusals:
        payload = dict(payload, category="other", urgency="normal")
        try:
            api.create_issue(**payload)
            fail(f"{label} is refused", "it was accepted")
        except frappe.ValidationError as exc:
            # The message matters: a missing store would also raise here, and would
            # pass a check that only asked whether *something* was raised.
            check(f"{label} is refused", expected in str(exc), str(exc)[:70])
        except Exception as exc:  # noqa: BLE001
            fail(f"{label} is refused", f"{type(exc).__name__}: {exc}")

    sample = _portal_user_with_a_report() or _sample_customer_user()
    stores = identity.stores(user=sample) if sample else []
    if not stores:
        warn("no store to list manager names for", "see docs/18 section 5")
        return
    names = identity.managers(stores[0]["customer"])
    check(
        f"the picker has names for {stores[0]['customer_name']}",
        bool(names),
        f"{names}",
    )


def _owned_by(issue_name, user):
    """True when `issue_name` belongs to `user`, read without permissions."""
    try:
        owner = frappe.db.get_value("Issue", issue_name, "owner")
    except Exception:  # noqa: BLE001
        return False
    return str(owner or "").lower() == str(user or "").lower()


def _app_file(relative):
    """Read a file from the installed app, or "" if it is not there."""
    try:
        with open(os.path.join(BENCH, "apps", "solrise_erp", "solrise_erp", relative),
                  encoding="utf-8") as handle:
            return handle.read()
    except Exception:  # noqa: BLE001 - a missing file is a failed check, not a crash
        return ""


def check_support_routing():
    """The Support Manager must be the one who sees a newly filed report."""
    print("support routing:")
    rule = frappe.db.get_value(
        "Assignment Rule", {"name": "Solrise Support Routing"}, "name"
    )
    check("the assignment rule exists", bool(rule), repr(rule))
    if rule:
        doc = frappe.get_doc("Assignment Rule", rule)
        users = [u.user for u in doc.users if u.user]
        check("the rule has assignees", bool(users), f"{users}")
        managers = [
            u for u in frappe.get_all(
                "Has Role",
                filters={"role": "Support Manager", "parenttype": "User"},
                pluck="parent",
            )
            if frappe.db.get_value("User", u, "enabled")
        ]
        missing = [m for m in managers if m not in users]
        check("every Support Manager is routed to", not missing, f"missing: {missing}")


def main():
    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    try:
        print(f"verifying the customer portal on {SITE}\n")
        check_permissions()
        print()
        check_landing()
        print()
        check_widgets()
        print()
        check_store_data()
        print()
        check_portal_api()
        print()
        check_my_reports()
        print()
        check_report_form()
        print()
        check_support_routing()
    finally:
        frappe.destroy()

    print()
    if failures:
        print(f"portal: {len(failures)} thing(s) FAILED")
        for entry in failures:
            print(f"  - {entry}")
        return 1
    if warnings:
        print(f"portal: OK ({len(warnings)} thing(s) need an operator decision)")
        return 0
    print("portal: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
