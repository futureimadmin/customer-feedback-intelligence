# Control Plane runtime SA
resource "google_service_account" "control_plane" {
  account_id   = "cfi-control-plane"
  display_name = "CFI Control Plane"
  project      = var.project_id
}

# Data Plane runtime SA
resource "google_service_account" "data_plane" {
  account_id   = "cfi-data-plane"
  display_name = "CFI Data Plane"
  project      = var.project_id
}

# JiraTool runtime SA
resource "google_service_account" "jira_tool" {
  account_id   = "cfi-jira-tool"
  display_name = "CFI JiraTool"
  project      = var.project_id
}

# Bucket IAM
resource "google_storage_bucket_iam_member" "data_plane_object_admin" {
  bucket = google_storage_bucket.data_lake.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.data_plane.email}"
}

resource "google_storage_bucket_iam_member" "control_plane_object_viewer" {
  bucket = google_storage_bucket.data_lake.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.control_plane.email}"
}

# BigQuery data editor for analytics loader
resource "google_project_iam_member" "data_plane_bq" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.data_plane.email}"
}

resource "google_project_iam_member" "data_plane_bq_job" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.data_plane.email}"
}

# Secret accessor for JiraTool
resource "google_project_iam_member" "jira_tool_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.jira_tool.email}"
}

# Vertex AI user for data plane (embeddings / prediction)
resource "google_project_iam_member" "data_plane_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.data_plane.email}"
}
