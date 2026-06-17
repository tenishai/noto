# distro/

The Argus OpenTelemetry Collector distribution. Holds the `ocb` (OpenTelemetry Collector Builder)
manifest that pins core + the specific contrib components Argus needs, and builds the custom collector
image. Adding a component is a rebuild-and-release event. See [../docs/design.md](../docs/design.md).
