# Customer Feedback Intelligence — Architecture Update

**Version 1.1** | September 2026  
**Project:** intelligent-machines | **Region:** us-central1 | **Bucket:** customer-feedback

Addendum to HLD v1.0. Binary DOCX with embedded diagram: see release artifacts / local `CFI_Architecture_Current_vs_Target_Vertex_MLOps.docx`.

---

## 3.1 Current State Architecture

As-implemented relative to the HLD. Logical flow is unchanged; runtime uses Pub/Sub + GKE workers.

### What runs today

- **Control Plane (GKE / FastAPI):** run lifecycle, Firestore state, heartbeat API, retry/DLQ, config, model governance records, Jira dispatch.
- **Launch path:** Cloud Scheduler → Pub/Sub `cfi-heartbeat-tick` → heartbeat worker → `POST /v1/runs` or `/v1/scheduler/heartbeat-tick` → Pub/Sub `cfi-stage-dispatch`.
- **Data Plane:** GKE `stage_worker` pulls Pub/Sub and executes Python stage modules (ingest → normalize → features → classify → analytics → agentic_rag) largely in-process after INGEST.
- **Storage:** GCS `landing/` / `raw/` / `curated/`; BigQuery analytics; JiraTool service.
- **Explicitly absent:** Kafka on the Jira path; Dataflow / Apache Beam.

### Coupling characteristics

- Loose at the **message** boundary (Pub/Sub stage dispatch).
- Tight at **execution**: one worker process chains stages and calls Control Plane HTTP for stage status.
- Control Plane does **not** yet submit Vertex `PipelineJob` for the online feedback→ticket path.

---

## 11.2 Target State Architecture

Vertex AI MLOps is the primary Data Plane executor. Control Plane remains the operational brain. Classification still precedes the analytics vs ticket branch. **No Kafka** on the Jira path. **No Dataflow**.

### Structural changes

| Concern | Current state | Target state |
|---------|---------------|--------------|
| Who runs stages? | GKE stage_worker (Pub/Sub pull) | Vertex AI Pipeline (KFP container steps) |
| CP → DP contract | Pub/Sub stage message | `PipelineJob` parameters + template version |
| DP → CP feedback | HTTP stage reports during run | Job completion webhook/poller + GCS artifact URIs |
| Model training | Ad hoc / partial | Vertex training pipelines + Model Registry |
| Jira / Agentic RAG | Inside stage chain / services | After classified artifact; services outside train path |
| Dataflow | Not used | Not used |

### Every step in the target state

#### Step 1 — Sources
Customer feedback (web, mobile, social, email, surveys, contact center) and application/system logs (4xx/5xx, exceptions, latency) land via source adapters with stable source record ids.

#### Step 2 — Landing zone
Objects under `gs://customer-feedback/landing/{source_system}/`. Drop zone for mode-aware ingest.

#### Step 3 — Immutable raw lake
Verbatim payloads under `raw/`, partitioned by source/date, tagged with `ingestionRunId`, `sourceReference`, `schemaVersion`. Never mutated.

#### Step 4 — Control Plane decision and launch
Control Plane (GKE):

- Accepts modes: `INITIAL_FULL`, `HEARTBEAT`, `ON_DEMAND_FULL`, `TARGETED_REPLAY`.
- Creates durable pipeline run in Firestore (`customer-feedback-intelligence`).
- Resolves pipeline template version, model endpoints, taxonomy/prompt versions.
- Submits **Vertex AI PipelineJob** with `parameter_values`: `pipeline_run_id`, `mode`, `source_filter`, `watermark`, model endpoint ids, `pipeline_version`.
- Owns retry, DLQ, and manual-intervention policy when Vertex reports failure.

Does **not** perform heavy transforms.

#### Step 5 — Vertex AI MLOps pipeline (Data Plane DAG)

Compiled KFP v2 template as serverless PipelineJob:

| Substep | Name | Behavior |
|---------|------|----------|
| 5a | Ingest | Mode-aware envelopes, fingerprints, raw-envelope writes |
| 5b | Normalize | Validate, PII redaction, `curated/normalized` |
| 5c | Features | Embeddings, cluster (Model 2), anomaly (Model 3) |
| 5d | Classify | Model 1 BU/category, confidence, `reviewRequired` |
| 5e | Index | Vertex AI Vector Search upsert |
| 5f | Analytics | BigQuery load (independent of Jira) |

Completion updates Firestore via poller/webhook.

#### Step 6 — Training pipelines (separate Vertex jobs)
Governed datasets from curated history + labels. Train/fine-tune:

- **Model 1** — Business-unit & category classifier  
- **Model 2** — Clustering / issue-grouping  
- **Model 3** — Anomaly detection  

Evaluate → Model Registry → promote PRODUCTION endpoints used by 5c–5d.

#### Step 7 — Classified boundary
Shared intelligence product; analytics and tickets fork without re-deriving ownership.

#### Step 8 — Analytics branch
BigQuery dashboards, KPIs, conversational analytics. Not blocked on Jira.

#### Step 9 — Agentic RAG
Retrieve similar feedback (Vector Search), taxonomy, tickets, policies; tool-constrained agent; ranked evidence package.

#### Step 10 — Cortex API + Gemini
Enterprise Gemini wrapper; schema-validated ticket JSON.

#### Step 11 — JiraTool
Direct Gemini tool call (**no Kafka**); deterministic `ticketDeduplicationKey`; create/update; final idempotency boundary.

#### Step 12 — Jira
Engineering system of record for authorized, deduplicated issues.

#### Step 13 — Outcomes and lineage
Persist `jira_key`, evidence, model/prompt versions to Control Plane and analytics.

### Modes (same template, different parameters)

- `INITIAL_FULL` / `ON_DEMAND_FULL` — watermark ignored  
- `HEARTBEAT` — watermark applied; skip empty runs  
- `TARGETED_REPLAY` — checkpoint/artifact parameters  

### Outside Vertex training path

- Control Plane state machine, human review, config APIs  
- JiraTool secrets and Jira mutations  
- Agentic RAG policy service (GKE/Cloud Run after classify)  

### Migration posture

1. Containerize stages as KFP components; CP submits PipelineJob for normalize→classify→analytics  
2. Move ingest + Vector Search upsert into template; retire Pub/Sub stage chaining for happy path  
3. Training pipelines + promotion gates + feedback-to-model loop  

**Dataflow is not required at any phase.**

---

## Summary

Control Plane decides *when/whether* and records business outcomes. Vertex MLOps runs a versioned serverless DAG for enrichment/ML. After classification, BigQuery analytics and Agentic RAG → Cortex → JiraTool → Jira remain independent branches.
