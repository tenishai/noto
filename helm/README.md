# helm/

Pinned Helm components for the platform. [pins.yaml](pins.yaml) is the machine-readable
manifest (repo + chart + exact version + values file + topology) the step engine reads;
[values/](values/) holds the per-component values with the spike gotchas encoded (Perses
plugin mount, Loki OTLP structured metadata, Tempo metrics-generator ownership).

Dynamic inputs (bucket names, service accounts, endpoints) are NOT baked into values -
the engine supplies them at apply time from the seam outputs (`--set`), so the committed
values stay deployment-agnostic (scale posture: numbers are inputs).

`make validate-helm` templates every pinned chart with our values (CI job on every PR):
a values key the chart rejects, or a chart/version that does not exist, fails there
instead of at apply time.
