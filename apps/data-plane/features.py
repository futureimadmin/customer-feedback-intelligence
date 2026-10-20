"""
Stage 3 — Feature engineering, embeddings, anomaly score, cluster assignment.

Produces FeatureArtifact. Embedding calls Vertex AI when configured; otherwise
a deterministic local stub vector is used for offline/dev.

Hooks for Model 2 (clustering) and Model 3 (anomaly detection) live here so
classification and RAG can consume the same artifact.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
from typing import Any

from common import gcs_bucket_name, gcs_upload_json, load_pipeline_config, report_stage, short_id, utcnow

logger = logging.getLogger("data-plane.features")

NEGATIVE_WORDS = {"crash", "fail", "broken", "error", "unable", "slow", "bug", "issue"}
URGENCY_WORDS = {"crash", "every time", "critical", "urgent", "down", "outage"}


def _simple_sentiment(text: str) -> dict[str, Any]:
    lower = text.lower()
    hits = sum(1 for w in NEGATIVE_WORDS if w in lower)
    if hits >= 2:
        return {"label": "negative", "score": min(0.5 + 0.15 * hits, 0.98)}
    if hits == 1:
        return {"label": "negative", "score": 0.65}
    return {"label": "neutral", "score": 0.55}


def _simple_intent(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ("crash", "bug", "error", "exception")):
        return "bug_report"
    if any(w in lower for w in ("slow", "latency", "performance")):
        return "performance"
    if any(w in lower for w in ("bill", "charge", "payment", "refund")):
        return "billing"
    return "general_feedback"


def _stub_embedding(text: str, dim: int = 64) -> list[float]:
    """Deterministic pseudo-embedding for local/dev without Vertex."""
    h = hashlib.sha256(text.encode()).digest()
    vals = []
    for i in range(dim):
        b = h[i % len(h)]
        vals.append((b / 255.0) * 2 - 1)
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


def embed_text(text: str, model_name: str) -> tuple[list[float], str]:
    """Try Vertex AI text embedding; fall back to stub."""
    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    location = os.environ.get("GCP_REGION", "us-central1")
    try:
        import vertexai
        from vertexai.language_models import TextEmbeddingModel

        vertexai.init(project=project, location=location)
        model = TextEmbeddingModel.from_pretrained(model_name)
        emb = model.get_embeddings([text])[0].values
        return list(emb), model_name
    except Exception as e:
        logger.info("Vertex embedding unavailable, using stub: %s", e)
        return _stub_embedding(text), "stub-embedding"


def _anomaly_score(text: str, window_stats: dict[str, float] | None) -> dict[str, Any]:
    lower = text.lower()
    urgency = sum(1 for w in URGENCY_WORDS if w in lower)
    base = min(0.3 + 0.2 * urgency, 0.95)
    if window_stats:
        similar = float(window_stats.get("similarCount1h") or 0)
        baseline = float(window_stats.get("baseline1h") or 1)
        if baseline > 0 and similar / baseline > 5:
            base = min(base + 0.25, 0.99)
    return {
        "isAnomaly": base >= 0.8,
        "anomalyScore": round(base, 3),
        "reasons": ["urgency_keywords"] if urgency else [],
    }


def _cluster_assign(text: str, product: str | None) -> dict[str, Any]:
    lower = text.lower()
    if "checkout" in lower and "crash" in lower:
        cid = "clu-checkout-crash"
    elif "payment" in lower or "billing" in lower:
        cid = "clu-payments"
    elif "login" in lower or "auth" in lower:
        cid = "clu-auth"
    else:
        cid = f"clu-{hashlib.md5((product or 'gen').encode()).hexdigest()[:8]}"
    return {"clusterId": cid, "method": "rule-v1"}


def build_feature_artifact(
    normalized: dict[str, Any],
    window_stats: dict[str, float] | None = None,
) -> dict[str, Any]:
    cfg = load_pipeline_config()
    text = normalized.get("cleanedText") or normalized.get("text") or ""
    model_name = cfg.get("models", {}).get("embedding_model", "text-embedding-004")
    emb, emb_model = embed_text(text, model_name)
    return {
        "artifactId": f"feat-{short_id()}",
        "parentNormalizedId": normalized.get("artifactId"),
        "embedding": emb,
        "embeddingModel": emb_model,
        "features": {
            "sentiment": _simple_sentiment(text),
            "intent": _simple_intent(text),
            "tokenCount": len(text.split()),
        },
        "anomaly": _anomaly_score(text, window_stats),
        "cluster": _cluster_assign(text, normalized.get("product")),
        "product": normalized.get("product"),
        "channel": normalized.get("channel"),
        "cleanedText": text,
        "createdAt": utcnow(),
    }


def run_features(
    pipeline_run_id: str,
    normalized: dict[str, Any],
    window_stats: dict[str, float] | None = None,
) -> dict[str, Any]:
    cfg = load_pipeline_config()
    bucket = gcs_bucket_name()
    prefix = cfg.get("gcs", {}).get("prefixes", {}).get("features", "curated/features/")

    report_stage(pipeline_run_id, "FEATURES", "RUNNING")
    try:
        artifact = build_feature_artifact(normalized, window_stats)
        path = f"{prefix}{artifact['artifactId']}.json"
        uri = gcs_upload_json(bucket, path, artifact)
        # Phase 3: stream embedding into Vertex Vector Search (non-fatal)
        try:
            from vector_upsert import upsert_feature_embedding

            upsert_result = upsert_feature_embedding(artifact)
            artifact["vectorUpsert"] = upsert_result
            if upsert_result.get("status") == "upserted":
                logger.info("vector upsert ok %s", upsert_result.get("datapoint_id"))
        except Exception as ve:
            logger.warning("vector upsert skipped: %s", ve)
            artifact["vectorUpsert"] = {"status": "error", "error": str(ve)}

        report_stage(
            pipeline_run_id,
            "FEATURES",
            "COMPLETED",
            artifact_ids=[artifact["artifactId"]],
            parent_artifact_ids=[normalized.get("artifactId", "")],
            metrics={
                "features": 1,
                "vector_upserted": 1 if artifact.get("vectorUpsert", {}).get("status") == "upserted" else 0,
            },
        )
        logger.info(
            "features %s anomaly=%s cluster=%s -> %s",
            artifact["artifactId"],
            artifact["anomaly"].get("isAnomaly"),
            artifact["cluster"].get("clusterId"),
            uri,
        )
        return artifact
    except Exception as e:
        report_stage(pipeline_run_id, "FEATURES", "FAILED", error=str(e))
        raise


if __name__ == "__main__":
    import json

    norm = {
        "artifactId": "norm-demo",
        "cleanedText": "App crashes every time I try to checkout on Android",
        "product": "Checkout",
        "channel": "mobile_app",
        "language": "en",
    }
    print(json.dumps(run_features("pipe-demo", norm, {"similarCount1h": 38, "baseline1h": 3.2}), indent=2)[:2000])
