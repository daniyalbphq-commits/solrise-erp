"""
Solrise ERP - import the records the Solrise app ships as fixtures, without the app.

`apps/solrise_erp` applies approval workflows, notifications, reports and
dashboards from its own fixtures on every `bench migrate`. Until that app is
deployed, this imports the same records - but only the ones that touch standard
DocTypes:

  imported  workflow_action_master, workflow_state, workflow (4 approvals),
            notification (4), report (7 of 9), dashboard_chart (4 of 5),
            dashboard (Solrise Operations)
  skipped   role.json, custom_docperm.json
              -> scripts/roles_rbac.py writes those, and it copies each DocType's
                 shipped DocPerms before the first Custom DocPerm. Importing the
                 exported rows directly would silently revoke every role the
                 export did not mention (docs/11-rbac.md, rule 3).
            service_level_agreement.json, assignment_rule.json
              -> scripts/setup_erp.py creates both (with a round-robin rule and
                 unassign/close conditions the export does not carry).
            solrise_faq.json
              -> needs the app's own `Solrise FAQ` DocType.
            custom_field / print_format / property_setter /
            solrise_notification_channel
              -> empty exports.

Anything that references an app DocType (`Solrise Chat Log`,
`Solrise Message Log`, ...) is skipped on purpose: two of the nine reports and one
of the five dashboard charts do, and the reports table would simply error.

Run it through the wrapper, which stages the JSON into the container:
    SITE_ENV=aws ./scripts/import_app_fixtures.sh

Idempotent: a record that already exists is left untouched, never overwritten.
The exit code is non-zero only when an import actually failed.
"""
import json
import os
import sys

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = "/home/frappe/frappe-bench/sites"
FIXTURE_DIR = os.environ.get("FIXTURE_DIR", "/tmp/solrise-fixtures")

# Import order matters: states/actions before workflows, charts before the
# dashboard that links them.
IMPORT_ORDER = [
    "workflow_action_master.json",
    "workflow_state.json",
    "workflow.json",
    "notification.json",
    "report.json",
    "dashboard_chart.json",
    "dashboard.json",
]

SKIP_FILES = {
    "role.json": "scripts/roles_rbac.py creates the roles",
    "custom_docperm.json": "scripts/roles_rbac.py writes the matrix (it preserves shipped DocPerms)",
    "service_level_agreement.json": "scripts/setup_erp.py creates the Issue SLA",
    "assignment_rule.json": "scripts/setup_erp.py creates the routing rule",
    "solrise_faq.json": "needs the app's Solrise FAQ DocType",
    "custom_field.json": "empty export",
    "print_format.json": "empty export",
    "property_setter.json": "empty export",
    "solrise_notification_channel.json": "empty export",
}

# DocTypes that only the app provides. Any fixture mentioning one is skipped.
APP_DOCTYPES = [
    "Solrise Settings",
    "Solrise Chat Log",
    "Solrise AI Audit Log",
    "Solrise Message Log",
    "Solrise FAQ",
    "Solrise Notification Channel",
]

# Keys that are not DocType fields but must survive the filter: `doctype` and
# `name` are how the record is addressed and inserted.
STRUCTURAL_FIELDS = {"doctype", "name"}

# Set by Frappe, not part of what a fixture is trying to say.
SYSTEM_FIELDS = {
    "owner", "creation", "modified", "modified_by", "idx", "docstatus",
    "_user_tags", "_comments", "_assign", "_liked_by", "_seen",
    "parent", "parentfield", "parenttype",
}

# Link fields the app itself declares. Without the app there is nothing to link
# to, and none of them are required, so the value is dropped instead of failing
# (the app's next `bench migrate` sets them again).
DROPPABLE_LINKS = {"module"}

# Top-level links that must resolve, or there is nothing to import against.
HARD_LINKS = ("document_type", "ref_doctype")

results = {"created": [], "exists": [], "skipped": [], "failed": []}
dropped_fields = set()
adjusted_fields = set()


def load(name):
    with open(os.path.join(FIXTURE_DIR, name)) as handle:
        return json.load(handle)


def has_doctype(name):
    try:
        return bool(frappe.db.exists("DocType", name))
    except Exception:  # noqa: BLE001
        return False


def referenced_app_doctype(doc):
    blob = json.dumps(doc)
    for name in APP_DOCTYPES:
        if name in blob:
            return name
    return None


def missing_hard_link(doc):
    for field in HARD_LINKS:
        value = doc.get(field)
        if value and not has_doctype(value):
            return f"{field}={value!r}"
    return None


def clean_fields(doctype, payload):
    """Drop system fields and anything the installed DocType does not have."""
    meta = frappe.get_meta(doctype)
    clean = {}
    for key, value in payload.items():
        if key in SYSTEM_FIELDS:
            continue
        if key not in STRUCTURAL_FIELDS and not meta.has_field(key):
            dropped_fields.add(f"{doctype}.{key}")
            continue
        if key in DROPPABLE_LINKS and value and not frappe.db.exists("Module Def", value):
            dropped_fields.add(f"{doctype}.{key} ({value!r} is not installed)")
            continue
        if key == "is_standard" and value in (1, True, "Yes"):
            # Only a shipped app may own a standard record - Frappe refuses to
            # save one outside developer mode. Imported here as a custom record;
            # the app re-applies its own value when it is deployed.
            clean[key] = "No" if isinstance(value, str) else 0
            adjusted_fields.add(f"{doctype}.{key}: {value!r} -> {clean[key]!r}")
            continue
        field = meta.get_field(key)
        if isinstance(value, list) and getattr(field, "fieldtype", None) == "Table":
            clean[key] = [
                clean_fields(field.options, row) for row in value if isinstance(row, dict)
            ]
        else:
            clean[key] = value
    return clean


def prune_children(doctype, payload, name):
    """Drop child rows whose link target is missing, and say so."""
    if doctype == "Dashboard":
        kept = [row for row in payload.get("charts", []) if frappe.db.exists("Dashboard Chart", row.get("chart"))]
        if len(kept) != len(payload.get("charts", [])):
            results["skipped"].append(f"{name}: {len(payload['charts']) - len(kept)} dashboard chart link(s) dropped (chart not installed)")
        payload["charts"] = kept

    if doctype == "Workflow":
        states = [row for row in payload.get("states", []) if frappe.db.exists("Workflow State", row.get("state"))]
        transitions = []
        for row in payload.get("transitions", []):
            if not frappe.db.exists("Workflow Action Master", row.get("action")):
                results["skipped"].append(f"{name}: transition '{row.get('action')}' dropped (action master missing)")
                continue
            if not frappe.db.exists("Workflow State", row.get("state")) or not frappe.db.exists("Workflow State", row.get("next_state")):
                results["skipped"].append(f"{name}: transition '{row.get('action')}' dropped (state missing)")
                continue
            if row.get("allowed") and not frappe.db.exists("Role", row["allowed"]):
                results["skipped"].append(f"{name}: transition '{row.get('action')}' dropped (role {row['allowed']!r} missing - run roles_rbac.py first)")
                continue
            transitions.append(row)
        payload["states"] = states
        payload["transitions"] = transitions

    if doctype == "Notification":
        kept = []
        for row in payload.get("recipients", []):
            role = row.get("receiver_by_role")
            if role and not frappe.db.exists("Role", role):
                results["skipped"].append(f"{name}: recipient role {role!r} missing - recipient dropped")
                continue
            kept.append(row)
        payload["recipients"] = kept

    if doctype == "Report":
        kept = []
        for row in payload.get("roles", []):
            role = row.get("role")
            if role and not frappe.db.exists("Role", role):
                results["skipped"].append(f"{name}: report role {role!r} missing - role row dropped")
                continue
            kept.append(row)
        payload["roles"] = kept

    return payload


def upsert(doctype, payload):
    name = payload.get("name")
    if not name:
        results["failed"].append(f"{doctype}: fixture entry has no name")
        return
    if frappe.db.exists(doctype, name):
        results["exists"].append(f"{doctype} {name}")
        return
    try:
        doc = frappe.get_doc(payload)
        # Honour the name from the fixture even for doctypes that autoname on
        # "Prompt" (Workflow State, Workflow Action Master, Issue Priority...),
        # which otherwise wait for interactive input.
        doc.flags.name_set = True
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        results["created"].append(f"{doctype} {name}")
    except Exception as exc:  # noqa: BLE001 - report every failure, keep going
        frappe.db.rollback()
        results["failed"].append(f"{doctype} {name}: {type(exc).__name__}: {exc}")


def main():
    if not os.path.isdir(FIXTURE_DIR):
        print(f"fixture directory not found: {FIXTURE_DIR}", file=sys.stderr)
        return 2

    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    frappe.set_user("Administrator")
    try:
        print(f"importing app fixtures on {SITE} (from {FIXTURE_DIR})\n")
        for filename in IMPORT_ORDER:
            path = os.path.join(FIXTURE_DIR, filename)
            if not os.path.exists(path):
                print(f"{filename}: not staged; skipping")
                continue
            docs = load(filename)
            print(f"{filename}: {len(docs)} record(s)")
            for doc in docs:
                doctype = doc.get("doctype")
                label = f"{doctype} {doc.get('name')}"
                if not doctype or not has_doctype(doctype):
                    results["skipped"].append(f"{label}: DocType not installed")
                    continue
                app_doctype = referenced_app_doctype(doc)
                if app_doctype:
                    results["skipped"].append(f"{label}: needs the app's {app_doctype!r}")
                    continue
                missing = missing_hard_link(doc)
                if missing:
                    results["skipped"].append(f"{label}: {missing} is not installed")
                    continue
                payload = clean_fields(doctype, doc)
                payload = prune_children(doctype, payload, doc.get("name"))
                upsert(doctype, payload)
        for filename, reason in SKIP_FILES.items():
            print(f"{filename}: skipped by design - {reason}")
    finally:
        frappe.destroy()

    print()
    print(f"created : {len(results['created'])}")
    for entry in results["created"]:
        print(f"          + {entry}")
    print(f"existed : {len(results['exists'])}")
    print(f"skipped : {len(results['skipped'])}")
    for entry in results["skipped"]:
        print(f"          - {entry}")
    if dropped_fields:
        print("fields dropped (not in this version): " + ", ".join(sorted(dropped_fields)))
    if adjusted_fields:
        print("fields adjusted: " + ", ".join(sorted(adjusted_fields)))
    if results["failed"]:
        print(f"FAILED  : {len(results['failed'])}", file=sys.stderr)
        for entry in results["failed"]:
            print(f"          ! {entry}", file=sys.stderr)
        return 1
    print("\napp fixtures imported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
