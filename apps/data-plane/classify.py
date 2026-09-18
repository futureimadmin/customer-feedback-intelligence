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
        # Expect custom response shape from trained model
        pred = prediction.predictions[0]
        return {
            "businessUnit": pred.get("businessUnit"),
            "category": pred.get("category"),
            "subCategory": pred.get("subCategory"),
            "suggestedSeverity": pred.get("suggestedSeverity", "Medium"),
            "confidence": float(pred.get("confidence", 0.5)),
            "modelVersion": pred.get("modelVersion", "classifier-bu-endpoint"),
            "probabilities": pred.get("probabilities", {}),
        }
    except Exception as e:
        logger.warning("Vertex classifier failed: %s", e)
        return None


def run_classify(pipeline_run_id: str, feature_artifact: dict[str, Any]) -> dict[str, Any]:
    cfg = load_pipeline_config()
    bucket = gcs_bucket_name(cfg)
    prefix = cfg.get("gcs", {}).get("prefixes", {}).get("classified", "curated/classified/")
    threshold = float(cfg.get("classification", {}).get("confidence_threshold", 0.75))

    report_stage(pipeline_run_id, "CLASSIFY", "RUNNING")
    try:
        text = feature_artifact.get("cleanedText") or ""
        features = feature_artifact.get("features") or {}
        result = vertex_classify(text, features) or rule_classify(text, feature_artifact.get("product"))

        confidence = float(result["confidence"])
        review_required = confidence < threshold

        record = {
            "classifiedId": f"cls-{short_id()}",
            "parentFeatureId": feature_artifact.get("artifactId"),
            "businessUnit": result["businessUnit"],
            "category": result["category"],
            "subCategory": result.get("subCategory"),
            "suggestedSeverity": result.get("suggestedSeverity"),
            "confidence": confidence,
            "modelVersion": result["modelVersion"],
            "probabilities": result.get("probabilities", {}),
            "classifiedAt": utcnow(),
            "pipelineVersion": cfg.get("pipeline_version", "1.0.0"),
            "reviewRequired": review_required,
            "anomaly": feature_artifact.get("anomaly"),
            "cluster": feature_artifact.get("cluster"),
            "product": feature_artifact.get("product"),
            "channel": feature_artifact.get("channel"),
            "cleanedText": text,
        }

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
        return record
    except Exception as e:
        report_stage(pipeline_run_id, "CLASSIFY", "FAILED", error=str(e))
        raise


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
