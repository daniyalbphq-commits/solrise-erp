#!/usr/bin/env bash
# Publish the Solrise app as its own branch of a repository, in the layout
# `bench get-app` needs: the ROOT of the branch must be the Frappe app.
#
#   ./scripts/publish-app.sh /path/to/solrise_erp
#   DRY_RUN=1 ./scripts/publish-app.sh /path/to/solrise_erp
#   PUBLISH_REMOTE=git@github.com:owner/repo.git PUBLISH_BRANCH=main \
#     ./scripts/publish-app.sh /path/to/solrise_erp
#
# Run it on the machine that has the checkout - this repo's deploy never needs
# the app's source, only the branch it produces. Nothing in the checkout is
# modified:
#
#   * checkout is a git repo -> pushes its HEAD to <branch> (history kept)
#   * plain directory       -> clones the remote into a temp dir, replaces the
#                              tree there, commits and pushes
#
# Pushing needs write access to the remote. On this project's remote the default
# SSH key authenticates as the wrong account, so either use the alias form
# (`git@github-bphq:...`) or:
#   GIT_SSH_COMMAND="ssh -i ~/.ssh/id_ed25519_bphq" ./scripts/publish-app.sh ...
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SRC="${1:?usage: publish-app.sh <path-to-solrise_erp-checkout>}"
[ -d "${SRC}" ] || { echo "ERROR: not a directory: ${SRC}" >&2; exit 2; }
SRC="$(cd "${SRC}" && pwd -P)"

REMOTE="${PUBLISH_REMOTE:-git@github.com:daniyalbphq-commits/solrise-erp.git}"
BRANCH="${PUBLISH_BRANCH:-solrise_erp-app}"
DRY="${DRY_RUN:-0}"

log() { printf '[publish-app] %s\n' "$*"; }
die() { printf '[publish-app:error] %s\n' "$*" >&2; exit 1; }

# --- 1. is this the app, and is its root the app root? ------------------------
[ -f "${SRC}/pyproject.toml" ] || [ -f "${SRC}/setup.py" ] || die \
"${SRC} has no pyproject.toml/setup.py - that is the app *package* directory, not
the app repository root. Point at the directory that holds solrise_erp/ (the one
you would run 'bench get-app' on)."

HOOKS="$(find "${SRC}" -maxdepth 2 -name hooks.py | head -1)"
[ -n "${HOOKS}" ] || die "${SRC} has no hooks.py within two levels - not a Frappe app."
APP_NAME="$(grep -h '^app_name' "${HOOKS}" 2>/dev/null | head -1 || true)"
[ -n "${APP_NAME}" ] || die "${HOOKS} declares no app_name."
log "app: ${APP_NAME}"

if [ -x "${SCRIPT_DIR}/check-app-source.sh" ]; then
  "${SCRIPT_DIR}/check-app-source.sh" "${SRC}" || true
  echo
fi

# --- 2. publish ---------------------------------------------------------------
# `git -C` walks up to the enclosing repository, so "is this a git checkout?" has
# to mean "is this directory that repository's root?". The usual layout here is
# the app *inside* the deployment repository (gitignored), where the naive test
# would push the whole deployment repository as the app branch - the one mistake
# this script must never make.
REPO_TOP="$(git -C "${SRC}" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "${REPO_TOP}" ] && [ "$(cd "${REPO_TOP}" && pwd -P)" = "${SRC}" ]; then
  DIRTY="$(git -C "${SRC}" status --porcelain | wc -l)"
  [ "${DIRTY}" -eq 0 ] || log "note: ${DIRTY} uncommitted change(s) in the checkout will NOT be published"
  log "pushing $(git -C "${SRC}" rev-parse --short HEAD) -> ${REMOTE} ${BRANCH}"
  if [ "${DRY}" = "1" ]; then
    log "DRY_RUN=1 - not pushing"
  else
    git -C "${SRC}" push "${REMOTE}" "HEAD:refs/heads/${BRANCH}"
  fi
else
  command -v tar >/dev/null || die "tar is required for the non-git case"
  TMP="$(mktemp -d)"
  trap 'rm -rf "${TMP}"' EXIT
  # Prefer to add a commit to the branch that is already there. Publishing a fresh
  # orphan instead would rewrite the branch's history on every run, and the push
  # would be rejected as non-fast-forward the second time anyone published.
  # A shallow clone is enough for either path.
  if git clone --quiet --depth 1 --branch "${BRANCH}" "${REMOTE}" "${TMP}/repo" 2>/dev/null; then
    log "app branch ${BRANCH} exists - adding a commit on top of it"
    cd "${TMP}/repo"
  else
    log "app branch ${BRANCH} does not exist yet - creating it from ${REMOTE}"
    git clone --quiet --depth 1 "${REMOTE}" "${TMP}/repo" || die "could not clone ${REMOTE} (does it exist, and do you have access?)"
    cd "${TMP}/repo"
    git checkout --quiet --orphan "${BRANCH}"
  fi
  # The branch is a snapshot of ${SRC}: clear the tree first, so a file the app no
  # longer has is removed here too, then let `git add -A` stage both halves of the
  # diff.
  find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
  ( cd "${SRC}" && tar -cf - \
      --exclude=.git --exclude=__pycache__ --exclude=node_modules \
      --exclude='*.pyc' --exclude=.env . ) | tar -xf - -C "${TMP}/repo"
  git add -A
  if git diff --cached --quiet; then
    log "nothing changed - ${BRANCH} already matches ${SRC}"
  else
    git commit -q -m "Solrise app snapshot from ${SRC}"
    log "committed $(git rev-parse --short HEAD): $(git ls-tree -r --name-only HEAD | wc -l) file(s)"
  fi
  if [ "${DRY}" = "1" ]; then
    log "DRY_RUN=1 - not pushing (branch ${BRANCH} exists only in ${TMP})"
    trap - EXIT
    log "inspect it with: git -C ${TMP}/repo log --stat -1"
  else
    git push --quiet origin "HEAD:refs/heads/${BRANCH}"
    log "pushed ${BRANCH}"
  fi
fi

# --- 3. what to set next ------------------------------------------------------
case "${REMOTE}" in
  *@*:*|ssh://*|https://*|http://*)
    HTTPS_URL="$(printf '%s' "${REMOTE}" \
      | sed -E 's#^git@github-[^:]+:#https://github.com/#; s#^git@([^:]+):#https://\1/#; s#^ssh://git@([^:]+)/#https://\1/#; s#^[^@]*@#https://#; s#\.git$##')"
    ;;
  *)
    HTTPS_URL="${REMOTE}"
    ;;
esac
echo
log "next:"
echo "  GitHub secret    SOLRISE_APP_URL    = ${HTTPS_URL}"
echo "  GitHub variable  SOLRISE_APP_BRANCH = ${BRANCH}"
echo "  group_vars       solrise_app_url    = ${HTTPS_URL}"
echo "  group_vars       solrise_app_branch = ${BRANCH}"
echo "  group_vars       solrise_app_enabled: true"
echo
echo "  (private repo? use https://x-access-token:<PAT>@<host>/<owner>/<repo> for SOLRISE_APP_URL)"
echo "  then tell Zed: it flips the group_vars, triggers build-image and deploys."
