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

# The contract's own strings, validated at the boundary rather than trusted. They are not
# free text downstream: `app` and `namespace` become object names and `-n` arguments on
# every kubectl argv, and `cluster` becomes the tenant header dashboards query with. A
# value with a slash, a quote, a `..` or 64 characters in it is not a Kubernetes name, and
# the place to say so is here - before a temp file is written or a command is built.
_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_NAME_MAX = 63
# Cluster names are not k8s object names (they carry dots and capitals in every cloud's
# console), so they get their own, looser shape - still no separators, quotes or spaces.
_CLUSTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CLUSTER_MAX = 253


def contract_fingerprint(gojira_yaml_text: str) -> str:
    """Fingerprint a gojira.yaml contract exactly the way the provisioner does.

    Format (comments, key order, quoting) does not change the fingerprint - only the
    decoded document's meaning does.
    """
    doc = yaml.safe_load(gojira_yaml_text)
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canon.encode()).hexdigest()


def _require_name(value: object, field: str, pattern: re.Pattern[str], max_len: int) -> None:
    if not isinstance(value, str):
        raise JobInputError(f"contract field {field} must be a string (got {type(value).__name__})")
    if len(value) > max_len:
        raise JobInputError(
            f"contract field {field} is longer than {max_len} characters: {value[:24]!r}..."
        )
    if not pattern.match(value):
        raise JobInputError(f"contract field {field} is not a valid name: {value!r}")


def _validate_contract_names(doc: Mapping) -> None:
    """The fields argus turns into names and headers, checked.

    Only the strings are this function's business: the contract's SHAPE belongs to
    argus.gojira.reader, which is the one parser and raises its own errors. An
    `environments` that is not a list of mappings is left to it rather than reported
    twice, in two vocabularies.
    """
    _require_name(doc.get("app"), "app", _DNS_LABEL_RE, _NAME_MAX)
    environments = doc.get("environments")
    if not isinstance(environments, list):
        return
    for index, env in enumerate(environments):
        if not isinstance(env, Mapping):
            continue
        where = env.get("name") if isinstance(env.get("name"), str) else index
        _require_name(
            env.get("namespace"), f"environments[{where}].namespace", _DNS_LABEL_RE, _NAME_MAX
        )
        _require_name(
            env.get("cluster"), f"environments[{where}].cluster", _CLUSTER_RE, _CLUSTER_MAX
        )


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

    _validate_contract_names(doc)

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


def success_summary(
    app: str, env: str, applied: int, skipped: int, sha: str, note: str | None = None
) -> str:
    """The line a provisioner reads on success.

    The counts are STEPS, not objects: how many engine steps ran and how many the engine
    skipped. They were once worded "applied/unchanged", which reads as a count of objects
    that did not move - and `ApplyRendered.check()` never skips, so it could only ever
    have been steps.

    `note` is for something the run wants the launcher to see even though it succeeded
    (today: that there was no platform record, so the tenant came from the contract).
    """
    line = f"observed {app}/{env}: {applied} steps applied, {skipped} skipped, contract {sha[:12]}"
    if note:
        line += f" ({note})"
    return summary_line(line)


def failure_summary(step: str | None, reason: str) -> str:
    if step is None:
        return summary_line(f"observe failed: {reason}")
    return summary_line(f"observe failed: {step}: {reason}")
