#!/usr/bin/env bash
# Bring the stack up, then create the site and install apps (idempotent).
#   SITE_ENV=local ./scripts/create-site.sh
#   SITE_ENV=prod  ./scripts/create-site.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

log "stack: ${SITE_ENV} (${COMPOSE_FILE})"
log "starting services ..."
compose up -d

# Only wait for an *embedded* MariaDB. With an external database (RDS) there is
# no mariadb service, and the TCP endpoint is reachable long before the backend
# starts talking to it.
if grep -qE '^  mariadb:' "${COMPOSE_FILE}"; then
  log "waiting for mariadb to report healthy ..."
  # NOTE: `podman-compose ps` takes no service argument, so filter the output.
  for _ in $(seq 1 60); do
    if compose ps 2>/dev/null | grep -i mariadb | grep -qi healthy; then
      break
    fi
    sleep 5
  done
else
  log "external database (no embedded mariadb service) - skipping local db wait"
fi

log "running create-site (idempotent) ..."
# `create-site` sits behind the `init` profile (it must not start with the stack),
# so the profile has to be named - otherwise podman-compose reports it as a
# missing service and the deploy stops here. restore.sh already does this.
compose --profile init run --rm create-site

# Install the apps the site is missing. The create-site service only installs
# INSTALL_APPS when it *creates* the site, so an app added to the image later
# (solrise_erp - the white-label branding, RBAC, assistant and chat layer - or
# cloud_storage) would be baked in and never installed on an existing site.
# Checked explicitly rather than relying on `bench install-app` being a no-op, so
# a re-run can never re-execute an app's after_install hooks.
#
# Register first: `sites/` is a volume (so the site survives an image rebuild),
# which SHADOWS the image's own sites/apps.txt. An app the rebuild baked in is
# therefore present under apps/ but unknown to bench, and `bench install-app`
# stops with "App <name> not in apps.txt". scripts/register-apps.sh writes the
# name into the bench's apps.txt, links the app's public/ into assets and clears
# the bench-wide caches that would otherwise keep the app invisible.
log "registering the apps the image carries ..."
"${SCRIPT_DIR}/register-apps.sh"

log "checking apps (INSTALL_APPS=${INSTALL_APPS:-})"
for app in $(printf '%s' "${INSTALL_APPS:-}" | tr ',' ' '); do
  if compose exec -T backend bench --site "${SITE_NAME}" list-apps 2>/dev/null | tr -d '\r' | grep -qx "${app}"; then
    log "app installed: ${app}"
  else
    log "installing app on the existing site: ${app}"
    compose exec -T backend bench --site "${SITE_NAME}" install-app "${app}"
  fi
done

# Bring the database up to date with the code in the image. CI rebuilds the image
# whenever apps.json or the build machinery changes and the apps track their
# branches, so a redeploy can carry schema changes and patches the site has not
# seen yet - without this the site would run new code against an old schema.
# Idempotent, and a no-op on a freshly created site.
log "applying pending migrations ..."
compose exec -T backend bench --site "${SITE_NAME}" migrate

log "installed apps:"
compose exec -T backend bench --site "${SITE_NAME}" list-apps

# Apply the programmatic configuration. Both scripts are idempotent, so this is
# safe on a brand-new site and on every re-run of `make site`. Order matters:
# roles_rbac.py must run *after* the fixtures sync (done during install-app /
# migrate) so it recreates the shipped-role Custom DocPerm rows it preserves -
# without it, the tight Solrise fixture filter would leave those roles without
# access to the DocTypes it manages. Set SKIP_CONFIG=1 to skip both.
if [ "${SKIP_CONFIG:-0}" = "1" ]; then
  log "SKIP_CONFIG=1 - leaving setup_erp.py / roles_rbac.py to the operator"
else
  log "applying programmatic configuration (idempotent) ..."
  "${SCRIPT_DIR}/run-python.sh" scripts/setup_erp.py
  "${SCRIPT_DIR}/run-python.sh" scripts/roles_rbac.py
fi

log "done. Site: ${SITE_NAME}"
if [ "${SITE_ENV}" = "local" ]; then
  log "open: http://localhost:${HTTP_PORT:-8080}"
else
  log "open: https://${DOMAIN}"
fi
