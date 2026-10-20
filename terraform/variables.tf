variable "project_id" {
  description = "GCP project ID"
  type        = string
  default     = "intelligent-machines"
}

variable "region" {
  description = "Primary region"
  type        = string
  default     = "us-central1"
}

variable "bucket_name" {
  description = "GCS data lake bucket (must be globally unique)"
  type        = string
  default     = "customer-feedback"
}

variable "environment" {
  description = "Environment label"
  type        = string
  default     = "dev"
}

variable "gke_cluster_name" {
  description = "GKE cluster name"
  type        = string
  default     = "cfi-cluster"
}

variable "bq_dataset_id" {
  description = "BigQuery dataset for analytics"
  type        = string
  default     = "feedback_intelligence"
}

variable "enable_gke" {
  description = "Create GKE Autopilot cluster"
  type        = bool
  default     = true
}

# --- Jira (non-secret defaults; secrets go to Secret Manager) ---

variable "jira_project_key" {
  description = "Default Jira project key for auto-created issues"
  type        = string
  default     = "MOB"
}

variable "jira_issue_type" {
  description = "Default Jira issue type"
  type        = string
  default     = "Bug"
}

variable "jira_base_url_placeholder" {
  description = "Placeholder only — real URL stored in Secret Manager"
  type        = string
  default     = "https://YOUR_DOMAIN.atlassian.net"
}

# --- Vertex AI Vector Search ---

variable "enable_vector_search" {
  description = "Create Vertex AI Matching Engine index + endpoint for Agentic RAG"
  type        = bool
  default     = true
}

variable "vector_index_dimensions" {
  description = "Embedding dimensions (text-embedding-004 = 768)"
  type        = number
  default     = 768
}

variable "vector_index_approx_neighbors" {
  description = "Approximate neighbors for the tree-AH index"
  type        = number
  default     = 150
}

variable "vector_shard_size" {
  description = "Vertex Matching Engine shard size"
  type        = string
  default     = "SHARD_SIZE_SMALL"
}
