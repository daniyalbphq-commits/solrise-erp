#!/usr/bin/env bash
# Make the apps the image carries known to the bench, and refresh the caches that
# decide which of them Frappe actually loads.
#
#   SITE_ENV=aws ./scripts/register-apps.sh
#
# Why this exists - two failures, one cause. `sites/` is a named volume, so it
# SHADOWS the image's own sites/apps.txt, and the volume's copy was written the
# first time the stack came up. An app a later image bakes in is therefore present
# under apps/ but unknown to bench:
#
#   * `bench install-app <app>` stops with "App <app> not in apps.txt", so the
#     deploy cannot install it at all; and
#   * less obviously, `app_include_js` / `web_include_js` from that app are
#     ignored even once it is installed, because Frappe intersects the site's
#     installed apps with `get_all_apps()` - which reads this file - before it
#     loads any hooks. Every chat endpoint answers and the widget still never
#     loads.
#
# `app_hooks`, `all_apps` and `installed_apps` are bench-wide caches, so the file
# alone is not enough: the last step clears them, which is also what makes a new
# image's asset manifest (assets_json) take effect.
#
# Idempotent, and called by create-site.sh and by the rollout targets.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

# The app list comes from the image's apps/ directory rather than from .env, so an
# app that was baked in is registered even if INSTALL_APPS was not updated.
compose exec -T backend bash -lc '
  set -euo pipefail
  cd /home/frappe/frappe-bench
  for dir in apps/*/; do
    app="$(basename "$dir")"
    [ -f "${dir}${app}/hooks.py" ] || continue
    if ! grep -qx "$app" sites/apps.txt 2>/dev/null; then
      printf "%s\n" "$app" >> sites/apps.txt
      echo "  registered ${app} in sites/apps.txt"
    fi
    if [ -d "${dir}${app}/public" ] && [ ! -e "assets/${app}" ]; then
      ln -sfn "/home/frappe/frappe-bench/${dir}${app}/public" "assets/${app}"
      echo "  linked ${app} assets"
    fi
  done'

compose exec -T backend bench --site "${SITE_NAME}" execute \
  frappe.cache_manager.clear_global_cache >/dev/null

log "apps registered (sites/apps.txt) and global caches cleared"
