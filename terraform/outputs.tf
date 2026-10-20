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

output "vector_search" {
  description = "Vertex Vector Search resources for Agentic RAG"
  value = var.enable_vector_search ? {
    index_id              = try(google_vertex_ai_index.feedback[0].id, null)
    index_endpoint_id     = try(google_vertex_ai_index_endpoint.feedback[0].id, null)
    index_endpoint_name   = try(google_vertex_ai_index_endpoint.feedback[0].name, null)
    deployed_index_id     = "cfi_feedback_v1"
    env = {
      VECTOR_INDEX_ENDPOINT    = try(google_vertex_ai_index_endpoint.feedback[0].name, "")
      VECTOR_DEPLOYED_INDEX_ID = "cfi_feedback_v1"
    }
  } : null
}

output "gcs_training_prefix" {
  value = "gs://${google_storage_bucket.data_lake.name}/training/"
}
