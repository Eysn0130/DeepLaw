"""Fail-closed verification for a sanitized external v0.13 evidence bundle."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

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


class ExternalQualificationBundleError(ValueError):
    """Raised when an external bundle is missing, unsafe, or not reproducible."""


def _strict_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ExternalQualificationBundleError(
            "external qualification JSON is invalid"
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
    if value is None or isinstance(value, (bool, int, float)):
        return
    raise ExternalQualificationBundleError("external evidence contains an unsupported value")


def validate_external_bundle(
    root: Path,
    *,
    active_qualification: Path,
    classification: Path,
    expected_evidence_run_id: int | None = None,
) -> dict[str, Any]:
    """Verify only the sanitized bundle and re-run every Core source validator."""

    selected_root = root.expanduser().resolve(strict=True)
    if not selected_root.is_dir():
        raise ExternalQualificationBundleError("external evidence root is not a directory")
    files: list[Path] = []
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
        total_bytes += size
        files.append(path)
        if len(files) > MAX_FILES or total_bytes > MAX_TOTAL_BYTES:
            raise ExternalQualificationBundleError(
                "external evidence bundle exceeds its aggregate bound"
            )
        if path.suffix == ".json":
            _check_json_projection(_strict_json(path))
    collection_path = selected_root / "evidence/gate-collection.json"
    template_path = selected_root / "commercial-release-template.json"
    for required in (collection_path, template_path):
        if required.is_symlink() or not required.is_file():
            raise ExternalQualificationBundleError(
                "external evidence bundle is incomplete"
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
        "schema_version": "deeplaw.external-qualification-bundle-validation/v1",
        "status": "passed",
        "file_count": len(files),
        "total_bytes": total_bytes,
        "release_ready": True,
        "claim_eligible": True,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--active-qualification", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--evidence-run-id", type=int, required=True)
    args = parser.parse_args()
    try:
        result = validate_external_bundle(
            args.root,
            active_qualification=args.active_qualification,
            classification=args.classification,
            expected_evidence_run_id=args.evidence_run_id,
        )
    except (OSError, ExternalQualificationBundleError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
