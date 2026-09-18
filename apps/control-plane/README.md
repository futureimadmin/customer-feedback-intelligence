# Control Plane

Operational brain of Customer Feedback Intelligence. Owns desired/execution state, scheduling, retries, lineage and config. Does **not** run heavy ML or GCS transforms — Data Plane workers do that and report stage status here.

## Run locally

```bash
cd apps/control-plane
pip install -r requirements.txt
export JIRA_TOOL_URL=http://localhost:8081
uvicorn main:app --reload --port 8080
```

## API surface

| Method | Path | Purpose |
|--------|------|--------|
| GET | `/healthz` | Liveness |
| POST | `/v1/heartbeat` | Source health / new-data signal |
| GET | `/v1/heartbeat` | List last-seen by source |
| POST | `/v1/runs` | Create + start pipeline run |
| GET | `/v1/runs` | List runs |
| GET | `/v1/runs/{id}` | Run detail + stages |
| POST | `/v1/runs/{id}/stages` | Worker stage progress |
| POST | `/v1/runs/{id}/retry` | Retry after FAILED |
| POST | `/v1/runs/{id}/resume` | Resume MANUAL_INTERVENTION |
| POST | `/v1/runs/{id}/reprocess` | Reprocess COMPLETED run |
| POST | `/v1/runs/{id}/ticket-candidates` | Register ticket candidate |
| POST | `/v1/jira/dispatch` | Call JiraTool |
| GET | `/v1/lineage/{artifact_id}` | Parent artifacts |
| GET | `/v1/config/pipeline` | Pipeline config view |

## State machine

```
CREATED → VALIDATING → RUNNING → COMPLETED
RUNNING → FAILED → RETRY_PENDING → RUNNING
FAILED → MANUAL_INTERVENTION → RESUMED → RUNNING
COMPLETED → REPROCESS_REQUESTED → RUNNING
```

## Production notes

- Replace in-memory `MetadataStore` with Cloud Spanner or Firestore.
- Heartbeat CronJob can POST `/v1/runs` with `mode=HEARTBEAT` on a schedule.
- Workload Identity SA: `cfi-control-plane@intelligent-machines.iam.gserviceaccount.com`.
