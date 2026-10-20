# Phase 3 — Release notes

## Features

1. **Embedding upsert** — After FEATURES, datapoints stream into Vertex Matching Engine (`VECTOR_INDEX_NAME`).
2. **CustomJob training** — `VERTEX_TRAINING_IMAGE` triggers Vertex AI CustomJob from `training.py`.
3. **Review workbench** — Low-confidence classifications enqueue to Control Plane; UI supports Approve/Reject.
4. **Cost controller** — Aggregate USD by service; optional daily budget gate.

## Environment

```bash
export VECTOR_INDEX_ENDPOINT=projects/.../locations/.../indexEndpoints/...
export VECTOR_DEPLOYED_INDEX_ID=cfi_feedback_v1
export VECTOR_INDEX_NAME=projects/.../locations/.../indexes/...   # required for upsert
export VERTEX_TRAINING_IMAGE=gcr.io/PROJECT/cfi-trainer:latest   # optional
export GEMINI_MODEL=gemini-2.0-flash-001
```

## Verify

```bash
# Control Plane
curl -s localhost:8080/v1/health
curl -s localhost:8080/v1/sla
curl -s localhost:8080/v1/cost/summary
curl -s localhost:8080/v1/review-queue

# Training candidate
cd apps/data-plane && python training.py
```

## Version
**1.3.0**
