from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from benchmarks.release.candidate_regression import (
    AGGREGATE_SCHEMA,
    RECEIPT_SCHEMA,
    aggregate_shard_receipts,
    build_platform_matrix_receipt,
    build_regression_receipt,
    build_shard_manifest,
    main,
    merge_shard_junit,
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
    assert {shard["algorithm"] for shard in shards} == {
        "longest_processing_time_source_bytes_v1"
    }
    assert {shard["source_file_sizes_sha256"] for shard in shards} == {
        shards[0]["source_file_sizes_sha256"]
    }


def test_candidate_windows_source_byte_lpt_handles_inserted_small_file(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    heavy_sizes = {
        "test_a.py": 90,
        "test_b.py": 80,
        "test_c.py": 70,
        "test_e.py": 60,
        "test_f.py": 50,
        "test_g.py": 40,
    }
    for name, size in heavy_sizes.items():
        (tests / name).write_bytes(b"x" * size)

    baseline_shards = [
        build_shard_manifest(repository=tmp_path, shard_count=3, shard_index=index)
        for index in range(1, 4)
    ]
    baseline_assignment = {
        path: index
        for index, shard in enumerate(baseline_shards)
        for path in shard["selected_test_files"]
    }

    (tests / "test_d.py").write_bytes(b"x")
    shards = [
        build_shard_manifest(repository=tmp_path, shard_count=3, shard_index=index)
        for index in range(1, 4)
    ]
    totals = [
        sum((tmp_path / path).stat().st_size for path in shard["selected_test_files"])
        for shard in shards
    ]
    assert totals == [131, 130, 130]
    assert {shard["algorithm"] for shard in shards} == {
        "longest_processing_time_source_bytes_v1"
    }
    assert all(
        baseline_assignment[path] == index
        for index, shard in enumerate(shards)
        for path in shard["selected_test_files"]
        if path != "tests/test_d.py"
    )

    all_files = sorted(
        path.relative_to(tmp_path).as_posix() for path in tests.glob("test_*.py")
    )
    round_robin_totals = [
        sum(
            (tmp_path / path).stat().st_size
            for offset, path in enumerate(all_files)
            if offset % 3 == index
        )
        for index in range(3)
    ]
    assert round_robin_totals != totals

    zero_repository = tmp_path / "zero-repository"
    zero_tests = zero_repository / "tests"
    zero_tests.mkdir(parents=True)
    for name in ("test_a.py", "test_b.py", "test_c.py"):
        (zero_tests / name).write_bytes(b"")
    zero_shards = [
        build_shard_manifest(
            repository=zero_repository,
            shard_count=3,
            shard_index=index,
        )
        for index in range(1, 4)
    ]
    assert [shard["selected_test_file_count"] for shard in zero_shards] == [1, 1, 1]
    assert len({shard["source_file_sizes_sha256"] for shard in zero_shards}) == 1


def test_candidate_source_size_digest_rebuilds_and_rejects_size_drift(
    tmp_path: Path,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    sizes = {
        "test_a.py": 90,
        "test_b.py": 80,
        "test_c.py": 70,
        "test_d.py": 1,
        "test_e.py": 60,
        "test_f.py": 50,
        "test_g.py": 40,
    }
    for name, size in sizes.items():
        (tests / name).write_bytes(b"x" * size)

    shards = [
        build_shard_manifest(repository=tmp_path, shard_count=3, shard_index=index)
        for index in range(1, 4)
    ]
    input_directory = tmp_path / "receipts"
    for index, shard in enumerate(shards, start=1):
        directory = input_directory / f"shard-{index}"
        directory.mkdir(parents=True)
        (directory / "candidate-test-shard.json").write_text(
            canonical_json(shard) + "\n",
            encoding="utf-8",
        )
        (directory / "candidate-skip-receipt.json").write_text(
            canonical_json(_receipt(shard, tests=index)) + "\n",
            encoding="utf-8",
        )

    original_digest = shards[0]["source_file_sizes_sha256"]
    (tests / "test_c.py").write_bytes(b"x" * 71)
    rebuilt = build_shard_manifest(
        repository=tmp_path,
        shard_count=3,
        shard_index=1,
    )
    assert rebuilt["source_file_sizes_sha256"] != original_digest
    with pytest.raises(RuntimeError, match="does not match the source tree"):
        aggregate_shard_receipts(
            repository=tmp_path,
            input_directory=input_directory,
            matrix_python="3.12",
        )


def test_candidate_windows_duration_weighted_shards_are_rebuildable() -> None:
    files = sorted(
        path.relative_to(REPOSITORY).as_posix()
        for path in (REPOSITORY / "tests").glob("test_*.py")
        if path.is_file() and not path.is_symlink()
    )
    weights = {path: float(index + 1) for index, path in enumerate(files)}
    shards = [
        build_shard_manifest(
            repository=REPOSITORY,
            shard_count=3,
            shard_index=index,
            duration_weights=weights,
        )
        for index in range(1, 4)
    ]
    selected = [path for shard in shards for path in shard["selected_test_files"]]
    assert sorted(selected) == files
    assert len(selected) == len(set(selected))
    assert {shard["algorithm"] for shard in shards} == {
        "longest_processing_time_duration_v1"
    }
    assert len({shard["duration_weights_sha256"] for shard in shards}) == 1
    assert all("source_file_sizes_sha256" not in shard for shard in shards)


def test_candidate_windows_duration_estimate_uses_cross_python_stable_sum() -> None:
    files = sorted(
        path.relative_to(REPOSITORY).as_posix()
        for path in (REPOSITORY / "tests").glob("test_*.py")
        if path.is_file() and not path.is_symlink()
    )
    weights = {path: 0.1 for path in files}
    shard = build_shard_manifest(
        repository=REPOSITORY,
        shard_count=3,
        shard_index=1,
        duration_weights=weights,
    )

    assert shard["selected_estimated_duration_seconds"] == math.fsum(
        weights[name] for name in shard["selected_test_files"]
    )


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
        (directory / "candidate-tests.xml").write_text(
            '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
            f'<testcase classname="tests.fixture_{index}" name="test_pass" />'
            "</testsuite></testsuites>",
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
    assert aggregate["shards"]["source_file_sizes_sha256"] == shard[
        "source_file_sizes_sha256"
    ]
    assert "duration_weights_sha256" not in aggregate["shards"]

    merged = tmp_path / "merged.xml"
    merge_shard_junit(
        input_directory=tmp_path,
        output=merged,
        matrix_python="3.12",
    )
    assert b'candidate-full-windows-3.12' in merged.read_bytes()
    assert merged.read_bytes().count(b"<testcase ") == 3

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


def test_candidate_platform_receipt_binds_exact_nine_raw_junit_cells(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "platform"
    calibration = tmp_path / "calibration"
    for artifact_name in ("ubuntu-latest", "macos-latest"):
        for version in ("3.11", "3.12", "3.13"):
            path = artifacts / f"candidate-full-{artifact_name}-{version}/candidate-tests.xml"
            path.parent.mkdir(parents=True)
            path.write_text(
                f'<testsuites><testsuite><testcase classname="tests.{artifact_name}" '
                f'name="test_{version}" /></testsuite></testsuites>',
                encoding="utf-8",
            )
    for version in ("3.11", "3.13"):
        path = artifacts / f"candidate-full-windows-{version}-aggregate/candidate-tests.xml"
        path.parent.mkdir(parents=True)
        path.write_text(
            f'<testsuites><testsuite><testcase classname="tests.windows" '
            f'name="test_{version}" /></testsuite></testsuites>',
            encoding="utf-8",
        )
    calibration.mkdir()
    (calibration / "windows-calibration.xml").write_text(
        '<testsuites><testsuite><testcase classname="tests.windows" '
        'name="test_3.12" /></testsuite></testsuites>',
        encoding="utf-8",
    )
    active = tmp_path / "active.json"
    active.write_text(
        canonical_json(
            {
                "candidate_binding": {
                    "source_commit": "1" * 40,
                    "source_tree": "2" * 40,
                    "lock_sha256": "3" * 64,
                    "wheel_sha256": "4" * 64,
                    "sdist_sha256": "5" * 64,
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    receipt = build_platform_matrix_receipt(
        repository=REPOSITORY,
        active_qualification=active,
        platform_artifacts=artifacts,
        windows_calibration=calibration,
        candidate_run_id=123,
    )
    assert len(receipt["rows"]) == 9
    assert {
        (row["platform"], row["python_version"]) for row in receipt["rows"]
    } == {
        (platform, version)
        for platform in ("ubuntu", "macos", "windows")
        for version in ("3.11", "3.12", "3.13")
    }
    from benchmarks.release.release_provenance_v7 import (
        _load_candidate_provenance_identities,
    )

    identities = _load_candidate_provenance_identities()["candidate_platform_receipt"]
    assert receipt["receipt"]["runner"] == identities["runner"]
    assert receipt["receipt"]["scorer"] == identities["scorer"]
