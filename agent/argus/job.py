"""Job-mode inputs for `argus observe` when a provisioner launches it as a one-shot
in-cluster Job (design decision, issue tracked in the born-observed plan).

The provisioner (gojira) writes the app's contract into the job's environment rather
than a mounted file, and pins a sha256 of it so a mismatched contract (a stale
ConfigMap, a race with a redeploy) is caught before anything is applied. The
fingerprint algorithm here MUST match the provisioner's byte for byte: parse the YAML,
canonicalize with sorted keys and compact separators, hash the UTF-8 bytes.

The job always ends with exactly one `ARGUS_SUMMARY <line>` on stdout, success or
failure, so the launcher can parse a result without scraping the whole log.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

import yaml

SUMMARY_PREFIX = "ARGUS_SUMMARY "
SUMMARY_MAX = 300

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_KNOWN_ENVS = ("dev", "qa", "prod")


def contract_fingerprint(gojira_yaml_text: str) -> str:
    """Fingerprint a gojira.yaml contract exactly the way the provisioner does.

    Format (comments, key order, quoting) does not change the fingerprint - only the
    decoded document's meaning does.
    """
    doc = yaml.safe_load(gojira_yaml_text)
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


@dataclass(frozen=True)
class JobInputs:
    contract_yaml: str
    contract_sha256: str
    env: str
    cluster: str | None
    namespace: str  # platform namespace, default "argus"


class JobInputError(ValueError):
    """Job-mode environment variables are present but unusable."""


def job_inputs(environ: Mapping[str, str]) -> JobInputs | None:
    """None when GOJIRA_CONTRACT_YAML is absent (operator mode).

    Raises JobInputError when present but the inputs are unusable.
    """
    contract_yaml = environ.get("GOJIRA_CONTRACT_YAML")
    if contract_yaml is None:
        return None

    sha_raw = environ.get("GOJIRA_CONTRACT_SHA256")
    if sha_raw is None:
        raise JobInputError("GOJIRA_CONTRACT_SHA256 is required in job mode")
    if not _SHA256_RE.match(sha_raw):
        raise JobInputError("GOJIRA_CONTRACT_SHA256 must be 64 hex characters")
    sha = sha_raw.lower()

    try:
        doc = yaml.safe_load(contract_yaml)
    except yaml.YAMLError as e:
        raise JobInputError(f"GOJIRA_CONTRACT_YAML is not valid YAML: {e}") from e
    if not isinstance(doc, dict):
        raise JobInputError("GOJIRA_CONTRACT_YAML must decode to a mapping")

    actual = contract_fingerprint(contract_yaml)
    if actual != sha:
        raise JobInputError(
            "GOJIRA_CONTRACT_SHA256 does not match the contract fingerprint "
            f"(given {sha[:12]}..., computed {actual[:12]}...)"
        )

    env = environ.get("ARGUS_ENV")
    if env not in _KNOWN_ENVS:
        raise JobInputError(f"ARGUS_ENV must be one of {', '.join(_KNOWN_ENVS)} (got {env!r})")

    cluster = environ.get("ARGUS_CLUSTER", "").strip() or None
    namespace = environ.get("ARGUS_NAMESPACE", "argus")

    return JobInputs(
        contract_yaml=contract_yaml,
        contract_sha256=sha,
        env=env,
        cluster=cluster,
        namespace=namespace,
    )


def summary_line(text: str) -> str:
    """'ARGUS_SUMMARY ' + text collapsed to one line, clipped to SUMMARY_MAX total."""
    collapsed = " ".join(text.split())
    return (SUMMARY_PREFIX + collapsed)[:SUMMARY_MAX]


def success_summary(app: str, env: str, applied: int, unchanged: int, sha: str) -> str:
    return summary_line(
        f"observed {app}/{env}: {applied} applied, {unchanged} unchanged, contract {sha[:12]}"
    )


def failure_summary(step: str | None, reason: str) -> str:
    if step is None:
        return summary_line(f"observe failed: {reason}")
    return summary_line(f"observe failed: {step}: {reason}")
