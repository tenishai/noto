#!/usr/bin/env bash
#
# lint-conventions.sh - enforce Argus prose and pinning conventions.
#
# Checks:
#   1. No em dashes in prose (Markdown). Code and generated config are exempt.
#   2. No floating version pins (`:latest`, `version: latest`) in config.
#
# Usage: scripts/lint-conventions.sh
# Exit:  0 = clean, 1 = violations found.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

status=0

# Files git tracks, so vendored/ignored content is out of scope.
tracked() { git ls-files "$@"; }

echo "==> Checking for em dashes in prose (*.md)"
# U+2014 EM DASH. Exclude this script and the testing doc that names the rule.
em_hits="$(tracked '*.md' | grep -vE '^(scripts/|CONTRIBUTING\.md$|docs/testing\.md$)' \
  | xargs grep -nP '\x{2014}' 2>/dev/null || true)"
if [[ -n "$em_hits" ]]; then
  echo "FAIL: em dashes found (use ' - ', a comma, or parentheses):"
  echo "$em_hits"
  status=1
else
  echo "ok"
fi

echo "==> Checking for floating 'latest' version pins (*.yaml, *.yml)"
# Match image tags and version fields pinned to 'latest', not prose.
latest_hits="$(tracked '*.yaml' '*.yml' \
  | xargs grep -nE '(:latest(["'\'' ]|$)|version:[[:space:]]*["'\'']?latest)' 2>/dev/null || true)"
if [[ -n "$latest_hits" ]]; then
  echo "FAIL: floating 'latest' pins found (pin an exact version):"
  echo "$latest_hits"
  status=1
else
  echo "ok"
fi

if [[ "$status" -eq 0 ]]; then
  echo "==> conventions: clean"
else
  echo "==> conventions: violations found"
fi
exit "$status"
