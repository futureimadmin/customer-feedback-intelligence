"""
Control Plane API — orchestration, heartbeat, retries, lineage.
Does not perform heavy data processing; dispatches work to Data Plane workers.

Env:
  PIPELINE_CONFIG_PATH  path to configs/pipeline.yaml
  JIRA_TOOL_URL         e.g. http://jira-tool:8080
  PORT                  default 8080
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from state import (
    MetadataStore,
    RunMode,
    RunStatus,
    StageName,
    StageStatus,
    store,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("control-plane")

app = FastAPI(
    title="CFI Control Plane",
    version="1.0.0",
    description="Orchestration, state, heartbeat, retries, lineage for Customer Feedback Intelligence",
)


def load_pipeline_config() -> dict[str, Any]:
    path = os.environ.get("PIPELINE_CONFIG_PATH", "/config/pipeline.yaml")
    if os.path.isfile(path):
        with open(path) as f:
            return yaml.safe_load(f).get("pipeline", {})
    return {
        "gcs": {"bucket": "customer-feedback"},
        "classification": {"confidence_threshold": 0.75},
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "control-plane"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


# ---------------------------------------------------------------------------
# Heartbeat monitor
# ---------------------------------------------------------------------------


class HeartbeatRequest(BaseModel):
    source: str = Field(..., description="Source system id, e.g. mobile_app, app_log")


@app.post("/v1/heartbeat")
def post_heartbeat(body: HeartbeatRequest):
    """Sources (or adapters) call this to signal health / new data available."""
    ts = store.heartbeat(body.source)
    logger.info("heartbeat source=%s at=%s", body.source, ts)
    return {"source": body.source, "last_seen": ts}


@app.get("/v1/heartbeat")
def list_heartbeats():
    return store.list_heartbeats()


# ---------------------------------------------------------------------------
# Ingestion scheduler — create pipeline runs
# ---------------------------------------------------------------------------


class CreateRunRequest(BaseModel):
    mode: RunMode = RunMode.HEARTBEAT
    source_filter: str | None = None
    ingestion_run_id: str | None = None


@app.post("/v1/runs")
def create_run(body: CreateRunRequest):
    run = store.create_run(
        mode=body.mode,
        source_filter=body.source_filter,
        ingestion_run_id=body.ingestion_run_id,
    )
    # Auto-start: CREATED → VALIDATING → RUNNING
    store.transition(run.pipeline_run_id, RunStatus.VALIDATING)
    run = store.transition(run.pipeline_run_id, RunStatus.RUNNING)
    logger.info(
        "created run %s mode=%s ingestion=%s",
        run.pipeline_run_id,
        run.mode,
        run.ingestion_run_id,
    )
    return run


@app.get("/v1/runs")
def list_runs(limit: int = 50):
    return store.list_runs(limit=limit)


@app.get("/v1/runs/{pipeline_run_id}")
def get_run(pipeline_run_id: str):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


# ---------------------------------------------------------------------------
# Stage updates (Data Plane workers report progress)
# ---------------------------------------------------------------------------


class StageUpdateRequest(BaseModel):
    stage: StageName
    status: StageStatus
    artifact_ids: list[str] = Field(default_factory=list)
    parent_artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)


@app.post("/v1/runs/{pipeline_run_id}/stages")
def update_stage(pipeline_run_id: str, body: StageUpdateRequest):
    if not store.get_run(pipeline_run_id):
        raise HTTPException(404, "run not found")
    try:
        run = store.update_stage(
            pipeline_run_id,
            body.stage,
            body.status,
            artifact_ids=body.artifact_ids or None,
            error=body.error,
        )
    except KeyError:
        raise HTTPException(404, "run not found")

    for aid in body.artifact_ids:
        if body.parent_artifact_ids:
            store.record_lineage(aid, body.parent_artifact_ids)

    for k, v in body.metrics.items():
        store.set_metric(pipeline_run_id, k, v)

    # Auto-complete run when all stages done
    if all(s.status in (StageStatus.COMPLETED, StageStatus.SKIPPED) for s in run.stages):
        try:
            run = store.transition(pipeline_run_id, RunStatus.COMPLETED)
        except ValueError:
            pass

    if body.status == StageStatus.FAILED:
        try:
            run = store.transition(pipeline_run_id, RunStatus.FAILED, error=body.error)
        except ValueError:
            pass

    return run


# ---------------------------------------------------------------------------
# Failure / retry manager
# ---------------------------------------------------------------------------


class RetryRequest(BaseModel):
    force: bool = False


@app.post("/v1/runs/{pipeline_run_id}/retry")
def retry_run(pipeline_run_id: str, body: RetryRequest = RetryRequest()):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != RunStatus.FAILED and not body.force:
        raise HTTPException(400, f"run status is {run.status}, expected FAILED")
    if run.retry_count >= run.max_retries and not body.force:
        store.transition(pipeline_run_id, RunStatus.MANUAL_INTERVENTION, error="max retries exceeded")
        raise HTTPException(409, "max retries exceeded; marked MANUAL_INTERVENTION")
    store.transition(pipeline_run_id, RunStatus.RETRY_PENDING)
    run = store.transition(pipeline_run_id, RunStatus.RUNNING)
    logger.info("retry run %s count=%s", pipeline_run_id, run.retry_count)
    return run


@app.post("/v1/runs/{pipeline_run_id}/resume")
def resume_run(pipeline_run_id: str):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != RunStatus.MANUAL_INTERVENTION:
        raise HTTPException(400, f"expected MANUAL_INTERVENTION, got {run.status}")
    store.transition(pipeline_run_id, RunStatus.RESUMED)
    return store.transition(pipeline_run_id, RunStatus.RUNNING)


@app.post("/v1/runs/{pipeline_run_id}/reprocess")
def reprocess_run(pipeline_run_id: str):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(400, f"expected COMPLETED, got {run.status}")
    store.transition(pipeline_run_id, RunStatus.REPROCESS_REQUESTED)
    return store.transition(pipeline_run_id, RunStatus.RUNNING)


# ---------------------------------------------------------------------------
# Ticket candidates (bridge to JiraTool)
# ---------------------------------------------------------------------------


class CreateTicketCandidateRequest(BaseModel):
    classified_id: str
    deduplication_key: str | None = None


@app.post("/v1/runs/{pipeline_run_id}/ticket-candidates")
def create_ticket_candidate(pipeline_run_id: str, body: CreateTicketCandidateRequest):
    if not store.get_run(pipeline_run_id):
        raise HTTPException(404, "run not found")
    tc = store.create_ticket_candidate(
        pipeline_run_id, body.classified_id, body.deduplication_key
    )
    store.update_ticket_candidate(tc.ticket_candidate_id, status="VALIDATED")
    return store.get_ticket_candidate(tc.ticket_candidate_id)


class DispatchJiraRequest(BaseModel):
    ticket_candidate_id: str
    summary: str
    description: str
    priority: str = "Medium"
    labels: list[str] = Field(default_factory=list)
    suggested_existing_key: str | None = None
    issue_type: str | None = None


@app.post("/v1/jira/dispatch")
def dispatch_jira(body: DispatchJiraRequest):
    """Hand off to JiraTool service (final idempotency boundary)."""
    tc = store.get_ticket_candidate(body.ticket_candidate_id)
    if not tc:
        raise HTTPException(404, "ticket candidate not found")

    store.update_ticket_candidate(body.ticket_candidate_id, status="DEDUP_CHECK")
    jira_tool_url = os.environ.get("JIRA_TOOL_URL", "http://localhost:8081").rstrip("/")

    payload = {
        "ticket_candidate_id": body.ticket_candidate_id,
        "deduplication_key": tc.deduplication_key or body.ticket_candidate_id,
        "summary": body.summary,
        "description": body.description,
        "priority": body.priority,
        "labels": body.labels,
        "suggested_existing_key": body.suggested_existing_key,
        "issue_type": body.issue_type,
    }

    store.update_ticket_candidate(body.ticket_candidate_id, status="READY")

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{jira_tool_url}/v1/tickets", json=payload)
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        store.update_ticket_candidate(
            body.ticket_candidate_id, status="FAILED", error=str(e)
        )
        raise HTTPException(502, f"JiraTool error: {e}") from e

    status = result.get("status", "JIRA_CREATED")
    jira_key = result.get("jira_key")
    store.update_ticket_candidate(
        body.ticket_candidate_id,
        status=status,
        jira_key=jira_key,
    )
    return {"ticket_candidate": store.get_ticket_candidate(body.ticket_candidate_id), "jira_tool": result}


@app.get("/v1/ticket-candidates/{ticket_candidate_id}")
def get_ticket_candidate(ticket_candidate_id: str):
    tc = store.get_ticket_candidate(ticket_candidate_id)
    if not tc:
        raise HTTPException(404, "not found")
    return tc


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


@app.get("/v1/lineage/{artifact_id}")
def get_lineage(artifact_id: str):
    parents = store.get_lineage(artifact_id)
    return {"artifact_id": artifact_id, "parent_artifact_ids": parents}


# ---------------------------------------------------------------------------
# Config (read-only view for UI)
# ---------------------------------------------------------------------------


@app.get("/v1/config/pipeline")
def get_pipeline_config():
    return load_pipeline_config()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
