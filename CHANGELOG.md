# Changelog

## 1.3.0 — Phase 3 release (2026-10-20)

### Added
- **Vector Search upsert** after FEATURES (`apps/data-plane/vector_upsert.py`) when `VECTOR_INDEX_NAME` is set
- **Vertex CustomJob** submission from training when `VERTEX_TRAINING_IMAGE` is set
- **Human review workbench**: `POST/GET /v1/review-queue`, action approve/reject/edit; classify enqueues low-confidence items; live Review Queue UI
- **Cost controller**: `POST /v1/cost/events`, `GET /v1/cost/summary` with daily budget from config
- Pipeline version **1.3.0** with `cost` section

### Prior
- **1.2.0** — Phase 2: Vector Search Terraform, training registry, SLA metrics, Settings load
- **1.1.0** — Gemini via ADC, real JiraTool REST, Settings UI
- **1.0.0** — Initial Control/Data Plane skeleton

## Release checklist
- [x] No Cortex dependency; Gemini via Vertex ADC
- [x] JiraTool REST with dry-run fallback
- [x] Multi-layer idempotency concepts
- [x] Vector Search infra + runtime upsert hook
- [x] Model registry + training candidate path
- [x] Review queue API + UI
- [x] SLA + cost summary endpoints
- [x] CHANGELOG + VERSION
