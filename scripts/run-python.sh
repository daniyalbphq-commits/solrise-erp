#!/usr/bin/env bash
# Run a Python file inside the backend container against the live site.
# The script is piped to the bench virtualenv's python; it must be
# self-bootstrapping (see scripts/setup_erp.py for the pattern).
#   ./scripts/run-python.sh scripts/setup_erp.py
#   SITE_ENV=prod ./scripts/run-python.sh scripts/roles_rbac.py
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

FILE="${1:?usage: run-python.sh <python-file>}"
[ -f "${FILE}" ] || die "file not found: ${FILE}"

# Pass the media/S3 settings through: scripts/configure_s3_media.py reads them
# (nothing at runtime does - the app reads the site config file). Explicit `-e`
# flags rather than a loop: unset variables then simply arrive empty, and a
# value with spaces or slashes needs no quoting games.
log "executing ${FILE} against ${SITE_NAME} (${SITE_ENV})"
compose exec -T \
  -e "SITE_NAME=${SITE_NAME}" \
  -e "S3_MEDIA_BUCKET=${S3_MEDIA_BUCKET:-}" \
  -e "S3_MEDIA_REGION=${S3_MEDIA_REGION:-}" \
  -e "S3_MEDIA_ENDPOINT_URL=${S3_MEDIA_ENDPOINT_URL:-}" \
  -e "S3_MEDIA_FOLDER=${S3_MEDIA_FOLDER:-}" \
  -e "S3_MEDIA_PRESIGNED_EXPIRY=${S3_MEDIA_PRESIGNED_EXPIRY:-}" \
  -e "S3_MEDIA_VERIFY=${S3_MEDIA_VERIFY:-}" \
  -e "S3_MEDIA_WRITE_LOCAL=${S3_MEDIA_WRITE_LOCAL:-}" \
  -e "S3_MEDIA_MIGRATE_EXISTING=${S3_MEDIA_MIGRATE_EXISTING:-}" \
  -e "S3_MEDIA_MIGRATE_DRY_RUN=${S3_MEDIA_MIGRATE_DRY_RUN:-}" \
  -e "S3_MEDIA_MIGRATE_REMOVE_LOCAL=${S3_MEDIA_MIGRATE_REMOVE_LOCAL:-}" \
  -e "S3_MEDIA_ACCESS_KEY=${S3_MEDIA_ACCESS_KEY:-}" \
  -e "S3_MEDIA_SECRET_KEY=${S3_MEDIA_SECRET_KEY:-}" \
  -e "STORE_LOGINS_PASSWORD=${STORE_LOGINS_PASSWORD:-}" \
  -e "STORE_LOGINS_CSV=${STORE_LOGINS_CSV:-}" \
  -e "STORES_USER_DOMAIN=${STORES_USER_DOMAIN:-}" \
  -e "ASSISTANT_ENABLED=${ASSISTANT_ENABLED:-}" \
  -e "ASSISTANT_PROVIDER=${ASSISTANT_PROVIDER:-}" \
  -e "ASSISTANT_API_BASE_URL=${ASSISTANT_API_BASE_URL:-}" \
  -e "ASSISTANT_API_KEY=${ASSISTANT_API_KEY:-}" \
  -e "ASSISTANT_MODEL=${ASSISTANT_MODEL:-}" \
  -e "ASSISTANT_MAX_TOKENS=${ASSISTANT_MAX_TOKENS:-}" \
  -e "ASSISTANT_TEMPERATURE=${ASSISTANT_TEMPERATURE:-}" \
  -e "ASSISTANT_RATE_LIMIT_PER_HOUR=${ASSISTANT_RATE_LIMIT_PER_HOUR:-}" \
  -e "ASSISTANT_SYSTEM_PROMPT=${ASSISTANT_SYSTEM_PROMPT:-}" \
  -e "ASSISTANT_CHAT_FALLBACK=${ASSISTANT_CHAT_FALLBACK:-}" \
  -e "ASSISTANT_PROBE=${ASSISTANT_PROBE:-}" \
  -e "SUPPORT_ROUTING_USERS=${SUPPORT_ROUTING_USERS:-}" \
  -e "SUPPORT_ACCESS_ONLY_USERS=${SUPPORT_ACCESS_ONLY_USERS:-}" \
  -e "SUPPORT_MANAGER_ROLE=${SUPPORT_MANAGER_ROLE:-}" \
  -e "SUPPORT_RULE=${SUPPORT_RULE:-}" \
  backend bash -lc \
  'cd /home/frappe/frappe-bench/sites && ../env/bin/python -' < "${FILE}"
log "done"
