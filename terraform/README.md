# terraform/

Cloud-side, stateful, outside-the-cluster resources: object-storage buckets for Mimir/Loki/Tempo,
IAM / Workload Identity Federation for backends and collectors. Applied directly (no GitOps
controller). See [../docs/design.md](../docs/design.md).
