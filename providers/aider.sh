#!/usr/bin/env bash
set -euo pipefail

# required envs: SOURCE_REPO, SOURCE_ISSUE_NUMBER, GITHUB_TOKEN
# optional: SOURCE_ISSUE_TITLE, SOURCE_ISSUE_BODY (orchestrator / SourceProvider)
# secrets via env: OPENROUTER_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_DIR="$(cd "$SCRIPT_DIR/../prompt" && pwd)"
source "$PROMPT_DIR/action_prompt.sh"

# ── 1. Authenticate ──
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

# ── 2. Clone & branch ──
source "$SCRIPT_DIR/git_workflow.sh"
trap 'agent_set_status "agent:status:failed" || true' ERR
FULL_MSG="$(build_action_prompt)"
git_clone_and_branch

# ── 3. Run Aider via OpenRouter ──
AIDER_MODEL="${AIDER_MODEL:-openrouter/anthropic/claude-sonnet-4}"

echo "Running Aider (model=$AIDER_MODEL) for issue #${ISSUE_NUMBER} ..."

aider \
  --model "$AIDER_MODEL" \
  --api-key openrouter="$OPENROUTER_API_KEY" \
  --yes-always \
  --no-auto-commits \
  --message "$FULL_MSG"

# Commit all changes made by aider
if ! git diff --quiet || ! git diff --cached --quiet; then
  git add -A
  git commit -m "fix: resolve issue #${ISSUE_NUMBER} — ${SOURCE_ISSUE_TITLE:-no title}"
fi

# ── 4. Push & create PR ──
git_push_and_pr "Automated PR created by Aider (OpenRouter) for issue #${ISSUE_NUMBER}."
agent_set_status "agent:status:done"

echo "Done"
