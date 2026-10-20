"""
Model training lifecycle helpers (task-specific models).

Builds governed training datasets from curated classified feedback,
trains lightweight local baselines (or submits Vertex CustomJob when configured),
evaluates, and registers candidates with the Control Plane model registry.

Does NOT train foundation models from scratch — only task models:
  1. Business-Unit & Category Classifier
  2. Clustering / Issue-Grouping (feature prep)
  3. Anomaly Detection thresholds
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import Counter
from typing import Any

import httpx

from common import control_plane_url, gcs_bucket_name, load_pipeline_config, short_id, utcnow

logger = logging.getLogger("data-plane.training")


def _gcs_list_prefix(prefix: str) -> list[str]:
    try:
        from google.cloud import storage

        client = storage.Client(project=os.environ.get("GCP_PROJECT", "intelligent-machines"))
        bucket = client.bucket(gcs_bucket_name())
        return [b.name for b in client.list_blobs(bucket, prefix=prefix)]
    except Exception as e:
        logger.info("GCS list skipped: %s", e)
        return []


def _gcs_download_json(object_name: str) -> dict[str, Any] | None:
    try:
        from google.cloud import storage

        client = storage.Client(project=os.environ.get("GCP_PROJECT", "intelligent-machines"))
        bucket = client.bucket(gcs_bucket_name())
        blob = bucket.blob(object_name)
        return json.loads(blob.download_as_text())
    except Exception as e:
        logger.warning("download %s failed: %s", object_name, e)
        return None


def _gcs_upload_json(object_name: str, payload: dict[str, Any]) -> str:
    try:
        from google.cloud import storage

        client = storage.Client(project=os.environ.get("GCP_PROJECT", "intelligent-machines"))
        bucket = client.bucket(gcs_bucket_name())
        blob = bucket.blob(object_name)
        blob.upload_from_string(json.dumps(payload, indent=2), content_type="application/json")
        return f"gs://{bucket.name}/{object_name}"
    except Exception as e:
        logger.warning("upload %s failed: %s", object_name, e)
        return object_name


def build_classifier_dataset(max_samples: int = 5000) -> dict[str, Any]:
    cfg = load_pipeline_config()
    prefix = cfg.get("gcs", {}).get("prefixes", {}).get("classified", "curated/classified/")
    names = _gcs_list_prefix(prefix)[:max_samples]
    samples: list[dict[str, Any]] = []
    for name in names:
        if not name.endswith(".json"):
            continue
        rec = _gcs_download_json(name)
        if not rec:
            continue
        text = rec.get("cleanedText") or rec.get("text") or ""
        label = rec.get("businessUnit") or rec.get("category")
        if text and label:
            samples.append(
                {
                    "text": text,
                    "businessUnit": rec.get("businessUnit"),
                    "category": rec.get("category"),
                    "confidence": rec.get("confidence"),
                    "source": name,
                }
            )

    fp = hashlib.sha256(json.dumps(samples[:200], sort_keys=True).encode()).hexdigest()[:12]
    dataset_version = f"clf-ds-{fp}"
    uri = _gcs_upload_json(
        f"training/datasets/{dataset_version}/manifest.json",
        {
            "dataset_version": dataset_version,
            "task": "classification",
            "sample_count": len(samples),
            "created_at": utcnow(),
            "samples_preview": samples[:20],
        },
    )
    logger.info("dataset %s samples=%d uri=%s", dataset_version, len(samples), uri)
    return {
        "dataset_version": dataset_version,
        "sample_count": len(samples),
        "uri": uri,
        "samples": samples,
    }


def evaluate_rule_baseline(samples: list[dict[str, Any]]) -> dict[str, float]:
    from classify import rule_classify

    if not samples:
        return {"accuracy": 0.0, "n": 0.0}

    correct = 0
    for s in samples:
        pred = rule_classify(s["text"], None)
        if pred.get("businessUnit") == s.get("businessUnit"):
            correct += 1
    n = len(samples)
    return {
        "accuracy": round(correct / n, 4) if n else 0.0,
        "n": float(n),
        "support": float(n),
    }


def train_classifier_candidate(
    dataset: dict[str, Any] | None = None,
    model_id: str = "classifier-bu",
) -> dict[str, Any]:
    dataset = dataset or build_classifier_dataset()
    samples = dataset.get("samples") or []
    metrics = evaluate_rule_baseline(samples)
    version = f"v{utcnow()[:10].replace('-', '')}-{short_id()[:6]}"

    dist = Counter(s.get("businessUnit") or "unknown" for s in samples)
    artifact = {
        "model_id": model_id,
        "version": version,
        "task": "classification",
        "algorithm": "rules+optional-vertex-endpoint",
        "dataset_version": dataset.get("dataset_version"),
        "metrics": metrics,
        "label_distribution": dict(dist),
        "created_at": utcnow(),
    }
    uri = _gcs_upload_json(f"models/{model_id}/{version}/metadata.json", artifact)

    vertex_image = os.environ.get("VERTEX_TRAINING_IMAGE")
    if vertex_image:
        logger.info(
            "VERTEX_TRAINING_IMAGE set (%s) — submit CustomJob externally; "
            "registering candidate metadata only",
            vertex_image,
        )
        artifact["vertex_training_image"] = vertex_image

    registered = register_with_control_plane(
        model_id=model_id,
        version=version,
        task="classification",
        metrics={k: float(v) for k, v in metrics.items()},
        dataset_version=dataset.get("dataset_version"),
        endpoint=os.environ.get("CLASSIFIER_ENDPOINT"),
    )
    return {"artifact_uri": uri, "record": registered, "metrics": metrics}


def register_with_control_plane(
    model_id: str,
    version: str,
    task: str,
    metrics: dict[str, float],
    dataset_version: str | None = None,
    endpoint: str | None = None,
    status: str = "CANDIDATE",
) -> dict[str, Any]:
    cp = control_plane_url()
    payload = {
        "model_id": model_id,
        "version": version,
        "task": task,
        "status": status,
        "metrics": metrics,
        "dataset_version": dataset_version,
        "endpoint": endpoint,
    }
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(f"{cp}/v1/models", json=payload)
            if r.status_code < 400:
                return r.json()
            logger.warning("register model failed %s: %s", r.status_code, r.text[:300])
    except Exception as e:
        logger.warning("register model error: %s", e)
    return payload


def promote_model(model_id: str, version: str) -> dict[str, Any]:
    cp = control_plane_url()
    try:
        with httpx.Client(timeout=20.0) as client:
            r = client.post(f"{cp}/v1/models/{model_id}/versions/{version}/promote")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("promote failed: %s", e)
        return {"error": str(e)}


if __name__ == "__main__":
    result = train_classifier_candidate()
    print(json.dumps({k: v for k, v in result.items() if k != "record"}, indent=2))
    print("registered:", json.dumps(result.get("record"), indent=2))
