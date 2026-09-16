"""
Solrise ERP - white-label branding without the Solrise application layer.

The full rebrand lives in `apps/solrise_erp` (`branding.py`, plus `boot.py`, the
`solrise_erp.js` boot patch and the footer template override that fix the payload
the platform attaches after `boot_session`). Until that app is deployed, this
applies the **DocType half** of `docs/10-branding.md`:

  * System Settings   - app_name, language, country, currency, time zone
  * Website Settings  - app_name, brand_html (login page), copyright (footer)
  * Global Defaults   - country, default_currency
  * Company           - optionally renamed to `Solrise` (opt-in, see below)

What it cannot do, and which really does need the app: the navbar/app-switcher
logo (`app_logo_url` and `apps_data`), the sidebar workspace labels, and the
client-side strings the platform injects into the Desk payload. See
`docs/16-deployment-pipeline-status.md` section 5.

Normally run by the deploy or by hand:
    SITE_ENV=aws ./scripts/run-python.sh scripts/branding_only.py
    BRANDING_RENAME_COMPANY=1 SITE_ENV=aws ./scripts/branding_only.py

Idempotent: values are only written when they differ. Read-only apart from the
DocType values above.
"""
import os
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"

BRAND = "Solrise"
# A text lockup, so no image asset is required (docs/10 section 3 shows how to
# swap in a real logo later).
BRAND_HTML = (
    "<span class='solrise-brand' "
    "style='font-weight:600;font-size:1.15rem;letter-spacing:.02em'>Solrise</span>"
)

# (doctype, fieldname, value) - skipped when the version does not have the field.
SINGLES = [
    ("System Settings", "app_name", BRAND),
    ("System Settings", "language", "en"),
    ("System Settings", "country", "United States"),
    ("System Settings", "currency", "USD"),
    ("System Settings", "time_zone", "America/New_York"),
    ("Website Settings", "app_name", BRAND),
    ("Website Settings", "brand_html", BRAND_HTML),
    ("Website Settings", "copyright", BRAND),
    ("Global Defaults", "country", "United States"),
    ("Global Defaults", "default_currency", "USD"),
]


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def has_field(doctype, fieldname):
    try:
        return frappe.db.exists("DocType", doctype) and frappe.get_meta(doctype).has_field(fieldname)
    except Exception:  # noqa: BLE001 - a missing DocType is a skip, not a crash
        return False


def apply_singles():
    changed, skipped = [], []
    for doctype, fieldname, value in SINGLES:
        if not has_field(doctype, fieldname):
            skipped.append(f"{doctype}.{fieldname} (no such field)")
            continue
        try:
            current = frappe.db.get_single_value(doctype, fieldname)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{doctype}.{fieldname} ({type(exc).__name__})")
            continue
        if current == value:
            print(f"  = {doctype}.{fieldname} already {value!r}")
            continue
        frappe.db.set_single_value(doctype, fieldname, value)
        changed.append(f"{doctype}.{fieldname}")
        print(f"  + {doctype}.{fieldname}: {current!r} -> {value!r}")
    return changed, skipped


def report_locale():
    """Locale is part of white labeling, but only as a default: report drift."""
    for doctype, fieldname, expected in [
        ("Global Defaults", "country", "United States"),
        ("Global Defaults", "default_currency", "USD"),
        ("System Settings", "time_zone", "America/New_York"),
    ]:
        if not has_field(doctype, fieldname):
            continue
        value = frappe.db.get_single_value(doctype, fieldname)
        if value != expected:
            print(f"  warn {doctype}.{fieldname} is {value!r}, expected {expected!r}")


def report_company():
    """The Company name appears on documents and print formats.

    Renaming is opt-in (`BRANDING_RENAME_COMPANY=1`): it rewrites links to the
    Company, so it should be a deliberate decision on a site with data.
    """
    companies = frappe.get_all("Company", fields=["name", "abbr"], limit=5)
    if not companies:
        print("  warn no Company yet - run the platform setup wizard before HR/payroll/accounting")
        return
    for company in companies:
        if company.name == BRAND:
            print(f"  = Company already {BRAND!r} (abbr {company.abbr!r})")
            continue
        if not env_flag("BRANDING_RENAME_COMPANY"):
            print(
                f"  warn Company is {company.name!r} (abbr {company.abbr!r}), not {BRAND!r} - "
                "documents and print formats show that name. Re-run with "
                "BRANDING_RENAME_COMPANY=1 to rename it."
            )
            continue
        try:
            frappe.rename_doc("Company", company.name, BRAND, force=True, merge=False)
            frappe.db.commit()
            print(f"  + Company renamed: {company.name!r} -> {BRAND!r} (abbr stays {company.abbr!r})")
        except Exception as exc:  # noqa: BLE001 - report, keep going
            frappe.db.rollback()
            print(f"  warn could not rename Company {company.name!r}: {type(exc).__name__}: {exc}")


def main():
    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        print(f"applying white-label branding on {SITE}\n")
        changed, skipped = apply_singles()
        print("\nlocale:")
        report_locale()
        print("\ncompany:")
        report_company()
        frappe.db.commit()
    finally:
        frappe.destroy()

    print()
    print(f"branding applied: {len(changed)} value(s) written, {len(skipped)} skipped")
    if skipped:
        print("  skipped: " + "; ".join(skipped))
    print(
        "  Not covered without apps/solrise_erp: navbar/app-switcher logo, sidebar\n"
        "  workspace labels, and the client-side Desk payload. See docs/10-branding.md\n"
        "  and docs/16-deployment-pipeline-status.md section 5."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
