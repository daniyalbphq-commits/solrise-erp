"""
Solrise ERP - configure the outgoing (and optionally incoming) Email Account.

In this Frappe version the outgoing SSL flag is `use_ssl_for_outgoing`; the
plain `use_ssl` field only drives the IMAP/POP3 side. Setting `use_ssl=1` for
SMTP therefore silently does nothing, and Frappe dials port 465 in plaintext -
`Connection unexpectedly closed: timed out`. That mistake is easy to repeat, so
the account is created and then *proved* with a real SMTP send, not just a
connection probe.

Inputs (environment):
    SL_EMAIL_ID       mailbox / login, e.g. info@solrisestores.com
    SL_EMAIL_PASSWORD password for that mailbox
    SL_SMTP_HOST      default mail.solrisestores.com
    SL_SMTP_PORT      default 465 (implicit TLS)
    SL_IMAP_HOST      default SL_SMTP_HOST
    SL_IMAP_PORT      default 993 (implicit TLS)
    SL_ENABLE_INCOMING  "1" to also configure the IMAP side (default off)
    SL_TEST_TO        optional recipient for a real test email
    SITE_NAME         default erp.localhost

Run:  podman exec -i <backend> /home/frappe/frappe-bench/env/bin/python - < scripts/configure_email.py
"""
import os

import frappe

SITE = os.environ.get("SITE_NAME", "erp.localhost")
SITES_PATH = os.environ.get("SITES_PATH", "/home/frappe/frappe-bench/sites")

EMAIL_ID = os.environ.get("SL_EMAIL_ID", "info@solrisestores.com")
PASSWORD = os.environ.get("SL_EMAIL_PASSWORD", "")
SMTP_HOST = os.environ.get("SL_SMTP_HOST", "mail.solrisestores.com")
SMTP_PORT = os.environ.get("SL_SMTP_PORT", "465")
IMAP_HOST = os.environ.get("SL_IMAP_HOST", SMTP_HOST)
IMAP_PORT = os.environ.get("SL_IMAP_PORT", "993")
ENABLE_INCOMING = os.environ.get("SL_ENABLE_INCOMING") == "1"
TEST_TO = os.environ.get("SL_TEST_TO")

ACCOUNT_NAME = "Solrise Support"


def upsert_account():
    """Create or update the account, bypassing the connection probe.

    The probe is skipped on save and run explicitly below, so a failed probe
    reports the real SMTP error instead of turning into a save rollback.
    """
    frappe.local.flags.in_install = True
    try:
        if frappe.db.exists("Email Account", ACCOUNT_NAME):
            doc = frappe.get_doc("Email Account", ACCOUNT_NAME)
            doc.set("imap_folder", [])
        else:
            doc = frappe.new_doc("Email Account")

        doc.email_account_name = ACCOUNT_NAME
        doc.email_id = EMAIL_ID
        doc.login_id_is_different = 0
        doc.login_id = None
        doc.service = ""
        doc.auth_method = "Basic"
        doc.awaiting_password = 0
        doc.no_smtp_authentication = 0
        doc.ascii_encode_password = 0

        doc.enable_outgoing = 1
        doc.default_outgoing = 1
        doc.smtp_server = SMTP_HOST
        doc.smtp_port = str(SMTP_PORT)
        doc.use_ssl_for_outgoing = 1
        doc.use_tls = 0
        doc.always_use_account_email_id_as_sender = 1

        doc.enable_incoming = 1 if ENABLE_INCOMING else 0
        doc.use_imap = 1 if ENABLE_INCOMING else 0
        doc.use_ssl = 1 if ENABLE_INCOMING else 0
        doc.email_server = IMAP_HOST
        doc.incoming_port = str(IMAP_PORT)
        doc.email_sync_option = "UNSEEN"
        if ENABLE_INCOMING:
            doc.append("imap_folder", {"folder_name": "INBOX", "uidvalidity": ""})

        doc.password = PASSWORD
        doc.save(ignore_permissions=True)
    finally:
        frappe.local.flags.in_install = False
    frappe.db.commit()
    return doc


def prove_outgoing(doc):
    """Open a real SMTP session (and send, when a recipient is given)."""
    print("\n--- outgoing SMTP probe ---")
    config = doc.sendmail_config()
    print(f"  server={config['server']} port={config['port']} "
          f"use_ssl={config['use_ssl']} use_tls={config['use_tls']} "
          f"login={config['login']} timeout={config.get('timeout')}")
    if not config["use_ssl"]:
        print("  FAIL: use_ssl is 0 - Frappe would dial the port in plaintext")
        return False

    from frappe.email.smtp import SMTPServer

    server = SMTPServer(**config)
    with server.session:
        pass
    print("  ok: authenticated SMTP session over implicit TLS")

    if TEST_TO:
        server = SMTPServer(**config)
        # `session` is the underlying smtplib.SMTP object; SMTPServer is a wrapper.
        session = server.session
        session.sendmail(doc.email_id, [TEST_TO], _message(doc))
        server.quit()
        print(f"  ok: test email sent to {TEST_TO}")
    return True


def prove_incoming(doc):
    """Open a real IMAP session over implicit TLS."""
    print("\n--- incoming IMAP probe ---")
    print(f"  server={doc.email_server} port={doc.incoming_port} "
          f"use_imap={doc.use_imap} use_ssl={doc.use_ssl}")
    try:
        # in_receive=True keeps the connection open (otherwise it is logged out).
        server = doc.get_incoming_server(in_receive=True)
        typ, folders = server.imap.list()
        names = sorted(
            line.decode(errors="replace").rsplit(' ", ', 1)[-1].strip('"')
            for line in folders
        )
        print(f"  ok: authenticated IMAP session; {len(names)} folder(s)")
        print(f"  folders: {names[:8]}")
        server.imap.select("INBOX")
        typ, data = server.imap.search(None, "UNSEEN")
        unseen = len((data[0] or b"").split())
        print(f"  ok: INBOX selectable, {unseen} unseen message(s)")
        server.imap.logout()
        return True
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return False


def _message(doc):
    from email.mime.text import MIMEText
    from email.utils import formatdate, make_msgid

    body = (
        "This is a test message from Solrise ERP.\n\n"
        "If you are reading this, outgoing mail is configured.\n"
    )
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = "Solrise ERP - test email"
    msg["From"] = doc.email_id
    msg["To"] = TEST_TO
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=doc.email_id.split("@", 1)[1])
    return msg.as_string()


def main():
    # The container has no site context, so frappe's log paths resolve relative
    # to the cwd; without this it looks for /home/frappe/logs/database.log.
    os.chdir(SITES_PATH)
    frappe.init(site=SITE, sites_path=SITES_PATH)
    frappe.connect()
    try:
        if not PASSWORD:
            print("SL_EMAIL_PASSWORD is not set")
            return 1
        doc = upsert_account()
        print(f"account saved: {doc.name} ({doc.email_id})")
        ok = prove_outgoing(doc)
        if ENABLE_INCOMING:
            ok = prove_incoming(doc) and ok
        return 0 if ok else 1
    finally:
        frappe.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
