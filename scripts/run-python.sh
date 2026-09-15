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
  backend bash -lc \
  'cd /home/frappe/frappe-bench/sites && ../env/bin/python -' < "${FILE}"
log "done"
