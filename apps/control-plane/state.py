"""
Control Plane domain models + Firestore metadata store.

Database: customer-feedback-intelligence (Firestore Native)
Collections:
  pipeline_runs, ticket_candidates, lineage, heartbeats,
  idempotency_keys, model_versions, config, dlq, watermarks,
  review_queue, cost_events
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger("control-plane.state")

FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE", "customer-feedback-intelligence")
GCP_PROJECT = os.environ.get("GCP_PROJECT", "intelligent-machines")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunMode(str, Enum):
    INITIAL_FULL = "INITIAL_FULL"
    HEARTBEAT = "HEARTBEAT"
    ON_DEMAND_FULL = "ON_DEMAND_FULL"
    TARGETED_REPLAY = "TARGETED_REPLAY"


class RunStatus(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"
    RESUMED = "RESUMED"
    REPROCESS_REQUESTED = "REPROCESS_REQUESTED"


class StageName(str, Enum):
    INGEST = "INGEST"
    NORMALIZE = "NORMALIZE"
    FEATURES = "FEATURES"
    CLASSIFY = "CLASSIFY"
    ANALYTICS = "ANALYTICS"
    AGENTIC_RAG = "AGENTIC_RAG"
    JIRA = "JIRA"


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


STAGE_DEPENDENCIES: dict[StageName, list[StageName]] = {
    StageName.INGEST: [],
    StageName.NORMALIZE: [StageName.INGEST],
    StageName.FEATURES: [StageName.NORMALIZE],
    StageName.CLASSIFY: [StageName.FEATURES],
    StageName.ANALYTICS: [StageName.CLASSIFY],
    StageName.AGENTIC_RAG: [StageName.CLASSIFY],
    StageName.JIRA: [StageName.AGENTIC_RAG],
}


class StageState(BaseModel):
    name: StageName
    status: StageStatus = StageStatus.PENDING
    artifact_uri: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class PipelineRun(BaseModel):
    pipeline_run_id: str
    pipeline_version: str = "1.3.0"
    source: str = "api"
    mode: RunMode = RunMode.ON_DEMAND_FULL
    status: RunStatus = RunStatus.CREATED
    stages: list[StageState] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class ReviewItem(BaseModel):
    review_id: str
    classified_id: str
    pipeline_run_id: str | None = None
    text: str = ""
    business_unit: str | None = None
    category: str | None = None
    confidence: float | None = None
    status: str = "PENDING"
    reason: str | None = None
    reviewer_notes: str | None = None
    created_at: str
    updated_at: str


class ModelVersionRecord(BaseModel):
    model_id: str
    version: str
    task: str = "classifier"
    status: str = "candidate"
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_uri: str | None = None
    dataset_version: str | None = None
    created_at: str


def default_stages() -> list[StageState]:
    return [StageState(name=s) for s in StageName]


def stage_deps_satisfied(run: PipelineRun, stage: StageName) -> bool:
    deps = STAGE_DEPENDENCIES.get(stage, [])
    by_name = {s.name: s for s in run.stages}
    for d in deps:
        st = by_name.get(d)
        if not st or st.status not in (StageStatus.COMPLETED, StageStatus.SKIPPED):
            return False
    return True


def next_runnable_stages(run: PipelineRun) -> list[StageName]:
    out = []
    by_name = {s.name: s for s in run.stages}
    for s in StageName:
        st = by_name.get(s)
        if st and st.status == StageStatus.PENDING and stage_deps_satisfied(run, s):
            out.append(s)
    return out


class FirestoreStore:
    """Firestore-backed store with in-memory fallback for local dev."""

    def __init__(self, project: str | None = None, database: str | None = None) -> None:
        self.project = project or GCP_PROJECT
        self.database = database or FIRESTORE_DATABASE
        self._memory: dict[str, dict[str, Any]] = {
            "runs": {},
            "review": {},
            "models": {},
            "config": {},
            "cost": [],
            "heartbeats": {},
            "dlq": [],
        }
        self._client = None
        try:
            from google.cloud import firestore
            self._client = firestore.Client(project=self.project, database=self.database)
            logger.info("Firestore client ready db=%s", self.database)
        except Exception as e:
            logger.warning("Firestore unavailable, using memory store: %s", e)

    def _col(self, name: str):
        if self._client:
            return self._client.collection(name)
        return None

    def create_run(self, pipeline_version: str, source: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        rid = f"run-{uuid4().hex[:12]}"
        now = _now()
        run = PipelineRun(
            pipeline_run_id=rid,
            pipeline_version=pipeline_version,
            source=source,
            stages=default_stages(),
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._put_run(run)
        return run.model_dump()

    def _put_run(self, run: PipelineRun) -> None:
        data = run.model_dump()
        col = self._col("pipeline_runs")
        if col:
            col.document(run.pipeline_run_id).set(data)
        else:
            self._memory["runs"][run.pipeline_run_id] = data

    def get_run(self, pipeline_run_id: str) -> dict[str, Any] | None:
        col = self._col("pipeline_runs")
        if col:
            doc = col.document(pipeline_run_id).get()
            return doc.to_dict() if doc.exists else None
        return self._memory["runs"].get(pipeline_run_id)

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        col = self._col("pipeline_runs")
        if col:
            return [d.to_dict() for d in col.limit(limit).stream()]
        return list(self._memory["runs"].values())[:limit]

    def report_stage(
        self,
        run_id: str,
        stage: str,
        status: str,
        artifact_uri: str | None = None,
        metrics: dict | None = None,
        error: str | None = None,
    ) -> bool:
        run = self.get_run(run_id)
        if not run:
            return False
        for s in run.get("stages", []):
            if s.get("name") == stage or s.get("name") == getattr(StageName, stage, stage):
                s["status"] = status
                if artifact_uri:
                    s["artifact_uri"] = artifact_uri
                if metrics:
                    s["metrics"] = metrics
                if error:
                    s["error"] = error
                s["updated_at"] = _now()
                break
        run["updated_at"] = _now()
        col = self._col("pipeline_runs")
        if col:
            col.document(run_id).set(run)
        else:
            self._memory["runs"][run_id] = run
        return True

    # --- Models ---
    def register_model(
        self,
        name: str,
        version: str,
        task: str = "classifier",
        metrics: dict | None = None,
        artifact_uri: str | None = None,
        status: str = "candidate",
    ) -> str:
        mid = f"{name}@{version}"
        rec = {
            "model_id": name,
            "version": version,
            "task": task,
            "status": status,
            "metrics": metrics or {},
            "artifact_uri": artifact_uri,
            "created_at": _now(),
        }
        col = self._col("model_versions")
        if col:
            col.document(mid).set(rec)
        else:
            self._memory["models"][mid] = rec
        return mid

    def list_models(self) -> list[dict[str, Any]]:
        col = self._col("model_versions")
        if col:
            return [d.to_dict() for d in col.stream()]
        return list(self._memory["models"].values())

    def promote_model(self, model_id: str) -> bool:
        # mark matching docs as production
        models = self.list_models()
        found = False
        for m in models:
            if m.get("model_id") == model_id or f"{m.get('model_id')}@{m.get('version')}" == model_id:
                m["status"] = "production"
                mid = f"{m['model_id']}@{m['version']}"
                col = self._col("model_versions")
                if col:
                    col.document(mid).set(m)
                else:
                    self._memory["models"][mid] = m
                found = True
        return found

    # --- Config ---
    def get_config(self, key: str) -> dict[str, Any] | None:
        col = self._col("config")
        if col:
            doc = col.document(key).get()
            return doc.to_dict() if doc.exists else None
        return self._memory["config"].get(key)

    def put_config(self, key: str, value: dict[str, Any]) -> None:
        col = self._col("config")
        if col:
            col.document(key).set(value)
        else:
            self._memory["config"][key] = value

    # --- Review queue ---
    def enqueue_review(self, body: dict[str, Any]) -> str:
        rid = body.get("review_id") or f"rev-{uuid4().hex[:10]}"
        now = _now()
        item = {
            "review_id": rid,
            "classified_id": body.get("classified_id", ""),
            "pipeline_run_id": body.get("pipeline_run_id"),
            "text": body.get("text", ""),
            "business_unit": body.get("business_unit"),
            "category": body.get("category"),
            "confidence": body.get("confidence"),
            "status": "PENDING",
            "reason": body.get("reason"),
            "created_at": now,
            "updated_at": now,
        }
        col = self._col("review_queue")
        if col:
            col.document(rid).set(item)
        else:
            self._memory["review"][rid] = item
        return rid

    def list_review_queue(self, status: str | None = "PENDING", limit: int = 50) -> list[dict[str, Any]]:
        col = self._col("review_queue")
        if col:
            q = col.limit(limit)
            items = [d.to_dict() for d in q.stream()]
            if status:
                items = [i for i in items if i.get("status") == status]
            return items[:limit]
        items = list(self._memory["review"].values())
        if status:
            items = [i for i in items if i.get("status") == status]
        return items[:limit]

    def update_review(
        self,
        review_id: str,
        action: str,
        notes: str | None = None,
        edits: dict | None = None,
    ) -> bool:
        col = self._col("review_queue")
        item = None
        if col:
            doc = col.document(review_id).get()
            if not doc.exists:
                return False
            item = doc.to_dict()
        else:
            item = self._memory["review"].get(review_id)
            if not item:
                return False
        status_map = {"approve": "APPROVED", "reject": "REJECTED", "edit": "EDITED"}
        item["status"] = status_map.get(action, action.upper())
        if notes:
            item["reviewer_notes"] = notes
        if edits:
            item.update(edits)
        item["updated_at"] = _now()
        if col:
            col.document(review_id).set(item)
        else:
            self._memory["review"][review_id] = item
        return True

    # --- Cost ---
    def record_cost_event(
        self,
        service: str,
        amount_usd: float,
        units: float | None = None,
        run_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        evt = {
            "service": service,
            "amount_usd": amount_usd,
            "units": units,
            "run_id": run_id,
            "metadata": metadata or {},
            "ts": _now(),
        }
        col = self._col("cost_events")
        if col:
            col.add(evt)
        else:
            self._memory["cost"].append(evt)

    def cost_summary(self, limit: int = 500) -> dict[str, Any]:
        col = self._col("cost_events")
        events = []
        if col:
            events = [d.to_dict() for d in col.limit(limit).stream()]
        else:
            events = self._memory["cost"][-limit:]
        by_svc: dict[str, float] = {}
        total = 0.0
        for e in events:
            amt = float(e.get("amount_usd") or 0)
            total += amt
            svc = e.get("service") or "unknown"
            by_svc[svc] = by_svc.get(svc, 0.0) + amt
        return {"total_usd": round(total, 4), "by_service": by_svc, "event_count": len(events)}

    # --- SLA / metrics stubs ---
    def sla_summary(self) -> dict[str, Any]:
        runs = self.list_runs(100)
        completed = sum(1 for r in runs if r.get("status") == "COMPLETED")
        failed = sum(1 for r in runs if r.get("status") == "FAILED")
        return {
            "healthy": failed == 0 or (completed / max(1, completed + failed)) > 0.95,
            "completed": completed,
            "failed": failed,
            "sample_size": len(runs),
        }

    def metrics_summary(self) -> dict[str, Any]:
        runs = self.list_runs(200)
        completed = sum(1 for r in runs if r.get("status") == "COMPLETED")
        return {
            "ingested": len(runs),
            "completed": completed,
            "tickets_created": sum(1 for r in runs if any(
                s.get("name") == "JIRA" and s.get("status") == "COMPLETED" for s in r.get("stages", [])
            )),
            "success_rate": completed / max(1, len(runs)),
        }

    def data_quality_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        return []


# Singleton used by main.py
store = FirestoreStore()
