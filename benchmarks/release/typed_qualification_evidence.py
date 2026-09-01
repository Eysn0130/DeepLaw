"""Fail-closed parsers for the current v8 qualification evidence seam.

The input to this module is an evidence manifest, not a result report.  The
manifest identifies immutable raw receipts (and, for Candidate Full, the raw
JUnit XML); this module verifies those bytes and derives the only values that
may be consumed by a qualification validator.  A caller cannot make a gate
pass by adding "passed", "facts", or an aggregate count to a receipt.

This module has no network, model, Host, credential, or Ledger side effects.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from defusedxml import ElementTree as DefusedET
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.qualification_artifact_safety import (
    ABSOLUTE_PATH_RE as _ABSOLUTE_PATH_RE,
)
from benchmarks.release.qualification_artifact_safety import MAX_SOURCE_BYTES
from benchmarks.release.qualification_artifact_safety import (
    SECRET_MARKER_RE as _SECRET_RE,
)
from benchmarks.release.security_domain_receipt import (
    ROLES as _SECURITY_DOMAIN_ROLES,
)
from benchmarks.release.security_domain_receipt import (
    SecurityDomainReceiptError as _SecurityDomainReceiptError,
)
from benchmarks.release.security_domain_receipt import (
    security_domain_set_sha256 as _security_domain_set_sha256,
)
from benchmarks.release.security_domain_receipt import (
    validate_security_domain_receipt as _validate_security_domain_receipt,
)
from deeplaw.native_host import (
    NativeHostObservationError,
    derive_native_host_receipt,
    parse_native_host_event,
)

SCHEMA_VERSION = "deeplaw.typed-qualification-evidence/v1"
SCHEMA_V2_VERSION = "deeplaw.typed-qualification-evidence/v2"
SCHEMA_V3_VERSION = "deeplaw.typed-qualification-evidence/v3"
DERIVED_SCHEMA_VERSION = "deeplaw.typed-qualification-derived/v1"
DERIVED_V2_SCHEMA_VERSION = "deeplaw.typed-qualification-derived/v2"
DERIVED_V3_SCHEMA_VERSION = "deeplaw.typed-qualification-derived/v3"
PACKAGE_NAME = "deeplaw"
_V2_COMPATIBLE_SCHEMA_VERSIONS = frozenset({SCHEMA_V2_VERSION, SCHEMA_V3_VERSION})
_V3_PROFESSIONAL_CASE_TYPES = frozenset(
    {
        "exact_source_locator",
        "wrong_version_rejection",
        "false_authority_rejection",
        "effective_date_exception_proviso_cross_reference",
        "ocr_critical_token_gap",
        "wiki_exact_source_drill_down",
    }
)
_V3_PROFESSIONAL_DUTIES = (
    "original_bytes",
    "original_hash",
    "document",
    "version",
    "fragment",
    "locator",
    "wrong_version_rejection",
    "effective_date",
    "exception",
    "proviso",
    "cross_reference",
    "false_authority",
    "ocr_critical_token_gap",
    "wiki_exact_source_drill_down",
)
_V3_PROFESSIONAL_HARD_FAILURES = (
    "original_bytes_mismatch",
    "original_hash_mismatch",
    "document_identity_mismatch",
    "version_identity_mismatch",
    "fragment_identity_mismatch",
    "locator_invalid",
    "wrong_version_rejection_failure",
    "effective_date_mismatch",
    "exception_mismatch",
    "proviso_mismatch",
    "cross_reference_mismatch",
    "false_authority_admission",
    "ocr_critical_token_gap_missing",
    "wiki_exact_source_drill_down_failure",
    "expected_observed_mismatch",
)
_LEGACY_CORPUS_ROLES = frozenset({"candidate_full", "qualification_holdout", "final_blind"})
_V3_CORPUS_ROLES = frozenset(
    {
        "candidate_full",
        "candidate_platform",
        "host_qualification",
        "professional_evidence",
        "living_wiki",
        "scale_10000",
        "supply_chain",
    }
)
_REQUIRED_CANDIDATE_FULL_IDENTITIES = frozenset(
    {
        (
            "tests.test_knowledge_control",
            "test_interrupted_migration_rolls_back_and_retains_a_verified_backup",
        ),
        (
            "tests.test_v013_pass22_continuity_closure",
            "test_partial_checkpoint_recovers_after_process_exit_and_restart",
        ),
    }
)
_PLATFORM_MANIFEST_SOURCE_SHA256 = (
    "76802b1082e1672167dcd84432d592717a52c733b20315271e185fde6fdf7e86"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = frozenset(
    {
        "passed",
        "pass",
        "facts",
        "fact",
        "aggregate",
        "aggregates",
        "counts",
        "count",
        "total",
        "total_count",
        "row_count",
        "case_count",
        "failure_count",
        "failed_count",
        "passed_count",
        "skip_count",
        "skipped_count",
        "error_count",
        "success_count",
    }
)
_COUNT_KEY_RE = re.compile(r"(?:^|_)(?:count|counts)$")
_RAW_JSON_POSIX_PATH_RE = re.compile(
    r"""(?<![A-Za-z0-9_:/\[])/(?:Users|home|root|private|tmp|var|etc|opt|workspace|Volumes|System|Library|bin|sbin|usr|dev|proc|sys|run|mnt)(?:/|[\s"']|$)""",
    re.VERBOSE,
)
_SECRET_ENV_KEY_RE = re.compile(
    r"(?:auth|credential|secret|password|passwd|api[_-]?key|private[_-]?key|"
    r"access[_-]?token|token|bearer)",
    re.IGNORECASE,
)


class TypedQualificationEvidenceError(ValueError):
    """Raised when raw qualification evidence is absent, forged, or unsafe."""


class _SourceData(NamedTuple):
    ref: Mapping[str, Any]
    path: Path
    raw: bytes


def _fail(message: str) -> None:
    raise TypedQualificationEvidenceError(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise TypedQualificationEvidenceError("evidence is not canonical JSON") from exc


def _record_digest(value: Mapping[str, Any], *, field: str = "record_sha256") -> str:
    body = dict(value)
    declared = body.pop(field, None)
    if not isinstance(declared, str) or _SHA256_RE.fullmatch(declared) is None:
        _fail(f"{field} is not a lowercase SHA-256 digest")
    observed = _sha256_bytes(_canonical(body))
    if declared != observed:
        _fail(f"{field} does not match canonical receipt bytes")
    return observed


def _strict_json(raw: bytes, *, label: str) -> Any:
    if not isinstance(raw, bytes) or not raw:
        _fail(f"{label} is empty")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                _fail(f"{label} contains duplicate JSON keys")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        _fail(f"{label} contains non-finite JSON value: {value}")

    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, TypedQualificationEvidenceError):
            raise
        raise TypedQualificationEvidenceError(f"{label} is not strict UTF-8 JSON") from exc

    def reject_nonfinite(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            _fail(f"{label} contains a non-finite number")
        if isinstance(item, Mapping):
            for child in item.values():
                reject_nonfinite(child)
        elif isinstance(item, list):
            for child in item:
                reject_nonfinite(child)

    reject_nonfinite(value)
    return value


def _forbidden_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _FORBIDDEN_KEYS or bool(_COUNT_KEY_RE.search(lowered))


def _reject_forbidden_keys(
    value: Any,
    *,
    path: tuple[str, ...] = (),
    allow_count_paths: frozenset[tuple[str, ...]] = frozenset(),
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("evidence object key is not a string")
            key_path = (*path, key)
            if _forbidden_key(key) and key_path not in allow_count_paths:
                _fail(f"caller-authored aggregate/pass field is forbidden: {'.'.join(key_path)}")
            _reject_forbidden_keys(
                item,
                path=key_path,
                allow_count_paths=allow_count_paths,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_keys(
                item,
                path=(*path, str(index)),
                allow_count_paths=allow_count_paths,
            )


def _reject_v3_competitive_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {
                "machine_reference_scorer",
                "scorer_panel",
                "arbiter",
                "scorer_a",
                "scorer_b",
                "agent_review_panel",
                "agent_consensus",
                "machine_reference",
                "machine_reference_isolation",
                "qualification_holdout",
                "qualification_comparative_holdout",
                "final_blind",
                "final_blind_comparative_holdout",
                "comparative_incremental_benefit",
                "superiority",
                "sota",
            }:
                _fail("v3 Kernel Release Core evidence contains competitive scorer fields")
            _reject_v3_competitive_fields(item)
    elif isinstance(value, list):
        for item in value:
            _reject_v3_competitive_fields(item)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_contract(filename: str) -> dict[str, Any]:
    path = _repository_root() / "contracts" / filename
    if path.is_symlink() or not path.is_file():
        _fail(f"required contract is unavailable: {filename}")
    try:
        value = _strict_json(path.read_bytes(), label=filename)
        if not isinstance(value, dict):
            _fail(f"contract is not an object: {filename}")
        Draft202012Validator.check_schema(value)
        return value
    except OSError as exc:
        raise TypedQualificationEvidenceError(f"contract could not be read: {filename}") from exc


def _validate_contract(value: Any, filename: str, *, label: str) -> None:
    schema = _load_contract(filename)
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        _fail(f"{label} failed strict schema validation: {errors[0].message}")


def _regular_directory(path: Path, *, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        _fail(f"{label} must be a regular non-symlink directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TypedQualificationEvidenceError(f"{label} could not be resolved") from exc
    if resolved.is_symlink() or not resolved.is_dir():
        _fail(f"{label} is not a regular directory")
    return resolved


def _prepare_root(source: Path, root: Path | str | None) -> tuple[Path, Path, bool]:
    selected = Path(source).expanduser()
    if selected.is_symlink() or not selected.is_file():
        _fail("typed evidence manifest must be a regular non-symlink file")
    try:
        manifest = selected.resolve(strict=True)
    except OSError as exc:
        raise TypedQualificationEvidenceError("typed evidence manifest is unavailable") from exc
    explicit_root = root is not None
    root_path = _regular_directory(
        Path(root) if root is not None else manifest.parent,
        label="evidence root",
    )
    try:
        manifest.relative_to(root_path)
    except ValueError as exc:
        raise TypedQualificationEvidenceError("manifest is outside evidence root") from exc
    return manifest, root_path, explicit_root


def _relative_source_path(relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        _fail("evidence source path must be a safe relative POSIX path")
    selected = Path(relative)
    if selected.is_absolute() or not selected.parts or any(
        part in {".", ".."} for part in selected.parts
    ):
        _fail("evidence source path escapes its root")
    if len(relative) > 512 or "\x00" in relative:
        _fail("evidence source path is invalid")
    return selected


def _source_data(
    ref: Mapping[str, Any],
    *,
    root: Path,
    label: str,
    media_type: str | None = None,
) -> _SourceData:
    if not isinstance(ref, Mapping):
        _fail(f"{label} source is not an object")
    if set(ref) != {"relative_path", "byte_size", "sha256", "media_type"}:
        _fail(f"{label} source keys are not closed")
    relative = _relative_source_path(ref["relative_path"])
    if media_type is not None and ref["media_type"] != media_type:
        _fail(f"{label} source media type mismatch")
    digest = ref["sha256"]
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        _fail(f"{label} source digest is invalid")
    size = ref["byte_size"]
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_SOURCE_BYTES:
        _fail(f"{label} source byte size is invalid")
    selected = root / relative
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            _fail(f"{label} source path contains a symlink")
    try:
        resolved = selected.resolve(strict=True)
    except OSError as exc:
        raise TypedQualificationEvidenceError(f"{label} source is unavailable") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TypedQualificationEvidenceError(f"{label} source escapes its root") from exc
    if resolved.is_symlink() or not resolved.is_file():
        _fail(f"{label} source is not a regular non-symlink file")
    try:
        observed_size = resolved.stat().st_size
        raw = resolved.read_bytes()
    except OSError as exc:
        raise TypedQualificationEvidenceError(f"{label} source could not be read") from exc
    if observed_size != size or len(raw) != size:
        _fail(f"{label} source byte size differs from its manifest")
    if _sha256_bytes(raw) != digest:
        _fail(f"{label} source digest differs from its manifest")
    return _SourceData(ref, resolved, raw)


def _source_json(
    ref: Mapping[str, Any],
    *,
    root: Path,
    label: str,
    allow_count_paths: frozenset[tuple[str, ...]] = frozenset(),
    allow_frozen_identity_literals: bool = False,
) -> tuple[Any, _SourceData]:
    source = _source_data(ref, root=root, label=label, media_type="application/json")
    if not allow_frozen_identity_literals:
        _safe_artifact_bytes(source, label=label, json_document=True)
    value = _strict_json(source.raw, label=label)
    _reject_projection_strings(
        value,
        label=label,
        allow_frozen_identity_literals=allow_frozen_identity_literals,
    )
    _reject_forbidden_keys(value, allow_count_paths=allow_count_paths)
    return value, source


def _source_xml(
    ref: Mapping[str, Any],
    *,
    root: Path,
    label: str,
    allow_frozen_identity_literals: bool = False,
) -> _SourceData:
    source = _source_data(ref, root=root, label=label, media_type="application/xml")
    if not allow_frozen_identity_literals:
        _safe_artifact_bytes(source, label=label)
    return source


def _safe_artifact_bytes(
    source: _SourceData,
    *,
    label: str,
    json_document: bool = False,
) -> None:
    try:
        text = source.raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise TypedQualificationEvidenceError(
            f"{label} sanitized evidence must be strict UTF-8"
        ) from exc
    path_pattern = _RAW_JSON_POSIX_PATH_RE if json_document else _ABSOLUTE_PATH_RE
    if path_pattern.search(text) or _SECRET_RE.search(text):
        _fail(f"{label} contains a secret or local absolute path")


def _reject_projection_strings(
    value: Any,
    *,
    label: str,
    path: tuple[str, ...] = (),
    allow_frozen_identity_literals: bool = False,
) -> None:
    if isinstance(value, str):
        if allow_frozen_identity_literals and (
            path[-1:] == ("node_id",)
            or path[-2:] in {("junit", "classname"), ("junit", "name")}
        ):
            return
        if _ABSOLUTE_PATH_RE.search(value) or _SECRET_RE.search(value):
            _fail(f"{label} contains a secret or local absolute path")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _reject_projection_strings(
                item,
                label=label,
                path=(*path, str(key)),
                allow_frozen_identity_literals=allow_frozen_identity_literals,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_projection_strings(
                item,
                label=label,
                path=(*path, str(index)),
                allow_frozen_identity_literals=allow_frozen_identity_literals,
            )


def _check_manifest_closure(
    *,
    root: Path,
    manifest: Path,
    referenced: Sequence[Path],
) -> None:
    allowed = {path.resolve(strict=True) for path in referenced}
    allowed.add(manifest.resolve(strict=True))
    try:
        entries = list(root.rglob("*"))
    except OSError as exc:
        raise TypedQualificationEvidenceError("evidence root could not be enumerated") from exc
    for path in entries:
        if path.is_symlink():
            _fail("evidence root contains an untrusted symlink")
    for path in entries:
        if not path.is_file():
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise TypedQualificationEvidenceError(
                "evidence root contains an unavailable file"
            ) from exc
        if resolved not in allowed:
            _fail(f"unreferenced evidence file is present: {path.relative_to(root).as_posix()}")


def _source_refs(kind: str, payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    mapping: dict[str, list[str]] = {
        "candidate_full_junit": ["source"],
        "candidate_platform_receipt": ["source", "platform_manifest_source"],
        "host_event_sequence": [
            "event_source",
            "lifecycle_source",
            "usage_source",
            "expected_source",
            "continuity_source",
            "isolation_source",
        ],
        "exact_wheel_execution": ["source"],
        "human_gold_scorer": [
            "semantic_gold_source",
            "candidate_binding_source",
            "scorer_rows_source",
            "human_attestation_source",
        ],
        "machine_reference_scorer": [
            "candidate_output_source",
            "candidate_execution_source",
            "semantic_reference_source",
            "candidate_binding_source",
            "agent_roster_source",
            "agent_consensus_source",
            "agent_isolation_source",
            "scorer_a_rows_source",
            "scorer_b_rows_source",
            "arbiter_consensus_rows_source",
        ],
        "legal_rows": [
            "source_catalog_source",
            "expected_source",
            "observed_source",
        ],
        "professional_evidence_rows": [
            "source_catalog_source",
            "expected_source",
            "observed_source",
        ],
        "wiki_journey_rows": ["expected_source", "observed_source"],
        "context_capsule_selection_usage": [
            "expected_source",
            "provider_capsule_source",
            "query_trace_source",
            "ledger_source",
            "usage_source",
        ],
        "scale_report": ["expected_source", "observed_source"],
        "retained_supply_chain": [
            "candidate_build_source",
            "retained_candidate_source",
            "pre_publish_receipt_source",
            "wheel_source",
            "sdist_source",
            "sbom_source",
            "openvex_source",
            "licenses_source",
            "provenance_source",
        ],
    }
    names = mapping.get(kind)
    if names is None:
        _fail("typed evidence kind is unsupported")
    refs = [payload[name] for name in names]
    if kind == "candidate_platform_receipt":
        descriptors = payload.get("junit_sources")
        if not isinstance(descriptors, list):
            _fail("Candidate Platform JUnit source descriptors are missing")
        for index, descriptor in enumerate(descriptors):
            item = _require_mapping(
                descriptor,
                label=f"Candidate Platform JUnit source[{index}]",
                keys={"platform", "python_version", "source"},
            )
            refs.append(item["source"])
    elif kind == "machine_reference_scorer":
        security_receipts = payload.get("security_domain_receipt_sources")
        if not isinstance(security_receipts, list) or len(security_receipts) != 5:
            _fail("Machine reference security-domain receipt sources are incomplete")
        process_receipts = payload.get("process_receipt_sources")
        if not isinstance(process_receipts, list) or len(process_receipts) != 6:
            _fail("Machine reference process receipt sources are incomplete")
        refs.extend(security_receipts)
        refs.extend(process_receipts)
    return refs


def _validate_envelope(
    envelope: Mapping[str, Any],
    *,
    expected_candidate: Mapping[str, Any] | None,
    expected_run_id: str | None,
    expected_workflow_run_id: int | None,
    expected_corpus_sha256: str | None,
    expected_runner: Mapping[str, Any] | None,
    expected_scorer: Mapping[str, Any] | None,
) -> tuple[str, str]:
    schema_version = envelope.get("schema_version")
    schema_name = {
        SCHEMA_VERSION: "typed-qualification-evidence.v1.schema.json",
        SCHEMA_V2_VERSION: "typed-qualification-evidence.v2.schema.json",
        SCHEMA_V3_VERSION: "typed-qualification-evidence.v3.schema.json",
    }.get(schema_version)
    if schema_name is None:
        _fail("typed evidence schema version is unsupported")
    # Keep the failure specific when a v2 machine scorer omits the retained
    # candidate bytes.  This is both easier to audit and prevents the generic
    # JSON-Schema error from hiding the evidence-boundary defect.
    if schema_version == SCHEMA_V2_VERSION and envelope.get("kind") == "machine_reference_scorer":
        payload = envelope.get("payload")
        if not isinstance(payload, Mapping):
            _fail("candidate output and execution sources are required")
        for field, label in (
            ("candidate_output_source", "candidate output"),
            ("candidate_execution_source", "candidate execution"),
        ):
            if field not in payload:
                _fail(f"{label} source is required for machine reference scoring")
        process_receipts = payload.get("process_receipt_sources")
        if not isinstance(process_receipts, list) or len(process_receipts) != 6:
            _fail(
                "process receipt sources are required for machine reference scoring"
            )
    _validate_contract(envelope, schema_name, label="typed evidence")
    kind = envelope.get("kind")
    if not isinstance(kind, str):
        _fail("typed evidence kind is missing")
    candidate = envelope["candidate_binding"]
    if expected_candidate is not None:
        for field in ("commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"):
            if candidate.get(field) != expected_candidate.get(field):
                _fail(f"candidate binding mismatch: {field}")
    if expected_run_id is not None and envelope["run_binding"]["run_id"] != expected_run_id:
        _fail("qualification run binding mismatch")
    if (
        expected_workflow_run_id is not None
        and envelope["run_binding"]["workflow_run_id"] != expected_workflow_run_id
    ):
        _fail("qualification workflow run binding mismatch")
    if (
        expected_corpus_sha256 is not None
        and envelope["corpus"]["sha256"] != expected_corpus_sha256
    ):
        _fail("corpus binding mismatch")
    for label, observed, expected in (
        ("runner", envelope["runner"], expected_runner),
        ("scorer", envelope["scorer"], expected_scorer),
    ):
        if expected is not None and (
            observed.get("identity") != expected.get("identity")
            or observed.get("sha256") != expected.get("sha256")
        ):
            _fail(f"{label} identity binding mismatch")
    record = _record_digest(envelope)
    return kind, record


def _require_mapping(
    value: Any,
    *,
    label: str,
    keys: set[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    if keys is not None and set(value) != keys:
        _fail(f"{label} keys are not closed")
    return value


def _require_rows(value: Any, *, label: str) -> list[Mapping[str, Any]]:
    wrapper = _require_mapping(value, label=label, keys={"rows"})
    rows = wrapper["rows"]
    if not isinstance(rows, list) or not rows:
        _fail(f"{label}.rows must contain real rows")
    return [
        _require_mapping(row, label=f"{label}.rows[{index}]")
        for index, row in enumerate(rows)
    ]


def _receipt_metadata(
    value: Any,
    *,
    envelope: Mapping[str, Any],
    label: str,
) -> Mapping[str, Any]:
    """Validate the external receipt binding carried beside raw rows.

    The envelope is the only authority for candidate, run, corpus, runner,
    and scorer identity.  A raw producer may repeat those values, but cannot
    choose a different candidate or silently omit workflow/scorer binding.
    """

    receipt = _require_mapping(
        value,
        label=f"{label}.receipt",
        keys={"candidate", "run", "corpus", "runner", "scorer"},
    )
    candidate = _require_mapping(
        receipt["candidate"],
        label=f"{label}.receipt.candidate",
        keys={"commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"},
    )
    expected_candidate = envelope["candidate_binding"]
    if dict(candidate) != dict(expected_candidate):
        _fail(f"{label} receipt is bound to a different candidate")
    run = _require_mapping(
        receipt["run"],
        label=f"{label}.receipt.run",
        keys={"run_id", "workflow_run_id"},
    )
    if dict(run) != dict(envelope["run_binding"]):
        _fail(f"{label} receipt is bound to a different run or workflow")
    corpus = _require_mapping(
        receipt["corpus"],
        label=f"{label}.receipt.corpus",
        keys={"sha256", "role"},
    )
    if dict(corpus) != dict(envelope["corpus"]):
        _fail(f"{label} receipt is bound to a different corpus")
    for identity_label in ("runner", "scorer"):
        identity = _require_mapping(
            receipt[identity_label],
            label=f"{label}.receipt.{identity_label}",
            keys={"identity", "sha256"},
        )
        if dict(identity) != dict(envelope[identity_label]):
            _fail(f"{label} receipt is bound to a different {identity_label}")
    return receipt


def _receipt_rows(
    value: Any,
    *,
    envelope: Mapping[str, Any],
    label: str,
    field: str = "rows",
) -> list[Mapping[str, Any]]:
    wrapper = _require_mapping(value, label=label, keys={"receipt", field})
    _receipt_metadata(wrapper["receipt"], envelope=envelope, label=label)
    rows = wrapper[field]
    if not isinstance(rows, list) or not rows:
        _fail(f"{label}.{field} must contain real rows")
    return [
        _require_mapping(row, label=f"{label}.{field}[{index}]")
        for index, row in enumerate(rows)
    ]


def _number(value: Any, *, label: str, minimum: float = 0.0) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        _fail(f"{label} must be a finite number")
    if value < minimum:
        _fail(f"{label} must not be below {minimum}")
    return value


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail(f"{label} is not a SHA-256 digest")
    return value


def _identity_digest(values: Iterable[Any]) -> str:
    return _sha256_bytes(_canonical(list(values)))


def _derived(
    kind: str,
    metrics: Mapping[str, Any],
    failures: Mapping[str, int],
    record_sha256: str,
) -> dict[str, Any]:
    normalized = {str(key): int(value) for key, value in failures.items()}
    return {
        "schema_version": DERIVED_SCHEMA_VERSION,
        "kind": kind,
        "status": "passed" if sum(normalized.values()) == 0 else "failed",
        "metrics": dict(metrics),
        "hard_failure_counts": normalized,
        "evidence_record_sha256": record_sha256,
    }


def _parse_junit(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
) -> dict[str, Any]:
    observations = _junit_observations(
        envelope["payload"]["source"],
        root=root,
        label="Candidate Full JUnit",
    )
    identities = list(observations)
    failure = sum(item == "failure" for item in observations.values())
    skipped = sum(item == "skip" for item in observations.values())
    observed_identities = {
        tuple(identity.split("::", 1))
        for identity in identities
        if "::" in identity
    }
    missing_required = sorted(
        "::".join(identity)
        for identity in _REQUIRED_CANDIDATE_FULL_IDENTITIES - observed_identities
    )
    recovery_failed = failure + skipped + len(missing_required)
    return _derived(
        "candidate_full_junit",
        {
            "testcase_count": len(identities),
            "successful_testcase_count": len(identities) - failure - skipped,
            "identity_sha256": _identity_digest(identities),
            "required_identity_sha256": _identity_digest(
                sorted(_REQUIRED_CANDIDATE_FULL_IDENTITIES)
            ),
            "required_identity_count": len(_REQUIRED_CANDIDATE_FULL_IDENTITIES),
            "required_identity_present_count": len(
                _REQUIRED_CANDIDATE_FULL_IDENTITIES & observed_identities
            ),
            "required_identity_missing": missing_required,
            "recovery_pass_rate": 1.0 if recovery_failed == 0 else 0.0,
        },
        {
            "junit_failure": failure,
            "junit_skip": skipped,
            "junit_required_case_missing": len(missing_required),
            "recovery_data_loss": recovery_failed,
        },
        record_sha256,
    )


def _junit_observations(
    ref: Mapping[str, Any],
    *,
    root: Path,
    label: str,
) -> dict[str, str]:
    source = _source_xml(
        ref,
        root=root,
        label=label,
        allow_frozen_identity_literals=True,
    )
    try:
        root_element = DefusedET.fromstring(source.raw)
    except Exception as exc:
        raise TypedQualificationEvidenceError(
            "Candidate Full JUnit XML is invalid or unsafe"
        ) from exc

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    testcases = [
        item for item in root_element.iter() if local_name(item.tag) == "testcase"
    ]
    for element in root_element.iter():
        element_name = local_name(element.tag)
        for key, value in element.attrib.items():
            if element_name == "testcase" and key in {"classname", "name"}:
                continue
            _reject_projection_strings(value, label=label)
        if element.text:
            _reject_projection_strings(element.text, label=label)
        if element.tail:
            _reject_projection_strings(element.tail, label=label)
    if not testcases:
        _fail("Candidate Full JUnit contains no real testcase elements")
    observations: dict[str, str] = {}
    for index, testcase in enumerate(testcases):
        classname = testcase.attrib.get("classname")
        name = testcase.attrib.get("name")
        if not classname or not name:
            _fail(f"Candidate Full JUnit testcase[{index}] lacks identity")
        identity = f"{classname}::{name}"
        if identity in observations:
            _fail("Candidate Full JUnit contains duplicate testcase identity")
        children = {local_name(child.tag) for child in testcase}
        if "failure" in children or "error" in children:
            outcome = "failure"
        elif "skipped" in children:
            outcome = "skip"
        else:
            outcome = "success"
        observations[identity] = outcome
    return observations


def _platform_rows(
    value: Any,
    *,
    envelope: Mapping[str, Any],
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    wrapper = _require_mapping(
        value,
        label="Candidate Platform receipt",
        keys={"receipt", "rows"},
    )
    _receipt_metadata(
        wrapper["receipt"],
        envelope=envelope,
        label="Candidate Platform receipt",
    )
    rows = wrapper["rows"]
    if not isinstance(rows, list) or not rows:
        _fail("Candidate Platform receipt rows are missing")
    return wrapper, [
        _require_mapping(row, label=f"platform row {index}")
        for index, row in enumerate(rows)
    ]


def _platform_manifest_expectations(
    ref: Mapping[str, Any],
    *,
    root: Path,
) -> tuple[dict[str, set[tuple[str, str]]], str, str]:
    manifest, source = _source_json(
        ref,
        root=root,
        label="Platform core test manifest",
        allow_count_paths=frozenset(
            {
                ("inventories", "common", "count"),
                ("inventories", "windows", "count"),
            }
        ),
        allow_frozen_identity_literals=True,
    )
    if source.ref["sha256"] != _PLATFORM_MANIFEST_SOURCE_SHA256:
        _fail("Platform core test manifest is not the frozen tracked bytes")
    _validate_contract(
        manifest,
        "platform-core-test-manifest.v2.schema.json",
        label="Platform core test manifest",
    )
    if not isinstance(manifest, Mapping):
        _fail("Platform core test manifest is not an object")
    manifest_digest = _sha(manifest["manifest_sha256"], label="platform manifest digest")
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    if manifest_digest != _sha256_bytes(_canonical(body)):
        _fail("Platform core test manifest digest differs from its bytes")
    common = manifest["inventories"]["common"]
    windows = manifest["inventories"]["windows"]

    def identities(cases: Any, *, label: str) -> set[tuple[str, str]]:
        if not isinstance(cases, list) or not cases:
            _fail(f"{label} cases are missing")
        values: list[tuple[str, str]] = []
        for index, case in enumerate(cases):
            item = _require_mapping(case, label=f"{label}[{index}]")
            junit = _require_mapping(
                item.get("junit"),
                label=f"{label}[{index}].junit",
                keys={"classname", "name"},
            )
            if any(
                not isinstance(junit[key], str) or not junit[key]
                for key in ("classname", "name")
            ):
                _fail(f"{label}[{index}] JUnit identity is invalid")
            values.append((junit["classname"], junit["name"]))
        if len(values) != len(set(values)):
            _fail(f"{label} contains duplicate JUnit identities")
        return set(values)

    common_identities = identities(common["cases"], label="Platform common inventory")
    windows_identities = identities(
        [*common["cases"], *windows["additional_cases"]],
        label="Platform Windows inventory",
    )
    common_digest = _sha256_bytes(_canonical(common["cases"]))
    windows_digest = _sha256_bytes(
        _canonical([*common["cases"], *windows["additional_cases"]])
    )
    if (
        common["count"] != len(common["cases"])
        or common["sha256"] != common_digest
        or windows["count"] != len(windows_identities)
        or windows["sha256"] != windows_digest
    ):
        _fail("Platform core test manifest inventory digest or count is invalid")
    if not windows_identities.issuperset(common_identities):
        _fail("Platform Windows inventory does not extend common inventory")
    return (
        {
            "ubuntu": common_identities,
            "macos": common_identities,
            "windows": windows_identities,
        },
        source.ref["sha256"],
        manifest_digest,
    )


def _platform_junit_sources(
    payload: Mapping[str, Any],
    *,
    root: Path,
) -> tuple[list[Mapping[str, Any]], dict[str, tuple[str, str, dict[str, str]]]]:
    descriptors = payload.get("junit_sources")
    if not isinstance(descriptors, list) or len(descriptors) != 9:
        _fail("Candidate Platform receipt must retain exactly nine raw JUnit sources")
    seen_sources: set[str] = set()
    by_digest: dict[str, tuple[str, str, dict[str, str]]] = {}
    cells: set[tuple[str, str]] = set()
    for index, descriptor in enumerate(descriptors):
        item = _require_mapping(
            descriptor,
            label=f"Candidate Platform JUnit source[{index}]",
            keys={"platform", "python_version", "source"},
        )
        platform = item["platform"]
        python_version = item["python_version"]
        if platform not in {"ubuntu", "macos", "windows"} or python_version not in {
            "3.11",
            "3.12",
            "3.13",
        }:
            _fail("Candidate Platform JUnit source has an unknown cell")
        source_ref = item["source"]
        source = _source_data(
            source_ref,
            root=root,
            label=f"Candidate Platform JUnit source[{index}]",
            media_type="application/xml",
        )
        source_digest = source.ref["sha256"]
        if source_digest in seen_sources:
            _fail("Candidate Platform receipt contains a duplicate JUnit source")
        seen_sources.add(source_digest)
        observations = _junit_observations(
            source_ref,
            root=root,
            label=f"Candidate Platform JUnit source[{index}]",
        )
        if not observations:
            _fail("Candidate Platform JUnit source contains no testcase")
        cells.add((platform, python_version))
        by_digest[source_digest] = (platform, python_version, observations)
    expected_cells = {
        (platform, python_version)
        for platform in ("ubuntu", "macos", "windows")
        for python_version in ("3.11", "3.12", "3.13")
    }
    if cells != expected_cells:
        _fail("Candidate Platform receipt must cover the exact 3x3 platform/Python grid")
    return [
        _require_mapping(
            descriptor,
            label=f"Candidate Platform JUnit source[{index}]",
            keys={"platform", "python_version", "source"},
        )
        for index, descriptor in enumerate(descriptors)
    ], by_digest


def _parse_platform(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
) -> dict[str, Any]:
    value, _ = _source_json(
        envelope["payload"]["source"],
        root=root,
        label="Candidate Platform receipt",
    )
    _platform_wrapper, rows = _platform_rows(value, envelope=envelope)
    required_by_platform, platform_manifest_source_sha256, platform_manifest_digest = (
        _platform_manifest_expectations(
            envelope["payload"]["platform_manifest_source"],
            root=root,
        )
    )
    descriptors, observations_by_digest = _platform_junit_sources(
        envelope["payload"],
        root=root,
    )
    expected = {"platform", "python_version", "artifact_sha256", "junit_source_sha256"}
    binding_keys: set[tuple[str, str, str]] = set()
    testcase_keys: set[tuple[str, str, str]] = set()
    outcomes: Counter[str] = Counter()
    identity_sets: list[tuple[str, str, set[tuple[str, str]]]] = []
    wheel_sha = envelope["candidate_binding"]["wheel_sha256"]
    for index, row in enumerate(rows):
        if set(row) != expected:
            _fail(f"platform row {index} keys are not closed")
        platform = row["platform"]
        python_version = row["python_version"]
        source_digest = row["junit_source_sha256"]
        if not isinstance(platform, str) or not isinstance(python_version, str):
            _fail(f"platform row {index} identity is invalid")
        if _sha(row["artifact_sha256"], label=f"platform row {index} artifact") != wheel_sha:
            _fail("platform receipt is bound to a different candidate wheel")
        source_digest = _sha(
            source_digest,
            label=f"platform row {index} JUnit source",
        )
        source_binding = observations_by_digest.get(source_digest)
        if source_binding is None:
            _fail("platform receipt references a missing raw JUnit source")
        source_platform, source_python, observations = source_binding
        if (platform, python_version) != (source_platform, source_python):
            _fail("platform receipt cell does not match its raw JUnit source")
        binding = (platform, python_version, source_digest)
        if binding in binding_keys:
            _fail("Candidate Platform receipt contains duplicate source binding")
        binding_keys.add(binding)
        identities = {
            tuple(identity.split("::", 1))
            for identity in observations
            if "::" in identity
        }
        identity_sets.append((platform, python_version, identities))
        for testcase_identity, outcome in observations.items():
            testcase_key = (platform, python_version, testcase_identity)
            if testcase_key in testcase_keys:
                _fail("Candidate Platform receipt contains duplicate testcase identity")
            testcase_keys.add(testcase_key)
            outcomes[outcome] += 1
    expected_bindings = {
        (item["platform"], item["python_version"], item["source"]["sha256"])
        for item in descriptors
    }
    if binding_keys != expected_bindings:
        _fail("Candidate Platform receipt does not bind every raw JUnit source exactly once")
    if len(rows) != len(descriptors):
        _fail("Candidate Platform receipt row/source binding count differs")
    if not identity_sets:
        _fail("Candidate Platform receipt has no testcase identity sets")
    identity_set_mismatch = 0
    required_identity_count = 0
    present_required = 0
    missing_required = 0
    unexpected_identities = 0
    for platform, _python_version, identities in identity_sets:
        required_identities = required_by_platform[platform]
        identity_set_mismatch += int(identities != required_identities)
        required_identity_count += len(required_identities)
        present_required += len(identities & required_identities)
        missing_required += len(required_identities - identities)
        unexpected_identities += len(identities - required_identities)
    return _derived(
        "candidate_platform_receipt",
        {
            "row_count": len(rows),
            "testcase_count": sum(outcomes.values()),
            "successful_testcase_count": outcomes["success"],
            "platforms": ["ubuntu", "macos", "windows"],
            "python_versions": ["3.11", "3.12", "3.13"],
            "platform_matrix_rows": len(rows),
            "source_sha256": _identity_digest(sorted(observations_by_digest)),
            "platform_manifest_source_sha256": platform_manifest_source_sha256,
            "platform_manifest_digest": platform_manifest_digest,
            "required_identity_count": required_identity_count,
            "required_identity_present_count": present_required,
            "required_identity_missing_count": missing_required,
            "unexpected_identity_count": unexpected_identities,
            "identity_set_sha256": _identity_digest(
                sorted(
                    f"{platform}|{python_version}|{classname}::{name}"
                    for platform, python_version, identities in identity_sets
                    for classname, name in identities
                )
            ),
            "identity_sha256": _identity_digest(
                sorted(
                    f"{platform}|{python_version}|{identity}"
                    for platform, python_version, source_observation in (
                        observations_by_digest.values()
                    )
                    for identity in source_observation
                )
            ),
        },
        {
            "platform_failure": outcomes["failure"],
            "platform_skip": outcomes["skip"],
            "platform_identity_set_mismatch": identity_set_mismatch,
            "platform_required_identity_missing": missing_required,
            "platform_unexpected_identity": unexpected_identities,
            "mandatory_skip": outcomes["skip"],
        },
        record_sha256,
    )


def _host_items(value: Any, *, field: str) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, Mapping) and set(value) == {field}:
        items = value[field]
    elif isinstance(value, Mapping) and field == "events" and set(value) == {"items"}:
        items = value["items"]
    else:
        _fail(f"Host receipt {field} are missing")
    if not isinstance(items, list) or not items:
        _fail(f"Host receipt {field} are missing")
    return [
        _require_mapping(item, label=f"Host {field}[{index}]")
        for index, item in enumerate(items)
    ]


def _host_sequence_container(
    value: Any,
    *,
    field: str,
    label: str,
) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    required = {
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "actual_response_model_id",
        field,
    }
    container = _require_mapping(value, label=label, keys=required)
    if (
        not isinstance(container["run_id"], str)
        or not container["run_id"]
        or isinstance(container["workflow_run_id"], bool)
        or not isinstance(container["workflow_run_id"], int)
        or container["workflow_run_id"] < 1
        or container["task_case"]
        not in {
            "cold/new",
            "resume/fork/concurrent-worktree",
            "compaction/forget/stale",
        }
        or container["host"] not in {"codex", "opencode"}
        or not isinstance(container["actual_response_model_id"], str)
        or not container["actual_response_model_id"]
    ):
        _fail(f"{label} run/task/Host identity metadata is invalid")
    items = container[field]
    if not isinstance(items, list) or not items:
        _fail(f"{label} {field} are missing")
    return container, [
        _require_mapping(item, label=f"Host {field}[{index}]")
        for index, item in enumerate(items)
    ]


def _host_route_expectation(value: Any, *, label: str) -> Mapping[str, Any]:
    route = _require_mapping(
        value,
        label=label,
        keys={
            "status",
            "source",
            "verified",
            "binding_sha256",
            "task_handle_sha256",
            "project_sha256",
            "repository_sha256",
            "worktree_sha256",
        },
    )
    if route["status"] not in {
        "exact",
        "unbound",
        "mismatch",
        "stale",
        "forgotten",
        "ambiguous",
    } or route["source"] not in {"host_observed", "none"} or not isinstance(
        route["verified"], bool
    ):
        _fail(f"{label} status is invalid")
    for field in (
        "binding_sha256",
        "task_handle_sha256",
        "project_sha256",
        "repository_sha256",
        "worktree_sha256",
    ):
        if route[field] is not None:
            _sha(route[field], label=f"{label}.{field}")
    return route


def _host_expected(value: Any, *, label: str) -> Mapping[str, Any]:
    rows = _require_rows(value, label=label)
    required = {
        "task_case",
        "event_indices",
        "operations",
        "routes",
        "gap_codes",
        "continuity",
    }
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if set(row) != required or not isinstance(row["task_case"], str):
            _fail(f"{label} row {index} keys are not closed")
        task_case = row["task_case"]
        if task_case in result or task_case not in {
            "cold/new",
            "resume/fork/concurrent-worktree",
            "compaction/forget/stale",
        }:
            _fail(f"{label} row {index} task case is invalid or duplicated")
        indices = row["event_indices"]
        operations = row["operations"]
        routes = row["routes"]
        gaps = row["gap_codes"]
        if (
            not isinstance(indices, list)
            or any(isinstance(item, bool) or not isinstance(item, int) for item in indices)
            or indices != list(range(len(indices)))
            or not isinstance(operations, list)
            or any(not isinstance(item, str) or not item for item in operations)
            or len(operations) != len(indices)
            or not isinstance(routes, list)
            or len(routes) != len(indices)
            or not isinstance(gaps, list)
            or any(not isinstance(item, str) or not item for item in gaps)
            or len(gaps) != len(set(gaps))
        ):
            _fail(f"{label} row {index} event expectations are invalid")
        for route_index, route in enumerate(routes):
            _host_route_expectation(route, label=f"{label} row {index}.routes[{route_index}]")
        continuity = _require_mapping(
            row["continuity"],
            label=f"{label} row {index}.continuity",
            keys={
                "status",
                "gap_codes",
                "binding_sha256",
                "task_handle_sha256",
                "project_sha256",
            },
        )
        if not isinstance(continuity["status"], str) or not continuity["status"]:
            _fail(f"{label} row {index}.continuity.status is invalid")
        if (
            not isinstance(continuity["gap_codes"], list)
            or any(not isinstance(item, str) or not item for item in continuity["gap_codes"])
            or len(continuity["gap_codes"]) != len(set(continuity["gap_codes"]))
        ):
            _fail(f"{label} row {index}.continuity.gap_codes is invalid")
        for field in ("binding_sha256", "task_handle_sha256", "project_sha256"):
            if continuity[field] is not None:
                _sha(continuity[field], label=f"{label} row {index}.continuity.{field}")
        result[task_case] = row
    return result


def _host_continuity(value: Any, *, label: str) -> tuple[str, Mapping[str, Any]]:
    wrapper = _require_mapping(value, label=label, keys={"task_case", "result"})
    if not isinstance(wrapper["task_case"], str) or not wrapper["task_case"]:
        _fail(f"{label} task case is invalid")
    result = _require_mapping(wrapper["result"], label=f"{label}.result")
    _validate_contract(
        result,
        "task-continuity-result.v2.schema.json",
        label=f"{label} result",
    )
    if "gap_codes" in result:
        gap_codes = result["gap_codes"]
        if not isinstance(gap_codes, list) or any(
            not isinstance(item, str) or not item for item in gap_codes
        ):
            _fail(f"{label}.result.gap_codes is invalid")
    return wrapper["task_case"], result


def _host_isolation(
    value: Any,
    *,
    envelope: Mapping[str, Any],
    label: str,
) -> tuple[Mapping[str, Any], str]:
    required = {
        "schema_version",
        "candidate_binding",
        "run_binding",
        "corpus",
        "runner",
        "scorer",
        "host",
        "task_case",
        "owner_env",
        "host_parent_expected_secret_present",
        "mcp_child_expected_secret_present",
        "auth_read_or_recorded",
        "transcript_read_or_recorded",
        "raw_prompt_read_or_recorded",
        "reasoning_read_or_recorded",
        "secret_value_read_or_recorded",
        "record_sha256",
    }
    result = _require_mapping(value, label=label, keys=required)
    if result["schema_version"] != "deeplaw.host-isolation-receipt/v1":
        _fail(f"{label} schema version is unsupported")
    candidate = _require_mapping(
        result["candidate_binding"],
        label=f"{label}.candidate_binding",
        keys={"commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"},
    )
    run = _require_mapping(
        result["run_binding"],
        label=f"{label}.run_binding",
        keys={"run_id", "workflow_run_id"},
    )
    corpus = _require_mapping(
        result["corpus"],
        label=f"{label}.corpus",
        keys={"sha256", "role"},
    )
    if dict(candidate) != dict(envelope["candidate_binding"]):
        _fail(f"{label} candidate binding differs from envelope")
    if dict(run) != dict(envelope["run_binding"]):
        _fail(f"{label} run/workflow binding differs from envelope")
    if dict(corpus) != dict(envelope["corpus"]):
        _fail(f"{label} corpus binding differs from envelope")
    for identity_label in ("runner", "scorer"):
        identity = _require_mapping(
            result[identity_label],
            label=f"{label}.{identity_label}",
            keys={"identity", "sha256"},
        )
        if dict(identity) != dict(envelope[identity_label]):
            _fail(f"{label} {identity_label} binding differs from envelope")
    if result["host"] not in {"codex", "opencode"} or result["task_case"] not in {
        "cold/new",
        "resume/fork/concurrent-worktree",
        "compaction/forget/stale",
    }:
        _fail(f"{label} host/task case identity is invalid")
    owner_env = _require_mapping(
        result["owner_env"],
        label=f"{label}.owner_env",
        keys={"path_class", "outside_repository", "regular", "symlink", "owner_only"},
    )
    if owner_env["path_class"] != "owner_only_external_env" or any(
        not isinstance(owner_env[key], bool)
        for key in ("outside_repository", "regular", "symlink", "owner_only")
    ):
        _fail(f"{label}.owner_env metadata is invalid")
    bool_fields = (
        "host_parent_expected_secret_present",
        "mcp_child_expected_secret_present",
        "auth_read_or_recorded",
        "transcript_read_or_recorded",
        "raw_prompt_read_or_recorded",
        "reasoning_read_or_recorded",
        "secret_value_read_or_recorded",
    )
    if any(not isinstance(result[field], bool) for field in bool_fields):
        _fail(f"{label} isolation flags are invalid")
    return result, _record_digest(result)


def _parse_host(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_corpus_sha256: str | None,
) -> dict[str, Any]:
    # v0.13 Gate v9 keeps the public typed kind ``host_event_sequence`` but
    # uses three new task-family labels.  Dispatch only after inspecting the
    # immutable event source so historical v1/v2/old-v3 Host labels retain
    # their exact parser behavior.
    probe_value, _ = _source_json(
        envelope["payload"]["event_source"],
        root=root,
        label="Native Host event sequence",
    )
    try:
        from benchmarks.release.typed_qualification_evidence_v3_host_tasks import (
            HostTaskEvidenceError,
            is_v013_host_task_event_source,
            parse_host_task_evidence,
        )

        if is_v013_host_task_event_source(probe_value):
            try:
                return parse_host_task_evidence(
                    envelope,
                    root=root,
                    record_sha256=record_sha256,
                    expected_corpus_sha256=expected_corpus_sha256,
                )
            except HostTaskEvidenceError as exc:
                _fail(str(exc))
    except ImportError:
        # The historical path remains usable when this optional v0.13 module
        # is absent from an older source tree.
        pass
    event_value, _ = _source_json(
        envelope["payload"]["event_source"],
        root=root,
        label="Native Host event sequence",
    )
    lifecycle_value, _ = _source_json(
        envelope["payload"]["lifecycle_source"],
        root=root,
        label="Native Host lifecycle receipt",
    )
    usage_value, _ = _source_json(
        envelope["payload"]["usage_source"],
        root=root,
        label="Native Host usage receipt",
    )
    if expected_corpus_sha256 is None:
        _fail("Host expected-source corpus binding is required")
    expected_ref = envelope["payload"]["expected_source"]
    if expected_ref["sha256"] != expected_corpus_sha256:
        _fail("Host expected source is bound to a different corpus")
    expected_value, _ = _source_json(
        expected_ref,
        root=root,
        label="Native Host expected rows",
    )
    expected_rows = _host_expected(expected_value, label="Native Host expected rows")
    continuity_value, _ = _source_json(
        envelope["payload"]["continuity_source"],
        root=root,
        label="Task continuity result",
    )
    continuity_task_case, continuity_result = _host_continuity(
        continuity_value,
        label="Task continuity result",
    )
    isolation_value, _ = _source_json(
        envelope["payload"]["isolation_source"],
        root=root,
        label="Host secret isolation receipt",
    )
    isolation, isolation_record = _host_isolation(
        isolation_value,
        envelope=envelope,
        label="Host secret isolation receipt",
    )
    event_meta, events = _host_sequence_container(
        event_value,
        field="events",
        label="Native Host event sequence",
    )
    lifecycle_meta, lifecycle = _host_sequence_container(
        lifecycle_value,
        field="receipts",
        label="Native Host lifecycle receipt",
    )
    metadata_fields = (
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "actual_response_model_id",
    )
    if any(event_meta[field] != lifecycle_meta[field] for field in metadata_fields):
        _fail("Native Host event/lifecycle metadata does not agree")
    if (
        event_meta["run_id"] != envelope["run_binding"]["run_id"]
        or event_meta["workflow_run_id"] != envelope["run_binding"]["workflow_run_id"]
    ):
        _fail("Native Host raw receipt is bound to a different run")
    expected = expected_rows.get(event_meta["task_case"])
    if expected is None or set(expected_rows) != {event_meta["task_case"]}:
        _fail("Native Host expected rows do not bind this task case exactly")
    if continuity_task_case != event_meta["task_case"]:
        _fail("Task continuity result task case differs from Host receipt")
    if (
        isolation["host"] != event_meta["host"]
        or isolation["task_case"] != event_meta["task_case"]
    ):
        _fail("Host secret isolation receipt task/Host identity differs")
    if continuity_result.get("run_id") != envelope["run_binding"]["run_id"]:
        _fail("Task continuity result is bound to a different run")
    usage_rows = _receipt_rows(usage_value, envelope=envelope, label="Host usage")
    if len(events) != len(lifecycle):
        _fail("Native Host lifecycle receipt is incomplete")
    event_indices = [item.get("event_sequence", {}).get("index") for item in events]
    if any(not isinstance(index, int) for index in event_indices):
        _fail("Native Host event sequence index is missing")
    if event_indices != list(range(len(events))):
        _fail("Native Host event sequence is not contiguous")
    hosts: set[str] = set()
    host_identity_digests: set[str] = set()
    host_model_ids: set[str | None] = set()
    host_identity_values: list[Mapping[str, Any]] = []
    sessions: list[str] = []
    sequence_digests: list[str] = []
    hard_failures: Counter[str] = Counter()
    isolation_verified = (
        isolation["owner_env"]["outside_repository"] is True
        and isolation["owner_env"]["regular"] is True
        and isolation["owner_env"]["symlink"] is False
        and isolation["owner_env"]["owner_only"] is True
        and isolation["host_parent_expected_secret_present"] is True
        and isolation["mcp_child_expected_secret_present"] is False
        and all(
            isolation[field] is False
            for field in (
                "auth_read_or_recorded",
                "transcript_read_or_recorded",
                "raw_prompt_read_or_recorded",
                "reasoning_read_or_recorded",
                "secret_value_read_or_recorded",
            )
        )
    )
    if not isolation_verified:
        hard_failures["host_secret_isolation"] += 1
    observed_gap_codes: set[str] = set()
    observed_routes: list[Mapping[str, Any]] = []
    observed_operations: list[str] = []
    for index, (event, receipt) in enumerate(zip(events, lifecycle, strict=True)):
        try:
            parsed_event = parse_native_host_event(event)
            expected_receipt = derive_native_host_receipt(parsed_event)
        except (NativeHostObservationError, TypeError, ValueError) as exc:
            raise TypedQualificationEvidenceError(
                f"Native Host event[{index}] is not a real receipt"
            ) from exc
        _validate_contract(
            receipt,
            "native-host-lifecycle-receipt.v2.schema.json",
            label=f"Host lifecycle[{index}]",
        )
        lifecycle_record = _record_digest(receipt, field="receipt_sha256")
        if dict(receipt) != expected_receipt:
            _fail("Native Host lifecycle receipt does not derive from its event sequence")
        if receipt["event_sequence"]["index"] != index:
            _fail("Native Host lifecycle sequence index mismatch")
        if parsed_event["provenance_level"] == "compatibility_bridge" or receipt[
            "provenance_level"
        ] == "compatibility_bridge":
            hard_failures["host_compatibility_bridge"] += 1
        hosts.add(parsed_event["host"])
        host_identity_values.append(parsed_event["host_identity"])
        host_model_ids.add(
            parsed_event["host_identity"].get(
                "request_model"
                if parsed_event["host"] == "codex"
                else "expected_response_model_id"
            )
        )
        host_identity_digests.add(
            _sha256_bytes(_canonical(parsed_event["host_identity"]))
        )
        sessions.append(parsed_event["session_sha256"])
        sequence_digests.append(lifecycle_record)
        if receipt["status"] == "gap":
            observed_gap_codes.add(receipt["gap"]["code"])
        observed_routes.append(receipt["route_binding_provenance"])
        observed_operations.append(receipt["operation"])
    if event_indices != expected["event_indices"]:
        hard_failures["host_event_expectation_mismatch"] += 1
    if observed_operations != expected["operations"]:
        hard_failures["host_operation_expectation_mismatch"] += 1
    for _index, (observed_route, expected_route) in enumerate(
        zip(observed_routes, expected["routes"], strict=True)
    ):
        if dict(observed_route) != dict(expected_route):
            hard_failures["host_route_binding_mismatch"] += 1
    if observed_gap_codes != set(expected["gap_codes"]):
        hard_failures["host_gap_expectation_mismatch"] += 1
    continuity_expected = expected["continuity"]
    continuity_gap_codes = set(continuity_result.get("gap_codes", []))
    if (
        continuity_result["status"] != continuity_expected["status"]
        or continuity_gap_codes != set(continuity_expected["gap_codes"])
        or any(
            continuity_result.get(field) != continuity_expected[field]
            for field in ("binding_sha256", "task_handle_sha256", "project_sha256")
        )
    ):
        hard_failures["host_continuity_expectation_mismatch"] += 1
    if len(hosts) != 1:
        hard_failures["host_identity_mismatch"] += 1
    if hosts != {event_meta["host"]} or len(host_identity_digests) != 1:
        hard_failures["host_identity_mismatch"] += 1
    actual_model = event_meta["actual_response_model_id"]
    expected_model = (
        "gpt-5.6-luna" if event_meta["host"] == "codex" else "deepseek-v4-flash"
    )
    if actual_model != expected_model:
        hard_failures["host_response_model_mismatch"] += 1
    if host_model_ids != {expected_model}:
        hard_failures["host_identity_model_mismatch"] += 1
    usage_fields = {
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "actual_response_model_id",
        "host_identity_sha256",
        "candidate_commit",
        "candidate_tree",
        "corpus_sha256",
        "runner_identity",
        "runner_sha256",
        "input_tokens",
        "output_tokens",
        "cache_tokens",
        "reasoning_tokens",
        "provider_bytes",
        "latency_ms",
        "rss_peak_bytes",
    }
    totals: Counter[str] = Counter()
    for index, row in enumerate(usage_rows):
        if set(row) != usage_fields:
            _fail(f"Host usage row {index} keys are not closed")
        if row["run_id"] != envelope["run_binding"]["run_id"]:
            _fail("Host usage row is bound to a different run")
        if (
            row["workflow_run_id"] != event_meta["workflow_run_id"]
            or row["task_case"] != event_meta["task_case"]
            or row["host"] != event_meta["host"]
            or row["actual_response_model_id"] != event_meta["actual_response_model_id"]
            or row["host_identity_sha256"] != next(iter(host_identity_digests), None)
        ):
            _fail("Host usage row does not cross-bind raw Host identity metadata")
        if (
            row["candidate_commit"] != envelope["candidate_binding"]["commit"]
            or row["candidate_tree"] != envelope["candidate_binding"]["tree"]
            or row["corpus_sha256"] != envelope["corpus"]["sha256"]
            or row["runner_identity"] != envelope["runner"]["identity"]
            or row["runner_sha256"] != envelope["runner"]["sha256"]
        ):
            _fail("Host usage row is bound to a different candidate/corpus/runner")
        for field in usage_fields - {
            "run_id",
            "workflow_run_id",
            "task_case",
            "host",
            "actual_response_model_id",
            "host_identity_sha256",
            "candidate_commit",
            "candidate_tree",
            "corpus_sha256",
            "runner_identity",
            "runner_sha256",
        }:
            totals[field] += _number(row[field], label=f"Host usage row {index}.{field}")
    return _derived(
        "host_event_sequence",
        {
            "host": next(iter(hosts)) if len(hosts) == 1 else None,
            "task_case": event_meta["task_case"],
            "run_id": event_meta["run_id"],
            "workflow_run_id": event_meta["workflow_run_id"],
            "actual_response_model_id": actual_model,
            "host_identity": (
                dict(host_identity_values[0]) if len(host_identity_digests) == 1 else None
            ),
            "host_identity_sha256": next(iter(host_identity_digests), None),
            "event_count": len(events),
            "lifecycle_receipt_count": len(lifecycle),
            "event_sequence_sha256": _identity_digest(event_indices),
            "session_identity_sha256": _identity_digest(sessions),
            "lifecycle_record_sha256": _identity_digest(sequence_digests),
            "observed_gap_codes": sorted(observed_gap_codes),
            "continuity_status": continuity_result["status"],
            "continuity_gap_codes": sorted(continuity_result.get("gap_codes", [])),
            "host_isolation_record_sha256": isolation_record,
            "host_secret_isolation_verified": isolation_verified,
            "usage": dict(totals),
        },
        hard_failures,
        record_sha256,
    )


def _parse_exact_wheel(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_candidate_run_id: int | None = None,
) -> dict[str, Any]:
    receipt, _ = _source_json(
        envelope["payload"]["source"],
        root=root,
        label="exact-wheel execution receipt",
        allow_count_paths=frozenset({("public_journey", "step_count")}),
    )
    envelope_is_v2 = envelope["schema_version"] in _V2_COMPATIBLE_SCHEMA_VERSIONS
    receipt_schema = (
        "exact-wheel-execution-receipt.v2.schema.json"
        if envelope_is_v2
        else "exact-wheel-execution-receipt.v1.schema.json"
    )
    expected_receipt_schema = (
        "deeplaw.exact-wheel-execution-receipt/v2"
        if envelope_is_v2
        else "deeplaw.exact-wheel-execution-receipt/v1"
    )
    if receipt.get("schema_version") != expected_receipt_schema:
        _fail("exact-wheel receipt schema version is not bound to the typed evidence version")
    _validate_contract(receipt, receipt_schema, label="exact-wheel receipt")
    receipt_record = _record_digest(receipt)
    if receipt["runner_source_sha256"] != envelope["runner"]["sha256"]:
        _fail("exact-wheel receipt runner differs from the bound external runner")
    candidate = receipt["candidate"]
    binding = envelope["candidate_binding"]
    if envelope_is_v2:
        if expected_candidate_run_id is None:
            _fail("exact-wheel v2 candidate run binding expectation is required")
        if (
            isinstance(expected_candidate_run_id, bool)
            or not isinstance(expected_candidate_run_id, int)
            or expected_candidate_run_id < 1
        ):
            _fail("exact-wheel v2 candidate run binding expectation is invalid")
        provenance = receipt["candidate_provenance"]
        if (
            provenance["commit"] != binding["commit"]
            or provenance["tree"] != binding["tree"]
            or provenance["lock_sha256"] != binding["lock_sha256"]
        ):
            _fail("exact-wheel receipt candidate provenance differs from the typed binding")
        if (
            provenance["commit"] == "0" * 40
            or provenance["tree"] == "0" * 40
            or provenance["lock_sha256"] == "0" * 64
        ):
            _fail("exact-wheel receipt candidate provenance contains a placeholder digest")
        run_binding = receipt["run_binding"]
        if run_binding["candidate_run_id"] != expected_candidate_run_id:
            _fail("exact-wheel receipt candidate run differs from the expected Candidate Full run")
        if run_binding["candidate_run_id"] == run_binding["evidence_run_id"]:
            _fail("exact-wheel receipt candidate and evidence runs must be distinct")
        if run_binding["evidence_run_id"] != envelope["run_binding"]["workflow_run_id"]:
            _fail("exact-wheel receipt evidence run differs from the typed evidence workflow run")
        corpus = receipt["corpus_binding"]
        if (
            corpus["role"] != envelope["corpus"]["role"]
            or corpus["sha256"] != envelope["corpus"]["sha256"]
            or corpus["role"] != "candidate_full"
        ):
            _fail("exact-wheel receipt corpus differs from Candidate Full raw inventory")
        if corpus["sha256"] == "0" * 64:
            _fail("exact-wheel receipt corpus contains a placeholder digest")
    if candidate["wheel_sha256"] != binding["wheel_sha256"]:
        _fail("exact-wheel receipt is bound to a different wheel")
    if candidate["wheel_sha256"] == "0" * 64 or receipt["runner_source_sha256"] == "0" * 64:
        _fail("exact-wheel receipt contains a placeholder artifact identity")
    if candidate["wheel_size"] < 1 or not candidate["wheel_filename"].endswith(".whl"):
        _fail("exact-wheel receipt candidate identity is invalid")
    if receipt["status"] != "exact_wheel_executed":
        _fail("exact-wheel receipt does not prove execution")
    wheel_parts = candidate["wheel_filename"].split("-")
    wheel_version = wheel_parts[1] if len(wheel_parts) > 1 else None
    runtime = receipt["runtime"]
    entrypoint = receipt["entrypoint"]
    version_check = receipt["version_check"]
    journey = receipt["public_journey"]
    network = receipt["network_acquisition"]
    policy = receipt["environment_policy"]
    import_verified = (
        runtime["distribution_name"] == PACKAGE_NAME
        and runtime["import_module"] == PACKAGE_NAME
        and runtime["distribution_version"] == wheel_version
        and runtime["import_file_path_class"] == "venv_site_packages"
        and runtime["import_file_relative_path"].startswith("deeplaw/")
    )
    entry_verified = (
        entrypoint["name"] == PACKAGE_NAME
        and entrypoint["group"] == "console_scripts"
        and entrypoint["executable_path_class"] == "venv_bin"
        and entrypoint["module_path_class"] == "venv_site_packages"
        and entrypoint["module_relative_path"].startswith("deeplaw/")
    )
    command_verified = (
        version_check["argv"] == ["deeplaw", "--version"]
        and version_check["exit_code"] == 0
        and version_check["stdout_path_class"] == "sanitized_stdout"
        and journey["journey_status"] == "passed"
        and journey["step_count"] == 5
        and all(
            step["status"] == "passed" and step["exit_code"] == 0
            for step in journey["steps"]
        )
        and journey["network_policy"]["network_access"] == "not_requested"
        and journey["network_policy"]["model_sidecar"] is False
        and journey["network_policy"]["environment_allowlist"] == "minimal"
    )
    venv_verified = (
        receipt["venv"]["path_class"] == "new_isolated_venv"
        and receipt["venv"]["created_new"] is True
        and receipt["venv"]["system_site_packages"] is False
        and receipt["venv"]["site_packages_path_class"] == "venv_site_packages"
        and runtime["python_executable_path_class"] == "new_isolated_venv"
        and candidate["path_class"] == "candidate_full_wheel"
        and receipt["requirements"]["path_class"] == "candidate_requirements"
        and receipt["requirements"]["hash_pinned"] is True
    )
    mode = network["mode"]
    network_verified = (
        network["explicit"] is True
        and network["hash_pinned"] is True
        and mode in {"candidate_wheelhouse", "fixed_pypi_index"}
        and policy["python_isolated_mode"] is True
        and policy["pythonpath_cleared"] is True
        and policy["pythonhome_cleared"] is True
        and policy["user_site_disabled"] is True
        and policy["requirements_hashes_required"] is True
        and policy["candidate_source_only"] is True
        and (
            mode == "fixed_pypi_index"
            or policy["network_disabled_for_install"] is True
        )
    )
    return _derived(
        "exact_wheel_execution",
        {
            "receipt_record_sha256": receipt_record,
            "wheel_sha256": candidate["wheel_sha256"],
            "wheel_size": candidate["wheel_size"],
            "distribution_version": receipt["runtime"]["distribution_version"],
            "import_origin_verified": import_verified,
            "entrypoint_origin_verified": entry_verified,
            "isolated_environment_verified": venv_verified and network_verified,
            "version_command_verified": command_verified,
            "network_mode": mode,
            "audit_integrity_pass_rate": float(
                import_verified
                and entry_verified
                and venv_verified
                and network_verified
                and command_verified
            ),
        },
        {
            "exact_wheel_identity": 0,
            "exact_wheel_origin": int(not (import_verified and entry_verified)),
            "exact_wheel_isolation": int(not (venv_verified and network_verified)),
            "exact_wheel_command": int(not command_verified),
            "canonical_integrity_failure": int(
                not (
                    import_verified
                    and entry_verified
                    and venv_verified
                    and network_verified
                    and command_verified
                )
            ),
        },
        record_sha256,
    )


def _parse_human_gold(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    trusted_human_approver: Mapping[str, Any] | None,
) -> dict[str, Any]:
    payload = envelope["payload"]
    gold, gold_source = _source_json(
        payload["semantic_gold_source"],
        root=root,
        label="semantic Human Gold",
    )
    binding, _ = _source_json(
        payload["candidate_binding_source"],
        root=root,
        label="Candidate Gold binding",
    )
    rows_value, _ = _source_json(
        payload["scorer_rows_source"],
        root=root,
        label="Human Gold scorer rows",
    )
    attestation, attestation_source = _source_json(
        payload["human_attestation_source"],
        root=root,
        label="external human attestation",
    )
    _validate_contract(
        gold,
        "semantic-human-gold.v3.schema.json",
        label="semantic Human Gold",
    )
    _validate_contract(
        binding,
        "candidate-gold-binding-receipt.v1.schema.json",
        label="Candidate Gold binding",
    )
    binding_record = _record_digest(binding)
    _validate_external_attestation(
        gold=gold,
        attestation=attestation,
        attestation_source=attestation_source,
        semantic_gold_sha256=_sha256_bytes(gold_source.raw),
        trusted_human_approver=trusted_human_approver,
    )
    if _sha256_bytes(gold_source.raw) != binding["semantic_gold"]["sha256"]:
        _fail("Candidate Gold binding does not identify the semantic Gold bytes")
    candidate = envelope["candidate_binding"]
    for field in ("commit", "tree", "lock_sha256"):
        if binding["candidate"][field] != candidate[field]:
            _fail(f"Candidate Gold binding candidate mismatch: {field}")
    if (
        binding["artifacts"]["wheel"]["sha256"] != candidate["wheel_sha256"]
        or binding["artifacts"]["sdist"]["sha256"] != candidate["sdist_sha256"]
    ):
        _fail("Candidate Gold binding artifact mismatch")
    if binding["scorer"] != envelope["scorer"] or binding["runner"] != envelope["runner"]:
        _fail("Candidate Gold binding scorer/runner identity mismatch")
    bound_corpora = {
        (binding["holdout"]["role"], binding["holdout"]["sha256"]),
        (binding["blind"]["role"], binding["blind"]["sha256"]),
    }
    envelope_corpus = (envelope["corpus"]["role"], envelope["corpus"]["sha256"])
    if envelope_corpus not in bound_corpora:
        _fail("Human Gold corpus is not the bound holdout or blind corpus")
    process = payload["process_identity"]
    if (
        process["scorer_identity_sha256"] != envelope["scorer"]["sha256"]
        or process["runner_identity_sha256"] != envelope["runner"]["sha256"]
        or process["scorer_process_id"] == process["runner_process_id"]
        or process["scorer_identity_sha256"] == process["runner_identity_sha256"]
        or process["separate_processes"] is not True
    ):
        _fail("Human Gold scorer and runner are not independently identified processes")
    rows = _receipt_rows(rows_value, envelope=envelope, label="Human Gold scorer")
    expected_cases = {case["case_id"]: case for case in gold["cases"]}
    seen: set[str] = set()
    mismatch = hard = 0
    false_authority = 0
    duties_seen: set[str] = set()
    known_hard_failures = {item["code"] for item in gold["hard_failures"]}
    required = {
        "case_id",
        "expected",
        "observed",
        "duties",
        "hard_failures",
        "scorer_process_id",
        "runner_process_id",
        "false_authority",
    }
    for index, row in enumerate(rows):
        if set(row) != required:
            _fail(f"Human Gold scorer row {index} keys are not closed")
        case_id = row["case_id"]
        if case_id not in expected_cases or case_id in seen:
            _fail("Human Gold scorer rows do not have a unique semantic case identity")
        seen.add(case_id)
        case = expected_cases[case_id]
        if row["expected"] != case["expected"]:
            _fail("Human Gold scorer row expected value differs from semantic Gold")
        if row["duties"] != case["duties"]:
            _fail("Human Gold scorer row duties differ from semantic Gold")
        if (
            row["scorer_process_id"] != process["scorer_process_id"]
            or row["runner_process_id"] != process["runner_process_id"]
        ):
            _fail("Human Gold scorer process identity differs from the bound process")
        if not isinstance(row["false_authority"], bool):
            _fail("Human Gold scorer false-authority observation is invalid")
        if not isinstance(row["hard_failures"], list) or any(
            not isinstance(item, str) or item not in known_hard_failures
            for item in row["hard_failures"]
        ) or len(row["hard_failures"]) != len(set(row["hard_failures"])):
            _fail("Human Gold scorer hard-failure rows are invalid")
        duties_seen.update(row["duties"])
        if row["observed"] != row["expected"]:
            mismatch += 1
        hard += len(row["hard_failures"])
        false_authority += int(row["false_authority"])
    if seen != set(expected_cases):
        _fail("Human Gold scorer rows do not cover every semantic Gold case")
    required_duties = {item["duty_id"] for item in gold["duties"]}
    duty_coverage = (
        len(duties_seen & required_duties) / len(required_duties)
        if required_duties
        else 1.0
    )
    case_passes = sum(
        row["observed"] == row["expected"] and not row["hard_failures"]
        for row in rows
    )
    case_pass_rate = case_passes / len(rows)
    thresholds = gold["thresholds"]
    threshold_failures = {
        "gold_case_pass_rate": int(
            case_pass_rate < thresholds["minimum_case_pass_rate"]
        ),
        "gold_duty_coverage": int(
            duty_coverage < thresholds["minimum_duty_coverage"]
        ),
        "gold_hard_failure": int(hard > thresholds["maximum_hard_failures"]),
        "gold_false_authority": int(
            false_authority > thresholds["maximum_false_authority"]
        ),
    }
    return _derived(
        "human_gold_scorer",
        {
            "gold_id": gold["gold_id"],
            "semantic_gold_sha256": _sha256_bytes(gold_source.raw),
            "candidate_binding_record_sha256": binding_record,
            "case_count": len(rows),
            "matching_case_count": len(rows) - mismatch,
            "observation_mismatch_count": mismatch,
            "case_pass_count": case_passes,
            "case_pass_rate": case_pass_rate,
            "duty_coverage": duty_coverage,
            "duty_missing_count": len(required_duties - duties_seen),
            "hard_failure_observation_count": hard,
            "false_authority_count": false_authority,
            "thresholds": dict(thresholds),
            "corpus_role": envelope["corpus"]["role"],
            "corpus_sha256": envelope["corpus"]["sha256"],
            "attestation_receipt_bound": True,
            "human_attested": True,
            "attestation_record_sha256": _record_digest(attestation),
        },
        threshold_failures,
        record_sha256,
    )


def _machine_scorer_rows(
    value: Any,
    *,
    envelope: Mapping[str, Any],
    expected_scorer: Mapping[str, Any],
    label: str,
) -> list[Mapping[str, Any]]:
    wrapper = _require_mapping(value, label=label, keys={"receipt", "rows"})
    receipt = _require_mapping(
        wrapper["receipt"],
        label=f"{label}.receipt",
        keys={"candidate", "run", "corpus", "runner", "scorer"},
    )
    for field, expected in (
        ("candidate", envelope["candidate_binding"]),
        ("run", envelope["run_binding"]),
        ("corpus", envelope["corpus"]),
        ("runner", envelope["runner"]),
    ):
        observed = _require_mapping(
            receipt[field],
            label=f"{label}.receipt.{field}",
        )
        if dict(observed) != dict(expected):
            _fail(f"{label} receipt has a different {field} binding")
    scorer = _require_mapping(
        receipt["scorer"],
        label=f"{label}.receipt.scorer",
        keys={"identity", "sha256"},
    )
    if (
        scorer["identity"] != expected_scorer["identity"]
        or scorer["sha256"] != expected_scorer["sha256"]
    ):
        _fail(f"{label} receipt has a different scorer binding")
    rows = wrapper["rows"]
    if not isinstance(rows, list) or not rows:
        _fail(f"{label}.rows must contain real rows")
    return [
        _require_mapping(row, label=f"{label}.rows[{index}]")
        for index, row in enumerate(rows)
    ]


def _parse_machine_reference(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
) -> dict[str, Any]:
    payload = envelope["payload"]
    candidate_output, candidate_output_source = _source_json(
        payload["candidate_output_source"],
        root=root,
        label="retained machine candidate output",
    )
    candidate_execution, _candidate_execution_source = _source_json(
        payload["candidate_execution_source"],
        root=root,
        label="retained machine candidate execution",
    )
    reference, reference_source = _source_json(
        payload["semantic_reference_source"],
        root=root,
        label="semantic machine reference",
    )
    binding, _ = _source_json(
        payload["candidate_binding_source"],
        root=root,
        label="Candidate machine-reference binding",
    )
    roster, roster_source = _source_json(
        payload["agent_roster_source"],
        root=root,
        label="Agent review roster",
    )
    consensus, consensus_source = _source_json(
        payload["agent_consensus_source"],
        root=root,
        label="Agent review consensus",
    )
    isolation, isolation_source = _source_json(
        payload["agent_isolation_source"],
        root=root,
        label="Agent review isolation",
    )
    scorer_a_value, _ = _source_json(
        payload["scorer_a_rows_source"],
        root=root,
        label="independent scorer A rows",
    )
    scorer_b_value, _ = _source_json(
        payload["scorer_b_rows_source"],
        root=root,
        label="independent scorer B rows",
    )
    arbiter_value, _ = _source_json(
        payload["arbiter_consensus_rows_source"],
        root=root,
        label="deterministic arbiter rows",
    )
    security_receipts: dict[str, Mapping[str, Any]] = {}
    for index, source_ref in enumerate(payload["security_domain_receipt_sources"]):
        value, _ = _source_json(
            source_ref,
            root=root,
            label=f"security-domain receipt[{index}]",
            allow_count_paths=frozenset({("negative_canary", "leaked_count")}),
        )
        try:
            receipt = _validate_security_domain_receipt(value)
        except _SecurityDomainReceiptError as error:
            _fail(str(error))
        role = str(receipt["role"])
        if role in security_receipts:
            _fail("security-domain receipt roles are duplicated")
        security_receipts[role] = receipt
    if set(security_receipts) != set(_SECURITY_DOMAIN_ROLES):
        _fail("security-domain receipt roles are incomplete")
    retained_process_receipts: list[str] = []
    for index, source_ref in enumerate(payload["process_receipt_sources"]):
        _value, source = _source_json(
            source_ref,
            root=root,
            label=f"sanitized process receipt[{index}]",
        )
        retained_process_receipts.append(str(source.ref["sha256"]))
    required_process_receipts = {
        digest
        for receipt in security_receipts.values()
        for digest in receipt["process_receipt_sha256s"]
    }
    if (
        len(retained_process_receipts) != len(set(retained_process_receipts))
        or set(retained_process_receipts) != required_process_receipts
    ):
        _fail("security-domain process receipt bytes are not retained exactly once")
    try:
        security_domains_sha256 = _security_domain_set_sha256(
            list(security_receipts.values())
        )
    except _SecurityDomainReceiptError as error:
        _fail(str(error))
    if len(
        {
            receipt["attester_executable_sha256"]
            for receipt in security_receipts.values()
        }
    ) != 1:
        _fail("security-domain attester executable bindings differ")
    _validate_contract(
        reference,
        "semantic-machine-reference.v1.schema.json",
        label="semantic machine reference",
    )
    _validate_contract(
        binding,
        "candidate-gold-binding-receipt.v2.schema.json",
        label="Candidate machine-reference binding",
    )
    _validate_contract(
        candidate_output,
        "machine-candidate-output.v1.schema.json",
        label="retained machine candidate output",
    )
    _validate_contract(
        candidate_execution,
        "machine-candidate-execution.v1.schema.json",
        label="retained machine candidate execution",
    )
    candidate_output_record = _record_digest(candidate_output)
    candidate_execution_record = _record_digest(candidate_execution)
    for value in (reference, binding, roster, consensus, isolation):
        _record_digest(value)

    expected_profile = "machine_evaluated_no_human_attestation"
    if any(
        value.get("profile") != expected_profile
        for value in (
            envelope,
            candidate_output,
            candidate_execution,
            reference,
            binding,
            roster,
            consensus,
            isolation,
        )
    ):
        _fail("machine-reference profile binding differs")
    if any(
        value.get("human_authenticity", "not_claimed") != "not_claimed"
        for value in (envelope, reference, binding)
    ):
        _fail("machine-reference evidence makes a human-authenticity claim")
    if reference["reference_provenance"] != "agent_consensus":
        _fail("semantic machine reference provenance is not Agent consensus")
    for label, value in (
        ("candidate output", candidate_output),
        ("candidate execution", candidate_execution),
    ):
        for field, expected in (
            ("candidate", envelope["candidate_binding"]),
            ("run", envelope["run_binding"]),
            ("corpus", envelope["corpus"]),
            ("runner", envelope["runner"]),
        ):
            if dict(value[field]) != dict(expected):
                _fail(f"{label} has a different {field} binding")
    if candidate_execution["executable_sha256"] != envelope["runner"]["sha256"]:
        _fail("candidate execution executable hash differs from runner")
    if candidate_execution["output_sha256"] != _sha256_bytes(candidate_output_source.raw):
        _fail("candidate execution does not bind retained candidate output")
    read_only_inputs = candidate_execution["process"]["read_only_input_sha256s"]
    if any(
        digest not in read_only_inputs
        for digest in (
            envelope["candidate_binding"]["wheel_sha256"],
            envelope["corpus"]["sha256"],
        )
    ):
        _fail("candidate execution read-only inputs do not cover candidate and corpus")
    if candidate_execution["process"]["exit_code"] != 0:
        _fail("candidate execution did not exit successfully")
    process_receipt = candidate_execution["process"]
    if process_receipt["pid"] == process_receipt["parent_pid"]:
        _fail("candidate execution PID and parent PID are not distinct")
    environment_keys = process_receipt["environment_key_allowlist"]
    if any(
        key.casefold() in {"home", "codex_home"} or _SECRET_ENV_KEY_RE.search(key)
        for key in environment_keys
    ):
        _fail("candidate runner environment allowlist contains a Secret or Host auth key")
    reference_digest = _sha256_bytes(reference_source.raw)
    semantic_binding = _require_mapping(
        binding["semantic_reference"],
        label="Candidate semantic machine-reference binding",
        keys={"reference_id", "schema_version", "sha256"},
    )
    if (
        semantic_binding["reference_id"] != reference["reference_id"]
        or semantic_binding["schema_version"] != reference["schema_version"]
        or semantic_binding["sha256"] != reference_digest
    ):
        _fail("Candidate binding does not identify the semantic machine reference")

    review = _require_mapping(reference["agent_review"], label="Agent review")
    reviewers = review["reviewers"]
    if not isinstance(reviewers, list) or len(reviewers) < 3:
        _fail("semantic machine reference lacks three independent reviewers")
    if len(reviewers) < review["minimum_distinct_agents"]:
        _fail("semantic machine reference misses its minimum Agent roster")
    agent_ids = [item["agent_id"] for item in reviewers]
    process_digests = [item["process_identity_sha256"] for item in reviewers]
    output_digests = [item["output_sha256"] for item in reviewers]
    if (
        len(set(agent_ids)) != len(agent_ids)
        or len(set(process_digests)) != len(process_digests)
        or len(set(output_digests)) != len(output_digests)
    ):
        _fail("Agent reviewers are not independently identified")

    roster_keys = {
        "schema_version",
        "profile",
        "reference_id",
        "reviewers",
        "record_sha256",
    }
    roster_record = _require_mapping(roster, label="Agent review roster", keys=roster_keys)
    if (
        roster_record["schema_version"] != "deeplaw.agent-review-roster/v1"
        or roster_record["reference_id"] != reference["reference_id"]
        or roster_record["reviewers"] != reviewers
        or _sha256_bytes(roster_source.raw) != review["roster_sha256"]
        or binding["agent_roster"]["sha256"] != review["roster_sha256"]
    ):
        _fail("Agent review roster does not bind the semantic reference")

    consensus_keys = {
        "schema_version",
        "profile",
        "reference_id",
        "roster_sha256",
        "rubric_sha256",
        "source_corpus_sha256",
        "reviewer_output_sha256s",
        "unanimous",
        "disagreements",
        "record_sha256",
    }
    consensus_record = _require_mapping(
        consensus,
        label="Agent review consensus",
        keys=consensus_keys,
    )
    if (
        consensus_record["schema_version"] != "deeplaw.agent-review-consensus/v1"
        or consensus_record["reference_id"] != reference["reference_id"]
        or consensus_record["roster_sha256"] != review["roster_sha256"]
        or consensus_record["rubric_sha256"] != review["rubric_sha256"]
        or consensus_record["source_corpus_sha256"] != review["source_corpus_sha256"]
        or consensus_record["reviewer_output_sha256s"] != output_digests
        or consensus_record["unanimous"] is not True
        or consensus_record["disagreements"] != []
        or _sha256_bytes(consensus_source.raw) != review["consensus_sha256"]
        or binding["agent_consensus"]["sha256"] != review["consensus_sha256"]
    ):
        _fail("Agent review consensus is incomplete or inconsistent")

    isolation_keys = {
        "schema_version",
        "profile",
        "reference_id",
        "reviewer_processes_distinct",
        "reviewer_outputs_hidden",
        "candidate_hidden",
        "runner_reference_labels_hidden",
        "scorers_mutually_hidden",
        "scorer_runner_isolated",
        "arbiter_deterministic",
        "compiler_reference_access",
        "evaluator_output_mutation",
        "blind_contamination",
        "violations",
        "record_sha256",
    }
    isolation_record = _require_mapping(
        isolation,
        label="Agent review isolation",
        keys=isolation_keys,
    )
    isolation_true = (
        "reviewer_processes_distinct",
        "reviewer_outputs_hidden",
        "candidate_hidden",
        "runner_reference_labels_hidden",
        "scorers_mutually_hidden",
        "scorer_runner_isolated",
        "arbiter_deterministic",
    )
    if (
        isolation_record["schema_version"] != "deeplaw.agent-review-isolation/v1"
        or isolation_record["reference_id"] != reference["reference_id"]
        or any(isolation_record[field] is not True for field in isolation_true)
        or any(
            isolation_record[field] is not False
            for field in (
                "compiler_reference_access",
                "evaluator_output_mutation",
                "blind_contamination",
            )
        )
        or isolation_record["violations"] != []
        or _sha256_bytes(isolation_source.raw) != review["isolation_sha256"]
        or binding["agent_isolation"]["sha256"] != review["isolation_sha256"]
    ):
        _fail("Agent review isolation is not fail-closed")

    candidate = envelope["candidate_binding"]
    for field in ("commit", "tree", "lock_sha256"):
        if binding["candidate"][field] != candidate[field]:
            _fail(f"Candidate machine-reference binding mismatch: {field}")
    if (
        binding["artifacts"]["wheel"]["sha256"] != candidate["wheel_sha256"]
        or binding["artifacts"]["sdist"]["sha256"] != candidate["sdist_sha256"]
    ):
        _fail("Candidate machine-reference artifact binding differs")
    bound_corpora = {
        (binding["holdout"]["role"], binding["holdout"]["sha256"]),
        (binding["blind"]["role"], binding["blind"]["sha256"]),
    }
    if (envelope["corpus"]["role"], envelope["corpus"]["sha256"]) not in bound_corpora:
        _fail("machine-reference corpus is not the bound holdout or blind corpus")

    panel = _require_mapping(envelope["scorer_panel"], label="scorer panel")
    binding_panel = _require_mapping(binding["scorer_panel"], label="bound scorer panel")
    if dict(panel) != dict(binding_panel):
        _fail("Candidate binding scorer panel differs from the evidence envelope")
    scorer_a = _require_mapping(panel["scorer_a"], label="independent scorer A")
    scorer_b = _require_mapping(panel["scorer_b"], label="independent scorer B")
    panel_digest = _sha256_bytes(
        _canonical({"scorer_a": dict(scorer_a), "scorer_b": dict(scorer_b)})
    )
    if (
        scorer_a["identity"] == scorer_b["identity"]
        or scorer_a["sha256"] == scorer_b["sha256"]
        or panel["panel_sha256"] != panel_digest
    ):
        _fail("independent scorer panel identity is not closed")
    arbiter = _require_mapping(envelope["arbiter"], label="deterministic arbiter")
    if dict(arbiter) != dict(binding["arbiter"]):
        _fail("Candidate binding arbiter differs from the evidence envelope")
    if (
        envelope["scorer"]["identity"] != arbiter["identity"]
        or envelope["scorer"]["sha256"] != arbiter["sha256"]
        or dict(envelope["runner"]) != dict(binding["runner"])
    ):
        _fail("machine-reference scorer/runner binding differs")

    expected_artifacts = {
        "reference_freezer": (
            {"reference-cases", "reviewer-inputs"},
            {"sealed-reference"},
        ),
        "candidate_host": (
            {
                "verified-candidate-artifacts",
                "qualification-inputs",
                "final-blind-inputs",
            },
            {"candidate-sanitized-output"},
        ),
        "scorer_a": (
            {"candidate-sanitized-output", "sealed-reference"},
            {"scorer-a-output"},
        ),
        "scorer_b": (
            {"candidate-sanitized-output", "sealed-reference"},
            {"scorer-b-output"},
        ),
        "arbiter": ({"scorer-a-output", "scorer-b-output"}, {"arbiter-output"}),
    }
    forbidden_artifacts = {
        "reference_freezer": {
            "candidate-sanitized-output",
            "scorer-a-output",
            "scorer-b-output",
            "arbiter-output",
        },
        "candidate_host": {
            "sealed-reference",
            "scorer-a-output",
            "scorer-b-output",
            "arbiter-output",
        },
        "scorer_a": {"scorer-b-output", "arbiter-output"},
        "scorer_b": {"scorer-a-output", "arbiter-output"},
        "arbiter": {"candidate-sanitized-output", "sealed-reference"},
    }
    producers: dict[str, str] = {}
    for receipt in security_receipts.values():
        for artifact in receipt["egress"]:
            if artifact["name"] in producers:
                _fail("security-domain artifact has multiple producers")
            producers[artifact["name"]] = artifact["sha256"]
    for role, receipt in security_receipts.items():
        ingress = {item["name"] for item in receipt["ingress"]}
        egress = {item["name"] for item in receipt["egress"]}
        if (ingress, egress) != expected_artifacts[role]:
            _fail(f"security-domain {role} artifact visibility is not role-bounded")
        for artifact in receipt["ingress"]:
            produced = producers.get(artifact["name"])
            if produced is not None and produced != artifact["sha256"]:
                _fail("security-domain producer/consumer artifact hash differs")
        if {
            item["name"] for item in receipt["negative_canary"]["targets"]
        } != forbidden_artifacts[role]:
            _fail(f"security-domain {role} prohibited visibility is incomplete")
        expected_process_count = 2 if role == "candidate_host" else 1
        if len(receipt["process_receipt_sha256s"]) != expected_process_count:
            _fail(f"security-domain {role} process receipt inventory is incomplete")
        if role == "candidate_host":
            if (
                receipt["secret_policy"] != "broker_only_exact_host"
                or receipt["network"]["policy"] != "host_provider_allowlist"
            ):
                _fail("candidate security domain is not broker-only/provider-allowlisted")
        elif (
            receipt["secret_policy"] != "forbidden"
            or receipt["network"]["policy"] != "deny_all"
        ):
            _fail(f"non-candidate security domain {role} is not closed")
    executable_bindings = {
        "candidate_host": envelope["runner"]["sha256"],
        "scorer_a": scorer_a["sha256"],
        "scorer_b": scorer_b["sha256"],
        "arbiter": arbiter["sha256"],
    }
    for role, expected in executable_bindings.items():
        if security_receipts[role]["executable"]["executable_sha256"] != expected:
            _fail(f"security-domain executable is not the frozen {role} input")

    process = payload["process_identity"]
    process_ids = {
        process["scorer_a_process_id"],
        process["scorer_b_process_id"],
        process["runner_process_id"],
        process["arbiter_process_id"],
    }
    identity_digests = {
        process["scorer_a_identity_sha256"],
        process["scorer_b_identity_sha256"],
        process["runner_identity_sha256"],
        process["arbiter_identity_sha256"],
    }
    if len(process_ids) != 4 or len(identity_digests) != 4:
        _fail("machine-reference runner, scorers, and arbiter are not distinct")
    if (
        process["scorer_a_identity_sha256"] != scorer_a["sha256"]
        or process["scorer_b_identity_sha256"] != scorer_b["sha256"]
        or process["runner_identity_sha256"] != envelope["runner"]["sha256"]
        or process["arbiter_identity_sha256"] != arbiter["sha256"]
    ):
        _fail("machine-reference process identities differ from their bindings")

    rows_a = _machine_scorer_rows(
        scorer_a_value,
        envelope=envelope,
        expected_scorer=scorer_a,
        label="independent scorer A",
    )
    rows_b = _machine_scorer_rows(
        scorer_b_value,
        envelope=envelope,
        expected_scorer=scorer_b,
        label="independent scorer B",
    )
    arbiter_rows = _receipt_rows(
        arbiter_value,
        envelope=envelope,
        label="deterministic arbiter",
    )
    expected_cases = {case["case_id"]: case for case in reference["cases"]}
    known_hard_failures = {item["code"] for item in reference["hard_failures"]}
    candidate_output_rows = candidate_output["rows"]
    candidate_output_by_case: dict[str, Mapping[str, Any]] = {}
    for _index, row in enumerate(candidate_output_rows):
        if row["case_id"] in candidate_output_by_case:
            _fail("retained candidate output contains duplicate case identity")
        if row["case_id"] not in expected_cases:
            _fail("retained candidate output contains an unknown case identity")
        candidate_output_by_case[row["case_id"]] = row
    if set(candidate_output_by_case) != set(expected_cases):
        _fail("retained candidate output does not cover every case")
    scorer_keys = {
        "case_id",
        "expected",
        "observed",
        "duties",
        "hard_failures",
        "scorer_process_id",
        "runner_process_id",
        "false_authority",
        "candidate_output_row_sha256",
    }
    arbiter_keys = {
        "case_id",
        "expected",
        "observed",
        "duties",
        "hard_failures",
        "false_authority",
        "scorer_a_row_sha256",
        "scorer_b_row_sha256",
        "arbiter_process_id",
        "agreement",
    }
    if len(rows_a) != len(rows_b) or len(rows_a) != len(arbiter_rows):
        _fail("machine-reference scorer and arbiter row inventories differ")
    by_case_a: dict[str, Mapping[str, Any]] = {}
    by_case_b: dict[str, Mapping[str, Any]] = {}
    for label, rows, scorer_process_id, target in (
        ("scorer A", rows_a, process["scorer_a_process_id"], by_case_a),
        ("scorer B", rows_b, process["scorer_b_process_id"], by_case_b),
    ):
        for index, row in enumerate(rows):
            if set(row) != scorer_keys:
                _fail(f"machine-reference {label} row {index} keys are not closed")
            case_id = row["case_id"]
            if case_id not in expected_cases or case_id in target:
                _fail(f"machine-reference {label} case identity is invalid")
            case = expected_cases[case_id]
            if row["expected"] != case["expected"] or row["duties"] != case["duties"]:
                _fail(f"machine-reference {label} expected data differs")
            if (
                row["scorer_process_id"] != scorer_process_id
                or row["runner_process_id"] != process["runner_process_id"]
                or not isinstance(row["false_authority"], bool)
            ):
                _fail(f"machine-reference {label} process or authority observation differs")
            output_row = candidate_output_by_case[case_id]
            if row["candidate_output_row_sha256"] != _sha256_bytes(_canonical(output_row)):
                _fail(
                    f"machine-reference {label} row is not bound to candidate output; "
                    "exact agreement cannot be established"
                )
            for field in ("observed", "duties", "hard_failures", "false_authority"):
                if row[field] != output_row[field]:
                    _fail(
                        f"machine-reference {label} row differs from candidate output; "
                        "exact agreement cannot be established"
                    )
            if (
                not isinstance(row["hard_failures"], list)
                or len(row["hard_failures"]) != len(set(row["hard_failures"]))
                or any(item not in known_hard_failures for item in row["hard_failures"])
            ):
                _fail(f"machine-reference {label} hard failures are invalid")
            target[case_id] = row
    if set(by_case_a) != set(expected_cases) or set(by_case_b) != set(expected_cases):
        _fail("machine-reference scorer rows do not cover every case")

    seen_arbiter: set[str] = set()
    duties_seen: set[str] = set()
    mismatch = hard = false_authority = 0
    for index, row in enumerate(arbiter_rows):
        if set(row) != arbiter_keys:
            _fail(f"machine-reference arbiter row {index} keys are not closed")
        case_id = row["case_id"]
        if case_id not in expected_cases or case_id in seen_arbiter:
            _fail("machine-reference arbiter case identity is invalid")
        seen_arbiter.add(case_id)
        scorer_a_row = by_case_a[case_id]
        scorer_b_row = by_case_b[case_id]
        normalized_a = {
            key: value
            for key, value in scorer_a_row.items()
            if key not in {"scorer_process_id", "runner_process_id"}
        }
        normalized_b = {
            key: value
            for key, value in scorer_b_row.items()
            if key not in {"scorer_process_id", "runner_process_id"}
        }
        if normalized_a != normalized_b or row["agreement"] is not True:
            _fail("independent machine scorers did not reach exact agreement")
        if (
            row["scorer_a_row_sha256"] != _sha256_bytes(_canonical(scorer_a_row))
            or row["scorer_b_row_sha256"] != _sha256_bytes(_canonical(scorer_b_row))
            or row["arbiter_process_id"] != process["arbiter_process_id"]
        ):
            _fail("machine-reference arbiter does not bind both scorer rows")
        for field in (
            "expected",
            "observed",
            "duties",
            "hard_failures",
            "false_authority",
        ):
            if row[field] != scorer_a_row[field]:
                _fail("machine-reference arbiter changed a scorer observation")
        duties_seen.update(row["duties"])
        mismatch += int(row["observed"] != row["expected"])
        hard += len(row["hard_failures"])
        false_authority += int(row["false_authority"])
    if seen_arbiter != set(expected_cases):
        _fail("machine-reference arbiter rows do not cover every case")

    # Derive all qualification observations from the retained candidate bytes.
    # Scorer and arbiter rows are only cross-checks; they cannot manufacture a
    # passing observation or keep a stale receipt valid after output changes.
    duties_seen = set()
    mismatch = hard = false_authority = 0
    for case_id, output_row in candidate_output_by_case.items():
        expected = expected_cases[case_id]
        duties_seen.update(output_row["duties"])
        mismatch += int(output_row["observed"] != expected["expected"])
        hard += len(output_row["hard_failures"])
        false_authority += int(output_row["false_authority"])

    required_duties = {item["duty_id"] for item in reference["duties"]}
    duty_coverage = (
        len(duties_seen & required_duties) / len(required_duties)
        if required_duties
        else 1.0
    )
    case_passes = len(arbiter_rows) - mismatch - sum(
        1 for row in arbiter_rows if row["observed"] == row["expected"] and row["hard_failures"]
    )
    case_pass_rate = case_passes / len(arbiter_rows)
    thresholds = reference["thresholds"]
    failures = {
        "machine_reference_case_pass_rate": int(
            case_pass_rate < thresholds["minimum_case_pass_rate"]
        ),
        "machine_reference_duty_coverage": int(
            duty_coverage < thresholds["minimum_duty_coverage"]
        ),
        "machine_reference_hard_failure": int(
            hard > thresholds["maximum_hard_failures"]
        ),
        "machine_reference_false_authority": int(
            false_authority > thresholds["maximum_false_authority"]
        ),
        "compiler_reference_access": 0,
        "evaluator_output_mutation": 0,
        "blind_contamination": 0,
    }
    return _derived(
        "machine_reference_scorer",
        {
            "reference_id": reference["reference_id"],
            "semantic_reference_sha256": reference_digest,
            "candidate_output_record_sha256": candidate_output_record,
            "candidate_execution_record_sha256": candidate_execution_record,
            "candidate_output_sha256": _sha256_bytes(candidate_output_source.raw),
            "candidate_binding_record_sha256": _record_digest(binding),
            "agent_reviewer_count": len(reviewers),
            "agent_model_count": len({item["model_id"] for item in reviewers}),
            "case_count": len(arbiter_rows),
            "case_pass_count": case_passes,
            "case_pass_rate": case_pass_rate,
            "duty_coverage": duty_coverage,
            "duty_missing_count": len(required_duties - duties_seen),
            "hard_failure_observation_count": hard,
            "false_authority_count": false_authority,
            "reference_isolation_pass_rate": 1.0,
            "security_domains_sha256": security_domains_sha256,
            "scorer_panel_sha256": panel_digest,
            "arbiter_sha256": arbiter["sha256"],
            "corpus_role": envelope["corpus"]["role"],
            "corpus_sha256": envelope["corpus"]["sha256"],
            "reference_provenance": "agent_consensus",
            "human_authenticity": "not_claimed",
            "human_attested": False,
            "thresholds": dict(thresholds),
        },
        failures,
        record_sha256,
    )


def _validate_external_attestation(
    *,
    gold: Mapping[str, Any],
    attestation: Any,
    attestation_source: _SourceData,
    semantic_gold_sha256: str,
    trusted_human_approver: Mapping[str, Any] | None,
) -> None:
    """Require an independently retained attestation receipt.

    The semantic manifest's human fields are intentionally treated as claims
    until this separate receipt binds the exact bytes and approval record.  The
    receipt has a small private contract here so this candidate parser does
    not add another public knowledge or evidence engine.
    """

    expected_keys = {
        "schema_version",
        "attestation_identity",
        "attestation_digest",
        "approval_record",
        "approved_at",
        "decision",
        "semantic_gold_sha256",
        "signing_key_id",
        "signature_algorithm",
        "signature_b64",
        "signature_payload_sha256",
        "record_sha256",
    }
    receipt = _require_mapping(attestation, label="external human attestation", keys=expected_keys)
    if receipt["schema_version"] != "deeplaw.external-human-attestation/v1":
        _fail("external human attestation schema version is unsupported")
    if not isinstance(receipt["attestation_identity"], str) or not receipt["attestation_identity"]:
        _fail("external human attestation identity is missing")
    attestation_digest = _sha(receipt["attestation_digest"], label="attestation digest")
    if not isinstance(receipt["approved_at"], str) or not receipt["approved_at"]:
        _fail("external human attestation approval time is missing")
    if receipt["decision"] != "approved":
        _fail("external human attestation is not approved")
    if receipt["signature_algorithm"] != "Ed25519":
        _fail("external human attestation signature algorithm is unsupported")
    if not isinstance(receipt["signing_key_id"], str) or not receipt["signing_key_id"]:
        _fail("external human attestation signing key identity is missing")
    if trusted_human_approver is None:
        _fail("trusted human approver public key is required")
    if set(trusted_human_approver) != {"identity", "key_id", "public_key_b64"}:
        _fail("trusted human approver key descriptor is not closed")
    if (
        not isinstance(trusted_human_approver["identity"], str)
        or not trusted_human_approver["identity"]
        or not isinstance(trusted_human_approver["key_id"], str)
        or not trusted_human_approver["key_id"]
        or not isinstance(trusted_human_approver["public_key_b64"], str)
        or not trusted_human_approver["public_key_b64"]
        or
        trusted_human_approver["identity"] != receipt["attestation_identity"]
    ):
        _fail("trusted human approver key identity mismatch")
    if receipt["semantic_gold_sha256"] != semantic_gold_sha256:
        _fail("external human attestation binds different semantic Gold bytes")
    approval = _require_mapping(
        receipt["approval_record"],
        label="external human attestation approval record",
        keys={"record_id", "record_sha256", "issuer"},
    )
    for key in ("record_id", "issuer"):
        if not isinstance(approval[key], str) or not approval[key]:
            _fail(f"external human attestation approval {key} is missing")
    approval_digest = _sha(approval["record_sha256"], label="approval record digest")
    if receipt["attestation_identity"] != gold["human_approval"]["attestation_identity"]:
        _fail("external human attestation identity differs from semantic Gold")
    if attestation_digest != gold["human_approval"]["attestation_digest"]:
        _fail("external human attestation digest differs from semantic Gold")
    if receipt["approved_at"] != gold["human_approval"]["approved_at"]:
        _fail("external human attestation time differs from semantic Gold")
    if approval != gold["human_approval"]["approval_record"]:
        _fail("external human attestation approval record differs from semantic Gold")
    if _sha256_bytes(attestation_source.raw) == "0" * 64:
        _fail("external human attestation source is not retained")
    if approval_digest == "0" * 64:
        _fail("external human attestation approval digest is a placeholder")
    _record_digest(receipt)
    try:
        signature = base64.b64decode(receipt["signature_b64"], validate=True)
        public_key = base64.b64decode(trusted_human_approver["public_key_b64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise TypedQualificationEvidenceError(
            "external human attestation signature encoding is invalid"
        ) from exc
    if len(signature) != 64 or len(public_key) != 32:
        _fail("external human attestation Ed25519 key/signature length is invalid")
    public_key_digest = _sha256_bytes(public_key)
    if (
        receipt["signing_key_id"] != public_key_digest
        or trusted_human_approver["key_id"] != public_key_digest
    ):
        _fail("external human attestation key id is not bound to public key bytes")
    signature_payload = {
        "schema_version": "deeplaw.external-human-attestation/v1",
        "semantic_gold_sha256": semantic_gold_sha256,
        "attestation_identity": receipt["attestation_identity"],
        "approved_at": receipt["approved_at"],
        "approval_record": dict(approval),
    }
    signature_payload_bytes = _canonical(signature_payload)
    if receipt["signature_payload_sha256"] != _sha256_bytes(signature_payload_bytes):
        _fail("external human attestation signature payload digest mismatch")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            signature_payload_bytes,
        )
    except (InvalidSignature, ValueError) as exc:
        raise TypedQualificationEvidenceError(
            "external human attestation signature is not valid for the trusted key"
        ) from exc


def _legal_expected(value: Any, *, label: str) -> Mapping[str, Any]:
    result = _require_mapping(
        value,
        label=label,
        keys={
            "version_id",
            "authority",
            "fragment",
            "locator",
            "quote_text",
            "quote_sha256",
            "effective_date",
            "exception",
            "proviso",
            "cross_reference",
            "ocr_critical_token",
            "wrong_version",
            "gap",
            "wiki_drill_down",
        },
    )
    for key in ("version_id", "authority", "effective_date", "quote_text"):
        if not isinstance(result[key], str) or not result[key]:
            _fail(f"{label}.{key} is invalid")
    quote_sha256 = _sha(result["quote_sha256"], label=f"{label}.quote_sha256")
    if quote_sha256 != _sha256_bytes(result["quote_text"].encode("utf-8")):
        _fail(f"{label}.quote_sha256 does not match quote_text bytes")
    fragment = _require_mapping(
        result["fragment"],
        label=f"{label}.fragment",
        keys={"document_id", "fragment_id", "text", "text_sha256"},
    )
    for key in ("document_id", "fragment_id", "text"):
        if not isinstance(fragment[key], str) or not fragment[key]:
            _fail(f"{label}.fragment.{key} is invalid")
    if _sha(fragment["text_sha256"], label=f"{label}.fragment.text_sha256") != _sha256_bytes(
        fragment["text"].encode("utf-8")
    ):
        _fail(f"{label}.fragment.text_sha256 does not match fragment text bytes")
    locator = _require_mapping(
        result["locator"],
        label=f"{label}.locator",
        keys={"kind", "value"},
    )
    if any(not isinstance(locator[key], str) or not locator[key] for key in ("kind", "value")):
        _fail(f"{label}.locator is invalid")
    for key in ("exception", "proviso", "cross_reference", "ocr_critical_token"):
        values = result[key]
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item for item in values
        ):
            _fail(f"{label}.{key} is invalid")
    if not isinstance(result["wrong_version"], bool):
        _fail(f"{label}.wrong_version is invalid")
    gap = result["gap"]
    if gap is not None:
        gap_mapping = _require_mapping(gap, label=f"{label}.gap", keys={"code"})
        if not isinstance(gap_mapping["code"], str) or not gap_mapping["code"]:
            _fail(f"{label}.gap.code is invalid")
    drill_down = result["wiki_drill_down"]
    if drill_down is not None:
        drill = _require_mapping(
            drill_down,
            label=f"{label}.wiki_drill_down",
            keys={"source_id", "version_id", "fragment_id", "quote_sha256"},
        )
        for key in ("source_id", "version_id", "fragment_id"):
            if not isinstance(drill[key], str) or not drill[key]:
                _fail(f"{label}.wiki_drill_down.{key} is invalid")
        _sha(drill["quote_sha256"], label=f"{label}.wiki_drill_down.quote_sha256")
    return result


def _parse_legal(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_corpus_sha256: str | None,
) -> dict[str, Any]:
    if expected_corpus_sha256 is None:
        _fail("Legal expected-source corpus binding is required")
    if envelope["payload"]["expected_source"]["sha256"] != expected_corpus_sha256:
        _fail("Legal expected source is bound to a different corpus")
    catalog_value, _ = _source_json(
        envelope["payload"]["source_catalog_source"],
        root=root,
        label="Legal source catalog",
    )
    expected_value, expected_source = _source_json(
        envelope["payload"]["expected_source"],
        root=root,
        label="Legal expected rows",
    )
    observed_value, observed_source = _source_json(
        envelope["payload"]["observed_source"],
        root=root,
        label="Legal observed rows",
    )
    if expected_source.path == observed_source.path:
        _fail("Legal expected and observed evidence must be separate files")
    catalog_wrapper = _require_mapping(
        catalog_value,
        label="Legal source catalog",
        keys={"sources"},
    )
    sources = catalog_wrapper["sources"]
    if not isinstance(sources, list) or len(sources) != 28:
        _fail("Legal source catalog must contain exactly 28 source identities")
    source_ids: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, source in enumerate(sources):
        item = _require_mapping(source, label=f"Legal source[{index}]")
        if set(item) != {
            "source_id",
            "version_id",
            "document_sha256",
            "document_byte_size",
            "media_type",
            "authority",
            "effective_date",
        }:
            _fail("Legal source catalog keys are not closed")
        source_key = (str(item["source_id"]), str(item["version_id"]))
        if source_key in source_ids:
            _fail("Legal source catalog contains duplicate source identity")
        _sha(item["document_sha256"], label="Legal source document")
        if (
            isinstance(item["document_byte_size"], bool)
            or not isinstance(item["document_byte_size"], int)
            or item["document_byte_size"] < 1
        ):
            _fail("Legal source document byte size is invalid")
        if item["media_type"] not in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/html",
            "text/markdown",
        }:
            _fail("Legal source document media type is not an allowed original format")
        if not all(
            isinstance(item[key], str) and item[key]
            for key in ("source_id", "version_id", "authority", "effective_date")
        ):
            _fail("Legal source catalog identity is invalid")
        source_ids[source_key] = item
    original_refs = envelope["payload"]["original_source_refs"]
    original_keys: set[tuple[str, str]] = set()
    for index, descriptor in enumerate(original_refs):
        original = _require_mapping(
            descriptor,
            label=f"Legal original source[{index}]",
            keys={"source_id", "version_id", "source"},
        )
        key = (original["source_id"], original["version_id"])
        if key in original_keys or key not in source_ids:
            _fail("Legal original source identity does not match catalog")
        original_keys.add(key)
        source_ref = original["source"]
        source = _source_data(
            source_ref,
            root=root,
            label=f"Legal original source[{index}]",
        )
        catalog = source_ids[key]
        if (
            source.ref["sha256"] != catalog["document_sha256"]
            or source.ref["byte_size"] != catalog["document_byte_size"]
            or source.ref["media_type"] != catalog["media_type"]
            or _sha256_bytes(source.raw) != catalog["document_sha256"]
            or len(source.raw) != catalog["document_byte_size"]
        ):
            _fail("Legal catalog document identity differs from original source bytes")
    if original_keys != set(source_ids):
        _fail("Legal original source refs do not cover the 28-source catalog")
    expected_rows = _require_rows(expected_value, label="Legal expected rows")
    observed_rows = _receipt_rows(
        observed_value,
        envelope=envelope,
        label="Legal observed rows",
    )
    expected_required = {"source_id", "version_id", "fragment_id", "expected"}
    observed_required = {"source_id", "version_id", "fragment_id", "observed"}
    expected_by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    observed_by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for index, row in enumerate(expected_rows):
        if set(row) != expected_required:
            _fail(f"Legal expected row {index} keys are not closed")
        key = (row["source_id"], row["version_id"], row["fragment_id"])
        if key in expected_by_key or (row["source_id"], row["version_id"]) not in source_ids:
            _fail("Legal expected row identity is invalid")
        _legal_expected(row["expected"], label=f"Legal expected row {index}.expected")
        expected_by_key[key] = row
    for index, row in enumerate(observed_rows):
        if set(row) != observed_required:
            _fail(f"Legal observed row {index} keys are not closed")
        key = (row["source_id"], row["version_id"], row["fragment_id"])
        if key in observed_by_key or key not in expected_by_key:
            _fail("Legal observed row identity does not match expected evidence")
        _legal_expected(row["observed"], label=f"Legal observed row {index}.observed")
        observed_by_key[key] = row
    if set(expected_by_key) != set(observed_by_key):
        _fail("Legal expected and observed rows do not cover the same exact cases")
    seen_sources: set[tuple[str, str]] = set()
    mismatch = 0
    wrong_version = wrong_authority = quote_mismatch = locator_mismatch = 0
    for key, expected_row in expected_by_key.items():
        observed_row = observed_by_key[key]
        source_key = (key[0], key[1])
        source = source_ids[source_key]
        expected = _legal_expected(
            expected_row["expected"],
            label=f"Legal expected {key}.expected",
        )
        observed = _legal_expected(
            observed_row["observed"],
            label=f"Legal observed {key}.observed",
        )
        seen_sources.add(source_key)
        for value, value_label in (
            (expected, f"Legal expected {key}.expected"),
            (observed, f"Legal observed {key}.observed"),
        ):
            if (
                value["version_id"] != key[1]
                or value["fragment"]["document_id"] != key[0]
                or value["fragment"]["fragment_id"] != key[2]
                or value["quote_text"] not in value["fragment"]["text"]
                or value["effective_date"] != source["effective_date"]
            ):
                _fail(f"{value_label} is not bound to its canonical source fragment")
            drill_down = value["wiki_drill_down"]
            if drill_down is not None and (
                drill_down["source_id"] != key[0]
                or drill_down["version_id"] != key[1]
                or drill_down["fragment_id"] != key[2]
                or drill_down["quote_sha256"] != value["quote_sha256"]
            ):
                _fail(f"{value_label}.wiki_drill_down is not bound to its source fragment")
        if (
            expected["version_id"] != source["version_id"]
            or observed["version_id"] != source["version_id"]
            or expected["wrong_version"]
            or observed["wrong_version"]
        ):
            wrong_version += 1
        if (
            expected["authority"] != source["authority"]
            or observed["authority"] != source["authority"]
        ):
            wrong_authority += 1
        if expected["locator"] != observed["locator"]:
            locator_mismatch += 1
        if (
            expected["quote_sha256"] != observed["quote_sha256"]
        ):
            quote_mismatch += 1
        if expected != observed:
            mismatch += 1
    missing_sources = len(set(source_ids) - seen_sources)
    return _derived(
        "legal_rows",
        {
            "exact_source_count": len(source_ids),
            "row_count": len(expected_rows),
            "matching_row_count": len(expected_rows) - mismatch,
            "source_identity_sha256": _identity_digest(sorted(source_ids)),
        },
        {
            "legal_mismatch": mismatch,
            "legal_wrong_version": wrong_version,
            "legal_wrong_authority": wrong_authority,
            "legal_quote_hash_mismatch": quote_mismatch,
            "legal_locator_mismatch": locator_mismatch,
            "legal_source_missing": missing_sources,
        },
        record_sha256,
    )


_V3_PROFESSIONAL_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/html",
        "text/markdown",
    }
)


def _professional_catalog(
    value: Any,
    *,
    label: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    wrapper = _require_mapping(value, label=label, keys={"sources"})
    sources = wrapper["sources"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 64:
        _fail(f"{label} must contain 1..64 source identities")
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    required = {
        "source_id",
        "version_id",
        "document_sha256",
        "document_byte_size",
        "media_type",
        "origin",
        "authority",
        "legal_authority",
        "effective_date",
    }
    for index, source in enumerate(sources):
        item = _require_mapping(source, label=f"{label}.sources[{index}]")
        if set(item) != required:
            _fail(f"{label}.sources[{index}] keys are not closed")
        if any(
            not isinstance(item[key], str) or not item[key]
            for key in ("source_id", "version_id", "effective_date")
        ):
            _fail(f"{label}.sources[{index}] identity is invalid")
        _sha(item["document_sha256"], label=f"{label}.sources[{index}].document_sha256")
        if (
            isinstance(item["document_byte_size"], bool)
            or not isinstance(item["document_byte_size"], int)
            or not 1 <= item["document_byte_size"] <= MAX_SOURCE_BYTES
        ):
            _fail(f"{label}.sources[{index}].document_byte_size is invalid")
        if item["media_type"] not in _V3_PROFESSIONAL_MEDIA_TYPES:
            _fail(f"{label}.sources[{index}].media_type is not a professional source")
        if item["origin"] not in {"user_source", "external_import"}:
            _fail(f"{label}.sources[{index}].origin is invalid")
        if item["authority"] not in {"source_attributed", "unverified"}:
            _fail(f"{label}.sources[{index}].authority is invalid")
        if item["legal_authority"] is not False:
            _fail(f"{label}.sources[{index}] falsely claims legal authority")
        key = (item["source_id"], item["version_id"])
        if key in result:
            _fail(f"{label} contains duplicate source identity")
        result[key] = item
    return result


def _professional_original_sources(
    descriptors: Any,
    *,
    root: Path,
    catalog: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], _SourceData]]:
    if not isinstance(descriptors, list) or not 1 <= len(descriptors) <= 64:
        _fail("Professional original source refs must contain 1..64 sources")
    seen: set[tuple[str, str]] = set()
    sources: dict[tuple[str, str], _SourceData] = {}
    for index, descriptor in enumerate(descriptors):
        item = _require_mapping(
            descriptor,
            label=f"Professional original source[{index}]",
            keys={"source_id", "version_id", "source"},
        )
        source_id = item["source_id"]
        version_id = item["version_id"]
        if not isinstance(source_id, str) or not source_id or not isinstance(
            version_id, str
        ) or not version_id:
            _fail(f"Professional original source[{index}] identity is invalid")
        key = (source_id, version_id)
        expected = catalog.get(key)
        if expected is None or key in seen:
            _fail("Professional original source identity does not match catalog")
        seen.add(key)
        source = _source_data(
            item["source"],
            root=root,
            label=f"Professional original source[{index}]",
        )
        if (
            source.ref["sha256"] != expected["document_sha256"]
            or source.ref["byte_size"] != expected["document_byte_size"]
            or source.ref["media_type"] != expected["media_type"]
            or _sha256_bytes(source.raw) != expected["document_sha256"]
            or len(source.raw) != expected["document_byte_size"]
        ):
            _fail("Professional catalog document identity differs from original bytes")
        sources[key] = source
    if seen != set(catalog):
        _fail("Professional original source refs do not cover the catalog")
    return seen, sources


def _professional_evidence(
    value: Any,
    *,
    label: str,
) -> Mapping[str, Any]:
    result = _require_mapping(
        value,
        label=label,
        keys={
            "document",
            "version",
            "fragment",
            "locator",
            "quote",
            "effective_date",
            "exception",
            "proviso",
            "cross_reference",
            "ocr_critical_token",
            "rejection",
            "gap",
            "wiki_drill_down",
        },
    )
    document = _require_mapping(
        result["document"],
        label=f"{label}.document",
        keys={"source_id"},
    )
    version = _require_mapping(
        result["version"],
        label=f"{label}.version",
        keys={"version_id"},
    )
    if not isinstance(document["source_id"], str) or not document["source_id"]:
        _fail(f"{label}.document.source_id is invalid")
    if not isinstance(version["version_id"], str) or not version["version_id"]:
        _fail(f"{label}.version.version_id is invalid")
    fragment = _require_mapping(
        result["fragment"],
        label=f"{label}.fragment",
        keys={"document_id", "version_id", "fragment_id", "text", "text_sha256"},
    )
    for key in ("document_id", "version_id", "fragment_id", "text"):
        if not isinstance(fragment[key], str) or not fragment[key]:
            _fail(f"{label}.fragment.{key} is invalid")
    if _sha(fragment["text_sha256"], label=f"{label}.fragment.text_sha256") != _sha256_bytes(
        fragment["text"].encode("utf-8")
    ):
        _fail(f"{label}.fragment.text_sha256 does not match fragment text bytes")
    locator = _require_mapping(
        result["locator"],
        label=f"{label}.locator",
        keys={"kind", "value"},
    )
    if any(not isinstance(locator[key], str) or not locator[key] for key in ("kind", "value")):
        _fail(f"{label}.locator is invalid")
    quote = _require_mapping(
        result["quote"],
        label=f"{label}.quote",
        keys={"text", "sha256"},
    )
    if not isinstance(quote["text"], str) or not quote["text"]:
        _fail(f"{label}.quote.text is invalid")
    if _sha(quote["sha256"], label=f"{label}.quote.sha256") != _sha256_bytes(
        quote["text"].encode("utf-8")
    ):
        _fail(f"{label}.quote.sha256 does not match quote bytes")
    if quote["text"] not in fragment["text"]:
        _fail(f"{label}.quote is not contained by its fragment")
    if not isinstance(result["effective_date"], str) or not result["effective_date"]:
        _fail(f"{label}.effective_date is invalid")
    for key in ("exception", "proviso", "cross_reference", "ocr_critical_token"):
        values = result[key]
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item for item in values
        ):
            _fail(f"{label}.{key} is invalid")
    rejection = result["rejection"]
    if rejection is not None:
        rejection = _require_mapping(
            rejection,
            label=f"{label}.rejection",
        )
        code = rejection.get("code")
        if code == "wrong_version_rejected":
            if set(rejection) != {"code", "challenged_version_id"}:
                _fail(f"{label}.rejection keys are not closed for wrong-version rejection")
            if (
                not isinstance(rejection["challenged_version_id"], str)
                or not rejection["challenged_version_id"]
            ):
                _fail(f"{label}.rejection.challenged_version_id is invalid")
        elif code == "false_authority_rejected":
            if set(rejection) != {
                "code",
                "challenged_authority",
                "challenged_legal_authority",
            }:
                _fail(f"{label}.rejection keys are not closed for false-authority rejection")
            if rejection["challenged_authority"] not in {"official", "human_verified"}:
                _fail(f"{label}.rejection.challenged_authority is invalid")
            if rejection["challenged_legal_authority"] is not True:
                _fail(f"{label}.rejection.challenged_legal_authority must be true")
        else:
            _fail(f"{label}.rejection.code is invalid")
    gap = result["gap"]
    if gap is not None:
        gap = _require_mapping(gap, label=f"{label}.gap", keys={"code"})
        if gap["code"] != "ocr_critical_token_gap":
            _fail(f"{label}.gap.code is invalid")
    drill_down = result["wiki_drill_down"]
    if drill_down is not None:
        drill_down = _require_mapping(
            drill_down,
            label=f"{label}.wiki_drill_down",
            keys={"source_id", "version_id", "fragment_id", "locator", "quote_sha256"},
        )
        for key in ("source_id", "version_id", "fragment_id"):
            if not isinstance(drill_down[key], str) or not drill_down[key]:
                _fail(f"{label}.wiki_drill_down.{key} is invalid")
        drill_locator = _require_mapping(
            drill_down["locator"],
            label=f"{label}.wiki_drill_down.locator",
            keys={"kind", "value"},
        )
        if any(
            not isinstance(drill_locator[key], str) or not drill_locator[key]
            for key in ("kind", "value")
        ):
            _fail(f"{label}.wiki_drill_down.locator is invalid")
        _sha(drill_down["quote_sha256"], label=f"{label}.wiki_drill_down.quote_sha256")
    return result


def _professional_parse_row(
    row: Mapping[str, Any],
    *,
    index: int,
    field: str,
    catalog: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[str, tuple[str, str, str], Mapping[str, Any]]:
    required = {
        "case_id",
        "case_type",
        "source_id",
        "version_id",
        "fragment_id",
        field,
    }
    if set(row) != required:
        _fail(f"Professional row {index} keys are not closed")
    if not isinstance(row["case_id"], str) or not row["case_id"]:
        _fail(f"Professional row {index}.case_id is invalid")
    if row["case_type"] not in _V3_PROFESSIONAL_CASE_TYPES:
        _fail(f"Professional row {index}.case_type is invalid")
    if any(
        not isinstance(row[key], str) or not row[key]
        for key in ("source_id", "version_id", "fragment_id")
    ):
        _fail(f"Professional row {index} identity is invalid")
    source_key = (row["source_id"], row["version_id"])
    if source_key not in catalog:
        _fail(f"Professional row {index} source identity is not catalogued")
    evidence = _professional_evidence(
        row[field], label=f"Professional row {index}.{field}"
    )
    evidence_label = f"Professional row {index}.{field}"
    if (
        evidence["document"]["source_id"] != row["source_id"]
        or evidence["version"]["version_id"] != row["version_id"]
        or evidence["fragment"]["document_id"] != row["source_id"]
        or evidence["fragment"]["version_id"] != row["version_id"]
        or evidence["fragment"]["fragment_id"] != row["fragment_id"]
    ):
        _fail(f"{evidence_label} is not bound to its row identity")
    drill_down = evidence["wiki_drill_down"]
    if drill_down is not None and (
        drill_down["source_id"] != row["source_id"]
        or drill_down["version_id"] != row["version_id"]
        or drill_down["fragment_id"] != row["fragment_id"]
        or drill_down["locator"] != evidence["locator"]
        or drill_down["quote_sha256"] != evidence["quote"]["sha256"]
    ):
        _fail(f"{evidence_label}.wiki_drill_down is not exact-source bound")
    if row["case_type"] in {"wrong_version_rejection", "false_authority_rejection"}:
        expected_code = (
            "wrong_version_rejected"
            if row["case_type"] == "wrong_version_rejection"
            else "false_authority_rejected"
        )
        rejection = evidence["rejection"]
        if rejection is None or rejection["code"] != expected_code:
            _fail(f"{evidence_label} lacks explicit {expected_code} code")
        if row["case_type"] == "wrong_version_rejection" and (
            rejection["challenged_version_id"] == row["version_id"]
        ):
            _fail(f"{evidence_label} challenges the correct version instead of a wrong version")
    elif evidence["rejection"] is not None:
        _fail(f"{evidence_label}.rejection is not allowed for this case type")
    if row["case_type"] == "effective_date_exception_proviso_cross_reference" and any(
        not evidence[key] for key in ("exception", "proviso", "cross_reference")
    ):
        _fail(f"{evidence_label} lacks exception, proviso, and cross-reference evidence")
    if row["case_type"] == "ocr_critical_token_gap" and (
        not evidence["ocr_critical_token"] or evidence["gap"] is None
    ):
        _fail(f"{evidence_label} lacks the OCR critical-token Gap")
    if row["case_type"] == "wiki_exact_source_drill_down" and (
        evidence["wiki_drill_down"] is None
    ):
        _fail(f"Professional row {index} lacks exact Wiki drill-down")
    return (
        row["case_type"],
        (row["source_id"], row["version_id"], row["fragment_id"]),
        evidence,
    )


def _parse_professional(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_corpus_sha256: str | None,
) -> dict[str, Any]:
    if expected_corpus_sha256 is None:
        _fail("Professional expected-source corpus binding is required")
    payload = envelope["payload"]
    if payload["expected_source"]["sha256"] != expected_corpus_sha256:
        _fail("Professional expected source is bound to a different corpus")
    catalog_value, _ = _source_json(
        payload["source_catalog_source"],
        root=root,
        label="Professional source catalog",
    )
    expected_value, expected_source = _source_json(
        payload["expected_source"],
        root=root,
        label="Professional expected rows",
    )
    observed_value, observed_source = _source_json(
        payload["observed_source"],
        root=root,
        label="Professional observed rows",
    )
    if expected_source.path == observed_source.path:
        _fail("Professional expected and observed evidence must be separate files")
    catalog = _professional_catalog(catalog_value, label="Professional source catalog")
    _seen_sources, original_sources = _professional_original_sources(
        payload["original_source_refs"],
        root=root,
        catalog=catalog,
    )
    expected_rows = _require_rows(expected_value, label="Professional expected rows")
    observed_rows = _receipt_rows(
        observed_value,
        envelope=envelope,
        label="Professional observed rows",
    )
    expected_by_id: dict[str, Mapping[str, Any]] = {}
    observed_by_id: dict[str, Mapping[str, Any]] = {}
    ParsedProfessional = tuple[
        str,
        tuple[str, str, str],
        Mapping[str, Any],
    ]
    expected_parsed: dict[str, ParsedProfessional] = {}
    observed_parsed: dict[str, ParsedProfessional] = {}
    for index, row in enumerate(expected_rows):
        parsed = _professional_parse_row(
            row,
            index=index,
            field="expected",
            catalog=catalog,
        )
        if row["case_id"] in expected_by_id:
            _fail("Professional expected rows contain duplicate case identity")
        expected_by_id[row["case_id"]] = row
        expected_parsed[row["case_id"]] = parsed
    for index, row in enumerate(observed_rows):
        parsed = _professional_parse_row(
            row,
            index=index,
            field="observed",
            catalog=catalog,
        )
        if row["case_id"] in observed_by_id:
            _fail("Professional observed rows contain duplicate case identity")
        observed_by_id[row["case_id"]] = row
        observed_parsed[row["case_id"]] = parsed
    if set(expected_by_id) != set(observed_by_id):
        _fail("Professional expected and observed rows do not cover the same cases")
    case_types = {parsed[0] for parsed in expected_parsed.values()}
    if case_types != _V3_PROFESSIONAL_CASE_TYPES:
        _fail("Professional evidence does not cover every required case type")

    failures = {key: 0 for key in _V3_PROFESSIONAL_HARD_FAILURES}
    matching_cases = 0
    duty_hits = {key: 0 for key in _V3_PROFESSIONAL_DUTIES}
    duty_totals = {key: 0 for key in _V3_PROFESSIONAL_DUTIES}

    def mark(duty: str, passed: bool, *, failure: str | None = None) -> None:
        duty_totals[duty] += 1
        if passed:
            duty_hits[duty] += 1
        elif failure is not None:
            failures[failure] += 1

    for case_id in expected_by_id:
        expected_case = expected_parsed[case_id]
        observed_case = observed_parsed[case_id]
        expected_type, expected_identity, expected = expected_case
        observed_type, observed_identity, observed = observed_case
        if (
            expected_type == observed_type
            and expected_identity == observed_identity
            and expected == observed
        ):
            matching_cases += 1
        if (
            expected_type != observed_type
            or expected_identity != observed_identity
            or expected != observed
        ):
            failures["expected_observed_mismatch"] += 1
        source_key = (expected_identity[0], expected_identity[1])
        catalog_entry = catalog[source_key]
        source = original_sources[source_key]
        mark(
            "original_bytes",
            len(source.raw) == catalog_entry["document_byte_size"],
            failure="original_bytes_mismatch",
        )
        mark(
            "original_hash",
            _sha256_bytes(source.raw) == catalog_entry["document_sha256"],
            failure="original_hash_mismatch",
        )
        for evidence in (expected, observed):
            mark(
                "document",
                evidence["document"]["source_id"] == expected_identity[0],
                failure="document_identity_mismatch",
            )
            mark(
                "version",
                evidence["version"]["version_id"] == expected_identity[1],
                failure="version_identity_mismatch",
            )
            mark(
                "fragment",
                evidence["fragment"]["document_id"] == expected_identity[0]
                and evidence["fragment"]["version_id"] == expected_identity[1]
                and evidence["fragment"]["fragment_id"] == expected_identity[2],
                failure="fragment_identity_mismatch",
            )
        mark(
            "locator",
            expected["locator"] == observed["locator"],
            failure="locator_invalid",
        )
        mark(
            "effective_date",
            expected["effective_date"]
            == observed["effective_date"]
            == catalog_entry["effective_date"],
            failure="effective_date_mismatch",
        )
        mark(
            "exception",
            expected["exception"] == observed["exception"],
            failure="exception_mismatch",
        )
        mark(
            "proviso",
            expected["proviso"] == observed["proviso"],
            failure="proviso_mismatch",
        )
        mark(
            "cross_reference",
            expected["cross_reference"] == observed["cross_reference"],
            failure="cross_reference_mismatch",
        )
        if expected_type == "wrong_version_rejection":
            mark(
                "wrong_version_rejection",
                expected["rejection"]["code"]
                == observed["rejection"]["code"]
                == "wrong_version_rejected",
                failure="wrong_version_rejection_failure",
            )
        else:
            duty_totals["wrong_version_rejection"] += 1
            duty_hits["wrong_version_rejection"] += 1
        if expected_type == "false_authority_rejection":
            mark(
                "false_authority",
                catalog_entry["legal_authority"] is False
                and catalog_entry["authority"] in {"source_attributed", "unverified"}
                and expected["rejection"]["code"]
                == observed["rejection"]["code"]
                == "false_authority_rejected",
                failure="false_authority_admission",
            )
        else:
            duty_totals["false_authority"] += 1
            duty_hits["false_authority"] += 1
        if expected_type == "ocr_critical_token_gap":
            mark(
                "ocr_critical_token_gap",
                bool(expected["ocr_critical_token"])
                and bool(observed["ocr_critical_token"])
                and expected["gap"] == observed["gap"]
                and expected["gap"]["code"] == "ocr_critical_token_gap",
                failure="ocr_critical_token_gap_missing",
            )
        else:
            duty_totals["ocr_critical_token_gap"] += 1
            duty_hits["ocr_critical_token_gap"] += 1
        if expected_type == "wiki_exact_source_drill_down":
            mark(
                "wiki_exact_source_drill_down",
                expected["wiki_drill_down"] == observed["wiki_drill_down"]
                and expected["wiki_drill_down"] is not None,
                failure="wiki_exact_source_drill_down_failure",
            )
        else:
            duty_totals["wiki_exact_source_drill_down"] += 1
            duty_hits["wiki_exact_source_drill_down"] += 1
    metrics = {
        "case_count": len(expected_rows),
        "matching_case_count": matching_cases,
        "source_count": len(catalog),
        "original_source_count": len(original_sources),
        "required_case_type_count": len(_V3_PROFESSIONAL_CASE_TYPES),
        "required_duty_count": len(_V3_PROFESSIONAL_DUTIES),
        "duty_missing_count": sum(
            1 for duty in _V3_PROFESSIONAL_DUTIES if duty_totals[duty] == 0
        ),
        "observation_mismatch_count": len(expected_rows) - matching_cases,
    }
    for duty in _V3_PROFESSIONAL_DUTIES:
        metric_name = {
            "original_bytes": "original_bytes_preservation_rate",
            "original_hash": "original_hash_match_rate",
            "document": "document_identity_rate",
            "version": "version_identity_rate",
            "fragment": "fragment_identity_rate",
            "locator": "locator_validity_rate",
            "wrong_version_rejection": "wrong_version_rejection_rate",
            "effective_date": "effective_date_rate",
            "exception": "exception_rate",
            "proviso": "proviso_rate",
            "cross_reference": "cross_reference_rate",
            "false_authority": "false_authority_zero_rate",
            "ocr_critical_token_gap": "ocr_critical_token_gap_disposition_rate",
            "wiki_exact_source_drill_down": "wiki_exact_source_drill_down_rate",
        }[duty]
        total = duty_totals[duty]
        metrics[metric_name] = duty_hits[duty] / total if total else 0.0
    return _derived(
        "professional_evidence_rows",
        metrics,
        failures,
        record_sha256,
    )


def _wiki_snapshot(value: Any, *, label: str) -> Mapping[str, Any]:
    result = _require_mapping(value, label=label)
    allowed = {
        "identity",
        "revision_sha256",
        "exists",
        "aliases",
        "relations",
        "file_protected",
    }
    if set(result) != allowed:
        _fail(f"{label} keys are not closed")
    if not isinstance(result["identity"], str) or not result["identity"]:
        _fail(f"{label}.identity is invalid")
    if result["revision_sha256"] is not None:
        _sha(result["revision_sha256"], label=f"{label}.revision_sha256")
    if not isinstance(result["exists"], bool):
        _fail(f"{label}.exists is invalid")
    if result["exists"] and result["revision_sha256"] is None:
        _fail(f"{label}.revision_sha256 is required for an existing state")
    aliases = result["aliases"]
    if not isinstance(aliases, list) or any(
        not isinstance(alias, str) or not alias for alias in aliases
    ) or len(aliases) != len(set(aliases)):
        _fail(f"{label}.aliases is invalid")
    relations = result["relations"]
    if not isinstance(relations, list):
        _fail(f"{label}.relations is invalid")
    relation_keys = {"predicate", "target_identity", "direction"}
    relation_ids: set[tuple[str, str, str]] = set()
    for index, relation in enumerate(relations):
        item = _require_mapping(
            relation,
            label=f"{label}.relations[{index}]",
            keys=relation_keys,
        )
        if any(
            not isinstance(item[key], str) or not item[key]
            for key in relation_keys
        ):
            _fail(f"{label}.relations[{index}] identity is invalid")
        identity = tuple(item[key] for key in ("predicate", "target_identity", "direction"))
        if identity in relation_ids:
            _fail(f"{label}.relations contains duplicate identity")
        relation_ids.add(identity)
    if not isinstance(result["file_protected"], bool):
        _fail(f"{label}.file_protected is invalid")
    return result


def _parse_wiki(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_corpus_sha256: str | None,
) -> dict[str, Any]:
    if expected_corpus_sha256 is None:
        _fail("Wiki expected-source corpus binding is required")
    if envelope["payload"]["expected_source"]["sha256"] != expected_corpus_sha256:
        _fail("Wiki expected source is bound to a different corpus")
    expected_value, expected_source = _source_json(
        envelope["payload"]["expected_source"],
        root=root,
        label="Wiki expected journey rows",
    )
    observed_value, observed_source = _source_json(
        envelope["payload"]["observed_source"],
        root=root,
        label="Wiki observed journey rows",
    )
    if expected_source.path == observed_source.path:
        _fail("Wiki expected and observed evidence must be separate files")
    expected_rows = _require_rows(expected_value, label="Wiki expected journey")
    observed_rows = _receipt_rows(
        observed_value,
        envelope=envelope,
        label="Wiki observed journey",
    )
    operations = {
        "alias",
        "same_name_entity",
        "rename",
        "move",
        "edit",
        "reconcile",
        "backlink",
        "outlink",
        "source_successor",
        "wrong_merge",
        "user_file_protection",
        "full_incremental_equivalence",
    }
    expected_required = {"journey_id", "operation", "before", "after", "expected"}
    observed_required = {"journey_id", "operation", "before", "after", "observed"}
    expected_by_id: dict[str, Mapping[str, Any]] = {}
    observed_by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(expected_rows):
        if set(row) != expected_required or not isinstance(row["journey_id"], str):
            _fail(f"Wiki expected journey row {index} keys are not closed")
        if row["journey_id"] in expected_by_id:
            _fail("Wiki expected journey identity is duplicated")
        if row["operation"] not in operations:
            _fail("Wiki expected journey operation is invalid")
        _wiki_snapshot(row["before"], label=f"Wiki expected row {index}.before")
        _wiki_snapshot(row["after"], label=f"Wiki expected row {index}.after")
        _wiki_snapshot(row["expected"], label=f"Wiki expected row {index}.expected")
        _reject_forbidden_keys(row["expected"])
        expected_by_id[row["journey_id"]] = row
    for index, row in enumerate(observed_rows):
        if set(row) != observed_required or not isinstance(row["journey_id"], str):
            _fail(f"Wiki observed journey row {index} keys are not closed")
        if row["journey_id"] in observed_by_id or row["journey_id"] not in expected_by_id:
            _fail("Wiki observed journey identity does not match expected evidence")
        if row["operation"] != expected_by_id[row["journey_id"]]["operation"]:
            _fail("Wiki observed journey operation differs from expected evidence")
        _wiki_snapshot(row["before"], label=f"Wiki observed row {index}.before")
        _wiki_snapshot(row["after"], label=f"Wiki observed row {index}.after")
        _wiki_snapshot(row["observed"], label=f"Wiki observed row {index}.observed")
        _reject_forbidden_keys(row["observed"])
        observed_by_id[row["journey_id"]] = row
    if set(expected_by_id) != set(observed_by_id):
        _fail("Wiki expected and observed journeys do not cover the same cases")
    counts: Counter[str] = Counter()
    failed_by_operation: Counter[str] = Counter()
    failed = 0
    for journey_id, expected_row in expected_by_id.items():
        observed_row = observed_by_id[journey_id]
        operation = expected_row["operation"]
        expected_before = _wiki_snapshot(
            expected_row["before"], label=f"Wiki expected {journey_id}.before"
        )
        expected_after = _wiki_snapshot(
            expected_row["after"], label=f"Wiki expected {journey_id}.after"
        )
        before = _wiki_snapshot(
            observed_row["before"], label=f"Wiki observed {journey_id}.before"
        )
        after = _wiki_snapshot(
            observed_row["after"], label=f"Wiki observed {journey_id}.after"
        )
        expected = expected_row["expected"]
        observed = observed_row["observed"]
        valid = observed == expected and before == expected_before and after == expected_after
        if operation in {"rename", "move", "edit", "reconcile"}:
            valid = valid and before["identity"] == after["identity"]
        if operation == "edit":
            valid = valid and before["revision_sha256"] != after["revision_sha256"]
        elif operation == "alias":
            valid = valid and (
                before["identity"] == after["identity"]
                and bool(after["aliases"])
                and set(before["aliases"]) != set(after["aliases"])
            )
        elif operation == "same_name_entity":
            valid = valid and (
                before["identity"] != after["identity"]
                and bool(set(before["aliases"]) & set(after["aliases"]))
            )
        elif operation in {"backlink", "outlink", "source_successor"}:
            valid = valid and operation in {
                relation["predicate"] for relation in after["relations"]
            }
        elif operation == "wrong_merge":
            valid = valid and before["identity"] != after["identity"]
        elif operation == "user_file_protection":
            valid = valid and after["file_protected"] is True
        counts[operation] += 1
        if not valid:
            failed += 1
            failed_by_operation[operation] += 1
    missing_operations = sorted(operation for operation in operations if counts[operation] == 0)
    missing = set(missing_operations)
    operation_failure = {
        operation: failed_by_operation[operation] + int(operation in missing)
        for operation in operations
    }
    wiki_failures = {
        "alias_identity_failure": (
            operation_failure["alias"] + operation_failure["same_name_entity"]
        ),
        "rename_move_identity_failure": (
            operation_failure["rename"] + operation_failure["move"]
        ),
        "external_reconcile_failure": (
            operation_failure["edit"] + operation_failure["reconcile"]
        ),
        "link_index_failure": (
            operation_failure["backlink"] + operation_failure["outlink"]
        ),
        "source_successor_failure": operation_failure["source_successor"],
        "wrong_merge_admission": operation_failure["wrong_merge"],
        "user_file_mutation": operation_failure["user_file_protection"],
        "full_incremental_noop_mismatch": operation_failure[
            "full_incremental_equivalence"
        ],
    }
    return _derived(
        "wiki_journey_rows",
        {
            "row_count": len(expected_rows),
            "operation_counts": dict(sorted(counts.items())),
            "required_operations": sorted(operations),
            "missing_operations": missing_operations,
            "wiki_journey_pass_rate": 1.0 if not any(wiki_failures.values()) else 0.0,
            "journey_identity_sha256": _identity_digest(
                [
                    (
                        row["operation"],
                        row["before"]["identity"],
                        row["after"]["identity"],
                    )
                    for row in observed_rows
                ]
            ),
        },
        {
            "wiki_journey_mismatch": failed,
            "wiki_operation_missing": len(missing_operations),
            **wiki_failures,
        },
        record_sha256,
    )


def _context_items(
    value: Any,
    *,
    envelope: Mapping[str, Any],
    label: str,
) -> list[Mapping[str, Any]]:
    wrapper = _require_mapping(value, label=label, keys={"receipt", "items"})
    _receipt_metadata(wrapper["receipt"], envelope=envelope, label=label)
    items = wrapper["items"]
    if not isinstance(items, list) or not items:
        _fail(f"{label}.items are missing")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(items):
        mapping = _require_mapping(
            item,
            label=f"{label}.items[{index}]",
            keys={
                "identity",
                "revision_sha256",
                "selection",
                "version_id",
                "authority",
                "duties",
                "gap_codes",
            },
        )
        if not isinstance(mapping["identity"], str) or not mapping["identity"]:
            _fail(f"{label}.items[{index}] keys are not closed")
        _sha(mapping["revision_sha256"], label=f"{label}.items[{index}].revision_sha256")
        if mapping["selection"] not in {"include", "exclude"}:
            _fail(f"{label}.items[{index}].selection is invalid")
        if not all(
            isinstance(mapping[field], str) and mapping[field]
            for field in ("version_id", "authority")
        ):
            _fail(f"{label}.items[{index}] version/authority identity is invalid")
        for field in ("duties", "gap_codes"):
            values = mapping[field]
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value for value in values
            ) or len(values) != len(set(values)):
                _fail(f"{label}.items[{index}].{field} is invalid")
        result.append(mapping)
    identities = [
        (
            item["identity"],
            item["revision_sha256"],
            item["selection"],
            item["version_id"],
            item["authority"],
        )
        for item in result
    ]
    if len(identities) != len(set(identities)):
        _fail(f"{label} contains duplicate selected identity")
    return result


def _context_expected(value: Any, *, label: str) -> Mapping[str, Any]:
    result = _require_mapping(
        value,
        label=label,
        keys={
            "expected_include",
            "expected_exclude",
            "required_duties",
            "acceptable_gap",
            "hard_failures",
            "projection",
            "projection_budget",
        },
    )
    expected_item_keys = {"identity", "revision_sha256", "version_id", "authority"}
    all_identities: set[tuple[str, str]] = set()
    for field in ("expected_include", "expected_exclude"):
        items = result[field]
        if not isinstance(items, list):
            _fail(f"{label}.{field} must be a list")
        identities: set[tuple[str, str]] = set()
        for index, item in enumerate(items):
            row = _require_mapping(
                item,
                label=f"{label}.{field}[{index}]",
                keys=expected_item_keys,
            )
            if not all(
                isinstance(row[key], str) and row[key]
                for key in ("identity", "version_id", "authority")
            ):
                _fail(f"{label}.{field}[{index}] identity is invalid")
            _sha(row["revision_sha256"], label=f"{label}.{field}[{index}].revision_sha256")
            identity = (row["identity"], row["revision_sha256"])
            if identity in identities or identity in all_identities:
                _fail(f"{label}.{field} contains duplicate identity")
            identities.add(identity)
            all_identities.add(identity)
    duties = result["required_duties"]
    if not isinstance(duties, list) or any(
        not isinstance(item, str) or not item for item in duties
    ) or len(duties) != len(set(duties)):
        _fail(f"{label}.required_duties is invalid")
    gap = _require_mapping(
        result["acceptable_gap"],
        label=f"{label}.acceptable_gap",
        keys={"allowed", "codes"},
    )
    if not isinstance(gap["allowed"], bool) or not isinstance(gap["codes"], list):
        _fail(f"{label}.acceptable_gap is invalid")
    if any(not isinstance(item, str) or not item for item in gap["codes"]):
        _fail(f"{label}.acceptable_gap.codes is invalid")
    hard_failures = _require_mapping(
        result["hard_failures"],
        label=f"{label}.hard_failures",
        keys={"wrong_version", "false_authority"},
    )
    if hard_failures != {"wrong_version": True, "false_authority": True}:
        _fail(f"{label}.hard_failures must freeze both hard-failure semantics")
    if result["projection"] not in {"continuity", "normal", "legal_source_first"}:
        _fail(f"{label}.projection is invalid")
    budget = _require_mapping(
        result["projection_budget"],
        label=f"{label}.projection_budget",
        keys={
            "continuity_max_bytes",
            "normal_max_bytes",
            "legal_source_first_max_bytes",
            "tools_list_max_bytes",
            "global_max_bytes",
        },
    )
    for key in budget:
        value = budget[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail(f"{label}.projection_budget.{key} is invalid")
    if budget["global_max_bytes"] > 65536:
        _fail(f"{label}.projection_budget.global_max_bytes exceeds hard maximum")
    if any(
        budget[key] > budget["global_max_bytes"]
        for key in (
            "continuity_max_bytes",
            "normal_max_bytes",
            "legal_source_first_max_bytes",
            "tools_list_max_bytes",
        )
    ):
        _fail(f"{label}.projection_budget exceeds global maximum")
    return result


def _parse_context(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_corpus_sha256: str | None,
) -> dict[str, Any]:
    payload = envelope["payload"]
    if expected_corpus_sha256 is None:
        _fail("Context expected-source corpus binding is required")
    if payload["expected_source"]["sha256"] != expected_corpus_sha256:
        _fail("Context expected source is bound to a different corpus")
    expected_value, _expected_source = _source_json(
        payload["expected_source"],
        root=root,
        label="Context expected selection",
    )
    expected = _context_expected(expected_value, label="Context expected selection")
    provider, _ = _source_json(
        payload["provider_capsule_source"],
        root=root,
        label="Provider Capsule",
    )
    query, _ = _source_json(
        payload["query_trace_source"],
        root=root,
        label="Query Trace",
    )
    ledger, _ = _source_json(
        payload["ledger_source"],
        root=root,
        label="Canonical Ledger",
    )
    usage, _ = _source_json(
        payload["usage_source"],
        root=root,
        label="Provider usage",
    )
    provider_items = _context_items(
        provider,
        envelope=envelope,
        label="Provider Capsule",
    )
    query_items = _context_items(
        query,
        envelope=envelope,
        label="Query Trace",
    )
    ledger_items = _context_items(
        ledger,
        envelope=envelope,
        label="Canonical Ledger",
    )
    sets = [
        {
            (
                item["identity"],
                item["revision_sha256"],
                item["selection"],
                item["version_id"],
                item["authority"],
                tuple(item["duties"]),
                tuple(item["gap_codes"]),
            )
            for item in items
        }
        for items in (provider_items, query_items, ledger_items)
    ]
    identity_mismatch = int(not (sets[0] == sets[1] == sets[2]))
    expected_include = {
        (item["identity"], item["revision_sha256"], item["version_id"], item["authority"])
        for item in expected["expected_include"]
    }
    expected_exclude = {
        (item["identity"], item["revision_sha256"], item["version_id"], item["authority"])
        for item in expected["expected_exclude"]
    }
    expected_by_identity = {
        item["identity"]: item
        for item in (*expected["expected_include"], *expected["expected_exclude"])
    }
    actual_rows = provider_items
    actual_include = {
        (item["identity"], item["revision_sha256"], item["version_id"], item["authority"])
        for item in actual_rows
        if item["selection"] == "include"
    }
    actual_exclude = {
        (item["identity"], item["revision_sha256"], item["version_id"], item["authority"])
        for item in actual_rows
        if item["selection"] == "exclude"
    }
    include_mismatch = int(actual_include != expected_include)
    exclude_mismatch = int(actual_exclude != expected_exclude)
    wrong_version = 0
    false_authority = 0
    duties_seen: set[str] = set()
    actual_gap_codes: set[str] = set()
    for item in actual_rows:
        duties_seen.update(item["duties"])
        actual_gap_codes.update(item["gap_codes"])
        expected_item = expected_by_identity.get(item["identity"])
        if expected_item is None:
            continue
        if (
            item["revision_sha256"] != expected_item["revision_sha256"]
            or item["version_id"] != expected_item["version_id"]
        ):
            wrong_version += 1
        if item["authority"] != expected_item["authority"]:
            false_authority += 1
    usage_rows = _receipt_rows(usage, envelope=envelope, label="Provider usage")
    fields = {
        "run_id",
        "candidate_commit",
        "candidate_tree",
        "corpus_sha256",
        "runner_identity",
        "runner_sha256",
        "input_tokens",
        "output_tokens",
        "cache_tokens",
        "reasoning_tokens",
        "tools_list_bytes",
        "provider_bytes",
        "relevant_chars",
        "context_chars",
        "evidence_identities",
        "distractor_answer_delta",
    }
    totals: Counter[str] = Counter()
    tools_list_values: list[int | float] = []
    provider_byte_values: list[int | float] = []
    relevant_total: int | float = 0
    context_total: int | float = 0
    relevant_exceeds_context = 0
    for index, row in enumerate(usage_rows):
        if set(row) != fields:
            _fail(f"Provider usage row {index} keys are not closed")
        if row["run_id"] != envelope["run_binding"]["run_id"]:
            _fail("Provider usage row is bound to a different run")
        if (
            row["candidate_commit"] != envelope["candidate_binding"]["commit"]
            or row["candidate_tree"] != envelope["candidate_binding"]["tree"]
            or row["corpus_sha256"] != envelope["corpus"]["sha256"]
            or row["runner_identity"] != envelope["runner"]["identity"]
            or row["runner_sha256"] != envelope["runner"]["sha256"]
        ):
            _fail("Provider usage row is bound to a different candidate/corpus/runner")
        for field in fields - {
            "run_id",
            "candidate_commit",
            "candidate_tree",
            "corpus_sha256",
            "runner_identity",
            "runner_sha256",
            "evidence_identities",
            "tools_list_bytes",
            "provider_bytes",
        }:
            totals[field] += _number(row[field], label=f"Provider usage row {index}.{field}")
        tools_list = _number(
            row["tools_list_bytes"],
            label=f"Provider usage row {index}.tools_list_bytes",
        )
        provider_bytes = _number(
            row["provider_bytes"],
            label=f"Provider usage row {index}.provider_bytes",
        )
        relevant = _number(
            row["relevant_chars"],
            label=f"Provider usage row {index}.relevant_chars",
        )
        context_chars = _number(
            row["context_chars"],
            label=f"Provider usage row {index}.context_chars",
        )
        tools_list_values.append(tools_list)
        provider_byte_values.append(provider_bytes)
        relevant_total += relevant
        context_total += context_chars
        relevant_exceeds_context += int(relevant > context_chars)
        evidence_ids = row["evidence_identities"]
        if not isinstance(evidence_ids, list) or any(
            not isinstance(item, str) or not item for item in evidence_ids
        ):
            _fail(f"Provider usage row {index}.evidence_identities is invalid")
        totals["duplicate_evidence"] += len(evidence_ids) - len(set(evidence_ids))
    budget = expected["projection_budget"]
    payload_limit = {
        "continuity": budget["continuity_max_bytes"],
        "normal": budget["normal_max_bytes"],
        "legal_source_first": budget["legal_source_first_max_bytes"],
    }[expected["projection"]]
    tools_list_peak = max(tools_list_values)
    provider_bytes_peak = max(provider_byte_values)
    # A repeated tools definition is one provider projection, not a budget
    # multiplier. Check every raw row and report the peak payload instead of
    # summing repeated definition bytes across traces.
    tools_limit_failures = sum(
        value > budget["tools_list_max_bytes"] for value in tools_list_values
    )
    payload_limit_failures = sum(value > payload_limit for value in provider_byte_values)
    global_limit_failures = sum(
        value > budget["global_max_bytes"] for value in provider_byte_values
    )
    required_duties = set(expected["required_duties"])
    duty_missing = len(required_duties - duties_seen)
    gap_failures = int(
        (not expected["acceptable_gap"]["allowed"] and bool(actual_gap_codes))
        or bool(actual_gap_codes - set(expected["acceptable_gap"]["codes"]))
    )
    provider_payload_failures = (
        tools_limit_failures + payload_limit_failures + global_limit_failures
    )
    return _derived(
        "context_capsule_selection_usage",
        {
            "selected_identity_count": len(sets[0]),
            "selected_identity_sha256": _identity_digest(sorted(sets[0])),
            "provider_query_ledger_identity_match": not identity_mismatch,
            "expected_include_count": len(expected_include),
            "expected_exclude_count": len(expected_exclude),
            "actual_include_count": len(actual_include),
            "actual_exclude_count": len(actual_exclude),
            "required_duty_count": len(required_duties),
            "observed_duty_count": len(duties_seen),
            "observed_gap_codes": sorted(actual_gap_codes),
            "usage_row_count": len(usage_rows),
            "usage": {
                **dict(totals),
                "tools_list_bytes": tools_list_peak,
                "provider_bytes": provider_bytes_peak,
            },
            "projection": expected["projection"],
            "projection_budget": dict(budget),
            "tools_list_bytes": tools_list_peak,
            "provider_payload_bytes": provider_bytes_peak,
            "provider_payload_pass_rate": (
                1.0 if provider_payload_failures == 0 else 0.0
            ),
            "provider_tokens": {
                key: totals[key]
                for key in ("input_tokens", "output_tokens", "cache_tokens", "reasoning_tokens")
            },
            "duty_coverage_rows": len(duties_seen & required_duties),
            "relevant_chars": relevant_total,
            "context_chars": context_total,
            "duplicate_evidence": totals["duplicate_evidence"],
            "distractor_answer_delta": totals["distractor_answer_delta"],
            "redundancy": (
                1 - relevant_total / context_total
                if context_total
                else 0.0
            ),
        },
        {
            "context_identity_mismatch": identity_mismatch,
            "context_include_mismatch": include_mismatch,
            "context_exclude_mismatch": exclude_mismatch,
            "context_wrong_version": wrong_version,
            "context_false_authority": false_authority,
            "context_duty_missing": duty_missing,
            "context_unacceptable_gap": gap_failures,
            "context_tools_list_exceeded": tools_limit_failures,
            "context_projection_exceeded": payload_limit_failures,
            "context_global_payload_exceeded": global_limit_failures,
            "provider_payload_bound_exceeded": provider_payload_failures,
            "context_relevant_exceeds_context": relevant_exceeds_context,
        },
        record_sha256,
    )


def _parse_scale_v9(
    envelope: Mapping[str, Any],
    *,
    expected_value: Any,
    observed_value: Any,
    record_sha256: str,
) -> dict[str, Any]:
    from benchmarks.v013.scale_qualification_v9 import (
        ACTIVE_GOVERNED_OBJECT_TARGET,
        DEFERRED_100000,
        FRAGMENTS_PER_SOURCE,
        HARD_FAILURE_IDS,
        PROVIDER_HARD_LIMIT_BYTES,
        RUNNER_RELATIVE_PATH,
        SOURCE_BATCH_COUNT,
        WARM_SAMPLE_TARGET,
        verify_report,
    )
    from benchmarks.v013.scale_qualification_v9 import (
        SCHEMA_VERSION as SCALE_V9_SCHEMA_VERSION,
    )

    expected = _require_mapping(
        expected_value,
        label="scale v9 expected contract",
        keys={
            "schema_version",
            "active_governed_object_count",
            "source_file_count",
            "fragments_per_source",
            "query_plan_version",
            "warm_samples",
            "provider_hard_limit_bytes",
            "above_10000_status",
            "deferred_100000",
        },
    )
    exact_expected = {
        "schema_version": "deeplaw.v013-scale-qualification-expected/v9",
        "active_governed_object_count": ACTIVE_GOVERNED_OBJECT_TARGET,
        "source_file_count": SOURCE_BATCH_COUNT,
        "fragments_per_source": FRAGMENTS_PER_SOURCE,
        "query_plan_version": "5",
        "warm_samples": WARM_SAMPLE_TARGET,
        "provider_hard_limit_bytes": PROVIDER_HARD_LIMIT_BYTES,
        "above_10000_status": "experimental_unqualified",
        "deferred_100000": DEFERRED_100000,
    }
    if dict(expected) != exact_expected:
        _fail("scale v9 expected contract differs from the frozen 10k boundary")
    observed = _require_mapping(observed_value, label="scale v9 observed report")
    if observed.get("schema_version") != SCALE_V9_SCHEMA_VERSION:
        _fail("scale v9 observed report schema is unsupported")
    verification = verify_report(observed)
    if verification.get("valid") is not True:
        errors = verification.get("errors")
        detail = errors[0] if isinstance(errors, list) and errors else "unknown validation error"
        _fail(f"scale v9 observed report is invalid: {detail}")

    candidate = observed["candidate_binding"]
    envelope_candidate = envelope["candidate_binding"]
    candidate_pairs = (
        ("commit", candidate["commit"], envelope_candidate["commit"]),
        ("tree", candidate["tree"], envelope_candidate["tree"]),
        ("lock_sha256", candidate["lock_sha256"], envelope_candidate["lock_sha256"]),
        ("wheel_sha256", candidate["wheel"]["sha256"], envelope_candidate["wheel_sha256"]),
        ("sdist_sha256", candidate["sdist"]["sha256"], envelope_candidate["sdist_sha256"]),
    )
    for field, observed_binding, expected_binding in candidate_pairs:
        if observed_binding != expected_binding:
            _fail(f"scale v9 candidate binding mismatch: {field}")
    run = observed["run_binding"]
    envelope_run = envelope["run_binding"]
    if (
        run["run_id"] != envelope_run["run_id"]
        or run["workflow_run_id"] != envelope_run["workflow_run_id"]
    ):
        _fail("scale v9 run binding differs from the typed envelope")
    if (
        run["runner"] != RUNNER_RELATIVE_PATH
        or run["runner_sha256"] != envelope["runner"]["sha256"]
    ):
        _fail("scale v9 runner binding differs from the typed envelope")

    query = observed["warm_samples"]["query"]
    context = observed["warm_samples"]["context"]
    report_metrics = observed["metrics"]
    equivalence = observed["equivalence"]
    user_bytes = observed["user_bytes"]
    provider = observed["provider"]
    metrics = {
        "p50": max(query["p50_ms"], context["p50_ms"]),
        "p95": max(query["p95_ms"], context["p95_ms"]),
        "max": max(query["max_ms"], context["max_ms"]),
        "rss": report_metrics["rss"]["peak_bytes"],
        "storage": report_metrics["storage_bytes"],
        "file_count": report_metrics["file_count"],
        "build": report_metrics["build_duration_ms"],
        "rebuild": report_metrics["rebuild_duration_ms"],
        "full_incremental_noop_equivalence": int(
            equivalence["full_incremental_equal"]
            and equivalence["incremental_noop_equal"]
            and equivalence["exact"]
        ),
        "user_bytes": int(user_bytes["all_unchanged"]),
        "provider_bound": int(
            provider["violation"] is False
            and provider["violation_count"] == 0
            and provider["max_bytes"] <= provider["hard_limit_bytes"]
        ),
        "active_governed_object_count": observed["vault"][
            "active_governed_object_count"
        ],
        "query_sample_count": query["sample_count"],
        "context_sample_count": context["sample_count"],
        "report_sha256": observed["report_sha256"],
    }
    observed_failures = set(observed["hard_failures"])
    unknown_failures = observed_failures - set(HARD_FAILURE_IDS)
    if unknown_failures:
        _fail("scale v9 report contains an unknown hard failure")
    failures = {
        "scale_not_executed": int(observed["status"] != "executed"),
        **{
            failure_id: int(failure_id in observed_failures)
            for failure_id in HARD_FAILURE_IDS
        },
    }
    return _derived("scale_report", metrics, failures, record_sha256)


def _parse_scale(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_corpus_sha256: str | None,
) -> dict[str, Any]:
    if expected_corpus_sha256 is None:
        _fail("Scale expected-source corpus binding is required")
    if envelope["payload"]["expected_source"]["sha256"] != expected_corpus_sha256:
        _fail("Scale expected source is bound to a different corpus")
    expected_source = _source_data(
        envelope["payload"]["expected_source"],
        root=root,
        label="scale expected thresholds",
        media_type="application/json",
    )
    observed_source = _source_data(
        envelope["payload"]["observed_source"],
        root=root,
        label="scale observed report",
        media_type="application/json",
    )
    for label, source in (
        ("scale expected thresholds", expected_source),
        ("scale observed report", observed_source),
    ):
        _safe_artifact_bytes(source, label=label, json_document=True)
    expected_value = _strict_json(expected_source.raw, label="scale expected thresholds")
    observed_value = _strict_json(observed_source.raw, label="scale observed report")
    _reject_projection_strings(expected_value, label="scale expected thresholds")
    _reject_projection_strings(observed_value, label="scale observed report")
    if expected_source.path == observed_source.path:
        _fail("Scale expected and observed evidence must be separate files")
    if (
        envelope["schema_version"] == SCHEMA_V3_VERSION
        and isinstance(observed_value, Mapping)
        and observed_value.get("schema_version")
        == "deeplaw.v013-scale-qualification-report/v9"
    ):
        return _parse_scale_v9(
            envelope,
            expected_value=expected_value,
            observed_value=observed_value,
            record_sha256=record_sha256,
        )
    _reject_forbidden_keys(expected_value)
    _reject_forbidden_keys(observed_value)
    expected_rows = _require_rows(expected_value, label="scale expected")
    observed_rows_value = observed_value
    if envelope["schema_version"] in _V2_COMPATIBLE_SCHEMA_VERSIONS:
        commercial_keys = {
            "evidence_class",
            "fixture_kind",
            "candidate_wheel_sha256",
            "actual_candidate",
            "claim_eligible",
            "receipt",
            "rows",
        }
        commercial = _require_mapping(
            observed_value,
            label="commercial candidate scale",
            keys=commercial_keys,
        )
        if commercial["evidence_class"] != "commercial_candidate_scale":
            _fail("commercial candidate scale evidence class is not claim eligible")
        if commercial["fixture_kind"] != "frozen_candidate_corpus":
            _fail("commercial candidate scale fixture is not frozen candidate corpus")
        if commercial["candidate_wheel_sha256"] != envelope["candidate_binding"]["wheel_sha256"]:
            _fail("commercial candidate scale is bound to a different candidate wheel")
        if commercial["actual_candidate"] is not True or commercial["claim_eligible"] is not True:
            _fail("commercial candidate scale is not an actual claim-eligible candidate run")
        observed_rows_value = {
            "receipt": commercial["receipt"],
            "rows": commercial["rows"],
        }
    observed_rows = _receipt_rows(
        observed_rows_value,
        envelope=envelope,
        label="scale observed",
    )
    expected_required = {"sample_size", "expected_cases", "thresholds"}
    observed_required = {
        "sample_size",
        "latency_ms",
        "rss_bytes",
        "storage_bytes",
        "throughput_per_sec",
        "observed_cases",
        "command",
        "execution_id",
        "exit_code",
    }
    if envelope["schema_version"] in _V2_COMPATIBLE_SCHEMA_VERSIONS:
        observed_required.add("process")
    expected_sizes = {1000, 10000, 100000}
    threshold_keys = {
        "max_latency_ms",
        "max_rss_bytes",
        "max_storage_bytes",
        "min_throughput_per_sec",
    }
    observed_sizes: set[int] = set()
    expected_by_size: dict[int, Mapping[str, Any]] = {}
    for index, row in enumerate(expected_rows):
        if set(row) != expected_required:
            _fail(f"scale expected row {index} keys are not closed")
        size = row["sample_size"]
        if isinstance(size, bool) or not isinstance(size, int) or size in expected_by_size:
            _fail("scale expected rows contain duplicate or invalid sample size")
        if size not in expected_sizes:
            _fail("scale expected rows must cover 1k/10k/100k")
        thresholds = _require_mapping(
            row["thresholds"],
            label=f"scale expected row {index}.thresholds",
            keys=threshold_keys,
        )
        for threshold in threshold_keys:
            _number(
                thresholds[threshold],
                label=f"scale expected row {index}.thresholds.{threshold}",
            )
        expected_cases = row["expected_cases"]
        if not isinstance(expected_cases, list) or not expected_cases:
            _fail(f"scale expected row {index}.expected_cases is invalid")
        expected_case_ids: set[str] = set()
        for case_index, case in enumerate(expected_cases):
            case_value = _require_mapping(
                case,
                label=f"scale expected row {index}.expected_cases[{case_index}]",
                keys={"case_id", "expected"},
            )
            if (
                not isinstance(case_value["case_id"], str)
                or not case_value["case_id"]
                or case_value["case_id"] in expected_case_ids
            ):
                _fail(f"scale expected row {index} has duplicate case identity")
            expected_case_ids.add(case_value["case_id"])
        expected_by_size[size] = row
    if set(expected_by_size) != expected_sizes:
        _fail("scale expected rows lack one or more required sample sizes")
    details: dict[str, dict[str, Any]] = {}
    mismatches = 0
    command_failures = 0
    latency_exceeded = 0
    rss_exceeded = 0
    storage_exceeded = 0
    throughput_below = 0
    execution_ids: set[str] = set()
    for index, row in enumerate(observed_rows):
        if set(row) != observed_required:
            _fail(f"scale observed row {index} keys are not closed")
        size = row["sample_size"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size not in expected_sizes
            or size in observed_sizes
            or size not in expected_by_size
        ):
            _fail("scale report must contain one real row for each 1k/10k/100k sample")
        observed_sizes.add(size)
        if (
            not isinstance(row["command"], str)
            or not row["command"]
            or not isinstance(row["execution_id"], str)
            or not row["execution_id"]
            or row["execution_id"] in execution_ids
        ):
            _fail("scale observed row lacks unique command execution identity")
        execution_ids.add(row["execution_id"])
        if isinstance(row["exit_code"], bool) or not isinstance(row["exit_code"], int):
            _fail("scale observed exit code is invalid")
        if envelope["schema_version"] in _V2_COMPATIBLE_SCHEMA_VERSIONS:
            process_keys = {
                "executable_sha256",
                "pid",
                "parent_pid",
                "process_tree_sha256",
                "input_sha256s",
                "output_sha256",
                "environment_key_allowlist",
                "read_only_mounts",
                "started_at",
                "finished_at",
                "exit_code",
            }
            process = _require_mapping(
                row["process"],
                label=f"scale observed row {index}.process",
                keys=process_keys,
            )
            if process["executable_sha256"] != envelope["runner"]["sha256"]:
                _fail("scale process executable differs from the bound runner")
            if (
                isinstance(process["pid"], bool)
                or not isinstance(process["pid"], int)
                or process["pid"] < 1
                or isinstance(process["parent_pid"], bool)
                or not isinstance(process["parent_pid"], int)
                or process["parent_pid"] < 1
                or process["pid"] == process["parent_pid"]
            ):
                _fail("scale process PID binding is invalid")
            for field in ("process_tree_sha256", "output_sha256"):
                if (
                    not isinstance(process[field], str)
                    or not _SHA256_RE.fullmatch(process[field])
                    or process[field] == "0" * 64
                ):
                    _fail(f"scale process {field} is invalid")
            input_sha256s = process["input_sha256s"]
            required_inputs = {
                envelope["candidate_binding"]["wheel_sha256"],
                envelope["corpus"]["sha256"],
            }
            if (
                not isinstance(input_sha256s, list)
                or not 2 <= len(input_sha256s) <= 256
                or not all(
                    isinstance(digest, str) and _SHA256_RE.fullmatch(digest)
                    for digest in input_sha256s
                )
                or len(input_sha256s) != len(set(input_sha256s))
                or not required_inputs <= set(input_sha256s)
            ):
                _fail("scale process input hashes do not cover candidate and corpus")
            environment_keys = process["environment_key_allowlist"]
            if (
                not isinstance(environment_keys, list)
                or len(environment_keys) > 64
                or not all(
                    isinstance(key, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key)
                    for key in environment_keys
                )
                or len(environment_keys) != len(set(environment_keys))
                or any(
                    key.casefold() in {"home", "codex_home"}
                    or _SECRET_ENV_KEY_RE.search(key)
                    for key in environment_keys
                )
            ):
                _fail("scale process environment allowlist contains a Secret or Host auth key")
            mounts = process["read_only_mounts"]
            if not isinstance(mounts, list) or not mounts:
                _fail("scale process read-only mounts are missing")
            mounted_sha256s: set[str] = set()
            mount_ids: set[str] = set()
            for mount_index, mount in enumerate(mounts):
                mount_value = _require_mapping(
                    mount,
                    label=f"scale process mount {mount_index}",
                    keys={"mount_id", "source_sha256", "mode"},
                )
                mount_id = mount_value["mount_id"]
                source_sha256 = mount_value["source_sha256"]
                if (
                    not isinstance(mount_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", mount_id)
                    or mount_id in mount_ids
                    or not isinstance(source_sha256, str)
                    or not _SHA256_RE.fullmatch(source_sha256)
                    or source_sha256 in mounted_sha256s
                    or mount_value["mode"] != "read_only"
                ):
                    _fail("scale process read-only mount binding is invalid")
                mount_ids.add(mount_id)
                mounted_sha256s.add(source_sha256)
            if set(input_sha256s) != mounted_sha256s:
                _fail("scale process read-only mounts differ from its input hashes")
            try:
                started = datetime.fromisoformat(process["started_at"].replace("Z", "+00:00"))
                finished = datetime.fromisoformat(process["finished_at"].replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError) as error:
                raise TypedQualificationEvidenceError(
                    "scale process timestamps are invalid"
                ) from error
            if started.tzinfo is None or finished.tzinfo is None or finished < started:
                _fail("scale process timestamps are invalid")
            if process["exit_code"] != row["exit_code"]:
                _fail("scale process exit code differs from the observed row")
        command_failures += int(row["exit_code"] != 0)
        values = {
            field: _number(row[field], label=f"scale row {index}.{field}")
            for field in (
                "latency_ms",
                "rss_bytes",
                "storage_bytes",
                "throughput_per_sec",
            )
        }
        observed_cases = row["observed_cases"]
        if not isinstance(observed_cases, list) or not observed_cases:
            _fail(f"scale observed row {index}.observed_cases is invalid")
        observed_by_id: dict[str, Any] = {}
        for case_index, case in enumerate(observed_cases):
            case_value = _require_mapping(
                case,
                label=f"scale observed row {index}.observed_cases[{case_index}]",
                keys={"case_id", "observed"},
            )
            case_id = case_value["case_id"]
            if not isinstance(case_id, str) or not case_id or case_id in observed_by_id:
                _fail(f"scale observed row {index} has duplicate case identity")
            observed_by_id[case_id] = case_value["observed"]
        expected_by_id = {
            case["case_id"]: case["expected"]
            for case in expected_by_size[size]["expected_cases"]
        }
        if set(observed_by_id) != set(expected_by_id):
            _fail("scale observed cases do not cover the frozen expected cases")
        thresholds = expected_by_size[size]["thresholds"]
        latency_exceeded += int(values["latency_ms"] > thresholds["max_latency_ms"])
        rss_exceeded += int(values["rss_bytes"] > thresholds["max_rss_bytes"])
        storage_exceeded += int(values["storage_bytes"] > thresholds["max_storage_bytes"])
        throughput_below += int(
            values["throughput_per_sec"] < thresholds["min_throughput_per_sec"]
        )
        correctness_match = all(
            observed_by_id[case_id] == expected_value
            for case_id, expected_value in expected_by_id.items()
        )
        if not correctness_match:
            mismatches += 1
        details[str(size)] = {
            **values,
            "correctness_match": correctness_match,
            "case_identity_sha256": _identity_digest(sorted(observed_by_id)),
            "execution_id": row["execution_id"],
            "exit_code": row["exit_code"],
            "thresholds": dict(thresholds),
        }
    if observed_sizes != expected_sizes:
        _fail("scale report lacks one or more required sample rows")
    return _derived(
        "scale_report",
        {"sample_sizes": sorted(observed_sizes), "samples": details},
        {
            "scale_correctness_mismatch": mismatches,
            "scale_command_failure": command_failures,
            "scale_latency_exceeded": latency_exceeded,
            "scale_rss_exceeded": rss_exceeded,
            "scale_storage_exceeded": storage_exceeded,
            "scale_throughput_below": throughput_below,
        },
        record_sha256,
    )


def _candidate_version_from_name(name: Any, *, role: str) -> str:
    if not isinstance(name, str):
        _fail(f"candidate {role} artifact name is invalid")
    if role == "wheel":
        match = re.fullmatch(
            r"deeplaw-(?P<version>[0-9][A-Za-z0-9.!+_-]*)-[^-]+-[^-]+-[^-]+\.whl",
            name,
        )
    else:
        match = re.fullmatch(
            r"deeplaw-(?P<version>[0-9][A-Za-z0-9.!+_-]*)\.tar\.gz",
            name,
        )
    if match is None:
        _fail(f"candidate {role} artifact name is invalid")
    return match.group("version")


def _validate_supply_artifact(
    value: Any,
    *,
    kind: str,
    candidate: Mapping[str, Any],
    candidate_version: str,
    artifact_sizes: Mapping[str, int] | None = None,
) -> None:
    label = kind.upper()
    if not isinstance(value, Mapping):
        _fail(f"{label} artifact must be an object")
    if kind == "sbom":
        if value.get("bomFormat") != "CycloneDX" or value.get("specVersion") not in {
            "1.5",
            "1.6",
        }:
            _fail("SBOM is not CycloneDX 1.5/1.6")
        metadata = value.get("metadata")
        metadata_component = metadata.get("component") if isinstance(metadata, Mapping) else None
        if not isinstance(metadata_component, Mapping):
            _fail("SBOM metadata component is missing")
        if (
            metadata_component.get("name") != PACKAGE_NAME
            or metadata_component.get("version") != candidate_version
        ):
            _fail("SBOM metadata component does not bind candidate version")
        components = value.get("components")
        if not isinstance(components, list) or not components:
            _fail("SBOM has no component rows")
    elif kind == "openvex":
        if value.get("@context") != "https://openvex.dev/ns/v0.2.0":
            _fail("OpenVEX context is unsupported")
        statements = value.get("statements")
        if not isinstance(statements, list) or not statements:
            _fail("OpenVEX has no statement rows")
        expected_product = f"pkg:pypi/{PACKAGE_NAME}@{candidate_version}"
        for index, statement in enumerate(statements):
            row = _require_mapping(statement, label=f"OpenVEX.statements[{index}]")
            products = row.get("products")
            if not isinstance(products, list) or expected_product not in {
                item.get("@id") for item in products if isinstance(item, Mapping)
            }:
                _fail("OpenVEX statement is not bound to the candidate version")
    elif kind == "licenses":
        _validate_contract(
            value,
            "installed-license-inventory.v1.schema.json",
            label="installed license inventory",
        )
        _record_digest(value)
        if (
            value.get("status") != "passed"
            or value.get("blocked") != []
            or value.get("review_required") != []
            or value.get("package_count") != len(value.get("packages", []))
        ):
            _fail("installed license inventory did not pass")
        binding = _require_mapping(value.get("binding"), label="license inventory binding")
        if (
            binding.get("commit") != candidate["commit"]
            or binding.get("tree") != candidate["tree"]
            or binding.get("lock_sha256") != candidate["lock_sha256"]
            or binding.get("package_version") != candidate_version
            or binding.get("worktree_clean") is not True
        ):
            _fail("installed license inventory is bound to a different candidate")
        packages = value.get("packages")
        if not isinstance(packages, list) or not packages:
            _fail("installed license inventory does not contain the candidate package")
        if any(
            not isinstance(item, Mapping)
            or item.get("status") not in {"approved", "reviewed_exception"}
            for item in packages
        ):
            _fail("installed license inventory contains a blocked or unresolved package")
        if not any(
            item.get("normalized_name") == PACKAGE_NAME
            and item.get("version") == candidate_version
            for item in packages
        ):
            _fail("installed license inventory does not contain the candidate package")
    else:
        _validate_contract(
            value,
            "reproducible-build-report.v2.schema.json",
            label="reproducible build provenance",
        )
        _record_digest(value)
        binding = _require_mapping(value.get("binding"), label="build provenance binding")
        if (
            value.get("repository_commit") != candidate["commit"]
            or value.get("lock_sha256") != candidate["lock_sha256"]
            or binding.get("commit") != candidate["commit"]
            or binding.get("tree") != candidate["tree"]
            or binding.get("lock_sha256") != candidate["lock_sha256"]
            or binding.get("package_version") != candidate_version
            or binding.get("worktree_clean") is not True
            or value.get("working_tree_dirty") is not False
            or value.get("reproducible") is not True
            or value.get("package_inventory_verified") is not True
            or value.get("artifact_release_eligible") is not True
            or value.get("artifact_release_blockers") != []
        ):
            _fail("reproducible build provenance is not bound to the candidate")
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != 2:
            _fail("reproducible build provenance artifacts are incomplete")
        observed: dict[str, Mapping[str, Any]] = {}
        for index, artifact in enumerate(artifacts):
            row = _require_mapping(
                artifact,
                label=f"reproducible provenance artifact[{index}]",
            )
            name = row.get("name")
            if not isinstance(name, str) or not name.startswith(f"{PACKAGE_NAME}-"):
                _fail("reproducible provenance artifact name is invalid")
            if name.endswith(".whl"):
                role = "wheel"
            elif name.endswith(".tar.gz"):
                role = "sdist"
            else:
                _fail("reproducible provenance artifact type is invalid")
            if role in observed:
                _fail("reproducible provenance contains duplicate artifact role")
            observed[role] = row
            if _sha(row.get("sha256"), label=f"provenance {role} hash") != candidate[
                f"{role}_sha256"
            ]:
                _fail("reproducible provenance artifact hash differs from candidate")
            if artifact_sizes is not None and row.get("byte_size") != artifact_sizes[role]:
                _fail("reproducible provenance artifact size differs from retained bytes")
        if set(observed) != {"wheel", "sdist"}:
            _fail("reproducible provenance artifact roles are incomplete")


def _parse_retained(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
) -> dict[str, Any]:
    payload = envelope["payload"]
    candidate = envelope["candidate_binding"]
    candidate_build, _ = _source_json(
        payload["candidate_build_source"],
        root=root,
        label="Candidate Full reproducible build report",
        allow_count_paths=frozenset(
            {
                ("artifacts", "0", "path_count"),
                ("artifacts", "1", "path_count"),
                ("binding", "contracts", "count"),
            }
        ),
    )
    _validate_contract(
        candidate_build,
        "reproducible-build-report.v2.schema.json",
        label="Candidate Full reproducible build report",
    )
    candidate_build_record = _record_digest(candidate_build)
    build_binding = _require_mapping(
        candidate_build.get("binding"),
        label="Candidate Full build binding",
    )
    if (
        candidate_build.get("repository_commit") != candidate["commit"]
        or candidate_build.get("lock_sha256") != candidate["lock_sha256"]
        or build_binding.get("commit") != candidate["commit"]
        or build_binding.get("tree") != candidate["tree"]
        or build_binding.get("lock_sha256") != candidate["lock_sha256"]
        or build_binding.get("worktree_clean") is not True
        or candidate_build.get("working_tree_dirty") is not False
        or candidate_build.get("reproducible") is not True
        or candidate_build.get("package_inventory_verified") is not True
        or candidate_build.get("artifact_release_eligible") is not True
        or candidate_build.get("artifact_release_blockers") != []
    ):
        _fail("Candidate Full reproducible build report is not bound to the candidate")
    build_artifacts = candidate_build.get("artifacts")
    if not isinstance(build_artifacts, list) or len(build_artifacts) != 2:
        _fail("Candidate Full reproducible build report must contain wheel and sdist")
    build_by_role: dict[str, Mapping[str, Any]] = {}
    for index, artifact in enumerate(build_artifacts):
        row = _require_mapping(
            artifact,
            label=f"Candidate Full build artifact[{index}]",
        )
        name = row.get("name")
        if not isinstance(name, str) or not name.startswith(f"{PACKAGE_NAME}-"):
            _fail("Candidate Full build artifact name is invalid")
        if name.endswith(".whl"):
            role = "wheel"
        elif name.endswith(".tar.gz"):
            role = "sdist"
        else:
            _fail("Candidate Full build artifact type is invalid")
        if role in build_by_role:
            _fail("Candidate Full build report contains duplicate artifact role")
        _sha(row.get("sha256"), label=f"Candidate Full {role} hash")
        if isinstance(row.get("byte_size"), bool) or not isinstance(row.get("byte_size"), int):
            _fail("Candidate Full build artifact size is invalid")
        build_by_role[role] = row
    if set(build_by_role) != {"wheel", "sdist"}:
        _fail("Candidate Full build report artifact roles are incomplete")
    wheel_version = _candidate_version_from_name(build_by_role["wheel"]["name"], role="wheel")
    sdist_version = _candidate_version_from_name(build_by_role["sdist"]["name"], role="sdist")
    if wheel_version != sdist_version or build_binding.get("package_version") != wheel_version:
        _fail("Candidate Full build package version differs from artifact identities")
    pre_publish, _ = _source_json(
        payload["pre_publish_receipt_source"],
        root=root,
        label="pre-publish artifact gate",
        allow_count_paths=frozenset({("builds", "count")}),
    )
    _validate_contract(
        pre_publish,
        "pre-publish-artifact-gate.v1.schema.json",
        label="pre-publish artifact gate",
    )
    pre_record = _record_digest(pre_publish)
    for field in ("commit", "tree", "lock_sha256"):
        if pre_publish["candidate"][field] != candidate[field]:
            _fail(f"pre-publish candidate mismatch: {field}")
    if pre_publish["builds"]["byte_identical"] is not True:
        _fail("pre-publish byte-identical claim is not true")
    for build_id in ("first", "second"):
        build_receipt = pre_publish["builds"][build_id]
        _sha(build_receipt["receipt_sha256"], label=f"pre-publish {build_id} receipt")
        if build_receipt["receipt_sha256"] == "0" * 64:
            _fail(f"pre-publish {build_id} receipt is a placeholder")
        for role in ("wheel", "sdist"):
            if _sha(
                build_receipt[f"{role}_sha256"],
                label=f"pre-publish {build_id} {role} hash",
            ) != build_by_role[role]["sha256"]:
                _fail("pre-publish build hash differs from Candidate Full report")
    retained_value, retained_source = _source_json(
        payload["retained_candidate_source"],
        root=root,
        label="retained candidate artifact manifest",
    )
    _validate_contract(
        retained_value,
        "retained-candidate-artifacts.v1.schema.json",
        label="retained candidate artifact manifest",
    )
    if (
        retained_value["package_version"] != wheel_version
        or retained_value["git_commit"] != candidate["commit"]
        or retained_value["git_tree"] != candidate["tree"]
        or retained_value["lock_sha256"] != candidate["lock_sha256"]
    ):
        _fail("retained candidate artifact manifest is bound to a different candidate")
    wheel = _source_data(
        payload["wheel_source"],
        root=root,
        label="retained wheel bytes",
        media_type="application/octet-stream",
    )
    sdist = _source_data(
        payload["sdist_source"],
        root=root,
        label="retained sdist bytes",
        media_type="application/octet-stream",
    )
    observed = {"wheel": wheel, "sdist": sdist}
    for role, artifact in observed.items():
        manifest_artifact = retained_value[role]
        build_artifact = build_by_role[role]
        if (
            manifest_artifact["sha256"] != _sha256_bytes(artifact.raw)
            or manifest_artifact["bytes"] != len(artifact.raw)
            or manifest_artifact["sha256"] != candidate[f"{role}_sha256"]
            or manifest_artifact["sha256"] != build_artifact["sha256"]
            or manifest_artifact["bytes"] != build_artifact["byte_size"]
            or manifest_artifact["filename"] != build_artifact["name"]
        ):
            _fail(f"retained {role} bytes differ from Candidate Full identities")
    retained_receipt = _require_mapping(
        pre_publish["retained_artifacts"],
        label="pre-publish retained artifacts",
    )
    if (
        retained_receipt["manifest_path"] != payload["retained_candidate_source"]["relative_path"]
        or retained_receipt["manifest_sha256"] != _sha256_bytes(retained_source.raw)
    ):
        _fail("pre-publish receipt does not identify retained candidate manifest bytes")
    for role, artifact in observed.items():
        receipt_artifact = retained_receipt[role]
        if (
            receipt_artifact["sha256"] != _sha256_bytes(artifact.raw)
            or receipt_artifact["byte_size"] != len(artifact.raw)
            or receipt_artifact["name"] != retained_value[role]["filename"]
            or receipt_artifact["retained_path"] != payload[f"{role}_source"]["relative_path"]
        ):
            _fail(f"pre-publish retained {role} identity mismatch")
    auxiliary: dict[str, str] = {}
    for field, label in (
        ("sbom", "SBOM"),
        ("openvex", "OpenVEX"),
        ("licenses", "licenses"),
        ("provenance", "provenance"),
    ):
        allow_count_paths = frozenset()
        if field == "licenses":
            allow_count_paths = frozenset(
                {
                    ("package_count",),
                    ("binding", "contracts", "count"),
                }
            )
        elif field == "provenance":
            allow_count_paths = frozenset(
                {
                    ("artifacts", "0", "path_count"),
                    ("artifacts", "1", "path_count"),
                    ("binding", "contracts", "count"),
                }
            )
        value, source = _source_json(
            payload[f"{field}_source"],
            root=root,
            label=label,
            allow_count_paths=allow_count_paths,
        )
        _validate_supply_artifact(
            value,
            kind=field,
            candidate=candidate,
            candidate_version=wheel_version,
            artifact_sizes={role: len(artifact.raw) for role, artifact in observed.items()},
        )
        receipt_artifact = pre_publish[field]
        if (
            receipt_artifact["path"] != source.ref["relative_path"]
            or receipt_artifact["sha256"] != _sha256_bytes(source.raw)
        ):
            _fail(f"pre-publish {label} receipt does not identify retained bytes")
        auxiliary[field] = _sha256_bytes(source.raw)
    return _derived(
        "retained_supply_chain",
        {
            "candidate_build_record_sha256": candidate_build_record,
            "pre_publish_record_sha256": pre_record,
            "retained_manifest_sha256": _sha256_bytes(retained_source.raw),
            "wheel_sha256": _sha256_bytes(wheel.raw),
            "sdist_sha256": _sha256_bytes(sdist.raw),
            "wheel_size": len(wheel.raw),
            "sdist_size": len(sdist.raw),
            "auxiliary_sha256": auxiliary,
            "public_redownload_verified": False,
            "supply_chain_pass_rate": 1.0,
        },
        {
            "retained_wheel_mismatch": 0,
            "retained_sdist_mismatch": 0,
            "retained_supply_chain_mismatch": 0,
            "artifact_hash_mismatch": 0,
            "secret_leak": 0,
            "private_path_disclosure": 0,
        },
        record_sha256,
    )


_PARSERS = {
    "candidate_full_junit": _parse_junit,
    "candidate_platform_receipt": _parse_platform,
    "host_event_sequence": _parse_host,
    "exact_wheel_execution": _parse_exact_wheel,
    "human_gold_scorer": _parse_human_gold,
    "machine_reference_scorer": _parse_machine_reference,
    "legal_rows": _parse_legal,
    "professional_evidence_rows": _parse_professional,
    "wiki_journey_rows": _parse_wiki,
    "context_capsule_selection_usage": _parse_context,
    "scale_report": _parse_scale,
    "retained_supply_chain": _parse_retained,
}


def parse_typed_evidence(
    source: Path | str,
    *,
    root: Path | str | None = None,
    expected_candidate: Mapping[str, Any] | None = None,
    expected_run_id: str | None = None,
    expected_workflow_run_id: int | None = None,
    expected_candidate_run_id: int | None = None,
    expected_corpus_sha256: str | None = None,
    expected_runner: Mapping[str, Any] | None = None,
    expected_scorer: Mapping[str, Any] | None = None,
    trusted_human_approver: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one typed manifest and derive its evidence metrics."""

    manifest, evidence_root, _explicit_root = _prepare_root(Path(source), root)
    try:
        envelope_value = _strict_json(
            manifest.read_bytes(),
            label="typed evidence manifest",
        )
    except OSError as exc:
        raise TypedQualificationEvidenceError(
            "typed evidence manifest could not be read"
        ) from exc
    if not isinstance(envelope_value, Mapping):
        _fail("typed evidence manifest must be an object")
    _reject_forbidden_keys(envelope_value)
    if envelope_value.get("schema_version") == SCHEMA_V3_VERSION:
        _reject_v3_competitive_fields(envelope_value)
    kind, record = _validate_envelope(
        envelope_value,
        expected_candidate=expected_candidate,
        expected_run_id=expected_run_id,
        expected_workflow_run_id=expected_workflow_run_id,
        expected_corpus_sha256=expected_corpus_sha256,
        expected_runner=expected_runner,
        expected_scorer=expected_scorer,
    )
    refs = _source_refs(kind, envelope_value["payload"])
    sources = [
        _source_data(ref, root=evidence_root, label=f"{kind} source[{index}]")
        for index, ref in enumerate(refs)
    ]
    referenced_paths = [item.path for item in sources]
    if kind in {"legal_rows", "professional_evidence_rows"}:
        original_refs = envelope_value["payload"]["original_source_refs"]
        if not isinstance(original_refs, list):
            _fail("Original source references are missing")
        for index, descriptor in enumerate(original_refs):
            original = _require_mapping(
                descriptor,
                label=f"Original source[{index}]",
                keys={"source_id", "version_id", "source"},
            )
            referenced_paths.append(
                _source_data(
                    original["source"],
                    root=evidence_root,
                    label=f"Original source[{index}]",
                ).path
            )
    _check_manifest_closure(
        root=evidence_root,
        manifest=manifest,
        referenced=referenced_paths,
    )
    parser = _PARSERS.get(kind)
    if parser is None:
        _fail("typed evidence kind has no parser")
    parser_kwargs: dict[str, Any] = {
        "root": evidence_root,
        "record_sha256": record,
    }
    if kind == "human_gold_scorer":
        parser_kwargs["trusted_human_approver"] = trusted_human_approver
    if kind == "exact_wheel_execution":
        parser_kwargs["expected_candidate_run_id"] = expected_candidate_run_id
    if kind in {
        "legal_rows",
        "professional_evidence_rows",
        "wiki_journey_rows",
        "context_capsule_selection_usage",
        "scale_report",
        "host_event_sequence",
    }:
        parser_kwargs["expected_corpus_sha256"] = expected_corpus_sha256
    result = parser(envelope_value, **parser_kwargs)
    if envelope_value["schema_version"] == SCHEMA_V2_VERSION:
        result["schema_version"] = DERIVED_V2_SCHEMA_VERSION
    elif envelope_value["schema_version"] == SCHEMA_V3_VERSION:
        result["schema_version"] = DERIVED_V3_SCHEMA_VERSION
    return result


def parse_candidate_full_junit(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_candidate_platform_receipt(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_host_event_sequence(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_exact_wheel_execution(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_human_gold_scorer(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_machine_reference_scorer(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_legal_rows(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_professional_evidence_rows(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_wiki_journey_rows(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_context_capsule_selection_usage(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_scale_report(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def parse_retained_supply_chain(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return parse_typed_evidence(*args, **kwargs)


def _load_cli_descriptor(path: Path, *, label: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} must be a regular non-symlink file")
    try:
        value = _strict_json(path.read_bytes(), label=label)
    except OSError as exc:
        raise TypedQualificationEvidenceError(f"{label} could not be read") from exc
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    _reject_forbidden_keys(value)
    return value


def _cli_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse one typed raw qualification evidence manifest and emit derived metrics."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        "--candidate-json",
        dest="candidate_path",
        type=Path,
        required=True,
    )
    parser.add_argument("--run", "--run-json", dest="run_path", type=Path, required=True)
    parser.add_argument("--corpus", "--corpus-json", dest="corpus_path", type=Path, required=True)
    parser.add_argument("--runner", "--runner-json", dest="runner_path", type=Path, required=True)
    parser.add_argument("--scorer", "--scorer-json", dest="scorer_path", type=Path, required=True)
    parser.add_argument(
        "--trusted-human-approver",
        "--trusted-human-key",
        dest="trusted_human_path",
        type=Path,
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        candidate = _load_cli_descriptor(args.candidate_path, label="candidate descriptor")
        if set(candidate) != {"commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"}:
            _fail("candidate descriptor keys are not closed")
        run = _load_cli_descriptor(args.run_path, label="run descriptor")
        if set(run) != {"run_id", "workflow_run_id"}:
            _fail("run descriptor keys are not closed")
        if (
            not isinstance(run["run_id"], str)
            or not run["run_id"]
            or isinstance(run["workflow_run_id"], bool)
            or not isinstance(run["workflow_run_id"], int)
            or run["workflow_run_id"] < 1
        ):
            _fail("run descriptor identity is invalid")
        corpus = _load_cli_descriptor(args.corpus_path, label="corpus descriptor")
        if set(corpus) != {"sha256", "role"}:
            _fail("corpus descriptor keys are not closed")
        _sha(corpus["sha256"], label="corpus descriptor sha256")
        if corpus["role"] not in _LEGACY_CORPUS_ROLES | _V3_CORPUS_ROLES:
            _fail("corpus descriptor role is invalid")
        runner = _load_cli_descriptor(args.runner_path, label="runner descriptor")
        scorer = _load_cli_descriptor(args.scorer_path, label="scorer descriptor")
        for label, value in (("runner", runner), ("scorer", scorer)):
            if set(value) != {"identity", "sha256"}:
                _fail(f"{label} descriptor keys are not closed")
            if not isinstance(value["identity"], str) or not value["identity"]:
                _fail(f"{label} descriptor identity is invalid")
            _sha(value["sha256"], label=f"{label} descriptor sha256")
        trusted = None
        if args.trusted_human_path is not None:
            trusted_value = _load_cli_descriptor(
                args.trusted_human_path,
                label="trusted human approver descriptor",
            )
            if set(trusted_value) != {"identity", "key_id", "public_key_b64"}:
                _fail("trusted human approver descriptor keys are not closed")
            trusted = trusted_value
        result = parse_typed_evidence(
            args.manifest,
            root=args.root,
            expected_candidate=candidate,
            expected_run_id=run["run_id"],
            expected_workflow_run_id=run["workflow_run_id"],
            expected_corpus_sha256=corpus["sha256"],
            expected_runner=runner,
            expected_scorer=scorer,
            trusted_human_approver=trusted,
        )
        output = _canonical(result).decode("utf-8") + "\n"
        if args.output is None:
            sys.stdout.write(output)
        else:
            if args.output.exists() and args.output.is_symlink():
                _fail("derived output must not be a symlink")
            args.output.write_text(output, encoding="utf-8")
        return 0
    except (OSError, TypedQualificationEvidenceError, ValueError) as exc:
        print(f"typed qualification evidence rejected: {exc}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DERIVED_SCHEMA_VERSION",
    "DERIVED_V2_SCHEMA_VERSION",
    "DERIVED_V3_SCHEMA_VERSION",
    "SCHEMA_V2_VERSION",
    "SCHEMA_V3_VERSION",
    "SCHEMA_VERSION",
    "TypedQualificationEvidenceError",
    "main",
    "parse_candidate_full_junit",
    "parse_candidate_platform_receipt",
    "parse_context_capsule_selection_usage",
    "parse_exact_wheel_execution",
    "parse_host_event_sequence",
    "parse_human_gold_scorer",
    "parse_legal_rows",
    "parse_machine_reference_scorer",
    "parse_professional_evidence_rows",
    "parse_retained_supply_chain",
    "parse_scale_report",
    "parse_typed_evidence",
    "parse_wiki_journey_rows",
]
