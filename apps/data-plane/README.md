# Data Plane

Stage workers for Customer Feedback Intelligence. Each stage is a **separate module**; `pipeline.py` wires them end-to-end.

## Modules (one stage each)

| File | Stage | Responsibility |
|------|-------|----------------|
| `ingest.py` | 1 INGEST | Canonical envelope, fingerprint, write `raw/` + `raw-envelope/` |
| `normalize.py` | 2 NORMALIZE | Schema validation, PII redaction, `curated/normalized/` |
| `features.py` | 3 FEATURES | Sentiment/intent, embeddings, **Model 2 clustering**, **Model 3 anomaly** |
| `classify.py` | 6 CLASSIFY | **Model 1** BU/category classifier, review flag |
| `analytics.py` | 7 ANALYTICS | BigQuery load (`feedback_classified`) — independent of Jira |
| `agentic_rag.py` | 8–9 RAG + JIRA | Grounded context, ticket candidate, Control Plane → JiraTool |
| `common.py` | — | Config, GCS helpers, Control Plane stage reporting |
| `pipeline.py` | — | E2E runner |

Stages 4–5 (offline training / Vector Search index upsert) are batch jobs; hooks for embeddings and cluster IDs are in `features.py`.

## Local E2E

```bash
# Terminals: control-plane :8080, jira-tool :8081
cd apps/data-plane
pip install -r requirements.txt
export CONTROL_PLANE_URL=http://localhost:8080
python pipeline.py
```

Without GCP credentials, GCS/BQ/Vertex calls fall back to local stubs so the pipeline still runs.

## Control Plane reporting

Every `run_*` function calls `report_stage(pipeline_run_id, STAGE, RUNNING|COMPLETED|FAILED)` so the Control Plane state machine stays in sync.
