# 19 - Email: mail server configuration

The site sends as **`info@solrisestores.com`** over **`mail.solrisestores.com:465`
(implicit TLS)**. This is the account Frappe uses for every notification, the
"Solrise New Ticket" mail to the Support Manager, and password resets.

Last updated: 2026-10-08. Section 6 (who support mail goes to) is new.

---

## 1. The trap this doc exists to prevent

In this Frappe version the outgoing SSL flag is **`use_ssl_for_outgoing`**;
the bare **`use_ssl`** field drives the *incoming* (IMAP/POP3) side only.

`EmailAccount.sendmail_config()` reads:

```python
"use_ssl": cint(self.use_ssl_for_outgoing),
"use_tls": cint(self.use_tls),
```

So setting `use_ssl = 1` for SMTP looks correct on the form and does nothing.
Frappe then dials port **465 in plaintext**, the server drops the connection, and
`validate_smtp_conn()` reports:

```
Invalid Outgoing Mail Server or Port: Connection unexpectedly closed: timed out
```

That message blames the network or the port. It is a one-field mistake.

| Direction | Field | Value for this server |
|---|---|---|
| Outgoing (SMTP 465) | `use_ssl_for_outgoing` | `1` |
| Outgoing | `use_tls` | `0` (do not start TLS on an already-TLS port) |
| Incoming (IMAP 993) | `use_ssl` | `1` |
| Incoming | `use_imap` | `1` |

## 2. Configuring it

The mailbox password is **never** committed and never written to `.env`. Pass it
in the environment:

```bash
# on the workstation, then the host - or straight on the host
SL_EMAIL_PASSWORD='...' SITE_ENV=aws ./scripts/configure_email.sh

# local stack
SL_EMAIL_PASSWORD='...' SITE_ENV=local ./scripts/configure_email.sh
```

`make mail` is the same thing. Overridable inputs:

| Variable | Default | Meaning |
|---|---|---|
| `SL_EMAIL_ID` | `info@solrisestores.com` | mailbox the ERP sends as and logs in as |
| `SL_EMAIL_PASSWORD` | *(required)* | that mailbox's password |
| `SL_SMTP_HOST` / `SL_SMTP_PORT` | `mail.solrisestores.com` / `465` | outgoing server, implicit TLS |
| `SL_ENABLE_INCOMING` | `0` | `1` also points the IMAP side at the same server |
| `SL_TEST_TO` | *(unset)* | send a real test message to this address |
| `SL_IMAP_HOST` / `SL_IMAP_PORT` | host / `993` | incoming server, implicit TLS |

It is **idempotent**: the one Email Account (`Solrise Support`) is updated in
place, so re-running after a password rotation is the supported path.

The script saves with `frappe.local.flags.in_install` set, which skips Frappe's
connection probe on save, then opens the SMTP session itself. That way a failure
reports the real server error instead of rolling back a save.

## 3. Verifying it

`SL_TEST_TO` makes the script send a real message, so a run that says `ok` has
proved the whole path:

```
account saved: Solrise Support (info@solrisestores.com)

--- outgoing SMTP probe ---
  server=mail.solrisestores.com port=465 use_ssl=1 use_tls=0 login=info@solrisestores.com
  ok: authenticated SMTP session over implicit TLS
  ok: test email sent to info@solrisestores.com
```

To check the site's own send path (not just the account), queue a message and
read its status:

```bash
podman exec -i solrise_backend_1 /home/frappe/frappe-bench/env/bin/python - <<'PY'
import os
os.chdir("/home/frappe/frappe-bench/sites")
import frappe
frappe.init(site="erp.solrise.online", sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
frappe.sendmail(recipients=["info@solrisestores.com"], subject="test",
                message="body", now=True)
print(frappe.db.get_all("Email Queue", fields=["name", "status", "error"],
                        order_by="creation desc", limit=1))
frappe.destroy()
PY
```

`status` must be `Sent` with `error` empty. `Error` means the account saved but
the session is still wrong.

## 4. Incoming mail (off by default)

IMAP 993 authenticates and lists folders on this server, but the account ships
with `enable_incoming = 0`, deliberately:

* `info@solrisestores.com` is a **shared business mailbox** - at the time of
  writing its INBOX held **303 unread messages**. Turning incoming on makes the
  scheduler pull them into the ERP and, because Frappe marks fetched mail as
  read, consume them from the mailbox everyone else uses.
* Incoming mail creates `Communication` records against Issues. That is only
  useful if a dedicated address feeds the ERP.

If you want it, use a dedicated mailbox (e.g. `support@`) and set
`SL_ENABLE_INCOMING=1`. Nothing else changes.

## 5. Operational notes

* **Rotate the password** by re-running the script; there is no second copy to
  update.
* **The mail server's TLS certificate expires 2026-10-02.** It was valid when
  this was written, but it is short-lived - renew it on the mail host, or
  outbound mail will start failing closed.
* The sender is the mailbox itself (`always_use_account_email_id_as_sender`), so
  the address on every ERP mail is `info@solrisestores.com` regardless of which
  user triggered it.
* Store managers' user logins use `@stores.invalid` placeholder addresses
  (`docs/18` section 5), so nothing can be *delivered* to them. Outgoing mail to
  the Support Manager works; per-manager mail needs real addresses in the store
  sheet.

## 6. Who support mail goes to

Every support mail is addressed by **role**, not by person. The two Issue
notifications (`Solrise New Ticket`, `Solrise SLA Breach Alert`) list
`receiver_by_role = Support Manager`, and `tasks.py`'s hourly stale-ticket nudge
and daily digest both resolve `_support_manager_recipients()` to that same role.
The `Solrise Support Routing` Assignment Rule is a fifth sender: it round-robins a
new Issue across a named user list and emails whoever it picks.

So the role was doing two jobs at once - permission *and* notification - and an
administrator who also worked tickets received every support mail. The two can be
separated, because `Support Team` and `Support Agent` grant the same `Issue`
permissions as `Support Manager` (read/write/create/share - see `install.py` and
the Custom DocPerm on `Issue`):

```bash
make support          # SUPPORT_* in the environment
```

`scripts/configure_support.py` leaves `Support Manager` to the one person who
answers the mail and points the Assignment Rule at that same person, while the
accounts that only need access keep `Support Team` / `Support Agent`. Defaults:
mail and routing to `umair.nawaz@solrisestores.com`; `daniyalbphq@gmail.com` and
`daniyal@solrise.com` keep access and lose the role. Idempotent, and it only ever
touches the users named in `SUPPORT_ROUTING_USERS` / `SUPPORT_ACCESS_ONLY_USERS`.

> **A Role Profile re-applies its roles on every User save**, so a role removed
> from the user alone comes straight back. `daniyal@solrise.com` is on the
> site-local `HR` profile, which listed `Support Manager`; the script removes it
> from the profile too, then re-saves the user. That is the trap here - the first
> run reported the role removed while the user still held it.

### 6.1 The alerts were nameless

`tasks._issue_fields()` built its field list by filtering candidates through
`meta.has_field(...)`, and **`name` is a virtual field**, so `has_field("name")`
is `False` and it was dropped. Every alert therefore read
`[Solrise] Ticket idle for 3 days: None`, the ticket link was empty,
`_stamp_breached` wrote to `None`, and `_collect_breached_issues` deduplicated on
`None` - so **SLA breach alerts never sent at all**. `_issue_fields()` now always
includes `name`, and `verify_app_layer.py` asserts it
(`alert queries carry the ticket name`).
