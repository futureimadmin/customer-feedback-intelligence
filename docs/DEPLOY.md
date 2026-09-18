# Deploy to GCP (intelligent-machines / us-central1)

## 1. Auth

```bash
gcloud auth application-default login
gcloud config set project intelligent-machines
```

Enable APIs (once):

```bash
gcloud services enable \
  container.googleapis.com \
  storage.googleapis.com \
  bigquery.googleapis.com \
  secretmanager.googleapis.com \
  aiplatform.googleapis.com \
  iam.googleapis.com \
  compute.googleapis.com
```

## 2. Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform apply
```

Creates:

- Bucket `customer-feedback` with prefixes `raw/`, `raw-envelope/`, `curated/normalized/`, `curated/features/`, `curated/classified/`, `quarantine/`, `models/`, `training/`
- GKE Autopilot cluster `cfi-cluster`
- BigQuery dataset `feedback_intelligence`
- Service accounts + Workload Identity bindings
- Secret Manager shells for Jira

**Note:** GCS bucket names are globally unique. If `customer-feedback` is taken, set `bucket_name` in `terraform.tfvars` to e.g. `customer-feedback-intelligent-machines`.

## 3. Jira secrets

```bash
export JIRA_BASE_URL=https://YOUR.atlassian.net
export JIRA_EMAIL=automation@example.com
export JIRA_API_TOKEN=your_token
./scripts/bootstrap_secrets.sh
```

Edit non-secret defaults in `configs/jira.yaml` or the UI **Jira settings** page.

## 4. UI (local)

```bash
cd ui && npm install && npm run dev
```

## 5. JiraTool (local dry-run)

```bash
cd apps/jira-tool
pip install -r requirements.txt
uvicorn main:app --reload --port 8080
```

## Will it create Jira tickets?

Yes when:

1. Credentials are present in Secret Manager (or env for local),
2. A Ticket Candidate reaches `POST /v1/tickets` on JiraTool,
3. Dedup registry does not already map the `deduplicationKey`.

Wire the real Jira REST `POST /rest/api/3/issue` inside JiraTool where the dry-run synthetic key is generated today.
