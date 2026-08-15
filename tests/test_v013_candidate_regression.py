from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from benchmarks.release.candidate_regression import (
    AGGREGATE_SCHEMA,
    RECEIPT_SCHEMA,
    aggregate_shard_receipts,
    build_regression_receipt,
    build_shard_manifest,
    main,
)
from deeplaw.util import canonical_json

REPOSITORY = Path(__file__).resolve().parents[1]


def _receipt(shard: dict[str, object], *, tests: int) -> dict[str, object]:
    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "current_source_regression",
        "release_ready": False,
        "environment": {
            "matrix_os": "windows-latest",
            "matrix_python": "3.12",
            "python_version": "fixture",
        },
        "test_manifest": {
            "schema_version": "fixture/v1",
            "manifest_sha256": "a" * 64,
        },
        "junit": {"tests": tests, "failures": 0, "errors": 0, "skipped": 0},
        "qualification": {"status": "not_executed", "skipped": 0},
        "nonapplicable": {"status": "nonapplicable", "skipped": 0},
        "historical_compatibility": {"status": "not_executed", "skipped": 0},
        "shard": {
            key: shard[key]
            for key in (
                "shard_count",
                "shard_index",
                "all_test_file_count",
                "all_test_files_sha256",
                "selected_test_file_count",
                "selected_test_files_sha256",
            )
        },
    }


def test_candidate_windows_shards_are_deterministic_complete_and_disjoint() -> None:
    shards = [
        build_shard_manifest(repository=REPOSITORY, shard_count=3, shard_index=index)
        for index in range(1, 4)
    ]
    selected = [path for shard in shards for path in shard["selected_test_files"]]
    expected = sorted(
        path.relative_to(REPOSITORY).as_posix()
        for path in (REPOSITORY / "tests").glob("test_*.py")
        if path.is_file() and not path.is_symlink()
    )

    assert sorted(selected) == expected
    assert len(selected) == len(set(selected))
    assert {shard["all_test_files_sha256"] for shard in shards} == {
        shards[0]["all_test_files_sha256"]
    }


def test_candidate_shard_cli_writes_lf_only_paths(tmp_path: Path) -> None:
    manifest_path = tmp_path / "shard.json"
    paths_path = tmp_path / "paths.txt"

    assert (
        main(
            [
                "--repository",
                str(REPOSITORY),
                "select",
                "--shard-count",
                "3",
                "--shard-index",
                "1",
                "--output",
                str(manifest_path),
                "--paths-output",
                str(paths_path),
            ]
        )
        == 0
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    encoded = paths_path.read_bytes()

    assert b"\r" not in encoded
    assert encoded.endswith(b"\n")
    assert encoded.decode("utf-8").splitlines() == manifest["selected_test_files"]


def test_candidate_regression_receipt_parses_junit_and_binds_python(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "candidate-tests.xml"
    junit.write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.fixture" name="test_pass" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    receipt = build_regression_receipt(
        repository=REPOSITORY,
        junit_path=junit,
        matrix_os="ubuntu-latest",
        matrix_python=f"{sys.version_info.major}.{sys.version_info.minor}",
    )

    assert receipt["schema_version"] == RECEIPT_SCHEMA
    assert receipt["junit"] == {
        "tests": 1,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    assert receipt["release_ready"] is False


def test_candidate_windows_shard_aggregate_rejects_drift(
    tmp_path: Path,
) -> None:
    for index in range(1, 4):
        directory = tmp_path / f"shard-{index}"
        directory.mkdir()
        shard = build_shard_manifest(
            repository=REPOSITORY,
            shard_count=3,
            shard_index=index,
        )
        (directory / "candidate-test-shard.json").write_text(
            canonical_json(shard) + "\n",
            encoding="utf-8",
        )
        (directory / "candidate-skip-receipt.json").write_text(
            canonical_json(_receipt(shard, tests=index)) + "\n",
            encoding="utf-8",
        )

    aggregate = aggregate_shard_receipts(
        repository=REPOSITORY,
        input_directory=tmp_path,
        matrix_python="3.12",
    )
    assert aggregate["schema_version"] == AGGREGATE_SCHEMA
    assert aggregate["junit"] == {
        "tests": 6,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    assert aggregate["shards"]["complete"] is True
    assert aggregate["shards"]["overlap_absent"] is True

    selected = tmp_path / "shard-2/candidate-test-shard.json"
    drifted = json.loads(selected.read_text(encoding="utf-8"))
    drifted["selected_test_files"] = drifted["selected_test_files"][1:]
    selected.write_text(canonical_json(drifted) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not match the source tree"):
        aggregate_shard_receipts(
            repository=REPOSITORY,
            input_directory=tmp_path,
            matrix_python="3.12",
        )
