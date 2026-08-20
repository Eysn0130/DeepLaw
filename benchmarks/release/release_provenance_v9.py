"""Build and revalidate the v0.13 Kernel Release Core provenance manifest.

This boundary reopens the exact Kernel bundle, independently reruns the Gate
v9 assembler, compares every Gate/result byte with the retained qualification
assets, and binds the reproducible wheel/sdist receipt.  Capability and
competitive/research claims remain explicit non-authorizing records.

The module has no network, Host, credential, signing, tag, or publish side
effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release import assemble_commercial_qualification_v9 as assembler
from benchmarks.release import kernel_qualification_bundle_v1 as bundle

REPOSITORY = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPOSITORY / "contracts/commercial-release-manifest.v9.schema.json"
SCHEMA_VERSION = "deeplaw.commercial-release-manifest/v9"
PROFILE = "kernel_release_core"
REFERENCE_PROVENANCE = "deterministic_expected_evidence"
HUMAN_AUTHENTICITY = "not_claimed"
MAX_FILE_BYTES = 64 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_RE = re.compile(r"^[0-9a-f]{40}$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ReleaseProvenanceV9Error(ValueError):
    """Raised when the retained v9 release chain is incomplete or differs."""


def _fail(message: str) -> None:
    raise ReleaseProvenanceV9Error(message)


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, UnicodeError, ValueError) as error:
        raise ReleaseProvenanceV9Error("release provenance is not canonical JSON") from error


def record_sha256(value: Mapping[str, Any], *, field: str = "record_sha256") -> str:
    body = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(canonical_json(body)).hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("strict JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    _fail(f"strict JSON contains a non-finite constant: {value}")


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except ReleaseProvenanceV9Error:
        raise
    except (TypeError, UnicodeError, ValueError) as error:
        raise ReleaseProvenanceV9Error(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        _fail(f"{label} must be an object")

    def finite(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            _fail(f"{label} contains a non-finite number")
        if isinstance(item, Mapping):
            for child in item.values():
                finite(child)
        elif isinstance(item, list):
            for child in item:
                finite(child)

    finite(value)
    return value


def _safe_relative(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or value.startswith("/")
        or DRIVE_RE.match(value)
    ):
        _fail(f"{label} is not a safe relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _fail(f"{label} is not a safe relative path")
    return path.as_posix()


def _root(path: Path | str, *, label: str) -> Path:
    selected = Path(path).expanduser()
    if selected.is_symlink() or not selected.is_dir():
        _fail(f"{label} is not a regular directory")
    return selected.resolve(strict=True)


def _regular_bytes(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} is not a regular file")
    size = path.stat().st_size
    if size < 1 or size > MAX_FILE_BYTES:
        _fail(f"{label} exceeds the release artifact byte bound")
    raw = path.read_bytes()
    if len(raw) != size:
        _fail(f"{label} changed while reading")
    return raw


def _relative_to(root: Path, path: Path | str, *, label: str) -> tuple[str, Path]:
    selected = Path(path).expanduser()
    if not selected.is_absolute():
        selected = root / selected
    if selected.is_symlink() or not selected.is_file():
        _fail(f"{label} is not a regular file")
    resolved = selected.resolve(strict=True)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as error:
        raise ReleaseProvenanceV9Error(f"{label} is outside the assets root") from error
    return _safe_relative(relative, label=f"{label} path"), resolved


def _load_json(
    path: Path, *, label: str, schema: Path | None = None
) -> tuple[dict[str, Any], bytes]:
    raw = _regular_bytes(path, label=label)
    value = _strict_json(raw, label=label)
    if schema is not None:
        schema_value = _strict_json(
            _regular_bytes(schema, label=f"{label} schema"), label=f"{label} schema"
        )
        errors = list(
            Draft202012Validator(
                schema_value,
                format_checker=FormatChecker(),
            ).iter_errors(value)
        )
        if errors:
            location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
            _fail(f"{label} schema validation failed at {location}")
    return value, raw


def _environment() -> dict[str, Any]:
    try:
        uv_version = subprocess.run(
            ["uv", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseProvenanceV9Error("uv version is unavailable") from error
    return {
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "machine": platform.machine(),
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_executable_name": Path(sys.executable).name,
        "uv_version": uv_version,
        "ci": os.environ.get("CI", "").casefold() == "true",
        "github_actions": os.environ.get("GITHUB_ACTIONS", "").casefold() == "true",
        "github_runner_os": os.environ.get("RUNNER_OS"),
        "github_runner_arch": os.environ.get("RUNNER_ARCH"),
    }


def _artifact(root: Path, value: Mapping[str, Any], *, role: str) -> dict[str, Any]:
    relative, path = _relative_to(root, value.get("retained_path", ""), label=f"retained {role}")
    raw = _regular_bytes(path, label=f"retained {role}")
    if (
        value.get("sha256") != _sha256(raw)
        or value.get("byte_size") != len(raw)
        or value.get("name") != path.name
    ):
        _fail(f"retained {role} binding differs from reopened bytes")
    return {"path": relative, "sha256": _sha256(raw), "byte_size": len(raw)}


def _claim_rows(
    gate_values: Mapping[str, Mapping[str, Any]],
    gate_ids: Sequence[str],
) -> list[dict[str, Any]]:
    return [
        {
            "gate_id": gate_id,
            "status": gate_values[gate_id]["status"],
            "claim_eligible": gate_values[gate_id]["status"] == "passed",
        }
        for gate_id in gate_ids
    ]


def _reassemble(
    *,
    bundle_root: Path,
    retained_root: Path,
    expected_candidate: Mapping[str, Any],
    run_ids: Mapping[str, int],
) -> tuple[dict[str, Any], bytes, dict[str, dict[str, Any]]]:
    with tempfile.TemporaryDirectory(prefix="deeplaw-v9-provenance-") as temporary:
        output = Path(temporary)
        result = assembler.assemble_commercial_qualification(
            bundle_root=bundle_root,
            output_root=output,
            expected_candidate=expected_candidate,
            expected_run_ids=run_ids,
        )
        report_path = output / result["report_path"]
        report, report_raw = _load_json(
            report_path,
            label="reassembled commercial evidence report",
            schema=REPOSITORY / "contracts/commercial-evidence-report.v6.schema.json",
        )
        gate_values: dict[str, dict[str, Any]] = {}
        for reference in report["gate_results"]:
            relative = reference["result"]["relative_path"]
            generated_path = output / PurePosixPath(relative)
            retained_path = retained_root / PurePosixPath(relative)
            generated_raw = _regular_bytes(
                generated_path, label=f"generated Gate {reference['gate_id']}"
            )
            retained_raw = _regular_bytes(
                retained_path, label=f"retained Gate {reference['gate_id']}"
            )
            if generated_raw != retained_raw:
                _fail(f"retained Gate {reference['gate_id']} differs from independent assembly")
            gate_values[reference["gate_id"]] = _strict_json(
                generated_raw,
                label=f"Gate {reference['gate_id']}",
            )
        retained_report_raw = _regular_bytes(
            retained_root / PurePosixPath(result["report_path"]),
            label="retained commercial evidence report",
        )
        if report_raw != retained_report_raw:
            _fail("retained commercial evidence report differs from independent assembly")
        return report, report_raw, gate_values


def build_release_manifest(
    *,
    assets_root: Path | str,
    bundle_root: Path | str,
    report_path: Path | str,
    pre_publish_receipt_path: Path | str,
    release_commit: str,
    release_tree: str,
    candidate_run_id: int,
    evidence_run_id: int,
    qualification_run_id: int,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reopen the transitive v9 chain and derive one pre-public manifest."""

    root = _root(assets_root, label="assets root")
    bundle_directory = _root(bundle_root, label="Kernel bundle root")
    try:
        bundle_relative = bundle_directory.relative_to(root).as_posix()
    except ValueError as error:
        raise ReleaseProvenanceV9Error("Kernel bundle root is outside assets root") from error
    bundle_manifest_relative = _safe_relative(
        f"{bundle_relative}/{bundle.BUNDLE_MANIFEST_NAME}",
        label="Kernel bundle manifest path",
    )
    run_ids = {
        "candidate_run_id": candidate_run_id,
        "evidence_run_id": evidence_run_id,
        "qualification_run_id": qualification_run_id,
    }
    if (
        any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in run_ids.values()
        )
        or len(set(run_ids.values())) != 3
    ):
        _fail("candidate, evidence, and qualification run ids must be distinct positive integers")
    try:
        admitted = bundle.validate_bundle(bundle_directory, expected_run_ids=run_ids)
    except Exception as error:
        raise ReleaseProvenanceV9Error("Kernel qualification bundle rejected") from error
    if admitted.get("status") != "passed":
        _fail("Kernel qualification bundle did not pass admission")
    bundle_manifest, bundle_raw = _load_json(
        bundle_directory / bundle.BUNDLE_MANIFEST_NAME,
        label="Kernel bundle manifest",
        schema=REPOSITORY / "contracts/kernel-qualification-bundle-manifest.v1.schema.json",
    )
    candidate = bundle_manifest["candidate_binding"]
    if not GIT_RE.fullmatch(release_commit) or not GIT_RE.fullmatch(release_tree):
        _fail("release Git identity is invalid")
    if release_tree != candidate["tree"]:
        _fail("release tree differs from the exact candidate tree")
    report_relative, report_file = _relative_to(
        root, report_path, label="commercial evidence report"
    )
    report, report_raw = _load_json(
        report_file,
        label="commercial evidence report",
        schema=REPOSITORY / "contracts/commercial-evidence-report.v6.schema.json",
    )
    reassembled, reassembled_raw, gate_values = _reassemble(
        bundle_root=bundle_directory,
        retained_root=root,
        expected_candidate=candidate,
        run_ids=run_ids,
    )
    if report != reassembled or report_raw != reassembled_raw:
        _fail("commercial evidence report differs from independent assembly")
    expected_report_candidate = {
        "candidate_commit": candidate["commit"],
        "candidate_tree": candidate["tree"],
        "candidate_wheel_sha256": candidate["wheel_sha256"],
        "candidate_sdist_sha256": candidate["sdist_sha256"],
    }
    classification_sha = bundle_manifest["bindings"]["gate_classification"]["sha256"]
    if (
        report["candidate_binding"] != expected_report_candidate
        or report["qualification_run_id"] != qualification_run_id
        or report["classification_binding"]["classification_sha256"] != classification_sha
        or set(gate_values)
        != set(
            (
                *assembler.CORE_GATE_IDS,
                *assembler.CAPABILITY_GATE_IDS,
                *assembler.COMPETITIVE_GATE_IDS,
            )
        )
    ):
        _fail("commercial report identity differs from the Kernel bundle")
    core_passed = all(
        gate_values[gate_id]["status"] == "passed" for gate_id in assembler.CORE_GATE_IDS
    )
    hard_zero = all(
        all(item["count"] == 0 for item in gate_values[gate_id]["hard_failures"])
        for gate_id in assembler.CORE_GATE_IDS
    )
    if report["kernel_release_core_passed"] is not core_passed or report["release_ready"] is not (
        core_passed and hard_zero
    ):
        _fail("commercial report release flags do not derive from Core Gate bytes")

    pre_relative, pre_file = _relative_to(
        root,
        pre_publish_receipt_path,
        label="pre-publish artifact receipt",
    )
    pre_publish, pre_raw = _load_json(
        pre_file,
        label="pre-publish artifact receipt",
        schema=REPOSITORY / "contracts/pre-publish-artifact-gate.v1.schema.json",
    )
    if pre_publish["record_sha256"] != record_sha256(pre_publish):
        _fail("pre-publish receipt record digest differs")
    supply_manifests = [
        item
        for item in bundle_manifest["files"]
        if item["artifact_kind"] == "typed_manifest"
        and item["evidence_kind"] == "retained_supply_chain"
    ]
    if len(supply_manifests) != 1:
        _fail("Kernel bundle does not contain one retained supply-chain manifest")
    supply_path = bundle_directory / PurePosixPath(supply_manifests[0]["relative_path"])
    supply, _supply_raw = _load_json(supply_path, label="retained supply-chain typed manifest")
    pre_source = supply.get("payload", {}).get("pre_publish_receipt_source")
    if not isinstance(pre_source, Mapping) or pre_source.get("sha256") != _sha256(pre_raw):
        _fail("pre-publish receipt differs from the typed supply-chain source")
    if pre_publish["candidate"] != {
        "commit": candidate["commit"],
        "tree": candidate["tree"],
        "lock_sha256": candidate["lock_sha256"],
    }:
        _fail("pre-publish receipt candidate differs from the Kernel bundle")
    builds = pre_publish["builds"]
    for build_id in ("first", "second"):
        if (
            builds[build_id]["wheel_sha256"] != candidate["wheel_sha256"]
            or builds[build_id]["sdist_sha256"] != candidate["sdist_sha256"]
        ):
            _fail("pre-publish reproducible build differs from the candidate")
    retained = pre_publish["retained_artifacts"]
    wheel = _artifact(root, retained["wheel"], role="wheel")
    sdist = _artifact(root, retained["sdist"], role="sdist")
    if wheel["sha256"] != candidate["wheel_sha256"] or sdist["sha256"] != candidate["sdist_sha256"]:
        _fail("retained artifact hashes differ from the candidate")
    _retained_relative, retained_manifest_file = _relative_to(
        root,
        retained["manifest_path"],
        label="retained artifact manifest",
    )
    retained_manifest_raw = _regular_bytes(
        retained_manifest_file,
        label="retained artifact manifest",
    )
    if _sha256(retained_manifest_raw) != retained["manifest_sha256"]:
        _fail("retained artifact manifest hash differs")
    for role in ("sbom", "openvex", "licenses", "provenance"):
        reference = pre_publish[role]
        _relative, artifact_path = _relative_to(
            root,
            reference["path"],
            label=f"pre-publish {role}",
        )
        if (
            _sha256(_regular_bytes(artifact_path, label=f"pre-publish {role}"))
            != reference["sha256"]
        ):
            _fail(f"pre-publish {role} hash differs from reopened bytes")

    release_ready = core_passed and hard_zero and report["release_ready"] is True
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "reference_provenance": REFERENCE_PROVENANCE,
        "human_authenticity": HUMAN_AUTHENTICITY,
        "environment": dict(environment) if environment is not None else _environment(),
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": "0.13.0",
            "tag": "v0.13.0",
            "commit": release_commit,
            "tree": release_tree,
        },
        "run_ids": run_ids,
        "candidate_binding": dict(candidate),
        "artifact_binding": {
            "wheel": wheel,
            "sdist": sdist,
            "retained_manifest_sha256": retained["manifest_sha256"],
        },
        "evidence_bundle_binding": {
            "manifest_path": bundle_manifest_relative,
            "manifest_sha256": _sha256(bundle_raw),
            "candidate_run_id": candidate_run_id,
            "evidence_run_id": evidence_run_id,
        },
        "pre_publish_artifact_gate": {
            "path": pre_relative,
            "receipt_sha256": _sha256(pre_raw),
            "status": "pre_publish_passed",
        },
        "gate_evidence": {
            "report_path": report_relative,
            "report_sha256": _sha256(report_raw),
            "record_sha256": report["report_sha256"],
            "classification_sha256": classification_sha,
            "status": "passed" if release_ready else "failed",
            "hard_zero": hard_zero,
            "core_gates_passed": core_passed,
            "capability_claims": _claim_rows(gate_values, assembler.CAPABILITY_GATE_IDS),
            "competitive_research_claims": _claim_rows(
                gate_values,
                assembler.COMPETITIVE_GATE_IDS,
            ),
        },
        "release_ready": release_ready,
        "public_release_verified": False,
        "post_public_verification": None,
        "kernel_release_claim_eligible": release_ready,
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
        "record_sha256": "0" * 64,
    }
    manifest["record_sha256"] = record_sha256(manifest)
    schema = _strict_json(
        _regular_bytes(SCHEMA_PATH, label="release schema"), label="release schema"
    )
    errors = list(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest)
    )
    if errors:
        location = ".".join(str(part) for part in errors[0].absolute_path) or "<root>"
        _fail(f"release manifest schema validation failed at {location}")
    return manifest


def validate_release_provenance(
    release_manifest_path: Path | str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Rebuild the expected manifest and compare every non-environment field."""

    path = Path(release_manifest_path).expanduser()
    release, raw = _load_json(path, label="release manifest", schema=SCHEMA_PATH)
    if release["record_sha256"] != record_sha256(release):
        _fail("release manifest record digest differs")
    expected = build_release_manifest(
        release_commit=release["release"]["commit"],
        release_tree=release["release"]["tree"],
        candidate_run_id=release["run_ids"]["candidate_run_id"],
        evidence_run_id=release["run_ids"]["evidence_run_id"],
        qualification_run_id=release["run_ids"]["qualification_run_id"],
        environment=release["environment"],
        **kwargs,
    )
    if release != expected:
        _fail("release manifest differs from reopened transitive evidence")
    return {
        "schema_version": "deeplaw.release-provenance-validation/v9",
        "status": "transitive_provenance_validated",
        "release_manifest_sha256": _sha256(raw),
        "record_sha256": release["record_sha256"],
        "release_ready": release["release_ready"],
        "kernel_release_claim_eligible": release["kernel_release_claim_eligible"],
        "human_attested_claim_eligible": False,
        "competitive_claim_eligible": False,
    }


verify_release_provenance = validate_release_provenance


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--pre-publish-receipt", type=Path, required=True)
    parser.add_argument("--release-commit")
    parser.add_argument("--release-tree")
    parser.add_argument("--candidate-run-id", type=int)
    parser.add_argument("--evidence-run-id", type=int)
    parser.add_argument("--qualification-run-id", type=int)
    parser.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        common = {
            "assets_root": args.assets_root,
            "bundle_root": args.bundle_root,
            "report_path": args.report,
            "pre_publish_receipt_path": args.pre_publish_receipt,
        }
        if args.write:
            if not all(
                (
                    args.release_commit,
                    args.release_tree,
                    args.candidate_run_id,
                    args.evidence_run_id,
                    args.qualification_run_id,
                )
            ):
                _fail("write mode requires exact release and run bindings")
            manifest = build_release_manifest(
                **common,
                release_commit=args.release_commit,
                release_tree=args.release_tree,
                candidate_run_id=args.candidate_run_id,
                evidence_run_id=args.evidence_run_id,
                qualification_run_id=args.qualification_run_id,
            )
            target = args.release_manifest
            if target.exists() and (target.is_symlink() or not target.is_file()):
                _fail("release manifest output target is unsafe")
            target.write_bytes(canonical_json(manifest) + b"\n")
        validation = validate_release_provenance(args.release_manifest, **common)
        sys.stdout.write(canonical_json(validation).decode("utf-8") + "\n")
        return 0
    except (OSError, ReleaseProvenanceV9Error, ValueError) as error:
        print(f"release provenance v9 rejected: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ReleaseProvenanceV9Error",
    "build_release_manifest",
    "canonical_json",
    "main",
    "record_sha256",
    "validate_release_provenance",
    "verify_release_provenance",
]
