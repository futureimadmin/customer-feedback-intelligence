# Customer Feedback Intelligence — Low-Level Design v1.1

**Project:** intelligent-machines | **Region:** us-central1  
**Bucket:** customer-feedback | **Firestore DB:** customer-feedback-intelligence

Supersedes LLD v1.0 for orchestration: **target** execution is Vertex AI Pipelines; Pub/Sub `stage_worker` remains transitional.

Binary DOCX (with diagram): `Customer_Feedback_Intelligence_LLD_v1.1.docx` (see local artifacts / attach in release).

---

## 1. Purpose and scope

Details stage contracts, Control Plane ↔ Data Plane boundaries, sample I/O, three task models, Agentic RAG, ticket accuracy, and target-state Vertex MLOps launch.

- **In scope:** stage behaviors, Firestore collections, PipelineJob parameters, artifact schemas, JiraTool idempotency, modes.
- **Out of scope:** training foundation models from scratch; Kafka on Jira path; Dataflow.

---

## 2. Current vs target execution

| Aspect | Current (v1.0 runtime) | Target (v1.1) |
|--------|------------------------|---------------|
| Orchestrator | CP + Pub/Sub + stage_worker | CP submits Vertex PipelineJob |
| Stage code | Python modules in one process | KFP container components |
| State store | Firestore | Firestore + Vertex ML Metadata |
| Training | Partial / ad hoc | Vertex training pipelines + registry |

---

## 3. Control Plane LLD

### Responsibilities

- Create/track pipeline runs (modes, watermarks, correlationId) in Firestore.
- Resolve pipeline template version, model endpoints, taxonomy/prompt versions.
- **Target:** submit Vertex `PipelineJob`. **Transitional:** publish `cfi-stage-dispatch`.
- Retry/backoff, DLQ, manual intervention, config service, model governance.
- Register ticket candidates; dispatch to JiraTool (never call Jira create APIs directly from CP).

### Firestore collections

| Collection | Contents |
|------------|----------|
| `pipeline_runs` | State machine, stages, metrics, mode |
| `ticket_candidates` | Candidate lifecycle, dedup key, jira_key |
| `lineage` | artifact_id → parent_artifact_ids |
| `idempotency_keys` | `ticket:{dedupKey}`, stage keys |
| `model_versions` | model_id, version, task, status, endpoint |
| `config` / `heartbeats` / `watermarks` / `dlq` | Ops config and recovery |

### Target launch parameters (conceptual)

```json
{
  "pipeline_run_id": "pipe-abc",
  "mode": "HEARTBEAT",
  "source_filter": "mobile_app",
  "watermark": "2026-09-18T10:00:00Z",
  "classifier_endpoint": "projects/.../endpoints/...",
  "embedding_model": "text-embedding-004",
  "pipeline_version": "1.1.0"
}
```

On job SUCCEEDED/FAILED, CP poller/webhook updates stages and may start Agentic RAG + Jira for eligible classified artifacts.

### Mode semantics

- **INITIAL_FULL / ON_DEMAND_FULL:** watermark ignored; scan landing up to policy cap.
- **HEARTBEAT:** only objects with `updated > watermark`; skip submit if empty.
- **TARGETED_REPLAY:** `checkpoint_uri` / artifact ids.

---

## 4. Data Plane stages

### 4.1 INGEST

- **Input:** landing objects or single payload.
- **Output:** canonical envelope in `raw-envelope/`.
- `sourceFingerprint = sha256(sourceSystem|sourceRecordId|rawText)`.

### 4.2 NORMALIZE

- **Input:** envelope.
- **Output:** `curated/normalized` with `cleanedText`, `contentHash`, PII placeholders only.

### 4.3 FEATURES

- **Output:** sentiment, intent, embedding, cluster (Model 2), anomaly (Model 3).
- Embedding model version on artifact.

### 4.4 CLASSIFY (Model 1)

- **Output:** `businessUnit`, `category`, `confidence`, `modelVersion`, `reviewRequired`.
- If confidence < threshold → review queue; ticket path deferred.

**Sample:** `{ "businessUnit": "Mobile Engineering", "category": "Crash", "confidence": 0.93, "reviewRequired": false }`

### 4.5 INDEX

Upsert embedding + metadata (BU, product, channel, date) into Vertex AI Vector Search.

### 4.6 ANALYTICS

Load to BigQuery `feedback_intelligence.feedback_classified`. Independent of Jira.

### 4.7 AGENTIC RAG + ticket (post-pipeline service)

Not a Vertex training step. After eligible classified artifact:

1. Retrieve similar issues, open tickets, taxonomy.
2. Cortex/Gemini → structured `TicketCandidate`.
3. Gemini may call **JiraTool** (dedup, create/update).
4. Persist `jira_key` and evidence refs.

---

## 5. Three task models

| Model | Task | Notes |
|-------|------|-------|
| Model 1 | BU & category classifier | Labeled curated + outcomes; Vertex CustomJob; Endpoint |
| Model 2 | Clustering / issue group | Batch on embeddings; periodic rebuild |
| Model 3 | Anomaly detection | Volume/sentiment residuals; threshold in config |

Embedding model ≠ task models ≠ Gemini (Cortex).

---

## 6. Agentic RAG

- **Input:** classified record + feature refs.
- **Process:** retrieval plan → Vector Search → structured stores → rank → grounded package → Gemini ticket JSON → optional JiraTool.
- **Output:** `TicketCandidate` with summary, description, priority, labels, `deduplicationKey`, `evidenceRefs`, `promptVersion`.

**Accuracy vs feedback:** field faithfulness, taxonomy consistency, dedup precision/recall, composite TicketQuality; human labels feed Model 1 retraining.

---

## 7. Idempotency layers

1. Ingest: `sourceFingerprint`
2. Stages: `pipeline_run_id` + stage + artifact/checkpoint
3. Ticket: deterministic `ticketDeduplicationKey` + JiraTool search
4. Retries: state machine; safe re-entry

---

## 8. Sample end-to-end I/O

**Input:** `{ "message": "App crashes every time I try to checkout on Android", "os": "Android 14", "product": "Checkout" }`

**Classified:** Mobile Engineering / Crash / 0.93

**Ticket summary:** `Checkout crash — App crashes every time I try to checkout on Android`

**JiraTool:** `JIRA_CREATED` + `jira_key` (or `EXISTING_TICKET` on dedup hit)

---

## 9. Transitional note

Until PipelineJob cutover completes, `stage_worker` + Pub/Sub remain supported. Both paths must use the same stage names and artifact schemas so Firestore stays consistent.

---

## 10. References

- HLD v1.0 architecture template
- [ARCHITECTURE_CURRENT_VS_TARGET.md](./ARCHITECTURE_CURRENT_VS_TARGET.md)
- Repo: `futureimadmin/customer-feedback-intelligence`
