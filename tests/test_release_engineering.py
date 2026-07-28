from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release.evidence import verify_record_digest
from benchmarks.release.inventory_licenses import _reviewed_exception_matches, inventory
from benchmarks.release.run_distribution_lifecycle import _isolated_environment
from benchmarks.release.verify_reproducible_build import (
    DEFAULT_SOURCE_DATE_EPOCH,
    _verify_build_inputs,
    archive_inventory,
    verify,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def test_distribution_lifecycle_uses_an_explicit_isolated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "lifecycle-home"

    environment = _isolated_environment(home)

    assert home.is_dir()
    assert environment["HOME"] == str(home.absolute())
    assert environment["USERPROFILE"] == str(home.absolute())
    assert "PYTHONUTF8" in environment
    assert "PATH" in environment


def test_license_inventory_has_no_unreviewed_installed_distribution() -> None:
    report = inventory(
        policy_path=REPOSITORY / "benchmarks" / "release" / "license-policy-v1.json",
        notices_path=REPOSITORY / "THIRD_PARTY_NOTICES.md",
    )

    assert report["status"] == "passed"
    assert report["blocked"] == []
    assert report["review_required"] == []
    assert report["package_count"] == len(report["packages"])


def test_reviewed_license_exception_requires_exact_empty_metadata_and_notice() -> None:
    exception = {
        "version": "13.0.3.0",
        "license_evidence": None,
        "notice_marker": "Optional Linux CUDA Runtime Dependencies",
    }
    notices = "## Optional Linux CUDA Runtime Dependencies\n"

    assert _reviewed_exception_matches(
        exception, version="13.0.3.0", evidence="", notices=notices
    )
    assert not _reviewed_exception_matches(
        exception,
        version="13.0.3.0",
        evidence="LicenseRef-NVIDIA-Proprietary",
        notices=notices,
    )
    assert not _reviewed_exception_matches(
        exception, version="13.0.4.0", evidence="", notices=notices
    )


def test_distribution_inventory_rejects_parent_path(tmp_path: Path) -> None:
    artifact = tmp_path / "unsafe.whl"
    with zipfile.ZipFile(artifact, "w") as archive:
        archive.writestr("../outside.py", "unsafe")

    with pytest.raises(RuntimeError, match="unsafe path"):
        archive_inventory(artifact)


def test_reproducible_builder_publishes_the_exact_verified_artifacts(
    tmp_path: Path,
) -> None:
    artifact_directory = tmp_path / "verified-dist"

    report = verify(
        REPOSITORY,
        source_date_epoch=DEFAULT_SOURCE_DATE_EPOCH,
        artifact_directory=artifact_directory,
    )

    schema = json.loads(
        (REPOSITORY / "contracts" / "reproducible-build-report.v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(report)
    verify_record_digest(report, field="reproducible build report")
    assert report["binding"]["commit"] == report["repository_commit"]
    assert report["binding"]["lock_sha256"] == report["lock_sha256"]
    assert report["binding"]["contracts"]["count"] > 0
    assert report["environment"]["platform_system"]
    constraints = REPOSITORY / "benchmarks" / "release" / "build-constraints.txt"
    assert report["build_constraints_sha256"] == hashlib.sha256(
        constraints.read_bytes()
    ).hexdigest()
    assert report["lock_sha256"] == hashlib.sha256(
        (REPOSITORY / "uv.lock").read_bytes()
    ).hexdigest()
    assert report["build_dependencies"] == {
        "hatchling": "1.31.0",
        "packaging": "26.2",
        "pathspec": "1.1.1",
        "pluggy": "1.6.0",
        "trove-classifiers": "2026.6.1.19",
    }
    published = sorted(artifact_directory.iterdir(), key=lambda item: item.name)
    assert [item.name for item in published] == sorted(
        artifact["name"] for artifact in report["artifacts"]
    )
    expected = {artifact["name"]: artifact for artifact in report["artifacts"]}
    for artifact in published:
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected[artifact.name][
            "sha256"
        ]
        assert artifact.stat().st_size == expected[artifact.name]["byte_size"]


def test_reproducible_builder_refuses_a_nonempty_publish_directory(
    tmp_path: Path,
) -> None:
    artifact_directory = tmp_path / "verified-dist"
    artifact_directory.mkdir()
    (artifact_directory / "stale.whl").write_bytes(b"stale")

    with pytest.raises(RuntimeError, match="must be empty"):
        verify(
            REPOSITORY,
            source_date_epoch=DEFAULT_SOURCE_DATE_EPOCH,
            artifact_directory=artifact_directory,
        )


def test_reproducible_builder_rejects_constraint_hash_drift(tmp_path: Path) -> None:
    (tmp_path / "benchmarks" / "release").mkdir(parents=True)
    for relative in (
        Path("pyproject.toml"),
        Path("uv.lock"),
        Path("benchmarks/release/build-constraints.txt"),
    ):
        target = tmp_path / relative
        target.write_bytes((REPOSITORY / relative).read_bytes())
    constraints = tmp_path / "benchmarks" / "release" / "build-constraints.txt"
    constraints.write_text(
        constraints.read_text(encoding="utf-8").replace(
            "aac80bec8b6fe35e8480f1c335be8910fa210a0e6f735a139be205dadcacb544",
            "0" * 64,
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="hashes differ from the lock"):
        _verify_build_inputs(tmp_path)


def test_checked_in_actual_pdf_diagnostic_is_bound_to_current_engine() -> None:
    report_path = (
        REPOSITORY
        / "benchmarks"
        / "release"
        / "document-engine-actual-pdf-2026-07-28.json"
    )
    schema_path = (
        REPOSITORY
        / "contracts"
        / "document-engine-actual-pdf-diagnostic.v1.schema.json"
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)

    assert report["claim_eligible"] is False
    assert report["candidate"]["worktree_dirty"] is False
    assert report["models"]["verified"] is True
    assert report["models"]["network_during_ingest"] is False
    assert report["result"]["success"] is True
    assert report["result"]["expected_text_observed"] is True
    for relative_path, expected_sha256 in report["candidate"][
        "implementation_files"
    ].items():
        actual_sha256 = hashlib.sha256((REPOSITORY / relative_path).read_bytes()).hexdigest()
        assert actual_sha256 == expected_sha256
