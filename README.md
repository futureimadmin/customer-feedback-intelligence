# Customer Feedback Intelligence

AI-powered platform that turns multichannel customer feedback and application logs into deduplicated, actionable Jira tickets on **Google Cloud Platform**.

**GitHub:** https://github.com/futureimadmin/customer-feedback-intelligence  
**GCP Project:** `intelligent-machines`  
**Primary region:** `us-central1`  
**Data lake bucket:** `customer-feedback`

## Architecture (summary)

| Layer | Role |
|-------|------|
| **Control Plane** (GKE) | Scheduling, state, retries, lineage, governance |
| **Data Plane** (GKE) | Ingestion, normalization, ML features, classification |
| **Vertex AI** | Embeddings, Vector Search, model serving, Gemini via Cortex API |
| **BigQuery** | Analytics branch (independent of Jira) |
| **Agentic RAG** | Grounded context → Gemini → **JiraTool** → Jira |

Kafka is **not** used on the Jira creation path. Idempotency is enforced at source fingerprint, pipeline checkpoints, `ticketDeduplicationKey`, Ticket Registry, and JiraTool.

### Three task models
1. **Business-Unit & Category Classifier**
2. **Clustering / Issue-Grouping**
3. **Anomaly Detection**

## Repository layout

```
├── terraform/           # GCP infrastructure (GCS, GKE, BQ, IAM, secrets)
├── apps/
│   ├── control-plane/   # Orchestrator, heartbeat, state API
│   ├── data-plane/      # Ingest, normalize, features, classify workers
│   └── jira-tool/       # Controlled Jira create/update service
├── ui/                  # React admin & review console
├── configs/             # Jira, pipeline, taxonomy (non-secret defaults)
├── docs/                # Architecture notes
└── scripts/             # Bootstrap helpers
```

## Prerequisites

- GCP project `intelligent-machines` with billing enabled
- `gcloud` CLI authenticated; Terraform ≥ 1.5
- Node.js 20+ (UI)
- Jira Cloud API token (stored in Secret Manager — never commit)

## Quick start

### 1. Infrastructure

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit if needed (project_id, jira placeholders)
terraform init
terraform plan
terraform apply
```

Creates GCS bucket `customer-feedback` with folder prefixes (`raw/`, `curated/`, …), GKE, BigQuery, IAM, Secret Manager placeholders.

### 2. Jira secrets

```bash
gcloud secrets versions add jira-base-url --data-file=- <<< "https://your-domain.atlassian.net"
gcloud secrets versions add jira-email --data-file=- <<< "automation@example.com"
gcloud secrets versions add jira-api-token --data-file=- <<< "YOUR_API_TOKEN"
```

Non-secret defaults: `configs/jira.yaml`.

### 3. UI

```bash
cd ui && cp .env.example .env.local && npm install && npm run dev
```

## GCS layout

```
gs://customer-feedback/
  raw/{source}/{yyyy}/{mm}/{dd}/{ingestionRunId}/
  raw-envelope/
  curated/normalized/
  curated/features/
  curated/classified/
  quarantine/
```

## License

Proprietary — Intelligent Machines.
