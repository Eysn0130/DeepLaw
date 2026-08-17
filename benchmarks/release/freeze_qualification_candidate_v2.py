"""Freeze one exact v0.13 machine-only candidate from verified build receipts.

The tracked v2 document is a construction template.  This boundary is opened
only after the reproducible report and retained artifact manifest agree on the
same clean source, lock, wheel, and sdist bytes.  It never performs a build,
qualification run, or release decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPOSITORY = Path(__file__).resolve().parents[2]
ACTIVE_SCHEMA = REPOSITORY / "contracts/v013-active-qualification.v2.schema.json"
RETAINED_SCHEMA = REPOSITORY / "contracts/retained-candidate-artifacts.v1.schema.json"
MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
CONSTRUCTION_STATUS = "construction_candidate_machine_evaluation_pending"
FROZEN_STATUS = "frozen_exact_candidate_machine_evaluation_pending"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")


class QualificationFreezeV2Error(ValueError):
    """Raised when exact machine-candidate provenance cannot be closed."""


QualificationFreezeError = QualificationFreezeV2Error


def _error(message: str) -> None:
    raise QualificationFreezeV2Error(message)


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _error("strict JSON contains a duplicate key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    _error(f"strict JSON contains a non-finite constant: {value}")


def _check_json(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        _error("strict JSON exceeds its depth bound")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                _error("strict JSON object key is not a string")
            _check_json(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_json(item, depth=depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        _error("strict JSON contains a non-finite number")
    elif value is not None and not isinstance(value, (str, int, bool, float)):
        _error("strict JSON contains an unsupported value")


def _has_symlink_component(path: Path) -> bool:
    parts = path.parts
    start = 1 if path.is_absolute() else 0
    for index in range(start, len(parts) + 1):
        try:
            if Path(*parts[:index]).is_symlink():
                return True
        except OSError:
            return True
    return False


def _regular_file(
    path: Path, *, label: str, max_bytes: int = MAX_INPUT_BYTES
) -> tuple[Path, bytes]:
    selected = path.expanduser()
    if _has_symlink_component(selected) or selected.is_symlink():
        _error(f"{label} must be a regular non-symlink file")
    try:
        resolved = selected.resolve(strict=True)
        file_stat = resolved.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            _error(f"{label} must be a regular non-symlink file")
        size = file_stat.st_size
        if not 1 <= size <= max_bytes:
            _error(f"{label} exceeds its byte bound")
        raw = resolved.read_bytes()
    except QualificationFreezeV2Error:
        raise
    except OSError as error:
        raise QualificationFreezeV2Error(f"{label} is unavailable") from error
    if len(raw) != size:
        _error(f"{label} changed while it was read")
    return resolved, raw


def _strict_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes, Path]:
    resolved, raw = _regular_file(path, label=label)
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except QualificationFreezeV2Error:
        raise
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise QualificationFreezeV2Error(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        _error(f"{label} must be a JSON object")
    _check_json(value)
    return value, raw, resolved


def _schema(path: Path, *, label: str) -> dict[str, Any]:
    value, _raw, _resolved = _strict_json(path, label=label)
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise QualificationFreezeV2Error(f"{label} is not a valid JSON Schema") from error
    return value


def _validate(value: Any, schema: Mapping[str, Any], *, label: str) -> None:
    try:
        errors = sorted(
            Draft202012Validator(schema).iter_errors(value),
            key=lambda error: list(error.path),
        )
    except Exception as error:
        raise QualificationFreezeV2Error(f"{label} schema validation failed") from error
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        _error(f"{label} schema validation failed at {location}: {errors[0].message}")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path, *, label: str) -> tuple[str, int]:
    _resolved, raw = _regular_file(path, label=label, max_bytes=512 * 1024 * 1024)
    return _sha256(raw), len(raw)


def _record_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            {key: item for key, item in value.items() if key != "record_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise QualificationFreezeV2Error("record is not canonical JSON") from error
    return _sha256(encoded)


def _verify_record(value: Mapping[str, Any], *, label: str) -> None:
    record = value.get("record_sha256")
    if not isinstance(record, str) or not SHA256.fullmatch(record):
        _error(f"{label} record digest is missing")
    if record != _record_digest(value):
        _error(f"{label} record digest differs")


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _error(f"{label} must be an object")
    return value


def _sha(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        _error(f"{label} must be a lowercase SHA-256 digest")
    return value


def _git(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not GIT.fullmatch(value):
        _error(f"{label} must be an exact Git object digest")
    return value


def _binding_from_report(report: Mapping[str, Any]) -> Mapping[str, Any]:
    if report.get("schema_version") != "deeplaw.reproducible-build-report/v2":
        _error("reproducible build report schema is unsupported")
    _verify_record(report, label="reproducible build report")
    binding = _mapping(report.get("binding"), label="reproducible build binding")
    commit = _git(binding.get("commit"), label="reproducible build commit")
    _git(binding.get("tree"), label="reproducible build tree")
    lock = _sha(binding.get("lock_sha256"), label="reproducible build lock")
    if binding.get("package_version") != "0.13.0":
        _error("reproducible build package version is not 0.13.0")
    if binding.get("worktree_clean") is not True:
        _error("reproducible build source tree is not clean")
    if report.get("repository_commit") != commit:
        _error("reproducible build repository commit differs")
    if report.get("working_tree_dirty") is not False:
        _error("reproducible build report marks the tree dirty")
    if report.get("lock_sha256") != lock:
        _error("reproducible build report lock differs")
    if report.get("reproducible") is not True:
        _error("reproducible build did not pass")
    if report.get("package_inventory_verified") is not True:
        _error("reproducible build package inventory is not verified")
    if report.get("artifact_release_eligible") is not True:
        _error("reproducible build is not release-eligible")
    blockers = report.get("artifact_release_blockers")
    if blockers != []:
        _error("reproducible build has artifact blockers")
    if isinstance(report.get("source_date_epoch"), bool) or not isinstance(
        report.get("source_date_epoch"), int
    ):
        _error("reproducible build source date is invalid")
    return binding


def _artifact_rows(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = report.get("artifacts")
    if not isinstance(rows, list) or len(rows) != 2:
        _error("reproducible build must contain exactly two artifacts")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        item = _mapping(row, label="reproducible artifact row")
        name = item.get("name")
        if not isinstance(name, str) or not ARTIFACT.fullmatch(name) or name in result:
            _error("reproducible artifact name is invalid or duplicated")
        _sha(item.get("sha256"), label=f"reproducible artifact {name} hash")
        size = item.get("byte_size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            _error(f"reproducible artifact {name} size is invalid")
        result[name] = item
    wheels = [name for name in result if name.endswith(".whl")]
    sdists = [name for name in result if name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1:
        _error("reproducible build must contain one wheel and one sdist")
    if not wheels[0].startswith("deeplaw-0.13.0-") or sdists[0] != "deeplaw-0.13.0.tar.gz":
        _error("reproducible artifact filenames are not the exact 0.13.0 candidate")
    return result


def _verify_manifest(
    manifest: Mapping[str, Any],
    *,
    manifest_raw: bytes,
    report: Mapping[str, Any],
    report_binding: Mapping[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    retained_schema = _schema(RETAINED_SCHEMA, label="retained artifact schema")
    _validate(manifest, retained_schema, label="retained artifact manifest")
    if manifest.get("schema_version") != "deeplaw.retained-candidate-artifacts/v1":
        _error("retained artifact manifest schema is unsupported")
    if manifest.get("package_version") != "0.13.0":
        _error("retained artifact manifest package version is not 0.13.0")
    if manifest.get("release_ready") is not False or manifest.get("claim_eligible") is not False:
        _error("retained artifact manifest contains a release or claim assertion")
    for field, label in (
        ("git_commit", "retained artifact commit"),
        ("git_tree", "retained artifact tree"),
        ("lock_sha256", "retained artifact lock"),
    ):
        expected_key = {
            "git_commit": "commit",
            "git_tree": "tree",
            "lock_sha256": "lock_sha256",
        }[field]
        expected = report_binding[expected_key]
        actual = manifest.get(field)
        if actual != expected:
            raise QualificationFreezeV2Error(f"{label} differs from reproducible report")
    artifacts = _artifact_rows(report)
    manifest_artifacts: dict[str, Mapping[str, Any]] = {}
    for role in ("wheel", "sdist"):
        row = _mapping(manifest.get(role), label=f"retained {role}")
        name = row.get("filename")
        if not isinstance(name, str) or name in manifest_artifacts:
            _error(f"retained {role} filename is invalid")
        _sha(row.get("sha256"), label=f"retained {role} hash")
        size = row.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            _error(f"retained {role} size is invalid")
        if name not in artifacts:
            _error(f"retained {role} is absent from reproducible report")
        report_row = artifacts[name]
        if row["sha256"] != report_row["sha256"] or size != report_row["byte_size"]:
            _error(f"retained {role} differs from reproducible report")
        manifest_artifacts[role] = row
    artifact_dir = manifest_path.parent
    for role, row in manifest_artifacts.items():
        artifact_path = artifact_dir / str(row["filename"])
        digest, size = _sha256_file(artifact_path, label=f"retained {role} bytes")
        if digest != row["sha256"] or size != row["bytes"]:
            _error(f"retained {role} bytes differ from manifest")
    wheels = list(artifact_dir.glob("*.whl"))
    sdists = list(artifact_dir.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        _error("candidate artifact directory must contain one wheel and one sdist")
    if wheels[0].name != manifest_artifacts["wheel"]["filename"]:
        _error("candidate wheel directory contains an unexpected artifact")
    if sdists[0].name != manifest_artifacts["sdist"]["filename"]:
        _error("candidate sdist directory contains an unexpected artifact")
    return {
        "wheel": dict(manifest_artifacts["wheel"]),
        "sdist": dict(manifest_artifacts["sdist"]),
        "artifact_manifest_sha256": _sha256(manifest_raw),
    }


def _verify_protocol(template: Mapping[str, Any]) -> None:
    protocol = _mapping(template.get("protocol_binding"), label="protocol binding")
    relative = protocol.get("relative_path")
    if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
        _error("protocol binding path is invalid")
    protocol_path, protocol_raw = _regular_file(REPOSITORY / relative, label="protocol binding")
    if REPOSITORY not in protocol_path.parents:
        _error("protocol binding escapes the repository")
    if protocol.get("sha256") != _sha256(protocol_raw):
        _error("protocol binding hash differs from the retained protocol bytes")


def freeze_candidate(
    *,
    template_path: Path,
    reproducible_report_path: Path,
    artifact_manifest_path: Path,
) -> dict[str, Any]:
    template, _template_raw, _template_resolved = _strict_json(
        template_path, label="active qualification construction template"
    )
    report, _report_raw, _report_resolved = _strict_json(
        reproducible_report_path, label="reproducible build report"
    )
    manifest, manifest_raw, manifest_resolved = _strict_json(
        artifact_manifest_path, label="retained artifact manifest"
    )

    # Check the construction state before schema validation so the tracked
    # 0.12 pending document can never be mistaken for a candidate template.
    if template.get("profile") != "machine_evaluated_no_human_attestation":
        _error("active qualification template is not machine-only")
    if template.get("status") != CONSTRUCTION_STATUS:
        _error("active qualification template is not in construction state")
    if template.get("candidate_version") != "0.13.0":
        _error("active qualification template is not the 0.13.0 candidate")
    _verify_protocol(template)
    external = _mapping(template.get("external_inputs"), label="active external inputs")
    if not external:
        _error("active external inputs are empty")
    for key, value in external.items():
        _sha(value, label=f"active external input {key}")

    active_schema = _schema(ACTIVE_SCHEMA, label="active qualification v2 schema")
    _validate(template, active_schema, label="active qualification construction template")
    report_binding = _binding_from_report(report)
    report_artifacts = _artifact_rows(report)
    retained = _verify_manifest(
        manifest,
        manifest_raw=manifest_raw,
        report=report,
        report_binding=report_binding,
        manifest_path=manifest_resolved,
    )
    if set(report_artifacts) != {
        retained["wheel"]["filename"],
        retained["sdist"]["filename"],
    }:
        _error("reproducible report and retained manifest artifact sets differ")

    candidate_binding = dict(_mapping(template.get("candidate_binding"), label="candidate binding"))
    candidate_binding.update(
        {
            "package_version": "0.13.0",
            "source_commit": report_binding["commit"],
            "source_tree": report_binding["tree"],
            "lock_sha256": report_binding["lock_sha256"],
            "wheel_filename": retained["wheel"]["filename"],
            "wheel_sha256": retained["wheel"]["sha256"],
            "sdist_filename": retained["sdist"]["filename"],
            "sdist_sha256": retained["sdist"]["sha256"],
            "artifact_manifest_sha256": retained["artifact_manifest_sha256"],
        }
    )
    if "source_date_epoch" in candidate_binding:
        candidate_binding["source_date_epoch"] = report["source_date_epoch"]
    frozen = dict(template)
    frozen["status"] = FROZEN_STATUS
    frozen["candidate_version"] = "0.13.0"
    frozen["candidate_binding"] = candidate_binding
    frozen["blocker"] = None
    frozen["release_ready"] = False
    frozen["claim_eligible"] = False
    if "machine_qualification_claim_eligible" in frozen:
        frozen["machine_qualification_claim_eligible"] = False
    if "competitive_claim_eligible" in frozen:
        frozen["competitive_claim_eligible"] = False
    _validate(frozen, active_schema, label="frozen active qualification")
    return frozen


freeze_machine_candidate = freeze_candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--reproducible-report", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        value = freeze_candidate(
            template_path=args.template,
            reproducible_report_path=args.reproducible_report,
            artifact_manifest_path=args.artifact_manifest,
        )
        output = args.output
        if _has_symlink_component(output) or output.is_symlink():
            raise QualificationFreezeV2Error("freeze output must not be a symlink")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except (OSError, QualificationFreezeV2Error, ValueError) as error:
        print(f"qualification candidate v2 freeze failed: {error}", file=sys.stderr)
        return 1
    return 0


__all__ = [
    "QualificationFreezeError",
    "QualificationFreezeV2Error",
    "freeze_candidate",
    "freeze_machine_candidate",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
