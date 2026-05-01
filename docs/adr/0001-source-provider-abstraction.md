# ADR 0001: Source Provider Abstraction

## Status

Accepted

## Context

The orchestrator currently receives GitHub webhooks, verifies the GitHub
signature, reads GitHub issue payloads, generates GitHub App installation
tokens, and starts Kubernetes worker jobs.

That works for the current proof of concept, but it couples the orchestration
flow to GitHub-specific details. Future support for GitLab, Gitea, Forgejo, or
other issue trackers would otherwise require GitHub conditionals in `app/app.py`
and make the core flow harder to test.

The project also has two provider concepts:

- Source providers: issue tracking and code hosting, such as GitHub or GitLab.
- AI worker providers: Claude, Codex, Aider.

This ADR covers source providers only.

## Decision

Introduce an async `SourceProvider` interface under `providers/source/`.

The interface owns provider-specific behavior for:

- Webhook verification and event parsing.
- Issue, comment, label, and pull request operations.
- Git clone credentials via `get_clone_credentials(repo)`.

GitHub remains the only built-in source provider for now. The existing GitHub
behavior is extracted into `GitHubProvider`, and `app/app.py` keeps responsibility
for orchestration decisions and Kubernetes job creation.

`get_clone_credentials(repo)` returns `(username, token)` for HTTPS Git auth.
Implementations should use the shortest-lived and narrowest-scoped credential
available for their platform.

For GitHub, this means:

- Username: `x-access-token`
- Token: GitHub App installation token

## Non-Goals

- No GitLab, Gitea, Forgejo, or Linear implementation in this change.
- No label state machine changes.
- No change to the existing `ai-pr-*` trigger behavior.
- No replacement of Kubernetes job orchestration.

## Consequences

Positive:

- `app/app.py` no longer needs to know GitHub webhook signature or token minting
  details.
- Source-provider behavior is unit-testable with mocked HTTP.
- Adding another source provider can happen behind the same contract.
- The distinction between source providers and AI worker providers is explicit.

Tradeoffs:

- The first abstraction is based on GitHub's current behavior, so future
  providers may reveal contract gaps.
- Some source platforms cannot always provide short-lived repo-scoped clone
  credentials. Their implementations must document the best available security
  model.

Worker jobs receive **source-agnostic metadata** as `SOURCE_*` environment
variables (for example `SOURCE_REPO`, `SOURCE_ISSUE_NUMBER`, `SOURCE_ISSUE_BODY`,
`SOURCE_ISSUE_URL`, `SOURCE_INSTALLATION_ID`, `SOURCE_ACTION`, `SOURCE_TRIGGER`,
`SOURCE_TRIGGER_COMMAND`) populated
from the active `SourceProvider`. The HTTPS clone credential still uses the
secret key **`GITHUB_TOKEN`** today (GitHub App installation token). Renaming
that credential for non-GitHub hosts is a separate change.

## Validation

The GitHub implementation is covered by unit tests for:

- Webhook signature verification.
- Issue label webhook parsing.
- Label add/delete/replace operations.
- Clone credential generation.
