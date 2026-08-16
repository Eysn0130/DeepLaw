"""Fail-closed verification for a sanitized external v0.13 evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.release.v013_gate_collection import GateCollectionError, validate_collection
from deeplaw.util import assert_provider_output_safe

MAX_FILES = 10_000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_ABSOLUTE_PATH = re.compile(
    r"(?:(?<![A-Za-z0-9])/(?:Users|home|private|var|tmp|root|etc|opt|Volumes)(?:/|$))"
    r"|(?:^|[\s\"'])[A-Za-z]:[\\/]",
    re.IGNORECASE,
)
_SECRET_NAME = re.compile(
    r"(?:^|[._-])(?:auth|credential|credentials|secret|secrets|password|passwd|"
    r"api[_-]?key|private[_-]?key|token)(?:$|[._-])",
    re.IGNORECASE,
)
_FORBIDDEN_CONTENT_NAMES = re.compile(
    r"(?:transcript|chain[_-]?of[_-]?thought|hidden[_-]?reasoning|raw[_-]?(?:events|log))",
    re.IGNORECASE,
)
_SAFE_MEASUREMENT_KEYS = frozenset(
    {
        "absolute_path_count",
        "ambient_credentials_visible",
        "authentication_material_retained",
        "credential_path_forwarded",
        "private_path_count",
        "private_path_matches",
        "secret_canary_count",
        "secret_canary_matches",
        "secret_count",
        "secret_leak",
    }
)
BUNDLE_MANIFEST_NAME = "bundle-manifest.json"
REPOSITORY = Path(__file__).resolve().parents[2]
BUNDLE_MANIFEST_SCHEMA = (
    REPOSITORY / "contracts/external-qualification-bundle-manifest.v2.schema.json"
)


class ExternalQualificationBundleError(ValueError):
    """Raised when an external bundle is missing, unsafe, or not reproducible."""


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ExternalQualificationBundleError(
            "external qualification JSON must be a regular file"
        )
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise ExternalQualificationBundleError(
            "external qualification JSON must be strict UTF-8 JSON"
        ) from error
    if not isinstance(value, dict):
        raise ExternalQualificationBundleError(
            "external qualification JSON must be an object"
        )
    return value


def _check_json_projection(value: Any, *, depth: int = 0) -> None:
    if depth > 20:
        raise ExternalQualificationBundleError("external evidence exceeds its depth bound")
    if isinstance(value, str):
        try:
            assert_provider_output_safe(value, interface="external qualification evidence")
        except PermissionError:
            raise ExternalQualificationBundleError(
                "external evidence contains private path or Secret material"
            ) from None
        if _ABSOLUTE_PATH.search(value):
            raise ExternalQualificationBundleError(
                "external evidence contains a private absolute path"
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or (
                _SECRET_NAME.search(key) and key not in _SAFE_MEASUREMENT_KEYS
            ):
                raise ExternalQualificationBundleError(
                    "external evidence contains a Secret-shaped field"
                )
            _check_json_projection(item, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value:
            _check_json_projection(item, depth=depth + 1)
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExternalQualificationBundleError(
                "external evidence contains a non-finite number"
            )
        return
    raise ExternalQualificationBundleError("external evidence contains an unsupported value")


def _check_sanitized_text(path: Path, raw: bytes) -> None:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ExternalQualificationBundleError(
            "sanitized non-JSON evidence must be UTF-8 text"
        ) from error
    _check_json_projection(text)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_sha256(value: dict[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _active_candidate(active: dict[str, Any]) -> dict[str, Any]:
    candidate = active.get("candidate_binding")
    if not isinstance(candidate, dict):
        raise ExternalQualificationBundleError(
            "active qualification candidate binding is missing"
        )
    return {
        "commit": candidate.get("source_commit"),
        "tree": candidate.get("source_tree"),
        "lock_sha256": candidate.get("lock_sha256"),
        "wheel_sha256": candidate.get("wheel_sha256"),
        "sdist_sha256": candidate.get("sdist_sha256"),
    }


def _validate_bundle_manifest(
    manifest_path: Path,
    *,
    files: dict[str, tuple[Path, bytes]],
    active_qualification: Path,
    expected_candidate_run_id: int | None,
    expected_evidence_run_id: int | None,
) -> dict[str, Any]:
    manifest = _strict_json(manifest_path)
    schema = _strict_json(BUNDLE_MANIFEST_SCHEMA)
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        raise ExternalQualificationBundleError(
            f"external bundle manifest schema violation at {location}: {errors[0].message}"
        )
    if manifest["record_sha256"] != _record_sha256(manifest):
        raise ExternalQualificationBundleError(
            "external bundle manifest record digest differs"
        )
    for label, observed, expected in (
        ("candidate", manifest["candidate_run_id"], expected_candidate_run_id),
        ("evidence", manifest["evidence_run_id"], expected_evidence_run_id),
    ):
        if (
            not isinstance(expected, int)
            or isinstance(expected, bool)
            or expected < 1
            or observed != expected
        ):
            raise ExternalQualificationBundleError(
                f"external bundle {label} workflow run identity differs"
            )
    references = manifest["files"]
    by_path = {item["relative_path"]: item for item in references}
    if len(by_path) != len(references):
        raise ExternalQualificationBundleError(
            "external bundle manifest contains duplicate file references"
        )
    observed_paths = set(files)
    referenced_paths = set(by_path)
    if observed_paths != referenced_paths | {BUNDLE_MANIFEST_NAME}:
        raise ExternalQualificationBundleError(
            "external bundle contains an orphan or unreferenced file"
        )
    for relative_path, reference in by_path.items():
        selected = files.get(relative_path)
        if selected is None:
            raise ExternalQualificationBundleError(
                "external bundle manifest references a missing file"
            )
        _path, raw = selected
        if (
            reference["byte_size"] != len(raw)
            or reference["sha256"] != hashlib.sha256(raw).hexdigest()
        ):
            raise ExternalQualificationBundleError(
                "external bundle file binding differs from retained bytes"
            )
    active = _strict_json(active_qualification)
    if active.get("status") != "frozen_exact_candidate":
        raise ExternalQualificationBundleError(
            "external qualification requires a frozen exact candidate"
        )
    if manifest["candidate_binding"] != _active_candidate(active):
        raise ExternalQualificationBundleError(
            "external bundle candidate differs from active qualification"
        )
    external = active.get("external_inputs")
    if not isinstance(external, dict):
        raise ExternalQualificationBundleError(
            "active qualification external input binding is missing"
        )
    current_external = manifest["external_inputs"]
    active_external = {
        "semantic_gold_sha256": external.get(
            "semantic_gold_manifest_sha256",
            external.get("human_gold_manifest_sha256"),
        ),
        "candidate_gold_binding_sha256": external.get(
            "candidate_gold_binding_sha256"
        ),
        "qualification_holdout_sha256": external.get(
            "qualification_holdout_sha256"
        ),
        "final_blind_holdout_sha256": external.get(
            "final_blind_holdout_sha256"
        ),
        "runner_sha256": external.get("runner_sha256"),
        "scorer_sha256": external.get("scorer_sha256"),
        "compiler_scorer_isolation_sha256": external.get(
            "compiler_scorer_isolation_sha256"
        ),
    }
    if current_external != active_external:
        raise ExternalQualificationBundleError(
            "external bundle Gold, corpus, runner, or scorer binding differs"
        )
    return manifest


def validate_external_bundle(
    root: Path,
    *,
    active_qualification: Path,
    classification: Path,
    expected_candidate_run_id: int | None = None,
    expected_evidence_run_id: int | None = None,
) -> dict[str, Any]:
    """Verify only the sanitized bundle and re-run every Core source validator."""

    expanded_root = root.expanduser()
    if expanded_root.is_symlink():
        raise ExternalQualificationBundleError(
            "external evidence root must not be a symbolic link"
        )
    selected_root = expanded_root.resolve(strict=True)
    if not selected_root.is_dir():
        raise ExternalQualificationBundleError("external evidence root is not a directory")
    files: dict[str, tuple[Path, bytes]] = {}
    total_bytes = 0
    for path in selected_root.rglob("*"):
        if path.is_symlink():
            raise ExternalQualificationBundleError(
                "external evidence must not contain symbolic links"
            )
        if not path.is_file():
            continue
        relative = PurePosixPath(path.relative_to(selected_root).as_posix())
        if (
            any(_SECRET_NAME.search(part) for part in relative.parts)
            or any(_FORBIDDEN_CONTENT_NAMES.search(part) for part in relative.parts)
            or path.name == ".env"
        ):
            raise ExternalQualificationBundleError(
                "external evidence contains a forbidden filename"
            )
        size = path.stat().st_size
        if not 1 <= size <= MAX_FILE_BYTES:
            raise ExternalQualificationBundleError(
                "external evidence file exceeds its byte bound"
            )
        raw = path.read_bytes()
        total_bytes += size
        relative_name = relative.as_posix()
        files[relative_name] = (path, raw)
        if len(files) > MAX_FILES or total_bytes > MAX_TOTAL_BYTES:
            raise ExternalQualificationBundleError(
                "external evidence bundle exceeds its aggregate bound"
            )
        if path.suffix == ".json":
            _check_json_projection(_strict_json(path))
        else:
            _check_sanitized_text(path, raw)
    manifest_path = selected_root / BUNDLE_MANIFEST_NAME
    collection_path = selected_root / "evidence/gate-collection.json"
    template_path = selected_root / "commercial-release-template.json"
    for required in (manifest_path, collection_path, template_path):
        if required.is_symlink() or not required.is_file():
            raise ExternalQualificationBundleError(
                "external evidence bundle is incomplete"
            )
    manifest = _validate_bundle_manifest(
        manifest_path,
        files=files,
        active_qualification=active_qualification,
        expected_candidate_run_id=expected_candidate_run_id,
        expected_evidence_run_id=expected_evidence_run_id,
    )
    try:
        result = validate_collection(
            collection_path,
            root=selected_root,
            active_path=active_qualification,
            classification_path=classification,
            expected_evidence_run_id=expected_evidence_run_id,
        )
    except GateCollectionError as error:
        raise ExternalQualificationBundleError(str(error)) from error
    if not result["release_ready"] or not result["claim_eligible"]:
        raise ExternalQualificationBundleError(
            "external evidence does not pass every Core Gate"
        )
    return {
        "schema_version": "deeplaw.external-qualification-bundle-validation/v2",
        "status": "passed",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "candidate_run_id": manifest["candidate_run_id"],
        "evidence_run_id": manifest["evidence_run_id"],
        "bundle_manifest_sha256": hashlib.sha256(
            files[BUNDLE_MANIFEST_NAME][1]
        ).hexdigest(),
        "release_ready": True,
        "claim_eligible": True,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--active-qualification", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--candidate-run-id", type=int, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    args = parser.parse_args()
    try:
        result = validate_external_bundle(
            args.root,
            active_qualification=args.active_qualification,
            classification=args.classification,
            expected_candidate_run_id=args.candidate_run_id,
            expected_evidence_run_id=args.evidence_run_id,
        )
    except (OSError, ExternalQualificationBundleError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
