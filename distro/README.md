# distro/

The Argus OpenTelemetry Collector distribution. [manifest.yaml](manifest.yaml) pins core plus the
exact contrib components the renderers emit (all at the v0.154.0 train, gomods sourced from the
official otelcol-contrib manifest at the same tag). Adding a component is a rebuild-and-release
event. See [../docs/design.md](../docs/design.md) and [../docs/versions.md](../docs/versions.md).

Build locally (Go >= 1.26 required; 0.154's generics break older linkers):

```
curl -sSfL "https://github.com/open-telemetry/opentelemetry-collector-releases/releases/download/cmd%2Fbuilder%2Fv0.154.0/ocb_0.154.0_$(uname -s | tr A-Z a-z)_$(uname -m | sed 's/x86_64/amd64/')" -o ocb && chmod +x ocb
cd distro && ../ocb --config manifest.yaml
./_build/argus-otelcol --version
```

CI (`.github/workflows/distro.yml`) builds the distro and validates the rendered collector configs
against the resulting binary whenever the manifest or the renderers change - proving the component
set is sufficient for everything Argus emits.
