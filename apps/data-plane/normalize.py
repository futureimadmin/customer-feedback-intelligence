"""
Stage 2 — Validation, normalization & PII handling.
Reads canonical envelope → writes curated/normalized/ NormalizedRecord.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from common import gcs_bucket_name, gcs_upload_json, load_pipeline_config, report_stage, short_id, utcnow

logger = logging.getLogger("data-plane.normalize")

PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{8,}\d")
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def redact_pii(text: str) -> tuple[str, dict[str, str]]:
    tokens: dict[str, str] = {}

    def _phone(m: re.Match) -> str:
        tok = f"[PHONE_{len(tokens)}]"
        tokens[tok] = m.group(0)
        return tok

    def _email(m: re.Match) -> str:
        tok = f"[EMAIL_{len(tokens)}]"
        tokens[tok] = m.group(0)
        return tok

    cleaned = PHONE_RE.sub(_phone, text)
    cleaned = EMAIL_RE.sub(_email, cleaned)
    return cleaned.strip(), tokens


def normalize_envelope(envelope: dict[str, Any], pipeline_version: str = "1.0.0") -> dict[str, Any]:
    payload = envelope.get("payload", {})
    raw_text = payload.get("rawText") or ""
    cleaned, pii_tokens = redact_pii(raw_text)
    content_hash = hashlib.sha256(cleaned.encode()).hexdigest()
    return {
        "artifactId": f"norm-{short_id()}",
        "parentEnvelopeId": envelope.get("envelopeId"),
        "ingestionRunId": envelope.get("ingestionRunId"),
        "pipelineVersion": pipeline_version,
        "schemaVersion": envelope.get("schemaVersion", "1.0"),
        "normalizedAt": utcnow(),
        "language": "en",
        "cleanedText": cleaned,
        "contentHash": f"sha256:{content_hash}",
        "piiTokens": {k: "[REDACTED]" for k in pii_tokens},
        "sourceSystem": envelope.get("sourceSystem"),
        "customerId": payload.get("customerId"),
        "product": payload.get("product"),
        "platform": payload.get("platform"),
        "channel": payload.get("channel"),
        "originalTimestamp": payload.get("timestamp"),
    }


def run_normalize(pipeline_run_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
    cfg = load_pipeline_config()
    bucket = gcs_bucket_name(cfg)
    prefix = cfg.get("gcs", {}).get("prefixes", {}).get("normalized", "curated/normalized/")

    report_stage(pipeline_run_id, "NORMALIZE", "RUNNING")
    try:
        if not envelope.get("payload", {}).get("rawText") and not envelope.get("payload"):
            raise ValueError("invalid envelope: missing payload/rawText")

        record = normalize_envelope(envelope, pipeline_version=cfg.get("pipeline_version", "1.0.0"))
        path = f"{prefix}{record['artifactId']}.json"
        uri = gcs_upload_json(bucket, path, record)

        report_stage(
            pipeline_run_id,
            "NORMALIZE",
            "COMPLETED",
            artifact_ids=[record["artifactId"]],
            parent_artifact_ids=[envelope.get("envelopeId", "")],
            metrics={"normalized": 1},
        )
        logger.info("normalized %s -> %s", record["artifactId"], uri)
        return record
    except Exception as e:
        report_stage(pipeline_run_id, "NORMALIZE", "FAILED", error=str(e))
        raise


if __name__ == "__main__":
    import json

    sample = {
        "envelopeId": "e7f3a2b1",
        "ingestionRunId": "run-20260918-001",
        "sourceSystem": "mobile_app",
        "schemaVersion": "1.0",
        "payload": {
            "rawText": "Call me at +1-555-123-4567, crash on checkout",
            "customerId": "cust-4421",
            "product": "Checkout",
            "platform": "Android 14",
            "timestamp": "2026-09-18T09:58:00Z",
        },
    }
    print(json.dumps(run_normalize("pipe-demo", sample), indent=2))
