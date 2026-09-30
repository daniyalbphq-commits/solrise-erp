#!/usr/bin/env bash
# Configure the outgoing (and optionally incoming) mail server for the site.
#
#   SL_EMAIL_PASSWORD='...' SITE_ENV=aws ./scripts/configure_email.sh
#   SL_EMAIL_PASSWORD='...' SITE_ENV=local ./scripts/configure_email.sh
#
# Idempotent: re-running updates the one Email Account in place, so it is safe
# after every deploy and after a password rotation. The password is read from the
# environment and never written to the repo or to the host's .env - see
# docs/19-email.md.
#
# Its value is the mailbox the ERP sends as (default info@solrisestores.com) and
# logs in as. Set SL_ENABLE_INCOMING=1 to also point the IMAP side at the same
# server; leave it unset to receive nothing, which is the safe default on a shared
# mailbox (Frappe marks whatever it reads as read).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

: "${SL_EMAIL_PASSWORD:?SL_EMAIL_PASSWORD is not set - export the mailbox password (it is never stored in the repo)}"

SMTP_HOST="${SL_SMTP_HOST:-mail.solrisestores.com}"
SMTP_PORT="${SL_SMTP_PORT:-465}"
EMAIL_ID="${SL_EMAIL_ID:-info@solrisestores.com}"

if ! compose exec -T backend bash -lc "test -d sites/${SITE_NAME}"; then
  die "backend container not running or site ${SITE_NAME} missing - bring the stack up first"
fi

log "configuring the mail account ${EMAIL_ID} on ${SITE_NAME} (outgoing ${SMTP_HOST}:${SMTP_PORT})"
compose exec -T \
  -e "SITE_NAME=${SITE_NAME}" \
  -e "SL_EMAIL_ID=${EMAIL_ID}" \
  -e "SL_EMAIL_PASSWORD=${SL_EMAIL_PASSWORD}" \
  -e "SL_SMTP_HOST=${SMTP_HOST}" \
  -e "SL_SMTP_PORT=${SMTP_PORT}" \
  -e "SL_ENABLE_INCOMING=${SL_ENABLE_INCOMING:-0}" \
  -e "SL_TEST_TO=${SL_TEST_TO:-}" \
  backend bash -lc \
  'cd /home/frappe/frappe-bench/sites && ../env/bin/python -' < "${SCRIPT_DIR}/configure_email.py"

log "done"
