"""
JiraTool — controlled create/update boundary for Gemini / Agentic RAG.
Credentials from Secret Manager (or env for local); non-secret config from configs/jira.yaml.
Real Jira Cloud REST (API v3) when credentials are present; dry-run otherwise.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import httpx
import yaml
from fastapi import FastAPI
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jira-tool")

app = FastAPI(title="CFI JiraTool", version="1.1.0")


def load_jira_config() -> dict[str, Any]:
    path = os.environ.get("JIRA_CONFIG_PATH", "/config/jira.yaml")
    if os.path.isfile(path):
        with open(path) as f:
            return yaml.safe_load(f).get("jira", {})
    return {
        "default_project_key": os.environ.get("JIRA_PROJECT_KEY", "MOB"),
        "default_issue_type": os.environ.get("JIRA_ISSUE_TYPE", "Bug"),
        "behaviour": {
            "prefer_update_if_similar": True,
            "add_comment_on_update": True,
            "link_source_feedback": True,
        },
        "http": {"timeout_seconds": 30, "max_retries": 5},
        "search": {"max_results": 10, "similarity_threshold": 0.85},
    }


def get_jira_credentials() -> tuple[str, str, str]:
    """Resolve base URL, email, token from env (local) or mounted secret files (GKE)."""
    base = os.environ.get("JIRA_BASE_URL")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    if base and email and token:
        return base.rstrip("/"), email, token

    def _read(path: str) -> str | None:
        if os.path.isfile(path):
            with open(path) as f:
                return f.read().strip()
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
    status: str
    jira_issue_id: str | None = None
    jira_key: str | None = None
    action_taken: str | None = None
    deduplication_key: str
    ticket_candidate_id: str | None = None
    message: str | None = None


_REGISTRY: dict[str, str] = {}


def _priority_name(priority: str | None) -> str:
    p = (priority or "Medium").strip().lower()
    mapping = {
        "highest": "Highest",
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "lowest": "Lowest",
    }
    return mapping.get(p, "Medium")


def _jira_client(base: str, email: str, token: str, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        base_url=base,
        auth=(email, token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout,
    )


def jira_search_similar(
    client: httpx.Client, project: str, summary: str, max_results: int = 5
) -> list[dict[str, Any]]:
    tokens = " ".join(summary.split()[:8]).replace('"', '\\"')
    jql = (
        f'project = {project} AND statusCategory != Done '
        f'AND text ~ "{tokens}" ORDER BY updated DESC'
    )
    try:
        r = client.get(
            "/rest/api/3/search",
            params={"jql": jql, "maxResults": max_results, "fields": "summary,status,key"},
        )
        if r.status_code >= 400:
            logger.warning("Jira search failed %s: %s", r.status_code, r.text[:300])
            return []
        return r.json().get("issues", [])
    except Exception as e:
        logger.warning("Jira search error: %s", e)
        return []


def jira_add_comment(client: httpx.Client, issue_key: str, body: str) -> bool:
    try:
        r = client.post(
            f"/rest/api/3/issue/{issue_key}/comment",
            json={"body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": body}]}
            ]}},
        )
        return r.status_code < 300
    except Exception as e:
        logger.warning("Jira comment failed: %s", e)
        return False


def jira_create_issue(
    client: httpx.Client,
    project: str,
    issue_type: str,
    summary: str,
    description: str,
    priority: str,
    labels: list[str],
) -> dict[str, Any] | None:
    payload = {
        "fields": {
            "project": {"key": project},
            "issuetype": {"name": issue_type},
            "summary": summary[:255],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": description[:32000]}],
                    }
                ],
            },
            "priority": {"name": _priority_name(priority)},
            "labels": [lb[:255] for lb in (labels or [])][:20],
        }
    }
    try:
        r = client.post("/rest/api/3/issue", json=payload)
        if r.status_code >= 400:
            logger.error("Jira create failed %s: %s", r.status_code, r.text[:500])
            return None
        return r.json()
    except Exception as e:
        logger.error("Jira create error: %s", e)
        return None


@app.get("/healthz")
@app.get("/v1/health")
def healthz():
    creds_ok = False
    try:
        get_jira_credentials()
        creds_ok = True
    except RuntimeError:
        pass
    return {"status": "ok", "jira_credentials_configured": creds_ok}


@app.post("/v1/tickets", response_model=JiraToolResult)
def create_or_update(candidate: TicketCandidate):
    cfg = load_jira_config()
    project = cfg.get("default_project_key", "MOB")
    issue_type = candidate.issue_type or cfg.get("default_issue_type", "Bug")
    timeout = float(cfg.get("http", {}).get("timeout_seconds", 30))
    prefer_update = cfg.get("behaviour", {}).get("prefer_update_if_similar", True)
    add_comment = cfg.get("behaviour", {}).get("add_comment_on_update", True)
    max_results = int(cfg.get("search", {}).get("max_results", 10))

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

    try:
        base, email, token = get_jira_credentials()
    except RuntimeError as e:
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

    with _jira_client(base, email, token, timeout=timeout) as client:
        if candidate.suggested_existing_key and prefer_update:
            if add_comment:
                comment = (
                    f"[CFI] Related feedback linked via Agentic RAG.\n"
                    f"Candidate: {candidate.ticket_candidate_id}\n"
                    f"Dedup key: {candidate.deduplication_key}\n\n"
                    f"{candidate.description[:2000]}"
                )
                jira_add_comment(client, candidate.suggested_existing_key, comment)
            _REGISTRY[candidate.deduplication_key] = candidate.suggested_existing_key
            return JiraToolResult(
                status="EXISTING_TICKET_UPDATED",
                jira_key=candidate.suggested_existing_key,
                action_taken="added_comment_and_link",
                deduplication_key=candidate.deduplication_key,
                ticket_candidate_id=candidate.ticket_candidate_id,
            )

        similar = jira_search_similar(client, project, candidate.summary, max_results=max_results)
        if similar and prefer_update:
            existing_key = similar[0].get("key")
            if existing_key:
                if add_comment:
                    comment = (
                        f"[CFI] Similar feedback matched by search.\n"
                        f"Candidate: {candidate.ticket_candidate_id}\n"
                        f"Summary: {candidate.summary}\n\n"
                        f"{candidate.description[:1500]}"
                    )
                    jira_add_comment(client, existing_key, comment)
                _REGISTRY[candidate.deduplication_key] = existing_key
                return JiraToolResult(
                    status="EXISTING_TICKET_UPDATED",
                    jira_key=existing_key,
                    action_taken="search_match_comment",
                    deduplication_key=candidate.deduplication_key,
                    ticket_candidate_id=candidate.ticket_candidate_id,
                    message=f"Matched {len(similar)} similar issue(s)",
                )

        created = jira_create_issue(
            client,
            project=project,
            issue_type=issue_type,
            summary=candidate.summary,
            description=candidate.description,
            priority=candidate.priority or "Medium",
            labels=candidate.labels or [],
        )
        if not created:
            return JiraToolResult(
                status="FAILED",
                action_taken="create_failed",
                deduplication_key=candidate.deduplication_key,
                ticket_candidate_id=candidate.ticket_candidate_id,
                message="Jira create API returned error",
            )

        key = created.get("key")
        issue_id = created.get("id")
        _REGISTRY[candidate.deduplication_key] = key or ""
        logger.info("created Jira issue %s id=%s", key, issue_id)
        return JiraToolResult(
            status="JIRA_CREATED",
            jira_key=key,
            jira_issue_id=issue_id,
            action_taken="created",
            deduplication_key=candidate.deduplication_key,
            ticket_candidate_id=candidate.ticket_candidate_id,
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
