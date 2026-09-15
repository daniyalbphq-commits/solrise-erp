#!/usr/bin/env bash
# Point Frappe's file storage at the S3 media bucket (idempotent).
#
#   SITE_ENV=aws ./scripts/setup-media.sh
#   SITE_ENV=aws S3_MEDIA_MIGRATE_EXISTING=1 ./scripts/setup-media.sh
#
# The deploy runs this for you (infra/ansible/roles/solrise). It expects the
# stack to be up and the site to exist, and reads the S3_MEDIA_* settings that
# Ansible renders into .env.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

BUCKET="${S3_MEDIA_BUCKET:-}"
FOLDER="${S3_MEDIA_FOLDER:-}"
if [ -z "${BUCKET}" ]; then
  log "S3_MEDIA_BUCKET is empty - uploaded files stay on the volume (nothing to do)"
  exit 0
fi
log "media bucket: s3://${BUCKET} (${S3_MEDIA_REGION:-?}), folder=${FOLDER:-<bucket root>}"

if ! compose exec -T backend bash -lc "test -d sites/${SITE_NAME}"; then
  die "backend container not running or site ${SITE_NAME} missing - run 'make aws-up' and create the site first"
fi

# `install_apps` only runs on the *first* site creation, so a deployment that
# existed before the media bucket needs the app installed explicitly.
if compose exec -T backend bench --site "${SITE_NAME}" list-apps | grep -qx "cloud_storage"; then
  log "cloud_storage already installed on ${SITE_NAME}"
else
  log "installing cloud_storage on ${SITE_NAME} ..."
  compose exec -T backend bench --site "${SITE_NAME}" install-app cloud_storage
fi

# Writes the site config, round-trips a probe object, and migrates if asked.
"${SCRIPT_DIR}/run-python.sh" scripts/configure_s3_media.py

log "media storage ready: new uploads go to s3://${BUCKET}/${FOLDER}"
