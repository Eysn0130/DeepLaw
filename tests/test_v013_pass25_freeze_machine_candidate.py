from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import benchmarks.release.freeze_qualification_candidate_v2 as freeze

REPOSITORY = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40
TREE = "2" * 40
LOCK = "3" * 64
EXTERNAL = "4" * 64


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


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
        (REPOSITORY / "benchmarks/v013/active-qualification-v2.json").read_text(
            encoding="utf-8"
        )
    )
    template["status"] = "construction_candidate_machine_evaluation_pending"
    template["candidate_version"] = "0.13.0"
    template["blocker"] = "candidate_artifact_not_built"
    template["candidate_binding"]["package_version"] = "0.13.0"
    template["candidate_binding"]["lock_sha256"] = LOCK
    protocol = REPOSITORY / "benchmarks/v013/qualification-protocol-v2.json"
    template["protocol_binding"]["sha256"] = hashlib.sha256(protocol.read_bytes()).hexdigest()
    template["external_inputs"] = {
        key: EXTERNAL for key in template["external_inputs"]
    }
    return template


def _seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setattr(
        freeze,
        "ACTIVE_SCHEMA",
        REPOSITORY / "contracts/v013-active-qualification.v2.schema.json",
    )

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
        "wheel": {"filename": wheel.name, "sha256": wheel_sha, "bytes": wheel.stat().st_size},
        "sdist": {"filename": sdist.name, "sha256": sdist_sha, "bytes": sdist.stat().st_size},
    }
    paths = {
        "template": tmp_path / "active-qualification-v2.json",
        "report": tmp_path / "reproducible-build.json",
        "manifest": tmp_path / "retained-artifact-manifest.json",
        "output": tmp_path / "frozen-active-qualification.json",
    }
    _write(paths["template"], _construction_template())
    _write(paths["report"], report)
    _write(paths["manifest"], manifest)
    return paths


def test_freeze_binds_exact_candidate_and_preserves_machine_pending_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed(tmp_path, monkeypatch)

    frozen = freeze.freeze_candidate(
        template_path=paths["template"],
        reproducible_report_path=paths["report"],
        artifact_manifest_path=paths["manifest"],
    )

    assert frozen["status"] == "frozen_exact_candidate_machine_evaluation_pending"
    assert frozen["candidate_version"] == "0.13.0"
    assert frozen["candidate_binding"]["source_commit"] == COMMIT
    assert frozen["candidate_binding"]["source_tree"] == TREE
    assert frozen["candidate_binding"]["lock_sha256"] == LOCK
    assert frozen["candidate_binding"]["wheel_filename"] == "deeplaw-0.13.0-py3-none-any.whl"
    assert frozen["candidate_binding"]["sdist_filename"] == "deeplaw-0.13.0.tar.gz"
    assert frozen["release_ready"] is False
    assert frozen["claim_eligible"] is False


def test_freeze_rejects_the_pending_012_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed(tmp_path, monkeypatch)
    pending = json.loads(
        (REPOSITORY / "benchmarks/v013/active-qualification-v2.json").read_text(
            encoding="utf-8"
        )
    )
    _write(paths["template"], pending)

    with pytest.raises(freeze.QualificationFreezeV2Error, match="construction state"):
        freeze.freeze_candidate(
            template_path=paths["template"],
            reproducible_report_path=paths["report"],
            artifact_manifest_path=paths["manifest"],
        )


@pytest.mark.parametrize("field", ["commit", "tree", "lock_sha256"])
def test_freeze_rejects_cross_receipt_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    paths = _seed(tmp_path, monkeypatch)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["binding"][field] = "f" * (40 if field in {"commit", "tree"} else 64)
    report["record_sha256"] = hashlib.sha256(
        _canonical({key: value for key, value in report.items() if key != "record_sha256"})
    ).hexdigest()
    _write(paths["report"], report)

    with pytest.raises(freeze.QualificationFreezeV2Error):
        freeze.freeze_candidate(
            template_path=paths["template"],
            reproducible_report_path=paths["report"],
            artifact_manifest_path=paths["manifest"],
        )


def test_freeze_rejects_tampered_retained_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed(tmp_path, monkeypatch)
    (tmp_path / "deeplaw-0.13.0-py3-none-any.whl").write_bytes(b"tampered-wheel")

    with pytest.raises(freeze.QualificationFreezeV2Error, match="wheel bytes"):
        freeze.freeze_candidate(
            template_path=paths["template"],
            reproducible_report_path=paths["report"],
            artifact_manifest_path=paths["manifest"],
        )


def test_freeze_rejects_duplicate_json_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _seed(tmp_path, monkeypatch)
    paths["template"].write_text(
        '{"schema_version":"x","schema_version":"y"}\n', encoding="utf-8"
    )

    with pytest.raises(freeze.QualificationFreezeV2Error, match="duplicate"):
        freeze.freeze_candidate(
            template_path=paths["template"],
            reproducible_report_path=paths["report"],
            artifact_manifest_path=paths["manifest"],
        )


def test_candidate_full_calls_v2_freezer_before_retaining_active_qualification() -> None:
    workflow = (REPOSITORY / ".github/workflows/candidate-full.yml").read_text(encoding="utf-8")

    assert "benchmarks.release.freeze_qualification_candidate_v2" in workflow
    assert "--template benchmarks/v013/active-qualification-v2.json" in workflow
    assert "--reproducible-report" in workflow
    assert "--artifact-manifest" in workflow
    assert "frozen-active-qualification.json" in workflow
    assert "cp benchmarks/v013/active-qualification-v2.json" not in workflow
