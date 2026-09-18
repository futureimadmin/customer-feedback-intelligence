# Control Plane (Firestore + workers)

**Firestore DB:** `customer-feedback-intelligence`  
**Project:** `intelligent-machines`

## Processes

| Process | Entry | Role |
|---------|-------|------|
| API | `main.py` (uvicorn) | Runs, stages, retry, DLQ, config, models, Jira dispatch |
| Heartbeat worker | `heartbeat_worker.py` | Pulls `cfi-heartbeat-tick-sub` → starts HEARTBEAT runs |
| Stage worker | `apps/data-plane/stage_worker.py` | Pulls `cfi-stage-dispatch-sub` → runs full stage chain |

## Launch modes

```bash
# Incremental (also every 5 min via Cloud Scheduler)
curl -X POST localhost:8080/v1/scheduler/heartbeat-tick

# Full historical
curl -X POST localhost:8080/v1/runs -H 'Content-Type: application/json' \
  -d '{"mode":"INITIAL_FULL","source_filter":"mobile_app"}'

# On-demand full
curl -X POST localhost:8080/v1/runs -H 'Content-Type: application/json' \
  -d '{"mode":"ON_DEMAND_FULL","source_filter":"mobile_app"}'
```

See [docs/CONTROL_PLANE_RUNTIME.md](../../docs/CONTROL_PLANE_RUNTIME.md) for the full sequence.

## Local

```bash
export GCP_PROJECT=intelligent-machines
export FIRESTORE_DATABASE=customer-feedback-intelligence
export JIRA_TOOL_URL=http://localhost:8081
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```
