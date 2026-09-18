"""
Control Plane domain models + Firestore metadata store.

Database: customer-feedback-intelligence (Firestore Native)
Collections:
  pipeline_runs, ticket_candidates, lineage, heartbeats,
  idempotency_keys, model_versions, config, dlq, watermarks
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


# Stage dependency graph: stage may start only when all deps are COMPLETED or SKIPPED
STAGE_DEPENDENCIES: dict[StageName, list[StageName]] = {
    StageName.INGEST: [],
    StageName.NORMALIZE: [StageName.INGEST],
    StageName.FEATURES: [StageName.NORMALIZE],
    StageName.CLASSIFY: [StageName.FEATURES],
    StageName.ANALYTICS: [StageName.CLASSIFY],
    StageName.AGENTIC_RAG: [StageName.CLASSIFY],
    StageName.JIRA: [StageName.AGENTIC_RAG],
}

ALLOWED_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.CREATED: {RunStatus.VALIDATING, RunStatus.FAILED},
    RunStatus.VALIDATING: {RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED},
    RunStatus.FAILED: {RunStatus.RETRY_PENDING, RunStatus.MANUAL_INTERVENTION},
    RunStatus.RETRY_PENDING: {RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.MANUAL_INTERVENTION: {RunStatus.RESUMED, RunStatus.FAILED},
    RunStatus.RESUMED: {RunStatus.RUNNING, RunStatus.FAILED},
    RunStatus.COMPLETED: {RunStatus.REPROCESS_REQUESTED},
    RunStatus.REPROCESS_REQUESTED: {RunStatus.RUNNING},
}


class StageState(BaseModel):
    name: StageName
    status: StageStatus = StageStatus.PENDING
    artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)


class PipelineRun(BaseModel):
    pipeline_run_id: str
    ingestion_run_id: str
    correlation_id: str
    mode: RunMode
    status: RunStatus = RunStatus.CREATED
    stages: list[StageState] = Field(default_factory=list)
    source_filter: str | None = None
    checkpoint_stage: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    next_retry_at: str | None = None
    created_at: str
    updated_at: str
    error: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)


class TicketCandidateState(BaseModel):
    ticket_candidate_id: str
    pipeline_run_id: str
    classified_id: str
    status: str = "CREATED"
    deduplication_key: str | None = None
    jira_key: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class ModelVersionRecord(BaseModel):
    model_id: str
    version: str
    task: str
    status: str = "CANDIDATE"  # CANDIDATE | PRODUCTION | RETIRED
    metrics: dict[str, float] = Field(default_factory=dict)
    dataset_version: str | None = None
    endpoint: str | None = None
    promoted_at: str | None = None
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
    out: list[StageName] = []
    for st in run.stages:
        if st.status == StageStatus.PENDING and stage_deps_satisfied(run, st.name):
            out.append(st.name)
    return out


class FirestoreStore:
    """Durable Control Plane state on Firestore database customer-feedback-intelligence."""

    def __init__(self, project: str | None = None, database: str | None = None) -> None:
        self.project = project or GCP_PROJECT
        self.database = database or FIRESTORE_DATABASE
        self._client = None
        self._memory_fallback = False
        self._mem: dict[str, Any] = {
            "runs": {},
            "tickets": {},
            "lineage": {},
            "heartbeats": {},
            "idempotency": {},
            "models": {},
            "config": {},
            "dlq": {},
            "watermarks": {},
        }
        try:
            from google.cloud import firestore

            self._client = firestore.Client(project=self.project, database=self.database)
            logger.info("Firestore client ready project=%s database=%s", self.project, self.database)
        except Exception as e:
            logger.warning("Firestore unavailable (%s); using in-memory fallback", e)
            self._memory_fallback = True

    def _col(self, name: str):
        assert self._client is not None
        return self._client.collection(name)

    # ---- runs ----
    def create_run(
        self,
        mode: RunMode,
        source_filter: str | None = None,
        ingestion_run_id: str | None = None,
    ) -> PipelineRun:
        rid = f"pipe-{uuid4().hex[:12]}"
        iid = ingestion_run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:4]}"
        cid = f"corr-{uuid4().hex[:12]}"
        now = _now()
        run = PipelineRun(
            pipeline_run_id=rid,
            ingestion_run_id=iid,
            correlation_id=cid,
            mode=mode,
            stages=default_stages(),
            source_filter=source_filter,
            created_at=now,
            updated_at=now,
        )
        self._put_run(run)
        return run

    def _put_run(self, run: PipelineRun) -> None:
        data = run.model_dump(mode="json")
        if self._memory_fallback:
            self._mem["runs"][run.pipeline_run_id] = data
            return
        self._col("pipeline_runs").document(run.pipeline_run_id).set(data)

    def get_run(self, pipeline_run_id: str) -> PipelineRun | None:
        if self._memory_fallback:
            raw = self._mem["runs"].get(pipeline_run_id)
            return PipelineRun(**raw) if raw else None
        doc = self._col("pipeline_runs").document(pipeline_run_id).get()
        if not doc.exists:
            return None
        return PipelineRun(**doc.to_dict())

    def list_runs(self, limit: int = 50) -> list[PipelineRun]:
        if self._memory_fallback:
            runs = [PipelineRun(**v) for v in self._mem["runs"].values()]
            return sorted(runs, key=lambda r: r.created_at, reverse=True)[:limit]
        docs = (
            self._col("pipeline_runs")
            .order_by("created_at", direction="DESCENDING")
            .limit(limit)
            .stream()
        )
        return [PipelineRun(**d.to_dict()) for d in docs]

    def transition(self, pipeline_run_id: str, new_status: RunStatus, error: str | None = None) -> PipelineRun:
        run = self.get_run(pipeline_run_id)
        if not run:
            raise KeyError(pipeline_run_id)
        allowed = ALLOWED_TRANSITIONS.get(run.status, set())
        if new_status not in allowed:
            raise ValueError(f"Illegal transition {run.status} → {new_status}")
        run.status = new_status
        run.updated_at = _now()
        if error:
            run.error = error
        if new_status == RunStatus.RETRY_PENDING:
            run.retry_count += 1
        self._put_run(run)
        return run

    def update_stage(
        self,
        pipeline_run_id: str,
        stage: StageName,
        status: StageStatus,
        artifact_ids: list[str] | None = None,
        error: str | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> PipelineRun:
        run = self.get_run(pipeline_run_id)
        if not run:
            raise KeyError(pipeline_run_id)
        for st in run.stages:
            if st.name == stage:
                st.status = status
                if status == StageStatus.RUNNING:
                    st.started_at = _now()
                if status in (StageStatus.COMPLETED, StageStatus.FAILED, StageStatus.SKIPPED):
                    st.completed_at = _now()
                    run.checkpoint_stage = stage.value
                if artifact_ids:
                    st.artifact_ids.extend(artifact_ids)
                if error:
                    st.error = error
                if checkpoint:
                    st.checkpoint.update(checkpoint)
                break
        run.updated_at = _now()
        if status == StageStatus.COMPLETED and stage == StageName.JIRA:
            run.metrics["tickets"] = run.metrics.get("tickets", 0) + len(artifact_ids or [])
        self._put_run(run)
        return run

    def set_metric(self, pipeline_run_id: str, key: str, value: int) -> None:
        run = self.get_run(pipeline_run_id)
        if run:
            run.metrics[key] = value
            run.updated_at = _now()
            self._put_run(run)

    def set_next_retry(self, pipeline_run_id: str, next_retry_at: str) -> PipelineRun:
        run = self.get_run(pipeline_run_id)
        if not run:
            raise KeyError(pipeline_run_id)
        run.next_retry_at = next_retry_at
        run.updated_at = _now()
        self._put_run(run)
        return run

    def reset_stages_from_checkpoint(self, pipeline_run_id: str) -> PipelineRun:
        """On retry: leave completed stages; re-queue failed/pending after checkpoint."""
        run = self.get_run(pipeline_run_id)
        if not run:
            raise KeyError(pipeline_run_id)
        for st in run.stages:
            if st.status == StageStatus.FAILED:
                st.status = StageStatus.PENDING
                st.error = None
                st.started_at = None
                st.completed_at = None
        run.updated_at = _now()
        self._put_run(run)
        return run

    # ---- lineage ----
    def record_lineage(self, artifact_id: str, parent_ids: list[str]) -> None:
        data = {"artifact_id": artifact_id, "parent_artifact_ids": parent_ids, "updated_at": _now()}
        if self._memory_fallback:
            self._mem["lineage"][artifact_id] = data
            return
        self._col("lineage").document(artifact_id).set(data)

    def get_lineage(self, artifact_id: str) -> list[str]:
        if self._memory_fallback:
            return list(self._mem["lineage"].get(artifact_id, {}).get("parent_artifact_ids", []))
        doc = self._col("lineage").document(artifact_id).get()
        if not doc.exists:
            return []
        return list(doc.to_dict().get("parent_artifact_ids", []))

    # ---- heartbeats & watermarks ----
    def heartbeat(self, source: str, watermark: str | None = None) -> str:
        ts = _now()
        data = {"source": source, "last_seen": ts, "watermark": watermark}
        if self._memory_fallback:
            self._mem["heartbeats"][source] = data
            if watermark:
                self._mem["watermarks"][source] = watermark
            return ts
        self._col("heartbeats").document(source).set(data, merge=True)
        if watermark:
            self._col("watermarks").document(source).set(
                {"source": source, "watermark": watermark, "updated_at": ts}, merge=True
            )
        return ts

    def list_heartbeats(self) -> dict[str, Any]:
        if self._memory_fallback:
            return dict(self._mem["heartbeats"])
        return {d.id: d.to_dict() for d in self._col("heartbeats").stream()}

    def get_watermark(self, source: str) -> str | None:
        if self._memory_fallback:
            return self._mem["watermarks"].get(source)
        doc = self._col("watermarks").document(source).get()
        if not doc.exists:
            return None
        return doc.to_dict().get("watermark")

    # ---- idempotency ----
    def claim_idempotency_key(self, key: str, owner: str) -> bool:
        """Return True if key newly claimed; False if already exists."""
        if self._memory_fallback:
            if key in self._mem["idempotency"]:
                return False
            self._mem["idempotency"][key] = {"owner": owner, "created_at": _now()}
            return True
        ref = self._col("idempotency_keys").document(key)
        snap = ref.get()
        if snap.exists:
            return False
        ref.set({"key": key, "owner": owner, "created_at": _now()})
        return True

    def get_idempotency(self, key: str) -> dict[str, Any] | None:
        if self._memory_fallback:
            return self._mem["idempotency"].get(key)
        doc = self._col("idempotency_keys").document(key).get()
        return doc.to_dict() if doc.exists else None

    # ---- tickets ----
    def create_ticket_candidate(
        self, pipeline_run_id: str, classified_id: str, deduplication_key: str | None = None
    ) -> TicketCandidateState:
        tid = f"tc-{uuid4().hex[:12]}"
        now = _now()
        tc = TicketCandidateState(
            ticket_candidate_id=tid,
            pipeline_run_id=pipeline_run_id,
            classified_id=classified_id,
            deduplication_key=deduplication_key,
            created_at=now,
            updated_at=now,
        )
        data = tc.model_dump(mode="json")
        if self._memory_fallback:
            self._mem["tickets"][tid] = data
        else:
            self._col("ticket_candidates").document(tid).set(data)
        if deduplication_key:
            self.claim_idempotency_key(f"ticket:{deduplication_key}", tid)
        return tc

    def update_ticket_candidate(self, ticket_candidate_id: str, **kwargs: Any) -> TicketCandidateState:
        tc = self.get_ticket_candidate(ticket_candidate_id)
        if not tc:
            raise KeyError(ticket_candidate_id)
        data = tc.model_dump()
        data.update(kwargs)
        data["updated_at"] = _now()
        tc = TicketCandidateState(**data)
        payload = tc.model_dump(mode="json")
        if self._memory_fallback:
            self._mem["tickets"][ticket_candidate_id] = payload
        else:
            self._col("ticket_candidates").document(ticket_candidate_id).set(payload)
        return tc

    def get_ticket_candidate(self, ticket_candidate_id: str) -> TicketCandidateState | None:
        if self._memory_fallback:
            raw = self._mem["tickets"].get(ticket_candidate_id)
            return TicketCandidateState(**raw) if raw else None
        doc = self._col("ticket_candidates").document(ticket_candidate_id).get()
        return TicketCandidateState(**doc.to_dict()) if doc.exists else None

    # ---- model governance ----
    def register_model(self, record: ModelVersionRecord) -> ModelVersionRecord:
        doc_id = f"{record.model_id}__{record.version}"
        data = record.model_dump(mode="json")
        if self._memory_fallback:
            self._mem["models"][doc_id] = data
        else:
            self._col("model_versions").document(doc_id).set(data)
        return record

    def list_models(self, model_id: str | None = None) -> list[ModelVersionRecord]:
        if self._memory_fallback:
            vals = self._mem["models"].values()
            if model_id:
                vals = [v for v in vals if v.get("model_id") == model_id]
            return [ModelVersionRecord(**v) for v in vals]
        q = self._col("model_versions")
        if model_id:
            q = q.where("model_id", "==", model_id)
        return [ModelVersionRecord(**d.to_dict()) for d in q.stream()]

    def promote_model(self, model_id: str, version: str) -> ModelVersionRecord:
        models = self.list_models(model_id)
        target = None
        for m in models:
            if m.version == version:
                target = m
            elif m.status == "PRODUCTION":
                m.status = "RETIRED"
                self.register_model(m)
        if not target:
            raise KeyError(f"{model_id}@{version}")
        target.status = "PRODUCTION"
        target.promoted_at = _now()
        return self.register_model(target)

    # ---- config ----
    def set_config(self, key: str, value: dict[str, Any]) -> None:
        data = {"key": key, "value": value, "updated_at": _now()}
        if self._memory_fallback:
            self._mem["config"][key] = data
            return
        self._col("config").document(key).set(data)

    def get_config(self, key: str) -> dict[str, Any] | None:
        if self._memory_fallback:
            item = self._mem["config"].get(key)
            return item.get("value") if item else None
        doc = self._col("config").document(key).get()
        if not doc.exists:
            return None
        return doc.to_dict().get("value")

    def list_config(self) -> dict[str, Any]:
        if self._memory_fallback:
            return {k: v.get("value") for k, v in self._mem["config"].items()}
        return {d.id: d.to_dict().get("value") for d in self._col("config").stream()}

    # ---- DLQ ----
    def enqueue_dlq(
        self,
        pipeline_run_id: str,
        stage: str,
        payload: dict[str, Any],
        error: str,
        correlation_id: str | None = None,
    ) -> str:
        dlq_id = f"dlq-{uuid4().hex[:12]}"
        data = {
            "dlq_id": dlq_id,
            "pipeline_run_id": pipeline_run_id,
            "stage": stage,
            "payload": payload,
            "error": error,
            "correlation_id": correlation_id,
            "created_at": _now(),
            "status": "OPEN",
        }
        if self._memory_fallback:
            self._mem["dlq"][dlq_id] = data
        else:
            self._col("dlq").document(dlq_id).set(data)
        return dlq_id

    def list_dlq(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._memory_fallback:
            items = list(self._mem["dlq"].values())
            return sorted(items, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]
        docs = self._col("dlq").order_by("created_at", direction="DESCENDING").limit(limit).stream()
        return [d.to_dict() for d in docs]


store = FirestoreStore()
