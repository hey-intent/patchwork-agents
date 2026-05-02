from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import app.app as app_module
from app.agent_orchestrator import MAX_ISSUE_BODY_CHARS
from providers.source.models import ActionTrigger, Comment, Issue, WebhookEvent

FIXTURES = Path(__file__).parents[1] / "providers" / "fixtures"


class FakeSourceProvider:
    def __init__(self, *, verify: bool = True, event: WebhookEvent | None = None) -> None:
        self.verify = verify
        self.event = event
        self.comments: list[tuple[str, str, str]] = []
        self.replaced_labels: list[tuple[str, str, list[str]]] = []

    async def verify_webhook(self, headers: dict, body: bytes) -> bool:
        return self.verify

    async def parse_event(self, headers: dict, body: bytes) -> WebhookEvent | None:
        return self.event

    def get_action_trigger(self, event: WebhookEvent) -> ActionTrigger | None:
        if event.type != "issue_commented" or not event.comment:
            return None
        if event.comment.body.startswith("/agent implement"):
            return ActionTrigger(action="implement", source="comment", raw_command="/agent implement")
        if event.comment.body.startswith("/agent plan"):
            return ActionTrigger(action="plan", source="comment", raw_command="/agent plan")
        return None

    async def can_trigger_action(self, event: WebhookEvent, trigger: ActionTrigger) -> bool:
        return True

    async def add_issue_comment(self, repo: str, issue_id: str, body: str):
        self.comments.append((repo, issue_id, body))
        return Comment(
            id="comment-response",
            issue_id=issue_id,
            body=body,
            author="agent",
            created_at=datetime.fromisoformat("2026-04-30T10:00:00+00:00"),
        )

    async def replace_issue_labels(self, repo: str, issue_id: str, labels: list[str]) -> None:
        self.replaced_labels.append((repo, issue_id, labels))

    async def get_clone_credentials(self, repo: str) -> tuple[str, str]:
        return "x-access-token", "installation-token"


class FakeBatch:
    def __init__(self) -> None:
        self.created_jobs = []

    def create_namespaced_job(self, *, namespace: str, body):
        self.created_jobs.append((namespace, body))
        return SimpleNamespace(metadata=SimpleNamespace(name=body.metadata.name, uid="job-uid"))


class FakeCore:
    def __init__(self) -> None:
        self.secrets = []
        self.secret_patches = []

    def create_namespaced_secret(self, *, namespace: str, body) -> None:
        self.secrets.append((namespace, body))

    def patch_namespaced_secret(self, *, name: str, namespace: str, body) -> None:
        self.secret_patches.append((name, namespace, body))

    def patch_namespaced_secret_status(self, *args, **kwargs) -> None:
        pass


def load_payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


async def post_webhook(body: bytes, headers: dict):
    transport = httpx.ASGITransport(app=app_module.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/webhook/github", content=body, headers=headers)


def issue_from_payload(payload: dict, *, labels: list[str] | None = None, body=None) -> Issue:
    raw_issue = payload["issue"]
    return Issue(
        id=str(raw_issue["id"]),
        number=raw_issue["number"],
        repo=payload["repository"]["full_name"],
        title=raw_issue["title"],
        body=raw_issue.get("body") if body is None else body,
        labels=labels if labels is not None else [label["name"] for label in raw_issue["labels"]],
        author=raw_issue["user"]["login"],
        url=raw_issue["html_url"],
        state=raw_issue["state"],
        created_at=datetime.fromisoformat(raw_issue["created_at"].replace("Z", "+00:00")),
        updated_at=datetime.fromisoformat(raw_issue["updated_at"].replace("Z", "+00:00")),
        raw=raw_issue,
    )


def comment(body: str) -> Comment:
    return Comment(
        id="2001",
        issue_id="1001",
        body=body,
        author="bob",
        created_at=datetime.fromisoformat("2026-04-30T10:00:00+00:00"),
    )


def issue_comment_event(
    payload: dict, *, labels: list[str] | None = None, body: str = "/agent implement"
) -> WebhookEvent:
    return WebhookEvent(
        type="issue_commented",
        actor="bob",
        repo=payload["repository"]["full_name"],
        source_installation_id=str(payload["installation"]["id"]),
        issue=issue_from_payload(payload, labels=labels),
        comment=comment(body),
        raw=payload,
    )


async def post_event(monkeypatch, event: WebhookEvent, provider: FakeSourceProvider | None = None):
    fake_provider = provider or FakeSourceProvider(event=event)
    batch = FakeBatch()
    core = FakeCore()
    monkeypatch.setattr(app_module, "get_source_provider", lambda: fake_provider)
    monkeypatch.setattr(app_module, "load_k8s_client", lambda: (batch, core))
    response = await post_webhook(
        json.dumps(event.raw).encode("utf-8"),
        {"X-GitHub-Event": "issue_comment"},
    )
    return response, batch, core, fake_provider


def plain_env_from_job(batch: FakeBatch) -> dict[str, str]:
    _ns, job = batch.created_jobs[0]
    container = job.spec.template.spec.containers[0]
    return {e.name: e.value for e in container.env if getattr(e, "value", None) is not None}


@pytest.mark.asyncio
async def test_github_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(app_module, "get_source_provider", lambda: FakeSourceProvider(verify=False))

    response = await post_webhook(b"{}", {"X-GitHub-Event": "issues"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_github_webhook_ignores_unparsed_source_event(monkeypatch):
    monkeypatch.setattr(app_module, "get_source_provider", lambda: FakeSourceProvider(event=None))

    response = await post_webhook(b"{}", {"X-GitHub-Event": "pull_request"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "ignored": True, "reason": "no source event"}


@pytest.mark.asyncio
async def test_issue_labeled_with_provider_sets_idle_but_does_not_trigger_job(monkeypatch):
    payload = load_payload("issue_labeled.json")
    event = WebhookEvent(
        type="issue_labeled",
        actor="bob",
        repo=payload["repository"]["full_name"],
        source_installation_id=str(payload["installation"]["id"]),
        issue=issue_from_payload(payload),
        label="ai-pr-claude",
        raw=payload,
    )

    response, batch, _core, provider = await post_event(monkeypatch, event)

    assert response.status_code == 200
    assert response.json()["ignored"] is True
    assert batch.created_jobs == []
    assert provider.replaced_labels == [("acme/widgets", "7", ["bug", "ai-pr-claude", "agent:status:idle"])]


@pytest.mark.asyncio
async def test_agent_implement_comment_triggers_worker_job(monkeypatch):
    payload = load_payload("issue_labeled.json")
    response, batch, core, provider = await post_event(monkeypatch, issue_comment_event(payload))

    assert response.status_code == 200
    data = response.json()
    assert data["triggered"] is True
    assert data["created"] is True
    assert data["repo"] == "acme/widgets"
    assert data["issue_number"] == 7
    assert data["action"] == "implement"
    assert data["provider"] == "claude"
    assert len(core.secrets) == 1
    assert core.secrets[0][1].string_data == {"GITHUB_TOKEN": "installation-token"}
    assert len(batch.created_jobs) == 1
    assert provider.replaced_labels == [("acme/widgets", "7", ["bug", "ai-pr-claude", "agent:status:running"])]

    env_plain = plain_env_from_job(batch)
    assert env_plain["SOURCE_ISSUE_BODY"] == payload["issue"]["body"]
    assert env_plain["SOURCE_ACTION"] == "implement"
    assert env_plain["SOURCE_TRIGGER"] == "comment"
    assert env_plain["SOURCE_TRIGGER_COMMAND"] == "/agent implement"


@pytest.mark.asyncio
async def test_agent_implement_comment_without_provider_sets_awaiting_human(monkeypatch):
    payload = load_payload("issue_labeled.json")
    event = issue_comment_event(payload, labels=["bug"])

    response, batch, _core, provider = await post_event(monkeypatch, event)

    assert response.status_code == 200
    assert response.json()["ignored"] is True
    assert batch.created_jobs == []
    assert provider.replaced_labels == [("acme/widgets", "7", ["bug", "agent:status:awaiting-human"])]
    assert provider.comments[0][2] == "`/agent implement` needs a provider label such as `ai-pr-aider`."


@pytest.mark.asyncio
async def test_normal_issue_comment_does_not_trigger_job(monkeypatch):
    payload = load_payload("issue_labeled.json")
    event = issue_comment_event(payload, body="normal comment")

    response, batch, _core, _provider = await post_event(monkeypatch, event)

    assert response.status_code == 200
    assert response.json()["ignored"] is True
    assert batch.created_jobs == []


@pytest.mark.asyncio
async def test_known_but_unimplemented_action_sets_awaiting_human(monkeypatch):
    import app.agent_orchestrator as agent_orchestrator_module

    monkeypatch.setattr(agent_orchestrator_module, "IMPLEMENTED_ACTIONS", frozenset({"implement"}))

    payload = load_payload("issue_labeled.json")
    event = issue_comment_event(payload, body="/agent plan")

    response, batch, _core, provider = await post_event(monkeypatch, event)

    assert response.status_code == 200
    assert response.json()["reason"] == "action not implemented: plan"
    assert batch.created_jobs == []
    assert provider.replaced_labels == [("acme/widgets", "7", ["bug", "ai-pr-claude", "agent:status:awaiting-human"])]
    assert provider.comments[0][2] == "`/agent plan` is recognized, but `plan` is not implemented yet."


@pytest.mark.asyncio
async def test_github_webhook_issue_body_truncated(monkeypatch):
    payload = load_payload("issue_labeled.json")
    long_body = "Z" * (MAX_ISSUE_BODY_CHARS + 500)
    event = issue_comment_event(payload)
    event.issue.body = long_body

    response, batch, _core, _provider = await post_event(monkeypatch, event)

    assert response.status_code == 200
    assert response.json()["triggered"] is True
    env_plain = plain_env_from_job(batch)
    assert len(env_plain["SOURCE_ISSUE_BODY"]) == MAX_ISSUE_BODY_CHARS
    assert env_plain["SOURCE_ISSUE_BODY"] == "Z" * MAX_ISSUE_BODY_CHARS
