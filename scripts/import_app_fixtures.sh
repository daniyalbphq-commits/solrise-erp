#!/usr/bin/env bash
# Import the Solrise app's exported fixtures that do not need the app itself
# (approval workflows, notifications, reports, dashboards).
#
#   SITE_ENV=aws ./scripts/import_app_fixtures.sh
#
# The fixture JSON lives on the control host; the importer runs inside the
# backend container, so this stages the files into the container first (into
# /tmp, which is not part of any volume and disappears on recreate).
#
# Idempotent - see scripts/import_app_fixtures.py. Roles/permissions and the
# SLA/assignment rule are NOT imported here: scripts/roles_rbac.py and
# scripts/setup_erp.py own those and run as part of the deploy.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

SRC_DIR="${ROOT_DIR}/fixtures/solrise_erp/fixtures"
[ -d "${SRC_DIR}" ] || die "fixtures not found: ${SRC_DIR}"
DEST_DIR="/tmp/solrise-fixtures"

if ! compose exec -T backend bash -lc "test -d sites/${SITE_NAME}"; then
  die "backend container not running or site ${SITE_NAME} missing - bring the stack up first"
fi

log "staging fixtures into the backend container (${DEST_DIR})"
compose exec -T backend bash -lc "rm -rf ${DEST_DIR} && mkdir -p ${DEST_DIR}"
for file in "${SRC_DIR}"/*.json; do
  compose exec -T backend bash -lc "cat > ${DEST_DIR}/$(basename "${file}")" < "${file}"
done

log "importing app fixtures"
compose exec -T \
  -e "SITE_NAME=${SITE_NAME}" \
  -e "FIXTURE_DIR=${DEST_DIR}" \
  backend bash -lc \
  'cd /home/frappe/frappe-bench/sites && ../env/bin/python -' < "${SCRIPT_DIR}/import_app_fixtures.py"

log "done"
