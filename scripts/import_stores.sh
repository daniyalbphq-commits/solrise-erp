#!/usr/bin/env bash
# Seed the store list (Customer + Address + Contact per store) from
# fixtures/solrise_erp/stores.csv.
#
#   SITE_ENV=aws ./scripts/import_stores.sh
#   SITE_ENV=local ./scripts/import_stores.sh
#
# Idempotent: re-running updates in place and creates only what is missing, so it
# is safe to run after every deploy. See docs/18-stores.md.
#
# The CSV lives on the control host; the importer runs inside the backend
# container, so this stages the file into /tmp, which is not part of any volume
# and disappears when the container is recreated.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

SRC="${ROOT_DIR}/fixtures/solrise_erp/stores.csv"
[ -f "${SRC}" ] || die "store list not found: ${SRC} (expected the file committed in the repo)"
DEST="/tmp/solrise-fixtures/stores.csv"

if ! compose exec -T backend bash -lc "test -d sites/${SITE_NAME}"; then
  die "backend container not running or site ${SITE_NAME} missing - bring the stack up first"
fi

log "staging the store list into the backend container (${DEST})"
compose exec -T backend bash -lc "mkdir -p $(dirname "${DEST}")"
compose exec -T backend bash -lc "cat > ${DEST}" < "${SRC}"

log "seeding stores (Customer + Address + Contact, idempotent)"
# An empty value means "create the records, skip the logins" - see STORES_DEFAULT_PASSWORD
# in docs/18 section 5.
compose exec -T \
  -e "SITE_NAME=${SITE_NAME}" \
  -e "STORES_CSV=${DEST}" \
  -e "STORES_DEFAULT_PASSWORD=${STORES_DEFAULT_PASSWORD:-}" \
  backend bash -lc \
  'cd /home/frappe/frappe-bench/sites && ../env/bin/python -' < "${SCRIPT_DIR}/import_stores.py"

log "done"
