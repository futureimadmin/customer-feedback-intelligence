"""
Stage 6 — Classification (Model 1: Business-Unit & Category Classifier).

Combines rule/heuristic classifier with optional Vertex endpoint when
CLASSIFIER_ENDPOINT is set. Low confidence → reviewRequired=true.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from common import gcs_bucket_name, gcs_upload_json, load_pipeline_config, report_stage, short_id, utcnow

logger = logging.getLogger("data-plane.classify")

# Simple taxonomy rules for bootstrap / cold start
RULES = [
    ({"crash", "android", "checkout"}, "Mobile Engineering", "Crash", "Checkout Flow", "High"),
    ({"crash", "ios"}, "Mobile Engineering", "Crash", "iOS", "High"),
    ({"payment", "charged", "refund", "billing"}, "Payments", "Billing", None, "High"),
    ({"login", "auth", "password"}, "Identity", "Authentication", None, "Medium"),
    ({"slow", "latency", "timeout"}, "Platform SRE", "Performance", None, "Medium"),
]


def rule_classify(text: str, product: str | None) -> dict[str, Any]:
    lower = text.lower()
    best = None
    best_hits = 0
    for keywords, bu, cat, sub, sev in RULES:
        hits = sum(1 for k in keywords if k in lower)
        if hits > best_hits:
            best_hits = hits
            best = (bu, cat, sub, sev, hits / max(len(keywords), 1))
    if best and best_hits > 0:
        bu, cat, sub, sev, conf = best
        conf = min(0.55 + 0.2 * best_hits, 0.95)
        return {
            "businessUnit": bu,
            "category": cat,
            "subCategory": sub,
            "suggestedSeverity": sev,
            "confidence": round(conf, 3),
            "modelVersion": "classifier-bu-v3.1.0-rules",
            "probabilities": {bu: conf},
        }
    # Default
    return {
        "businessUnit": "General Support",
        "category": "Other",
        "subCategory": None,
        "suggestedSeverity": "Low",
        "confidence": 0.4,
        "modelVersion": "classifier-bu-v3.1.0-rules",
        "probabilities": {"General Support": 0.4},
    }


def vertex_classify(text: str, features: dict[str, Any]) -> dict[str, Any] | None:
    endpoint = os.environ.get("CLASSIFIER_ENDPOINT")
    if not endpoint:
        return None
    try:
        from google.cloud import aiplatform

        aiplatform.init(
            project=os.environ.get("GCP_PROJECT", "intelligent-machines"),
            location=os.environ.get("GCP_REGION", "us-central1"),
        )
        ep = aiplatform.Endpoint(endpoint)
        prediction = ep.predict(instances=[{"text": text, "features": features}])
        preds = prediction.predictions[0] if prediction.predictions else {}
        return {
            "businessUnit": preds.get("businessUnit") or preds.get("label"),
            "category": preds.get("category"),
            "subCategory": preds.get("subCategory"),
            "suggestedSeverity": preds.get("severity", "Medium"),
            "confidence": float(preds.get("confidence", 0.5)),
            "modelVersion": preds.get("modelVersion", "classifier-vertex"),
            "probabilities": preds.get("probabilities") or {},
        }
    except Exception as e:
        logger.warning("vertex classify failed: %s", e)
        return None


def run_classify(pipeline_run_id: str, feature_artifact: dict[str, Any]) -> dict[str, Any]:
    cfg = load_pipeline_config()
    threshold = float(cfg.get("classification", {}).get("confidence_threshold", 0.75))
    text = feature_artifact.get("cleanedText") or feature_artifact.get("text") or ""
    product = feature_artifact.get("product")

    try:
        result = vertex_classify(text, feature_artifact.get("features") or {}) or rule_classify(text, product)
        confidence = float(result.get("confidence") or 0)
        review_required = confidence < threshold

        record = {
            "classifiedId": f"cls-{short_id()}",
            "artifactId": f"cls-{short_id()}",
            "parentFeatureId": feature_artifact.get("artifactId"),
            "pipelineRunId": pipeline_run_id,
            "businessUnit": result.get("businessUnit"),
            "category": result.get("category"),
            "subCategory": result.get("subCategory"),
            "suggestedSeverity": result.get("suggestedSeverity"),
            "confidence": confidence,
            "reviewRequired": review_required,
            "modelVersion": result.get("modelVersion"),
            "probabilities": result.get("probabilities") or {},
            "product": feature_artifact.get("product"),
            "channel": feature_artifact.get("channel"),
            "cleanedText": text,
            "createdAt": utcnow(),
        }

        bucket = gcs_bucket_name()
        prefix = cfg.get("gcs", {}).get("prefixes", {}).get("classified", "curated/classified/")
        path = f"{prefix}{record['classifiedId']}.json"
        uri = gcs_upload_json(bucket, path, record)

        report_stage(
            pipeline_run_id,
            "CLASSIFY",
            "COMPLETED",
            artifact_ids=[record["classifiedId"]],
            parent_artifact_ids=[feature_artifact.get("artifactId", "")],
            metrics={"classified": 1, "review_queue": 1 if review_required else 0},
        )
        logger.info(
            "classified %s bu=%s conf=%.2f review=%s -> %s",
            record["classifiedId"],
            record["businessUnit"],
            confidence,
            review_required,
            uri,
        )
        enqueue_human_review(pipeline_run_id, record)
        return record
    except Exception as e:
        report_stage(pipeline_run_id, "CLASSIFY", "FAILED", error=str(e))
        raise


def enqueue_human_review(pipeline_run_id: str, classified: dict[str, Any]) -> None:
    """Push low-confidence classification to Control Plane review queue."""
    if not classified.get("reviewRequired"):
        return
    try:
        import httpx
        from common import control_plane_url

        with httpx.Client(timeout=15.0) as client:
            client.post(
                f"{control_plane_url()}/v1/review-queue",
                json={
                    "classified_id": classified.get("classifiedId") or classified.get("artifactId"),
                    "pipeline_run_id": pipeline_run_id,
                    "text": classified.get("cleanedText") or "",
                    "business_unit": classified.get("businessUnit"),
                    "category": classified.get("category"),
                    "confidence": classified.get("confidence"),
                    "reason": "Below confidence threshold",
                },
            )
    except Exception as e:
        logger.warning("enqueue review failed: %s", e)


if __name__ == "__main__":
    import json

    feat = {
        "artifactId": "feat-demo",
        "cleanedText": "App crashes every time I try to checkout on Android",
        "product": "Checkout",
        "channel": "mobile_app",
        "features": {"sentiment": {"label": "negative", "score": 0.9}, "intent": "bug_report"},
        "anomaly": {"isAnomaly": True, "anomalyScore": 0.91},
        "cluster": {"clusterId": "clu-checkout-crash"},
    }
    print(json.dumps(run_classify("pipe-demo", feat), indent=2))
