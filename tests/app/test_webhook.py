from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import app.app as orchestrator
from providers.source.models import WebhookEvent

FIXTURES = Path(__file__).parents[1] / "providers" / "fixtures"


class FakeSourceProvider:
    def __init__(self, *, verify: bool = True, event: WebhookEvent | None = None) -> None:
        self.verify = verify
        self.event = event

    async def verify_webhook(self, headers: dict, body: bytes) -> bool:
        return self.verify

    async def parse_event(self, headers: dict, body: bytes) -> WebhookEvent | None:
        return self.event

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


def load_payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


async def post_webhook(body: bytes, headers: dict):
    transport = httpx.ASGITransport(app=orchestrator.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/webhook/github", content=body, headers=headers)


def _plain_env_from_job(batch: FakeBatch) -> dict[str, str]:
    _ns, job = batch.created_jobs[0]
    container = job.spec.template.spec.containers[0]
    return {e.name: e.value for e in container.env if getattr(e, "value", None) is not None}


async def _post_labeled_claude_webhook(monkeypatch, payload: dict):
    """Simulate validated webhook: issues event, ai-pr-claude label, fake k8s."""
    event = WebhookEvent(
        type="issue_labeled",
        actor="bob",
        repo=payload["repository"]["full_name"],
        label="ai-pr-claude",
        raw=payload,
    )
    batch = FakeBatch()
    core = FakeCore()
    monkeypatch.setattr(orchestrator, "get_source_provider", lambda: FakeSourceProvider(event=event))
    monkeypatch.setattr(orchestrator, "load_k8s_client", lambda: (batch, core))
    response = await post_webhook(
        json.dumps(payload).encode("utf-8"),
        {"X-GitHub-Event": "issues"},
    )
    return response, batch, core


@pytest.mark.asyncio
async def test_github_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(orchestrator, "get_source_provider", lambda: FakeSourceProvider(verify=False))

    response = await post_webhook(b"{}", {"X-GitHub-Event": "issues"})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_github_webhook_ignores_non_issue_event(monkeypatch):
    monkeypatch.setattr(orchestrator, "get_source_provider", lambda: FakeSourceProvider())

    response = await post_webhook(b"{}", {"X-GitHub-Event": "pull_request"})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "ignored": True, "reason": "event=pull_request"}


@pytest.mark.asyncio
async def test_github_webhook_triggers_worker_job(monkeypatch):
    payload = load_payload("issue_labeled.json")
    response, batch, core = await _post_labeled_claude_webhook(monkeypatch, payload)

    assert response.status_code == 200
    data = response.json()
    assert data["triggered"] is True
    assert data["created"] is True
    assert data["repo"] == "acme/widgets"
    assert data["issue_number"] == 7
    assert data["provider"] == "claude"
    assert len(core.secrets) == 1
    assert core.secrets[0][1].string_data == {"GITHUB_TOKEN": "installation-token"}
    assert len(batch.created_jobs) == 1
    env_plain = _plain_env_from_job(batch)
    assert env_plain["SOURCE_ISSUE_BODY"] == payload["issue"]["body"]


@pytest.mark.asyncio
async def test_github_webhook_issue_body_truncated(monkeypatch):
    payload = copy.deepcopy(load_payload("issue_labeled.json"))
    cap = orchestrator._MAX_ISSUE_BODY_CHARS
    payload["issue"]["body"] = "Z" * (cap + 500)

    response, batch, _core = await _post_labeled_claude_webhook(monkeypatch, payload)

    assert response.status_code == 200
    assert response.json()["triggered"] is True
    env_plain = _plain_env_from_job(batch)
    assert len(env_plain["SOURCE_ISSUE_BODY"]) == cap
    assert env_plain["SOURCE_ISSUE_BODY"] == "Z" * cap


@pytest.mark.asyncio
async def test_github_webhook_issue_body_missing(monkeypatch):
    payload = copy.deepcopy(load_payload("issue_labeled.json"))
    del payload["issue"]["body"]

    response, batch, _core = await _post_labeled_claude_webhook(monkeypatch, payload)

    assert response.status_code == 200
    assert response.json()["triggered"] is True
    env_plain = _plain_env_from_job(batch)
    assert env_plain["SOURCE_ISSUE_BODY"] == ""


@pytest.mark.asyncio
async def test_github_webhook_issue_body_non_string(monkeypatch):
    payload = copy.deepcopy(load_payload("issue_labeled.json"))
    payload["issue"]["body"] = ["unexpected", "list"]

    response, batch, _core = await _post_labeled_claude_webhook(monkeypatch, payload)

    assert response.status_code == 200
    assert response.json()["triggered"] is True
    env_plain = _plain_env_from_job(batch)
    assert env_plain["SOURCE_ISSUE_BODY"] == ""
