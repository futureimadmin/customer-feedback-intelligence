# Data lake bucket — immutable raw + curated products
resource "google_storage_bucket" "data_lake" {
  name                        = var.bucket_name
  location                    = var.region
  project                     = var.project_id
  force_destroy               = false
  uniform_bucket_level_access = true
  storage_class               = "STANDARD"

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  labels = local.labels
}

# GCS has no real "folders"; zero-byte objects establish prefixes for tooling/UI
resource "google_storage_bucket_object" "prefixes" {
  for_each = toset(local.gcs_prefixes)

  name    = each.value
  bucket  = google_storage_bucket.data_lake.name
  content = " "
}

output "data_lake_bucket" {
  value       = google_storage_bucket.data_lake.name
  description = "GCS data lake bucket name"
}

output "data_lake_url" {
  value = "gs://${google_storage_bucket.data_lake.name}"
}
