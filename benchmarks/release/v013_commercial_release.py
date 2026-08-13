"""Assemble a v0.13 release manifest from provenance-bound evidence.

The historical ``commercial-evidence-report/v1`` observation format is deliberately rejected: its
hashes do not prove that the reported command, model, runs, metrics, or scans exist.  The assembler
remains fail closed until the additive provenance-bound report/classification contracts and every
Core Gate's dedicated raw-artifact validator are complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.release.release_policy import (
    V013_ACTIVE_CLASSIFICATION_SCHEMA_PATH,
    V013_ACTIVE_CLASSIFICATION_SCHEMA_VERSION,
    validate_manifest_for_release,
)
from benchmarks.release.semantic_evidence import (
    SemanticEvidenceError,
    canonical_json,
    validate_release_manifest_semantics,
    validate_report,
)

MANIFEST_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / (
    "commercial-release-manifest.v6.schema.json"
)
LEGACY_REPORT_SCHEMA_VERSION = "deeplaw.commercial-evidence-report/v1"
PROVENANCE_REPORT_SCHEMA_VERSION = "deeplaw.commercial-evidence-report/v3"
PROVENANCE_CLASSIFICATION_SCHEMA_VERSION = V013_ACTIVE_CLASSIFICATION_SCHEMA_VERSION
_INPUT_FIELDS = {"schema_version", "environment", "release", "bindings", "artifacts"}
_DERIVED_FIELDS = {
    "semantic_evidence",
    "commercial_release_eligible",
    "quality_protocol_eligible",
    "competitive_claim_eligible",
    "record_sha256",
}


class V013CommercialReleaseError(ValueError):
    """Raised when decision-free v0.13 release inputs cannot produce a safe manifest."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise V013CommercialReleaseError(f"invalid JSON input: {error}") from error
    if not isinstance(value, dict):
        raise V013CommercialReleaseError("v0.13 release input must be a JSON object")
    return value


def _safe_asset(assets_root: Path, logical_path: Any) -> Path:
    if (
        not isinstance(logical_path, str)
        or not logical_path
        or "\\" in logical_path
        or logical_path.startswith("/")
        or any(part in {"", ".", ".."} for part in logical_path.split("/"))
    ):
        raise V013CommercialReleaseError("release input contains an unsafe artifact path")
    root = assets_root.expanduser().resolve(strict=True)
    candidate = root.joinpath(*logical_path.split("/"))
    if candidate.is_symlink():
        raise V013CommercialReleaseError("release artifact must not be a symbolic link")
    selected = candidate.resolve(strict=True)
    if not selected.is_file() or not selected.is_relative_to(root):
        raise V013CommercialReleaseError("release artifact escapes the asset root")
    return selected


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_sha256(manifest: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "record_sha256"}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _verify_inventory(document: Mapping[str, Any], assets_root: Path) -> None:
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise V013CommercialReleaseError("release artifact inventory must be an array")
    observed: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {
            "path",
            "sha256",
            "byte_size",
        }:
            raise V013CommercialReleaseError("release artifact inventory is not closed")
        logical_path = artifact["path"]
        if logical_path in observed:
            raise V013CommercialReleaseError("release artifact inventory contains a duplicate")
        observed.add(logical_path)
        path = _safe_asset(assets_root, logical_path)
        if artifact["sha256"] != _file_sha256(path) or artifact["byte_size"] != path.stat().st_size:
            raise V013CommercialReleaseError(
                f"release artifact inventory differs from actual bytes: {logical_path}"
            )


def _validate_provenance_classification(classification: Mapping[str, Any]) -> None:
    try:
        schema = _load_json(V013_ACTIVE_CLASSIFICATION_SCHEMA_PATH)
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(classification),
            key=lambda error: list(error.path),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise V013CommercialReleaseError(
            f"provenance gate classification is unavailable: {error}"
        ) from error
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "$"
        raise V013CommercialReleaseError(
            f"provenance gate classification violation at {location}: {first.message}"
        )


def assemble_manifest(
    template: Mapping[str, Any] | str | Path,
    *,
    semantic_report_path: str,
    assets_root: str | Path,
) -> dict[str, Any]:
    """Return a release manifest whose decisions are derived from actual report bytes."""

    if isinstance(template, (str, Path)):
        document = _load_json(Path(template).expanduser().resolve(strict=True))
    elif isinstance(template, Mapping):
        document = json.loads(canonical_json(template))
    else:
        raise V013CommercialReleaseError("v0.13 release template must be a mapping or JSON path")
    supplied_decisions = sorted(set(document) & _DERIVED_FIELDS)
    if supplied_decisions:
        raise V013CommercialReleaseError(
            "caller must not supply release decisions: " + ", ".join(supplied_decisions)
        )
    missing = sorted(_INPUT_FIELDS - set(document))
    unexpected = sorted(set(document) - _INPUT_FIELDS)
    if missing or unexpected:
        raise V013CommercialReleaseError(
            f"v0.13 release template fields differ; missing={missing}, unexpected={unexpected}"
        )
    if document["schema_version"] != "deeplaw.commercial-release-manifest/v6":
        raise V013CommercialReleaseError("v0.13 assembler requires manifest v6")

    root = Path(assets_root)
    _verify_inventory(document, root)
    bindings = document.get("bindings")
    if not isinstance(bindings, Mapping):
        raise V013CommercialReleaseError("v0.13 release bindings must be an object")
    report_path = _safe_asset(root, semantic_report_path)
    classification_path = _safe_asset(root, bindings.get("gate_classification_path"))
    report = _load_json(report_path)
    classification = _load_json(classification_path)
    report_schema_version = report.get("schema_version")
    if report_schema_version == LEGACY_REPORT_SCHEMA_VERSION:
        raise V013CommercialReleaseError(
            "commercial-evidence-report/v1 contains self-reported observations and cannot "
            "assemble a v0.13 release"
        )
    if report_schema_version != PROVENANCE_REPORT_SCHEMA_VERSION:
        raise V013CommercialReleaseError(
            "v0.13 assembler requires provenance-bound commercial evidence report v3"
        )
    if classification.get("schema_version") != PROVENANCE_CLASSIFICATION_SCHEMA_VERSION:
        raise V013CommercialReleaseError(
            "v0.13 assembler requires the active provenance-bound gate classification"
        )
    _validate_provenance_classification(classification)
    assembly_policy = classification["assembly_policy"]
    if assembly_policy["assembly_enabled"] is not True:
        raise V013CommercialReleaseError(
            "v0.13 provenance assembly remains disabled: "
            f"{assembly_policy['reason_code']}"
        )
    try:
        result = validate_report(
            report,
            expected_candidate_commit=bindings.get("candidate_commit"),
            expected_candidate_tree=bindings.get("candidate_tree"),
            expected_wheel_sha256=bindings.get("candidate_wheel_sha256"),
            expected_sdist_sha256=bindings.get("candidate_sdist_sha256"),
            expected_protocol_sha256=bindings.get("qualification_protocol_sha256"),
            expected_threshold_sha256=bindings.get("thresholds_sha256"),
            expected_gold_sha256=bindings.get("human_gold_manifest_sha256"),
            expected_corpus_role="final_blind",
            classification=classification,
        )
    except SemanticEvidenceError as error:
        raise V013CommercialReleaseError(str(error)) from error
    if not result["release_ready"] or not result["claim_eligible"]:
        raise V013CommercialReleaseError("semantic evidence does not pass every Core release gate")
    if result["competitive_claim_eligible"]:
        raise V013CommercialReleaseError(
            "v0.13 commercial manifest does not admit competitive claims"
        )
    category_by_gate = {item["gate_id"]: item["category"] for item in classification["gates"]}
    document["semantic_evidence"] = {
        "report_path": semantic_report_path,
        "report_artifact_sha256": _file_sha256(report_path),
        "report_record_sha256": report["report_sha256"],
        "report_kind": report["report_kind"],
        "status": result["status"],
        "hard_zero": result["hard_zero"],
        "release_ready": result["release_ready"],
        "claim_eligible": result["claim_eligible"],
        "competitive_claim_eligible": result["competitive_claim_eligible"],
        "gate_statuses": [
            {
                "gate_id": gate_id,
                "category": category_by_gate[gate_id],
                "status": result["gate_statuses"][gate_id],
            }
            for gate_id in sorted(category_by_gate)
        ],
    }
    document["commercial_release_eligible"] = True
    document["quality_protocol_eligible"] = True
    document["competitive_claim_eligible"] = False
    document["record_sha256"] = _record_sha256(document)

    schema = _load_json(MANIFEST_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: list(error.path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "$"
        raise V013CommercialReleaseError(
            f"assembled v0.13 manifest schema violation at {location}: {first.message}"
        )
    try:
        validate_manifest_for_release(document, release_version=document["release"]["version"])
        validate_release_manifest_semantics(document, assets_root=root)
    except Exception as error:
        raise V013CommercialReleaseError(str(error)) from error
    return document


def _main() -> int:
    parser = argparse.ArgumentParser(description="Assemble a semantic v0.13 release manifest")
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--semantic-report", required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = assemble_manifest(
            args.template,
            semantic_report_path=args.semantic_report,
            assets_root=args.assets_root,
        )
        args.output.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    except (OSError, V013CommercialReleaseError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
