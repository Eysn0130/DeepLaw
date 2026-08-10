"""Fail-closed verification for the benchmark-only v0.13 outcome package.

This module is deliberately smaller than a release assembler.  It validates a content-
addressed owner manifest, reopens every declared file, and delegates the authority of a
product result to the existing provenance-bound Gate Result validator.  Version 1 can record
only preparation, non-execution, failure, or development downgrade.  It deliberately rejects
``passed`` until each product has a dedicated deterministic validator whose identity and bytes
are frozen outside the candidate.  ``assembly_enabled`` and ``claim_eligible`` remain closed
false values.

The ``build_synthetic_fixture``/``dry_run`` helpers are credential-free development seams.
They write only caller-provided temporary files and intentionally create three
``prepared_not_executed`` outcomes.  Their output is not external evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from benchmarks.release.provenance_gate_result import (
    ProvenanceGateResultError,
    canonical_json,
    result_sha256,
    validate_gate_result,
)

SCHEMA_VERSION = "deeplaw.v013-product-outcome-package/v1"
SCHEMA_FILENAME = "v013-product-outcome-package.v1.schema.json"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ("continuity", "wiki", "legal")
PRODUCT_GATE_IDS = {
    "continuity": "deeplaw.v013.outcome.continuity",
    "wiki": "deeplaw.v013.outcome.wiki",
    "legal": "deeplaw.v013.outcome.legal",
}
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024

_DEDICATED_PURPOSE_ARTIFACT_KINDS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"candidate_wheel"}),
    "protocol": frozenset({"protocol_manifest"}),
    "thresholds": frozenset({"threshold_manifest"}),
    "classification": frozenset({"classification_manifest"}),
    "development_corpus": frozenset({"corpus_manifest"}),
    "qualification_holdout": frozenset({"corpus_manifest"}),
    "final_blind": frozenset({"corpus_manifest"}),
    "gold": frozenset({"gold_manifest"}),
    "scorer": frozenset({"scorer_source", "scorer_executable"}),
    "validator": frozenset({"validator_source", "validator_executable"}),
    "outcome_output": frozenset({"raw_outcome_output", "provenance_gate_result"}),
    "compiler_receipt": frozenset({"compiler_isolation_receipt"}),
    "evaluator_receipt": frozenset({"evaluator_isolation_receipt"}),
    "attestation": frozenset({"owner_attestation", "evaluator_attestation"}),
}
_DEDICATED_PURPOSE_VISIBILITIES: dict[str, frozenset[str]] = {
    "candidate": frozenset({"compiler_only", "compiler_evaluator"}),
    "protocol": frozenset({"owner_evaluator"}),
    "thresholds": frozenset({"owner_evaluator"}),
    "classification": frozenset({"owner_evaluator"}),
    "development_corpus": frozenset({"compiler_only"}),
    "qualification_holdout": frozenset({"compiler_only"}),
    "final_blind": frozenset({"compiler_only"}),
    "gold": frozenset({"evaluator_only"}),
    "scorer": frozenset({"evaluator_only"}),
    "validator": frozenset({"owner_evaluator"}),
    "outcome_output": frozenset({"owner_evaluator"}),
    "compiler_receipt": frozenset({"owner_evaluator"}),
    "evaluator_receipt": frozenset({"owner_evaluator"}),
    "attestation": frozenset({"owner_evaluator"}),
}
_COMPILER_VISIBLE_ARTIFACT_KINDS = frozenset(
    {
        "gold_manifest",
        "scorer_source",
        "scorer_executable",
        "protocol_manifest",
        "threshold_manifest",
        "classification_manifest",
        "validator_source",
        "validator_executable",
        "raw_outcome_output",
        "provenance_gate_result",
        "evaluator_isolation_receipt",
    }
)
_CORPUS_FORBIDDEN_PURPOSES = frozenset({"gold", "scorer", "outcome_output"})


class ProductOutcomePackageError(ValueError):
    """Raised when a product outcome package or a bound artifact is invalid."""


def canonical_digest(value: Mapping[str, Any], *, excluded_field: str) -> str:
    """Return the canonical SHA-256 digest used by manifest records."""

    body = {key: item for key, item in value.items() if key != excluded_field}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def package_sha256(value: Mapping[str, Any]) -> str:
    """Compute the manifest digest without trusting ``package_sha256``."""

    return canonical_digest(value, excluded_field="package_sha256")


def event_record_sha256(value: Mapping[str, Any]) -> str:
    """Compute one lifecycle event record digest."""

    return canonical_digest(value, excluded_field="record_sha256")


def _reject_nonfinite(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = item
    return result


def _strict_json(raw: bytes, *, field: str) -> dict[str, Any]:
    if not 1 <= len(raw) <= MAX_JSON_BYTES:
        raise ProductOutcomePackageError(f"{field} violates its bounded byte size")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ProductOutcomePackageError(f"{field} must contain strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ProductOutcomePackageError(f"{field} must contain a JSON object")
    return value


def _ensure_finite(value: Any, *, field: str = "manifest") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProductOutcomePackageError(f"{field} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _ensure_finite(item, field=f"{field}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _ensure_finite(item, field=f"{field}[{index}]")


def _load_schema() -> dict[str, Any]:
    path = DEFAULT_ROOT / "contracts" / SCHEMA_FILENAME
    try:
        schema = _strict_json(path.read_bytes(), field="product outcome schema")
        Draft202012Validator.check_schema(schema)
    except (OSError, ProductOutcomePackageError, SchemaError) as error:
        raise ProductOutcomePackageError(
            "product outcome package schema is unavailable or invalid"
        ) from error
    return schema


def _validate_schema(value: Mapping[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(_load_schema()).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "$"
        raise ProductOutcomePackageError(
            f"product outcome package schema violation at {location}: {first.message}"
        )


def _safe_relative_path(value: Any, *, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ProductOutcomePackageError(f"{field} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ProductOutcomePackageError(f"{field} must be a relative POSIX path")
    return path


def _safe_root(path: Path, *, field: str) -> Path:
    selected = Path(path).expanduser()
    if selected.is_symlink() or not selected.is_dir():
        raise ProductOutcomePackageError(f"{field} must be a regular directory")
    try:
        return selected.resolve(strict=True)
    except OSError as error:
        raise ProductOutcomePackageError(f"{field} cannot be resolved") from error


def _bound_file(
    artifact: Mapping[str, Any],
    *,
    root: Path,
    field: str,
) -> tuple[Path, bytes]:
    relative = _safe_relative_path(artifact.get("relative_path"), field=f"{field}.relative_path")
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise ProductOutcomePackageError(f"{field} must bind a regular non-symlink file")
    try:
        size = candidate.stat().st_size
    except OSError as error:
        raise ProductOutcomePackageError(f"{field} cannot be read within its root") from error
    if not 1 <= size <= MAX_ARTIFACT_BYTES:
        raise ProductOutcomePackageError(f"{field} violates its byte bound")
    try:
        selected = candidate.resolve(strict=True)
        selected.relative_to(root)
        raw = selected.read_bytes()
    except (OSError, ValueError) as error:
        raise ProductOutcomePackageError(f"{field} cannot be read within its root") from error
    if not 1 <= len(raw) <= MAX_ARTIFACT_BYTES:
        raise ProductOutcomePackageError(f"{field} violates its byte bound")
    if artifact.get("byte_size") != len(raw):
        raise ProductOutcomePackageError(f"{field} byte_size does not match the file")
    if artifact.get("file_sha256") != hashlib.sha256(raw).hexdigest():
        raise ProductOutcomePackageError(f"{field} file_sha256 does not match the file")
    return selected, raw


def _record_digest(raw: bytes) -> str:
    """Derive a content record digest without accepting a caller-supplied hash.

    JSON records use their own ``record_sha256`` when present, or the existing Gate Result
    ``result_sha256`` field.  Binary/text artifacts have a byte digest as their record
    digest; their exact bytes are still independently checked by ``file_sha256``.
    """

    try:
        document = _strict_json(raw, field="artifact")
    except ProductOutcomePackageError:
        return hashlib.sha256(raw).hexdigest()
    if "record_sha256" in document:
        if not isinstance(document["record_sha256"], str):
            raise ProductOutcomePackageError("artifact record_sha256 must be a SHA-256")
        expected = canonical_digest(document, excluded_field="record_sha256")
        if document["record_sha256"] != expected:
            raise ProductOutcomePackageError("artifact record_sha256 is invalid")
        return expected
    if "result_sha256" in document:
        if not isinstance(document["result_sha256"], str):
            raise ProductOutcomePackageError("Gate Result result_sha256 must be a SHA-256")
        expected = canonical_digest(document, excluded_field="result_sha256")
        if document["result_sha256"] != expected:
            raise ProductOutcomePackageError("Gate Result result_sha256 is invalid")
        return expected
    return hashlib.sha256(raw).hexdigest()


def _json_artifact(raw: bytes, *, field: str) -> dict[str, Any]:
    try:
        return _strict_json(raw, field=field)
    except ProductOutcomePackageError as error:
        raise ProductOutcomePackageError(f"{field} must be a JSON object") from error


def _mount_roots(
    package: Mapping[str, Any],
    *,
    root: Path | str | None,
    roots: Mapping[str, Path | str] | None,
) -> dict[str, Path]:
    mount_ids = [mount["mount_id"] for mount in package["mounts"]]
    if len(mount_ids) != len(set(mount_ids)):
        raise ProductOutcomePackageError("mount_id values must be unique")
    declared = set(mount_ids)
    owner_bound = package["evidence_kind"] == "owner_bound_external"
    if owner_bound and any(
        mount["purpose"] == "package_workspace" for mount in package["mounts"]
    ):
        raise ProductOutcomePackageError(
            "owner_bound_external packages cannot use a package_workspace mount"
        )
    if roots is not None and not isinstance(roots, Mapping):
        raise ProductOutcomePackageError("roots must be an explicit mount mapping")
    if owner_bound and not roots:
        raise ProductOutcomePackageError(
            "owner_bound_external packages require an explicit roots mapping"
        )
    if roots is not None:
        provided = set(roots)
        unknown = provided - declared
        if unknown:
            raise ProductOutcomePackageError(
                "roots contains unknown mount_id values: " + ", ".join(sorted(map(str, unknown)))
            )
        missing = declared - provided
        if missing:
            mount_id = sorted(missing)[0]
            raise ProductOutcomePackageError(f"mount {mount_id} has no explicitly provided root")
    selected = _safe_root(Path(root or DEFAULT_ROOT), field="root") if roots is None else None
    provided: dict[str, Path] = {}
    for mount in package["mounts"]:
        mount_id = mount["mount_id"]
        chosen = roots[mount_id] if roots is not None else selected
        if chosen is None:
            raise ProductOutcomePackageError(f"mount {mount_id} has no explicitly provided root")
        provided[mount_id] = _safe_root(Path(chosen), field=f"mount {mount_id}")
    return provided


def _validate_mount_role_roots(
    package: Mapping[str, Any], mount_roots: Mapping[str, Path]
) -> None:
    for mount in package["mounts"]:
        purpose = mount["purpose"]
        if purpose == "package_workspace":
            if mount["visibility"] != "owner_only":
                raise ProductOutcomePackageError(
                    "package_workspace mounts must remain owner_only"
                )
            continue
        allowed = _DEDICATED_PURPOSE_VISIBILITIES.get(purpose)
        if allowed is None or mount["visibility"] not in allowed:
            raise ProductOutcomePackageError(
                f"mount {mount['mount_id']} visibility is incompatible with purpose {purpose}"
            )
    compiler_roots = {
        mount_roots[mount["mount_id"]]
        for mount in package["mounts"]
        if mount["visibility"] == "compiler_only"
    }
    evaluator_roots = {
        mount_roots[mount["mount_id"]]
        for mount in package["mounts"]
        if mount["visibility"] == "evaluator_only"
    }
    if compiler_roots & evaluator_roots:
        raise ProductOutcomePackageError(
            "compiler_only and evaluator_only mounts must use distinct resolved roots"
        )


def _validate_artifact_mount_semantics(
    artifact: Mapping[str, Any], mount: Mapping[str, Any]
) -> None:
    purpose = mount["purpose"]
    artifact_kind = artifact["artifact_kind"]
    if purpose == "package_workspace":
        return
    allowed_kinds = _DEDICATED_PURPOSE_ARTIFACT_KINDS.get(purpose)
    if allowed_kinds is None or artifact_kind not in allowed_kinds:
        raise ProductOutcomePackageError(
            f"artifact {artifact.get('artifact_id')} kind {artifact_kind} is incompatible "
            f"with dedicated mount purpose {purpose}"
        )
    if (
        mount["visibility"] in {"compiler_only", "compiler_evaluator"}
        and artifact_kind in _COMPILER_VISIBLE_ARTIFACT_KINDS
    ):
        raise ProductOutcomePackageError(
            f"artifact {artifact.get('artifact_id')} is not allowed on a compiler-visible mount"
        )
    if artifact_kind == "corpus_manifest" and purpose in _CORPUS_FORBIDDEN_PURPOSES:
        raise ProductOutcomePackageError(
            f"corpus artifact {artifact.get('artifact_id')} cannot use mount purpose {purpose}"
        )


def _artifact_root(artifact: Mapping[str, Any], mount_roots: Mapping[str, Path]) -> Path:
    try:
        return mount_roots[artifact["root"]]
    except (KeyError, TypeError) as error:
        raise ProductOutcomePackageError(
            f"artifact {artifact.get('artifact_id')} references an unknown mount"
        ) from error


def _validate_artifacts(
    package: Mapping[str, Any], mount_roots: Mapping[str, Path]
) -> tuple[dict[str, Mapping[str, Any]], dict[str, tuple[Path, bytes]]]:
    by_id: dict[str, Mapping[str, Any]] = {}
    opened: dict[str, tuple[Path, bytes]] = {}
    mounts = {mount["mount_id"]: mount for mount in package["mounts"]}
    for index, artifact in enumerate(package["artifacts"]):
        artifact_id = artifact["artifact_id"]
        if artifact_id in by_id:
            raise ProductOutcomePackageError("artifact_id values must be unique")
        mount = mounts.get(artifact.get("root"))
        if mount is None:
            _artifact_root(artifact, mount_roots)
            raise ProductOutcomePackageError(
                f"artifact {artifact_id} references an unknown mount"
            )
        _validate_artifact_mount_semantics(artifact, mount)
        path, raw = _bound_file(
            artifact,
            root=mount_roots[artifact["root"]],
            field=f"artifacts[{index}]",
        )
        if artifact["record_sha256"] != _record_digest(raw):
            raise ProductOutcomePackageError(
                f"artifact {artifact_id} record_sha256 does not match its content"
            )
        try:
            document = _strict_json(raw, field=f"artifact {artifact_id}")
        except ProductOutcomePackageError:
            document = None
        if document is not None and document.get("schema_version") != artifact["schema_version"]:
            raise ProductOutcomePackageError(
                f"artifact {artifact_id} schema_version differs from its content"
            )
        by_id[artifact_id] = artifact
        opened[artifact_id] = (path, raw)
    return by_id, opened


def _collect_artifact_refs(value: Any, *, inside_artifact_list: bool = False) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if inside_artifact_list and key == "artifact_id":
                continue
            if (
                key == "artifacts"
                and isinstance(item, Sequence)
                and not isinstance(item, (str, bytes))
            ):
                refs.update(_collect_artifact_refs(item, inside_artifact_list=True))
                continue
            if key.endswith("_artifact_id") and isinstance(item, str):
                refs.add(item)
            elif (
                key.endswith("_artifact_ids")
                and isinstance(item, Sequence)
                and not isinstance(item, (str, bytes))
            ) or (key in {"artifact_refs", "artifact_ids"} and isinstance(item, Sequence)):
                refs.update(str(ref) for ref in item if isinstance(ref, str))
            refs.update(_collect_artifact_refs(item, inside_artifact_list=inside_artifact_list))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            refs.update(
                _collect_artifact_refs(
                    item,
                    inside_artifact_list=inside_artifact_list,
                )
            )
    return refs


def _validate_reference_closure(
    package: Mapping[str, Any], artifacts: Mapping[str, Mapping[str, Any]]
) -> None:
    refs = _collect_artifact_refs(package)
    unknown = refs - set(artifacts)
    if unknown:
        raise ProductOutcomePackageError(
            "package references an undeclared artifact: " + ", ".join(sorted(unknown))
        )
    if refs != set(artifacts):
        missing = sorted(set(artifacts) - refs)
        raise ProductOutcomePackageError(
            "every declared artifact must be consumed by a package reference: " + ", ".join(missing)
        )
    known_consumers = {
        "candidate_binding",
        "protocol_binding",
        "threshold_binding",
        "classification_binding",
        "isolation_receipts.compiler",
        "isolation_receipts.evaluator",
    }
    known_consumers.update(
        f"data_layer:{role}" for role in ("development", "qualification_holdout", "final_blind")
    )
    known_consumers.update(f"attestation:{role}" for role in ("owner", "evaluator"))
    known_consumers.update(event["event_id"] for event in package["lifecycle_events"])
    for product in PRODUCTS:
        known_consumers.update(
            {
                f"outcome:{product}:raw_output",
                f"outcome:{product}:gate_input",
                f"outcome:{product}:gate_result",
                f"outcome:{product}:scorer",
                f"outcome:{product}:validator",
            }
        )
    for artifact_id, artifact in artifacts.items():
        if not artifact["consumed_by"]:
            raise ProductOutcomePackageError(f"artifact {artifact_id} has no consumer")
        unknown_consumers = set(artifact["consumed_by"]) - known_consumers
        if unknown_consumers:
            raise ProductOutcomePackageError(f"artifact {artifact_id} has an unknown consumer")


def _artifact(
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
    artifact_id: str,
    *,
    field: str,
) -> tuple[Mapping[str, Any], Path, bytes]:
    try:
        descriptor = artifacts[artifact_id]
        path, raw = opened[artifact_id]
    except KeyError as error:
        raise ProductOutcomePackageError(
            f"{field} references an undeclared artifact {artifact_id!r}"
        ) from error
    return descriptor, path, raw


def _json_from_artifact(
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
    artifact_id: str,
    *,
    field: str,
) -> dict[str, Any]:
    _, _, raw = _artifact(artifacts, opened, artifact_id, field=field)
    return _json_artifact(raw, field=field)


def _validate_static_bindings(
    package: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
) -> None:
    candidate = package["candidate_binding"]
    wheel, wheel_path, _ = _artifact(
        artifacts, opened, candidate["wheel_artifact_id"], field="candidate wheel"
    )
    if wheel["artifact_kind"] != "candidate_wheel" or wheel_path.suffix != ".whl":
        raise ProductOutcomePackageError("candidate wheel binding must target a .whl artifact")
    if candidate["package_version"] != package["package_version"]:
        raise ProductOutcomePackageError("candidate and package versions differ")

    protocol = package["protocol_binding"]
    protocol_descriptor, _, protocol_raw = _artifact(
        artifacts, opened, protocol["protocol_artifact_id"], field="qualification protocol"
    )
    if protocol_descriptor["file_sha256"] != protocol["protocol_sha256"]:
        raise ProductOutcomePackageError("qualification protocol bytes do not match binding")
    protocol_document = _json_artifact(protocol_raw, field="qualification protocol")
    if protocol_document.get("protocol_id") != protocol["protocol_id"]:
        raise ProductOutcomePackageError("qualification protocol id differs from binding")
    for label, binding_key in (
        ("threshold", "threshold_binding"),
        ("classification", "classification_binding"),
    ):
        binding = package[binding_key]
        descriptor, _, raw = _artifact(
            artifacts, opened, binding[f"{label}_artifact_id"], field=label
        )
        if descriptor["file_sha256"] != binding[f"{label}_sha256"]:
            raise ProductOutcomePackageError(f"{label} bytes do not match binding")
        document = _json_artifact(raw, field=label)
        if label == "threshold":
            identity = document.get("threshold_id", document.get("catalogue_id"))
        else:
            identity = document.get("classification_id")
        if identity != binding[f"{label}_id"]:
            raise ProductOutcomePackageError(f"{label} id differs from binding")
    for name in ("protocol_binding", "threshold_binding", "classification_binding"):
        if package[name]["frozen"] is not True:
            raise ProductOutcomePackageError(f"{name} must be frozen")


def _validate_layer_manifest(
    package: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
) -> None:
    layers = package["data_layers"]
    for name, layer in layers.items():
        if layer["role"] != name:
            raise ProductOutcomePackageError(f"data layer {name} has a different role")
        if name == "development" and layer["status"] != "development_only":
            raise ProductOutcomePackageError("development layer must remain development_only")
        if name != "development" and layer["status"] == "development_only":
            raise ProductOutcomePackageError(f"{name} cannot use development_only status")
        if (
            layer["diagnostic_or_tuning_used"]
            and name == "qualification_holdout"
            and layer["status"] != "downgraded_development"
        ):
            raise ProductOutcomePackageError(
                "diagnosed or tuned qualification_holdout must be downgraded_development"
            )
        refs = (layer["corpus_artifact_id"], layer["gold_artifact_id"])
        if layer["status"] == "not_bound":
            if any(ref is not None for ref in refs):
                raise ProductOutcomePackageError(f"unbound {name} cannot bind corpus or Gold")
            continue
        if any(ref is None for ref in refs):
            raise ProductOutcomePackageError(f"bound {name} must bind corpus and Gold")
        corpus_id, gold_id = refs
        corpus, _, corpus_raw = _artifact(artifacts, opened, corpus_id, field=f"{name} corpus")
        gold, _, gold_raw = _artifact(artifacts, opened, gold_id, field=f"{name} Gold")
        if corpus["artifact_kind"] != "corpus_manifest" or gold["artifact_kind"] != "gold_manifest":
            raise ProductOutcomePackageError(f"{name} corpus/Gold artifact kinds are invalid")
        corpus_doc = _json_artifact(corpus_raw, field=f"{name} corpus")
        gold_doc = _json_artifact(gold_raw, field=f"{name} Gold")
        if corpus_doc.get("role") != name or corpus_doc.get("source") != layer["source"]:
            raise ProductOutcomePackageError(f"{name} corpus role/source differs from layer")
        if gold_doc.get("role") not in {name, f"{name}_gold"}:
            raise ProductOutcomePackageError(f"{name} Gold role differs from layer")
        if (
            corpus_doc.get("frozen") is not layer["frozen"]
            or gold_doc.get("frozen") is not layer["frozen"]
        ):
            raise ProductOutcomePackageError(f"{name} corpus/Gold frozen state differs from layer")


def _validate_isolation_receipts(
    package: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
) -> None:
    compiler_id = package["isolation_receipts"]["compiler_artifact_id"]
    evaluator_id = package["isolation_receipts"]["evaluator_artifact_id"]
    compiler, _, compiler_raw = _artifact(artifacts, opened, compiler_id, field="compiler receipt")
    evaluator, _, evaluator_raw = _artifact(
        artifacts, opened, evaluator_id, field="evaluator receipt"
    )
    if (
        compiler["artifact_kind"] != "compiler_isolation_receipt"
        or evaluator["artifact_kind"] != "evaluator_isolation_receipt"
    ):
        raise ProductOutcomePackageError("compiler/evaluator isolation receipt kinds are invalid")
    compiler_doc = _json_artifact(compiler_raw, field="compiler isolation receipt")
    evaluator_doc = _json_artifact(evaluator_raw, field="evaluator isolation receipt")
    if compiler_doc.get("role") != "compiler" or evaluator_doc.get("role") != "evaluator":
        raise ProductOutcomePackageError("isolation receipt roles are invalid")
    compiler_false = {
        "gold_access": False,
        "scorer_access": False,
        "repository_source_access": False,
        "expected_identity_access": False,
        "ambient_secret_access": False,
        "input_mounts_read_only": True,
    }
    evaluator_false = {
        "compiler_process_access": False,
        "candidate_mutation": False,
        "output_mutation": False,
        "read_only_inputs": True,
    }
    if any(compiler_doc.get(key) is not expected for key, expected in compiler_false.items()):
        raise ProductOutcomePackageError("compiler isolation receipt weakens the protocol")
    if any(evaluator_doc.get(key) is not expected for key, expected in evaluator_false.items()):
        raise ProductOutcomePackageError("evaluator isolation receipt weakens the protocol")


def _gate_root_for_artifact(descriptor: Mapping[str, Any], mount_roots: Mapping[str, Path]) -> Path:
    return _artifact_root(descriptor, mount_roots)


def _validate_gate_result_for_outcome(
    package: Mapping[str, Any],
    outcome: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
    mount_roots: Mapping[str, Path],
) -> dict[str, Any]:
    product = outcome["product_id"]
    gate_descriptor, gate_path, _ = _artifact(
        artifacts,
        opened,
        outcome["gate_result_artifact_id"],
        field=f"{product} Gate Result",
    )
    if gate_descriptor["artifact_kind"] != "provenance_gate_result":
        raise ProductOutcomePackageError(f"{product} result is not a provenance Gate Result")
    expected = PRODUCT_GATE_IDS[product]
    try:
        gate = validate_gate_result(
            gate_path,
            root=_gate_root_for_artifact(gate_descriptor, mount_roots),
            expected_validator_id=outcome["validator_id"],
            expected_validator_version=outcome["validator_version"],
        )
    except (ProvenanceGateResultError, OSError) as error:
        raise ProductOutcomePackageError(
            f"{product} Gate Result failed validation: {error}"
        ) from error
    if gate["gate_id"] != expected:
        raise ProductOutcomePackageError(f"{product} Gate Result is not dedicated to that product")
    candidate = package["candidate_binding"]
    gate_candidate = gate["candidate_binding"]
    if (
        gate_candidate["candidate_commit"] != candidate["candidate_commit"]
        or gate_candidate["candidate_tree"] != candidate["candidate_tree"]
        or gate_candidate["candidate_wheel_sha256"]
        != artifacts[candidate["wheel_artifact_id"]]["file_sha256"]
    ):
        raise ProductOutcomePackageError(f"{product} Gate Result candidate binding differs")
    if gate["protocol_binding"]["protocol_id"] != package["protocol_binding"][
        "protocol_id"
    ] or gate["protocol_binding"]["protocol_sha256"] != package["protocol_binding"][
        "protocol_sha256"
    ]:
        raise ProductOutcomePackageError(f"{product} Gate Result protocol binding differs")
    if gate["threshold_binding"]["threshold_id"] != package["threshold_binding"][
        "threshold_id"
    ] or gate["threshold_binding"]["threshold_sha256"] != package["threshold_binding"][
        "threshold_sha256"
    ]:
        raise ProductOutcomePackageError(f"{product} Gate Result threshold binding differs")
    if gate["classification_binding"]["classification_id"] != package[
        "classification_binding"
    ]["classification_id"] or gate["classification_binding"][
        "classification_sha256"
    ] != package["classification_binding"]["classification_sha256"]:
        raise ProductOutcomePackageError(f"{product} Gate Result classification binding differs")
    for input_record in gate["inputs"]:
        matches = [
            artifact
            for artifact in artifacts.values()
            if artifact["root"] == gate_descriptor["root"]
            and artifact["relative_path"] == input_record["relative_path"]
            and artifact["byte_size"] == input_record["byte_size"]
            and artifact["file_sha256"] == input_record["file_sha256"]
            and artifact["record_sha256"] == input_record["record_sha256"]
        ]
        if not matches:
            raise ProductOutcomePackageError(f"{product} Gate Result input is not package-bound")
        if not any(
            f"outcome:{product}:gate_input" in artifact["consumed_by"] for artifact in matches
        ):
            raise ProductOutcomePackageError(f"{product} Gate Result input has no outcome consumer")
    layer = package["data_layers"][outcome["corpus_role"]]
    corpus = gate["corpus"]
    if corpus["role"] != outcome["corpus_role"] or corpus["source"] != layer["source"]:
        raise ProductOutcomePackageError(f"{product} Gate Result corpus differs")
    if corpus["frozen"] is not True or layer["frozen"] is not True:
        raise ProductOutcomePackageError(f"{product} corpus must be frozen")
    corpus_artifact_id = layer["corpus_artifact_id"]
    gold_artifact_id = layer["gold_artifact_id"]
    if corpus_artifact_id is None or gold_artifact_id is None:
        raise ProductOutcomePackageError(f"{product} corpus/Gold layer is not bound")
    if corpus["sha256"] != artifacts[corpus_artifact_id]["file_sha256"]:
        raise ProductOutcomePackageError(f"{product} Gate Result corpus bytes differ")
    gold = gate["gold_binding"]
    if (
        gold["gold_sha256"] != artifacts[gold_artifact_id]["file_sha256"]
        or gold["source"] != layer["source"]
        or gold["frozen"] is not True
    ):
        raise ProductOutcomePackageError(f"{product} Gate Result Gold binding differs")
    if gate["status"] == "passed" or outcome["status"] == "passed":
        raise ProductOutcomePackageError(
            f"{product} v1 package cannot accept passed evidence before a dedicated "
            "deterministic validator is frozen"
        )
    if (
        outcome["status"] in {"prepared_not_executed", "not_executed", "downgraded_development"}
        and gate["status"] == "passed"
    ):
        raise ProductOutcomePackageError(
            f"{product} not-executed/downgraded outcome carries a pass result"
        )
    if outcome["status"] == "failed" and gate["status"] not in {"failed", "not_executed"}:
        raise ProductOutcomePackageError(
            f"{product} failed outcome has an incompatible Gate Result status"
        )
    if (
        outcome["corpus_role"] == "qualification_holdout"
        and (outcome["diagnostic_or_tuning_used"] or layer["diagnostic_or_tuning_used"])
        and gate["status"] == "passed"
    ):
        raise ProductOutcomePackageError("qualification_holdout diagnostics cannot pass")
    return gate


def _validate_outcome_raw_output(
    outcome: Mapping[str, Any],
    gate: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
) -> None:
    product = outcome["product_id"]
    descriptor, path, raw = _artifact(
        artifacts, opened, outcome["raw_output_artifact_id"], field=f"{product} raw output"
    )
    if descriptor["artifact_kind"] != "raw_outcome_output":
        raise ProductOutcomePackageError(f"{product} raw output artifact kind is invalid")
    document = _json_artifact(raw, field=f"{product} raw output")
    if document.get("product_id") != product:
        raise ProductOutcomePackageError(f"{product} raw output product differs")
    if (
        document.get("gate_result_path") not in {None, path.name, str(path.name)}
        and isinstance(document.get("gate_result_path"), str)
        and "/" in document["gate_result_path"]
    ):
        # A raw output may name its result, but may not smuggle a local absolute path.
        raise ProductOutcomePackageError(f"{product} raw output contains a path")
    raw_status = document.get("status")
    if raw_status == "passed" and gate["status"] != "passed":
        raise ProductOutcomePackageError(f"{product} raw output self-reports passed")
    if raw_status == "passed":
        raise ProductOutcomePackageError(
            f"{product} v1 raw output cannot establish a passed outcome"
        )


def _validate_outcomes(
    package: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
    mount_roots: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    products = [outcome["product_id"] for outcome in package["outcomes"]]
    if set(products) != set(PRODUCTS) or len(products) != len(set(products)):
        raise ProductOutcomePackageError("outcomes must contain exactly continuity, wiki and legal")
    by_product: dict[str, dict[str, Any]] = {}
    for outcome in package["outcomes"]:
        product = outcome["product_id"]
        if outcome["outcome_id"] != product:
            raise ProductOutcomePackageError(f"{product} outcome_id must equal product_id")
        if outcome["corpus_role"] == "qualification_holdout":
            layer = package["data_layers"]["qualification_holdout"]
            if outcome["diagnostic_or_tuning_used"] and layer["status"] != "downgraded_development":
                raise ProductOutcomePackageError(
                    "diagnosed or tuned qualification outcome must be downgraded_development"
                )
        gate = _validate_gate_result_for_outcome(package, outcome, artifacts, opened, mount_roots)
        _validate_outcome_raw_output(outcome, gate, artifacts, opened)
        scorer_source, scorer_source_path, _ = _artifact(
            artifacts,
            opened,
            outcome["scorer_source_artifact_id"],
            field=f"{product} scorer source",
        )
        scorer_executable, _, _ = _artifact(
            artifacts,
            opened,
            outcome["scorer_executable_artifact_id"],
            field=f"{product} scorer executable",
        )
        validator_source, _validator_source_path, _ = _artifact(
            artifacts,
            opened,
            outcome["validator_source_artifact_id"],
            field=f"{product} validator source",
        )
        validator_executable, _, _ = _artifact(
            artifacts,
            opened,
            outcome["validator_executable_artifact_id"],
            field=f"{product} validator executable",
        )
        if scorer_source["artifact_kind"] != "scorer_source" or scorer_source_path.suffix != ".py":
            raise ProductOutcomePackageError(f"{product} scorer source binding is invalid")
        if scorer_executable["artifact_kind"] != "scorer_executable":
            raise ProductOutcomePackageError(f"{product} scorer executable binding is invalid")
        if (
            validator_source["artifact_kind"] != "validator_source"
            or validator_executable["artifact_kind"] != "validator_executable"
        ):
            raise ProductOutcomePackageError(f"{product} validator artifact kinds are invalid")
        gate_descriptor = artifacts[outcome["gate_result_artifact_id"]]
        gate_source = gate["validator_source"]
        gate_executable = gate["validator_executable"]
        if (
            mount_roots[gate_descriptor["root"]]
            != mount_roots[validator_source["root"]]
            or mount_roots[gate_descriptor["root"]]
            != mount_roots[validator_executable["root"]]
            or gate_source["relative_path"] != validator_source["relative_path"]
            or gate_source["file_sha256"] != validator_source["file_sha256"]
            or gate_source["byte_size"] != validator_source["byte_size"]
            or gate_executable["relative_path"] != validator_executable["relative_path"]
            or gate_executable["file_sha256"] != validator_executable["file_sha256"]
            or gate_executable["byte_size"] != validator_executable["byte_size"]
        ):
            raise ProductOutcomePackageError(f"{product} validator bytes are not package-bound")
        by_product[product] = gate
    return by_product


def _validate_attestations(
    package: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
) -> None:
    roles: set[str] = set()
    for attestation in package["attestations"]:
        role = attestation["role"]
        if role in roles:
            raise ProductOutcomePackageError(f"duplicate {role} attestation")
        roles.add(role)
        document = _json_from_artifact(
            artifacts, opened, attestation["artifact_id"], field=f"{role} attestation"
        )
        if document.get("role") != role or document.get("package_id") != package["package_id"]:
            raise ProductOutcomePackageError(f"{role} attestation targets a different package")
        if document.get("claim_eligible") is not False:
            raise ProductOutcomePackageError(f"{role} attestation cannot grant claim eligibility")
    if roles != {"owner", "evaluator"}:
        raise ProductOutcomePackageError("one owner and one evaluator attestation are required")


def _validate_lifecycle(
    package: Mapping[str, Any],
    artifacts: Mapping[str, Mapping[str, Any]],
    opened: Mapping[str, tuple[Path, bytes]],
) -> None:
    events = package["lifecycle_events"]
    event_ids: set[str] = set()
    final_fail_refs: list[set[str]] = []
    replacement_events: list[Mapping[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        if event_id in event_ids:
            raise ProductOutcomePackageError("lifecycle event_id values must be unique")
        event_ids.add(event_id)
        if event["record_sha256"] != event_record_sha256(event):
            raise ProductOutcomePackageError(f"lifecycle event {event_id} record digest differs")
        unknown = set(event["artifact_refs"]) - set(artifacts)
        if unknown:
            raise ProductOutcomePackageError(
                f"lifecycle event {event_id} references an unknown artifact"
            )
        if event["event_type"] == "final_blind_failed":
            failed_refs = set(event["artifact_refs"])
            failed_kinds = {artifacts[artifact_id]["artifact_kind"] for artifact_id in failed_refs}
            if not {"corpus_manifest", "gold_manifest"} <= failed_kinds:
                raise ProductOutcomePackageError(
                    "final_blind failure must bind its failed corpus and Gold"
                )
            for artifact_id in failed_refs:
                if artifacts[artifact_id]["artifact_kind"] == "corpus_manifest":
                    role = _json_artifact(
                        opened[artifact_id][1], field="final_blind failed corpus"
                    ).get("role")
                    if role != "final_blind":
                        raise ProductOutcomePackageError(
                            "final_blind failure must bind a final_blind corpus"
                        )
                elif artifacts[artifact_id]["artifact_kind"] == "gold_manifest":
                    role = _json_artifact(
                        opened[artifact_id][1], field="final_blind failed Gold"
                    ).get("role")
                    if role != "final_blind_gold":
                        raise ProductOutcomePackageError(
                            "final_blind failure must bind final_blind Gold"
                        )
            final_fail_refs.append(failed_refs)
        elif event["event_type"] == "final_blind_replaced":
            replacement_events.append(event)
    if final_fail_refs:
        if package["data_layers"]["final_blind"]["status"] != "replaced":
            raise ProductOutcomePackageError(
                "a final_blind failure requires a replaced final_blind layer"
            )
        if not replacement_events:
            raise ProductOutcomePackageError(
                "a final_blind failure requires a replacement event bound to new unseen artifacts"
            )
        final_layer = package["data_layers"]["final_blind"]
        current = {final_layer["corpus_artifact_id"], final_layer["gold_artifact_id"]}
        if None in current or not current <= set(artifacts):
            raise ProductOutcomePackageError("final_blind replacement must bind corpus and Gold")
        if not any(current <= set(event["artifact_refs"]) for event in replacement_events):
            raise ProductOutcomePackageError(
                "final_blind replacement event does not bind current corpus/Gold"
            )
        for failed in final_fail_refs:
            if current & failed:
                raise ProductOutcomePackageError(
                    "final_blind replacement reuses failed corpus/Gold"
                )
            current_hashes = {artifacts[artifact_id]["file_sha256"] for artifact_id in current}
            failed_hashes = {artifacts[artifact_id]["file_sha256"] for artifact_id in failed}
            if current_hashes & failed_hashes:
                raise ProductOutcomePackageError(
                    "final_blind replacement reuses failed corpus/Gold bytes"
                )
    final_blind = package["data_layers"]["final_blind"]
    if final_blind["diagnostic_or_tuning_used"]:
        raise ProductOutcomePackageError(
            "current final_blind layer cannot be diagnostic or tuning material"
        )
    qualification = package["data_layers"]["qualification_holdout"]
    if (
        qualification["diagnostic_or_tuning_used"]
        and qualification["status"] != "downgraded_development"
    ):
        raise ProductOutcomePackageError(
            "qualification_holdout diagnostic use must downgrade lifecycle"
        )
    if (
        qualification["status"] == "downgraded_development"
        and package["lifecycle_status"] != "downgraded_development"
    ):
        raise ProductOutcomePackageError(
            "package lifecycle_status must reflect qualification downgrade"
        )


def validate_product_outcome_package(
    value: Mapping[str, Any] | str | Path,
    *,
    root: Path | str | None = None,
    roots: Mapping[str, Path | str] | None = None,
    expected_package_version: str | None = None,
) -> dict[str, Any]:
    """Validate one package and return its unmodified manifest.

    ``root`` is the default directory for all declared mounts.  Owners with physically
    isolated mounts may provide a ``roots`` mapping keyed by manifest ``mount_id``.  No
    root is inferred from an artifact path, and a symlink is never accepted as a mount or
    artifact file.
    """

    if isinstance(value, (str, Path)):
        package_path = Path(value)
        if package_path.is_symlink() or not package_path.is_file():
            raise ProductOutcomePackageError("package manifest must be a regular file")
        try:
            package = _strict_json(package_path.read_bytes(), field="package manifest")
        except OSError as error:
            raise ProductOutcomePackageError("package manifest cannot be read") from error
    elif isinstance(value, Mapping):
        package = dict(value)
    else:
        raise ProductOutcomePackageError("package manifest must be a mapping or JSON path")
    _ensure_finite(package)
    _validate_schema(package)
    if package["schema_version"] != SCHEMA_VERSION:
        raise ProductOutcomePackageError("package schema version is not v1")
    if (
        expected_package_version is not None
        and package["package_version"] != expected_package_version
    ):
        raise ProductOutcomePackageError("package version differs from expected version")
    if package["package_sha256"] != package_sha256(package):
        raise ProductOutcomePackageError("package_sha256 does not match the canonical manifest")
    if (
        package["assembly_policy"]["assembly_enabled"] is not False
        or package["claim_eligible"] is not False
    ):
        raise ProductOutcomePackageError("product outcome package cannot enable assembly or claims")
    mount_roots = _mount_roots(package, root=root, roots=roots)
    _validate_mount_role_roots(package, mount_roots)
    artifacts, opened = _validate_artifacts(package, mount_roots)
    _validate_reference_closure(package, artifacts)
    _validate_static_bindings(package, artifacts, opened)
    _validate_layer_manifest(package, artifacts, opened)
    _validate_isolation_receipts(package, artifacts, opened)
    _validate_outcomes(package, artifacts, opened, mount_roots)
    _validate_attestations(package, artifacts, opened)
    _validate_lifecycle(package, artifacts, opened)
    return package


# Short aliases make the benchmark seam convenient without creating another policy engine.
verify_product_outcome_package = validate_product_outcome_package
validate_outcome_package = validate_product_outcome_package


def _write_record(path: Path, value: Mapping[str, Any]) -> tuple[bytes, str]:
    body = dict(value)
    body["record_sha256"] = canonical_digest(body, excluded_field="record_sha256")
    raw = canonical_json(body).encode("utf-8")
    path.write_bytes(raw)
    return raw, body["record_sha256"]


def build_synthetic_fixture(root: Path) -> dict[str, Any]:
    """Write and return a synthetic ``prepared_not_executed`` package fixture.

    The fixture is intentionally local and source-free.  It contains no credentials,
    external corpus, Human Gold, provider output, or qualification claim.
    """

    root = _safe_root(root, field="synthetic fixture root")
    (root / "candidate").mkdir(exist_ok=True)
    (root / "protocol").mkdir(exist_ok=True)
    (root / "thresholds").mkdir(exist_ok=True)
    (root / "classification").mkdir(exist_ok=True)
    (root / "development").mkdir(exist_ok=True)
    (root / "receipts").mkdir(exist_ok=True)
    (root / "outputs").mkdir(exist_ok=True)
    (root / "scorers").mkdir(exist_ok=True)
    (root / "validators").mkdir(exist_ok=True)
    (root / "attestations").mkdir(exist_ok=True)
    paths: dict[str, Path] = {}
    paths["wheel"] = root / "candidate" / "deeplaw-0.12.0-py3-none-any.whl"
    paths["wheel"].write_bytes(b"synthetic v0.13 wheel bytes\n")
    paths["protocol"] = root / "protocol" / "qualification-protocol-v1.json"
    paths["protocol"].write_bytes(
        canonical_json(
            {
                "schema_version": "deeplaw.v013-qualification-protocol/v1",
                "protocol_id": "deeplaw-v013-source-candidate-qualification",
            }
        ).encode("utf-8")
    )
    paths["threshold"] = root / "thresholds" / "quality-metric-catalog-v1.json"
    paths["threshold"].write_bytes(
        canonical_json(
            {
                "schema_version": "deeplaw.v013-quality-thresholds/v1",
                "threshold_id": "v013-thresholds-v1",
            }
        ).encode("utf-8")
    )
    paths["classification"] = root / "classification" / "v013-gate-classification-v2.json"
    paths["classification"].write_bytes(
        canonical_json(
            {
                "schema_version": "deeplaw.v013-release-gate-classification/v2",
                "classification_id": "deeplaw-v013-commercial-gates-v2",
            }
        ).encode("utf-8")
    )
    corpus = root / "development" / "corpus.json"
    _, corpus_record = _write_record(
        corpus,
        {
            "schema_version": "synthetic.corpus/v1",
            "role": "development",
            "source": "repository",
            "frozen": True,
        },
    )
    gold = root / "development" / "gold.json"
    _, gold_record = _write_record(
        gold,
        {
            "schema_version": "synthetic.gold/v1",
            "role": "development_gold",
            "source": "repository",
            "frozen": True,
        },
    )
    compiler = root / "receipts" / "compiler.json"
    _, compiler_record = _write_record(
        compiler,
        {
            "schema_version": "synthetic.compiler-isolation/v1",
            "role": "compiler",
            "gold_access": False,
            "scorer_access": False,
            "repository_source_access": False,
            "expected_identity_access": False,
            "ambient_secret_access": False,
            "input_mounts_read_only": True,
        },
    )
    evaluator = root / "receipts" / "evaluator.json"
    _, evaluator_record = _write_record(
        evaluator,
        {
            "schema_version": "synthetic.evaluator-isolation/v1",
            "role": "evaluator",
            "compiler_process_access": False,
            "candidate_mutation": False,
            "output_mutation": False,
            "read_only_inputs": True,
        },
    )
    owner = root / "attestations" / "owner.json"
    _, owner_record = _write_record(
        owner,
        {
            "schema_version": "synthetic.owner-attestation/v1",
            "role": "owner",
            "package_id": "v013-product-outcome-synthetic",
            "claim_eligible": False,
        },
    )
    evaluator_attestation = root / "attestations" / "evaluator.json"
    _, evaluator_attestation_record = _write_record(
        evaluator_attestation,
        {
            "schema_version": "synthetic.evaluator-attestation/v1",
            "role": "evaluator",
            "package_id": "v013-product-outcome-synthetic",
            "claim_eligible": False,
        },
    )
    for product in PRODUCTS:
        scorer = root / "scorers" / f"{product}.py"
        scorer.write_bytes(f"# synthetic scorer for {product}\n".encode())
        paths[f"scorer_{product}"] = scorer
        executable = root / "scorers" / f"{product}.bin"
        executable.write_bytes(f"synthetic scorer executable {product}\n".encode())
        paths[f"scorer_executable_{product}"] = executable
        validator = root / "validators" / f"{product}.py"
        validator.write_bytes(f"# synthetic validator for {product}\n".encode())
        paths[f"validator_{product}"] = validator
        validator_executable = root / "validators" / f"{product}.bin"
        validator_executable.write_bytes(f"synthetic validator executable {product}\n".encode())
        paths[f"validator_executable_{product}"] = validator_executable

    protocol_sha = hashlib.sha256(paths["protocol"].read_bytes()).hexdigest()
    threshold_sha = hashlib.sha256(paths["threshold"].read_bytes()).hexdigest()
    classification_sha = hashlib.sha256(paths["classification"].read_bytes()).hexdigest()
    wheel_sha = hashlib.sha256(paths["wheel"].read_bytes()).hexdigest()
    artifacts: list[dict[str, Any]] = []

    def add(
        path: Path,
        artifact_id: str,
        kind: str,
        schema_version: str,
        consumers: list[str],
        record: str | None = None,
    ) -> None:
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "artifact_kind": kind,
                "root": "workspace",
                "relative_path": relative,
                "byte_size": len(raw),
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "schema_version": schema_version,
                "record_sha256": record or _record_digest(raw),
                "consumed_by": consumers,
            }
        )

    add(paths["wheel"], "candidate-wheel", "candidate_wheel", "binary", ["candidate_binding"])
    add(
        paths["protocol"],
        "qualification-protocol",
        "protocol_manifest",
        "deeplaw.v013-qualification-protocol/v1",
        ["protocol_binding"],
    )
    add(
        paths["threshold"],
        "threshold-manifest",
        "threshold_manifest",
        "deeplaw.v013-quality-thresholds/v1",
        ["threshold_binding"],
    )
    add(
        paths["classification"],
        "classification-manifest",
        "classification_manifest",
        "deeplaw.v013-release-gate-classification/v2",
        ["classification_binding"],
    )
    add(
        corpus,
        "development-corpus",
        "corpus_manifest",
        "synthetic.corpus/v1",
        ["data_layer:development", "outcome:*:gate_result"],
        corpus_record,
    )
    add(
        gold,
        "development-gold",
        "gold_manifest",
        "synthetic.gold/v1",
        ["data_layer:development", "outcome:*:gate_result"],
        gold_record,
    )
    add(
        compiler,
        "compiler-isolation",
        "compiler_isolation_receipt",
        "synthetic.compiler-isolation/v1",
        ["isolation_receipts.compiler"],
        compiler_record,
    )
    add(
        evaluator,
        "evaluator-isolation",
        "evaluator_isolation_receipt",
        "synthetic.evaluator-isolation/v1",
        ["isolation_receipts.evaluator"],
        evaluator_record,
    )
    add(
        owner,
        "owner-attestation",
        "owner_attestation",
        "synthetic.owner-attestation/v1",
        ["attestation:owner"],
        owner_record,
    )
    add(
        evaluator_attestation,
        "evaluator-attestation",
        "evaluator_attestation",
        "synthetic.evaluator-attestation/v1",
        ["attestation:evaluator"],
        evaluator_attestation_record,
    )

    for product in PRODUCTS:
        output = root / "outputs" / f"{product}.json"
        output.write_bytes(
            canonical_json(
                {
                    "schema_version": f"synthetic.{product}-output-v1",
                    "product_id": product,
                    "status": "prepared_not_executed",
                }
            ).encode("utf-8")
        )
        result_path = root / "outputs" / f"{product}-gate-result.json"
        gate_input_schema_version = f"synthetic.{product}-output-v1"
        raw_output_schema_version = f"synthetic.{product}-output-v1"
        result_input = {
            "schema_version": gate_input_schema_version,
            "artifact_kind": "raw_outcome_output",
            "product_id": product,
            "status": "prepared_not_executed",
        }
        input_path = output
        # Add the record field expected by the existing Gate Result validator while preserving
        # the same bytes as the package raw output.
        result_input["record_sha256"] = canonical_digest(
            result_input, excluded_field="record_sha256"
        )
        input_path.write_bytes(canonical_json(result_input).encode("utf-8"))
        source = paths[f"validator_{product}"]
        executable = paths[f"validator_executable_{product}"]
        input_raw = input_path.read_bytes()
        input_id = f"input-{product}"
        gate: dict[str, Any] = {
            "schema_version": "deeplaw.provenance-bound-gate-result/v1",
            "gate_id": PRODUCT_GATE_IDS[product],
            "category": "Core",
            "validator_id": f"deeplaw.v013.validator.{product}",
            "validator_version": "0.1.0",
            "validator_source": {
                "relative_path": source.relative_to(root).as_posix(),
                "byte_size": source.stat().st_size,
                "file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            },
            "validator_executable": {
                "relative_path": executable.relative_to(root).as_posix(),
                "byte_size": executable.stat().st_size,
                "file_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            },
            "classification_binding": {
                "classification_id": "deeplaw-v013-commercial-gates-v2",
                "classification_schema_version": "deeplaw.v013-release-gate-classification/v2",
                "classification_sha256": classification_sha,
            },
            "candidate_binding": {
                "candidate_commit": "a" * 40,
                "candidate_tree": "b" * 40,
                "candidate_wheel_sha256": wheel_sha,
                "candidate_sdist_sha256": "c" * 64,
            },
            "protocol_binding": {
                "protocol_id": "deeplaw-v013-source-candidate-qualification",
                "protocol_sha256": protocol_sha,
                "frozen": True,
            },
            "threshold_binding": {
                "threshold_id": "v013-thresholds-v1",
                "threshold_sha256": threshold_sha,
                "frozen": True,
            },
            "gold_binding": {
                "gold_sha256": hashlib.sha256(gold.read_bytes()).hexdigest(),
                "role": "development_gold",
                "source": "repository",
                "frozen": True,
            },
            "corpus": {
                "role": "development",
                "source": "repository",
                "sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
                "frozen": True,
            },
            "status": "not_executed",
            "executions": [],
            "run_ids": [],
            "unique_dimensions": [],
            "metrics": [],
            "hard_failures": [],
            "failures": [],
            "redaction": {
                "secret_canary_count": 0,
                "private_path_count": 0,
                "output_redacted": True,
                "input_refs": [input_id],
            },
            "inputs": [
                {
                    "input_id": input_id,
                    "relative_path": input_path.relative_to(root).as_posix(),
                    "byte_size": len(input_raw),
                    "file_sha256": hashlib.sha256(input_raw).hexdigest(),
                    "schema_version": gate_input_schema_version,
                    "record_sha256": result_input["record_sha256"],
                    "artifact_kind": "raw_outcome_output",
                }
            ],
        }
        gate["result_sha256"] = result_sha256(gate)
        result_path.write_bytes(canonical_json(gate).encode("utf-8"))
        add(
            output,
            f"raw-{product}",
            "raw_outcome_output",
            raw_output_schema_version,
            [f"outcome:{product}:raw_output", f"outcome:{product}:gate_input"],
            result_input["record_sha256"],
        )
        add(
            result_path,
            f"gate-{product}",
            "provenance_gate_result",
            "deeplaw.provenance-bound-gate-result/v1",
            [f"outcome:{product}:gate_result"],
        )
        add(
            paths[f"scorer_{product}"],
            f"scorer-source-{product}",
            "scorer_source",
            "source",
            [f"outcome:{product}:scorer"],
        )
        add(
            paths[f"scorer_executable_{product}"],
            f"scorer-executable-{product}",
            "scorer_executable",
            "binary",
            [f"outcome:{product}:scorer"],
        )
        add(
            paths[f"validator_{product}"],
            f"validator-source-{product}",
            "validator_source",
            "source",
            [f"outcome:{product}:validator"],
        )
        add(
            paths[f"validator_executable_{product}"],
            f"validator-executable-{product}",
            "validator_executable",
            "binary",
            [f"outcome:{product}:validator"],
        )

    for artifact in artifacts:
        # The wildcard consumers above are intentionally expanded in the manifest's
        # lifecycle references; the verifier only requires a non-empty declaration.
        if artifact["artifact_id"] in {"development-corpus", "development-gold"}:
            artifact["consumed_by"] = [
                "data_layer:development",
                *[f"outcome:{p}:gate_result" for p in PRODUCTS],
            ]
    package: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "package_id": "v013-product-outcome-synthetic",
        "package_version": "0.12.0",
        "package_sha256": "0" * 64,
        "evidence_kind": "synthetic_dry_run",
        "benchmark_only": True,
        "claim_eligible": False,
        "assembly_policy": {"assembly_enabled": False, "reason_code": "benchmark_only_manifest"},
        "lifecycle_status": "prepared_not_executed",
        "candidate_binding": {
            "candidate_commit": "a" * 40,
            "candidate_tree": "b" * 40,
            "package_version": "0.12.0",
            "wheel_artifact_id": "candidate-wheel",
        },
        "protocol_binding": {
            "protocol_id": "deeplaw-v013-source-candidate-qualification",
            "protocol_artifact_id": "qualification-protocol",
            "protocol_sha256": protocol_sha,
            "frozen": True,
        },
        "threshold_binding": {
            "threshold_id": "v013-thresholds-v1",
            "threshold_artifact_id": "threshold-manifest",
            "threshold_sha256": threshold_sha,
            "frozen": True,
        },
        "classification_binding": {
            "classification_id": "deeplaw-v013-commercial-gates-v2",
            "classification_artifact_id": "classification-manifest",
            "classification_sha256": classification_sha,
            "frozen": True,
        },
        "mounts": [
            {
                "mount_id": "workspace",
                "purpose": "package_workspace",
                "visibility": "owner_only",
                "read_only": True,
            }
        ],
        "artifacts": artifacts,
        "data_layers": {
            "development": {
                "role": "development",
                "source": "repository",
                "status": "development_only",
                "frozen": True,
                "diagnostic_or_tuning_used": True,
                "corpus_artifact_id": "development-corpus",
                "gold_artifact_id": "development-gold",
            },
            "qualification_holdout": {
                "role": "qualification_holdout",
                "source": "repository_external",
                "status": "not_bound",
                "frozen": False,
                "diagnostic_or_tuning_used": False,
                "corpus_artifact_id": None,
                "gold_artifact_id": None,
            },
            "final_blind": {
                "role": "final_blind",
                "source": "repository_external",
                "status": "not_bound",
                "frozen": False,
                "diagnostic_or_tuning_used": False,
                "corpus_artifact_id": None,
                "gold_artifact_id": None,
            },
        },
        "isolation_receipts": {
            "compiler_artifact_id": "compiler-isolation",
            "evaluator_artifact_id": "evaluator-isolation",
        },
        "outcomes": [
            {
                "outcome_id": product,
                "product_id": product,
                "status": "prepared_not_executed",
                "corpus_role": "development",
                "raw_output_artifact_id": f"raw-{product}",
                "gate_result_artifact_id": f"gate-{product}",
                "scorer_source_artifact_id": f"scorer-source-{product}",
                "scorer_executable_artifact_id": f"scorer-executable-{product}",
                "validator_id": f"deeplaw.v013.validator.{product}",
                "validator_version": "0.1.0",
                "validator_source_artifact_id": f"validator-source-{product}",
                "validator_executable_artifact_id": f"validator-executable-{product}",
                "diagnostic_or_tuning_used": True,
            }
            for product in PRODUCTS
        ],
        "attestations": [
            {
                "attestation_id": "owner",
                "role": "owner",
                "subject": "owner",
                "artifact_id": "owner-attestation",
                "claim_eligible": False,
            },
            {
                "attestation_id": "evaluator",
                "role": "evaluator",
                "subject": "evaluator",
                "artifact_id": "evaluator-attestation",
                "claim_eligible": False,
            },
        ],
        "lifecycle_events": [],
    }
    package["lifecycle_events"] = [
        {
            "event_id": "package-prepared",
            "event_type": "package_prepared",
            "actor_role": "owner",
            "occurred_at": "synthetic",
            "outcome_id": None,
            "artifact_refs": [
                "candidate-wheel",
                "qualification-protocol",
                "threshold-manifest",
                "classification-manifest",
                "compiler-isolation",
                "evaluator-isolation",
                "owner-attestation",
                "evaluator-attestation",
            ],
            "reason_code": "credential_free_dry_run",
            "record_sha256": "0" * 64,
        },
        *[
            {
                "event_id": f"{product}-not-executed",
                "event_type": "outcome_not_executed",
                "actor_role": "owner",
                "occurred_at": "synthetic",
                "outcome_id": product,
                "artifact_refs": [
                    f"raw-{product}",
                    f"gate-{product}",
                    f"scorer-source-{product}",
                    f"scorer-executable-{product}",
                    f"validator-source-{product}",
                    f"validator-executable-{product}",
                ],
                "reason_code": "external_credentials_absent",
                "record_sha256": "0" * 64,
            }
            for product in PRODUCTS
        ],
    ]
    for event in package["lifecycle_events"]:
        event["record_sha256"] = event_record_sha256(event)
    package["package_sha256"] = package_sha256(package)
    return package


def dry_run() -> dict[str, Any]:
    """Run the credential-free synthetic fixture and return a small verification receipt."""

    with tempfile.TemporaryDirectory(prefix="deeplaw-v013-outcome-dry-run-") as directory:
        root = Path(directory)
        package = build_synthetic_fixture(root)
        validate_product_outcome_package(package, root=root)
        return {
            "schema_version": "deeplaw.v013-product-outcome-dry-run/v1",
            "status": "prepared_not_executed",
            "evidence_kind": "synthetic_dry_run",
            "benchmark_only": True,
            "claim_eligible": False,
            "assembly_enabled": False,
            "products": list(PRODUCTS),
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify a benchmark-only v0.13 product outcome package"
    )
    parser.add_argument("--package", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.dry_run:
            print(json.dumps(dry_run(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.package is None:
            parser.error("--package is required unless --dry-run is selected")
        result = validate_product_outcome_package(
            args.package, root=args.root or args.package.parent
        )
        print(
            json.dumps(
                {"status": "valid", "package_sha256": result["package_sha256"]}, sort_keys=True
            )
        )
        return 0
    except (OSError, ProductOutcomePackageError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
