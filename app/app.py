#!/usr/bin/env python3
"""
app.py - FastAPI service to manage a GitHub App secret and create Kubernetes Jobs.
...
(la même docstring)
"""

from __future__ import annotations
import os
import re
import base64
import typing as t
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
import logging
import secrets
import time

from fastapi import FastAPI, HTTPException, Body, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import hmac
import hashlib
import jwt
import httpx

# --- Config / env ---
NAMESPACE = os.getenv("NAMESPACE", "ai-bot")
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "3600"))
TRIGGER_PREFIX = os.getenv("TRIGGER_PREFIX", "ai-pr-")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # required in prod
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # bearer token for admin endpoints
ENABLE_K8S_DEBUG = os.getenv("ENABLE_K8S_DEBUG", "false").lower() in ("1", "true", "yes")

# Worker images (configurable via env)
CLAUDE_WORKER_IMAGE = os.getenv("CLAUDE_WORKER_IMAGE", "worker-claude:latest")
CODEX_WORKER_IMAGE = os.getenv("CODEX_WORKER_IMAGE", "worker-codex:latest")
AIDER_WORKER_IMAGE = os.getenv("AIDER_WORKER_IMAGE", "worker-aider:latest")

# GitHub App credentials (used to generate ephemeral installation tokens)
GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "")
GITHUB_PRIVATE_KEY = os.getenv("GITHUB_PRIVATE_KEY", "")


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


# Per-provider config: image and API key secret
PROVIDER_CONFIG: dict[str, ProviderConfig] = {
    "claude": ProviderConfig(
        image=CLAUDE_WORKER_IMAGE,
        ai_provider="claude_code",
        api_secret=ProviderSecretRef("ANTHROPIC_API_KEY", "anthropic-api-key", "ANTHROPIC_API_KEY"),
    ),
    "codex": ProviderConfig(
        image=CODEX_WORKER_IMAGE,
        ai_provider="openai",
        api_secret=ProviderSecretRef("OPENAI_API_KEY", "openai-api-key", "OPENAI_API_KEY"),
    ),
    "aider": ProviderConfig(
        image=AIDER_WORKER_IMAGE,
        ai_provider="aider",
        api_secret=ProviderSecretRef("OPENROUTER_API_KEY", "openrouter-api-key", "OPENROUTER_API_KEY"),
    ),
}

# --- Logging setup ---
# Use uvicorn's logger so messages aren't disabled by uvicorn's dictConfig
logger = logging.getLogger("uvicorn.error")

if ENABLE_K8S_DEBUG:
    # debug du client k8s / urllib3 (affiche les requêtes HTTP vers l'API server)
    logging.getLogger("kubernetes").setLevel(logging.DEBUG)
    logging.getLogger("urllib3").setLevel(logging.DEBUG)
    logger.warning("Kubernetes client debug ENABLED (ENABLE_K8S_DEBUG=true) - do NOT enable in production if logs leak secrets")

app = FastAPI(title="orchestrator", version="1.0")
bearer_scheme = HTTPBearer(auto_error=False)


def verify_admin_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    """Require a valid bearer token for admin endpoints."""
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN not configured")
    if not credentials or not secrets.compare_digest(credentials.credentials, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
    return True

def load_k8s_client():
    """
    Load kube config: prefer in-cluster, fallback to KUBECONFIG if set, else raise.
    Returns BatchV1Api and CoreV1Api clients.
    """
    try:
        config.load_incluster_config()
    except Exception:
        kubeconfig = os.getenv("KUBECONFIG")
        if kubeconfig:
            config.load_kube_config(config_file=kubeconfig)
        else:
            # last resort: try default kube config
            config.load_kube_config()
    return client.BatchV1Api(), client.CoreV1Api()

def safe_name(s: str) -> str:
    s2 = re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")
    return (s2[:50] or "job")

async def _generate_installation_token(installation_id: str) -> str:
    """Generate an ephemeral GitHub installation token (1h) from App credentials."""
    if not GITHUB_APP_ID or not GITHUB_PRIVATE_KEY:
        raise HTTPException(status_code=500, detail="GITHUB_APP_ID and GITHUB_PRIVATE_KEY must be configured")

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": GITHUB_APP_ID,
    }
    encoded_jwt = jwt.encode(payload, GITHUB_PRIVATE_KEY, algorithm="RS256")

    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json",
    }

    async with httpx.AsyncClient() as http_client:
        resp = await http_client.post(url, headers=headers)

    if resp.status_code != 201:
        logger.error("GitHub token exchange failed: status=%s body=%s", resp.status_code, resp.text[:500])
        raise HTTPException(status_code=500, detail=f"GitHub installation token exchange failed ({resp.status_code})")

    token = resp.json().get("token")
    if not token:
        raise HTTPException(status_code=500, detail="GitHub API returned no token")

    return token


def _build_worker_job(
    job_name: str,
    cfg: ProviderConfig,
    provider: str,
    env_vars: dict[str, str],
    github_token_secret_name: str,
) -> client.V1Job:
    """Build a K8s Job object for a worker pod."""
    env_list = [
        client.V1EnvVar(name="AI_PROVIDER", value=cfg.ai_provider),
        client.V1EnvVar(
            name="GITHUB_TOKEN",
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name=github_token_secret_name, key="GITHUB_TOKEN")
            ),
        ),
    ]
    for k, v in env_vars.items():
        env_list.append(client.V1EnvVar(name=k, value=v))

    # Provider-specific extra env vars (plain values)
    for env_key, env_val in cfg.extra_env:
        env_list.append(client.V1EnvVar(name=env_key, value=env_val))

    # Provider-specific API key
    api_secret = cfg.api_secret
    env_list.append(
        client.V1EnvVar(
            name=api_secret.env_name,
            value_from=client.V1EnvVarSource(
                secret_key_ref=client.V1SecretKeySelector(name=api_secret.secret_name, key=api_secret.secret_key)
            ),
        )
    )

    # Provider-specific extra secrets (e.g. model selection)
    for ref in cfg.extra_secrets:
        env_list.append(
            client.V1EnvVar(
                name=ref.env_name,
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(name=ref.secret_name, key=ref.secret_key)
                ),
            )
        )

    container = client.V1Container(
        name="worker", image=cfg.image, image_pull_policy="Never",
        env=env_list,
    )
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"job-name": job_name, "provider": provider}),
        spec=client.V1PodSpec(restart_policy="Never", containers=[container]),
    )
    job_spec = client.V1JobSpec(template=template, backoff_limit=0, ttl_seconds_after_finished=JOB_TTL_SECONDS)
    return client.V1Job(metadata=client.V1ObjectMeta(name=job_name, namespace=NAMESPACE), spec=job_spec)


def _create_or_replace_secret(core: client.CoreV1Api, name: str, string_data: dict[str, str]) -> None:
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE),
        type="Opaque",
        string_data=string_data,
    )
    try:
        core.create_namespaced_secret(namespace=NAMESPACE, body=body)
    except ApiException as e:
        if e.status == 409:
            core.patch_namespaced_secret(name=name, namespace=NAMESPACE, body=body)
        else:
            raise


def _delete_secret_if_exists(core: client.CoreV1Api, name: str) -> None:
    try:
        core.delete_namespaced_secret(name=name, namespace=NAMESPACE)
    except ApiException as e:
        if e.status != 404:
            raise


def _attach_job_owner_to_secret(core: client.CoreV1Api, secret_name: str, job_name: str, job_uid: str) -> None:
    owner_ref = client.V1OwnerReference(
        api_version="batch/v1",
        kind="Job",
        name=job_name,
        uid=job_uid,
        controller=False,
        block_owner_deletion=False,
    )
    patch_body = {"metadata": {"ownerReferences": [owner_ref.to_dict()]}}
    core.patch_namespaced_secret(name=secret_name, namespace=NAMESPACE, body=patch_body)

class SecretPayload(BaseModel):
    github_app_id: str = Field(..., alias="GITHUB_APP_ID")
    github_private_key_pem: t.Optional[str] = Field(None, alias="GITHUB_PRIVATE_KEY_PEM")
    github_private_key_path: t.Optional[str] = Field(None, alias="GITHUB_PRIVATE_KEY_PATH")
    replace: bool = Field(True, description="If true, apply/replace the secret (default true)")

class JobSpec(BaseModel):
    name: str
    image: t.Optional[str] = CLAUDE_WORKER_IMAGE
    command: t.Optional[t.List[str]] = None
    env: t.Optional[t.Dict[str, str]] = None
    backoff_limit: int = 0

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post("/secrets/github-app", status_code=201)
def create_or_update_github_app_secret(payload: SecretPayload = Body(...), _auth: bool = Depends(verify_admin_token)):
    batch, core = load_k8s_client()
    name = "github-app"
    data = {}

    data["GITHUB_APP_ID"] = payload.github_app_id.encode("utf-8")
    pem_bytes: bytes | None = None
    if payload.github_private_key_pem:
        pem_bytes = payload.github_private_key_pem.encode("utf-8")
    elif payload.github_private_key_path:
        path = payload.github_private_key_path
        if not os.path.isfile(path):
            raise HTTPException(status_code=400, detail=f"private key not found at path: {path}")
        with open(path, "rb") as fh:
            pem_bytes = fh.read()
    else:
        raise HTTPException(status_code=400, detail="either GITHUB_PRIVATE_KEY_PEM or GITHUB_PRIVATE_KEY_PATH is required")

    data_b64 = {k: base64.b64encode(v).decode("utf-8") for k, v in {"GITHUB_APP_ID": data["GITHUB_APP_ID"], "GITHUB_PRIVATE_KEY": pem_bytes}.items()}

    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE),
        type="Opaque",
        data=data_b64,
    )

    try:
        core.patch_namespaced_secret(name=name, namespace=NAMESPACE, body=secret)
        action = "patched"
    except ApiException as e:
        if e.status == 404:
            core.create_namespaced_secret(namespace=NAMESPACE, body=secret)
            action = "created"
        else:
            logger.exception("k8s error when creating/patching secret: status=%s reason=%s", getattr(e,'status',None), getattr(e,'reason',None))
            raise HTTPException(status_code=500, detail=f"k8s error: {e.reason} ({getattr(e, 'status', '')})")
    return {"result": action, "secret": name, "namespace": NAMESPACE, "updated_at": datetime.now(timezone.utc).isoformat()}

@app.post("/webhook/github")
async def github_webhook(request: Request):
    body = await request.body()

    sig256 = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(WEBHOOK_SECRET, body, sig256):
        logger.warning("Invalid webhook signature")
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    if event != "issues":
        logger.info("Ignored event=%s", event)
        return {"ok": True, "ignored": True, "reason": f"event={event}"}

    payload = await request.json()
    action = payload.get("action")

    logger.info("Webhook received: event=issues action=%s repo=%s", action, ((payload.get("repository") or {}).get("full_name")))

    logger.debug("TRIGGER_PREFIX: %s", TRIGGER_PREFIX)
    logger.debug("PAYLOAD LABEL: %s", payload.get("label"))

    # Decide whether we trigger — match any label starting with TRIGGER_PREFIX
    # e.g. "ai-pr-claude", "ai-pr-openai"
    provider = None

    if action == "opened":
        provider = issue_find_provider(payload, TRIGGER_PREFIX)
    elif action == "labeled":
        label = payload.get("label") or {}
        label_name = label.get("name") if isinstance(label, dict) else str(label)
        provider = _extract_provider_from_label(label_name, TRIGGER_PREFIX)
    # else: action not handled

    if not provider:
        reason = f"no label matching {TRIGGER_PREFIX}* for action={action}"
        logger.info("Not triggering: %s", reason)
        return {"ok": True, "ignored": True, "reason": reason}

    # Validate provider before any logging or further use
    cfg = PROVIDER_CONFIG.get(provider)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"unknown provider: {provider[:20]}")

    logger.info("Triggered with provider=%s", provider)

    repo_full = ((payload.get("repository") or {}).get("full_name"))
    issue = payload.get("issue") or {}
    issue_number = issue.get("number")
    issue_title = issue.get("title", "")[:200]
    issue_url = issue.get("html_url", "")
    installation_id = ((payload.get("installation") or {}).get("id"))

    if not repo_full or not issue_number:
        logger.error("Missing repo_full or issue_number")
        raise HTTPException(status_code=400, detail="missing repository.full_name or issue.number")

    if not installation_id:
        logger.error("Missing installation.id in webhook payload")
        raise HTTPException(status_code=400, detail="missing installation.id (is the GitHub App installed?)")

    job_name = safe_name(f"ai-pr-{repo_full.replace('/', '-')}-{issue_number}-{provider}")

    batch, core = load_k8s_client()

    github_token = await _generate_installation_token(str(installation_id))
    token_secret_name = safe_name(f"{job_name}-gh-token")
    _create_or_replace_secret(core, token_secret_name, {"GITHUB_TOKEN": github_token})

    job = _build_worker_job(
        job_name=job_name,
        cfg=cfg,
        provider=provider,
        env_vars={
            "GITHUB_REPO": repo_full,
            "GITHUB_ISSUE_NUMBER": str(issue_number),
            "GITHUB_EVENT_ACTION": str(action),
            "GITHUB_ISSUE_TITLE": issue_title,
            "GITHUB_ISSUE_URL": issue_url,
            "GITHUB_INSTALLATION_ID": str(installation_id),
        },
        github_token_secret_name=token_secret_name,
    )

    # --- Logging around create_namespaced_job ---
    logger.info("Creating Job: name=%s namespace=%s image=%s", job_name, NAMESPACE, cfg.image)
    # logger.debug("Job body: %s", job)  # inutile en prod (peut contenir secrets)

    try:
        created_obj = batch.create_namespaced_job(namespace=NAMESPACE, body=job)
        created_name = getattr(created_obj.metadata, "name", None)
        created_uid = getattr(created_obj.metadata, "uid", None)
        logger.info("K8s Job created: name=%s uid=%s", created_name, created_uid)
        if created_name and created_uid:
            try:
                _attach_job_owner_to_secret(core, token_secret_name, created_name, created_uid)
            except Exception as ex:
                logger.warning("Unable to attach ownerReference on secret %s: %s", token_secret_name, ex)
        created = True
    except ApiException as e:
        # show useful details for debugging
        logger.exception("ApiException creating job: status=%s reason=%s ", getattr(e, "status", None), getattr(e, "reason", None))
        if getattr(e, "status", None) == 409:
            _delete_secret_if_exists(core, token_secret_name)
            logger.info("Job already exists (idempotent): %s", job_name)
            created = False
        else:
            _delete_secret_if_exists(core, token_secret_name)
            raise HTTPException(status_code=500, detail=f"k8s error creating job: {getattr(e,'reason',None)}")
    except Exception as ex:
        _delete_secret_if_exists(core, token_secret_name)
        logger.exception("Unexpected error creating job: %s", ex)
        raise HTTPException(status_code=500, detail="internal error")

    return {
        "ok": True,
        "triggered": True,
        "created": created,
        "job": job_name,
        "namespace": NAMESPACE,
        "repo": repo_full,
        "issue_number": issue_number,
        "action": action,
        "provider": provider,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.post("/jobs/run")
def run_job(_auth: bool = Depends(verify_admin_token)):
    job_id = str(uuid.uuid4())[:8]
    job_name = f"manual-{job_id}"

    container = client.V1Container(
        name="worker",
        image=CLAUDE_WORKER_IMAGE,
        image_pull_policy="Never",
    )

    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"job-name": job_name}),
        spec=client.V1PodSpec(
            restart_policy="Never",
            containers=[container],
        ),
    )

    job_spec = client.V1JobSpec(template=template, backoff_limit=0)

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(name=job_name, namespace=NAMESPACE),
        spec=job_spec,
    )

    batch, _ = load_k8s_client()
    logger.info("Manual run: creating job %s in %s", job_name, NAMESPACE)
    try:
        batch.create_namespaced_job(namespace=NAMESPACE, body=job)
    except ApiException as e:
        logger.exception("ApiException creating manual job: status=%s reason=%s", getattr(e, "status", None), getattr(e, "reason", None))
        raise HTTPException(status_code=500, detail=f"k8s error creating job: {getattr(e,'reason',None)}")

    return {"status": "started", "job_name": job_name}

def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> bool:
    if not secret:
        logger.error("WEBHOOK_SECRET is not configured — rejecting all webhooks")
        return False
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    their_sig = signature_header.split("=", 1)[1].strip()
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    our_sig = mac.hexdigest()
    return hmac.compare_digest(our_sig, their_sig)

def _extract_provider_from_label(label_name: str | None, prefix: str) -> str | None:
    """Return validated provider suffix if label matches prefix, else None."""
    if not label_name or not label_name.startswith(prefix) or len(label_name) <= len(prefix):
        return None
    suffix = label_name[len(prefix):]
    if suffix in PROVIDER_CONFIG:
        return suffix
    return None


def issue_find_provider(payload: dict, prefix: str) -> str | None:
    """Find the first label matching prefix and return the provider suffix, or None."""
    labels = (payload.get("issue") or {}).get("labels") or []
    for lb in labels:
        name = lb.get("name") if isinstance(lb, dict) else str(lb)
        result = _extract_provider_from_label(name, prefix)
        if result:
            return result
    return None

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")
