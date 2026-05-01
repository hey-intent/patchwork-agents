#!/usr/bin/env bash
set -euo pipefail

# required envs: SOURCE_REPO, SOURCE_ISSUE_NUMBER, GITHUB_TOKEN
# optional: SOURCE_ISSUE_TITLE, SOURCE_ISSUE_BODY (orchestrator / SourceProvider)
# secrets via env: OPENAI_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_DIR="$(cd "$SCRIPT_DIR/../prompt" && pwd)"
source "$PROMPT_DIR/action_prompt.sh"

# ── 1. Authenticate ──
# GITHUB_TOKEN is provided by the orchestrator (ephemeral installation token)
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

# ── 2. Clone & branch ──
source "$SCRIPT_DIR/git_workflow.sh"
trap 'agent_set_status "agent:status:failed" || true' ERR
FULL_PROMPT="$(build_action_prompt)"
git_clone_and_branch

# ── 3. Login & run Codex ──
echo "Logging in to Codex CLI..."
printenv OPENAI_API_KEY | codex login --with-api-key

CODEX_MODEL="${CODEX_MODEL:-gpt-5.3-codex}"
echo "Running Codex (model=$CODEX_MODEL) for issue #${ISSUE_NUMBER} ..."

codex exec --dangerously-bypass-approvals-and-sandbox --model "$CODEX_MODEL" "$FULL_PROMPT"

# ── 4. Push & create PR ──
git_push_and_pr "Automated PR created by Codex (OpenAI) for issue #${ISSUE_NUMBER}."
agent_set_status "agent:status:done"

echo "Done"
