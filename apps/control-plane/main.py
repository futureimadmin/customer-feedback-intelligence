"""
Control Plane — end-to-end orchestration on Firestore.

Database: customer-feedback-intelligence
Env:
  GCP_PROJECT, FIRESTORE_DATABASE=customer-feedback-intelligence
  JIRA_TOOL_URL, PIPELINE_CONFIG_PATH
"""
from __future__ import annotations

import os
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from state import FirestoreStore, ReviewItem
from observability import setup_logging

setup_logging()
logger = logging.getLogger("control-plane")

app = FastAPI(title="Customer Feedback Intelligence — Control Plane", version="1.3.0")
store = FirestoreStore()

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/healthz")
@app.get("/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "control-plane", "version": "1.3.0"}

# ---------------------------------------------------------------------------
# Runs / stages (core orchestration)
# ---------------------------------------------------------------------------

class CreateRunRequest(BaseModel):
    pipeline_version: str = "1.3.0"
    source: str = "api"
    metadata: dict[str, Any] = Field(default_factory=dict)

@app.post("/v1/runs")
def create_run(body: CreateRunRequest) -> dict[str, Any]:
    run = store.create_run(body.pipeline_version, body.source, body.metadata)
    return run

@app.get("/v1/runs")
def list_runs(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"runs": store.list_runs(limit)}

@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run

class StageReport(BaseModel):
    stage: str
    status: str
    artifact_uri: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

@app.post("/v1/runs/{run_id}/stages")
def report_stage(run_id: str, body: StageReport) -> dict[str, Any]:
    ok = store.report_stage(run_id, body.stage, body.status, body.artifact_uri, body.metrics, body.error)
    if not ok:
        raise HTTPException(404, "run not found")
    return {"ok": True}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@app.get("/v1/config/{section}")
def get_config(section: str) -> dict[str, Any]:
    cfg = store.get_config(section)
    return cfg or {}

@app.put("/v1/config/{section}")
def put_config(section: str, body: dict[str, Any]) -> dict[str, Any]:
    store.put_config(section, body)
    return {"ok": True, "section": section}

# ---------------------------------------------------------------------------
# Models registry
# ---------------------------------------------------------------------------

class ModelRegister(BaseModel):
    name: str
    version: str
    task: str = "classifier"
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_uri: str | None = None
    status: str = "candidate"

@app.post("/v1/models")
def register_model(body: ModelRegister) -> dict[str, Any]:
    mid = store.register_model(body.name, body.version, body.task, body.metrics, body.artifact_uri, body.status)
    return {"id": mid}

@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    return {"models": store.list_models()}

@app.post("/v1/models/{model_id}/promote")
def promote_model(model_id: str) -> dict[str, Any]:
    ok = store.promote_model(model_id)
    if not ok:
        raise HTTPException(404, "model not found")
    return {"ok": True}

# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

class ReviewAction(BaseModel):
    action: str  # approve | reject | edit
    notes: str | None = None
    edits: dict[str, Any] | None = None

@app.get("/v1/review-queue")
def list_review_queue(status: str = Query("PENDING"), limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"items": store.list_review_queue(status=status, limit=limit)}

@app.post("/v1/review-queue")
def enqueue_review(body: dict[str, Any]) -> dict[str, Any]:
    item_id = store.enqueue_review(body)
    return {"id": item_id}

@app.post("/v1/review-queue/{item_id}/action")
def review_action(item_id: str, body: ReviewAction) -> dict[str, Any]:
    ok = store.update_review(item_id, body.action, body.notes, body.edits)
    if not ok:
        raise HTTPException(404, "item not found")
    return {"ok": True}

# ---------------------------------------------------------------------------
# SLA / metrics / cost
# ---------------------------------------------------------------------------

@app.get("/v1/sla")
def sla_summary() -> dict[str, Any]:
    return store.sla_summary()

@app.get("/v1/metrics/summary")
def metrics_summary() -> dict[str, Any]:
    return store.metrics_summary()

@app.get("/v1/data-quality/recent")
def data_quality(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    return {"items": store.data_quality_recent(limit)}

class CostEvent(BaseModel):
    service: str
    amount_usd: float
    units: float | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

@app.post("/v1/cost/events")
def record_cost(body: CostEvent) -> dict[str, Any]:
    store.record_cost_event(body.service, body.amount_usd, body.units, body.run_id, body.metadata)
    return {"ok": True}

@app.get("/v1/cost/summary")
def cost_summary() -> dict[str, Any]:
    return store.cost_summary()

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
