#!/usr/bin/env bash

action_implement_prompt() {
  cat <<EOF
You are working in a git repository.

Task:
Fix GitHub issue #${ISSUE_NUMBER}: ${SOURCE_ISSUE_TITLE:-no title}
EOF

  if [ -n "${SOURCE_ISSUE_BODY:-}" ]; then
    printf '\n\nIssue description:\n%s\n' "${SOURCE_ISSUE_BODY}"
  fi

  cat <<'EOF'

Instructions:
1. Read the issue title and description carefully.
2. Inspect the relevant code before making changes.
3. Make the smallest correct change that resolves the issue.
4. Do not refactor unrelated code.
5. Do not change public APIs unless the issue explicitly requires it.
6. Add or update tests when relevant.
7. Run the most relevant validation command available in the repository:
   - tests
   - typecheck
   - lint
   - build
8. If validation cannot be run, explain why.

Output at the end:
- Summary of changes
- Files changed
- Validation performed
- Any remaining uncertainty

Do not invent requirements that are not in the issue.

Do not push to the remote or open a pull request yourself; automation will push and open the PR after you commit locally.
EOF
}
