#!/usr/bin/env bash
# Seed the vendor master and each store's vendor links from
# fixtures/solrise_erp/vendors.csv and fixtures/solrise_erp/store_vendors.csv.
#
#   SITE_ENV=aws ./scripts/import_vendors.sh
#   SITE_ENV=local ./scripts/import_vendors.sh
#
# Idempotent: re-running updates in place and creates only what is missing, so it
# is safe to run after every deploy. See docs/20-vendors.md.
#
# The CSVs live on the control host; the importer runs inside the backend
# container, so this stages them into /tmp, which is not part of any volume and
# disappears when the container is recreated.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

VENDORS_SRC="${ROOT_DIR}/fixtures/solrise_erp/vendors.csv"
LINKS_SRC="${ROOT_DIR}/fixtures/solrise_erp/store_vendors.csv"
[ -f "${VENDORS_SRC}" ] || die "vendor list not found: ${VENDORS_SRC} (expected the file committed in the repo)"
[ -f "${LINKS_SRC}" ] || die "store vendor list not found: ${LINKS_SRC} (expected the file committed in the repo)"
VENDORS_DEST="/tmp/solrise-fixtures/vendors.csv"
LINKS_DEST="/tmp/solrise-fixtures/store_vendors.csv"

if ! compose exec -T backend bash -lc "test -d sites/${SITE_NAME}"; then
  die "backend container not running or site ${SITE_NAME} missing - bring the stack up first"
fi

log "staging the vendor lists into the backend container"
compose exec -T backend bash -lc "mkdir -p $(dirname "${VENDORS_DEST}")"
compose exec -T backend bash -lc "cat > ${VENDORS_DEST}" < "${VENDORS_SRC}"
compose exec -T backend bash -lc "cat > ${LINKS_DEST}" < "${LINKS_SRC}"

log "seeding vendors + store links (idempotent)"
compose exec -T \
  -e "SITE_NAME=${SITE_NAME}" \
  -e "VENDORS_CSV=${VENDORS_DEST}" \
  -e "STORE_VENDORS_CSV=${LINKS_DEST}" \
  backend bash -lc \
  'cd /home/frappe/frappe-bench/sites && ../env/bin/python -' < "${SCRIPT_DIR}/import_vendors.py"

log "done"
