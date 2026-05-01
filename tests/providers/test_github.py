from __future__ import annotations

import hashlib
import hmac
from pathlib import Path

import pytest
import respx
from httpx import Response

from providers.source.github import GitHubProvider

FIXTURES = Path(__file__).parent / "fixtures"
REPO = "acme/widgets"


def provider() -> GitHubProvider:
    return GitHubProvider(
        app_id="123",
        private_key="test-private-key",
        webhook_secret="webhook-secret",
    )


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def signature(body: bytes, secret: str = "webhook-secret") -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.mark.asyncio
async def test_verify_webhook_valid_signature():
    body = load_fixture("issue_labeled.json")
    headers = {"X-Hub-Signature-256": signature(body)}

    assert await provider().verify_webhook(headers, body) is True


@pytest.mark.asyncio
async def test_verify_webhook_invalid_signature():
    body = load_fixture("issue_labeled.json")
    headers = {"X-Hub-Signature-256": "sha256=bad"}

    assert await provider().verify_webhook(headers, body) is False


@pytest.mark.asyncio
async def test_verify_webhook_rejects_non_hex_signature():
    body = load_fixture("issue_labeled.json")
    headers = {"X-Hub-Signature-256": f"sha256={'z' * 64}"}

    assert await provider().verify_webhook(headers, body) is False


@pytest.mark.asyncio
async def test_parse_event_rejects_invalid_body():
    assert await provider().parse_event({"X-GitHub-Event": "issues"}, b"\xff") is None
    assert await provider().parse_event({"X-GitHub-Event": "issues"}, b"[]") is None


@pytest.mark.asyncio
async def test_parse_event_issue_opened():
    body = load_fixture("issue_opened.json")
    event = await provider().parse_event({"X-GitHub-Event": "issues"}, body)

    assert event is not None
    assert event.type == "issue_opened"
    assert event.actor == "alice"
    assert event.repo == REPO
    assert event.issue is not None
    assert event.issue.number == 7


@pytest.mark.asyncio
async def test_parse_event_issue_labeled():
    body = load_fixture("issue_labeled.json")
    event = await provider().parse_event({"X-GitHub-Event": "issues"}, body)

    assert event is not None
    assert event.type == "issue_labeled"
    assert event.actor == "bob"
    assert event.repo == REPO
    assert event.label == "ai-pr-claude"
    assert event.issue is not None
    assert event.issue.number == 7
    assert event.issue.labels == ["bug", "ai-pr-claude"]


@pytest.mark.asyncio
async def test_parse_event_issue_unlabeled():
    body = load_fixture("issue_unlabeled.json")
    event = await provider().parse_event({"X-GitHub-Event": "issues"}, body)

    assert event is not None
    assert event.type == "issue_unlabeled"
    assert event.label == "ai-pr-claude"
    assert event.issue is not None
    assert event.issue.labels == ["bug"]


@pytest.mark.asyncio
async def test_parse_event_pr_opened():
    body = load_fixture("pr_opened.json")
    event = await provider().parse_event({"X-GitHub-Event": "pull_request"}, body)

    assert event is not None
    assert event.type == "pr_opened"
    assert event.actor == "alice"
    assert event.repo == REPO
    assert event.pr is not None
    assert event.pr.number == 12
    assert event.pr.branch == "feature/source-provider"
    assert event.pr.base == "main"


@pytest.mark.asyncio
@respx.mock
async def test_add_issue_label(monkeypatch):
    gh = provider()
    gh._installation_ids_by_repo[REPO] = "42"
    monkeypatch.setattr(gh, "_build_app_jwt", lambda: "jwt")
    respx.post("https://api.github.com/app/installations/42/access_tokens").mock(
        return_value=Response(201, json={"token": "installation-token"})
    )
    route = respx.post(f"https://api.github.com/repos/{REPO}/issues/7/labels").mock(
        return_value=Response(200, json=[{"name": "ai-pr-claude"}])
    )

    await gh.add_issue_label(REPO, "7", "ai-pr-claude")

    assert route.called
    assert route.calls.last.request.content == b'{"labels":["ai-pr-claude"]}'


@pytest.mark.asyncio
@respx.mock
async def test_delete_issue_label(monkeypatch):
    gh = provider()
    gh._installation_ids_by_repo[REPO] = "42"
    monkeypatch.setattr(gh, "_build_app_jwt", lambda: "jwt")
    respx.post("https://api.github.com/app/installations/42/access_tokens").mock(
        return_value=Response(201, json={"token": "installation-token"})
    )
    route = respx.delete(f"https://api.github.com/repos/{REPO}/issues/7/labels/ai-pr-claude").mock(
        return_value=Response(200, json=[])
    )

    await gh.delete_issue_label(REPO, "7", "ai-pr-claude")

    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_replace_issue_labels_atomic(monkeypatch):
    gh = provider()
    gh._installation_ids_by_repo[REPO] = "42"
    monkeypatch.setattr(gh, "_build_app_jwt", lambda: "jwt")
    respx.post("https://api.github.com/app/installations/42/access_tokens").mock(
        return_value=Response(201, json={"token": "installation-token"})
    )
    route = respx.put(f"https://api.github.com/repos/{REPO}/issues/7/labels").mock(
        return_value=Response(200, json=[{"name": "phase-ready"}])
    )

    await gh.replace_issue_labels(REPO, "7", ["phase-ready"])

    assert route.called
    assert route.calls.last.request.content == b'{"labels":["phase-ready"]}'


@pytest.mark.asyncio
@respx.mock
async def test_get_clone_credentials_returns_ephemeral_token(monkeypatch):
    gh = provider()
    monkeypatch.setattr(gh, "_build_app_jwt", lambda: "jwt")
    respx.get(f"https://api.github.com/repos/{REPO}/installation").mock(return_value=Response(200, json={"id": 42}))
    respx.post("https://api.github.com/app/installations/42/access_tokens").mock(
        return_value=Response(201, json={"token": "installation-token"})
    )

    username, token = await gh.get_clone_credentials(REPO)

    assert username == "x-access-token"
    assert token == "installation-token"


@pytest.mark.asyncio
@respx.mock
async def test_installation_token_is_cached(monkeypatch):
    gh = provider()
    monkeypatch.setattr(gh, "_build_app_jwt", lambda: "jwt")
    respx.get(f"https://api.github.com/repos/{REPO}/installation").mock(return_value=Response(200, json={"id": 42}))
    token_route = respx.post("https://api.github.com/app/installations/42/access_tokens").mock(
        return_value=Response(201, json={"token": "installation-token"})
    )

    assert await gh._installation_token_for_repo(REPO) == "installation-token"
    assert await gh._installation_token_for_repo(REPO) == "installation-token"

    assert token_route.call_count == 1
