#!/usr/bin/env bash
set -euo pipefail

# required envs: GITHUB_REPO, GITHUB_ISSUE_NUMBER, GITHUB_TOKEN
# secrets via env: ANTHROPIC_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── 1. Authenticate ──
# GITHUB_TOKEN is provided by the orchestrator (ephemeral installation token)
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

# ── 2. Clone & branch ──
source "$SCRIPT_DIR/git_workflow.sh"
git_clone_and_branch

# ── 3. Run Claude Code ──
echo "Running Claude Code for issue #${ISSUE_NUMBER} ..."

claude -p --dangerously-skip-permissions \
  "You are working in a git repo. Fix issue #${ISSUE_NUMBER}: ${GITHUB_ISSUE_TITLE:-no title}.
Read the relevant code, make the necessary changes, then commit with a meaningful message.
Do NOT push."

# ── 4. Push & create PR ──
git_push_and_pr "Automated PR created by Claude Code for issue #${ISSUE_NUMBER}."

echo "Done"
