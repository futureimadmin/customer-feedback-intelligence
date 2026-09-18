provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

locals {
  labels = {
    project     = "customer-feedback-intelligence"
    environment = var.environment
    managed_by  = "terraform"
  }

  # Logical folder prefixes inside the data lake bucket
  gcs_prefixes = [
    "raw/",
    "raw-envelope/",
    "curated/normalized/",
    "curated/features/",
    "curated/classified/",
    "quarantine/",
    "models/",
    "training/",
  ]
}

data "google_project" "current" {
  project_id = var.project_id
}
