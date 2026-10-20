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
    summary: str
    description: str = ""
    issue_type: str | None = None
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    deduplication_key: str | None = None
    project_key: str | None = None
    pipeline_run_id: str | None = None
    classified_id: str | None = None


class JiraToolResult(BaseModel):
    ok: bool
    action: str  # created | updated | dry_run | error
    issue_key: str | None = None
    issue_id: str | None = None
    message: str | None = None
    dry_run: bool = False


def _priority_name(priority: str | None) -> str:
    mapping = {
        "Highest": "Highest",
        "High": "High",
        "Medium": "Medium",
        "Low": "Low",
        "Lowest": "Lowest",
        "Critical": "Highest",
    }
    return mapping.get(priority or "Medium", "Medium")


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
    jql = f'project = {project} AND summary ~ "{summary[:50].replace(chr(34), "")}" ORDER BY updated DESC'
    try:
        r = client.get(
            "/rest/api/3/search",
            params={"jql": jql, "maxResults": max_results, "fields": "summary,key,status"},
        )
        if r.status_code >= 400:
            logger.warning("Jira search failed %s: %s", r.status_code, r.text[:300])
            return []
        return r.json().get("issues") or []
    except Exception as e:
        logger.warning("Jira search error: %s", e)
        return []


def jira_add_comment(client: httpx.Client, issue_key: str, body: str) -> bool:
    try:
        r = client.post(
            f"/rest/api/3/issue/{issue_key}/comment",
            json={"body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": body}]}]}},
        )
        return r.status_code < 400
    except Exception as e:
        logger.warning("Jira comment failed: %s", e)
        return False


def jira_create_issue(
    client: httpx.Client,
    project: str,
    summary: str,
    description: str,
    issue_type: str,
    priority: str,
    labels: list[str],
) -> dict[str, Any] | None:
    payload = {
        "fields": {
            "project": {"key": project},
            "summary": summary[:255],
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description[:30000]}]}],
            },
            "issuetype": {"name": issue_type},
            "priority": {"name": _priority_name(priority)},
            "labels": labels[:20],
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
    return {"status": "ok", "service": "jira-tool", "version": "1.1.0"}


@app.post("/v1/tickets", response_model=JiraToolResult)
def create_or_update(candidate: TicketCandidate):
    cfg = load_jira_config()
    project = candidate.project_key or cfg.get("default_project_key", "MOB")
    issue_type = candidate.issue_type or cfg.get("default_issue_type", "Bug")
    timeout = float(cfg.get("http", {}).get("timeout_seconds", 30))

    # Dry-run when credentials missing
    try:
        base, email, token = get_jira_credentials()
    except RuntimeError:
        dry_key = f"DRY-{hashlib.sha256((candidate.summary or '').encode()).hexdigest()[:8].upper()}"
        logger.info("dry-run ticket %s summary=%s", dry_key, candidate.summary[:60])
        return JiraToolResult(
            ok=True,
            action="dry_run",
            issue_key=dry_key,
            message="Credentials not configured — dry-run only",
            dry_run=True,
        )

    with _jira_client(base, email, token, timeout=timeout) as client:
        # Prefer update if similar existing issue
        if cfg.get("behaviour", {}).get("prefer_update_if_similar", True):
            similar = jira_search_similar(
                client, project, candidate.summary, max_results=cfg.get("search", {}).get("max_results", 5)
            )
            if similar:
                key = similar[0].get("key")
                if key and cfg.get("behaviour", {}).get("add_comment_on_update", True):
                    jira_add_comment(
                        client,
                        key,
                        f"Additional feedback (dedup={candidate.deduplication_key}):\n{candidate.description[:2000]}",
                    )
                return JiraToolResult(
                    ok=True,
                    action="updated",
                    issue_key=key,
                    issue_id=str(similar[0].get("id")),
                    message="Matched existing issue; comment added",
                )

        created = jira_create_issue(
            client,
            project=project,
            summary=candidate.summary,
            description=candidate.description,
            issue_type=issue_type,
            priority=candidate.priority or "Medium",
            labels=candidate.labels or [],
        )
        if not created:
            return JiraToolResult(
                ok=False,
                action="error",
                message="Jira create API returned error",
            )

        key = created.get("key")
        issue_id = str(created.get("id"))
        logger.info("created Jira issue %s id=%s", key, issue_id)
        return JiraToolResult(
            ok=True,
            action="created",
            issue_key=key,
            issue_id=issue_id,
            message="Issue created",
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8081")))
