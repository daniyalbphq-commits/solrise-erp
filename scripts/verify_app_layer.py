"""
Solrise ERP - verify that the deployed site carries the Solrise application layer.

The white labeling, the RBAC matrix, the approval workflows and notifications, the
reports and dashboards, the LLM assistant ("Ask Solrise") and the universal chat
entry flow all live in the `solrise_erp` app. Installing the app and running its
`after_migrate` is what applies them - so "the deploy delivered the product" is a
fact that can be checked, and this script checks it.

Normally run for you by the deploy:
    SITE_ENV=aws ./scripts/run-python.sh scripts/verify_app_layer.py

Exit code 0 means the application layer is installed and live. Failures are
counted and re-listed at the end; warnings are things only a human can decide
(an LLM provider key, a messaging channel, the company record).

Read-only: it changes nothing on the site.
"""
import os
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"

# The app layer, in install order. `cloud_storage` is verified by
# scripts/configure_s3_media.py, not here.
REQUIRED_APPS = ["erpnext", "hrms", "solrise_erp"]
BRAND = "Solrise"
# Roles the app and scripts/roles_rbac.py create; a missing one means the RBAC
# matrix never applied.
EXPECTED_ROLES = [
    "Support Agent",
    "Support Manager",
    "CRM User",
    "CRM Manager",
    "Solrise Admin",
]

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


def count(doctype, filters=None):
    """Row count, or None when the DocType is absent (a version difference)."""
    try:
        return frappe.db.count(doctype, filters)
    except Exception:  # noqa: BLE001 - a version difference is not a deployment failure
        return None


def single_value(doctype, fieldname):
    """Single value, or None when the DocType or the field is absent."""
    try:
        if not frappe.db.exists("DocType", doctype):
            return None
        return frappe.db.get_single_value(doctype, fieldname)
    except Exception:  # noqa: BLE001
        return None


def field_exists(doctype, fieldname):
    try:
        return frappe.get_meta(doctype).has_field(fieldname)
    except Exception:  # noqa: BLE001
        return False


def check_apps():
    print("apps installed on the site:")
    try:
        installed = {app.lower() for app in frappe.get_installed_apps()}
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        fail("read the installed app list", f"{type(exc).__name__}: {exc}")
        return
    for app in REQUIRED_APPS:
        check(f"app {app}", app in installed)
    extra = sorted(installed - set(REQUIRED_APPS))
    if extra:
        print(f"  note  also installed: {', '.join(extra)}")


def check_branding():
    """The white-label requirement: users must never see the upstream name."""
    print("white-label branding:")
    app_name = single_value("System Settings", "app_name")
    check(
        "System Settings.app_name",
        app_name == BRAND,
        f"got {app_name!r} - branding has not applied",
    )

    if field_exists("Website Settings", "app_name"):
        site_name = single_value("Website Settings", "app_name")
        check("Website Settings.app_name", site_name == BRAND, f"got {site_name!r}")
    else:
        warn("Website Settings.app_name", "field not present in this version")

    if field_exists("Website Settings", "brand_html"):
        brand_html = single_value("Website Settings", "brand_html") or ""
        check("Website Settings.brand_html", BRAND in brand_html, "login page brand")

    # Locale defaults, as applied by solrise_erp.branding.ensure_branding(). A
    # deliberate business change (another currency) shows up here as a warning.
    country = single_value("Global Defaults", "country")
    currency = single_value("Global Defaults", "default_currency")
    if country and country != "United States":
        warn("Global Defaults.country", f"got {country!r}, expected 'United States'")
    if currency and currency != "USD":
        warn("Global Defaults.default_currency", f"got {currency!r}, expected 'USD'")

    # A Company is created by the platform setup wizard, not by `bench new-site`;
    # without one, branded documents and print formats have nothing to point at.
    if count("Company") == 0:
        warn("Company", "none yet - run the setup wizard (docs/08) before HR/accounting")
    elif not frappe.db.exists("Company", BRAND):
        warn("Company", f"no {BRAND!r} company; documents use another company name")


def check_app_configuration():
    """Config surfaces the app needs before the assistant or chat can work."""
    print("application configuration:")
    exists = frappe.db.exists("DocType", "Solrise Settings")
    if not check("Solrise Settings exists", exists):
        print("  note  the app did not install its DocTypes - re-run the deploy")
        return

    enabled = single_value("Solrise Settings", "enabled")
    provider = single_value("Solrise Settings", "provider")
    model = single_value("Solrise Settings", "model")
    if not enabled:
        warn(
            "assistant enabled",
            "Solrise Settings.enabled is off - the LLM chat answers nothing until a "
            "provider and key are set (docs/06-phase4-assistant.md)",
        )
    else:
        ok("assistant enabled", f"provider={provider or '?'} model={model or '?'}")

    if single_value("Solrise Settings", "rate_limit_per_hour") in (None, 0):
        warn("assistant rate limit", "rate_limit_per_hour is unset")

    if single_value("Solrise Settings", "enable_universal_chat") is not None:
        flags = {
            key: single_value("Solrise Settings", key)
            for key in (
                "chat_enable_llm_fallback",
                "chat_allow_delete",
                "chat_allow_approve",
                "audit_log_retention_days",
            )
        }
        ok("universal chat", " ".join(f"{key}={value}" for key, value in flags.items()))
    else:
        warn("universal chat settings", "fields absent - chat falls back to its defaults")


def check_entry_points():
    """Both chat surfaces must be importable and callable, not just installed."""
    print("chat entry points:")
    try:
        from solrise_erp.api import v1

        health = v1.health()
        ok("assistant health", f"{health}")
        if isinstance(health, dict) and not health.get("assistant_enabled"):
            warn("assistant health", "reports assistant_enabled=false - provider not configured")
    except Exception as exc:  # noqa: BLE001 - the point is to report the reason
        fail("solrise_erp.api.v1.health", f"{type(exc).__name__}: {exc}")

    try:
        from solrise_erp.api import chat

        for name in ("bootstrap", "turn"):
            check(f"api.chat.{name} exposed", callable(getattr(chat, name, None)))
    except Exception as exc:  # noqa: BLE001
        fail("solrise_erp.api.chat", f"{type(exc).__name__}: {exc}")


def check_configuration_artifacts():
    """What the app's after_migrate creates: roles, workflows, reports."""
    print("configuration applied by the app:")
    for role in EXPECTED_ROLES:
        check(f"role {role!r}", frappe.db.exists("Role", role))

    workflows = count("Workflow")
    if workflows is None:
        warn("approval workflows", "Workflow DocType not found")
    elif workflows == 0:
        fail("approval workflows", "no Workflow documents - RBAC/workflows never applied")
    else:
        ok("approval workflows", f"{workflows} defined")

    reports = count("Report", {"report_name": ["like", f"{BRAND} %"]})
    if reports is None:
        warn("reports", "Report DocType not found")
    elif reports == 0:
        fail("Solrise reports", "no Solrise * reports - reporting never applied")
    else:
        ok("Solrise reports", f"{reports} available")

    if frappe.db.exists("DocType", "Dashboard"):
        check("Solrise Operations dashboard", frappe.db.exists("Dashboard", "Solrise Operations"))

    notifications = count("Notification")
    if notifications is not None and notifications == 0:
        fail("notifications", "no Notification documents - notifications never applied")
    elif notifications is not None:
        ok("notifications", f"{notifications} defined")

    has_log = frappe.db.exists("DocType", "Solrise Message Log")
    messages = count("Solrise Message Log") if has_log else None
    if messages is None:
        warn(
            "SMS / WhatsApp channel",
            "Solrise Notification Channel / Message Log not present, so messaging is off",
        )
    else:
        ok("messaging log", f"{messages} message(s) logged")

    faq = count("Solrise FAQ") if frappe.db.exists("DocType", "Solrise FAQ") else 0
    if not faq:
        warn("knowledge base", "Solrise FAQ is empty - the assistant answers no FAQ")


def main():
    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        print(f"verifying the Solrise application layer on {SITE}")
        check_apps()
        check_branding()
        check_app_configuration()
        check_entry_points()
        check_configuration_artifacts()
    finally:
        frappe.destroy()

    print()
    if failures:
        print(
            f"application layer: {len(failures)} FAILED check(s): {', '.join(failures)}",
            file=sys.stderr,
        )
        print(
            "  The app is expected in the image (apps.json -> ${SOLRISE_APP_URL}) and in\n"
            "  INSTALL_APPS. If the app repository is unavailable on purpose, set\n"
            "  solrise_app_enabled: false in infra/ansible/group_vars/all/main.yml.\n"
            "  Otherwise see docs/16-deployment-pipeline-status.md section 5.",
            file=sys.stderr,
        )
        return 1
    if warnings:
        print(f"application layer: OK ({len(warnings)} thing(s) need an operator decision)")
    else:
        print("application layer: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
