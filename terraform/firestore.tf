# Firestore Native mode database for Control Plane state
resource "google_project_service" "firestore" {
  project            = var.project_id
  service            = "firestore.googleapis.com"
  disable_on_destroy = false
}

resource "google_firestore_database" "control_plane" {
  project     = var.project_id
  name        = "customer-feedback-intelligence"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"

  depends_on = [google_project_service.firestore]
}

# Control Plane SA needs read/write on this database
resource "google_project_iam_member" "control_plane_datastore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.control_plane.email}"
}

output "firestore_database" {
  value       = google_firestore_database.control_plane.name
  description = "Firestore database ID for Control Plane"
}
