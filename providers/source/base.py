from __future__ import annotations

from abc import ABC, abstractmethod

from .models import ActionTrigger, Comment, Issue, PullRequest, WebhookEvent


class SourceProviderError(Exception):
    """Base exception for source-provider failures."""


class SourceProviderConfigurationError(SourceProviderError):
    """Raised when provider configuration is missing or invalid."""


class SourceProviderAPIError(SourceProviderError):
    """Raised when the remote source-provider API fails."""


class SourceProvider(ABC):
    """Source of truth for issues and PRs.

    A single provider handles both issue tracking and code hosting for now.
    Splitting these concerns is left to implementations.
    """

    name: str

    @abstractmethod
    async def verify_webhook(self, headers: dict, body: bytes) -> bool:
        """Return whether the webhook request is authentic."""

    @abstractmethod
    async def parse_event(self, headers: dict, body: bytes) -> WebhookEvent | None:
        """Parse provider-specific webhook payload into a generic event."""

    @abstractmethod
    def get_action_trigger(self, event: WebhookEvent) -> ActionTrigger | None:
        """Return an agent action requested by a source event, if any."""

    @abstractmethod
    async def can_trigger_action(self, event: WebhookEvent, trigger: ActionTrigger) -> bool:
        """Return whether the event actor is allowed to trigger this action."""

    @abstractmethod
    async def get_issue(self, repo: str, issue_id: str) -> Issue:
        """Return one issue by provider-specific issue identifier."""

    @abstractmethod
    async def list_issues(
        self,
        repo: str,
        *,
        labels: list[str] | None = None,
        state: str = "open",
    ) -> list[Issue]:
        """Return issues matching the provided filters."""

    @abstractmethod
    async def list_issue_comments(self, repo: str, issue_id: str) -> list[Comment]:
        """Return comments for an issue."""

    @abstractmethod
    async def add_issue_comment(self, repo: str, issue_id: str, body: str) -> Comment:
        """Add a comment to an issue."""

    @abstractmethod
    async def get_issue_labels(self, repo: str, issue_id: str) -> list[str]:
        """Return labels for an issue."""

    @abstractmethod
    async def add_issue_label(self, repo: str, issue_id: str, label: str) -> None:
        """Add a label to an issue."""

    @abstractmethod
    async def delete_issue_label(self, repo: str, issue_id: str, label: str) -> None:
        """Delete a label from an issue."""

    @abstractmethod
    async def replace_issue_labels(self, repo: str, issue_id: str, labels: list[str]) -> None:
        """Replace all labels on an issue."""

    @abstractmethod
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
        """Create a pull request."""

    @abstractmethod
    async def get_pr(self, repo: str, pr_id: str) -> PullRequest:
        """Return one pull request."""

    @abstractmethod
    async def get_clone_credentials(self, repo: str) -> tuple[str, str]:
        """Return (username, token) usable for git HTTPS auth.

        Implementations should return the shortest-lived and narrowest-scoped
        credential supported by the provider/deployment.
        """
