from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from kubernetes.client.rest import ApiException

from app.config import PROVIDER_CONFIG, ProviderConfig
from providers.source import (
    SourceProvider,
    SourceProviderAPIError,
    SourceProviderConfigurationError,
    SourceProviderError,
)

MAX_ISSUE_BODY_CHARS = 65536

IMPLEMENTED_ACTIONS = {"implement"}
AGENT_STATUS_PREFIX = "agent:status:"
AGENT_STATUS_IDLE = "agent:status:idle"
AGENT_STATUS_RUNNING = "agent:status:running"
AGENT_STATUS_AWAITING_HUMAN = "agent:status:awaiting-human"
AGENT_STATUS_BLOCKED = "agent:status:blocked"
AGENT_STATUS_FAILED = "agent:status:failed"

logger = logging.getLogger("uvicorn.error")


class AgentOrchestrator:
    def __init__(
        self,
        *,
        namespace: str,
        provider_label_prefix: str,
        load_k8s_client: Callable[[], tuple[Any, Any]],
        build_worker_job: Callable[[str, ProviderConfig, str, dict[str, str], str], Any],
        create_or_replace_secret: Callable[[Any, str, dict[str, str]], None],
        delete_secret_if_exists: Callable[[Any, str], None],
        attach_job_owner_to_secret: Callable[[Any, str, str, str], None],
        safe_name: Callable[[str], str],
    ) -> None:
        self.namespace = namespace
        self.provider_label_prefix = provider_label_prefix
        self.load_k8s_client = load_k8s_client
        self.build_worker_job = build_worker_job
        self.create_or_replace_secret = create_or_replace_secret
        self.delete_secret_if_exists = delete_secret_if_exists
        self.attach_job_owner_to_secret = attach_job_owner_to_secret
        self.safe_name = safe_name

    async def handle_webhook(self, source_provider: SourceProvider, headers: dict, body: bytes) -> dict:
        if not await source_provider.verify_webhook(headers, body):
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=401, detail="invalid webhook signature")

        source_event = await source_provider.parse_event(headers, body)
        if not source_event:
            logger.info("Ignored unhandled source event")
            return {"ok": True, "ignored": True, "reason": "no source event"}

        logger.info("Webhook received: type=%s repo=%s", source_event.type, source_event.repo)

        if source_event.issue and source_event.type in {"issue_opened", "issue_labeled"}:
            provider = self.find_provider(source_event.issue.labels)
            if provider:
                await self.replace_agent_status(
                    source_provider,
                    source_event.repo,
                    str(source_event.issue.number),
                    source_event.issue.labels,
                    AGENT_STATUS_IDLE,
                )

        trigger = source_provider.get_action_trigger(source_event)
        if not trigger:
            reason = f"no action trigger for source_event={source_event.type}"
            logger.info("Not triggering: %s", reason)
            return {"ok": True, "ignored": True, "reason": reason}

        if not source_event.issue:
            logger.error("Action trigger has no issue context")
            raise HTTPException(status_code=400, detail="missing source issue")

        repo_full = source_event.repo
        issue = source_event.issue
        issue_number = issue.number
        issue_id = str(issue_number)

        try:
            can_trigger = await source_provider.can_trigger_action(source_event, trigger)
        except SourceProviderAPIError as ex:
            await self.replace_agent_status(source_provider, repo_full, issue_id, issue.labels, AGENT_STATUS_BLOCKED)
            await source_provider.add_issue_comment(
                repo_full, issue_id, "Agent is blocked: unable to verify command permissions."
            )
            logger.exception("Source provider permission check failed: %s", ex)
            raise HTTPException(status_code=502, detail="source provider API request failed") from ex

        if not can_trigger:
            logger.info("Actor %s is not allowed to trigger agent action %s", source_event.actor, trigger.action)
            return {"ok": True, "ignored": True, "reason": "actor not allowed"}

        if trigger.action not in IMPLEMENTED_ACTIONS:
            await self.replace_agent_status(
                source_provider,
                repo_full,
                issue_id,
                issue.labels,
                AGENT_STATUS_AWAITING_HUMAN,
            )
            await source_provider.add_issue_comment(
                repo_full,
                issue_id,
                f"`{trigger.raw_command}` is recognized, but `{trigger.action}` is not implemented yet.",
            )
            return {
                "ok": True,
                "ignored": True,
                "reason": f"action not implemented: {trigger.action}",
                "action": trigger.action,
            }

        provider = self.find_provider(issue.labels)
        if not provider:
            await self.replace_agent_status(
                source_provider,
                repo_full,
                issue_id,
                issue.labels,
                AGENT_STATUS_AWAITING_HUMAN,
            )
            await source_provider.add_issue_comment(
                repo_full,
                issue_id,
                f"`{trigger.raw_command}` needs a provider label such as `{self.provider_label_prefix}aider`.",
            )
            reason = f"no label matching {self.provider_label_prefix}* for action={trigger.action}"
            logger.info("Not triggering: %s", reason)
            return {"ok": True, "ignored": True, "reason": reason}

        cfg = PROVIDER_CONFIG.get(provider)
        if not cfg:
            raise HTTPException(status_code=400, detail=f"unknown provider: {provider[:20]}")

        installation_id = source_event.source_installation_id
        if not installation_id:
            logger.error("Missing source installation id in source event")
            raise HTTPException(status_code=400, detail="missing source installation id")

        job_name = self.safe_name(f"ai-pr-{repo_full.replace('/', '-')}-{issue_number}-{provider}")
        batch, core = self.load_k8s_client()

        try:
            _, github_token = await source_provider.get_clone_credentials(repo_full)
        except SourceProviderConfigurationError as ex:
            await self.replace_agent_status(source_provider, repo_full, issue_id, issue.labels, AGENT_STATUS_BLOCKED)
            await source_provider.add_issue_comment(
                repo_full, issue_id, "Agent is blocked: source provider is not configured."
            )
            logger.exception("Source provider configuration error: %s", ex)
            raise HTTPException(status_code=500, detail="source provider is not configured") from ex
        except SourceProviderAPIError as ex:
            await self.replace_agent_status(source_provider, repo_full, issue_id, issue.labels, AGENT_STATUS_BLOCKED)
            await source_provider.add_issue_comment(
                repo_full, issue_id, "Agent is blocked: source provider API request failed."
            )
            logger.exception("Source provider API error: %s", ex)
            raise HTTPException(status_code=502, detail="source provider API request failed") from ex
        except SourceProviderError as ex:
            await self.replace_agent_status(source_provider, repo_full, issue_id, issue.labels, AGENT_STATUS_BLOCKED)
            await source_provider.add_issue_comment(repo_full, issue_id, "Agent is blocked: source provider error.")
            logger.exception("Source provider error: %s", ex)
            raise HTTPException(status_code=500, detail="source provider error") from ex

        token_secret_name = self.safe_name(f"{job_name}-gh-token")
        self.create_or_replace_secret(core, token_secret_name, {"GITHUB_TOKEN": github_token})

        job = self.build_worker_job(
            job_name,
            cfg,
            provider,
            {
                "SOURCE_REPO": repo_full,
                "SOURCE_ISSUE_NUMBER": issue_id,
                "SOURCE_ISSUE_TITLE": issue.title[:200],
                "SOURCE_ISSUE_BODY": issue.body[:MAX_ISSUE_BODY_CHARS],
                "SOURCE_ISSUE_URL": issue.url,
                "SOURCE_INSTALLATION_ID": str(installation_id),
                "SOURCE_ACTION": trigger.action,
                "SOURCE_TRIGGER": trigger.source,
                "SOURCE_TRIGGER_COMMAND": trigger.raw_command,
            },
            token_secret_name,
        )

        logger.info("Creating Job: name=%s namespace=%s image=%s", job_name, self.namespace, cfg.image)
        try:
            created_obj = batch.create_namespaced_job(namespace=self.namespace, body=job)
            created_name = getattr(created_obj.metadata, "name", None)
            created_uid = getattr(created_obj.metadata, "uid", None)
            logger.info("K8s Job created: name=%s uid=%s", created_name, created_uid)
            if created_name and created_uid:
                try:
                    self.attach_job_owner_to_secret(core, token_secret_name, created_name, created_uid)
                except Exception as ex:
                    logger.warning("Unable to attach ownerReference on secret %s: %s", token_secret_name, ex)
            await self.replace_agent_status(
                source_provider,
                repo_full,
                issue_id,
                issue.labels,
                AGENT_STATUS_RUNNING,
            )
            created = True
        except ApiException as e:
            logger.exception(
                "ApiException creating job: status=%s reason=%s ",
                getattr(e, "status", None),
                getattr(e, "reason", None),
            )
            self.delete_secret_if_exists(core, token_secret_name)
            if getattr(e, "status", None) == 409:
                logger.info("Job already exists (idempotent): %s", job_name)
                await self.replace_agent_status(
                    source_provider,
                    repo_full,
                    issue_id,
                    issue.labels,
                    AGENT_STATUS_AWAITING_HUMAN,
                )
                await source_provider.add_issue_comment(
                    repo_full,
                    issue_id,
                    "Agent command was received, but a worker job for this issue already exists.",
                )
                created = False
            else:
                await self.replace_agent_status(source_provider, repo_full, issue_id, issue.labels, AGENT_STATUS_FAILED)
                raise HTTPException(
                    status_code=500,
                    detail="k8s error creating job",
                ) from e
        except Exception as ex:
            self.delete_secret_if_exists(core, token_secret_name)
            await self.replace_agent_status(source_provider, repo_full, issue_id, issue.labels, AGENT_STATUS_FAILED)
            logger.exception("Unexpected error creating job: %s", ex)
            raise HTTPException(status_code=500, detail="internal error") from ex

        return {
            "ok": True,
            "triggered": True,
            "created": created,
            "job": job_name,
            "namespace": self.namespace,
            "repo": repo_full,
            "issue_number": issue_number,
            "action": trigger.action,
            "provider": provider,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def find_provider(self, labels: list[str]) -> str | None:
        for label in labels:
            result = self.extract_provider_from_label(label)
            if result:
                return result
        return None

    def extract_provider_from_label(self, label_name: str | None) -> str | None:
        if (
            not label_name
            or not label_name.startswith(self.provider_label_prefix)
            or len(label_name) <= len(self.provider_label_prefix)
        ):
            return None
        suffix = label_name[len(self.provider_label_prefix) :]
        if suffix in PROVIDER_CONFIG:
            return suffix
        return None

    async def replace_agent_status(
        self,
        source_provider: SourceProvider,
        repo: str,
        issue_id: str,
        labels: list[str],
        status: str,
    ) -> None:
        next_labels = [label for label in labels if not label.startswith(AGENT_STATUS_PREFIX)]
        if status not in next_labels:
            next_labels.append(status)
        if next_labels != labels:
            await source_provider.replace_issue_labels(repo, issue_id, next_labels)
