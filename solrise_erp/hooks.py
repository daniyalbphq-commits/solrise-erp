# Copyright (c) 2026, Solrise and contributors
# For license information, please see license.txt
"""Hooks for the Solrise application layer.

Every entry is a dotted path, so a problem inside one feature can never stop the
app from loading: `bench migrate` and the Desk survive it, and the failure shows
up in the Error Log instead.

`docs/16-deployment-pipeline-status.md` section 5 in the deployment repository is
the per-feature acceptance list this file serves.
"""

app_name = "solrise_erp"
app_title = "Solrise ERP"
app_publisher = "Solrise"
app_description = (
    "White labeling, RBAC, approval workflows, notifications, reporting, the LLM "
    "assistant and the universal chat entry flow."
)
app_email = "daniyalbphq@gmail.com"
app_license = "mit"
# The lockup from solrisestores.com, resized to a 512px-wide PNG.
app_logo_url = "/assets/solrise_erp/images/solrise-logo.png"
source_link = "https://github.com/daniyalbphq-commits/solrise-erp"

# --- lifecycle -------------------------------------------------------------------
after_install = "solrise_erp.install.after_install"
after_migrate = "solrise_erp.install.after_migrate"

# --- assets ----------------------------------------------------------------------
# solrise_chat.js is the shared Desk + Portal widget; solrise_erp.js is the small
# boot patch that keeps the upstream product name out of the Desk. Portal needs
# web_include_js - app_include_js does not load there (docs/12 section 0, item 3).
app_include_js = [
    "/assets/solrise_erp/js/solrise_erp.js",
    "/assets/solrise_erp/js/solrise_chat.js",
]
web_include_js = ["/assets/solrise_erp/js/solrise_chat.js"]

# --- boot ------------------------------------------------------------------------
# Rewrites the app title/logo the platform attaches after boot_session (docs/10).
boot_session = "solrise_erp.boot.boot_session"

# --- row-level permissions -------------------------------------------------------
# Row filters narrow the list views; has_permission closes the direct-link path for
# a Support Agent on somebody else's Issue (docs/11 sections 3.1, 4 and 10).
permission_query_conditions = {
    "Issue": "solrise_erp.permissions.issue_query_conditions",
    "Leave Application": "solrise_erp.permissions.leave_application_query_conditions",
    "Expense Claim": "solrise_erp.permissions.expense_claim_query_conditions",
}
has_permission = {
    "Issue": "solrise_erp.permissions.issue_has_permission",
}

# --- assistant -------------------------------------------------------------------
# Two-key gate: a tool must exist in solrise_erp.assistant.tools.TOOL_REGISTRY *and*
# its method must be listed here, or dispatch() refuses to run it (docs/06 section
# 4.4). Forgetting an entry is a deliberate failure mode.
assistant_allowed_methods = [
    "solrise_erp.api.v1.ask",
    "solrise_erp.api.v1.lookup",
    "solrise_erp.api.v1.ticket_status",
    "solrise_erp.api.v1.create_ticket",
    "solrise_erp.api.v1.search_faq",
    "solrise_erp.api.v1.navigation_hint",
]

# --- scheduled work --------------------------------------------------------------
# Run by the `scheduler` container. Every job is idempotent and cooldown-guarded.
scheduler_events = {
    "cron": {
        "*/15 * * * *": ["solrise_erp.tasks.check_sla_breaches"],
        "0 * * * *": ["solrise_erp.tasks.escalate_stale_tickets"],
    },
    "daily": [
        "solrise_erp.tasks.send_support_digest",
        "solrise_erp.tasks.purge_old_logs",
    ],
}

# --- fixtures --------------------------------------------------------------------
# Used by `make fixtures` in the deployment repository; the exported files live
# there under fixtures/solrise_erp/ and are imported on a fresh site by migrate.
# The filters are deliberately tight - an unfiltered "Role" would export every
# role the platform ships.
SOLRISE_ROLES = [
    "Support Agent",
    "Support Manager",
    "CRM User",
    "CRM Manager",
    "Finance Approver",
    "Solrise Admin",
    "Department Head",
    "Solrise Super Admin",
]

fixtures = [
    {"dt": "Role", "filters": [["name", "in", SOLRISE_ROLES]]},
    {"dt": "Custom DocPerm", "filters": [["role", "in", SOLRISE_ROLES]]},
    {"dt": "Workflow", "filters": [["name", "like", "Solrise %"]]},
    {"dt": "Notification", "filters": [["name", "like", "Solrise %"]]},
    {"dt": "Report", "filters": [["report_name", "like", "Solrise %"]]},
    {"dt": "Dashboard", "filters": [["name", "like", "Solrise %"]]},
    {"dt": "Dashboard Chart", "filters": [["name", "like", "Solrise %"]]},
    {"dt": "Assignment Rule", "filters": [["name", "like", "Solrise %"]]},
    {"dt": "Service Level Agreement", "filters": [["document_type", "=", "Issue"]]},
    "Solrise FAQ",
]
