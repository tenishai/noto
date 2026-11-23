# The noto image: a one-shot `noto observe` runner for a provisioner's in-cluster
# Job (README, "Running observe as a Job"). It preserves the repo layout instead of
# pip-installing the package into site-packages, because REPO_ROOT (agent/noto/cli.py)
# is derived from that file's own location and reads profiles/, integrations/ and
# helm/pins.yaml relative to it at runtime - see tests/unit/test_layout.py.
FROM --platform=$BUILDPLATFORM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS deps
ARG UV_VERSION=0.11.21
WORKDIR /lock
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir "uv==${UV_VERSION}" \
 && uv export --frozen --no-dev --no-emit-project -o requirements.txt

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
ARG TARGETARCH=amd64

# The upgrade pass pulls base packages to current security revisions even when
# the builder's cached base layer is stale; CI also builds with --pull. The release
# passes the day as APT_REFRESH, so this layer runs again on every release instead of
# coming out of the build cache with last month's packages.
ARG APT_REFRESH=
RUN echo "apt refresh: ${APT_REFRESH}" && apt-get update && apt-get -y upgrade \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG KUBECTL_VERSION=v1.33.4
RUN curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && curl -fsSL -o /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256" \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod +x /usr/local/bin/kubectl && rm /tmp/kubectl.sha256

# Sloth generates the SLO PrometheusRule from a spec (`_generate_slo_rules` in cli.py,
# `sloth generate`) - without the binary, every app with SLOs fails in the Job.
# scripts/validate-rendered.sh pins the same version locally; SLOTH_VERSION here must
# equal that script's SLOTH_VERSION (tests/unit/test_dockerfile_pins.py parses both).
# Sloth publishes no checksum file for this asset, so SLOTH_SHA256_AMD64 was computed
# once, by hand, from the real v0.16.0 linux-amd64 release (see the task report for how)
# and is verified below with sha256sum -c. The image is amd64 only, so the binary and
# its hash are both hardcoded to amd64 rather than derived from the build architecture.
# `.noto` is otherwise runtime scratch (never copied wholesale into the image) - this
# one binary is the sole exception.
ARG SLOTH_VERSION=0.16.0
ARG SLOTH_SHA256_AMD64=1cecaa50853abbb3669a928612b523df8174d258ea6f2a3c8f048e5fc5af90b2
ARG SLOTH_SHA256_ARM64=7b570c465df3cc997e744dfcfcea83c3559bba6b12f4d6165994925419cbb41c
RUN mkdir -p /app/.noto/tools \
    && curl -fsSL -o "/app/.noto/tools/sloth-${SLOTH_VERSION}" \
      "https://github.com/slok/sloth/releases/download/v${SLOTH_VERSION}/sloth-linux-${TARGETARCH}" \
    && sum="$([ "$TARGETARCH" = arm64 ] && echo "$SLOTH_SHA256_ARM64" || echo "$SLOTH_SHA256_AMD64")" \
    && echo "${sum}  /app/.noto/tools/sloth-${SLOTH_VERSION}" | sha256sum -c - \
    && chmod +x "/app/.noto/tools/sloth-${SLOTH_VERSION}" \
    && "/app/.noto/tools/sloth-${SLOTH_VERSION}" version

WORKDIR /app

# Runtime dependencies only, pinned to exactly what pyproject.toml specifies at the
# repo root (never the dev group) - tests/unit/test_dockerfile_pins.py keeps this pip
# install line from drifting away from pyproject.toml's pins.
COPY --from=deps /lock/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY pyproject.toml ./

# The repo layout, preserved verbatim - see the file header and test_layout.py.
COPY agent ./agent
COPY profiles ./profiles
COPY integrations ./integrations
COPY helm/pins.yaml ./helm/pins.yaml

RUN groupadd --system --gid 10001 noto \
    && useradd --system --uid 10001 --gid noto --home-dir /tmp --shell /usr/sbin/nologin noto
USER 10001
ENV PYTHONPATH=/app/agent PYTHONUNBUFFERED=1 HOME=/tmp
ENTRYPOINT ["python", "-m", "noto.cli"]
CMD ["observe"]
