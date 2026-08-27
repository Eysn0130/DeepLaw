from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import benchmarks.release.freeze_qualification_candidate_v3 as freeze

REPOSITORY = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
TREE = "2" * 40
LOCK = "3" * 64


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _record(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["record_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return result


def _write(path: Path, value: Any) -> bytes:
    raw = _canonical(value) + b"\n"
    path.write_bytes(raw)
    return raw


def _construction_template() -> dict[str, Any]:
    template = json.loads(
        (REPOSITORY / "benchmarks/v013/active-qualification-v3.json").read_text(
            encoding="utf-8"
        )
    )
    template["status"] = freeze.CONSTRUCTION_STATUS
    template["candidate_version"] = "0.13.0"
    template["blocker"] = "candidate_artifact_not_built"
    template["candidate_binding"]["package_version"] = "0.13.0"
    template["candidate_binding"]["lock_sha256"] = LOCK
    return template


def _seed(tmp_path: Path) -> dict[str, Path]:
    wheel = tmp_path / "deeplaw-0.13.0-py3-none-any.whl"
    sdist = tmp_path / "deeplaw-0.13.0.tar.gz"
    wheel.write_bytes(b"exact-wheel")
    sdist.write_bytes(b"exact-sdist")
    wheel_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    sdist_sha = hashlib.sha256(sdist.read_bytes()).hexdigest()
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
            "repository_commit": COMMIT,
            "working_tree_dirty": False,
            "source_date_epoch": 946684800,
            "lock_sha256": LOCK,
            "reproducible": True,
            "package_inventory_verified": True,
            "artifact_release_eligible": True,
            "artifact_release_blockers": [],
            "artifacts": [
                {
                    "name": wheel.name,
                    "sha256": wheel_sha,
                    "byte_size": wheel.stat().st_size,
                },
                {
                    "name": sdist.name,
                    "sha256": sdist_sha,
                    "byte_size": sdist.stat().st_size,
                },
            ],
        }
    )
    manifest = {
        "schema_version": "deeplaw.retained-candidate-artifacts/v1",
        "package_version": "0.13.0",
        "release_ready": False,
        "claim_eligible": False,
        "git_commit": COMMIT,
        "git_tree": TREE,
        "lock_sha256": LOCK,
        "wheel": {
            "filename": wheel.name,
            "sha256": wheel_sha,
            "bytes": wheel.stat().st_size,
        },
        "sdist": {
            "filename": sdist.name,
            "sha256": sdist_sha,
            "bytes": sdist.stat().st_size,
        },
    }
    paths = {
        "template": tmp_path / "active-qualification-v3.json",
        "report": tmp_path / "reproducible-build.json",
        "manifest": tmp_path / "retained-artifact-manifest.json",
    }
    _write(paths["template"], _construction_template())
    _write(paths["report"], report)
    _write(paths["manifest"], manifest)
    return paths


def _freeze(paths: dict[str, Path]) -> dict[str, Any]:
    return freeze.freeze_candidate(
        template_path=paths["template"],
        reproducible_report_path=paths["report"],
        artifact_manifest_path=paths["manifest"],
    )


def test_freeze_binds_exact_candidate_and_keeps_all_claims_pending(
    tmp_path: Path,
) -> None:
    paths = _seed(tmp_path)
    frozen = _freeze(paths)

    assert frozen["status"] == freeze.FROZEN_STATUS
    assert frozen["candidate_version"] == "0.13.0"
    assert frozen["candidate_binding"]["source_commit"] == COMMIT
    assert frozen["candidate_binding"]["source_tree"] == TREE
    assert frozen["candidate_binding"]["lock_sha256"] == LOCK
    assert frozen["candidate_binding"]["wheel_filename"] == "deeplaw-0.13.0-py3-none-any.whl"
    assert frozen["candidate_binding"]["sdist_filename"] == "deeplaw-0.13.0.tar.gz"
    assert frozen["release_ready"] is False
    assert frozen["claim_eligible"] is False
    assert frozen["kernel_release_claim_eligible"] is False
    assert all(
        row["status"] == "not_executed"
        and row["passed"] is False
        and row["claim"] is False
        for section in ("core_statuses", "capability_claims", "competitive_claims")
        for row in frozen[section]
    )


def test_freeze_rejects_pending_012_template(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    pending = json.loads(paths["template"].read_text(encoding="utf-8"))
    pending["status"] = "machine_evaluation_pending"
    pending["candidate_version"] = "0.12.0"
    pending["candidate_binding"]["package_version"] = "0.12.0"
    pending["blocker"] = "machine_evaluation_not_executed"
    _write(paths["template"], pending)
    with pytest.raises(freeze.QualificationFreezeV3Error, match="construction"):
        _freeze(paths)


@pytest.mark.parametrize("field", ["binding", "artifact"])
def test_freeze_rejects_tampered_candidate_evidence(tmp_path: Path, field: str) -> None:
    paths = _seed(tmp_path)
    if field == "binding":
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
        report["binding"]["commit"] = "f" * 40
        report["repository_commit"] = "f" * 40
        report["record_sha256"] = hashlib.sha256(
            _canonical({key: value for key, value in report.items() if key != "record_sha256"})
        ).hexdigest()
        _write(paths["report"], report)
    else:
        (tmp_path / "deeplaw-0.13.0-py3-none-any.whl").write_bytes(b"tampered-wheel")
    with pytest.raises(freeze.QualificationFreezeV3Error):
        _freeze(paths)


@pytest.mark.parametrize("section", ["core_statuses", "capability_claims", "competitive_claims"])
def test_freeze_rejects_self_reported_passes(tmp_path: Path, section: str) -> None:
    paths = _seed(tmp_path)
    template = json.loads(paths["template"].read_text(encoding="utf-8"))
    template[section][0]["status"] = "passed"
    template[section][0]["passed"] = True
    _write(paths["template"], template)
    with pytest.raises(freeze.QualificationFreezeV3Error):
        _freeze(paths)


def test_freeze_rejects_non_null_optional_inputs(tmp_path: Path) -> None:
    paths = _seed(tmp_path)
    template = json.loads(paths["template"].read_text(encoding="utf-8"))
    template["external_inputs"]["final_blind_holdout_sha256"] = "a" * 64
    _write(paths["template"], template)
    with pytest.raises(freeze.QualificationFreezeV3Error, match="null"):
        _freeze(paths)


def test_v2_v8_history_bytes_are_not_touched(tmp_path: Path) -> None:
    historical = (
        REPOSITORY / "benchmarks/v013/qualification-protocol-v2.json",
        REPOSITORY / "benchmarks/v013/active-qualification-v2.json",
        REPOSITORY / "benchmarks/release/v013-gate-classification-v8.json",
    )
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in historical}
    paths = _seed(tmp_path)
    _freeze(paths)
    assert before == {
        path: hashlib.sha256(path.read_bytes()).hexdigest() for path in historical
    }
