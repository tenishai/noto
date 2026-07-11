# Example wiring for a GCP solo deployment - also what CI validates.

terraform {
  required_version = "~> 1.12"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.37"
    }
  }
}

variable "project" { type = string }
variable "location" {
  type    = string
  default = "us-east4"
}
variable "cluster" { type = string }

module "storage" {
  source   = "../../modules/object-storage/gcp"
  project  = var.project
  location = var.location
  prefix   = var.cluster
}

module "identity" {
  source  = "../../modules/workload-identity/gcp"
  project = var.project
  buckets = module.storage.buckets
}

output "buckets" { value = module.storage.buckets }
output "service_accounts" { value = module.identity.service_accounts }
