# logging-bypass seam

Deliberately NOT an OpenTofu module. The managed-logging bypass mutates the cluster
resource (GKE: `--logging=SYSTEM`), and the cluster is owned by gojira's substrate, not
by Argus - adopting someone else's resource into our state would fight its owner (the
same reasoning as the GMP-supersession mechanics in design.md).

The seam is implemented as an idempotent engine CLI step instead (lands with the
`argus-platform` skill): per provider, GKE `gcloud container clusters update
--logging=SYSTEM`, EKS disable the CloudWatch agent, AKS disable AMA. `check()` reads
the current logging config; `apply()` narrows it to system-only. `kubectl logs -f` is
unaffected on every provider (kubelet reads node files).
