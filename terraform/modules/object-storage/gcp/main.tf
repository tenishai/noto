# Noto object-storage seam, GCP implementation: one bucket per backend block store.
# Retention/lifecycle values are INPUTS (scale-posture rule) - deployments size them.

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
variable "location" { type = string }
variable "prefix" {
  type        = string
  description = "Deployment-unique prefix, e.g. the region or cluster name."
}
variable "retention_days" {
  type        = map(number)
  description = "Object age (days) after which lifecycle deletion applies, per store."
  default     = { mimir = 400, loki = 30, tempo = 30 }
}
variable "force_destroy" {
  type    = bool
  default = false
}

locals {
  stores = { mimir = "blocks", loki = "chunks", tempo = "traces" }
}

resource "google_storage_bucket" "store" {
  for_each = local.stores

  project                     = var.project
  name                        = "${var.prefix}-noto-${each.key}-${each.value}"
  location                    = var.location
  uniform_bucket_level_access = true
  force_destroy               = var.force_destroy

  lifecycle_rule {
    condition {
      age = var.retention_days[each.key]
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    "noto-managed" = "true"
    "noto-store"   = each.key
  }
}

output "buckets" {
  value = { for k, b in google_storage_bucket.store : k => b.name }
}
