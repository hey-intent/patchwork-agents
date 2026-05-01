#!/usr/bin/env python3
"""
app.py - FastAPI service to manage a GitHub App secret and create Kubernetes Jobs.
...
(la mÃªme docstring)
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, Field

from app.agent_orchestrator import AgentOrchestrator
from app.config import ProviderConfig, settings
from providers.source import get_provider

# --- Logging setup ---
# Use uvicorn's logger so messages aren't disabled by uvicorn's dictConfig
logger = logging.getLogger("uvicorn.error")

if settings.enable_k8s_debug:
    # debug du client k8s / urllib3 (affiche les requÃªtes HTTP vers l'API server)
    logging.getLogger("kubernetes").setLevel(logging.DEBUG)
    logging.getLogger("urllib3").setLevel(logging.DEBUG)
    logger.warning(
        "Kubernetes client debug ENABLED (ENABLE_K8S_DEBUG=true) - do NOT enable in production if logs leak secrets"
    )

app = FastAPI(title="orchestrator", version="1.0")
bearer_scheme = HTTPBearer(auto_error=False)


def verify_admin_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)):
    """Require a valid bearer token for admin endpoints."""
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN not configured")
    if not credentials or not secrets.compare_digest(credentials.credentials, settings.admin_token):
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
    return s2[:50] or "job"


def get_source_provider():
    return get_provider(
        "github",
        app_id=settings.github_app_id,
        private_key=settings.github_private_key,
        webhook_secret=settings.webhook_secret,
    )


def get_agent_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator(
        namespace=settings.namespace,
        provider_label_prefix=settings.provider_label_prefix,
        load_k8s_client=load_k8s_client,
        build_worker_job=_build_worker_job,
        create_or_replace_secret=_create_or_replace_secret,
        delete_secret_if_exists=_delete_secret_if_exists,
        attach_job_owner_to_secret=_attach_job_owner_to_secret,
        safe_name=safe_name,
    )


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
        name="worker",
        image=cfg.image,
        image_pull_policy="Never",
        env=env_list,
    )
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels={"job-name": job_name, "provider": provider}),
        spec=client.V1PodSpec(restart_policy="Never", containers=[container]),
    )
    job_spec = client.V1JobSpec(template=template, backoff_limit=0, ttl_seconds_after_finished=settings.job_ttl_seconds)
    return client.V1Job(metadata=client.V1ObjectMeta(name=job_name, namespace=settings.namespace), spec=job_spec)


def _create_or_replace_secret(core: client.CoreV1Api, name: str, string_data: dict[str, str]) -> None:
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=settings.namespace),
        type="Opaque",
        string_data=string_data,
    )
    try:
        core.create_namespaced_secret(namespace=settings.namespace, body=body)
    except ApiException as e:
        if e.status == 409:
            core.patch_namespaced_secret(name=name, namespace=settings.namespace, body=body)
        else:
            raise


def _delete_secret_if_exists(core: client.CoreV1Api, name: str) -> None:
    try:
        core.delete_namespaced_secret(name=name, namespace=settings.namespace)
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
    core.patch_namespaced_secret(name=secret_name, namespace=settings.namespace, body=patch_body)


class SecretPayload(BaseModel):
    github_app_id: str = Field(..., alias="GITHUB_APP_ID")
    github_private_key_pem: str | None = Field(None, alias="GITHUB_PRIVATE_KEY_PEM")
    github_private_key_path: str | None = Field(None, alias="GITHUB_PRIVATE_KEY_PATH")
    replace: bool = Field(True, description="If true, apply/replace the secret (default true)")


class JobSpec(BaseModel):
    name: str
    image: str | None = settings.claude_worker_image
    command: list[str] | None = None
    env: dict[str, str] | None = None
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
        raise HTTPException(
            status_code=400,
            detail="either GITHUB_PRIVATE_KEY_PEM or GITHUB_PRIVATE_KEY_PATH is required",
        )

    data_b64 = {
        k: base64.b64encode(v).decode("utf-8")
        for k, v in {
            "GITHUB_APP_ID": data["GITHUB_APP_ID"],
            "GITHUB_PRIVATE_KEY": pem_bytes,
        }.items()
    }

    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=settings.namespace),
        type="Opaque",
        data=data_b64,
    )

    try:
        core.patch_namespaced_secret(name=name, namespace=settings.namespace, body=secret)
        action = "patched"
    except ApiException as e:
        if e.status == 404:
            core.create_namespaced_secret(namespace=settings.namespace, body=secret)
            action = "created"
        else:
            logger.exception(
                "k8s error when creating/patching secret: status=%s reason=%s",
                getattr(e, "status", None),
                getattr(e, "reason", None),
            )
            raise HTTPException(
                status_code=500,
                detail=f"k8s error: {e.reason} ({getattr(e, 'status', '')})",
            ) from e
    return {
        "result": action,
        "secret": name,
        "namespace": settings.namespace,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/webhook/github")
async def github_webhook(request: Request):
    body = await request.body()
    headers = dict(request.headers)
    source_provider = get_source_provider()
    orchestrator = get_agent_orchestrator()
    return await orchestrator.handle_webhook(source_provider, headers, body)


@app.post("/jobs/run")
def run_job(_auth: bool = Depends(verify_admin_token)):
    job_id = str(uuid.uuid4())[:8]
    job_name = f"manual-{job_id}"

    container = client.V1Container(
        name="worker",
        image=settings.claude_worker_image,
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
        metadata=client.V1ObjectMeta(name=job_name, namespace=settings.namespace),
        spec=job_spec,
    )

    batch, _ = load_k8s_client()
    logger.info("Manual run: creating job %s in %s", job_name, settings.namespace)
    try:
        batch.create_namespaced_job(namespace=settings.namespace, body=job)
    except ApiException as e:
        logger.exception(
            "ApiException creating manual job: status=%s reason=%s",
            getattr(e, "status", None),
            getattr(e, "reason", None),
        )
        raise HTTPException(
            status_code=500,
            detail="k8s error creating job",
        ) from e

    return {"status": "started", "job_name": job_name}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), log_level="info")
