terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.40"
    }
  }

  # Optional remote state — uncomment and set bucket after bootstrap
  # backend "gcs" {
  #   bucket = "customer-feedback-tfstate"
  #   prefix = "terraform/state"
  # }
}
