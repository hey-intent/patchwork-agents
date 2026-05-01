from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    namespace: str = os.getenv("NAMESPACE", "ai-bot")
    job_ttl_seconds: int = int(os.getenv("JOB_TTL_SECONDS", "3600"))
    trigger_prefix: str = os.getenv("TRIGGER_PREFIX", "ai-pr-")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    enable_k8s_debug: bool = _env_bool("ENABLE_K8S_DEBUG")

    claude_worker_image: str = os.getenv("CLAUDE_WORKER_IMAGE", "worker-claude:latest")
    codex_worker_image: str = os.getenv("CODEX_WORKER_IMAGE", "worker-codex:latest")
    aider_worker_image: str = os.getenv("AIDER_WORKER_IMAGE", "worker-aider:latest")

    github_app_id: str = os.getenv("GITHUB_APP_ID", "")
    github_private_key: str = os.getenv("GITHUB_PRIVATE_KEY", "")


@dataclass(frozen=True)
class ProviderSecretRef:
    env_name: str
    secret_name: str
    secret_key: str


@dataclass(frozen=True)
class ProviderConfig:
    image: str
    ai_provider: str
    api_secret: ProviderSecretRef
    extra_env: tuple[tuple[str, str], ...] = ()
    extra_secrets: tuple[ProviderSecretRef, ...] = ()


settings = Settings()


PROVIDER_CONFIG: dict[str, ProviderConfig] = {
    "claude": ProviderConfig(
        image=settings.claude_worker_image,
        ai_provider="claude_code",
        api_secret=ProviderSecretRef("ANTHROPIC_API_KEY", "anthropic-api-key", "ANTHROPIC_API_KEY"),
    ),
    "codex": ProviderConfig(
        image=settings.codex_worker_image,
        ai_provider="openai",
        api_secret=ProviderSecretRef("OPENAI_API_KEY", "openai-api-key", "OPENAI_API_KEY"),
    ),
    "aider": ProviderConfig(
        image=settings.aider_worker_image,
        ai_provider="aider",
        api_secret=ProviderSecretRef("OPENROUTER_API_KEY", "openrouter-api-key", "OPENROUTER_API_KEY"),
    ),
}
