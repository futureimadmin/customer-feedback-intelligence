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
    # L2 normalize
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
        logger.info("Vertex embedding unavailable (%s); using stub", e)
        return _stub_embedding(text), f"stub:{model_name}"


def score_anomaly(features: dict[str, Any], window_stats: dict[str, float] | None = None) -> dict[str, Any]:
    """Model 3 — lightweight anomaly score (volume spike + negative sentiment)."""
    window_stats = window_stats or {}
    similar_1h = window_stats.get("similarCount1h", 0)
    baseline = window_stats.get("baseline1h", 3.0)
    sentiment = features.get("sentiment", {})
    score = 0.0
    anomaly_type = None
    evidence = []

    if baseline > 0 and similar_1h >= max(10, baseline * 5):
        score = max(score, min(0.5 + similar_1h / (baseline * 20), 0.99))
        anomaly_type = "volume_spike"
        evidence.append(f"{similar_1h} similar in 1h vs baseline {baseline}")

    if sentiment.get("label") == "negative" and sentiment.get("score", 0) > 0.85:
        score = max(score, 0.7)
        anomaly_type = anomaly_type or "sentiment_shift"
        evidence.append(f"strong negative sentiment {sentiment.get('score')}")

    return {
        "isAnomaly": score >= float(load_pipeline_config().get("models", {}).get("anomaly_score_threshold", 0.8)),
        "anomalyScore": round(score, 3),
        "anomalyType": anomaly_type,
        "supportingEvidence": "; ".join(evidence) if evidence else None,
        "modelVersion": "anomaly-v1.4.0-rules",
    }


def assign_cluster(text: str, product: str | None) -> dict[str, Any]:
    """Model 2 — coarse rule-based cluster id (replace with HDBSCAN batch job)."""
    lower = text.lower()
    product_key = (product or "unknown").lower().replace(" ", "-")
    if "checkout" in lower and "crash" in lower:
        cid = f"clu-{product_key}-crash"
        summary = f"{product or 'App'} crash reports"
    elif "payment" in lower or "billing" in lower:
        cid = f"clu-{product_key}-billing"
        summary = "Billing / payment issues"
    else:
        cid = f"clu-{product_key}-general"
        summary = f"{product or 'General'} feedback cluster"
    return {
        "clusterId": cid,
        "distanceToCentroid": 0.1,
        "summary": summary,
        "modelVersion": "cluster-issue-v2.0.1-rules",
    }


def build_feature_artifact(
    normalized: dict[str, Any],
    window_stats: dict[str, float] | None = None,
) -> dict[str, Any]:
    cfg = load_pipeline_config()
    text = normalized.get("cleanedText") or ""
    model_name = cfg.get("models", {}).get("embedding_model", "text-embedding-004")

    sentiment = _simple_sentiment(text)
    intent = _simple_intent(text)
    urgency = [w for w in URGENCY_WORDS if w in text.lower()]
    embedding, emb_model = embed_text(text, model_name)

    feat_body = {
        "sentiment": sentiment,
        "intent": intent,
        "entities": [w for w in ("checkout", "Android", "payment", "login") if w.lower() in text.lower()],
        "urgencyKeywords": urgency,
        "language": normalized.get("language", "en"),
    }
    anomaly = score_anomaly(feat_body, window_stats)
    cluster = assign_cluster(text, normalized.get("product"))

    return {
        "artifactId": f"feat-{short_id()}",
        "parentNormalizedId": normalized.get("artifactId"),
        "pipelineVersion": cfg.get("pipeline_version", "1.0.0"),
        "embeddingModel": emb_model,
        "embeddingDim": len(embedding),
        "embedding": embedding,
        "features": feat_body,
        "anomaly": anomaly,
        "cluster": cluster,
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
    bucket = gcs_bucket_name(cfg)
    prefix = cfg.get("gcs", {}).get("prefixes", {}).get("features", "curated/features/")

    report_stage(pipeline_run_id, "FEATURES", "RUNNING")
    try:
        artifact = build_feature_artifact(normalized, window_stats)
        path = f"{prefix}{artifact['artifactId']}.json"
        # Store embedding-heavy object; in prod split vector to Vector Search upsert
        uri = gcs_upload_json(bucket, path, artifact)
        report_stage(
            pipeline_run_id,
            "FEATURES",
            "COMPLETED",
            artifact_ids=[artifact["artifactId"]],
            parent_artifact_ids=[normalized.get("artifactId", "")],
            metrics={"features": 1},
        )
        logger.info("features %s anomaly=%s cluster=%s -> %s",
                    artifact["artifactId"],
                    artifact["anomaly"].get("isAnomaly"),
                    artifact["cluster"].get("clusterId"),
                    uri)
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
