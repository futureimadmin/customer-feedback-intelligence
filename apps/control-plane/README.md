# Control Plane (Firestore)

Operational brain of Customer Feedback Intelligence.

**Firestore database:** `customer-feedback-intelligence`  
**GCP project:** `intelligent-machines`  
**Region:** `us-central1`

## Capabilities (implemented)

| Capability | Implementation |
|------------|----------------|
| Metadata & State Store | Firestore collections: `pipeline_runs`, `ticket_candidates`, `lineage`, `idempotency_keys`, `model_versions`, `config`, `dlq`, `heartbeats`, `watermarks` |
| Heartbeat Monitor | `POST /v1/heartbeat` + watermarks; Cloud Scheduler → `cfi-heartbeat-tick` Pub/Sub; `POST /v1/scheduler/heartbeat-tick` |
| Ingestion Scheduler | `POST /v1/runs` modes: INITIAL_FULL, HEARTBEAT, ON_DEMAND_FULL, TARGETED_REPLAY |
| Pipeline Orchestrator | Stage dependency graph, checkpoint fields, Pub/Sub `cfi-stage-dispatch`, auto-complete |
| Failure / Retry | Exponential backoff, max retries, MANUAL_INTERVENTION, DLQ collection + `cfi-dlq` topic |
| Observability | JSON structured logs with `correlation_id`, `pipeline_run_id`, `stage` |
| Config Service | File ConfigMap + Firestore `config` collection (`GET/PUT /v1/config`) |
| Model governance | `POST/GET /v1/models`, promote to PRODUCTION |

Falls back to in-memory store if Firestore is unreachable (local dev).

## Run locally

```bash
export GCP_PROJECT=intelligent-machines
export FIRESTORE_DATABASE=customer-feedback-intelligence
export JIRA_TOOL_URL=http://localhost:8081
# Optional: gcloud auth application-default login
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

## Terraform

```bash
cd terraform && terraform apply
# Creates Firestore DB customer-feedback-intelligence, Pub/Sub topics, Scheduler job
```

## Key APIs

- `POST /v1/runs` — create & start run (dispatches INGEST)
- `POST /v1/runs/{id}/stages` — worker progress (enforces deps, DLQ on fail)
- `POST /v1/runs/{id}/retry` — backoff + reset failed stages
- `POST /v1/scheduler/heartbeat-tick` — Scheduler entrypoint
- `GET /v1/dlq` — dead-letter queue
- `PUT /v1/config/{key}` — versioned config in Firestore
- `POST /v1/models` / `.../promote` — model registry
