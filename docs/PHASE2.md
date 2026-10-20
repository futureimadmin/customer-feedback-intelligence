# Phase 2 — Implementation notes

## Delivered

1. **Vertex AI Vector Search (Terraform)**  
   - `terraform/vertex_vector_search.tf` creates index + endpoint + deployed index `cfi_feedback_v1`.  
   - Outputs `VECTOR_INDEX_ENDPOINT` and `VECTOR_DEPLOYED_INDEX_ID` for data-plane env.  
   - Toggle with `enable_vector_search` (default true).

2. **Training / model registry**  
   - `apps/data-plane/training.py` builds datasets from curated classified data, evaluates rule baseline, registers candidates via `POST /v1/models`.  
   - UI **Models** page lists registry and supports promote.

3. **Control Plane ops**  
   - `GET /v1/metrics/summary`, `GET /v1/sla`, `GET /v1/data-quality/recent`.  
   - SLA thresholds in `configs/pipeline.yaml` under `sla:`.

4. **Settings persistence**  
   - Load on mount from `/v1/config/{jira,gemini,pipeline}`; save via PUT.

5. **Live UI wiring**  
   - Dashboard pulls metrics/SLA; Pipeline runs pull `/v1/runs`.

## Operator steps

```bash
# Infra (Vector Search is slow on first apply)
cd terraform && terraform apply

# Export vector env for data-plane
export VECTOR_INDEX_ENDPOINT=$(terraform output -json vector_search | jq -r .index_endpoint_name)
export VECTOR_DEPLOYED_INDEX_ID=cfi_feedback_v1

# Register a classifier candidate
cd apps/data-plane && python training.py
```

## Still future

- Stream embeddings into Vector Search after FEATURES stage  
- Vertex CustomJob for real classifier training (`VERTEX_TRAINING_IMAGE`)  
- Cost controller / per-pipeline billing labels  
- Full human review workbench actions
