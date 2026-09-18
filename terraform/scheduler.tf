# Enable Cloud Scheduler API
resource "google_project_service" "scheduler" {
  project            = var.project_id
  service            = "cloudscheduler.googleapis.com"
  disable_on_destroy = false
}

resource "google_project_service" "pubsub" {
  project            = var.project_id
  service            = "pubsub.googleapis.com"
  disable_on_destroy = false
}

# Pub/Sub topics for orchestration
resource "google_pubsub_topic" "stage_dispatch" {
  name    = "cfi-stage-dispatch"
  project = var.project_id
  labels  = local.labels
}

resource "google_pubsub_topic" "dlq" {
  name    = "cfi-dlq"
  project = var.project_id
  labels  = local.labels
}

resource "google_pubsub_topic" "heartbeat_tick" {
  name    = "cfi-heartbeat-tick"
  project = var.project_id
  labels  = local.labels
}

# SA for Scheduler to publish / hit Control Plane
resource "google_service_account" "scheduler" {
  account_id   = "cfi-scheduler"
  display_name = "CFI Cloud Scheduler"
  project      = var.project_id
}

resource "google_pubsub_topic_iam_member" "scheduler_publish_heartbeat" {
  topic  = google_pubsub_topic.heartbeat_tick.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.scheduler.email}"
}

# Every 5 minutes: heartbeat tick → Control Plane should create HEARTBEAT runs when sources have new data
resource "google_cloud_scheduler_job" "heartbeat" {
  name             = "cfi-heartbeat"
  description      = "Periodic heartbeat / incremental ingestion trigger"
  schedule         = "*/5 * * * *"
  time_zone        = "UTC"
  region           = var.region
  project          = var.project_id
  attempt_deadline = "320s"

  pubsub_target {
    topic_name = google_pubsub_topic.heartbeat_tick.id
    data       = base64encode(jsonencode({ "action" : "heartbeat_tick", "mode" : "HEARTBEAT" }))
  }

  depends_on = [google_project_service.scheduler]
}

output "pubsub_topics" {
  value = {
    stage_dispatch = google_pubsub_topic.stage_dispatch.name
    dlq            = google_pubsub_topic.dlq.name
    heartbeat_tick = google_pubsub_topic.heartbeat_tick.name
  }
}
