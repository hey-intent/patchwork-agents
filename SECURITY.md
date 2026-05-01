# Security Policy

## Supported Versions

This project is currently maintained on the `main` branch only.

## Reporting a Vulnerability

Please do not open public issues for security vulnerabilities.

Report privately by contacting the maintainers with:

- A clear description of the issue
- Reproduction steps or proof-of-concept
- Impact assessment (what can be accessed or modified)
- Suggested remediation (if available)

If your report contains secrets, rotate them immediately after sharing.

## Scope Notes

- This project handles sensitive material: source-provider credentials, AI API keys, webhook secrets, admin tokens, and short-lived git clone tokens.
- GitHub is currently the only built-in source provider. Its App private key must stay in the orchestrator pod only.
- Workers must receive only short-lived source credentials, never long-lived source-provider private keys.
- Never commit real secret values to git history.
- Kubernetes secret manifests under `k8s/secrets/` are templates only.
- Webhook fixture files under `tests/` must be anonymized and must not contain real repository names, users, tokens, signatures, private issue content, or internal URLs.

## Source Provider Security

Source providers live under `providers/source/` and own provider-specific webhook
verification, event parsing, API calls, and git clone credentials.

Provider implementations must:

- Verify webhook authenticity before parsing or acting on the payload.
- Treat webhook bodies, `raw` payloads, comments, issue bodies, and PR bodies as untrusted input.
- Return the shortest-lived and narrowest-scoped git credentials available from `get_clone_credentials(repo)`.
- Avoid logging tokens, webhook signatures, private keys, issue bodies, comments, or full raw payloads.
- Keep provider-specific secrets in the orchestrator, not in worker images or manifests.
- Document any provider that cannot issue short-lived repo-scoped credentials.

For GitHub, `get_clone_credentials(repo)` uses a GitHub App installation token.
Workers receive that token through an ephemeral Kubernetes Secret and do not
receive the GitHub App PEM key.

## Hardening Expectations

- Restrict public exposure to `/webhook/github` only.
- Keep admin endpoints (`/secrets/github-app`, `/jobs/run`) private.
- Use strong random `ADMIN_TOKEN` values and rotate credentials regularly.
- Run workers only in isolated, ephemeral environments.
- Keep worker ServiceAccount permissions minimal. Workers should not need access to Kubernetes Secrets.
- Prefer a secrets operator such as Sealed Secrets or External Secrets for production deployments.
- Rotate webhook secrets and source-provider credentials after suspected exposure.
- Review new source-provider implementations for webhook verification, credential lifetime, token scope, and logging behavior before enabling them.
