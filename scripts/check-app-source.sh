#!/usr/bin/env bash
# Preflight a Solrise app checkout before it is pushed to the app repository.
#
#   ./scripts/check-app-source.sh /path/to/solrise_erp
#
# Why: the deployment builds this app into the image and then relies on specific
# entry points (`install.apply_all`, `api.v1.health`, `api.chat.turn`, the widget
# assets, the DocTypes). Pushing a partial or wrong checkout costs a 15-minute CI
# run and a confusing failure; this catches it in a second.
#
# Advisory only - it reports what it finds, and the "missing" list is what the
# deployment will not be able to do. See docs/06 and docs/12 for what each piece
# is meant to be.
set -euo pipefail

SRC="${1:?usage: check-app-source.sh <path-to-solrise_erp-checkout>}"
[ -d "${SRC}" ] || { echo "ERROR: not a directory: ${SRC}" >&2; exit 2; }
# Physical path: the checks below compare it against a repository's top level.
SRC="$(cd "${SRC}" && pwd -P)"

missing=0
found=0

check() {
  # check <label> <find-expression...>
  local label="$1"; shift
  local hit
  hit="$(find "${SRC}" -maxdepth 6 "$@" 2>/dev/null | head -1)"
  if [ -n "${hit}" ]; then
    printf '  ok      %-34s %s\n' "${label}" "${hit#"${SRC}"/}"
    found=$((found + 1))
  else
    printf '  MISSING %-34s (%s)\n' "${label}" "$*"
    missing=$((missing + 1))
  fi
}

echo "checking ${SRC}"
echo

echo "repo basics:"
check "pyproject.toml or setup.py" \( -name pyproject.toml -o -name setup.py \)
check "hooks.py" -name hooks.py
check "modules.txt" -name modules.txt

if [ -f "${SRC}/$(cd "${SRC}" && find . -maxdepth 2 -name hooks.py | head -1 | sed 's|^\./||')" ]; then
  hooks="$(find "${SRC}" -maxdepth 2 -name hooks.py | head -1)"
  printf '  app_name in hooks.py:            '
  grep -h '^app_name' "${hooks}" || echo "(no app_name line - Frappe needs it)"
fi

echo
echo "what the deploy calls (docs/16 section 5):"
check "install.py (apply_all)" -name install.py
check "branding.py" -name branding.py
check "permissions.py (row rules)" -name permissions.py
check "tasks.py (scheduler jobs)" -name tasks.py
check "assistant/engine.py" -path '*/assistant/engine.py'
check "assistant/tools.py" -path '*/assistant/tools.py'
check "api/v1.py" -path '*/api/v1.py'
check "api/chat.py" -path '*/api/chat.py'
check "chat/registry.py" -path '*/chat/registry.py'
check "chat/nlp.py" -path '*/chat/nlp.py'
check "chat/context.py" -path '*/chat/context.py'
check "chat/menu.py" -path '*/chat/menu.py'
check "chat/permissions.py" -path '*/chat/permissions.py'
check "chat/intent.py" -path '*/chat/intent.py'
check "chat/schema.py" -path '*/chat/schema.py'
check "chat/executor.py" -path '*/chat/executor.py'
check "chat/workflow.py" -path '*/chat/workflow.py'
check "chat/audit.py" -path '*/chat/audit.py'
check "tests/test_chat_nlp.py" -path '*/tests/test_chat_nlp.py'
check "tests/test_chat_guardrails.py" -path '*/tests/test_chat_guardrails.py'
check "fixtures/solrise_faq.json" -path '*/fixtures/solrise_faq.json'
check "fixtures/workflow.json" -path '*/fixtures/workflow.json'

echo
echo "assets the widget needs:"
check "public/js/solrise_chat.js" -name solrise_chat.js
check "public/js/solrise_erp.js" -name solrise_erp.js
check "logo png" -name 'solrise-logo.png'

echo
echo "DocTypes (each needs a directory with its .json):"
for doctype in solrise_settings solrise_chat_log solrise_faq solrise_ai_audit_log \
               solrise_message_log solrise_notification_channel; do
  check "${doctype}" -type d -name "${doctype}"
done

echo
# `git -C` finds the enclosing repository, so this has to compare the repository's
# top level with the app directory: when the app lives inside the deployment
# repository (the normal layout here, gitignored), reporting that repository's
# branch and commit would describe the wrong tree entirely.
REPO_TOP="$(git -C "${SRC}" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -n "${REPO_TOP}" ] && [ "$(cd "${REPO_TOP}" && pwd -P)" = "${SRC}" ]; then
  echo "git: this directory is its own repository, so publish pushes its HEAD"
  printf '  branch   %s\n' "$(git -C "${SRC}" rev-parse --abbrev-ref HEAD)"
  printf '  commit   %s\n' "$(git -C "${SRC}" rev-parse HEAD)"
  printf '  remote   %s\n' "$(git -C "${SRC}" remote -v | head -1 || echo '(none)')"
  printf '  dirty    %s file(s)\n' "$(git -C "${SRC}" status --porcelain | wc -l)"
elif [ -n "${REPO_TOP}" ]; then
  echo "git: not its own repository (it lives inside ${REPO_TOP})"
  echo "     publish stages a copy into an orphan branch of the app repository,"
  echo "     so the enclosing repository's branch and commit do not apply."
  printf '  dirty    %s file(s) in that enclosing repository\n' \
    "$(git -C "${SRC}" status --porcelain | wc -l)"
else
  echo "git: not a repository (the image build clones by URL - it must be pushed)"
fi

echo
echo "found ${found}, missing ${missing}"
if [ "${missing}" -gt 0 ]; then
  echo "The missing entries are what the deployment will not be able to do."
  echo "If install.py / api/ / the DocTypes are missing, this is not the app the"
  echo "deploy expects - keep looking rather than pushing it."
fi
