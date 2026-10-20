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
        f"training/datasets/{dataset_version}.json",
        {"dataset_version": dataset_version, "samples": samples, "count": len(samples)},
    )
    dist = Counter(s.get("businessUnit") or "unknown" for s in samples)
    return {
        "dataset_version": dataset_version,
        "uri": uri,
        "count": len(samples),
        "label_distribution": dict(dist),
        "samples": samples,
    }


def evaluate_rule_baseline(samples: list[dict[str, Any]]) -> dict[str, float]:
    if not samples:
        return {"accuracy": 0.0, "support": 0}
    correct = 0
    for s in samples:
        text = (s.get("text") or "").lower()
        pred = "General Support"
        if "crash" in text:
            pred = "Mobile Engineering"
        elif "payment" in text or "bill" in text:
            pred = "Payments"
        elif "login" in text or "auth" in text:
            pred = "Identity"
        if pred == (s.get("businessUnit") or ""):
            correct += 1
    acc = correct / max(len(samples), 1)
    return {"accuracy": round(acc, 4), "support": len(samples)}


def submit_vertex_custom_job(
    model_id: str,
    version: str,
    dataset_uri: str | None,
    image_uri: str,
) -> dict[str, Any]:
    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    location = os.environ.get("GCP_REGION", "us-central1")
    try:
        from google.cloud import aiplatform

        aiplatform.init(project=project, location=location)
        job = aiplatform.CustomJob(
            display_name=f"{model_id}-{version}",
            worker_pool_specs=[
                {
                    "machine_spec": {"machine_type": "n1-standard-4"},
                    "replica_count": 1,
                    "container_spec": {
                        "image_uri": image_uri,
                        "args": [
                            "--dataset", dataset_uri or "",
                            "--model-id", model_id,
                            "--version", version,
                        ],
                    },
                }
            ],
        )
        job.submit()
        return {"status": "submitted", "resource_name": job.resource_name}
    except Exception as e:
        logger.warning("CustomJob submit failed: %s", e)
        return {"status": "error", "error": str(e)}


def train_classifier_candidate() -> dict[str, Any]:
    cfg = load_pipeline_config()
    max_samples = int(cfg.get("training", {}).get("max_dataset_samples", 5000))
    dataset = build_classifier_dataset(max_samples=max_samples)
    metrics = evaluate_rule_baseline(dataset.get("samples") or [])
    model_id = "classifier-bu"
    version = f"v{short_id()[:8]}"
    dist = dataset.get("label_distribution") or {}

    artifact = {
        "model_id": model_id,
        "version": version,
        "task": "classification",
        "metrics": metrics,
        "dataset_version": dataset.get("dataset_version"),
        "dataset_uri": dataset.get("uri"),
        "label_distribution": dist,
        "created_at": utcnow(),
    }
    uri = _gcs_upload_json(f"models/{model_id}/{version}/metadata.json", artifact)

    vertex_image = os.environ.get("VERTEX_TRAINING_IMAGE")
    if vertex_image:
        job_meta = submit_vertex_custom_job(
            model_id=model_id,
            version=version,
            dataset_uri=dataset.get("uri"),
            image_uri=vertex_image,
        )
        artifact["vertex_training_image"] = vertex_image
        artifact["vertex_custom_job"] = job_meta
        logger.info("CustomJob result: %s", job_meta.get("status"))

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
