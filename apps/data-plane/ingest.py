"""
Stage 1 — Source ingestion & canonical envelope.

Accepts heterogeneous feedback/log payloads, wraps them in a canonical envelope,
computes sourceFingerprint, writes to GCS raw/ and raw-envelope/.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any
from uuid import uuid4

from common import gcs_bucket_name, gcs_upload_json, load_pipeline_config, report_stage, short_id, utcnow

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
        day = utcnow()[:10].replace("-", "/")  # yyyy/mm/dd-ish simplified
        y, m, d = utcnow()[:4], utcnow()[5:7], utcnow()[8:10]

        raw_path = f"{prefixes.get('raw', 'raw/')}{source_system}/{y}/{m}/{d}/{ingestion_run_id}/{source_record_id}.json"
        env_path = f"{prefixes.get('raw_envelope', 'raw-envelope/')}{source_system}/{y}/{m}/{d}/{ingestion_run_id}/{envelope['envelopeId']}.json"

        # Verbatim payload + envelope
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


if __name__ == "__main__":
    import json

    env = run_ingest(
        pipeline_run_id="pipe-demo",
        ingestion_run_id="run-demo-001",
        source_system="mobile_app",
        source_record_id="mob-001",
        payload={
            "user_id": "cust-4421",
            "message": "App crashes every time I try to checkout on Android",
            "os": "Android 14",
            "app_ver": "5.2.1",
            "ts": "2026-09-18T09:58:00Z",
            "product": "Checkout",
        },
    )
    print(json.dumps(env, indent=2))
