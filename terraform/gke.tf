resource "google_container_cluster" "primary" {
  count    = var.enable_gke ? 1 : 0
  provider = google-beta

  name     = var.gke_cluster_name
  project  = var.project_id
  location = var.region

  enable_autopilot = true

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  ip_allocation_policy {}

  # Deletion protection off for non-prod convenience; enable in prod
  deletion_protection = false

  resource_labels = local.labels
}

# Workload Identity: bind K8s SA to GCP SA (names must match deploy manifests)
resource "google_service_account_iam_member" "control_plane_wi" {
  count = var.enable_gke ? 1 : 0

  service_account_id = google_service_account.control_plane.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[cfi-control-plane/control-plane]"
}

resource "google_service_account_iam_member" "data_plane_wi" {
  count = var.enable_gke ? 1 : 0

  service_account_id = google_service_account.data_plane.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[cfi-data-plane/data-plane]"
}

resource "google_service_account_iam_member" "jira_tool_wi" {
  count = var.enable_gke ? 1 : 0

  service_account_id = google_service_account.jira_tool.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[cfi-jira-tool/jira-tool]"
}

output "gke_cluster_name" {
  value = var.enable_gke ? google_container_cluster.primary[0].name : null
}

output "gke_cluster_endpoint" {
  value     = var.enable_gke ? google_container_cluster.primary[0].endpoint : null
  sensitive = true
}
