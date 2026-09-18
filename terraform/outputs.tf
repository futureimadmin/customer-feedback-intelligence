output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "service_accounts" {
  value = {
    control_plane = google_service_account.control_plane.email
    data_plane    = google_service_account.data_plane.email
    jira_tool     = google_service_account.jira_tool.email
  }
}
