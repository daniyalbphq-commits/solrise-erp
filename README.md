# Solrise ERP — the Solrise application layer

The custom Frappe app behind the Solrise deployment: **white labeling**, the
**RBAC** matrix and row-level rules, approval workflows, notifications, reporting,
the **LLM assistant** ("Ask Solrise") and the **universal chat entry flow**
(Desk + Portal).

It is built into the deployment image from `apps.json` (`${SOLRISE_APP_URL}` /
`${SOLRISE_APP_BRANCH}`) and installed on the site by `scripts/create-site.sh`.
Nothing about the deployment lives in here — see the deployment repository
(`daniyalbphq-commits/solrise-erp`) for Terraform, Ansible, compose and docs.

**This is a rebuild.** The original app's source was lost; this version was
reconstructed from `docs/06`, `docs/07`, `docs/10`, `docs/11` and `docs/12` of
that repository, which describe its behaviour in detail. Field names, settings
and endpoints match those documents so the deployment, the fixtures in
`fixtures/solrise_erp/` and the documented commands keep working. What is
deliberately *not* here is listed at the bottom.

## What it delivers

| Feature | Where |
|---|---|
| White labeling, US/USD locale, Company rename, workspace labels | `solrise_erp/branding.py` (+ `boot.py`, `public/js/solrise_erp.js`) |
| Settings surface (provider, guardrails, chat flags, retention) | `Solrise Settings` (Single) |
| LLM assistant: tools, two-key gate, rate limit, audit | `solrise_erp/assistant/`, `api/v1.py`, `Solrise Chat Log` |
| Universal chat: intent → permission gate → slot filling → execute → audit | `solrise_erp/chat/`, `api/chat.py`, `Solrise AI Audit Log` |
| The widget, shared by Desk and Portal | `public/js/solrise_chat.js` (`app_include_js` + `web_include_js`) |
| Row-level permissions (Issue, Leave Application, Expense Claim) | `solrise_erp/permissions.py` |
| Scheduled SLA / stale-ticket / digest / log-purge jobs | `solrise_erp/tasks.py` |
| Knowledge base | `Solrise FAQ` (with the author-curated `keywords` search) |
| Messaging data model (no dispatcher yet) | `Solrise Message Log`, `Solrise Notification Channel` |

The chat package is the largest piece and is layered so each concern can be read on
its own:

| Module | Concern |
|---|---|
| `chat/registry.py` | The closed vocabulary: DocTypes, allowed actions, module aliases, synonyms, naming-series fallbacks |
| `chat/nlp.py` | The pure parser (no `frappe` import) — text to intent, with a confidence score |
| `chat/intent.py` | Frappe-aware resolution: real prefixes from metadata, the Redis pending intent, slot answers, the question to ask next |
| `chat/context.py` | Who is asking, and on which channel (Desk / Portal / API) |
| `chat/menu.py` | Quick actions built from permissions, never from a role list |
| `chat/permissions.py` | The single authorization choke-point (`check` / `authorize` / `has_permission` + row rules) |
| `chat/schema.py` | Required-field inspection and answer validation via `frappe.get_meta` |
| `chat/executor.py` | The only module that touches records; every write goes through the Document API |
| `chat/workflow.py` | Approval transitions (`get_transitions` → `apply_workflow`) |
| `chat/audit.py` | The append-only `Solrise AI Audit Log` writer, with credential redaction |
| `api/chat.py` | The two whitelisted endpoints the widget calls |

## Install (normally the deploy does this)

```bash
bench get-app https://github.com/daniyalbphq-commits/solrise-erp --branch solrise_erp-app
bench --site <site> install-app solrise_erp
bench --site <site> migrate          # runs after_migrate -> apply_all()
bench --site <site> clear-cache      # asset manifest + permissions cache
```

## Configure

`Solrise Settings` in the Desk, or programmatically — see
`infra/README.md` §7.1 in the deployment repo. `enabled` + `api_base_url` +
`api_key` are all the assistant needs; the chat works deterministically without
any provider.

## Fixtures

`solrise_erp/fixtures/` ships the records the app owns, so a fresh site is
configured by `bench migrate` alone: the Solrise roles, the four workflows (with
their states and action masters), the notifications, the reports, the dashboard and
its charts, the Issue SLA, the assignment rule and the FAQ entry.

**`custom_docperm.json` is deliberately not shipped.** A `Custom DocPerm` row for a
DocType *replaces* that DocType's shipped permissions, and the tight export filter
only carries the Solrise roles — importing it would leave `System Manager` and `All`
without the access they need (`docs/11-rbac.md` section 0, rule 3). The permission
matrix is applied by `scripts/roles_rbac.py` in the deployment repository; the
exported file stays there as the backup, which is what `docs/11` section 5.4 calls
it.

**Nor is `is_standard` set on anything.** A fixture that claims to be standard is
refused outside developer mode — `Dashboard Chart.validate` throws "Cannot edit
Standard charts" — so a `bench migrate` on the deployed site stops there. Re-export
the fixtures (which marks the records standard when it runs in developer mode) and
clear `is_standard` before committing them here. `Solrise Open Tickets by Status`
and its four siblings were exactly that failure.

## Verify

```bash
bench --site <site> execute solrise_erp.api.v1.health          # assistant + chat status
bench --site <site> execute solrise_erp.api.chat.bootstrap     # the menu this user sees
cd apps/solrise_erp && python3 -m unittest discover -s solrise_erp/tests
```

The unit tests import no Frappe, so they run anywhere; `test_chat_guardrails.py`
also greps the package for the banned data primitives of `docs/12` section 7.3.

## Not in this rebuild

* **SMS / WhatsApp sending.** The two DocTypes exist, but there is no channel
  dispatcher, so `Solrise Message Delivery` stays empty.
* **Browser automation for the widget.** `public/js/solrise_chat.js` is syntax-
  checked and every endpoint it calls is tested, but the click-through needs a
  human (see `docs/12` section 8.2).
* **Sidebar workspace records** of its own (branding renames upstream ones).
* **Exact parity** with the lost original in wording, prompts and edge cases.

## License

MIT — see `license.txt`. Frappe Framework, ERPNext and HRMS are MIT licensed too;
rebranding the presentation is permitted, removing their copyright notices is not
(`docs/10-branding.md` section 5).
