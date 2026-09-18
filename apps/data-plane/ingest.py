"""
Stage 1 — Source ingestion & canonical envelope.

Modes:
  INITIAL_FULL / ON_DEMAND_FULL — scan all (or large) historical objects under source prefix
  HEARTBEAT — only objects newer than source watermark
  TARGETED_REPLAY — re-read from checkpoint path if provided
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any
from uuid import uuid4

from common import gcs_bucket_name, gcs_upload_json, load_pipeline_config, report_stage, utcnow

logger = logging.getLogger("data-plane.ingest")


def source_fingerprint(source_system: str, source_record_id: str, payload_text: str) -> str:
    h = hashlib.sha256(f"{source_system}|{source_record_id}|{payload_text}".encode()).hexdigest()
    return f"sha256:{h}"


def build_envelope(
    source_system: str,
    source_record_id: str,
    payload: dict[str, Any],
    ingestion_run_id: str,
    schema_version: str = "1.0",
) -> dict[str, Any]:
    raw_text = (
        payload.get("rawText")
        or payload.get("message")
        or payload.get("text")
        or str(payload)
    )
    return {
        "envelopeId": str(uuid4()),
        "ingestionRunId": ingestion_run_id,
        "sourceSystem": source_system,
        "sourceRecordId": source_record_id,
        "sourceFingerprint": source_fingerprint(source_system, source_record_id, raw_text),
        "ingestedAt": utcnow(),
        "schemaVersion": schema_version,
        "payload": {
            "rawText": raw_text,
            "timestamp": payload.get("timestamp") or payload.get("ts") or utcnow(),
            "channel": payload.get("channel") or source_system,
            "customerId": payload.get("customerId") or payload.get("user_id"),
            "product": payload.get("product"),
            "platform": payload.get("platform") or payload.get("os"),
            "metadata": {
                k: v
                for k, v in payload.items()
                if k
                not in {
                    "rawText",
                    "message",
                    "text",
                    "timestamp",
                    "ts",
                    "channel",
                    "customerId",
                    "user_id",
                    "product",
                    "platform",
                    "os",
                }
            },
        },
    }


def run_ingest(
    pipeline_run_id: str,
    ingestion_run_id: str,
    source_system: str,
    source_record_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    cfg = load_pipeline_config()
    bucket = gcs_bucket_name(cfg)
    prefixes = cfg.get("gcs", {}).get("prefixes", {})

    report_stage(pipeline_run_id, "INGEST", "RUNNING")

    try:
        envelope = build_envelope(
            source_system,
            source_record_id,
            payload,
            ingestion_run_id,
            schema_version=cfg.get("schema_version", "1.0"),
        )
        y, m, d = utcnow()[:4], utcnow()[5:7], utcnow()[8:10]
        raw_path = f"{prefixes.get('raw', 'raw/')}{source_system}/{y}/{m}/{d}/{ingestion_run_id}/{source_record_id}.json"
        env_path = f"{prefixes.get('raw_envelope', 'raw-envelope/')}{source_system}/{y}/{m}/{d}/{ingestion_run_id}/{envelope['envelopeId']}.json"

        gcs_upload_json(bucket, raw_path, {"source": payload, "ingestedAt": envelope["ingestedAt"]})
        uri = gcs_upload_json(bucket, env_path, envelope)

        report_stage(
            pipeline_run_id,
            "INGEST",
            "COMPLETED",
            artifact_ids=[envelope["envelopeId"]],
            metrics={"ingested": 1},
        )
        logger.info("ingested envelope %s -> %s", envelope["envelopeId"], uri)
        return envelope
    except Exception as e:
        report_stage(pipeline_run_id, "INGEST", "FAILED", error=str(e))
        raise


def _list_landing_objects(bucket: str, source: str, mode: str, watermark: str | None) -> list[dict[str, Any]]:
    """List objects under landing/ or raw/ for source; filter by watermark for HEARTBEAT."""
    prefix = f"landing/{source}/"
    results: list[dict[str, Any]] = []
    try:
        from google.cloud import storage

        client = storage.Client()
        b = client.bucket(bucket)
        for blob in client.list_blobs(b, prefix=prefix):
            if blob.name.endswith("/"):
                continue
            # HEARTBEAT: only objects updated after watermark (ISO compare on updated)
            if mode == "HEARTBEAT" and watermark:
                updated = blob.updated.isoformat() if blob.updated else ""
                if updated and updated <= watermark:
                    continue
            try:
                raw = blob.download_as_text()
                payload = json.loads(raw)
            except Exception:
                payload = {"message": blob.download_as_text()[:2000], "raw_path": blob.name}
            results.append(
                {
                    "source_record_id": blob.name.split("/")[-1].replace(".json", ""),
                    "payload": payload,
                    "blob_updated": blob.updated.isoformat() if blob.updated else utcnow(),
                }
            )
            if mode in ("INITIAL_FULL", "ON_DEMAND_FULL") and len(results) >= int(
                os.environ.get("FULL_INGEST_LIMIT", "5000")
            ):
                break
            if mode == "HEARTBEAT" and len(results) >= int(os.environ.get("HEARTBEAT_INGEST_LIMIT", "500")):
                break
    except Exception as e:
        logger.warning("GCS list failed (%s); using empty batch", e)
    return results


def run_ingest_batch(
    pipeline_run_id: str,
    ingestion_run_id: str,
    mode: str = "HEARTBEAT",
    source_filter: str | None = None,
    watermark: str | None = None,
) -> list[dict[str, Any]]:
    """Mode-aware batch ingest.

    INITIAL_FULL / ON_DEMAND_FULL: process all listed landing objects (capped).
    HEARTBEAT: only objects newer than watermark.
    TARGETED_REPLAY: same as heartbeat unless watermark forced null.
    """
    cfg = load_pipeline_config()
    bucket = gcs_bucket_name(cfg)
    source = source_filter or os.environ.get("DEFAULT_SOURCE", "mobile_app")

    report_stage(pipeline_run_id, "INGEST", "RUNNING")

    try:
        if mode in ("INITIAL_FULL", "ON_DEMAND_FULL"):
            effective_watermark = None  # full scan
        elif mode == "TARGETED_REPLAY":
            effective_watermark = watermark  # replay from given point
        else:
            effective_watermark = watermark  # HEARTBEAT incremental

        objects = _list_landing_objects(bucket, source, mode, effective_watermark)
        envelopes: list[dict[str, Any]] = []
        max_updated = effective_watermark or ""

        if not objects:
            # Dev fallback: no landing data — complete with zero so pipeline does not hang
            logger.info("no landing objects for source=%s mode=%s", source, mode)
            report_stage(
                pipeline_run_id,
                "INGEST",
                "COMPLETED",
                artifact_ids=[],
                metrics={"ingested": 0, "mode": mode},
            )
            return []

        for obj in objects:
            env = build_envelope(
                source,
                obj["source_record_id"],
                obj["payload"],
                ingestion_run_id,
                schema_version=cfg.get("schema_version", "1.0"),
            )
            y, m, d = utcnow()[:4], utcnow()[5:7], utcnow()[8:10]
            prefixes = cfg.get("gcs", {}).get("prefixes", {})
            env_path = (
                f"{prefixes.get('raw_envelope', 'raw-envelope/')}"
                f"{source}/{y}/{m}/{d}/{ingestion_run_id}/{env['envelopeId']}.json"
            )
            gcs_upload_json(bucket, env_path, env)
            envelopes.append(env)
            if obj.get("blob_updated", "") > max_updated:
                max_updated = obj["blob_updated"]

        # Advance watermark after successful HEARTBEAT/FULL
        if max_updated:
            try:
                import httpx
                from common import control_plane_url

                httpx.post(
                    f"{control_plane_url()}/v1/heartbeat",
                    json={"source": source, "watermark": max_updated, "healthy": True},
                    timeout=15.0,
                )
            except Exception as e:
                logger.warning("watermark update failed: %s", e)

        report_stage(
            pipeline_run_id,
            "INGEST",
            "COMPLETED",
            artifact_ids=[e["envelopeId"] for e in envelopes],
            metrics={"ingested": len(envelopes)},
            # checkpoint stored by control plane if supported via metrics only here
        )
        logger.info("batch ingest mode=%s count=%s source=%s", mode, len(envelopes), source)
        return envelopes
    except Exception as e:
        report_stage(pipeline_run_id, "INGEST", "FAILED", error=str(e))
        raise


if __name__ == "__main__":
    env = run_ingest(
        pipeline_run_id="pipe-demo",
        ingestion_run_id="run-demo-001",
        source_system="mobile_app",
        source_record_id="mob-001",
        payload={
            "user_id": "cust-4421",
            "message": "App crashes every time I try to checkout on Android",
            "os": "Android 14",
            "product": "Checkout",
            "ts": "2026-09-18T09:58:00Z",
        },
    )
    print(json.dumps(env, indent=2))
