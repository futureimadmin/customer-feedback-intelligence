# Pull subscriptions for workers (GKE)
resource "google_pubsub_subscription" "stage_dispatch" {
  name    = "cfi-stage-dispatch-sub"
  topic   = google_pubsub_topic.stage_dispatch.name
  project = var.project_id

  ack_deadline_seconds = 120

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = 5
  }

  expiration_policy {
    ttl = "" # never expire
  }

  labels = local.labels
}

resource "google_pubsub_subscription" "heartbeat_tick" {
  name    = "cfi-heartbeat-tick-sub"
  topic   = google_pubsub_topic.heartbeat_tick.name
  project = var.project_id

  ack_deadline_seconds = 60

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  labels = local.labels
}

# Allow control-plane and data-plane SAs to subscribe
resource "google_pubsub_subscription_iam_member" "stage_sub_data_plane" {
  subscription = google_pubsub_subscription.stage_dispatch.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.data_plane.email}"
}

resource "google_pubsub_subscription_iam_member" "heartbeat_sub_control_plane" {
  subscription = google_pubsub_subscription.heartbeat_tick.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.control_plane.email}"
}

resource "google_pubsub_topic_iam_member" "control_plane_publish_stage" {
  topic  = google_pubsub_topic.stage_dispatch.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.control_plane.email}"
}

resource "google_pubsub_topic_iam_member" "control_plane_publish_dlq" {
  topic  = google_pubsub_topic.dlq.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.control_plane.email}"
}

output "pubsub_subscriptions" {
  value = {
    stage_dispatch = google_pubsub_subscription.stage_dispatch.name
    heartbeat_tick = google_pubsub_subscription.heartbeat_tick.name
  }
}
