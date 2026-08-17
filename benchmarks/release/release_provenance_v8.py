"""Fail-closed v0.13 machine-only release provenance verification.

The verifier reopens every machine qualification boundary and derives its
decision from retained bytes.  It never accepts Human Gold, human attestation,
caller-authored status facts, or a trusted-human descriptor as an input.
"""

# The verifier keeps long contract field names visible at their validation seams.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.qualification_evidence_core import (
    canonical_json_bytes as _core_canonical_json_bytes,
)
from benchmarks.release.qualification_evidence_core import (
    digest_without as _core_digest_without,
)
from benchmarks.release.qualification_evidence_core import (
    regular_file_bytes as _core_regular_file_bytes,
)
from benchmarks.release.qualification_evidence_core import (
    require_exact_protocol_gate_ids as _core_require_exact_protocol_gate_ids,
)
from benchmarks.release.qualification_evidence_core import (
    safe_asset_file as _core_safe_asset_file,
)
from benchmarks.release.qualification_evidence_core import (
    safe_relative_posix as _core_safe_relative_posix,
)
from benchmarks.release.qualification_evidence_core import (
    safe_root_directory as _core_safe_root_directory,
)
from benchmarks.release.qualification_evidence_core import (
    sha256_bytes as _core_sha256_bytes,
)
from benchmarks.release.qualification_evidence_core import (
    strict_json_bytes as _core_strict_json_bytes,
)

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "contracts"
_CURRENT_CLASSIFICATION_PATH = REPOSITORY / "benchmarks/release/v013-gate-classification-v8.json"

_PROFILE = "machine_evaluated_no_human_attestation"
_REFERENCE_PROVENANCE = "agent_consensus"
_HUMAN_AUTHENTICITY = "not_claimed"
_RELEASE_SCHEMA_VERSION = "deeplaw.commercial-release-manifest/v8"
_REPORT_SCHEMA_VERSION = "deeplaw.commercial-evidence-report/v5"
_GATE_SCHEMA_VERSION = "deeplaw.provenance-bound-gate-result/v4"
_BUNDLE_SCHEMA_VERSION = "deeplaw.external-qualification-bundle-manifest/v4"
_TYPED_SCHEMA_VERSION = "deeplaw.typed-qualification-evidence/v2"
_CLASSIFICATION_SCHEMA_VERSION = "deeplaw.v013-release-gate-classification/v8"
_CLASSIFICATION_ID = "deeplaw-v013-commercial-gates-v8"
_VALIDATOR_ID = "deeplaw-typed-qualification-v2"
_VALIDATOR_VERSION = "2"
_VALIDATOR_SOURCE = "benchmarks/release/typed_qualification_evidence.py"
_VALIDATOR_EXECUTABLE = "benchmarks/release/assemble_commercial_qualification_v8.py"
_EXACT_WHEEL_RUNNER_SOURCE = REPOSITORY / "benchmarks/release/exact_wheel_runner.py"
_CANDIDATE_RAW_INVENTORY_SCHEMA = "deeplaw.candidate-full-inventory-receipt/v1"
_SEMANTIC_REFERENCE_SCHEMA = "deeplaw.semantic-machine-reference/v1"
_SEMANTIC_THRESHOLD_ID = "semantic-machine-reference-thresholds"
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:(?<![A-Za-z0-9])/(?:Users|home|private|var|tmp|root|etc|opt|Volumes|workspace)(?:/|$))"
    r"|(?:^|[\s\"'])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|(?:api[_-]?key|access[_-]?token|authorization|"
    r"bearer|password|passwd|private[_-]?key|secret)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_SECRET_KEY = re.compile(
    r"(?:^|[._-])(?:credential|credentials|secret|secrets|password|passwd|"
    r"api[_-]?key|private[_-]?key|access[_-]?token|bearer)(?:$|[._-])",
    re.IGNORECASE,
)
_SAFE_FALSE_FIELDS = frozenset(
    {
        "auth_file_read",
        "auth_store_read",
        "authentication_material_read",
        "credential_value_recorded",
    }
)
_SAFE_LITERAL_FIELDS = {
    "human_authenticity": {_HUMAN_AUTHENTICITY},
    "reference_provenance": {_REFERENCE_PROVENANCE},
    "auth_status_command": {"codex login status"},
    "auth_material_access": {"forbidden"},
    "secret_visibility": {"forbidden"},
}

_TYPED_KINDS = frozenset(
    {
        "candidate_full_junit",
        "candidate_platform_receipt",
        "host_event_sequence",
        "exact_wheel_execution",
        "legal_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "retained_supply_chain",
        "machine_reference_scorer",
    }
)
_CANDIDATE_KINDS = frozenset(
    {"candidate_full_junit", "candidate_platform_receipt", "retained_supply_chain"}
)
_EXTERNAL_KINDS = _TYPED_KINDS - _CANDIDATE_KINDS
_EXTERNAL_CORPUS_ROLES = frozenset({"qualification_holdout", "final_blind"})
_HOST_TASK_CASES = frozenset(
    {
        "cold/new",
        "resume/fork/concurrent-worktree",
        "compaction/forget/stale",
    }
)
_GATE_EVIDENCE_KINDS: dict[str, frozenset[str]] = {
    "canonical_integrity": frozenset({"exact_wheel_execution"}),
    "migration_recovery": frozenset({"candidate_full_junit"}),
    "secret_host_isolation": frozenset({"host_event_sequence"}),
    "bounded_context": frozenset({"context_capsule_selection_usage"}),
    "legal_evidence": frozenset({"legal_rows"}),
    "source_citation_locator": frozenset({"legal_rows"}),
    "scale_performance": frozenset({"scale_report"}),
    "supported_platforms": frozenset({"candidate_platform_receipt"}),
    "reproducible_supply_chain": frozenset({"retained_supply_chain"}),
    "machine_reference_isolation": frozenset({"machine_reference_scorer"}),
    "codex": frozenset({"host_event_sequence"}),
    "opencode": frozenset({"host_event_sequence"}),
    "selective_forget": frozenset({"wiki_journey_rows"}),
    "timeline": frozenset({"host_event_sequence"}),
}
_REQUIRED_TYPED_COUNTS = {
    "candidate_full_junit": 1,
    "candidate_platform_receipt": 9,
    "host_event_sequence": 6,
    "exact_wheel_execution": 1,
    "machine_reference_scorer": 2,
    "legal_rows": 1,
    "wiki_journey_rows": 1,
    "context_capsule_selection_usage": 1,
    "scale_report": 1,
    "retained_supply_chain": 1,
}
_HOST_MODELS = {"codex": "gpt-5.6-luna", "opencode": "deepseek-v4-flash"}


def _expected_gate_corpus_roles(gate_id: str) -> list[str]:
    if _GATE_EVIDENCE_KINDS.get(gate_id, frozenset()) <= _CANDIDATE_KINDS:
        return ["candidate_full"]
    if gate_id == "canonical_integrity":
        return ["candidate_full"]
    if gate_id == "machine_reference_isolation":
        return ["qualification_holdout", "final_blind"]
    return ["qualification_holdout"]


class ReleaseProvenanceV8Error(ValueError):
    """Raised when v8 transitive provenance is absent or inconsistent."""


class _BundleEntry:
    __slots__ = ("path", "raw", "reference", "relative")

    def __init__(
        self,
        *,
        relative: str,
        path: Path,
        raw: bytes,
        reference: Mapping[str, Any],
    ) -> None:
        self.relative = relative
        self.path = path
        self.raw = raw
        self.reference = reference


class _TypedRecord:
    __slots__ = ("derived", "entry", "kind", "manifest", "manifest_record")

    def __init__(
        self,
        *,
        kind: str,
        entry: _BundleEntry,
        manifest: Mapping[str, Any],
        manifest_record: str,
        derived: Mapping[str, Any],
    ) -> None:
        self.kind = kind
        self.entry = entry
        self.manifest = manifest
        self.manifest_record = manifest_record
        self.derived = derived


def _fail(message: str) -> None:
    raise ReleaseProvenanceV8Error(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _check_projection(value: Any, *, label: str, depth: int = 0) -> None:
    if depth > 64:
        _fail(f"{label} exceeds JSON depth bound")
    if isinstance(value, str):
        if _ABSOLUTE_PATH.search(value) or _SECRET_VALUE.search(value):
            _fail(f"{label} contains a private path or Secret")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(f"{label} contains a non-string field")
            if key in _SAFE_FALSE_FIELDS:
                if item is not False:
                    _fail(f"{label} authentication field must be false")
            elif key in _SAFE_LITERAL_FIELDS:
                if item not in _SAFE_LITERAL_FIELDS[key]:
                    _fail(f"{label} authentication field is invalid")
            elif _SECRET_KEY.search(key):
                _fail(f"{label} contains a Secret-shaped field")
            _check_projection(item, label=label, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _check_projection(item, label=label, depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        _fail(f"{label} contains a non-finite number")
    _fail(f"{label} contains an unsupported value")


def _parse_json(raw: bytes, *, label: str, project: bool = True) -> dict[str, Any]:
    return _core_strict_json_bytes(
        raw,
        label=label,
        error_type=ReleaseProvenanceV8Error,
        projection=(lambda value: _check_projection(value, label=label)) if project else None,
        require_object=True,
    )


def _has_symlink_component(path: Path) -> bool:
    parts = path.expanduser().parts
    start = 1 if path.expanduser().is_absolute() else 0
    for index in range(start, len(parts) + 1):
        try:
            if Path(*parts[:index]).is_symlink():
                return True
        except OSError:
            return True
    return False


def _regular_file(path: Path, *, label: str, max_bytes: int = _MAX_FILE_BYTES) -> bytes:
    _resolved, raw = _core_regular_file_bytes(
        path,
        label=label,
        max_bytes=max_bytes,
        error_type=ReleaseProvenanceV8Error,
    )
    return raw


def _exact_wheel_runner_identity() -> dict[str, str]:
    return {
        "identity": "exact-wheel-runner:v2",
        "sha256": _sha256(
            _regular_file(_EXACT_WHEEL_RUNNER_SOURCE, label="exact-wheel runner source")
        ),
    }


def _safe_root(path: Path, *, label: str) -> Path:
    return _core_safe_root_directory(
        path,
        label=label,
        error_type=ReleaseProvenanceV8Error,
    )


def _safe_relative(value: Any, *, label: str) -> str:
    return _core_safe_relative_posix(
        value,
        label=label,
        error_type=ReleaseProvenanceV8Error,
    )


def _safe_asset(root: Path, relative: Any, *, label: str) -> Path:
    return _core_safe_asset_file(
        root,
        relative,
        label=label,
        error_type=ReleaseProvenanceV8Error,
    )


def _sha256(raw: bytes) -> str:
    return _core_sha256_bytes(raw)


def _canonical(value: Any) -> bytes:
    return _core_canonical_json_bytes(value, error_type=ReleaseProvenanceV8Error)


def _canonical_digest(value: Mapping[str, Any], *, excluded: str) -> str:
    return _core_digest_without(
        value,
        field=excluded,
        error_type=ReleaseProvenanceV8Error,
    )


def _record_digest(value: Mapping[str, Any], *, field: str = "record_sha256") -> str:
    return _canonical_digest(value, excluded=field)


def _derived_digest(value: Mapping[str, Any]) -> str:
    return _sha256(_canonical(value))


def _load_schema(filename: str) -> dict[str, Any]:
    raw = _regular_file(CONTRACTS / filename, label=f"{filename} contract")
    schema = _parse_json(raw, label=f"{filename} contract", project=False)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise ReleaseProvenanceV8Error(f"{filename} is not a valid JSON Schema") from error
    return schema


def _validate_schema(value: Mapping[str, Any], filename: str, *, label: str) -> None:
    schema = _load_schema(filename)
    try:
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
            key=lambda item: list(item.path),
        )
    except Exception as error:
        raise ReleaseProvenanceV8Error(f"{label} schema validation failed") from error
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        _fail(f"{label} schema violation at {location}")


def _load_json(path: Path, *, label: str, schema: str | None = None) -> tuple[dict[str, Any], bytes]:
    raw = _regular_file(path, label=label)
    value = _parse_json(raw, label=label)
    if schema is not None:
        _validate_schema(value, schema, label=label)
    return value, raw


def _require_record(value: Mapping[str, Any], *, label: str, field: str = "record_sha256") -> None:
    digest = value.get(field)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _fail(f"{label} record digest is invalid")
    if digest != _record_digest(value, field=field):
        _fail(f"{label} record digest differs")


def _equal(label: str, *values: Any) -> None:
    if not values or any(value != values[0] for value in values[1:]):
        _fail(f"{label} differs across provenance boundaries")


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(f"{label} is invalid")
    return value


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    return value


def _candidate_from_release(release: Mapping[str, Any]) -> dict[str, str]:
    binding = _mapping(release.get("candidate_binding"), label="release candidate binding")
    result: dict[str, str] = {}
    for field in ("commit", "tree"):
        value = binding.get(field)
        if not isinstance(value, str) or not _GIT.fullmatch(value):
            _fail(f"release candidate {field} is invalid")
        result[field] = value
    for field in ("lock_sha256", "wheel_sha256", "sdist_sha256"):
        value = binding.get(field)
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            _fail(f"release candidate {field} is invalid")
        result[field] = value
    return result


def _candidate_from_active(active: Mapping[str, Any]) -> dict[str, str]:
    binding = _mapping(active.get("candidate_binding"), label="active candidate binding")
    values = {
        "commit": binding.get("source_commit"),
        "tree": binding.get("source_tree"),
        "lock_sha256": binding.get("lock_sha256"),
        "wheel_sha256": binding.get("wheel_sha256"),
        "sdist_sha256": binding.get("sdist_sha256"),
    }
    for field in ("commit", "tree"):
        if not isinstance(values[field], str) or not _GIT.fullmatch(values[field]):
            _fail(f"active candidate {field} is invalid")
    for field in ("lock_sha256", "wheel_sha256", "sdist_sha256"):
        if not isinstance(values[field], str) or not _SHA256.fullmatch(values[field]):
            _fail(f"active candidate {field} is invalid")
    return {field: str(value) for field, value in values.items()}


def _load_classification(path: Path) -> tuple[dict[str, Any], bytes, list[str]]:
    value, raw = _load_json(path, label="v8 Gate classification", schema="v013-release-gate-classification.v8.schema.json")
    if (
        value.get("schema_version") != _CLASSIFICATION_SCHEMA_VERSION
        or value.get("classification_id") != _CLASSIFICATION_ID
        or value.get("profile") != _PROFILE
    ):
        _fail("Gate classification identity is not the machine-only v8 contract")
    policy = _mapping(value.get("assembly_policy"), label="Gate assembly policy")
    if policy != {"assembly_enabled": False, "reason_code": "awaiting_all_core_gate_pass"}:
        _fail("v8 Gate assembly must remain closed")
    categories = value.get("categories")
    gates = value.get("gates")
    if not isinstance(categories, list) or not isinstance(gates, list):
        _fail("Gate classification inventory is missing")
    core_ids: list[str] = []
    for category in categories:
        item = _mapping(category, label="Gate classification category")
        if item.get("category") == "Core":
            listed = item.get("gate_ids")
            if not isinstance(listed, list) or not all(isinstance(gate_id, str) for gate_id in listed):
                _fail("Core Gate classification inventory is invalid")
            core_ids.extend(listed)
    if len(core_ids) != 14 or len(set(core_ids)) != 14:
        _fail("v8 Core Gate inventory must contain exactly 14 identities")
    if set(core_ids) != set(_GATE_EVIDENCE_KINDS):
        _fail("v8 Core Gate mapping is not closed")
    gate_by_id: dict[str, Mapping[str, Any]] = {}
    for item in gates:
        gate = _mapping(item, label="Gate classification entry")
        gate_id = gate.get("gate_id")
        if not isinstance(gate_id, str) or gate_id in gate_by_id:
            _fail("Gate classification contains duplicate identities")
        gate_by_id[gate_id] = gate
    if set(gate_by_id) != set(core_ids) | {
        "semantic_restore",
        "claude",
        "comparative_incremental_benefit",
        "superiority",
        "sota",
    }:
        _fail("v8 Gate classification has an unexpected Gate inventory")
    for gate_id in core_ids:
        gate = gate_by_id[gate_id]
        if (
            gate.get("category") != "Core"
            or gate.get("required") is not True
            or gate.get("not_claimed_only") is not False
            or gate.get("assembly_enabled") is not False
            or gate.get("validator_id") != _VALIDATOR_ID
            or str(gate.get("validator_version")) != _VALIDATOR_VERSION
            or gate.get("accepted_input_schema_versions") != [_TYPED_SCHEMA_VERSION]
            or gate.get("output_schema_versions") != [_GATE_SCHEMA_VERSION]
            or set(gate.get("artifact_kinds", [])) != set(_GATE_EVIDENCE_KINDS[gate_id])
        ):
            _fail(f"Gate {gate_id} is not bound to typed v2 and Gate v4")
        expected_roles = _expected_gate_corpus_roles(gate_id)
        if gate.get("required_corpus_roles") != expected_roles:
            _fail(f"Gate {gate_id} corpus roles are not closed")
    return value, raw, core_ids


def _bundle_index(bundle_root: Path, manifest: Mapping[str, Any]) -> dict[str, _BundleEntry]:
    references = manifest.get("files")
    if not isinstance(references, list) or not references:
        _fail("external bundle file inventory is missing")
    index: dict[str, _BundleEntry] = {}
    total = 0
    for reference_value in references:
        reference = _mapping(reference_value, label="external bundle file reference")
        relative = _safe_relative(reference.get("relative_path"), label="external bundle path")
        if relative == "bundle-manifest.json" or relative in index:
            _fail("external bundle inventory has a duplicate path")
        path = bundle_root / relative
        raw = _regular_file(path, label="external bundle file")
        if reference.get("byte_size") != len(raw) or reference.get("sha256") != _sha256(raw):
            _fail("external bundle file binding differs from retained bytes")
        index[relative] = _BundleEntry(
            relative=relative,
            path=path,
            raw=raw,
            reference=reference,
        )
        total += len(raw)
        if total > _MAX_TOTAL_BYTES:
            _fail("external bundle exceeds its aggregate byte bound")
    actual: set[str] = set()
    for path in bundle_root.rglob("*"):
        if path.is_symlink():
            _fail("external bundle contains a symbolic link")
        if path.is_file():
            relative = path.relative_to(bundle_root).as_posix()
            if relative != "bundle-manifest.json":
                actual.add(relative)
    if actual != set(index):
        _fail("external bundle has an orphan or unreferenced file")
    for entry in index.values():
        kind = entry.reference.get("evidence_kind")
        if kind in {"human_gold_scorer", "post_build_gold_binding", "semantic_human_gold"}:
            _fail("Human Gold evidence is forbidden in the machine-only provenance chain")
    return index


def _find_entry(
    index: Mapping[str, _BundleEntry],
    *,
    evidence_kind: str,
    digest: str | None = None,
    label: str,
) -> _BundleEntry:
    matches = [
        entry
        for entry in index.values()
        if entry.reference.get("evidence_kind") == evidence_kind
        and (digest is None or entry.reference.get("sha256") == digest)
    ]
    if len(matches) != 1:
        _fail(f"{label} must bind exactly one retained file")
    return matches[0]


def _machine_external_from_binding(
    binding: Mapping[str, Any],
    *,
    binding_sha256: str,
    compiler_scorer_isolation_sha256: str,
) -> dict[str, str]:
    semantic = _mapping(binding.get("semantic_reference"), label="semantic reference binding")
    panel = _mapping(binding.get("scorer_panel"), label="scorer panel binding")
    scorer_a = _mapping(panel.get("scorer_a"), label="scorer A binding")
    scorer_b = _mapping(panel.get("scorer_b"), label="scorer B binding")
    arbiter = _mapping(binding.get("arbiter"), label="arbiter binding")
    runner = _mapping(binding.get("runner"), label="runner binding")
    expected = {
        "semantic_reference_sha256": semantic.get("sha256"),
        "candidate_binding_sha256": binding_sha256,
        "qualification_holdout_sha256": _mapping(binding.get("holdout"), label="holdout binding").get("sha256"),
        "final_blind_holdout_sha256": _mapping(binding.get("blind"), label="blind binding").get("sha256"),
        "agent_roster_sha256": _mapping(binding.get("agent_roster"), label="Agent roster binding").get("sha256"),
        "agent_consensus_sha256": _mapping(binding.get("agent_consensus"), label="Agent consensus binding").get("sha256"),
        "agent_isolation_sha256": _mapping(binding.get("agent_isolation"), label="Agent isolation binding").get("sha256"),
        "runner_sha256": runner.get("sha256"),
        "scorer_panel_sha256": panel.get("panel_sha256"),
        "arbiter_sha256": arbiter.get("sha256"),
        "compiler_scorer_isolation_sha256": compiler_scorer_isolation_sha256,
    }
    for field, value in expected.items():
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            _fail(f"machine reference {field} is invalid")
    if scorer_a.get("identity") == scorer_b.get("identity") or scorer_a.get("sha256") == scorer_b.get("sha256"):
        _fail("machine scorer panel identities are not distinct")
    if panel.get("distinct_scorers") is not True:
        _fail("machine scorer panel is not marked distinct")
    if arbiter.get("role") != "deterministic_arbiter":
        _fail("machine arbiter role is invalid")
    if runner.get("identity") in {scorer_a.get("identity"), scorer_b.get("identity")}:
        _fail("machine runner identity overlaps a scorer")
    if compiler_scorer_isolation_sha256 == arbiter.get("sha256"):
        _fail("compiler/scorer isolation must not reuse the arbiter digest")
    return expected


def _candidate_provenance_identities() -> dict[str, dict[str, dict[str, str]]]:
    files: dict[str, dict[str, tuple[str, ...]]] = {
        "candidate_full_junit": {
            "runner": (".github/workflows/candidate-full.yml",),
            "scorer": ("benchmarks/release/typed_qualification_evidence.py",),
        },
        "candidate_platform_receipt": {
            "runner": (".github/workflows/candidate-full.yml",),
            "scorer": (
                "benchmarks/release/typed_qualification_evidence.py",
                "benchmarks/release/platform-core-test-manifest-v2.json",
            ),
        },
        "retained_supply_chain": {
            "runner": (
                ".github/workflows/candidate-full.yml",
                "benchmarks/release/verify_reproducible_build.py",
            ),
            "scorer": ("benchmarks/release/typed_qualification_evidence.py",),
        },
    }
    result: dict[str, dict[str, dict[str, str]]] = {}
    for kind, roles in files.items():
        result[kind] = {}
        for role, relatives in roles.items():
            components: list[dict[str, str]] = []
            for relative in relatives:
                selected = _safe_asset(REPOSITORY, relative, label=f"{kind} {role} source")
                raw = _regular_file(selected, label=f"{kind} {role} source")
                components.append({"path": relative, "sha256": _sha256(raw)})
            result[kind][role] = {
                "identity": f"candidate-{role}-set/{'/'.join(relatives)}",
                "sha256": _sha256(_canonical({"files": components})),
            }
    return result


def _parse_typed_record(
    entry: _BundleEntry,
    *,
    bundle_root: Path,
    candidate: Mapping[str, str],
    candidate_run_id: int,
    evidence_run_id: int,
    external: Mapping[str, str],
    binding: Mapping[str, Any],
    candidate_inventory_sha256: str,
) -> _TypedRecord:
    from benchmarks.release import typed_qualification_evidence as typed

    envelope = _parse_json(entry.raw, label="typed qualification manifest")
    kind = envelope.get("kind")
    if kind == "human_gold_scorer" or kind not in _TYPED_KINDS:
        _fail("Human Gold or unknown typed evidence is not admissible in v8")
    if entry.reference.get("evidence_kind") != kind:
        _fail("typed evidence kind differs from bundle inventory")
    _validate_schema(
        envelope,
        "typed-qualification-evidence.v2.schema.json",
        label="typed qualification manifest",
    )
    if (
        envelope.get("profile") != _PROFILE
        or envelope.get("reference_provenance") != _REFERENCE_PROVENANCE
        or envelope.get("human_authenticity") != _HUMAN_AUTHENTICITY
    ):
        _fail("typed evidence is not machine-only")
    _require_record(envelope, label="typed qualification manifest")
    candidate_binding = envelope.get("candidate_binding")
    if candidate_binding != dict(candidate):
        _fail("typed evidence candidate binding differs")
    run = _mapping(envelope.get("run_binding"), label="typed run binding")
    workflow_id = candidate_run_id if kind in _CANDIDATE_KINDS else evidence_run_id
    if run.get("workflow_run_id") != workflow_id:
        _fail("typed evidence workflow run binding differs")
    corpus = _mapping(envelope.get("corpus"), label="typed corpus binding")
    role = corpus.get("role")
    corpus_sha = corpus.get("sha256")
    if not isinstance(corpus_sha, str) or not _SHA256.fullmatch(corpus_sha):
        _fail("typed corpus digest is invalid")
    if kind in _CANDIDATE_KINDS:
        if role != "candidate_full":
            _fail("Candidate Full typed evidence has the wrong corpus role")
    elif kind == "exact_wheel_execution":
        if role != "candidate_full" or corpus_sha != candidate_inventory_sha256:
            _fail("exact-wheel evidence does not bind Candidate Full raw inventory")
    elif role not in _EXTERNAL_CORPUS_ROLES:
        _fail("external typed evidence has the wrong corpus role")
    elif corpus_sha != external["qualification_holdout_sha256"] and corpus_sha != external["final_blind_holdout_sha256"]:
        _fail("external typed corpus differs from its bound holdout/blind")
    if kind == "machine_reference_scorer":
        if envelope.get("scorer_panel") != binding.get("scorer_panel") or envelope.get("arbiter") != binding.get("arbiter"):
            _fail("machine scorer panel or arbiter differs from its binding")
        if envelope.get("runner") != binding.get("runner"):
            _fail("machine scorer runner differs from its binding")
    elif kind in _EXTERNAL_KINDS:
        expected_runner = (
            _exact_wheel_runner_identity()
            if kind == "exact_wheel_execution"
            else binding.get("runner")
        )
        if envelope.get("runner") != expected_runner:
            _fail("external typed runner differs from its binding")
        panel = envelope.get("scorer_panel")
        arbiter = envelope.get("arbiter")
        if panel is not None and panel != binding.get("scorer_panel"):
            _fail("external typed scorer panel differs from its binding")
        if arbiter is not None and arbiter != binding.get("arbiter"):
            _fail("external typed arbiter differs from its binding")

    manifest_root = bundle_root
    parser = typed._PARSERS.get(kind)
    if parser is None:
        _fail(f"typed evidence kind {kind} has no parser")
    kwargs: dict[str, Any] = {"root": manifest_root, "record_sha256": envelope["record_sha256"]}
    if kind in {
        "legal_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "host_event_sequence",
    }:
        kwargs["expected_corpus_sha256"] = corpus_sha
    if kind == "exact_wheel_execution":
        kwargs["expected_candidate_run_id"] = candidate_run_id
    try:
        derived = dict(parser(envelope, **kwargs))
    except Exception as error:
        raise ReleaseProvenanceV8Error("typed evidence parser rejected raw receipts") from error
    if derived.get("schema_version") != "deeplaw.typed-qualification-derived/v2":
        derived["schema_version"] = "deeplaw.typed-qualification-derived/v2"
    if derived.get("kind") != kind or derived.get("status") != "passed":
        _fail("typed evidence did not derive a passed result")
    if derived.get("evidence_record_sha256") != envelope["record_sha256"]:
        _fail("typed derived result is not bound to its manifest")
    return _TypedRecord(
        kind=kind,
        entry=entry,
        manifest=envelope,
        manifest_record=envelope["record_sha256"],
        derived=derived,
    )


def _validate_candidate_inventory(
    root: Path,
    *,
    bundle_root: Path,
    candidate_run_id: int,
    candidate_commit: str,
    expected_digest: str,
) -> bytes:
    selected = _safe_root(root, label="Candidate Full raw artifact root")
    bundle = _safe_root(bundle_root, label="external evidence root")
    if selected == bundle or selected.is_relative_to(bundle) or bundle.is_relative_to(selected):
        _fail("Candidate Full raw artifact root must be independent")
    inventory_path = selected / "candidate-full-inventory-receipt.json"
    inventory, raw = _load_json(inventory_path, label="Candidate Full raw inventory receipt")
    if (
        inventory.get("schema_version") != _CANDIDATE_RAW_INVENTORY_SCHEMA
        or inventory.get("record_kind") != "candidate_full_raw_inventory"
        or inventory.get("run_id") != candidate_run_id
        or inventory.get("head_sha") != candidate_commit
        or inventory.get("path_policy") != "logical_relative_paths_only"
    ):
        _fail("Candidate Full raw inventory identity differs")
    if _sha256(raw) != expected_digest:
        _fail("Candidate Full raw inventory digest differs from the bundle")
    rows = inventory.get("files")
    if not isinstance(rows, list):
        _fail("Candidate Full raw inventory file list is missing")
    declared: dict[str, tuple[str, int]] = {}
    for row_value in rows:
        row = _mapping(row_value, label="Candidate Full raw inventory row")
        if set(row) != {"logical_path", "sha256", "bytes"}:
            _fail("Candidate Full raw inventory row is not closed")
        logical = _safe_relative(row.get("logical_path"), label="Candidate raw logical path")
        digest = row.get("sha256")
        size = row.get("bytes")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _fail("Candidate raw inventory digest is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            _fail("Candidate raw inventory size is invalid")
        if logical in declared:
            _fail("Candidate Full raw inventory contains a duplicate path")
        declared[logical] = (digest, size)
    observed: dict[str, tuple[str, int]] = {}
    for path in selected.rglob("*"):
        if path.is_symlink():
            _fail("Candidate Full raw root contains a symbolic link")
        if path.is_file() and path != inventory_path:
            data = _regular_file(path, label="Candidate Full raw artifact")
            observed[path.relative_to(selected).as_posix()] = (_sha256(data), len(data))
    if observed != declared:
        _fail("Candidate Full raw inventory does not match retained bytes")
    run_receipt_path = selected / "candidate-full-run-receipt.json"
    run_receipt_raw = _regular_file(run_receipt_path, label="Candidate Full raw run receipt")
    run_receipt = _parse_json(run_receipt_raw, label="Candidate Full raw run receipt")
    if (
        set(run_receipt)
        != {
            "schema_version",
            "record_kind",
            "workflow",
            "workflow_path",
            "run_id",
            "run_attempt",
            "head_sha",
            "event",
            "retention_days",
            "path_policy",
        }
        or run_receipt.get("schema_version") != "deeplaw.candidate-full-run-receipt/v1"
        or run_receipt.get("record_kind") != "candidate_full_raw_evidence"
        or run_receipt.get("workflow") != "Candidate Full"
        or run_receipt.get("workflow_path") != ".github/workflows/candidate-full.yml"
        or run_receipt.get("run_id") != candidate_run_id
        or run_receipt.get("head_sha") != candidate_commit
        or isinstance(run_receipt.get("run_attempt"), bool)
        or not isinstance(run_receipt.get("run_attempt"), int)
        or run_receipt["run_attempt"] < 1
        or run_receipt.get("event") not in {"workflow_dispatch", "pull_request"}
        or run_receipt.get("retention_days") != 90
        or run_receipt.get("path_policy") != "logical_relative_paths_only"
    ):
        _fail("Candidate Full raw run receipt identity differs")
    return raw


def _load_machine_binding(
    path: Path,
    *,
    bundle_root: Path,
    index: Mapping[str, _BundleEntry],
    candidate: Mapping[str, str],
    external_inputs: Mapping[str, str],
) -> tuple[dict[str, Any], bytes]:
    value, raw = _load_json(path, label="post-build machine reference binding", schema="candidate-gold-binding-receipt.v2.schema.json")
    _require_record(value, label="post-build machine reference binding")
    if value.get("profile") != _PROFILE or value.get("reference_provenance") != _REFERENCE_PROVENANCE or value.get("human_authenticity") != _HUMAN_AUTHENTICITY:
        _fail("post-build machine reference binding is not machine-only")
    bound_candidate = _mapping(value.get("candidate"), label="machine reference candidate")
    if {
        "commit": bound_candidate.get("commit"),
        "tree": bound_candidate.get("tree"),
        "lock_sha256": bound_candidate.get("lock_sha256"),
    } != {
        "commit": candidate["commit"],
        "tree": candidate["tree"],
        "lock_sha256": candidate["lock_sha256"],
    }:
        _fail("machine reference candidate differs from exact candidate")
    artifacts = _mapping(value.get("artifacts"), label="machine reference artifacts")
    for name, field in (("wheel", "wheel_sha256"), ("sdist", "sdist_sha256")):
        artifact = _mapping(artifacts.get(name), label=f"machine reference {name}")
        _equal(f"machine reference {name}", artifact.get("sha256"), candidate[field])
    compiler_isolation = external_inputs.get("compiler_scorer_isolation_sha256")
    if not isinstance(compiler_isolation, str) or not _SHA256.fullmatch(compiler_isolation):
        _fail("external compiler/scorer isolation digest is invalid")
    expected = _machine_external_from_binding(
        value,
        binding_sha256=_sha256(raw),
        compiler_scorer_isolation_sha256=compiler_isolation,
    )
    _equal(
        "machine reference semantic reference",
        expected["semantic_reference_sha256"],
        external_inputs["semantic_reference_sha256"],
    )
    for field in (
        "candidate_binding_sha256",
        "qualification_holdout_sha256",
        "final_blind_holdout_sha256",
        "agent_roster_sha256",
        "agent_consensus_sha256",
        "agent_isolation_sha256",
        "runner_sha256",
        "scorer_panel_sha256",
        "arbiter_sha256",
        "compiler_scorer_isolation_sha256",
    ):
        _equal(f"machine reference {field}", expected[field], external_inputs[field])
    try:
        relative = path.resolve(strict=True).relative_to(bundle_root).as_posix()
    except (OSError, ValueError) as error:
        raise ReleaseProvenanceV8Error("machine reference binding is outside the bundle") from error
    entry = index.get(relative)
    if entry is None or entry.reference.get("evidence_kind") != "post_build_machine_reference_binding" or entry.raw != raw:
        _fail("machine reference binding bytes are not closed by the bundle")
    return value, raw


def _threshold_binding(reference: Mapping[str, Any]) -> dict[str, Any]:
    thresholds = _mapping(reference.get("thresholds"), label="semantic reference thresholds")
    return {
        "threshold_id": _SEMANTIC_THRESHOLD_ID,
        "threshold_sha256": _sha256(_canonical(thresholds)),
        "frozen": True,
    }


def _load_reference(
    index: Mapping[str, _BundleEntry],
    *,
    external_inputs: Mapping[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    entry = _find_entry(
        index,
        evidence_kind="semantic_machine_reference",
        digest=external_inputs["semantic_reference_sha256"],
        label="semantic machine reference",
    )
    reference = _parse_json(entry.raw, label="semantic machine reference")
    _validate_schema(reference, "semantic-machine-reference.v1.schema.json", label="semantic machine reference")
    _require_record(reference, label="semantic machine reference")
    if (
        reference.get("profile") != _PROFILE
        or reference.get("reference_provenance") != _REFERENCE_PROVENANCE
        or reference.get("human_authenticity") != _HUMAN_AUTHENTICITY
        or reference.get("human_claim_eligible") is not False
        or reference.get("competitive_claim_eligible") is not False
    ):
        _fail("semantic reference makes an unavailable human or competitive claim")
    review = _mapping(reference.get("agent_review"), label="semantic reference Agent review")
    for name in ("roster_sha256", "consensus_sha256", "isolation_sha256"):
        _equal(f"semantic reference {name}", review.get(name), external_inputs[f"agent_{name.removesuffix('_sha256')}_sha256"])
    return reference, _threshold_binding(reference)


def _validate_reference_auxiliary(
    index: Mapping[str, _BundleEntry],
    *,
    external_inputs: Mapping[str, str],
) -> None:
    for kind, field in (
        ("agent_roster", "agent_roster_sha256"),
        ("agent_consensus", "agent_consensus_sha256"),
        ("agent_isolation", "agent_isolation_sha256"),
    ):
        entry = _find_entry(index, evidence_kind=kind, digest=external_inputs[field], label=kind)
        value = _parse_json(entry.raw, label=kind)
        _require_record(value, label=kind)
        if value.get("profile") != _PROFILE:
            _fail(f"{kind} is not machine-only")


def _resolve_asset(root: Path, relative: Any, *, label: str, expected_sha256: str | None = None, expected_size: int | None = None) -> tuple[Path, bytes]:
    path = _safe_asset(root, relative, label=label)
    raw = _regular_file(path, label=label)
    if expected_sha256 is not None and _sha256(raw) != expected_sha256:
        _fail(f"{label} digest differs")
    if expected_size is not None and len(raw) != expected_size:
        _fail(f"{label} byte size differs")
    return path, raw


def _typed_entry_from_asset(
    root: Path,
    *,
    relative: str,
    records: Sequence[_TypedRecord],
) -> _TypedRecord:
    path, raw = _resolve_asset(root, relative, label="Gate typed input")
    for record in records:
        if record.entry.path.resolve(strict=True) == path.resolve(strict=True):
            if record.entry.raw != raw:
                _fail("Gate typed input bytes changed")
            return record
    _fail("Gate references a typed manifest outside the closed bundle")


def _validate_gate_result(
    path: Path,
    *,
    root: Path,
    gate_id: str,
    classification: Mapping[str, Any],
    classification_sha256: str,
    core_gate_ids: Sequence[str],
    candidate: Mapping[str, str],
    protocol_binding: Mapping[str, Any],
    threshold_binding: Mapping[str, Any],
    reference_binding: Mapping[str, Any],
    records: Sequence[_TypedRecord],
    candidate_run_id: int,
    evidence_run_id: int,
    qualification_run_id: int,
) -> dict[str, Any]:
    result, _raw = _load_json(
        path,
        label=f"Gate {gate_id}",
        schema="provenance-bound-gate-result.v4.schema.json",
    )
    _require_record(result, label=f"Gate {gate_id}", field="result_sha256")
    if (
        result.get("profile") != _PROFILE
        or result.get("reference_provenance") != _REFERENCE_PROVENANCE
        or result.get("human_authenticity") != _HUMAN_AUTHENTICITY
    ):
        _fail(f"Gate {gate_id} is not machine-only")
    if (
        result.get("gate_id") != gate_id
        or result.get("category") != "Core"
        or result.get("validator_id") != _VALIDATOR_ID
        or str(result.get("validator_version")) != _VALIDATOR_VERSION
        or result.get("qualification_run_id") != qualification_run_id
        or result.get("status") != "passed"
    ):
        _fail(f"Gate {gate_id} identity or status differs")
    class_bind = _mapping(
        result.get("classification_binding"),
        label=f"Gate {gate_id} classification",
    )
    _equal("Gate classification id", class_bind.get("classification_id"), _CLASSIFICATION_ID)
    _equal("Gate classification schema", class_bind.get("classification_schema_version"), _CLASSIFICATION_SCHEMA_VERSION)
    _equal("Gate classification digest", class_bind.get("classification_sha256"), classification_sha256)
    candidate_bind = _mapping(result.get("candidate_binding"), label=f"Gate {gate_id} candidate")
    _equal("Gate candidate commit", candidate_bind.get("candidate_commit"), candidate["commit"])
    _equal("Gate candidate tree", candidate_bind.get("candidate_tree"), candidate["tree"])
    _equal("Gate candidate wheel", candidate_bind.get("candidate_wheel_sha256"), candidate["wheel_sha256"])
    _equal("Gate candidate sdist", candidate_bind.get("candidate_sdist_sha256"), candidate["sdist_sha256"])
    for field in ("protocol_binding", "threshold_binding", "reference_binding"):
        _equal(f"Gate {gate_id} {field}", result.get(field), protocol_binding if field == "protocol_binding" else threshold_binding if field == "threshold_binding" else reference_binding)
    allowed = _GATE_EVIDENCE_KINDS.get(gate_id)
    if allowed is None:
        _fail(f"Gate {gate_id} is outside the closed v8 map")
    corpora_value = result.get("corpora")
    if not isinstance(corpora_value, list):
        _fail(f"Gate {gate_id} corpora are missing")
    expected_roles = _expected_gate_corpus_roles(gate_id)
    if [item.get("role") for item in corpora_value if isinstance(item, Mapping)] != expected_roles:
        _fail(f"Gate {gate_id} corpus roles are not closed")
    if len(corpora_value) != len(expected_roles) or any(
        not isinstance(item, Mapping)
        or item.get("role") not in expected_roles
        or item.get("frozen") is not True
        for item in corpora_value
    ):
        _fail(f"Gate {gate_id} corpus binding is invalid")
    corpora = {item["role"]: item for item in corpora_value}
    inputs = result.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        _fail(f"Gate {gate_id} inputs are missing")
    input_records: list[_TypedRecord] = []
    input_ids: set[str] = set()
    expected_metrics: dict[str, Any] = {}
    expected_failures: dict[str, int] = {}
    expected_runs: list[str] = []
    executions = result.get("executions")
    if not isinstance(executions, list) or len(executions) != len(inputs):
        _fail(f"Gate {gate_id} execution/input inventory differs")
    execution_by_input: dict[str, Mapping[str, Any]] = {}
    for execution in executions:
        item = _mapping(execution, label=f"Gate {gate_id} execution")
        refs = item.get("input_refs")
        if not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str):
            _fail(f"Gate {gate_id} execution input binding is invalid")
        execution_by_input[refs[0]] = item
    for item_value in inputs:
        item = _mapping(item_value, label=f"Gate {gate_id} input")
        input_id = item.get("input_id")
        if not isinstance(input_id, str) or input_id in input_ids:
            _fail(f"Gate {gate_id} input identity is invalid")
        input_ids.add(input_id)
        if item.get("schema_version") != _TYPED_SCHEMA_VERSION or item.get("artifact_kind") != "typed-qualification-evidence":
            _fail(f"Gate {gate_id} input schema identity differs")
        relative = _safe_relative(item.get("relative_path"), label=f"Gate {gate_id} input path")
        record = _typed_entry_from_asset(root, relative=relative, records=records)
        if (
            item.get("byte_size") != len(record.entry.raw)
            or item.get("file_sha256") != _sha256(record.entry.raw)
            or item.get("record_sha256") != record.manifest_record
            or item.get("evidence_kind") != record.kind
            or item.get("derived_record_sha256") != _derived_digest(record.derived)
        ):
            _fail(f"Gate {gate_id} input bytes or derived binding differs")
        if record.kind not in allowed:
            _fail(f"Gate {gate_id} input kind is not allowed for this Gate")
        record_corpus = _mapping(record.manifest.get("corpus"), label="typed corpus")
        record_role = record_corpus.get("role")
        if record_role not in corpora or record_corpus.get("sha256") != corpora[record_role].get("sha256"):
            _fail(f"Gate {gate_id} crosses corpus domains")
        input_records.append(record)
        expected_runs.append(str(record.manifest["run_binding"]["run_id"]))
        derived_metrics = _mapping(record.derived.get("metrics"), label=f"Gate {gate_id} derived metrics")
        derived_failures = _mapping(record.derived.get("hard_failure_counts"), label=f"Gate {gate_id} derived failures")
        for metric_name, observed in derived_metrics.items():
            expected_metrics[f"{input_id}:{metric_name}"] = observed
        for failure_name, count in derived_failures.items():
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                _fail(f"Gate {gate_id} derived failure count is invalid")
            expected_failures[f"{input_id}:{failure_name}"] = count
        execution = execution_by_input.get(input_id)
        if execution is None:
            _fail(f"Gate {gate_id} has an unbound typed execution")
        _equal(f"Gate {gate_id} execution run", execution.get("run_id"), record.manifest["run_binding"]["run_id"])
        _equal(f"Gate {gate_id} execution workflow", execution.get("workflow_run_id"), record.manifest["run_binding"]["workflow_run_id"])
        _equal(f"Gate {gate_id} execution kind", execution.get("evidence_kind"), record.kind)
    if len(expected_runs) != len(set(expected_runs)):
        _fail(f"Gate {gate_id} contains duplicate typed run identities")
    if result.get("run_ids") != expected_runs:
        _fail(f"Gate {gate_id} run id list is not derived from typed evidence")
    metrics = result.get("metrics")
    if not isinstance(metrics, list) or len(metrics) != len(expected_metrics):
        _fail(f"Gate {gate_id} metric inventory differs")
    observed_metrics: dict[str, Any] = {}
    for metric_value in metrics:
        metric = _mapping(metric_value, label=f"Gate {gate_id} metric")
        name = metric.get("metric")
        refs = metric.get("input_refs")
        if not isinstance(name, str) or not isinstance(refs, list) or len(refs) != 1:
            _fail(f"Gate {gate_id} metric reference is invalid")
        observed_metrics[name] = metric.get("observed")
        if refs[0] not in input_ids:
            _fail(f"Gate {gate_id} metric references an unknown input")
    if observed_metrics != expected_metrics:
        _fail(f"Gate {gate_id} metrics are not derived from typed evidence")
    failures = result.get("hard_failures")
    if not isinstance(failures, list) or len(failures) != len(expected_failures):
        _fail(f"Gate {gate_id} hard-failure inventory differs")
    observed_failures: dict[str, int] = {}
    for failure_value in failures:
        failure = _mapping(failure_value, label=f"Gate {gate_id} failure")
        failure_id = failure.get("failure_id")
        count = failure.get("count")
        refs = failure.get("input_refs")
        if not isinstance(failure_id, str) or not isinstance(count, int) or isinstance(count, bool) or not isinstance(refs, list):
            _fail(f"Gate {gate_id} failure reference is invalid")
        if failure.get("maximum_allowed") != 0 or any(ref not in input_ids for ref in refs):
            _fail(f"Gate {gate_id} hard-failure binding is invalid")
        observed_failures[failure_id] = count
    if observed_failures != expected_failures:
        _fail(f"Gate {gate_id} hard failures are not derived from typed evidence")
    if any(count != 0 for count in observed_failures.values()):
        _fail(f"Gate {gate_id} contains a non-zero raw failure")
    if set(record.kind for record in input_records) != set(allowed):
        _fail(f"Gate {gate_id} does not cover its closed evidence kind mapping")
    # Host composite runs are fixed at exactly three cases per Host.
    if gate_id in {"codex", "opencode"}:
        host_records = [
            record
            for record in input_records
            if record.kind == "host_event_sequence"
            and _mapping(record.derived.get("metrics"), label="Host metrics").get("host") == gate_id
        ]
        if len(host_records) != 3 or {
            _mapping(record.derived.get("metrics"), label="Host metrics").get("task_case")
            for record in host_records
        } != _HOST_TASK_CASES:
            _fail(f"{gate_id} Gate does not cover the three frozen Host task cases")
        if len({record.manifest["run_binding"]["run_id"] for record in host_records}) != 3:
            _fail(f"{gate_id} Host run identities are not distinct")
    if gate_id in {"secret_host_isolation", "timeline"} and len(input_records) != 6:
        _fail(f"Gate {gate_id} must consume all six Host runs")
    return result


def _validate_report(
    path: Path,
    *,
    root: Path,
    classification: Mapping[str, Any],
    classification_sha256: str,
    core_gate_ids: Sequence[str],
    candidate: Mapping[str, str],
    protocol_binding: Mapping[str, Any],
    threshold_binding: Mapping[str, Any],
    reference_binding: Mapping[str, Any],
    records: Sequence[_TypedRecord],
    qualification_run_id: int,
) -> tuple[dict[str, Any], bytes]:
    report, raw = _load_json(path, label="commercial evidence report", schema="commercial-evidence-report.v5.schema.json")
    _require_record(report, label="commercial evidence report", field="report_sha256")
    if (
        report.get("profile") != _PROFILE
        or report.get("reference_provenance") != _REFERENCE_PROVENANCE
        or report.get("human_authenticity") != _HUMAN_AUTHENTICITY
        or report.get("report_kind") != "v013_machine_provenance_bound_gate_collection"
        or report.get("qualification_run_id") != qualification_run_id
        or report.get("machine_qualification_claim_eligible") is not True
        or report.get("human_attested_claim_eligible") is not False
        or report.get("competitive_claim_eligible") is not False
    ):
        _fail("commercial report is not a passed machine-only collection")
    _equal("report candidate", report.get("candidate_binding"), {
        "candidate_commit": candidate["commit"],
        "candidate_tree": candidate["tree"],
        "candidate_wheel_sha256": candidate["wheel_sha256"],
        "candidate_sdist_sha256": candidate["sdist_sha256"],
    })
    _equal("report protocol", report.get("protocol_binding"), protocol_binding)
    _equal("report threshold", report.get("threshold_binding"), threshold_binding)
    _equal("report reference", report.get("reference_binding"), reference_binding)
    class_bind = _mapping(report.get("classification_binding"), label="report classification")
    _equal("report classification", class_bind, {
        "classification_id": _CLASSIFICATION_ID,
        "classification_schema_version": _CLASSIFICATION_SCHEMA_VERSION,
        "classification_sha256": classification_sha256,
    })
    corpora_value = report.get("corpora")
    if not isinstance(corpora_value, list) or len(corpora_value) != 3:
        _fail("report corpora must include candidate, holdout, and blind")
    corpora_by_role: dict[str, Mapping[str, Any]] = {}
    for corpus_value in corpora_value:
        corpus = _mapping(corpus_value, label="report corpus")
        role = corpus.get("role")
        if role in corpora_by_role or role not in {
            "candidate_full",
            "qualification_holdout",
            "final_blind",
        } or corpus.get("frozen") is not True:
            _fail("report corpora are not closed")
        corpora_by_role[str(role)] = corpus
    if set(corpora_by_role) != {
        "candidate_full",
        "qualification_holdout",
        "final_blind",
    }:
        _fail("report corpora are incomplete")
    refs = report.get("gate_results")
    if not isinstance(refs, list) or len(refs) != len(core_gate_ids):
        _fail("commercial report Core Gate inventory is incomplete")
    seen: set[str] = set()
    gate_results: dict[str, dict[str, Any]] = {}
    for ref_value in refs:
        ref = _mapping(ref_value, label="report Gate reference")
        gate_id = ref.get("gate_id")
        if gate_id not in core_gate_ids or gate_id in seen or ref.get("category") != "Core":
            _fail("commercial report Gate reference is not closed")
        seen.add(gate_id)
        artifact = _mapping(ref.get("result"), label=f"report {gate_id} result artifact")
        relative = _safe_relative(artifact.get("relative_path"), label=f"report {gate_id} result path")
        gate_path, gate_raw = _resolve_asset(root, relative, label=f"Gate {gate_id} result")
        if (
            artifact.get("byte_size") != len(gate_raw)
            or artifact.get("file_sha256") != _sha256(gate_raw)
            or artifact.get("schema_version") != _GATE_SCHEMA_VERSION
            or artifact.get("artifact_kind") != "provenance-bound-gate-result"
        ):
            _fail(f"report {gate_id} result artifact binding differs")
        gate, _ = _load_json(gate_path, label=f"Gate {gate_id}", schema="provenance-bound-gate-result.v4.schema.json")
        _equal(f"report {gate_id} result digest", artifact.get("record_sha256"), gate.get("result_sha256"))
        gate_results[gate_id] = _validate_gate_result(
            gate_path,
            root=root,
            gate_id=gate_id,
            classification=classification,
            classification_sha256=classification_sha256,
            core_gate_ids=core_gate_ids,
            candidate=candidate,
            protocol_binding=protocol_binding,
            threshold_binding=threshold_binding,
            reference_binding=reference_binding,
            records=records,
            candidate_run_id=0,
            evidence_run_id=0,
            qualification_run_id=qualification_run_id,
        )
    if seen != set(core_gate_ids):
        _fail("commercial report Core Gate inventory is incomplete")
    observed_corpora = {
        (record.manifest["corpus"].get("role"), record.manifest["corpus"].get("sha256"))
        for record in records
    }
    declared_corpora = {
        (role, corpus.get("sha256")) for role, corpus in corpora_by_role.items()
    }
    if observed_corpora != declared_corpora:
        _fail("report corpora are not transitively bound to typed evidence")
    return report, raw


def _active_external_map(active: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(active.get("external_inputs"), label="active qualification external inputs")


def _validate_active(
    active: Mapping[str, Any],
    *,
    candidate: Mapping[str, str],
    protocol: Mapping[str, Any],
    external: Mapping[str, str],
    binding: Mapping[str, Any],
) -> None:
    if (
        active.get("profile") != _PROFILE
        or active.get("status") != "frozen_exact_candidate_machine_evaluation_pending"
        or active.get("candidate_version") != "0.13.0"
        or active.get("blocker") is not None
        or active.get("release_ready") is not False
        or active.get("claim_eligible") is not False
        or active.get("machine_qualification_claim_eligible") is not False
        or active.get("competitive_claim_eligible") is not False
    ):
        _fail("active qualification is not the frozen exact machine-only candidate")
    active_candidate = _candidate_from_active(active)
    _equal("active candidate", active_candidate, candidate)
    protocol_ref = _mapping(active.get("protocol_binding"), label="active protocol binding")
    _equal("active protocol id", protocol_ref.get("schema_version"), "deeplaw.v013-qualification-protocol/v2")
    _equal("active protocol path", protocol_ref.get("relative_path"), "benchmarks/v013/qualification-protocol-v2.json")
    _equal("active protocol digest", protocol_ref.get("sha256"), protocol["protocol_sha256"])
    active_external = _active_external_map(active)
    panel = _mapping(binding.get("scorer_panel"), label="machine scorer panel")
    scorer_a = _mapping(panel.get("scorer_a"), label="machine scorer A")
    scorer_b = _mapping(panel.get("scorer_b"), label="machine scorer B")
    expected_active_external = {
        "semantic_machine_proposal_sha256": external["semantic_reference_sha256"],
        "qualification_holdout_sha256": external["qualification_holdout_sha256"],
        "final_blind_holdout_sha256": external["final_blind_holdout_sha256"],
        "agent_review_panel_sha256": _sha256(
            _canonical(
                {
                    "agent_roster_sha256": external["agent_roster_sha256"],
                    "agent_consensus_sha256": external["agent_consensus_sha256"],
                    "agent_isolation_sha256": external["agent_isolation_sha256"],
                }
            )
        ),
        "runner_sha256": external["runner_sha256"],
        "scorer_a_sha256": scorer_a.get("sha256"),
        "scorer_b_sha256": scorer_b.get("sha256"),
        "arbitration_sha256": external["arbiter_sha256"],
        "isolation_sha256": external["compiler_scorer_isolation_sha256"],
    }
    if dict(active_external) != expected_active_external:
        _fail("active qualification external bindings are incomplete or differ")


def _validate_supply_chain(
    *,
    root: Path,
    release: Mapping[str, Any],
    pre_publish: Mapping[str, Any],
    candidate: Mapping[str, str],
    active: Mapping[str, Any],
    records: Sequence[_TypedRecord],
) -> None:
    pre_candidate = _mapping(pre_publish.get("candidate"), label="pre-publish candidate")
    _equal("pre-publish candidate commit", pre_candidate.get("commit"), candidate["commit"])
    _equal("pre-publish candidate tree", pre_candidate.get("tree"), candidate["tree"])
    _equal("pre-publish candidate lock", pre_candidate.get("lock_sha256"), candidate["lock_sha256"])
    if pre_publish.get("status") != "pre_publish_passed":
        _fail("pre-publish artifact Gate is not passed")
    builds = _mapping(pre_publish.get("builds"), label="pre-publish builds")
    if builds.get("count") != 2 or builds.get("byte_identical") is not True:
        _fail("pre-publish reproducible builds are not closed")
    release_artifacts = _mapping(release.get("artifact_binding"), label="release artifacts")
    retained = _mapping(pre_publish.get("retained_artifacts"), label="pre-publish retained artifacts")
    _equal("retained manifest", release_artifacts.get("retained_manifest_sha256"), retained.get("manifest_sha256"))
    _manifest_path, manifest_raw = _resolve_asset(
        root,
        retained.get("manifest_path"),
        label="retained artifact manifest",
        expected_sha256=retained.get("manifest_sha256"),
    )
    if not manifest_raw:
        _fail("retained artifact manifest is empty")
    active_candidate = _mapping(
        active.get("candidate_binding"),
        label="active candidate binding",
    )
    _equal(
        "active retained artifact manifest",
        active_candidate.get("artifact_manifest_sha256"),
        _sha256(manifest_raw),
    )
    retained_manifest = _parse_json(manifest_raw, label="retained artifact manifest")
    _validate_schema(
        retained_manifest,
        "retained-candidate-artifacts.v1.schema.json",
        label="retained artifact manifest",
    )
    _equal("retained package version", retained_manifest.get("package_version"), "0.13.0")
    _equal("retained commit", retained_manifest.get("git_commit"), candidate["commit"])
    _equal("retained tree", retained_manifest.get("git_tree"), candidate["tree"])
    _equal("retained lock", retained_manifest.get("lock_sha256"), candidate["lock_sha256"])
    for name, field in (("wheel", "wheel_sha256"), ("sdist", "sdist_sha256")):
        receipt = _mapping(retained.get(name), label=f"retained {name}")
        release_artifact = _mapping(release_artifacts.get(name), label=f"release {name}")
        retained_artifact = _mapping(retained_manifest.get(name), label=f"retained manifest {name}")
        _equal(f"{name} digest", receipt.get("sha256"), release_artifact.get("sha256"), candidate[field])
        _equal(f"{name} manifest digest", retained_artifact.get("sha256"), candidate[field])
        _equal(f"{name} size", receipt.get("byte_size"), release_artifact.get("byte_size"))
        _equal(f"{name} manifest size", retained_artifact.get("bytes"), receipt.get("byte_size"))
        _resolve_asset(
            root,
            receipt.get("retained_path"),
            label=f"retained {name}",
            expected_sha256=candidate[field],
            expected_size=receipt.get("byte_size"),
        )
        _resolve_asset(
            root,
            release_artifact.get("path"),
            label=f"release {name}",
            expected_sha256=candidate[field],
            expected_size=release_artifact.get("byte_size"),
        )
        builds_first = _mapping(builds.get("first"), label="first build")
        builds_second = _mapping(builds.get("second"), label="second build")
        _equal(f"{name} first build", builds_first.get(f"{name}_sha256"), candidate[field])
        _equal(f"{name} second build", builds_second.get(f"{name}_sha256"), candidate[field])
    retained_records = [record for record in records if record.kind == "retained_supply_chain"]
    if len(retained_records) != 1:
        _fail("typed retained supply-chain evidence is not unique")
    retained_metrics = _mapping(
        retained_records[0].derived.get("metrics"),
        label="typed retained supply-chain metrics",
    )
    typed_auxiliary = _mapping(
        retained_metrics.get("auxiliary_sha256"),
        label="typed retained supply-chain sidecars",
    )
    for kind in ("sbom", "openvex", "licenses", "provenance"):
        receipt = _mapping(pre_publish.get(kind), label=f"{kind} receipt")
        _equal(
            f"typed and published {kind} digest",
            typed_auxiliary.get(kind),
            receipt.get("sha256"),
        )
        _resolve_asset(root, receipt.get("path"), label=f"{kind} artifact", expected_sha256=receipt.get("sha256"))


def _protocol_binding(
    root: Path,
    active: Mapping[str, Any],
    *,
    core_gate_ids: Sequence[str],
) -> dict[str, Any]:
    active_ref = _mapping(active.get("protocol_binding"), label="active protocol binding")
    relative = _safe_relative(active_ref.get("relative_path"), label="qualification protocol path")
    candidates = [root / relative, REPOSITORY / relative]
    for path in candidates:
        if path.exists() and path.is_file():
            raw = _regular_file(path, label="qualification protocol")
            if _sha256(raw) != active_ref.get("sha256"):
                _fail("qualification protocol bytes differ from active binding")
            protocol, _ = _load_json(path, label="qualification protocol", schema="v013-qualification-protocol.v2.schema.json")
            if protocol.get("profile") != _PROFILE:
                _fail("qualification protocol is not machine-only")
            _core_require_exact_protocol_gate_ids(
                protocol,
                expected_gate_ids=tuple(core_gate_ids),
                error_type=ReleaseProvenanceV8Error,
            )
            return {
                "protocol_id": protocol.get("protocol_id"),
                "protocol_sha256": _sha256(raw),
                "frozen": True,
            }
    _fail("qualification protocol bytes are unavailable")


def validate_release_provenance(
    release_manifest_path: str | Path,
    *,
    classification_path: str | Path,
    pre_publish_receipt_path: str | Path,
    external_bundle_manifest_path: str | Path,
    active_qualification_path: str | Path,
    assets_root: str | Path,
    candidate_raw_root: str | Path,
    expected_candidate_run_id: int,
    expected_evidence_run_id: int,
    expected_qualification_run_id: int,
    candidate_machine_reference_binding_path: str | Path | None = None,
    candidate_binding_path: str | Path | None = None,
) -> dict[str, Any]:
    """Reopen and verify the complete v8 machine provenance chain."""

    candidate_run = _positive_int(expected_candidate_run_id, label="candidate run id")
    evidence_run = _positive_int(expected_evidence_run_id, label="evidence run id")
    qualification_run = _positive_int(expected_qualification_run_id, label="qualification run id")
    if len({candidate_run, evidence_run, qualification_run}) != 3:
        _fail("candidate, evidence, and qualification run IDs must be distinct")
    binding_path_value = candidate_machine_reference_binding_path or candidate_binding_path
    if binding_path_value is None:
        _fail("post-build machine reference binding path is required")
    root = _safe_root(Path(assets_root), label="assets root")
    release, release_raw = _load_json(Path(release_manifest_path), label="release manifest", schema="commercial-release-manifest.v8.schema.json")
    pre_publish, pre_publish_raw = _load_json(Path(pre_publish_receipt_path), label="pre-publish receipt", schema="pre-publish-artifact-gate.v1.schema.json")
    active, _active_raw = _load_json(Path(active_qualification_path), label="active qualification", schema="v013-active-qualification.v2.schema.json")
    classification, classification_raw, core_gate_ids = _load_classification(Path(classification_path))
    if (
        release.get("profile") != _PROFILE
        or release.get("reference_provenance") != _REFERENCE_PROVENANCE
        or release.get("human_authenticity") != _HUMAN_AUTHENTICITY
    ):
        _fail("release manifest is not the machine-only v8 profile")
    _require_record(release, label="release manifest")
    _require_record(pre_publish, label="pre-publish receipt")
    _equal("release run ids", release.get("run_ids"), {
        "candidate_run_id": candidate_run,
        "evidence_run_id": evidence_run,
        "qualification_run_id": qualification_run,
    })
    external_result: Mapping[str, Any]
    bundle_manifest_path = Path(external_bundle_manifest_path)
    bundle_root = _safe_root(bundle_manifest_path.parent, label="external bundle root")
    try:
        from benchmarks.release.external_qualification_bundle_v4 import validate_external_bundle
        external_result = validate_external_bundle(
            bundle_root,
            expected_candidate_run_id=candidate_run,
            expected_evidence_run_id=evidence_run,
        )
    except Exception as error:
        raise ReleaseProvenanceV8Error("external qualification bundle v4 boundary rejected") from error
    bundle, bundle_raw = _load_json(bundle_manifest_path, label="external bundle manifest", schema="external-qualification-bundle-manifest.v4.schema.json")
    _require_record(bundle, label="external bundle manifest")
    if (
        bundle.get("schema_version") != _BUNDLE_SCHEMA_VERSION
        or bundle.get("profile") != _PROFILE
        or bundle.get("reference_provenance") != _REFERENCE_PROVENANCE
        or bundle.get("human_authenticity") != _HUMAN_AUTHENTICITY
        or bundle.get("candidate_run_id") != candidate_run
        or bundle.get("evidence_run_id") != evidence_run
    ):
        _fail("external bundle identity differs")
    counts = external_result.get("typed_evidence_kind_counts")
    if counts != _REQUIRED_TYPED_COUNTS:
        _fail("external bundle typed evidence inventory is not the closed v8 inventory")
    index = _bundle_index(bundle_root, bundle)
    candidate_binding_path = Path(binding_path_value)
    binding, binding_raw = _load_machine_binding(
        candidate_binding_path,
        bundle_root=bundle_root,
        index=index,
        candidate=_mapping(bundle.get("candidate_binding"), label="bundle candidate"),  # type: ignore[arg-type]
        external_inputs=_mapping(bundle.get("external_inputs"), label="bundle external inputs"),  # type: ignore[arg-type]
    )
    bundle_candidate = _mapping(bundle.get("candidate_binding"), label="bundle candidate")
    candidate_from_bundle = {
        key: bundle_candidate.get(key)
        for key in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256")
    }
    if any(not isinstance(value, str) for value in candidate_from_bundle.values()):
        _fail("bundle candidate binding is incomplete")
    candidate = _candidate_from_release(release)
    _equal("exact candidate", candidate, candidate_from_bundle)
    active_candidate = _candidate_from_active(active)
    _equal("active candidate", active_candidate, candidate)
    release_info = _mapping(release.get("release"), label="release identity")
    _equal("release commit", release_info.get("commit"), candidate["commit"])
    _equal("release tree", release_info.get("tree"), candidate["tree"])
    _equal("release tag", release_info.get("tag"), "v0.13.0")
    _equal("release version", release_info.get("version"), "0.13.0")
    if release.get("release_ready") is not True or release.get("public_release_verified") is not False or release.get("post_public_verification") is not None:
        _fail("release manifest is not a pre-public machine release boundary")
    if (
        release.get("machine_qualification_claim_eligible") is not True
        or release.get("human_attested_claim_eligible") is not False
        or release.get("competitive_claim_eligible") is not False
    ):
        _fail("release claim flags are not the machine-only profile")
    external_inputs = _mapping(bundle.get("external_inputs"), label="bundle external inputs")
    expected_external = _machine_external_from_binding(
        binding,
        binding_sha256=_sha256(binding_raw),
        compiler_scorer_isolation_sha256=external_inputs.get(
            "compiler_scorer_isolation_sha256", ""
        ),
    )
    _equal("bundle external inputs", external_inputs, expected_external)
    manifest_external = _mapping(release.get("external_bindings"), label="release external bindings")
    _equal("release semantic reference", manifest_external.get("semantic_reference_sha256"), expected_external["semantic_reference_sha256"])
    _equal("release machine binding", manifest_external.get("machine_binding_sha256"), expected_external["candidate_binding_sha256"])
    _equal("release holdout", manifest_external.get("holdout_sha256"), expected_external["qualification_holdout_sha256"])
    _equal("release blind", manifest_external.get("blind_sha256"), expected_external["final_blind_holdout_sha256"])
    _equal("release Agent roster", manifest_external.get("agent_roster_sha256"), expected_external["agent_roster_sha256"])
    _equal("release Agent consensus", manifest_external.get("agent_consensus_sha256"), expected_external["agent_consensus_sha256"])
    _equal("release Agent isolation", manifest_external.get("agent_isolation_sha256"), expected_external["agent_isolation_sha256"])
    _equal("release scorer panel", manifest_external.get("scorer_panel_sha256"), expected_external["scorer_panel_sha256"])
    _equal("release arbiter", manifest_external.get("arbiter_sha256"), expected_external["arbiter_sha256"])
    _equal("release runner", manifest_external.get("runner_sha256"), expected_external["runner_sha256"])
    _equal("release isolation", manifest_external.get("isolation_sha256"), expected_external["compiler_scorer_isolation_sha256"])
    candidate_inventory_raw = _validate_candidate_inventory(
        Path(candidate_raw_root),
        bundle_root=bundle_root,
        candidate_run_id=candidate_run,
        candidate_commit=candidate["commit"],
        expected_digest=bundle["candidate_full_raw_inventory_sha256"],
    )
    inventory_entry = _find_entry(
        index,
        evidence_kind="candidate_full_raw_inventory",
        digest=bundle["candidate_full_raw_inventory_sha256"],
        label="Candidate Full raw inventory",
    )
    if inventory_entry.raw != candidate_inventory_raw:
        _fail("Candidate Full raw inventory bytes differ across roots")
    _validate_reference_auxiliary(index, external_inputs=expected_external)
    isolation_matches = [
        entry
        for entry in index.values()
        if entry.reference.get("sha256") == expected_external["compiler_scorer_isolation_sha256"]
        and entry.reference.get("evidence_kind")
        in {"agent_isolation", "sanitized_supporting_receipt"}
    ]
    if len(isolation_matches) != 1:
        _fail("compiler/scorer isolation bytes are not independently retained")
    _semantic_reference, threshold_binding = _load_reference(
        index,
        external_inputs=expected_external,
    )
    reference_binding = {
        "semantic_reference_sha256": expected_external["semantic_reference_sha256"],
        "agent_roster_sha256": expected_external["agent_roster_sha256"],
        "agent_consensus_sha256": expected_external["agent_consensus_sha256"],
        "agent_isolation_sha256": expected_external["agent_isolation_sha256"],
        "scorer_panel_sha256": expected_external["scorer_panel_sha256"],
        "arbiter_sha256": expected_external["arbiter_sha256"],
        "frozen": True,
    }
    _equal("release machine evidence reference binding", _mapping(release.get("machine_evidence"), label="machine evidence").get("reference_binding"), reference_binding)
    records: list[_TypedRecord] = []
    for entry in index.values():
        if entry.reference.get("evidence_kind") in _TYPED_KINDS:
            records.append(_parse_typed_record(
                entry,
                bundle_root=bundle_root,
                candidate=candidate,
                candidate_run_id=candidate_run,
                evidence_run_id=evidence_run,
                external=expected_external,
                binding=binding,
                candidate_inventory_sha256=bundle[
                    "candidate_full_raw_inventory_sha256"
                ],
            ))
    if len(records) != sum(_REQUIRED_TYPED_COUNTS.values()):
        _fail("typed evidence record count differs")
    _equal("typed evidence kind count", {
        kind: sum(record.kind == kind for record in records) for kind in _REQUIRED_TYPED_COUNTS
    }, _REQUIRED_TYPED_COUNTS)
    candidate_identities = _candidate_provenance_identities()
    for record in records:
        if record.kind not in _CANDIDATE_KINDS:
            continue
        expected_identities = candidate_identities[record.kind]
        _equal(
            f"{record.kind} runner provenance",
            record.manifest.get("runner"),
            expected_identities["runner"],
        )
        _equal(
            f"{record.kind} scorer provenance",
            record.manifest.get("scorer"),
            expected_identities["scorer"],
        )
    protocol_binding = _protocol_binding(root, active, core_gate_ids=core_gate_ids)
    _validate_active(
        active,
        candidate=candidate,
        protocol=protocol_binding,
        external=expected_external,
        binding=binding,
    )
    _validate_supply_chain(
        root=root,
        release=release,
        pre_publish=pre_publish,
        candidate=candidate,
        active=active,
        records=records,
    )
    machine_evidence = _mapping(release.get("machine_evidence"), label="machine evidence")
    _equal("machine evidence status", machine_evidence.get("status"), "passed")
    report_path, report_raw = _resolve_asset(
        root,
        machine_evidence.get("report_path"),
        label="commercial evidence report",
        expected_sha256=machine_evidence.get("report_sha256"),
    )
    report, _ = _validate_report(
        report_path,
        root=root,
        classification=classification,
        classification_sha256=_sha256(classification_raw),
        core_gate_ids=core_gate_ids,
        candidate=candidate,
        protocol_binding=protocol_binding,
        threshold_binding=threshold_binding,
        reference_binding=reference_binding,
        records=records,
        qualification_run_id=qualification_run,
    )
    _equal("machine evidence report record", machine_evidence.get("record_sha256"), report.get("report_sha256"))
    if machine_evidence.get("hard_zero") is not True or machine_evidence.get("core_gates_passed") is not True:
        _fail("machine evidence does not prove all Core Gates passed")
    pre_ref = _mapping(release.get("pre_publish_artifact_gate"), label="release pre-publish binding")
    _equal("release pre-publish status", pre_ref.get("status"), "pre_publish_passed")
    _pre_path, pre_raw = _resolve_asset(
        root,
        pre_ref.get("path"),
        label="release pre-publish receipt",
        expected_sha256=pre_ref.get("receipt_sha256"),
    )
    _equal(
        "release pre-publish bytes",
        _sha256(pre_raw),
        _sha256(pre_publish_raw),
    )
    _equal("release machine qualification claim", release.get("machine_qualification_claim_eligible"), True)
    return _derived_validation_receipt(
        release=release,
        release_raw=release_raw,
        bundle=bundle,
        bundle_raw=bundle_raw,
        classification=classification,
        classification_raw=classification_raw,
        candidate=candidate,
        expected_external=expected_external,
        report=report,
        report_raw=report_raw,
        binding_raw=binding_raw,
        records=records,
        candidate_run_id=candidate_run,
        evidence_run_id=evidence_run,
        qualification_run_id=qualification_run,
    )


def _derived_validation_receipt(
    *,
    release: Mapping[str, Any],
    release_raw: bytes,
    bundle: Mapping[str, Any],
    bundle_raw: bytes,
    classification: Mapping[str, Any],
    classification_raw: bytes,
    candidate: Mapping[str, str],
    expected_external: Mapping[str, str],
    report: Mapping[str, Any],
    report_raw: bytes,
    binding_raw: bytes,
    records: Sequence[_TypedRecord],
    candidate_run_id: int,
    evidence_run_id: int,
    qualification_run_id: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "deeplaw.release-provenance-validation/v8",
        "status": "transitive_provenance_validated",
        "authorization_stage": "pre_public_machine_qualification",
        "candidate_run_id": candidate_run_id,
        "evidence_run_id": evidence_run_id,
        "qualification_run_id": qualification_run_id,
        "profile": _PROFILE,
        "reference_provenance": _REFERENCE_PROVENANCE,
        "human_authenticity": _HUMAN_AUTHENTICITY,
        "candidate": dict(candidate),
        "classification": {
            "classification_id": classification["classification_id"],
            "schema_version": classification["schema_version"],
            "sha256": _sha256(classification_raw),
        },
        "external_input_hashes": dict(expected_external),
        "artifact_hashes": {
            "release_manifest_sha256": _sha256(release_raw),
            "external_bundle_manifest_sha256": _sha256(bundle_raw),
            "machine_reference_binding_sha256": _sha256(binding_raw),
            "semantic_report_sha256": _sha256(report_raw),
        },
        "checked": {
            "typed_evidence_records": len(records),
            "core_gate_count": 14,
            "human_gold_inputs": 0,
            "public_release_verified": False,
            "byte_reopened": True,
        },
        "release_ready": True,
        "machine_qualification_claim_eligible": True,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
        "public_release_verified": False,
        "post_public_verification": None,
    }
    result["record_sha256"] = _record_digest(result)
    return result


def verify_release_provenance(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Versioned compatibility alias."""

    return validate_release_provenance(*args, **kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--pre-publish-receipt", type=Path, required=True)
    parser.add_argument("--candidate-machine-reference-binding", type=Path, required=True)
    parser.add_argument("--external-bundle-manifest", type=Path, required=True)
    parser.add_argument("--active-qualification", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--candidate-raw-root", type=Path, required=True)
    parser.add_argument("--candidate-run-id", type=int, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    parser.add_argument("--qualification-run-id", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = validate_release_provenance(
            args.release_manifest,
            classification_path=args.classification,
            pre_publish_receipt_path=args.pre_publish_receipt,
            candidate_machine_reference_binding_path=args.candidate_machine_reference_binding,
            external_bundle_manifest_path=args.external_bundle_manifest,
            active_qualification_path=args.active_qualification,
            assets_root=args.assets_root,
            candidate_raw_root=args.candidate_raw_root,
            expected_candidate_run_id=args.candidate_run_id,
            expected_evidence_run_id=args.evidence_run_id,
            expected_qualification_run_id=args.qualification_run_id,
        )
    except (OSError, ReleaseProvenanceV8Error, ValueError):
        print("release provenance v8 validation failed", file=sys.stderr)
        return 1
    print(_canonical(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
