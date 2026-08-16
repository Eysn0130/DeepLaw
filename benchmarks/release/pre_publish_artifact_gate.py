from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.evidence import canonical_json, sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[2]
CONTRACTS = REPOSITORY / "contracts"


class PrePublishArtifactGateError(ValueError):
    pass


def _fail(message: str) -> None:
    raise PrePublishArtifactGateError(message)


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in items:
        if key in value:
            _fail("strict JSON contains a duplicate key")
        value[key] = item
    return value


def _constant(value: str) -> Any:
    _fail("strict JSON contains a non-finite number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _fail("strict JSON contains a non-finite number")
    return number


def _regular(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        _fail(f"{label} must be a regular non-symlink file")
    raw = path.read_bytes()
    if not raw:
        _fail(f"{label} is empty")
    return raw


def _json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _regular(path, label=label)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise PrePublishArtifactGateError(f"{label} must be strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value, raw


def _schema(value: Mapping[str, Any], name: str, *, label: str) -> None:
    schema, _ = _json(CONTRACTS / name, label=f"{label} contract")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: list(error.path),
    )
    if errors:
        _fail(f"{label} violates its contract: {errors[0].message}")


def _record(value: Mapping[str, Any], field: str) -> None:
    declared = value.get(field)
    body = {key: item for key, item in value.items() if key != field}
    observed = sha256_bytes(canonical_json(body).encode("utf-8"))
    if declared != observed:
        _fail(f"{field} differs from canonical bytes")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _relative(root: Path, path: Path, *, label: str) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError) as error:
        raise PrePublishArtifactGateError(f"{label} is outside the artifact root") from error
    parsed = PurePosixPath(relative)
    if not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail(f"{label} path is unsafe")
    return relative


def _artifact_rows(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = report.get("artifacts")
    if not isinstance(rows, list) or len(rows) != 2:
        _fail("reproducible build report must contain wheel and sdist")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("name"), str):
            _fail("reproducible build artifact row is invalid")
        name = row["name"]
        role = "wheel" if name.endswith(".whl") else "sdist" if name.endswith(".tar.gz") else None
        if role is None or role in result:
            _fail("reproducible build artifact roles are invalid")
        result[role] = row
    if set(result) != {"wheel", "sdist"}:
        _fail("reproducible build artifact roles are incomplete")
    return result


def build_receipt(
    *,
    artifact_root: Path,
    reproducible_report_path: Path,
    retained_manifest_path: Path,
    sbom_path: Path,
    openvex_path: Path,
    licenses_path: Path,
    created_at_epoch: int,
) -> dict[str, Any]:
    root = artifact_root.expanduser().resolve(strict=True)
    if artifact_root.is_symlink() or not root.is_dir():
        _fail("artifact root must be a regular non-symlink directory")
    report, report_raw = _json(reproducible_report_path, label="reproducible build report")
    retained, retained_raw = _json(retained_manifest_path, label="retained artifact manifest")
    sbom, sbom_raw = _json(sbom_path, label="CycloneDX SBOM")
    openvex, openvex_raw = _json(openvex_path, label="OpenVEX")
    licenses, licenses_raw = _json(licenses_path, label="installed license inventory")
    _schema(report, "reproducible-build-report.v2.schema.json", label="reproducible build")
    _schema(retained, "retained-candidate-artifacts.v1.schema.json", label="retained artifact")
    _schema(licenses, "installed-license-inventory.v1.schema.json", label="license inventory")
    _record(report, "record_sha256")
    _record(licenses, "record_sha256")

    binding = report.get("binding")
    if not isinstance(binding, Mapping):
        _fail("reproducible build binding is missing")
    candidate = {
        "commit": retained["git_commit"],
        "tree": retained["git_tree"],
        "lock_sha256": retained["lock_sha256"],
    }
    if (
        report.get("repository_commit") != candidate["commit"]
        or report.get("lock_sha256") != candidate["lock_sha256"]
        or binding.get("commit") != candidate["commit"]
        or binding.get("tree") != candidate["tree"]
        or binding.get("lock_sha256") != candidate["lock_sha256"]
        or binding.get("package_version") != retained["package_version"]
        or binding.get("worktree_clean") is not True
        or report.get("working_tree_dirty") is not False
        or report.get("reproducible") is not True
        or report.get("package_inventory_verified") is not True
        or report.get("artifact_release_eligible") is not True
        or report.get("artifact_release_blockers") != []
    ):
        _fail("reproducible build is not a clean exact candidate")

    report_artifacts = _artifact_rows(report)
    retained_receipt: dict[str, Any] = {
        "manifest_sha256": _sha(retained_raw),
        "manifest_path": _relative(root, retained_manifest_path, label="retained manifest"),
    }
    for role in ("wheel", "sdist"):
        retained_row = retained[role]
        report_row = report_artifacts[role]
        path = root / retained_row["filename"]
        raw = _regular(path, label=f"retained {role}")
        if (
            retained_row["sha256"] != _sha(raw)
            or retained_row["bytes"] != len(raw)
            or report_row.get("name") != retained_row["filename"]
            or report_row.get("sha256") != retained_row["sha256"]
            or report_row.get("byte_size") != retained_row["bytes"]
        ):
            _fail(f"retained {role} differs from reproducible build bytes")
        retained_receipt[role] = {
            "name": retained_row["filename"],
            "sha256": retained_row["sha256"],
            "byte_size": retained_row["bytes"],
            "retained_path": _relative(root, path, label=f"retained {role}"),
        }

    version = retained["package_version"]
    metadata = sbom.get("metadata")
    component = metadata.get("component") if isinstance(metadata, Mapping) else None
    if (
        sbom.get("bomFormat") != "CycloneDX"
        or sbom.get("specVersion") not in {"1.5", "1.6"}
        or not isinstance(component, Mapping)
        or component.get("name") != "deeplaw"
        or component.get("version") != version
        or not isinstance(sbom.get("components"), list)
        or not sbom["components"]
    ):
        _fail("CycloneDX SBOM is not bound to the candidate")
    expected_product = f"pkg:pypi/deeplaw@{version}"
    statements = openvex.get("statements")
    if openvex.get("@context") != "https://openvex.dev/ns/v0.2.0" or not isinstance(
        statements, list
    ) or not statements:
        _fail("OpenVEX is unavailable or unsupported")
    for statement in statements:
        products = statement.get("products") if isinstance(statement, Mapping) else None
        if not isinstance(products, list) or expected_product not in {
            item.get("@id") for item in products if isinstance(item, Mapping)
        }:
            _fail("OpenVEX statement is not bound to the candidate version")
    license_binding = licenses.get("binding")
    if (
        licenses.get("status") != "passed"
        or licenses.get("blocked") != []
        or licenses.get("review_required") != []
        or not isinstance(license_binding, Mapping)
        or license_binding.get("commit") != candidate["commit"]
        or license_binding.get("tree") != candidate["tree"]
        or license_binding.get("lock_sha256") != candidate["lock_sha256"]
        or license_binding.get("package_version") != version
        or license_binding.get("worktree_clean") is not True
    ):
        _fail("installed license inventory is not bound to the candidate")

    if created_at_epoch < 315532800:
        _fail("created_at epoch is outside the supported range")
    created_at = datetime.fromtimestamp(created_at_epoch, tz=UTC).isoformat().replace(
        "+00:00", "Z"
    )
    report_sha = _sha(report_raw)
    receipt: dict[str, Any] = {
        "schema_version": "deeplaw.pre-publish-artifact-gate/v1",
        "status": "pre_publish_passed",
        "created_at": created_at,
        "candidate": candidate,
        "builds": {
            "count": 2,
            "byte_identical": True,
            "first": {
                "build_id": "first",
                "wheel_sha256": retained["wheel"]["sha256"],
                "sdist_sha256": retained["sdist"]["sha256"],
                "receipt_sha256": report_sha,
            },
            "second": {
                "build_id": "second",
                "wheel_sha256": retained["wheel"]["sha256"],
                "sdist_sha256": retained["sdist"]["sha256"],
                "receipt_sha256": report_sha,
            },
        },
        "retained_artifacts": retained_receipt,
        "sbom": {
            "format": "cyclonedx-json",
            "sha256": _sha(sbom_raw),
            "path": _relative(root, sbom_path, label="SBOM"),
            "verified": True,
        },
        "openvex": {
            "format": "openvex-json",
            "sha256": _sha(openvex_raw),
            "path": _relative(root, openvex_path, label="OpenVEX"),
            "verified": True,
        },
        "licenses": {
            "format": "license-report-json",
            "sha256": _sha(licenses_raw),
            "path": _relative(root, licenses_path, label="licenses"),
            "verified": True,
        },
        "provenance": {
            "format": "deeplaw-reproducible-build-report-v2",
            "sha256": report_sha,
            "path": _relative(root, reproducible_report_path, label="provenance"),
            "verified": True,
        },
    }
    receipt["record_sha256"] = sha256_bytes(canonical_json(receipt).encode("utf-8"))
    _schema(receipt, "pre-publish-artifact-gate.v1.schema.json", label="pre-publish receipt")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Derive the pre-publish artifact receipt.")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--reproducible-report", type=Path, required=True)
    parser.add_argument("--retained-manifest", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--openvex", type=Path, required=True)
    parser.add_argument("--licenses", type=Path, required=True)
    parser.add_argument("--created-at-epoch", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = build_receipt(
            artifact_root=args.artifact_root,
            reproducible_report_path=args.reproducible_report,
            retained_manifest_path=args.retained_manifest,
            sbom_path=args.sbom,
            openvex_path=args.openvex,
            licenses_path=args.licenses,
            created_at_epoch=args.created_at_epoch,
        )
        if args.output.exists() or args.output.is_symlink():
            _fail("pre-publish receipt output must be new")
        args.output.write_text(
            json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
    except (OSError, PrePublishArtifactGateError, ValueError) as error:
        print(f"pre-publish artifact gate rejected: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
