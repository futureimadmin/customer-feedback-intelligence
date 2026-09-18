resource "google_bigquery_dataset" "feedback" {
  dataset_id  = var.bq_dataset_id
  project     = var.project_id
  location    = var.region
  description = "Customer Feedback Intelligence analytics"

  labels = local.labels
}

resource "google_bigquery_table" "feedback_classified" {
  dataset_id = google_bigquery_dataset.feedback.dataset_id
  table_id   = "feedback_classified"
  project    = var.project_id

  time_partitioning {
    type  = "DAY"
    field = "classified_at"
  }

  clustering = ["business_unit", "product"]

  schema = jsonencode([
    { name = "classified_id", type = "STRING", mode = "REQUIRED" },
    { name = "parent_feature_id", type = "STRING", mode = "NULLABLE" },
    { name = "business_unit", type = "STRING", mode = "NULLABLE" },
    { name = "category", type = "STRING", mode = "NULLABLE" },
    { name = "confidence", type = "FLOAT", mode = "NULLABLE" },
    { name = "sentiment", type = "STRING", mode = "NULLABLE" },
    { name = "product", type = "STRING", mode = "NULLABLE" },
    { name = "channel", type = "STRING", mode = "NULLABLE" },
    { name = "model_version", type = "STRING", mode = "NULLABLE" },
    { name = "classified_at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "ingestion_run_id", type = "STRING", mode = "NULLABLE" },
  ])
}

resource "google_bigquery_table" "ticket_outcomes" {
  dataset_id = google_bigquery_dataset.feedback.dataset_id
  table_id   = "ticket_outcomes"
  project    = var.project_id

  time_partitioning {
    type  = "DAY"
    field = "processed_at"
  }

  schema = jsonencode([
    { name = "ticket_candidate_id", type = "STRING", mode = "REQUIRED" },
    { name = "jira_key", type = "STRING", mode = "NULLABLE" },
    { name = "status", type = "STRING", mode = "NULLABLE" },
    { name = "action_taken", type = "STRING", mode = "NULLABLE" },
    { name = "deduplication_key", type = "STRING", mode = "NULLABLE" },
    { name = "processed_at", type = "TIMESTAMP", mode = "REQUIRED" },
  ])
}

output "bq_dataset" {
  value = google_bigquery_dataset.feedback.dataset_id
}
