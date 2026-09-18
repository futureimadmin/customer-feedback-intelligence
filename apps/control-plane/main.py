"""
Control Plane — end-to-end orchestration on Firestore.

Database: customer-feedback-intelligence
Env:
  GCP_PROJECT, FIRESTORE_DATABASE=customer-feedback-intelligence
  JIRA_TOOL_URL, PIPELINE_CONFIG_PATH, PUBSUB_STAGE_TOPIC, PUBSUB_DLQ_TOPIC
  PORT
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from state import (
    ModelVersionRecord,
    RunMode,
    RunStatus,
    StageName,
    StageStatus,
    next_runnable_stages,
    store,
    _now,
)

class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": datetime.now(timezone.utc).isoformat(),
        }
        for key in ("correlation_id", "pipeline_run_id", "stage", "ingestion_run_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)

_handler = logging.StreamHandler()
_handler.setFormatter(JsonLogFormatter())
logging.root.handlers.clear()
logging.root.addHandler(_handler)
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("control-plane")

app = FastAPI(
    title="CFI Control Plane",
    version="2.0.0",
    description="Firestore-backed orchestration: state, heartbeat, schedule, retry, DLQ, config, models",
)

def load_file_pipeline_config() -> dict[str, Any]:
    path = os.environ.get("PIPELINE_CONFIG_PATH", "/config/pipeline.yaml")
    if os.path.isfile(path):
        with open(path) as f:
            return yaml.safe_load(f).get("pipeline", {})
    return {
        "gcs": {"bucket": os.environ.get("GCS_BUCKET", "customer-feedback")},
        "classification": {"confidence_threshold": 0.75},
        "retry": {"max_retries": 3, "backoff_base_seconds": 30},
    }

def publish_stage_dispatch(pipeline_run_id: str, stage: StageName, correlation_id: str) -> None:
    topic = os.environ.get("PUBSUB_STAGE_TOPIC", "cfi-stage-dispatch")
    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    message = {
        "pipeline_run_id": pipeline_run_id,
        "stage": stage.value,
        "correlation_id": correlation_id,
        "action": "run_stage",
    }
    try:
        from google.cloud import pubsub_v1
        publisher = pubsub_v1.PublisherClient()
        path = publisher.topic_path(project, topic)
        publisher.publish(path, json.dumps(message).encode("utf-8"))
        logger.info(
            "dispatched stage",
            extra={
                "pipeline_run_id": pipeline_run_id,
                "stage": stage.value,
                "correlation_id": correlation_id,
            },
        )
    except Exception as e:
        logger.warning("Pub/Sub dispatch skipped: %s msg=%s", e, message)

def publish_dlq(payload: dict[str, Any]) -> None:
    topic = os.environ.get("PUBSUB_DLQ_TOPIC", "cfi-dlq")
    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    try:
        from google.cloud import pubsub_v1
        publisher = pubsub_v1.PublisherClient()
        path = publisher.topic_path(project, topic)
        publisher.publish(path, json.dumps(payload).encode("utf-8"))
    except Exception as e:
        logger.warning("DLQ pubsub skipped: %s", e)

def dispatch_runnable_stages(run) -> list[str]:
    stages = next_runnable_stages(run)
    for s in stages:
        publish_stage_dispatch(run.pipeline_run_id, s, run.correlation_id)
    return [s.value for s in stages]

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "service": "control-plane",
        "firestore_database": os.environ.get("FIRESTORE_DATABASE", "customer-feedback-intelligence"),
        "store_fallback": getattr(store, "_memory_fallback", False),
    }

@app.get("/readyz")
def readyz():
    return {"status": "ready"}

class HeartbeatRequest(BaseModel):
    source: str
    watermark: str | None = None
    healthy: bool = True

@app.post("/v1/heartbeat")
def post_heartbeat(body: HeartbeatRequest):
    ts = store.heartbeat(body.source, watermark=body.watermark)
    logger.info("heartbeat source=%s watermark=%s", body.source, body.watermark, extra={"correlation_id": f"hb-{body.source}"})
    return {"source": body.source, "last_seen": ts, "watermark": body.watermark, "healthy": body.healthy}

@app.get("/v1/heartbeat")
def list_heartbeats():
    return store.list_heartbeats()

@app.get("/v1/watermarks/{source}")
def get_watermark(source: str):
    return {"source": source, "watermark": store.get_watermark(source)}

class CreateRunRequest(BaseModel):
    mode: RunMode = RunMode.HEARTBEAT
    source_filter: str | None = None
    ingestion_run_id: str | None = None

@app.post("/v1/runs")
def create_run(body: CreateRunRequest):
    run = store.create_run(mode=body.mode, source_filter=body.source_filter, ingestion_run_id=body.ingestion_run_id)
    store.transition(run.pipeline_run_id, RunStatus.VALIDATING)
    run = store.transition(run.pipeline_run_id, RunStatus.RUNNING)
    dispatched = dispatch_runnable_stages(run)
    logger.info("created run mode=%s dispatched=%s", run.mode.value, dispatched, extra={"pipeline_run_id": run.pipeline_run_id, "correlation_id": run.correlation_id, "ingestion_run_id": run.ingestion_run_id})
    return {**run.model_dump(), "dispatched_stages": dispatched}

@app.post("/v1/scheduler/heartbeat-tick")
def scheduler_heartbeat_tick():
    hbs = store.list_heartbeats()
    if not hbs:
        return {"action": "noop", "reason": "no heartbeats registered"}
    run = store.create_run(mode=RunMode.HEARTBEAT)
    store.transition(run.pipeline_run_id, RunStatus.VALIDATING)
    run = store.transition(run.pipeline_run_id, RunStatus.RUNNING)
    dispatched = dispatch_runnable_stages(run)
    return {"action": "started", "pipeline_run_id": run.pipeline_run_id, "sources": list(hbs.keys()), "dispatched_stages": dispatched}

@app.get("/v1/runs")
def list_runs(limit: int = 50):
    return store.list_runs(limit=limit)

@app.get("/v1/runs/{pipeline_run_id}")
def get_run(pipeline_run_id: str):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return {**run.model_dump(), "next_runnable_stages": [s.value for s in next_runnable_stages(run)]}

class StageUpdateRequest(BaseModel):
    stage: StageName
    status: StageStatus
    artifact_ids: list[str] = Field(default_factory=list)
    parent_artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)
    checkpoint: dict[str, Any] = Field(default_factory=dict)

@app.post("/v1/runs/{pipeline_run_id}/stages")
def update_stage(pipeline_run_id: str, body: StageUpdateRequest):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if body.status == StageStatus.RUNNING:
        from state import stage_deps_satisfied
        if not stage_deps_satisfied(run, body.stage):
            raise HTTPException(409, f"dependencies not satisfied for {body.stage.value}")
    run = store.update_stage(pipeline_run_id, body.stage, body.status, artifact_ids=body.artifact_ids or None, error=body.error, checkpoint=body.checkpoint or None)
    for aid in body.artifact_ids:
        if body.parent_artifact_ids:
            store.record_lineage(aid, body.parent_artifact_ids)
    for k, v in body.metrics.items():
        store.set_metric(pipeline_run_id, k, v)
    extra = {"pipeline_run_id": pipeline_run_id, "correlation_id": run.correlation_id, "stage": body.stage.value}
    if body.status == StageStatus.FAILED:
        dlq_id = store.enqueue_dlq(pipeline_run_id, body.stage.value, {"artifact_ids": body.artifact_ids, "checkpoint": body.checkpoint}, body.error or "stage failed", correlation_id=run.correlation_id)
        publish_dlq({"dlq_id": dlq_id, "pipeline_run_id": pipeline_run_id, "stage": body.stage.value})
        try:
            run = store.transition(pipeline_run_id, RunStatus.FAILED, error=body.error)
        except ValueError:
            pass
        logger.error("stage failed dlq=%s", dlq_id, extra=extra)
        return run
    if body.status in (StageStatus.COMPLETED, StageStatus.SKIPPED):
        dispatched = dispatch_runnable_stages(run)
        if all(s.status in (StageStatus.COMPLETED, StageStatus.SKIPPED) for s in run.stages):
            try:
                run = store.transition(pipeline_run_id, RunStatus.COMPLETED)
            except ValueError:
                pass
        logger.info("stage done dispatched=%s", dispatched, extra=extra)
        return {**run.model_dump(), "dispatched_stages": dispatched}
    return run

@app.get("/v1/runs/{pipeline_run_id}/next-stages")
def get_next_stages(pipeline_run_id: str):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return {"pipeline_run_id": pipeline_run_id, "stages": [s.value for s in next_runnable_stages(run)]}

class RetryRequest(BaseModel):
    force: bool = False

@app.post("/v1/runs/{pipeline_run_id}/retry")
def retry_run(pipeline_run_id: str, body: RetryRequest = RetryRequest()):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != RunStatus.FAILED and not body.force:
        raise HTTPException(400, f"run status is {run.status}, expected FAILED")
    cfg = load_file_pipeline_config()
    max_retries = int(cfg.get("retry", {}).get("max_retries", run.max_retries))
    base = int(cfg.get("retry", {}).get("backoff_base_seconds", 30))
    if run.retry_count >= max_retries and not body.force:
        store.transition(pipeline_run_id, RunStatus.MANUAL_INTERVENTION, error="max retries exceeded")
        raise HTTPException(409, "max retries exceeded; marked MANUAL_INTERVENTION")
    store.transition(pipeline_run_id, RunStatus.RETRY_PENDING)
    delay = base * (2 ** run.retry_count)
    next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    store.set_next_retry(pipeline_run_id, next_at)
    store.reset_stages_from_checkpoint(pipeline_run_id)
    run = store.transition(pipeline_run_id, RunStatus.RUNNING)
    dispatched = dispatch_runnable_stages(run)
    logger.info("retry scheduled delay=%ss", delay, extra={"pipeline_run_id": pipeline_run_id, "correlation_id": run.correlation_id})
    return {**run.model_dump(), "backoff_seconds": delay, "next_retry_at": next_at, "dispatched_stages": dispatched}

@app.post("/v1/runs/{pipeline_run_id}/resume")
def resume_run(pipeline_run_id: str):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != RunStatus.MANUAL_INTERVENTION:
        raise HTTPException(400, f"expected MANUAL_INTERVENTION, got {run.status}")
    store.transition(pipeline_run_id, RunStatus.RESUMED)
    store.reset_stages_from_checkpoint(pipeline_run_id)
    run = store.transition(pipeline_run_id, RunStatus.RUNNING)
    dispatched = dispatch_runnable_stages(run)
    return {**run.model_dump(), "dispatched_stages": dispatched}

@app.post("/v1/runs/{pipeline_run_id}/reprocess")
def reprocess_run(pipeline_run_id: str):
    run = store.get_run(pipeline_run_id)
    if not run:
        raise HTTPException(404, "run not found")
    if run.status != RunStatus.COMPLETED:
        raise HTTPException(400, f"expected COMPLETED, got {run.status}")
    store.transition(pipeline_run_id, RunStatus.REPROCESS_REQUESTED)
    for st in run.stages:
        st.status = StageStatus.PENDING
        st.error = None
        st.started_at = None
        st.completed_at = None
    store._put_run(run)
    run = store.transition(pipeline_run_id, RunStatus.RUNNING)
    dispatched = dispatch_runnable_stages(run)
    return {**run.model_dump(), "dispatched_stages": dispatched}

@app.get("/v1/dlq")
def list_dlq(limit: int = 50):
    return store.list_dlq(limit=limit)

class CreateTicketCandidateRequest(BaseModel):
    classified_id: str
    deduplication_key: str | None = None

@app.post("/v1/runs/{pipeline_run_id}/ticket-candidates")
def create_ticket_candidate(pipeline_run_id: str, body: CreateTicketCandidateRequest):
    if not store.get_run(pipeline_run_id):
        raise HTTPException(404, "run not found")
    if body.deduplication_key:
        existing = store.get_idempotency(f"ticket:{body.deduplication_key}")
        if existing:
            owner = existing.get("owner")
            tc = store.get_ticket_candidate(owner) if owner else None
            if tc:
                return tc
    tc = store.create_ticket_candidate(pipeline_run_id, body.classified_id, body.deduplication_key)
    return store.update_ticket_candidate(tc.ticket_candidate_id, status="VALIDATED")

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
    tc = store.get_ticket_candidate(body.ticket_candidate_id)
    if not tc:
        raise HTTPException(404, "ticket candidate not found")
    store.update_ticket_candidate(body.ticket_candidate_id, status="DEDUP_CHECK")
    jira_tool_url = os.environ.get("JIRA_TOOL_URL", "http://localhost:8081").rstrip("/")
    payload = {"ticket_candidate_id": body.ticket_candidate_id, "deduplication_key": tc.deduplication_key or body.ticket_candidate_id, "summary": body.summary, "description": body.description, "priority": body.priority, "labels": body.labels, "suggested_existing_key": body.suggested_existing_key, "issue_type": body.issue_type}
    store.update_ticket_candidate(body.ticket_candidate_id, status="READY")
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{jira_tool_url}/v1/tickets", json=payload)
            resp.raise_for_status()
            result = resp.json()
    except Exception as e:
        store.update_ticket_candidate(body.ticket_candidate_id, status="FAILED", error=str(e))
        store.enqueue_dlq(tc.pipeline_run_id, "JIRA", payload, str(e))
        raise HTTPException(502, f"JiraTool error: {e}") from e
    status = result.get("status", "JIRA_CREATED")
    jira_key = result.get("jira_key")
    store.update_ticket_candidate(body.ticket_candidate_id, status=status, jira_key=jira_key)
    return {"ticket_candidate": store.get_ticket_candidate(body.ticket_candidate_id), "jira_tool": result}

@app.get("/v1/ticket-candidates/{ticket_candidate_id}")
def get_ticket_candidate(ticket_candidate_id: str):
    tc = store.get_ticket_candidate(ticket_candidate_id)
    if not tc:
        raise HTTPException(404, "not found")
    return tc

@app.get("/v1/lineage/{artifact_id}")
def get_lineage(artifact_id: str):
    return {"artifact_id": artifact_id, "parent_artifact_ids": store.get_lineage(artifact_id)}

@app.get("/v1/config")
def list_config():
    return {"file": load_file_pipeline_config(), "firestore": store.list_config()}

@app.get("/v1/config/{key}")
def get_config(key: str):
    val = store.get_config(key)
    if val is None and key == "pipeline":
        return load_file_pipeline_config()
    if val is None:
        raise HTTPException(404, "config key not found")
    return {"key": key, "value": val}

class ConfigPutRequest(BaseModel):
    value: dict[str, Any]

@app.put("/v1/config/{key}")
def put_config(key: str, body: ConfigPutRequest):
    store.set_config(key, body.value)
    return {"key": key, "value": body.value, "updated_at": _now()}

class RegisterModelRequest(BaseModel):
    model_id: str
    version: str
    task: str
    status: str = "CANDIDATE"
    metrics: dict[str, float] = Field(default_factory=dict)
    dataset_version: str | None = None
    endpoint: str | None = None

@app.post("/v1/models")
def register_model(body: RegisterModelRequest):
    rec = ModelVersionRecord(model_id=body.model_id, version=body.version, task=body.task, status=body.status, metrics=body.metrics, dataset_version=body.dataset_version, endpoint=body.endpoint, created_at=_now())
    return store.register_model(rec)

@app.get("/v1/models")
def list_models(model_id: str | None = None):
    return store.list_models(model_id=model_id)

@app.post("/v1/models/{model_id}/versions/{version}/promote")
def promote_model(model_id: str, version: str):
    try:
        return store.promote_model(model_id, version)
    except KeyError:
        raise HTTPException(404, "model version not found")

@app.get("/v1/idempotency/{key}")
def get_idempotency(key: str):
    val = store.get_idempotency(key)
    if not val:
        raise HTTPException(404, "key not found")
    return val

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
