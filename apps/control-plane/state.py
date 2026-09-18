"""
Control Plane state machine + in-memory metadata store.

Production: replace MetadataStore with Cloud Spanner or Firestore.
States follow the LLD:
  CREATED → VALIDATING → RUNNING → COMPLETED
  RUNNING → FAILED → RETRY_PENDING → RUNNING
  FAILED → MANUAL_INTERVENTION → RESUMED
  COMPLETED → REPROCESS_REQUESTED → RUNNING

Ticket candidate path:
  CREATED → VALIDATED → DEDUP_CHECK → READY → JIRA_CREATED | EXISTING_TICKET | FAILED
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


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


class PipelineRun(BaseModel):
    pipeline_run_id: str
    ingestion_run_id: str
    correlation_id: str
    mode: RunMode
    status: RunStatus = RunStatus.CREATED
    stages: list[StageState] = Field(default_factory=list)
    source_filter: str | None = None
    checkpoint_stage: StageName | None = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: str
    updated_at: str
    error: str | None = None
    metrics: dict[str, int] = Field(default_factory=dict)


class TicketCandidateState(BaseModel):
    ticket_candidate_id: str
    pipeline_run_id: str
    classified_id: str
    status: str = "CREATED"  # CREATED|VALIDATED|DEDUP_CHECK|READY|JIRA_CREATED|EXISTING_TICKET|FAILED
    deduplication_key: str | None = None
    jira_key: str | None = None
    error: str | None = None
    created_at: str
    updated_at: str


def default_stages() -> list[StageState]:
    return [StageState(name=s) for s in StageName]


class MetadataStore:
    """Thread-safe in-memory store. Swap for Spanner/Firestore in production."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._runs: dict[str, PipelineRun] = {}
        self._tickets: dict[str, TicketCandidateState] = {}
        self._lineage: dict[str, list[str]] = {}  # artifact_id -> parent ids
        self._heartbeats: dict[str, str] = {}  # source -> last_seen ISO

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
        with self._lock:
            self._runs[rid] = run
        return run

    def get_run(self, pipeline_run_id: str) -> PipelineRun | None:
        with self._lock:
            return self._runs.get(pipeline_run_id)

    def list_runs(self, limit: int = 50) -> list[PipelineRun]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
            return runs[:limit]

    def transition(self, pipeline_run_id: str, new_status: RunStatus, error: str | None = None) -> PipelineRun:
        with self._lock:
            run = self._runs.get(pipeline_run_id)
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
            self._runs[pipeline_run_id] = run
            return run.model_copy(deep=True)

    def update_stage(
        self,
        pipeline_run_id: str,
        stage: StageName,
        status: StageStatus,
        artifact_ids: list[str] | None = None,
        error: str | None = None,
    ) -> PipelineRun:
        with self._lock:
            run = self._runs.get(pipeline_run_id)
            if not run:
                raise KeyError(pipeline_run_id)
            for st in run.stages:
                if st.name == stage:
                    st.status = status
                    if status == StageStatus.RUNNING:
                        st.started_at = _now()
                    if status in (StageStatus.COMPLETED, StageStatus.FAILED, StageStatus.SKIPPED):
                        st.completed_at = _now()
                    if artifact_ids:
                        st.artifact_ids.extend(artifact_ids)
                    if error:
                        st.error = error
                    break
            run.updated_at = _now()
            if status == StageStatus.COMPLETED and stage == StageName.JIRA:
                # mark metrics placeholder
                run.metrics["tickets"] = run.metrics.get("tickets", 0) + len(artifact_ids or [])
            self._runs[pipeline_run_id] = run
            return run.model_copy(deep=True)

    def set_metric(self, pipeline_run_id: str, key: str, value: int) -> None:
        with self._lock:
            run = self._runs.get(pipeline_run_id)
            if run:
                run.metrics[key] = value
                run.updated_at = _now()

    def record_lineage(self, artifact_id: str, parent_ids: list[str]) -> None:
        with self._lock:
            self._lineage[artifact_id] = parent_ids

    def get_lineage(self, artifact_id: str) -> list[str]:
        with self._lock:
            return list(self._lineage.get(artifact_id, []))

    def heartbeat(self, source: str) -> str:
        ts = _now()
        with self._lock:
            self._heartbeats[source] = ts
        return ts

    def list_heartbeats(self) -> dict[str, str]:
        with self._lock:
            return dict(self._heartbeats)

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
        with self._lock:
            self._tickets[tid] = tc
        return tc

    def update_ticket_candidate(self, ticket_candidate_id: str, **kwargs: Any) -> TicketCandidateState:
        with self._lock:
            tc = self._tickets.get(ticket_candidate_id)
            if not tc:
                raise KeyError(ticket_candidate_id)
            data = tc.model_dump()
            data.update(kwargs)
            data["updated_at"] = _now()
            tc = TicketCandidateState(**data)
            self._tickets[ticket_candidate_id] = tc
            return tc

    def get_ticket_candidate(self, ticket_candidate_id: str) -> TicketCandidateState | None:
        with self._lock:
            return self._tickets.get(ticket_candidate_id)


store = MetadataStore()
