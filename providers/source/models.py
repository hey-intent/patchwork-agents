from __future__ import annotations

from datetime import datetime
from typing import Literal, get_args

from pydantic import BaseModel, Field


class Issue(BaseModel):
    id: str
    number: int
    repo: str
    title: str
    body: str
    labels: list[str]
    author: str
    url: str
    state: Literal["open", "closed"]
    created_at: datetime
    updated_at: datetime
    raw: dict = Field(default_factory=dict)


class Comment(BaseModel):
    id: str
    issue_id: str
    body: str
    author: str
    created_at: datetime


class PullRequest(BaseModel):
    id: str
    number: int
    repo: str
    title: str
    body: str
    branch: str
    base: str
    state: Literal["open", "closed", "merged"]
    url: str
    issue_id: str | None = None


ActionName = Literal["spec", "plan", "implement", "review", "continue"]

AGENT_ACTION_NAMES: frozenset[str] = frozenset(get_args(ActionName))


class ActionTrigger(BaseModel):
    action: ActionName
    source: Literal["comment"]
    raw_command: str


WebhookEventType = Literal[
    "issue_opened",
    "issue_labeled",
    "issue_unlabeled",
    "issue_commented",
    "pr_opened",
    "pr_merged",
    "pr_closed",
]


class WebhookEvent(BaseModel):
    type: WebhookEventType
    actor: str
    repo: str
    source_installation_id: str | None = None
    issue: Issue | None = None
    pr: PullRequest | None = None
    comment: Comment | None = None
    label: str | None = None
    raw: dict = Field(default_factory=dict)
