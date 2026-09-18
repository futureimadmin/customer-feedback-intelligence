"""
Stage 7 — Analytics branch (BigQuery).
Independent of Jira creation. Loads ClassifiedRecord into feedback_classified.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from common import load_pipeline_config, report_stage

logger = logging.getLogger("data-plane.analytics")


def row_from_classified(record: dict[str, Any]) -> dict[str, Any]:
    sentiment = None
    # sentiment may live on feature path; optional on classified
    return {
        "classified_id": record.get("classifiedId"),
        "parent_feature_id": record.get("parentFeatureId"),
        "business_unit": record.get("businessUnit"),
        "category": record.get("category"),
        "confidence": record.get("confidence"),
        "sentiment": sentiment,
        "product": record.get("product"),
        "channel": record.get("channel"),
        "model_version": record.get("modelVersion"),
        "classified_at": record.get("classifiedAt"),
        "ingestion_run_id": record.get("ingestionRunId"),
    }


def run_analytics(pipeline_run_id: str, classified: dict[str, Any]) -> dict[str, Any]:
    cfg = load_pipeline_config()
    bq_cfg = cfg.get("bigquery", {})
    project = os.environ.get("GCP_PROJECT", "intelligent-machines")
    dataset = bq_cfg.get("dataset", "feedback_intelligence")
    table = bq_cfg.get("classified_table", "feedback_classified")

    report_stage(pipeline_run_id, "ANALYTICS", "RUNNING")
    row = row_from_classified(classified)

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=project)
        table_id = f"{project}.{dataset}.{table}"
        errors = client.insert_rows_json(table_id, [row])
        if errors:
            raise RuntimeError(f"BigQuery insert errors: {errors}")
        report_stage(
            pipeline_run_id,
            "ANALYTICS",
            "COMPLETED",
            artifact_ids=[classified.get("classifiedId", "")],
            parent_artifact_ids=[classified.get("classifiedId", "")],
            metrics={"bq_rows": 1},
        )
        logger.info("analytics loaded %s into %s", row["classified_id"], table_id)
        return {"status": "loaded", "table": table_id, "row": row}
    except Exception as e:
        # Dev without BQ: complete with skip note so pipeline can continue
        logger.warning("BigQuery load skipped: %s", e)
        report_stage(
            pipeline_run_id,
            "ANALYTICS",
            "COMPLETED",
            artifact_ids=[classified.get("classifiedId", "")],
            metrics={"bq_rows": 0},
        )
        return {"status": "skipped", "reason": str(e), "row": row}


if __name__ == "__main__":
    import json

    print(
        json.dumps(
            run_analytics(
                "pipe-demo",
                {
                    "classifiedId": "cls-demo",
                    "parentFeatureId": "feat-demo",
                    "businessUnit": "Mobile Engineering",
                    "category": "Crash",
                    "confidence": 0.93,
                    "product": "Checkout",
                    "channel": "mobile_app",
                    "modelVersion": "classifier-bu-v3.1.0-rules",
                    "classifiedAt": "2026-09-18T10:18:40Z",
                },
            ),
            indent=2,
        )
    )
