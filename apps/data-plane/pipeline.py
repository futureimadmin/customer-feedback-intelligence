"""
End-to-end Data Plane runner: ingest → normalize → features → classify
→ analytics (BQ) → agentic RAG → Jira (via Control Plane).

Usage:
  export CONTROL_PLANE_URL=http://localhost:8080
  export JIRA_TOOL_URL=http://localhost:8081   # used by control-plane
  python pipeline.py
"""
from __future__ import annotations

import json
import logging
import sys

import httpx

from agentic_rag import run_agentic_rag
from analytics import run_analytics
from classify import run_classify
from common import control_plane_url
from features import run_features
from ingest import run_ingest
from normalize import run_normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("data-plane.pipeline")


def create_control_plane_run(mode: str = "HEARTBEAT") -> dict:
    url = f"{control_plane_url()}/v1/runs"
    try:
        with httpx.Client(timeout=15.0) as client:
            r = client.post(url, json={"mode": mode})
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("Control Plane unavailable (%s); using local demo ids", e)
        return {
            "pipeline_run_id": "pipe-local-demo",
            "ingestion_run_id": "run-local-demo",
            "correlation_id": "corr-local",
        }


def run_one(payload: dict, source_system: str = "mobile_app", source_record_id: str = "src-001") -> dict:
    run = create_control_plane_run("HEARTBEAT")
    pipeline_run_id = run["pipeline_run_id"]
    ingestion_run_id = run["ingestion_run_id"]

    logger.info("=== pipeline_run_id=%s ===", pipeline_run_id)

    envelope = run_ingest(pipeline_run_id, ingestion_run_id, source_system, source_record_id, payload)
    normalized = run_normalize(pipeline_run_id, envelope)
    features = run_features(
        pipeline_run_id,
        normalized,
        window_stats={"similarCount1h": 38, "baseline1h": 3.2},
    )
    classified = run_classify(pipeline_run_id, features)
    analytics = run_analytics(pipeline_run_id, classified)
    ticket = run_agentic_rag(pipeline_run_id, classified)

    return {
        "pipeline_run_id": pipeline_run_id,
        "envelopeId": envelope.get("envelopeId"),
        "normalizedId": normalized.get("artifactId"),
        "featureId": features.get("artifactId"),
        "classifiedId": classified.get("classifiedId"),
        "reviewRequired": classified.get("reviewRequired"),
        "analytics": analytics.get("status"),
        "ticket": ticket,
    }


DEMO_PAYLOAD = {
    "user_id": "cust-4421",
    "message": "App crashes every time I try to checkout on Android",
    "os": "Android 14",
    "app_ver": "5.2.1",
    "ts": "2026-09-18T09:58:00Z",
    "product": "Checkout",
    "channel": "mobile_app",
}


if __name__ == "__main__":
    result = run_one(DEMO_PAYLOAD)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0)
