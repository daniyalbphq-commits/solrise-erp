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
EXPECTED_ISSUE_PERMS = {
    "read": 1,
    "write": 1,
    "create": 1,
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
        fields=["read", "write", "create", "if_owner"],
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
    """One enabled user carrying the Customer role, for the permission probes."""
    users = frappe.get_all(
        "Has Role",
        filters={"role": "Customer", "parenttype": "User"},
        pluck="parent",
    )
    for name in users:
        if frappe.db.get_value("User", name, "enabled"):
            return name
    return None


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
