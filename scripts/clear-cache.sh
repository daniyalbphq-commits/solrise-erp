#!/usr/bin/env bash
# Clear a site's cache after a rollout, so the hashed asset filenames from the
# new image are picked up.
#
#   SITE_ENV=aws ./scripts/clear-cache.sh
#
# Why this exists: `bench build` hashes every asset filename and writes the
# manifest to `sites/assets/assets.json` *in the image*. Frappe caches that
# manifest in Redis under `assets_json`. A rollout recreates the app containers on
# the new image but deliberately leaves Redis running - so the cache still holds
# the *old* hashes, every page asks for files that no longer exist, nginx answers
# `/assets/*.css` with its HTML fallback, and the browser refuses it
# ("MIME type ('text/html') is not a supported stylesheet MIME type"). The site
# then renders with no CSS at all.
#
# `bench clear-cache` deletes `assets_json` (`frappe.cache_manager.bench_cache_keys`)
# along with the site and website caches; the next request re-reads the manifest
# from the image. The rollout, redeploy and deploy paths call this for you.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

# The backend may still be starting right after `up -d`.
for attempt in 1 2 3 4 5; do
  if compose exec -T backend bench --site "${SITE_NAME}" clear-cache >/dev/null 2>&1; then
    log "cleared the site + asset caches (${SITE_NAME})"
    exit 0
  fi
  sleep 3
done

log "warning: could not clear the cache (is the backend up?). If the site renders"
log "         without CSS, run: compose exec -T backend bench --site ${SITE_NAME} clear-cache"
exit 0
