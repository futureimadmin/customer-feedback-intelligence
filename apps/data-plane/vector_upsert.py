"""
Upsert feature embeddings into Vertex AI Vector Search (Matching Engine).

Called after FEATURES stage when VECTOR_INDEX_ENDPOINT and VECTOR_DEPLOYED_INDEX_ID
are set. Failures are logged and non-fatal so the pipeline continues.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("data-plane.vector_upsert")


def upsert_feature_embedding(feature: dict[str, Any]) -> dict[str, Any]:
    """
    Stream-update one datapoint into the deployed Vector Search index.
    Returns status dict; never raises to the pipeline.
    """
    endpoint_name = os.environ.get("VECTOR_INDEX_ENDPOINT")
    # Prefer index resource name for upsert; fall back to env
    index_name = os.environ.get("VECTOR_INDEX_NAME") or os.environ.get("VECTOR_INDEX_ID")
    if not endpoint_name and not index_name:
        return {"status": "skipped", "reason": "VECTOR_INDEX_ENDPOINT/VECTOR_INDEX_NAME not set"}

    embedding = feature.get("embedding")
    if not embedding or not isinstance(embedding, list):
        return {"status": "skipped", "reason": "no embedding on feature artifact"}

    datapoint_id = (
        feature.get("artifactId")
        or feature.get("parentNormalizedId")
        or f"feat-{hash(str(embedding[:8])) & 0xFFFFFFFF:x}"
    )
    restricts = []
    for key in ("product", "channel"):
        val = feature.get(key)
        if val:
            restricts.append({"namespace": key, "allow_list": [str(val)]})
    cluster_id = (feature.get("cluster") or {}).get("clusterId")
    if cluster_id:
        restricts.append({"namespace": "cluster", "allow_list": [str(cluster_id)]})

    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    location = os.environ.get("GCP_REGION", "us-central1")

    try:
        from google.cloud import aiplatform_v1

        # Index name: projects/.../locations/.../indexes/...
        if not index_name:
            # Derive is not always possible from endpoint alone; require explicit index
            return {
                "status": "skipped",
                "reason": "Set VECTOR_INDEX_NAME to the full index resource name for stream upsert",
            }

        client = aiplatform_v1.IndexServiceClient(
            client_options={"api_endpoint": f"{location}-aiplatform.googleapis.com"}
        )
        datapoint = aiplatform_v1.IndexDatapoint(
            datapoint_id=str(datapoint_id),
            feature_vector=[float(x) for x in embedding],
            restricts=[
                aiplatform_v1.IndexDatapoint.Restriction(
                    namespace=r["namespace"],
                    allow_list=r["allow_list"],
                )
                for r in restricts
            ]
            if restricts
            else None,
        )
        request = aiplatform_v1.UpsertDatapointsRequest(
            index=index_name,
            datapoints=[datapoint],
        )
        client.upsert_datapoints(request=request)
        logger.info("upserted datapoint %s to %s", datapoint_id, index_name)
        return {"status": "upserted", "datapoint_id": datapoint_id, "index": index_name}
    except Exception as e:
        logger.warning("vector upsert failed for %s: %s", datapoint_id, e)
        return {"status": "error", "error": str(e), "datapoint_id": datapoint_id}
