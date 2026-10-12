# MinIO, rebuilt for the solo topology's in-cluster object store.
#
# The Mimir chart's bundled MinIO pins quay.io/minio/minio and hard-codes the
# official image's entrypoint. MinIO withdrew public distribution in 2026: that
# quay repository has no tags (401), docker.io/minio/minio is empty, and
# dl.min.io answers 410 for every binary. On 2026-10-06 a GKE node pool
# upgrade recreated every node in the dev cluster and the MinIO pod could not
# be pulled again, which took the whole metrics path down for eleven hours
# while logs kept flowing (tenishai/noto#90).
#
# This is the SAME release, rebuilt from the RPMs MinIO still publishes on
# GitHub Releases, checksum-verified against the .sha256sum assets, laid out
# like the official image: binaries in /usr/bin, MinIO's own
# docker-entrypoint.sh from the same tag. It also carries mc at the version
# the chart's make-buckets hook pins, so one image serves `minio.image` and
# `minio.mcImage`. Build it into the deployment's own registry and name it
# through the values-overrides seam (`noto platform --values-overrides`);
# nothing in this repository names a registry.
#
#   docker build -t <registry>/minio:RELEASE.2024-12-18T13-15-44Z images/minio
#   gcloud builds submit images/minio --tag <registry>/minio:RELEASE.2024-12-18T13-15-44Z
#
# Pins (docs/versions.md): MinIO RELEASE.2024-12-18T13-15-44Z, mc
# RELEASE.2024-11-21T17-21-54Z, the versions the mimir-distributed 6.1.0 chart
# pins through its minio 5.4.0 subchart. linux/amd64 only, like the platform.
ARG MINIO_RELEASE=RELEASE.2024-12-18T13-15-44Z
ARG MINIO_RPM=minio-20241218131544.0.0-1.x86_64.rpm
ARG MC_RELEASE=RELEASE.2024-11-21T17-21-54Z
ARG MC_RPM=mcli-20241121172154.0.0-1.x86_64.rpm

FROM debian:bookworm-slim AS extract
ARG MINIO_RELEASE
ARG MINIO_RPM
ARG MC_RELEASE
ARG MC_RPM
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl libarchive-tools \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /x
# The .sha256sum assets name the file as MinIO published it; checked as such.
# bsdtar reads an RPM directly (any payload compression), where rpm2cpio on
# Debian extracted nothing from these and the first build installed ''.
RUN curl -fsSL -o "$MINIO_RPM" "https://github.com/minio/minio/releases/download/${MINIO_RELEASE}/${MINIO_RPM}" \
 && curl -fsSL -o "$MINIO_RPM.sha256sum" "https://github.com/minio/minio/releases/download/${MINIO_RELEASE}/${MINIO_RPM}.sha256sum" \
 && sha256sum -c "$MINIO_RPM.sha256sum" \
 && mkdir minio && bsdtar -xf "$MINIO_RPM" -C minio \
 && find minio -type f | head -20 \
 && install -m 0755 "$(find minio -type f -name minio | head -1)" /minio
RUN curl -fsSL -o "$MC_RPM" "https://github.com/minio/mc/releases/download/${MC_RELEASE}/${MC_RPM}" \
 && curl -fsSL -o "$MC_RPM.sha256sum" "https://github.com/minio/mc/releases/download/${MC_RELEASE}/${MC_RPM}.sha256sum" \
 && sha256sum -c "$MC_RPM.sha256sum" \
 && mkdir mc && bsdtar -xf "$MC_RPM" -C mc \
 && find mc -type f | head -20 \
 && install -m 0755 "$(find mc -type f \( -name mc -o -name mcli \) | head -1)" /mc

FROM registry.access.redhat.com/ubi9/ubi-micro:latest
COPY --from=extract /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=extract /minio /usr/bin/minio
COPY --from=extract /mc /usr/bin/mc
COPY docker-entrypoint.sh /usr/bin/docker-entrypoint.sh
# As the official image does: the chart runs as an arbitrary uid and MinIO
# writes its config beside the binaries.
RUN chmod -R 777 /usr/bin
EXPOSE 9000
ENTRYPOINT ["/usr/bin/docker-entrypoint.sh"]
CMD ["minio"]
