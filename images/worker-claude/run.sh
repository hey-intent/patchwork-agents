#!/usr/bin/env bash
set -euo pipefail

echo "=== worker start ==="
echo "TIME: $(date -u --iso-8601=seconds)"
echo "AI_PROVIDER=${AI_PROVIDER:-claude_code}"
echo "SOURCE_REPO=${SOURCE_REPO:-}"
echo "SOURCE_ISSUE_NUMBER=${SOURCE_ISSUE_NUMBER:-}"
echo "SOURCE_INSTALLATION_ID=${SOURCE_INSTALLATION_ID:-}"
if [[ "${DEBUG_ENV:-0}" == "1" ]]; then
  echo "---- env (whitelist) ----"
  printenv | grep -E '^(AI_PROVIDER|SOURCE_REPO|SOURCE_ISSUE_NUMBER|SOURCE_INSTALLATION_ID|NAMESPACE|JOB_IMAGE|HOME|PATH)=' || true
  echo "---- end env ----"
fi

exec /app/providers/claude_code.sh
