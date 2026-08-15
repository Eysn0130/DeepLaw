"""Create the external frozen qualification binding for verified v0.13 bytes.

The tracked active document remains a construction template. Freezing happens
after the reproducible build, so artifact hashes do not create a circular source
tree dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.release.evidence import canonical_json, verify_record_digest

REPOSITORY = Path(__file__).resolve().parents[2]
SCHEMA = REPOSITORY / "contracts/v013-active-qualification.v1.schema.json"


class QualificationFreezeError(ValueError):
    """Raised when the construction template cannot bind one exact artifact."""


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise QualificationFreezeError("qualification freeze input must be a regular file")
    raw = path.read_bytes()
    if not 1 <= len(raw) <= 8 * 1024 * 1024:
        raise QualificationFreezeError("qualification freeze input exceeds its byte bound")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError) as error:
        raise QualificationFreezeError(
            "qualification freeze input is invalid strict JSON"
        ) from error
    if not isinstance(value, dict):
        raise QualificationFreezeError("qualification freeze input must be an object")
    return value, raw


def freeze_candidate(
    *,
    template_path: Path,
    reproducible_report_path: Path,
    artifact_manifest_path: Path,
) -> dict[str, Any]:
    template, _template_raw = _load(template_path)
    report, _report_raw = _load(reproducible_report_path)
    manifest, manifest_raw = _load(artifact_manifest_path)
    schema, _schema_raw = _load(SCHEMA)
    Draft202012Validator.check_schema(schema)
    errors = list(Draft202012Validator(schema).iter_errors(template))
    if errors or template.get("status") != "construction_candidate":
        raise QualificationFreezeError("active qualification template is invalid")
    if template.get("candidate_version") != "0.13.0":
        raise QualificationFreezeError("release_version_binding_deadlock")
    if any(value is None for value in template["external_inputs"].values()):
        raise QualificationFreezeError("blocked_external_qualification_input")
    if report.get("schema_version") != "deeplaw.reproducible-build-report/v2":
        raise QualificationFreezeError("reproducible build report is unsupported")
    verify_record_digest(report, field="reproducible build report")
    binding = report.get("binding")
    if (
        not isinstance(binding, dict)
        or binding.get("worktree_clean") is not True
        or binding.get("package_version") != "0.13.0"
        or report.get("artifact_release_eligible") is not True
        or report.get("reproducible") is not True
        or report.get("lock_sha256") != binding.get("lock_sha256")
    ):
        raise QualificationFreezeError("reproducible candidate is not exact and clean")
    expected_manifest = {
        "schema_version": "deeplaw.retained-candidate-artifacts/v1",
        "package_version": "0.13.0",
        "release_ready": False,
        "claim_eligible": False,
        "git_commit": binding["commit"],
        "git_tree": binding["tree"],
        "lock_sha256": binding["lock_sha256"],
    }
    if any(manifest.get(key) != value for key, value in expected_manifest.items()):
        raise QualificationFreezeError("artifact manifest differs from the clean candidate")
    artifacts = {item["name"]: item for item in report.get("artifacts", [])}
    if len(artifacts) != 2:
        raise QualificationFreezeError("reproducible build must contain one wheel and sdist")
    wheel = manifest.get("wheel")
    sdist = manifest.get("sdist")
    if not isinstance(wheel, dict) or not isinstance(sdist, dict):
        raise QualificationFreezeError("artifact manifest is incomplete")
    for item in (wheel, sdist):
        report_item = artifacts.get(item.get("filename"))
        if (
            not isinstance(report_item, dict)
            or item.get("sha256") != report_item.get("sha256")
            or item.get("bytes") != report_item.get("byte_size")
        ):
            raise QualificationFreezeError("artifact manifest hash differs from verified bytes")
    frozen = {
        **template,
        "status": "frozen_exact_candidate",
        "candidate_binding": {
            "source_commit": binding["commit"],
            "source_tree": binding["tree"],
            "lock_sha256": binding["lock_sha256"],
            "wheel_filename": wheel["filename"],
            "wheel_sha256": wheel["sha256"],
            "sdist_filename": sdist["filename"],
            "sdist_sha256": sdist["sha256"],
            "artifact_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
            "source_date_epoch": report["source_date_epoch"],
        },
        "blocker": None,
    }
    errors = sorted(
        Draft202012Validator(schema).iter_errors(frozen),
        key=lambda error: list(error.path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        raise QualificationFreezeError(
            f"frozen qualification schema violation at {location}: {errors[0].message}"
        )
    return frozen


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--reproducible-report", type=Path, required=True)
    parser.add_argument("--artifact-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = freeze_candidate(
        template_path=args.template,
        reproducible_report_path=args.reproducible_report,
        artifact_manifest_path=args.artifact_manifest,
    )
    args.output.write_text(canonical_json(value) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
