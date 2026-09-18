"""Shared utilities for all Data Plane stage workers."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
import yaml

logger = logging.getLogger("data-plane")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def short_id() -> str:
    return uuid4().hex[:12]


def load_pipeline_config() -> dict[str, Any]:
    path = os.environ.get("PIPELINE_CONFIG_PATH", "/config/pipeline.yaml")
    if os.path.isfile(path):
        with open(path) as f:
            return yaml.safe_load(f).get("pipeline", {})
    return {
        "schema_version": "1.0",
        "pipeline_version": "1.0.0",
        "gcs": {
            "bucket": os.environ.get("GCS_BUCKET", "customer-feedback"),
            "prefixes": {
                "raw": "raw/",
                "raw_envelope": "raw-envelope/",
                "normalized": "curated/normalized/",
                "features": "curated/features/",
                "classified": "curated/classified/",
                "quarantine": "quarantine/",
            },
        },
        "classification": {"confidence_threshold": 0.75},
        "models": {
            "embedding_model": "text-embedding-004",
            "anomaly_score_threshold": 0.8,
        },
        "bigquery": {
            "dataset": "feedback_intelligence",
            "classified_table": "feedback_classified",
            "outcomes_table": "ticket_outcomes",
        },
        "agentic_rag": {
            "vector_search_top_k": 8,
            "prompt_version": "ticket-intel-v4.2",
        },
    }


def gcs_bucket_name(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_pipeline_config()
    return os.environ.get("GCS_BUCKET") or cfg.get("gcs", {}).get("bucket", "customer-feedback")


def control_plane_url() -> str:
    return os.environ.get("CONTROL_PLANE_URL", "http://localhost:8080").rstrip("/")


def report_stage(
    pipeline_run_id: str,
    stage: str,
    status: str,
    artifact_ids: list[str] | None = None,
    parent_artifact_ids: list[str] | None = None,
    error: str | None = None,
    metrics: dict[str, int] | None = None,
) -> None:
    """Notify Control Plane of stage progress."""
    url = f"{control_plane_url()}/v1/runs/{pipeline_run_id}/stages"
    body = {
        "stage": stage,
        "status": status,
        "artifact_ids": artifact_ids or [],
        "parent_artifact_ids": parent_artifact_ids or [],
        "error": error,
        "metrics": metrics or {},
    }
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(url, json=body)
            r.raise_for_status()
    except Exception as e:
        logger.warning("control-plane report failed: %s", e)


def write_json_local(path: str, obj: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def read_json_local(path: str) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


# Optional GCS (works when ADC / Workload Identity available)
def gcs_upload_json(bucket: str, object_name: str, obj: dict[str, Any]) -> str:
    try:
        from google.cloud import storage

        client = storage.Client()
        b = client.bucket(bucket)
        blob = b.blob(object_name)
        blob.upload_from_string(json.dumps(obj), content_type="application/json")
        return f"gs://{bucket}/{object_name}"
    except Exception as e:
        logger.warning("GCS upload skipped (%s); writing local fallback", e)
        local = os.path.join("/tmp", "cfi", object_name.replace("/", "_"))
        write_json_local(local, obj)
        return local
