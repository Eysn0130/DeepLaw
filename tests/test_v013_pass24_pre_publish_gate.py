from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.release.evidence import canonical_json, sha256_bytes
from benchmarks.release.pre_publish_artifact_gate import (
    PrePublishArtifactGateError,
    build_receipt,
)

COMMIT = "1" * 40
TREE = "2" * 40
LOCK = "3" * 64


def _write(path: Path, value: Any) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    path.write_bytes(raw)
    return raw


def _record(value: dict[str, Any]) -> dict[str, Any]:
    value["record_sha256"] = sha256_bytes(canonical_json(value).encode("utf-8"))
    return value


def _seed(tmp_path: Path) -> dict[str, Path]:
    wheel = tmp_path / "deeplaw-0.13.0-py3-none-any.whl"
    sdist = tmp_path / "deeplaw-0.13.0.tar.gz"
    wheel.write_bytes(b"wheel-bytes")
    sdist.write_bytes(b"sdist-bytes")
    artifacts = []
    for path in (wheel, sdist):
        artifacts.append(
            {
                "name": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "byte_size": path.stat().st_size,
                "path_count": 1,
                "inventory_sha256": "4" * 64,
            }
        )
    binding = {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": LOCK,
        "package_version": "0.13.0",
        "worktree_clean": True,
    }
    report = _record(
        {
            "schema_version": "deeplaw.reproducible-build-report/v2",
            "binding": binding,
            "environment": {},
            "repository_commit": COMMIT,
            "working_tree_dirty": False,
            "source_date_epoch": 946684800,
            "build_constraints_sha256": "5" * 64,
            "lock_sha256": LOCK,
            "build_dependencies": {
                "hatchling": "1.31.0",
                "packaging": "26.2",
                "pathspec": "1.1.1",
                "pluggy": "1.6.0",
                "trove-classifiers": "2026.6.1.19",
            },
            "reproducible": True,
            "package_inventory_verified": True,
            "artifacts": artifacts,
            "artifact_release_eligible": True,
            "artifact_release_blockers": [],
        }
    )
    retained = {
        "schema_version": "deeplaw.retained-candidate-artifacts/v1",
        "package_version": "0.13.0",
        "release_ready": False,
        "claim_eligible": False,
        "git_commit": COMMIT,
        "git_tree": TREE,
        "lock_sha256": LOCK,
        "wheel": {
            "filename": wheel.name,
            "sha256": artifacts[0]["sha256"],
            "bytes": wheel.stat().st_size,
        },
        "sdist": {
            "filename": sdist.name,
            "sha256": artifacts[1]["sha256"],
            "bytes": sdist.stat().st_size,
        },
    }
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "metadata": {"component": {"name": "deeplaw", "version": "0.13.0"}},
        "components": [{"name": "dependency"}],
    }
    openvex = {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "statements": [{"products": [{"@id": "pkg:pypi/deeplaw@0.13.0"}]}],
    }
    licenses = _record(
        {
            "schema_version": "deeplaw.installed-license-inventory/v1",
            "policy_schema_version": "deeplaw.release-license-policy/v1",
            "package_count": 1,
            "status": "passed",
            "blocked": [],
            "review_required": [],
            "binding": binding,
            "environment": {},
            "packages": [
                {
                    "name": "deeplaw",
                    "normalized_name": "deeplaw",
                    "version": "0.13.0",
                    "license_expression": "Apache-2.0",
                    "declared_license": None,
                    "license_classifiers": [],
                    "status": "approved",
                    "reason": "approved_license_marker",
                }
            ],
        }
    )
    paths = {
        "report": tmp_path / "reproducible-build.json",
        "retained": tmp_path / "retained-artifact-manifest.json",
        "sbom": tmp_path / "deeplaw.cdx.json",
        "openvex": tmp_path / "openvex.json",
        "licenses": tmp_path / "installed-licenses.json",
    }
    for name, value in (
        ("report", report),
        ("retained", retained),
        ("sbom", sbom),
        ("openvex", openvex),
        ("licenses", licenses),
    ):
        _write(paths[name], value)
    return paths


def _build(tmp_path: Path, paths: dict[str, Path]) -> dict[str, Any]:
    return build_receipt(
        artifact_root=tmp_path,
        reproducible_report_path=paths["report"],
        retained_manifest_path=paths["retained"],
        sbom_path=paths["sbom"],
        openvex_path=paths["openvex"],
        licenses_path=paths["licenses"],
        created_at_epoch=946684800,
    )


def test_pre_publish_receipt_is_derived_from_exact_retained_bytes(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    receipt = _build(tmp_path, paths)

    assert receipt["status"] == "pre_publish_passed"
    assert receipt["candidate"] == {"commit": COMMIT, "tree": TREE, "lock_sha256": LOCK}
    assert receipt["builds"]["first"]["wheel_sha256"] == receipt["builds"]["second"][
        "wheel_sha256"
    ]
    assert receipt["provenance"]["path"] == "reproducible-build.json"
    expected_record = copy.deepcopy(receipt)
    del expected_record["record_sha256"]
    assert receipt["record_sha256"] == sha256_bytes(
        canonical_json(expected_record).encode("utf-8")
    )


def test_pre_publish_rejects_retained_bytes_changed_after_build(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    (tmp_path / "deeplaw-0.13.0-py3-none-any.whl").write_bytes(b"changed")

    with pytest.raises(PrePublishArtifactGateError, match="retained wheel"):
        _build(tmp_path, paths)


def test_pre_publish_rejects_openvex_for_another_candidate(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    value = json.loads(paths["openvex"].read_text(encoding="utf-8"))
    value["statements"][0]["products"][0]["@id"] = "pkg:pypi/deeplaw@0.12.0"
    _write(paths["openvex"], value)

    with pytest.raises(PrePublishArtifactGateError, match="candidate version"):
        _build(tmp_path, paths)


def test_pre_publish_rejects_self_claimed_license_pass_with_wrong_binding(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    value = json.loads(paths["licenses"].read_text(encoding="utf-8"))
    value["binding"]["commit"] = "9" * 40
    del value["record_sha256"]
    _record(value)
    _write(paths["licenses"], value)

    with pytest.raises(PrePublishArtifactGateError, match="not bound"):
        _build(tmp_path, paths)
