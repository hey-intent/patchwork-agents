from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from urllib.parse import quote

import httpx
import jwt

from .base import SourceProvider, SourceProviderAPIError, SourceProviderConfigurationError
from .models import ActionTrigger, Comment, Issue, PullRequest, WebhookEvent

logger = logging.getLogger("uvicorn.error")

DEFAULT_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
GITHUB_SIGNATURE_HEX_LENGTH = 64
JWT_LEEWAY_SECONDS = 60
JWT_TTL_SECONDS = 600
DEFAULT_INSTALLATION_TOKEN_TTL_SECONDS = 3300
AGENT_ACTIONS = {"spec", "plan", "implement", "review", "continue"}
ACTION_TRIGGER_COMMAND_MAX_CHARS = 64
ACTION_TRIGGER_ALLOWED_PERMISSIONS = {"write", "maintain", "admin"}


class GitHubProvider(SourceProvider):
    name = "github"

    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        webhook_secret: str,
        api_base_url: str = "https://api.github.com",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self.private_key = private_key
        self.webhook_secret = webhook_secret
        self.api_base_url = api_base_url.rstrip("/")
        self._http_client = http_client
        self._installation_ids_by_repo: dict[str, str] = {}
        self._installation_tokens_by_repo: dict[str, tuple[str, float]] = {}

    async def verify_webhook(self, headers: dict, body: bytes) -> bool:
        if not self.webhook_secret:
            logger.error("WEBHOOK_SECRET is not configured - rejecting all webhooks")
            return False

        signature_header = _header(headers, "X-Hub-Signature-256")
        if not signature_header or not signature_header.startswith("sha256="):
            return False

        their_sig = signature_header.split("=", 1)[1].strip()
        if len(their_sig) != GITHUB_SIGNATURE_HEX_LENGTH or not _is_hex(their_sig):
            return False

        mac = hmac.new(self.webhook_secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
        return hmac.compare_digest(mac.hexdigest(), their_sig)

    async def parse_event(self, headers: dict, body: bytes) -> WebhookEvent | None:
        event_name = _header(headers, "X-GitHub-Event")
        if not event_name:
            return None

        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None

        repo = (payload.get("repository") or {}).get("full_name")
        source_installation_id = None
        if repo:
            installation_id = (payload.get("installation") or {}).get("id")
            if installation_id:
                source_installation_id = str(installation_id)
                self._installation_ids_by_repo[repo] = str(installation_id)

        sender = payload.get("sender") or {}
        actor = sender.get("login", "")
        action = payload.get("action")

        if event_name == "issues":
            issue = _issue_from_payload(payload)
            if not issue:
                return None
            if action == "opened":
                return WebhookEvent(
                    type="issue_opened",
                    actor=actor,
                    repo=issue.repo,
                    source_installation_id=source_installation_id,
                    issue=issue,
                    raw=payload,
                )
            if action == "labeled":
                label = _label_name(payload.get("label"))
                return WebhookEvent(
                    type="issue_labeled",
                    actor=actor,
                    repo=issue.repo,
                    source_installation_id=source_installation_id,
                    issue=issue,
                    label=label,
                    raw=payload,
                )
            if action == "unlabeled":
                label = _label_name(payload.get("label"))
                return WebhookEvent(
                    type="issue_unlabeled",
                    actor=actor,
                    repo=issue.repo,
                    source_installation_id=source_installation_id,
                    issue=issue,
                    label=label,
                    raw=payload,
                )
            return None

        if event_name == "issue_comment" and action == "created":
            issue = _issue_from_payload(payload)
            comment = _comment_from_payload(payload)
            if not issue or not comment:
                return None
            return WebhookEvent(
                type="issue_commented",
                actor=actor,
                repo=issue.repo,
                source_installation_id=source_installation_id,
                issue=issue,
                comment=comment,
                raw=payload,
            )

        if event_name == "pull_request":
            pr = _pr_from_payload(payload)
            if not pr:
                return None
            if action == "opened":
                return WebhookEvent(
                    type="pr_opened",
                    actor=actor,
                    repo=pr.repo,
                    source_installation_id=source_installation_id,
                    pr=pr,
                    raw=payload,
                )
            if action == "closed":
                event_type = "pr_merged" if (payload.get("pull_request") or {}).get("merged") else "pr_closed"
                return WebhookEvent(
                    type=event_type,
                    actor=actor,
                    repo=pr.repo,
                    source_installation_id=source_installation_id,
                    pr=pr,
                    raw=payload,
                )
            return None

        return None

    def get_action_trigger(self, event: WebhookEvent) -> ActionTrigger | None:
        if event.type != "issue_commented" or not event.comment:
            return None

        for raw_line in event.comment.body.splitlines():
            line = raw_line.strip()
            if not line.startswith("/agent"):
                continue
            parts = line.split()
            if len(parts) < 2 or parts[0] != "/agent":
                continue
            action = parts[1].lower()
            if action not in AGENT_ACTIONS:
                continue
            raw_command = f"/agent {action}"[:ACTION_TRIGGER_COMMAND_MAX_CHARS]
            return ActionTrigger(action=action, source="comment", raw_command=raw_command)

        return None

    async def can_trigger_action(self, event: WebhookEvent, trigger: ActionTrigger) -> bool:
        if not event.actor:
            return False

        token = await self._installation_token_for_repo(event.repo)
        response = await self._request(
            "GET",
            f"/repos/{event.repo}/collaborators/{quote(event.actor, safe='')}/permission",
            headers=self._github_headers(f"Bearer {token}"),
        )
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise SourceProviderAPIError(f"GitHub collaborator permission lookup failed ({response.status_code})")

        permission = response.json().get("permission")
        return permission in ACTION_TRIGGER_ALLOWED_PERMISSIONS

    async def get_issue(self, repo: str, issue_id: str) -> Issue:
        data = await self._request_json("GET", f"/repos/{repo}/issues/{issue_id}", repo=repo)
        return _issue_from_api(repo, data)

    async def list_issues(
        self,
        repo: str,
        *,
        labels: list[str] | None = None,
        state: str = "open",
    ) -> list[Issue]:
        params: dict[str, str] = {"state": state}
        if labels:
            params["labels"] = ",".join(labels)
        data = await self._request_json("GET", f"/repos/{repo}/issues", repo=repo, params=params)
        return [_issue_from_api(repo, item) for item in data]

    async def list_issue_comments(self, repo: str, issue_id: str) -> list[Comment]:
        data = await self._request_json("GET", f"/repos/{repo}/issues/{issue_id}/comments", repo=repo)
        return [_comment_from_api(str(issue_id), item) for item in data]

    async def add_issue_comment(self, repo: str, issue_id: str, body: str) -> Comment:
        data = await self._request_json(
            "POST",
            f"/repos/{repo}/issues/{issue_id}/comments",
            repo=repo,
            json={"body": body},
        )
        return _comment_from_api(str(issue_id), data)

    async def get_issue_labels(self, repo: str, issue_id: str) -> list[str]:
        data = await self._request_json("GET", f"/repos/{repo}/issues/{issue_id}/labels", repo=repo)
        return [item["name"] for item in data if "name" in item]

    async def add_issue_label(self, repo: str, issue_id: str, label: str) -> None:
        await self._request_json(
            "POST",
            f"/repos/{repo}/issues/{issue_id}/labels",
            repo=repo,
            json={"labels": [label]},
        )

    async def delete_issue_label(self, repo: str, issue_id: str, label: str) -> None:
        encoded_label = quote(label, safe="")
        await self._request_json("DELETE", f"/repos/{repo}/issues/{issue_id}/labels/{encoded_label}", repo=repo)

    async def replace_issue_labels(self, repo: str, issue_id: str, labels: list[str]) -> None:
        await self._request_json(
            "PUT",
            f"/repos/{repo}/issues/{issue_id}/labels",
            repo=repo,
            json={"labels": labels},
        )

    async def create_pr(
        self,
        repo: str,
        *,
        branch: str,
        base: str,
        title: str,
        body: str,
        issue_id: str | None = None,
    ) -> PullRequest:
        data = await self._request_json(
            "POST",
            f"/repos/{repo}/pulls",
            repo=repo,
            json={"head": branch, "base": base, "title": title, "body": body},
        )
        pr = _pr_from_api(repo, data)
        pr.issue_id = issue_id
        return pr

    async def get_pr(self, repo: str, pr_id: str) -> PullRequest:
        data = await self._request_json("GET", f"/repos/{repo}/pulls/{pr_id}", repo=repo)
        return _pr_from_api(repo, data)

    async def get_clone_credentials(self, repo: str) -> tuple[str, str]:
        token = await self._installation_token_for_repo(repo)
        return "x-access-token", token

    async def _installation_token_for_repo(self, repo: str) -> str:
        cached = self._installation_tokens_by_repo.get(repo)
        if cached:
            token, expires_at = cached
            if time.time() < expires_at:
                return token

        return await self._generate_installation_token(repo)

    async def _generate_installation_token(self, repo: str) -> str:
        if not self.app_id or not self.private_key:
            raise SourceProviderConfigurationError("GITHUB_APP_ID and GITHUB_PRIVATE_KEY must be configured")

        installation_id = await self._get_installation_id(repo)
        encoded_jwt = self._build_app_jwt()
        headers = self._github_headers(f"Bearer {encoded_jwt}")
        response = await self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers=headers,
        )
        if response.status_code != 201:
            raise SourceProviderAPIError(f"GitHub installation token exchange failed ({response.status_code})")

        payload = response.json()
        token = payload.get("token")
        if not token:
            raise SourceProviderAPIError("GitHub API returned no token")
        self._installation_tokens_by_repo[repo] = (token, _token_expires_at(payload))
        return token

    async def _get_installation_id(self, repo: str) -> str:
        cached = self._installation_ids_by_repo.get(repo)
        if cached:
            return cached

        encoded_jwt = self._build_app_jwt()
        response = await self._request(
            "GET",
            f"/repos/{repo}/installation",
            headers=self._github_headers(f"Bearer {encoded_jwt}"),
        )
        if response.status_code != 200:
            raise SourceProviderAPIError(f"GitHub installation lookup failed ({response.status_code})")

        installation_id = response.json().get("id")
        if not installation_id:
            raise SourceProviderAPIError("GitHub API returned no installation id")
        self._installation_ids_by_repo[repo] = str(installation_id)
        return str(installation_id)

    def _build_app_jwt(self) -> str:
        now = int(time.time())
        payload = {
            "iat": now - JWT_LEEWAY_SECONDS,
            "exp": now + JWT_TTL_SECONDS,
            "iss": self.app_id,
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def _request_json(self, method: str, path: str, *, repo: str, **kwargs) -> dict | list:
        token = await self._installation_token_for_repo(repo)
        response = await self._request(method, path, headers=self._github_headers(f"Bearer {token}"), **kwargs)
        if response.status_code >= 400:
            raise SourceProviderAPIError(f"GitHub API request failed ({response.status_code})")
        if response.status_code == 204:
            return {}
        return response.json()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self.api_base_url}{path}"
        kwargs.setdefault("timeout", DEFAULT_HTTP_TIMEOUT)
        if self._http_client:
            return await self._http_client.request(method, url, **kwargs)
        async with httpx.AsyncClient() as client:
            return await client.request(method, url, **kwargs)

    @staticmethod
    def _github_headers(authorization: str) -> dict[str, str]:
        return {
            "Authorization": authorization,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }


def _header(headers: dict, name: str) -> str | None:
    if name in headers:
        return headers[name]
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _is_hex(value: str) -> bool:
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _token_expires_at(payload: dict) -> float:
    expires_at = payload.get("expires_at")
    if isinstance(expires_at, str):
        try:
            parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            pass
    return time.time() + DEFAULT_INSTALLATION_TOKEN_TTL_SECONDS


def _label_name(label: object) -> str | None:
    if isinstance(label, dict):
        value = label.get("name")
        return str(value) if value is not None else None
    if label is not None:
        return str(label)
    return None


def _issue_from_payload(payload: dict) -> Issue | None:
    repo = (payload.get("repository") or {}).get("full_name")
    issue = payload.get("issue") or {}
    if not repo or not issue:
        return None
    return _issue_from_api(repo, issue)


def _issue_from_api(repo: str, data: dict) -> Issue:
    labels = [_label_name(label) for label in data.get("labels", [])]
    user = data.get("user") or {}
    return Issue(
        id=str(data.get("id", "")),
        number=int(data.get("number", 0)),
        repo=repo,
        title=data.get("title") or "",
        body=data.get("body") or "",
        labels=[label for label in labels if label],
        author=user.get("login", ""),
        url=data.get("html_url") or "",
        state=data.get("state") or "open",
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        raw=data,
    )


def _comment_from_payload(payload: dict) -> Comment | None:
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    if not issue or not comment:
        return None
    return _comment_from_api(str(issue.get("id", issue.get("number", ""))), comment)


def _comment_from_api(issue_id: str, data: dict) -> Comment:
    user = data.get("user") or {}
    return Comment(
        id=str(data.get("id", "")),
        issue_id=issue_id,
        body=data.get("body") or "",
        author=user.get("login", ""),
        created_at=data.get("created_at"),
    )


def _pr_from_payload(payload: dict) -> PullRequest | None:
    repo = (payload.get("repository") or {}).get("full_name")
    pr = payload.get("pull_request") or {}
    if not repo or not pr:
        return None
    return _pr_from_api(repo, pr)


def _pr_from_api(repo: str, data: dict) -> PullRequest:
    user_state = data.get("state") or "open"
    state = "merged" if data.get("merged") or data.get("merged_at") else user_state
    head = data.get("head") or {}
    base = data.get("base") or {}
    return PullRequest(
        id=str(data.get("id", "")),
        number=int(data.get("number", 0)),
        repo=repo,
        title=data.get("title") or "",
        body=data.get("body") or "",
        branch=head.get("ref", ""),
        base=base.get("ref", ""),
        state=state,
        url=data.get("html_url") or "",
    )
