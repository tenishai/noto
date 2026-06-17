# Versioning and Releases

Argus pins everything (an existing hard convention) and versions everything consistently with that
rule. This document defines how we version, tag, and change-log.

## Semantic Versioning

We follow [SemVer 2.0.0](https://semver.org/): `MAJOR.MINOR.PATCH`.

- **MAJOR** - incompatible change to a public interface (the gojira-derived contract / `observability:`
  block, the profile schema, a skill's documented behavior, or the agent CLI).
- **MINOR** - backwards-compatible capability (a new profile, a new skill, a new step in the engine).
- **PATCH** - backwards-compatible fix (a corrected default, a bug fix, a doc fix that changes behavior).

While we are pre-`1.0.0`, the API is unstable by definition: minor versions may carry breaking changes,
and we communicate them loudly in the CHANGELOG. We tag the first end-to-end Phase-1 milestone `v0.1.0`.

## Two version lines

Argus contains two things that version on different clocks. Keep them distinct:

1. **The Argus release** - the repo as a whole (agent, profiles, helm/CRD pins, docs). Tagged on
   `main` as `vX.Y.Z`. This is the version a user means by "which Argus."
2. **The collector distro** - the `ocb`-built OpenTelemetry Collector in `distro/`. It tracks the
   upstream OTel Collector release train and is pinned in the ocb manifest. It carries its **own**
   version (matching the upstream collector minor it is built from), recorded in the manifest and in
   the built image tag, and is referenced by digest where deployed.

An Argus release records exactly which distro version it ships. Bumping the distro (to add a contrib
component or follow upstream) is a normal `feat`/`chore` change that flows into the next Argus release;
it does not get its own git tag on this repo. Rationale: one tag namespace on `main` stays simple, and
the manifest is the single source of truth for the distro version.

## Pinning (the hard rule, restated)

No floating `latest` anywhere. Every dependency is pinned to an exact version:

- ocb components: pinned gomod + version in the distro manifest.
- Helm charts: pinned chart version + pinned image tags in values.
- Terraform: pinned provider versions and a committed lockfile.
- Container images in deployed manifests: pinned by tag and, where possible, by digest.

CI's conventions lint flags obvious floating pins (`:latest`, `version: latest`).

## Git tags

- Releases are **annotated, signed-where-possible** tags on `main`: `git tag -a v0.1.0 -m "..."`.
- Tags are immutable. A mistake is fixed forward with a new patch tag, never by moving a tag.
- Pre-release milestones may use `-rc.N` suffixes (e.g. `v0.1.0-rc.1`).

## CHANGELOG

We keep a human-readable [CHANGELOG.md](../CHANGELOG.md) in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Unreleased work accumulates under an
`## [Unreleased]` heading; cutting a release renames that heading to the version and date and opens a
fresh `Unreleased`. Entries are grouped under Added / Changed / Fixed / Removed / Security.
