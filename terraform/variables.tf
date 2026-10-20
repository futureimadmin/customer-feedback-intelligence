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
  description = "GCS data lake bucket"
  type        = string
  default     = "customer-feedback"
}

variable "enable_vector_search" {
  description = "Provision Vertex AI Vector Search index + endpoint"
  type        = bool
  default     = true
}

variable "vector_index_dimensions" {
  description = "Embedding dimensions (text-embedding-004 = 768)"
  type        = number
  default     = 768
}

variable "vector_index_approx_neighbors" {
  description = "Approximate neighbors count for tree-AH"
  type        = number
  default     = 150
}

variable "vector_shard_size" {
  description = "Matching Engine shard size"
  type        = string
  default     = "SHARD_SIZE_SMALL"
}
