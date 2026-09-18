"""
JiraTool — controlled create/update boundary for Gemini / Agentic RAG.
Credentials loaded from Secret Manager; non-secret config from configs/jira.yaml.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jira-tool")

app = FastAPI(title="CFI JiraTool", version="1.0.0")


def load_jira_config() -> dict[str, Any]:
    path = os.environ.get("JIRA_CONFIG_PATH", "/config/jira.yaml")
    if os.path.isfile(path):
        with open(path) as f:
            return yaml.safe_load(f).get("jira", {})
    # Dev defaults
    return {
        "default_project_key": os.environ.get("JIRA_PROJECT_KEY", "MOB"),
        "default_issue_type": os.environ.get("JIRA_ISSUE_TYPE", "Bug"),
        "behaviour": {"prefer_update_if_similar": True, "add_comment_on_update": True},
        "http": {"timeout_seconds": 30, "max_retries": 5},
    }


def get_jira_credentials() -> tuple[str, str, str]:
    """Resolve base URL, email, token from env (local) or Secret Manager (GKE)."""
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if base and email and token:
        return base.rstrip("/"), email, token

    # Production: read from files mounted by Secret Manager CSI / envFrom
    def _read(path: str) -> str | None:
        if os.path.isfile(path):
            return open(path).read().strip()
        return None

    base = base or _read("/secrets/jira-base-url")
    email = email or _read("/secrets/jira-email")
    token = token or _read("/secrets/jira-api-token")
    if not (base and email and token):
        raise RuntimeError("Jira credentials not configured")
    return base.rstrip("/"), email, token


class TicketCandidate(BaseModel):
    ticket_candidate_id: str
    deduplication_key: str
    summary: str
    description: str
    issue_type: str | None = None
    priority: str | None = "Medium"
    labels: list[str] = Field(default_factory=list)
    suggested_existing_key: str | None = None
    business_unit: str | None = None


class JiraToolResult(BaseModel):
    status: str  # JIRA_CREATED | EXISTING_TICKET_UPDATED | EXISTING_TICKET | FAILED
    jira_issue_id: str | None = None
    jira_key: str | None = None
    action_taken: str | None = None
    deduplication_key: str
    ticket_candidate_id: str
    message: str | None = None


# In-memory registry for local/dev; replace with Firestore/Spanner in prod
_REGISTRY: dict[str, str] = {}


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/v1/tickets", response_model=JiraToolResult)
def create_or_update(candidate: TicketCandidate):
    cfg = load_jira_config()
    project = cfg.get("default_project_key", "MOB")
    issue_type = candidate.issue_type or cfg.get("default_issue_type", "Bug")

    # 1) Registry lookup (idempotent)
    if candidate.deduplication_key in _REGISTRY:
        key = _REGISTRY[candidate.deduplication_key]
        logger.info("registry hit %s -> %s", candidate.deduplication_key, key)
        return JiraToolResult(
            status="EXISTING_TICKET",
            jira_key=key,
            action_taken="noop_registry",
            deduplication_key=candidate.deduplication_key,
            ticket_candidate_id=candidate.ticket_candidate_id,
        )

    # 2) Prefer suggested existing key
    if candidate.suggested_existing_key and cfg.get("behaviour", {}).get("prefer_update_if_similar", True):
        _REGISTRY[candidate.deduplication_key] = candidate.suggested_existing_key
        return JiraToolResult(
            status="EXISTING_TICKET_UPDATED",
            jira_key=candidate.suggested_existing_key,
            action_taken="added_comment_and_link",
            deduplication_key=candidate.deduplication_key,
            ticket_candidate_id=candidate.ticket_candidate_id,
            message="Would call Jira API to add comment (wire credentials in prod)",
        )

    # 3) Create path — in prod call Jira REST; here we synthesize a key for dry-run
    try:
        base, email, token = get_jira_credentials()
        # Placeholder: real implementation uses requests/httpx Basic auth to
        # POST {base}/rest/api/3/issue with project, issuetype, summary, description
        synthetic_key = f"{project}-{int(hashlib.sha256(candidate.deduplication_key.encode()).hexdigest()[:4], 16) % 9000 + 1000}"
        _REGISTRY[candidate.deduplication_key] = synthetic_key
        logger.info("created (dry-run) %s via %s as %s", synthetic_key, base, email)
        return JiraToolResult(
            status="JIRA_CREATED",
            jira_key=synthetic_key,
            jira_issue_id=synthetic_key,
            action_taken="created",
            deduplication_key=candidate.deduplication_key,
            ticket_candidate_id=candidate.ticket_candidate_id,
            message=f"Dry-run create against {base}; issue type={issue_type}",
        )
    except RuntimeError as e:
        # No credentials: still return deterministic dry-run for local UI demos
        synthetic_key = f"{project}-DRY"
        _REGISTRY[candidate.deduplication_key] = synthetic_key
        return JiraToolResult(
            status="JIRA_CREATED",
            jira_key=synthetic_key,
            action_taken="created_dry_run",
            deduplication_key=candidate.deduplication_key,
            ticket_candidate_id=candidate.ticket_candidate_id,
            message=str(e),
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
