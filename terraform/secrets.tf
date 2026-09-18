# Jira connection secrets — values added out-of-band (never in TF state as plaintext)
resource "google_secret_manager_secret" "jira_base_url" {
  secret_id = "jira-base-url"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = local.labels
}

resource "google_secret_manager_secret" "jira_email" {
  secret_id = "jira-email"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = local.labels
}

resource "google_secret_manager_secret" "jira_api_token" {
  secret_id = "jira-api-token"
  project   = var.project_id

  replication {
    auto {}
  }

  labels = local.labels
}

output "jira_secret_ids" {
  value = {
    base_url  = google_secret_manager_secret.jira_base_url.secret_id
    email     = google_secret_manager_secret.jira_email.secret_id
    api_token = google_secret_manager_secret.jira_api_token.secret_id
  }
  description = "Secret Manager IDs for Jira; add versions with gcloud secrets versions add"
}
