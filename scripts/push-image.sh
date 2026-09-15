#!/usr/bin/env bash
# Push the built Solrise image to its registry (Docker Hub in CI).
#
#   ./scripts/push-image.sh
#
# Reads CUSTOM_IMAGE / CUSTOM_TAG / CONTAINER_ENGINE from .env, so the same
# command works locally and in GitHub Actions. The workflow authenticates with
# the DOCKERHUB_USERNAME / DOCKERHUB_TOKEN secrets before calling this; to push
# by hand, log in first:
#   podman login --username "$DOCKERHUB_USERNAME" --password-stdin docker.io
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/lib.sh"

: "${CUSTOM_IMAGE:?CUSTOM_IMAGE is not set in .env}"
: "${CUSTOM_TAG:?CUSTOM_TAG is not set in .env}"

REF="${CUSTOM_IMAGE}:${CUSTOM_TAG}"
log "pushing ${REF}"

if "${ENGINE}" push "${REF}"; then
  log "pushed ${REF}"
else
  die "push failed. Is '${CUSTOM_IMAGE%%/*}' the registry, is the tag correct, and did you log in?"
fi
