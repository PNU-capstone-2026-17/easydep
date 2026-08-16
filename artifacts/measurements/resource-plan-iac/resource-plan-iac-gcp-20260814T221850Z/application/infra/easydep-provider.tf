terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 5.45.2"
    }
  }
}
provider "google" {
  project = var.project_id
  region = "asia-northeast3"
}
