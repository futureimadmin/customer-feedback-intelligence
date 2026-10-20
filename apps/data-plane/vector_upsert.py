"""Vertex AI Vector Search (Matching Engine) upsert helpers.

Called after FEATURES when VECTOR_INDEX_NAME (or VECTOR_INDEX_ID) is set.
Uses Application Default Credentials / Workload Identity.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _index_name() -> Optional[str]:
    # Prefer full resource name; fall back to ID + project/location
    name = os.environ.get("VECTOR_INDEX_NAME") or os.environ.get("VECTOR_INDEX_ID")
    if not name:
        return None
    if name.startswith("projects/"):
        return name
    project = os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "intelligent-machines"
    location = os.environ.get("VERTEX_LOCATION") or os.environ.get("GCP_REGION") or "us-central1"
    return f"projects/{project}/locations/{location}/indexes/{name}"


def upsert_feature_embedding(
    datapoint_id: str,
    embedding: List[float],
    restricts: Optional[List[Dict[str, Any]]] = None,
    crowding_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a single embedding into the Vector Search index.

    Returns a status dict; never raises to the pipeline (best-effort).
    """
    index_name = _index_name()
    if not index_name:
        logger.debug("VECTOR_INDEX_NAME not set — skipping vector upsert")
        return {"status": "skipped", "reason": "VECTOR_INDEX_NAME unset"}

    if not embedding:
        return {"status": "skipped", "reason": "empty embedding"}

    try:
        from google.cloud import aiplatform_v1
        from google.cloud.aiplatform_v1.types import index as index_types

        client = aiplatform_v1.IndexServiceClient()
        datapoint = index_types.IndexDatapoint(
            datapoint_id=datapoint_id,
            feature_vector=list(embedding),
        )
        if restricts:
            datapoint.restricts = [
                index_types.IndexDatapoint.Restriction(
                    namespace=r.get("namespace", ""),
                    allow_list=r.get("allow_list") or [],
                    deny_list=r.get("deny_list") or [],
                )
                for r in restricts
            ]
        if crowding_tag:
            datapoint.crowding_tag = index_types.IndexDatapoint.CrowdingTag(
                crowding_attribute=crowding_tag
            )

        request = aiplatform_v1.UpsertDatapointsRequest(
            index=index_name,
            datapoints=[datapoint],
        )
        response = client.upsert_datapoints(request=request)
        logger.info("Upserted datapoint %s into %s", datapoint_id, index_name)
        return {
            "status": "ok",
            "datapoint_id": datapoint_id,
            "index": index_name,
            "response": str(response)[:200],
        }
    except Exception as exc:  # noqa: BLE001 — best-effort path
        logger.warning("Vector upsert failed for %s: %s", datapoint_id, exc)
        return {"status": "error", "datapoint_id": datapoint_id, "error": str(exc)}


def upsert_from_feature_artifact(artifact: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience: extract embedding + id from a FEATURES stage artifact."""
    fid = artifact.get("featureId") or artifact.get("sourceFingerprint") or artifact.get("id")
    emb = artifact.get("embedding") or artifact.get("featureVector")
    if not fid or not emb:
        return {"status": "skipped", "reason": "missing featureId or embedding"}
    restricts = None
    if artifact.get("businessUnit"):
        restricts = [{"namespace": "business_unit", "allow_list": [str(artifact["businessUnit"])]}]
    return upsert_feature_embedding(
        datapoint_id=str(fid),
        embedding=list(emb),
        restricts=restricts,
        crowding_tag=artifact.get("category"),
    )
