"""
Stage worker — pulls cfi-stage-dispatch and runs the matching Data Plane stage.

Env:
  GCP_PROJECT=intelligent-machines
  PUBSUB_STAGE_SUBSCRIPTION=cfi-stage-dispatch-sub
  CONTROL_PLANE_URL=http://control-plane:8080
  GCS_BUCKET=customer-feedback

Usage:
  python stage_worker.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

import httpx

from agentic_rag import run_agentic_rag
from analytics import run_analytics
from classify import run_classify
from common import control_plane_url, gcs_bucket_name, load_pipeline_config
from features import run_features
from ingest import run_ingest, run_ingest_batch
from normalize import run_normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("stage-worker")

PROJECT = os.environ.get("GCP_PROJECT", "intelligent-machines")
SUBSCRIPTION = os.environ.get("PUBSUB_STAGE_SUBSCRIPTION", "cfi-stage-dispatch-sub")


def get_run(pipeline_run_id: str) -> dict[str, Any]:
    url = f"{control_plane_url()}/v1/runs/{pipeline_run_id}"
    with httpx.Client(timeout=30.0) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json()


def report_running(pipeline_run_id: str, stage: str) -> None:
    url = f"{control_plane_url()}/v1/runs/{pipeline_run_id}/stages"
    with httpx.Client(timeout=30.0) as client:
        client.post(url, json={"stage": stage, "status": "RUNNING"})


def handle_message(data: dict[str, Any]) -> None:
    pipeline_run_id = data["pipeline_run_id"]
    stage = data["stage"]
    correlation_id = data.get("correlation_id", "")
    logger.info("handle stage=%s run=%s corr=%s", stage, pipeline_run_id, correlation_id)

    run = get_run(pipeline_run_id)
    mode = run.get("mode", "HEARTBEAT")
    source_filter = run.get("source_filter")
    ingestion_run_id = run.get("ingestion_run_id")
    # Optional mode metadata stored on run metrics/config
    watermark = None
    try:
        with httpx.Client(timeout=15.0) as client:
            if source_filter:
                wr = client.get(f"{control_plane_url()}/v1/watermarks/{source_filter}")
                if wr.status_code == 200:
                    watermark = wr.json().get("watermark")
    except Exception:
        pass

    report_running(pipeline_run_id, stage)

    if stage == "INGEST":
        # Batch ingest driven by mode
        results = run_ingest_batch(
            pipeline_run_id=pipeline_run_id,
            ingestion_run_id=ingestion_run_id,
            mode=mode,
            source_filter=source_filter,
            watermark=watermark,
        )
        logger.info("ingest batch count=%s", len(results))
        # Chain normalize for each envelope in-process for reliability
        for env in results:
            norm = run_normalize(pipeline_run_id, env)
            feat = run_features(pipeline_run_id, norm)
            cls = run_classify(pipeline_run_id, feat)
            run_analytics(pipeline_run_id, cls)
            run_agentic_rag(pipeline_run_id, cls)
        return

    # Single-stage messages (if control plane dispatches finer-grained later)
    if stage == "NORMALIZE":
        logger.warning("NORMALIZE-only message: expect payload in future; skipping")
        return
    if stage == "FEATURES":
        logger.warning("FEATURES-only message skipped without artifact payload")
        return
    if stage == "CLASSIFY":
        logger.warning("CLASSIFY-only message skipped without artifact payload")
        return
    if stage == "ANALYTICS":
        logger.warning("ANALYTICS-only message skipped without artifact payload")
        return
    if stage == "AGENTIC_RAG":
        logger.warning("AGENTIC_RAG-only message skipped without artifact payload")
        return
    if stage == "JIRA":
        logger.info("JIRA stage owned by agentic_rag / JiraTool")
        return

    logger.warning("unknown stage %s", stage)


def pull_loop() -> None:
    try:
        from google.cloud import pubsub_v1
    except ImportError:
        logger.error("google-cloud-pubsub required")
        sys.exit(1)

    subscriber = pubsub_v1.SubscriberClient()
    sub_path = subscriber.subscription_path(PROJECT, SUBSCRIPTION)
    logger.info("listening on %s", sub_path)

    def callback(message: Any) -> None:
        try:
            data = json.loads(message.data.decode("utf-8"))
            handle_message(data)
            message.ack()
        except Exception as e:
            logger.exception("stage failed: %s", e)
            message.nack()

    streaming_pull = subscriber.subscribe(sub_path, callback=callback)
    try:
        streaming_pull.result()
    except KeyboardInterrupt:
        streaming_pull.cancel()


def run_once_local_demo() -> None:
    """Without Pub/Sub: create a HEARTBEAT run via API and process demo payload."""
    from ingest import run_ingest

    with httpx.Client(timeout=30.0) as client:
        r = client.post(f"{control_plane_url()}/v1/runs", json={"mode": "HEARTBEAT", "source_filter": "mobile_app"})
        if r.status_code >= 400:
            logger.error("create run failed: %s", r.text)
            return
        run = r.json()
    pid = run["pipeline_run_id"]
    iid = run["ingestion_run_id"]
    env = run_ingest(
        pid,
        iid,
        "mobile_app",
        "demo-001",
        {
            "user_id": "cust-4421",
            "message": "App crashes every time I try to checkout on Android",
            "os": "Android 14",
            "product": "Checkout",
            "ts": "2026-09-18T09:58:00Z",
        },
    )
    norm = run_normalize(pid, env)
    feat = run_features(pid, norm, {"similarCount1h": 38, "baseline1h": 3.2})
    cls = run_classify(pid, feat)
    run_analytics(pid, cls)
    ticket = run_agentic_rag(pid, cls)
    logger.info("demo complete: %s", json.dumps({"run": pid, "ticket": ticket}, default=str)[:500])


if __name__ == "__main__":
    if os.environ.get("STAGE_WORKER_MODE", "pubsub") == "demo":
        run_once_local_demo()
    else:
        try:
            pull_loop()
        except Exception as e:
            logger.warning("Pub/Sub unavailable (%s); sleeping. Set STAGE_WORKER_MODE=demo for local.", e)
            while True:
                time.sleep(60)
