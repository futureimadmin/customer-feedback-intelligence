# Vertex AI Vector Search (Matching Engine) for Agentic RAG similar-issue retrieval.
# Index is created empty; stream or batch-update embeddings from the Data Plane.
# Set VECTOR_INDEX_ENDPOINT and VECTOR_DEPLOYED_INDEX_ID on data-plane workers.

resource "google_vertex_ai_index" "feedback" {
  count = var.enable_vector_search ? 1 : 0

  region       = var.region
  display_name = "cfi-feedback-embeddings"
  description  = "Customer feedback + issue embeddings for Agentic RAG"
  labels       = local.labels

  metadata {
    contents_delta_uri = "gs://${google_storage_bucket.data_lake.name}/training/vector-index-init/"
    config {
      dimensions                  = var.vector_index_dimensions
      approximate_neighbors_count = var.vector_index_approx_neighbors
      distance_measure_type       = "DOT_PRODUCT_DISTANCE"
      feature_norm_type           = "UNIT_L2_NORM"
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count    = 1000
          leaf_nodes_to_search_percent = 10
        }
      }
      shard_size = var.vector_shard_size
    }
  }

  index_update_method = "STREAM_UPDATE"

  lifecycle {
    ignore_changes = [metadata]
  }
}

resource "google_vertex_ai_index_endpoint" "feedback" {
  count = var.enable_vector_search ? 1 : 0

  region       = var.region
  display_name = "cfi-feedback-endpoint"
  description  = "Endpoint for CFI feedback vector similarity search"
  labels       = local.labels

  public_endpoint_enabled = true
}

resource "google_vertex_ai_index_endpoint_deployed_index" "feedback" {
  count = var.enable_vector_search ? 1 : 0

  index_endpoint = google_vertex_ai_index_endpoint.feedback[0].id
  index          = google_vertex_ai_index.feedback[0].id
  deployed_index_id = "cfi_feedback_v1"
  display_name      = "cfi-feedback-v1"

  dedicated_resources {
    machine_spec {
      machine_type = "e2-standard-2"
    }
    min_replica_count = 1
    max_replica_count = 2
  }

  depends_on = [
    google_vertex_ai_index.feedback,
    google_vertex_ai_index_endpoint.feedback,
  ]
}

resource "google_project_iam_member" "data_plane_aiplatform_admin_light" {
  count   = var.enable_vector_search ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.data_plane.email}"
}
