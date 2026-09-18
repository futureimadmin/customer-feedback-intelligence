"""
Data Plane — validation, normalization, PII handling.
Reads raw-envelope objects from gs://customer-feedback, writes curated/normalized/.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{8,}\d")
EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")


def fingerprint(source_system: str, source_record_id: str, payload: str) -> str:
    h = hashlib.sha256(f"{source_system}|{source_record_id}|{payload}".encode()).hexdigest()
    return f"sha256:{h}"


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
    return cleaned, tokens


def normalize_envelope(envelope: dict[str, Any], pipeline_version: str = "1.0.0") -> dict[str, Any]:
    payload = envelope.get("payload", {})
    raw_text = payload.get("rawText") or payload.get("message") or ""
    cleaned, pii_tokens = redact_pii(raw_text.strip())
    content_hash = hashlib.sha256(cleaned.encode()).hexdigest()
    return {
        "artifactId": f"norm-{uuid.uuid4().hex[:12]}",
        "parentEnvelopeId": envelope.get("envelopeId"),
        "ingestionRunId": envelope.get("ingestionRunId"),
        "pipelineVersion": pipeline_version,
        "schemaVersion": envelope.get("schemaVersion", "1.0"),
        "normalizedAt": datetime.now(timezone.utc).isoformat(),
        "language": "en",
        "cleanedText": cleaned,
        "contentHash": f"sha256:{content_hash}",
        "piiTokens": pii_tokens,
        "sourceSystem": envelope.get("sourceSystem"),
        "customerId": payload.get("customerId"),
        "product": payload.get("product"),
        "platform": payload.get("platform"),
        "originalTimestamp": payload.get("timestamp"),
    }


if __name__ == "__main__":
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
    print(json.dumps(normalize_envelope(sample), indent=2))
