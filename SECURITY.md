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

- This project handles sensitive material (GitHub App private key, API keys, webhook secret, admin token).
- Never commit real secret values to git history.
- Kubernetes secret manifests under `k8s/secrets/` are templates only.

## Hardening Expectations

- Restrict public exposure to `/webhook/github` only.
- Keep admin endpoints (`/secrets/github-app`, `/jobs/run`) private.
- Use strong random `ADMIN_TOKEN` values and rotate credentials regularly.
- Run workers only in isolated, ephemeral environments.
