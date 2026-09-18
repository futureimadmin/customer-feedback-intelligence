# Control Plane runtime — how modes launch work

## Architecture (after gap fill)

```
Cloud Scheduler (*/5 * * * *)
    → Pub/Sub topic cfi-heartbeat-tick
    → heartbeat_worker (pull sub cfi-heartbeat-tick-sub)
    → POST /v1/scheduler/heartbeat-tick
    → create PipelineRun mode=HEARTBEAT in Firestore
    → publish {stage:INGEST} to cfi-stage-dispatch

POST /v1/runs {"mode":"INITIAL_FULL"|"ON_DEMAND_FULL"|"HEARTBEAT"|"TARGETED_REPLAY"}
    → Firestore pipeline_runs
    → Pub/Sub cfi-stage-dispatch

stage_worker (pull sub cfi-stage-dispatch-sub)
    → INGEST (mode-aware batch from gs://…/landing/{source}/)
    → NORMALIZE → FEATURES → CLASSIFY → ANALYTICS → AGENTIC_RAG → JiraTool
    → reports each stage to Control Plane
```

## Mode behavior (ingest)

| Mode | Watermark | Landing scan |
|------|-----------|--------------|
| `INITIAL_FULL` | ignored | All objects under `landing/{source}/` (cap FULL_INGEST_LIMIT) |
| `ON_DEMAND_FULL` | ignored | Same as full |
| `HEARTBEAT` | used | Only objects with `updated > watermark` |
| `TARGETED_REPLAY` | optional | From watermark if set |

After success, HEARTBEAT advances watermark via `POST /v1/heartbeat`.

## How to launch

### Heartbeat (incremental)

Automatic every 5 minutes after `terraform apply`, or:

```bash
curl -X POST http://localhost:8080/v1/scheduler/heartbeat-tick
# or
HEARTBEAT_WORKER_MODE=once python apps/control-plane/heartbeat_worker.py
```

### Full historical load

```bash
curl -X POST http://localhost:8080/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"mode":"INITIAL_FULL","source_filter":"mobile_app"}'
```

### On-demand full reprocess

```bash
curl -X POST http://localhost:8080/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"mode":"ON_DEMAND_FULL","source_filter":"mobile_app"}'
```

### Local demo without Pub/Sub

```bash
# terminal 1
cd apps/control-plane && uvicorn main:app --port 8080
# terminal 2
cd apps/jira-tool && uvicorn main:app --port 8081
# terminal 3
cd apps/data-plane
export CONTROL_PLANE_URL=http://localhost:8080
export STAGE_WORKER_MODE=demo
python stage_worker.py
```

## Landing zone

Place source JSON files in:

```text
gs://customer-feedback/landing/{source_system}/{record_id}.json
```

Stage worker lists this prefix for batch ingest.

## Components checklist

| Component | Path |
|-----------|------|
| Control Plane API | `apps/control-plane/main.py` |
| Firestore store | `apps/control-plane/state.py` |
| Heartbeat worker | `apps/control-plane/heartbeat_worker.py` |
| Stage worker | `apps/data-plane/stage_worker.py` |
| Mode-aware ingest | `apps/data-plane/ingest.py` (`run_ingest_batch`) |
| Terraform Firestore | `terraform/firestore.tf` |
| Terraform Pub/Sub + Scheduler | `terraform/scheduler.tf`, `pubsub_subscriptions.tf` |
