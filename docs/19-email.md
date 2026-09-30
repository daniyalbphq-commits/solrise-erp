# 19 - Email: mail server configuration

The site sends as **`info@solrisestores.com`** over **`mail.solrisestores.com:465`
(implicit TLS)**. This is the account Frappe uses for every notification, the
"Solrise New Ticket" mail to the Support Manager, and password resets.

Last updated: 2026-10-01 (verified locally with a real send).

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
