# Customer Feedback Intelligence

AI-powered platform that turns multichannel customer feedback and application logs into deduplicated, actionable Jira tickets on **Google Cloud Platform**.

**GitHub:** https://github.com/futureimadmin/customer-feedback-intelligence  
**GCP Project:** `intelligent-machines`  
**Primary region:** `us-central1`  
**Data lake bucket:** `customer-feedback`  
**Release:** **1.3.0** — [CHANGELOG](CHANGELOG.md) · [Phase 3](docs/PHASE3.md)

## Architecture (summary)

| Layer | Role |
|-------|------|
| **Control Plane** (GKE) | Scheduling, state, retries, lineage, governance |
| **Data Plane** (GKE) | Ingestion, normalization, ML features, classification |
| **Vertex AI** | Embeddings, Vector Search, model serving, Gemini via Vertex AI (ADC) |
| **BigQuery** | Analytics branch (independent of Jira) |
| **Agentic RAG** | Grounded context → Gemini (ADC) → **JiraTool** → Jira |

Kafka is **not** used on the Jira creation path. Idempotency is enforced at source fingerprint, pipeline checkpoints, `ticketDeduplicationKey`, Ticket Registry, and JiraTool.

## Release highlights (1.3.0)

- Gemini via Vertex **ADC** (no Cortex)
- Real **JiraTool** REST + dry-run
- **Vector Search** Terraform + embedding upsert
- **Training** registry + optional CustomJob
- **Review workbench** API + UI
- **SLA** + **cost** summary endpoints

## Quick start

```bash
cd terraform && cp terraform.tfvars.example terraform.tfvars && terraform apply
# secrets: jira-base-url, jira-email, jira-api-token
cd ui && cp .env.example .env.local && npm install && npm run dev
```

## License

Proprietary — Intelligent Machines.
