#!/usr/bin/env bash
# git_workflow.sh — Shared clone/branch/push/PR logic.
# Exports: WORKDIR, BRANCH, BASE_SHA
# Provides: git_clone_and_branch, git_push_and_pr
# Source this file from provider scripts.

set -euo pipefail

REPO="${GITHUB_REPO:?}"
ISSUE_NUMBER="${GITHUB_ISSUE_NUMBER:?}"

# Use GIT_ASKPASS to avoid leaking token in process list / git error logs
_setup_git_askpass() {
  local askpass_script="/tmp/git-askpass.sh"
  printf '#!/bin/sh\necho "%s"\n' "$GITHUB_TOKEN" > "$askpass_script"
  chmod +x "$askpass_script"
  export GIT_ASKPASS="$askpass_script"
}

git_clone_and_branch() {
  WORKDIR="/work/repo"
  rm -rf "$WORKDIR"
  mkdir -p "$WORKDIR"
  cd "$WORKDIR"

  _setup_git_askpass

  echo "Cloning $REPO ..."
  git clone "https://x-access-token@github.com/${REPO}.git" .

  # Remove credentials from remote URL so AI tools don't try to use them
  # for GitHub API integration (GIT_ASKPASS handles auth for push)
  git remote set-url origin "https://github.com/${REPO}.git"

  BRANCH="ai-pr-${ISSUE_NUMBER}-$(date +%s)"
  git checkout -b "$BRANCH"

  # configure git identity for commits
  git config user.name "patchwork-agent"
  git config user.email "patchwork-agent@users.noreply.github.com"

  # Save base SHA to detect changes later
  BASE_SHA=$(git rev-parse HEAD)
  export WORKDIR BRANCH BASE_SHA
}

git_push_and_pr() {
  local pr_body="${1:-Automated PR for issue #${ISSUE_NUMBER}.}"

  # Check if AI actually committed anything
  if [ "$(git rev-parse HEAD)" = "$BASE_SHA" ]; then
    echo "ERROR: no changes were committed, aborting."
    exit 1
  fi

  echo "Pushing branch $BRANCH ..."
  git push origin "$BRANCH"

  echo "Creating pull request ..."
  export GH_TOKEN="$GITHUB_TOKEN"
  if command -v gh >/dev/null 2>&1; then
    gh pr create \
      --title "AI fix for issue #${ISSUE_NUMBER}" \
      --body "$pr_body" \
      --base main --head "$BRANCH" \
      --repo "$REPO"
  else
    curl -sf -X POST \
      -H "Authorization: token ${GITHUB_TOKEN}" \
      -H "Accept: application/vnd.github+json" \
      "https://api.github.com/repos/${REPO}/pulls" \
      -d "$(jq -nc \
        --arg t "AI fix for issue #${ISSUE_NUMBER}" \
        --arg b "$BRANCH" \
        --arg body "$pr_body" \
        '{title:$t, head:$b, base:"main", body:$body}')"
  fi
}
