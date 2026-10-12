# Noto workload-identity seam, GCP implementation: GKE Workload Identity from the
# backend KSAs to their buckets. One GSA per backend, objectAdmin on its own bucket
# only, workloadIdentityUser bound to the noto-namespace KSA of the same name.

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
variable "buckets" {
  type        = map(string)
  description = "Backend -> bucket name (the object-storage module's output)."
}
variable "namespace" {
  type    = string
  default = "noto"
}

resource "google_service_account" "backend" {
  for_each = var.buckets

  project      = var.project
  account_id   = "noto-${each.key}"
  display_name = "Noto ${each.key} backend"
}

resource "google_storage_bucket_iam_member" "object_admin" {
  for_each = var.buckets

  bucket = each.value
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.backend[each.key].email}"
}

resource "google_service_account_iam_member" "workload_identity" {
  for_each = var.buckets

  service_account_id = google_service_account.backend[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project}.svc.id.goog[${var.namespace}/noto-${each.key}]"
}

output "service_accounts" {
  value = { for k, sa in google_service_account.backend : k => sa.email }
}
