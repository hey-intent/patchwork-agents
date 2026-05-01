#!/usr/bin/env bash
# issue_prompt.sh — append optional SOURCE_ISSUE_BODY to a base worker prompt.
# Sourced from providers/*.sh via PROMPT_DIR (repo: prompt/ ; image: /app/prompt/).
# Uses: SOURCE_ISSUE_BODY (optional), set by orchestrator from SourceProvider.

issue_append_issue_body() {
  local base="$1"
  if [ -n "${SOURCE_ISSUE_BODY:-}" ]; then
    printf '%s\n\nIssue description:\n%s' "$base" "${SOURCE_ISSUE_BODY}"
  else
    printf '%s' "$base"
  fi
}
