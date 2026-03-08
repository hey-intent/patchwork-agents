#!/usr/bin/env bash
set -euo pipefail

echo "=== worker start ==="
echo "TIME: $(date -u --iso-8601=seconds)"
echo "AI_PROVIDER=${AI_PROVIDER:-claude_code}"
echo "GITHUB_REPO=${GITHUB_REPO:-}"
echo "GITHUB_ISSUE_NUMBER=${GITHUB_ISSUE_NUMBER:-}"
echo "GITHUB_INSTALLATION_ID=${GITHUB_INSTALLATION_ID:-}"
if [[ "${DEBUG_ENV:-0}" == "1" ]]; then
  echo "---- env (whitelist) ----"
  printenv | grep -E '^(AI_PROVIDER|GITHUB_REPO|GITHUB_ISSUE_NUMBER|GITHUB_INSTALLATION_ID|NAMESPACE|JOB_IMAGE|HOME|PATH)=' || true
  echo "---- end env ----"
fi

exec /app/providers/claude_code.sh
