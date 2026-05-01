#!/usr/bin/env bash
set -euo pipefail

# required envs: SOURCE_REPO, SOURCE_ISSUE_NUMBER, GITHUB_TOKEN
# optional: SOURCE_ISSUE_TITLE, SOURCE_ISSUE_BODY (orchestrator / SourceProvider)
# secrets via env: OPENAI_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_DIR="$(cd "$SCRIPT_DIR/../prompt" && pwd)"
source "$PROMPT_DIR/issue_prompt.sh"
source "$PROMPT_DIR/issue_start_prompt.sh"

# ── 1. Authenticate ──
# GITHUB_TOKEN is provided by the orchestrator (ephemeral installation token)
: "${GITHUB_TOKEN:?GITHUB_TOKEN is required}"

# ── 2. Clone & branch ──
source "$SCRIPT_DIR/git_workflow.sh"
git_clone_and_branch

# ── 3. Login & run Codex ──
echo "Logging in to Codex CLI..."
printenv OPENAI_API_KEY | codex login --with-api-key

CODEX_MODEL="${CODEX_MODEL:-gpt-5.3-codex}"
echo "Running Codex (model=$CODEX_MODEL) for issue #${ISSUE_NUMBER} ..."

BASE_PROMPT="$(issue_start_prompt)"
FULL_PROMPT="$(issue_append_issue_body "$BASE_PROMPT")"

codex exec --dangerously-bypass-approvals-and-sandbox --model "$CODEX_MODEL" "$FULL_PROMPT"

# ── 4. Push & create PR ──
git_push_and_pr "Automated PR created by Codex (OpenAI) for issue #${ISSUE_NUMBER}."

echo "Done"
