"""Build and aggregate fail-closed current-source regression receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SHARD_SCHEMA = "deeplaw.candidate-test-shard/v1"
RECEIPT_SCHEMA = "deeplaw.platform-candidate-regression-receipt/v1"
AGGREGATE_SCHEMA = "deeplaw.platform-candidate-regression-aggregate/v1"
DURATION_SCHEMA = "deeplaw.candidate-duration-weights/v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(_canonical_json(value) + "\n", encoding="utf-8")


def _write_paths(path: Path, paths: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(paths) + "\n")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"candidate regression input must be an object: {path.name}")
    return value


def _tracked_test_files(repository: Path) -> list[str]:
    tests = repository / "tests"
    files = sorted(
        path.relative_to(repository).as_posix()
        for path in tests.glob("test_*.py")
        if path.is_file() and not path.is_symlink()
    )
    if not files:
        raise RuntimeError("candidate regression found no regular test modules")
    return files


def build_shard_manifest(
    *,
    repository: Path,
    shard_count: int,
    shard_index: int,
    duration_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    if not 2 <= shard_count <= 16 or not 1 <= shard_index <= shard_count:
        raise ValueError("candidate regression shard selection is out of bounds")
    all_files = _tracked_test_files(repository)
    if duration_weights is None:
        selected = [
            path
            for offset, path in enumerate(all_files)
            if offset % shard_count == shard_index - 1
        ]
        algorithm = "round_robin_v1"
        normalized_weights = None
    else:
        if set(duration_weights) != set(all_files) or any(
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or float(weight) <= 0
            for weight in duration_weights.values()
        ):
            raise RuntimeError("candidate duration weights do not match the test inventory")
        normalized_weights = {
            path: float(duration_weights[path]) for path in all_files
        }
        assignments: list[list[str]] = [[] for _ in range(shard_count)]
        totals = [0.0 for _ in range(shard_count)]
        for path in sorted(all_files, key=lambda item: (-normalized_weights[item], item)):
            target = min(range(shard_count), key=lambda item: (totals[item], item))
            assignments[target].append(path)
            totals[target] += normalized_weights[path]
        selected = sorted(assignments[shard_index - 1])
        algorithm = "longest_processing_time_duration_v1"
    if not selected:
        raise RuntimeError("candidate regression shard is empty")
    manifest = {
        "schema_version": SHARD_SCHEMA,
        "algorithm": algorithm,
        "shard_count": shard_count,
        "shard_index": shard_index,
        "all_test_file_count": len(all_files),
        "all_test_files_sha256": _sha256_json(all_files),
        "selected_test_file_count": len(selected),
        "selected_test_files_sha256": _sha256_json(selected),
        "selected_test_files": selected,
    }
    if normalized_weights is not None:
        manifest["duration_weights"] = normalized_weights
        manifest["duration_weights_sha256"] = _sha256_json(normalized_weights)
        manifest["selected_estimated_duration_seconds"] = sum(
            normalized_weights[path] for path in selected
        )
    return manifest


def build_duration_weights(
    *, repository: Path, junit_path: Path
) -> dict[str, Any]:
    """Derive deterministic per-module weights from an executed Windows JUnit run."""

    repository = repository.resolve(strict=True)
    all_files = _tracked_test_files(repository)
    module_by_file = {
        path: path[:-3].replace("/", ".")
        for path in all_files
    }
    weights = {path: 0.001 for path in all_files}
    root = ET.parse(junit_path).getroot()
    observed = 0
    for case in root.findall(".//testcase"):
        classname = case.attrib.get("classname", "")
        duration_text = case.attrib.get("time", "0")
        try:
            duration = float(duration_text)
        except ValueError as error:
            raise RuntimeError("candidate calibration contains an invalid duration") from error
        if not math.isfinite(duration) or duration < 0:
            raise RuntimeError("candidate calibration contains an invalid duration")
        matches = [
            path
            for path, module in module_by_file.items()
            if classname == module or classname.startswith(module + ".")
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"candidate calibration testcase has no exact module: {classname}"
            )
        weights[matches[0]] += duration
        observed += 1
    if observed == 0:
        raise RuntimeError("candidate calibration contains no testcase durations")
    document = {
        "schema_version": DURATION_SCHEMA,
        "algorithm": "junit_module_duration_v1",
        "junit_sha256": hashlib.sha256(junit_path.read_bytes()).hexdigest(),
        "all_test_files_sha256": _sha256_json(all_files),
        "observed_testcase_count": observed,
        "weights_seconds": weights,
    }
    document["record_sha256"] = _sha256_json(document)
    return document


def _load_duration_weights(path: Path, repository: Path) -> dict[str, float]:
    document = _read_object(path)
    record = document.pop("record_sha256", None)
    if (
        document.get("schema_version") != DURATION_SCHEMA
        or record != _sha256_json(document)
        or document.get("all_test_files_sha256")
        != _sha256_json(_tracked_test_files(repository))
        or not isinstance(document.get("weights_seconds"), dict)
    ):
        raise RuntimeError("candidate duration-weight manifest is invalid")
    return {str(key): float(value) for key, value in document["weights_seconds"].items()}


def _classified_skip_identities(
    repository: Path,
) -> tuple[dict[str, Any], dict[str, set[tuple[str, str]]]]:
    manifest = _read_object(
        repository / "benchmarks/release/platform-core-test-manifest-v2.json"
    )

    def identities(cases: Any) -> set[tuple[str, str]]:
        if not isinstance(cases, list):
            raise RuntimeError("platform test classification must contain cases")
        return {
            (case["junit"]["classname"], case["junit"]["name"])
            for case in cases
        }

    classifications = manifest["classifications"]
    return manifest, {
        "qualification": identities(classifications["qualification"]["cases"]),
        "nonapplicable": identities(classifications["nonapplicable"]["cases"]),
        "historical_compatibility": identities(
            classifications["historical_compatibility"]["cases"]
        ),
    }


def build_regression_receipt(
    *,
    repository: Path,
    junit_path: Path,
    matrix_os: str,
    matrix_python: str,
    shard_manifest_path: Path | None = None,
) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    actual_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if actual_python != matrix_python:
        raise RuntimeError(
            f"matrix Python mismatch: expected {matrix_python}, got {actual_python}"
        )
    manifest, classifications = _classified_skip_identities(repository)
    root = ET.parse(junit_path).getroot()
    skipped = [
        (case.attrib.get("classname", ""), case.attrib.get("name", ""))
        for case in root.findall(".//testcase")
        if case.find("skipped") is not None
    ]
    classified = set().union(*classifications.values())
    unclassified = sorted(set(skipped) - classified)
    if unclassified:
        raise RuntimeError(f"unclassified candidate skips: {unclassified[:8]}")
    suites = root.findall(".//testsuite")
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA,
        "status": "current_source_regression",
        "release_ready": False,
        "environment": {
            "matrix_os": matrix_os,
            "matrix_python": matrix_python,
            "python_version": sys.version,
        },
        "test_manifest": {
            "schema_version": manifest["schema_version"],
            "manifest_sha256": manifest["manifest_sha256"],
        },
        "junit": {
            "tests": sum(int(suite.attrib.get("tests", "0")) for suite in suites),
            "failures": sum(int(suite.attrib.get("failures", "0")) for suite in suites),
            "errors": sum(int(suite.attrib.get("errors", "0")) for suite in suites),
            "skipped": len(skipped),
        },
        "qualification": {
            "status": "not_executed",
            "skipped": sum(item in classifications["qualification"] for item in skipped),
        },
        "nonapplicable": {
            "status": "nonapplicable",
            "skipped": sum(item in classifications["nonapplicable"] for item in skipped),
        },
        "historical_compatibility": {
            "status": "not_executed",
            "skipped": sum(
                item in classifications["historical_compatibility"] for item in skipped
            ),
        },
    }
    if shard_manifest_path is not None:
        shard = _read_object(shard_manifest_path)
        if shard.get("schema_version") != SHARD_SCHEMA:
            raise RuntimeError("candidate regression shard schema is invalid")
        receipt["shard"] = {
            "shard_count": shard["shard_count"],
            "shard_index": shard["shard_index"],
            "all_test_file_count": shard["all_test_file_count"],
            "all_test_files_sha256": shard["all_test_files_sha256"],
            "selected_test_file_count": shard["selected_test_file_count"],
            "selected_test_files_sha256": shard["selected_test_files_sha256"],
        }
    return receipt


def aggregate_shard_receipts(
    *, repository: Path, input_directory: Path, matrix_python: str
) -> dict[str, Any]:
    repository = repository.resolve(strict=True)
    input_directory = input_directory.resolve(strict=True)
    expected_files = _tracked_test_files(repository)
    manifests = sorted(input_directory.glob("**/candidate-test-shard.json"))
    if not manifests:
        raise RuntimeError("candidate regression aggregate found no shard manifests")
    by_index: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    for manifest_path in manifests:
        shard = _read_object(manifest_path)
        receipt_path = manifest_path.with_name("candidate-skip-receipt.json")
        receipt = _read_object(receipt_path)
        if receipt.get("environment", {}).get("matrix_python") != matrix_python:
            raise RuntimeError("candidate regression shard Python does not match")
        if receipt.get("environment", {}).get("matrix_os") != "windows-latest":
            raise RuntimeError("candidate regression aggregate accepts Windows shards only")
        index = shard.get("shard_index")
        if not isinstance(index, int) or index in by_index:
            raise RuntimeError("candidate regression shard index is invalid or duplicated")
        by_index[index] = (shard, receipt)
    shard_counts = {item[0].get("shard_count") for item in by_index.values()}
    if len(shard_counts) != 1:
        raise RuntimeError("candidate regression shard counts disagree")
    shard_count = shard_counts.pop()
    if not isinstance(shard_count, int) or set(by_index) != set(range(1, shard_count + 1)):
        raise RuntimeError("candidate regression shard set is incomplete")
    expected_hash = _sha256_json(expected_files)
    selected: list[str] = []
    for index in range(1, shard_count + 1):
        shard, receipt = by_index[index]
        duration_weights = shard.get("duration_weights")
        rebuilt = build_shard_manifest(
            repository=repository,
            shard_count=shard_count,
            shard_index=index,
            duration_weights=(
                {str(key): float(value) for key, value in duration_weights.items()}
                if isinstance(duration_weights, dict)
                else None
            ),
        )
        if shard != rebuilt or shard["all_test_files_sha256"] != expected_hash:
            raise RuntimeError("candidate regression shard manifest does not match the source tree")
        selected.extend(shard["selected_test_files"])
        if receipt.get("shard", {}).get("selected_test_files_sha256") != shard[
            "selected_test_files_sha256"
        ]:
            raise RuntimeError("candidate regression receipt is not bound to its shard")
    if len(selected) != len(set(selected)) or sorted(selected) != expected_files:
        raise RuntimeError("candidate regression shards are overlapping or incomplete")
    algorithms = {item[0].get("algorithm") for item in by_index.values()}
    duration_digests = {
        item[0].get("duration_weights_sha256") for item in by_index.values()
    }
    if len(algorithms) != 1 or len(duration_digests) != 1:
        raise RuntimeError("candidate regression shard algorithms or weights disagree")

    receipts = [by_index[index][1] for index in range(1, shard_count + 1)]

    def total(section: str, field: str) -> int:
        return sum(int(receipt[section][field]) for receipt in receipts)

    if total("junit", "failures") or total("junit", "errors"):
        raise RuntimeError("candidate regression shards contain test failures")
    test_manifests = {_canonical_json(receipt["test_manifest"]) for receipt in receipts}
    if len(test_manifests) != 1:
        raise RuntimeError("candidate regression shards use different test manifests")
    return {
        "schema_version": AGGREGATE_SCHEMA,
        "status": "current_source_regression",
        "release_ready": False,
        "claim_eligible": False,
        "environment": {
            "matrix_os": "windows-latest",
            "matrix_python": matrix_python,
        },
        "test_manifest": receipts[0]["test_manifest"],
        "shards": {
            "count": shard_count,
            "algorithm": algorithms.pop(),
            "duration_weights_sha256": duration_digests.pop(),
            "all_test_file_count": len(expected_files),
            "all_test_files_sha256": expected_hash,
            "complete": True,
            "overlap_absent": True,
        },
        "junit": {
            "tests": total("junit", "tests"),
            "failures": 0,
            "errors": 0,
            "skipped": total("junit", "skipped"),
        },
        "qualification": {
            "status": "not_executed",
            "skipped": total("qualification", "skipped"),
        },
        "nonapplicable": {
            "status": "nonapplicable",
            "skipped": total("nonapplicable", "skipped"),
        },
        "historical_compatibility": {
            "status": "not_executed",
            "skipped": total("historical_compatibility", "skipped"),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    select = commands.add_parser("select")
    select.add_argument("--shard-count", type=int, required=True)
    select.add_argument("--shard-index", type=int, required=True)
    select.add_argument("--output", type=Path, required=True)
    select.add_argument("--paths-output", type=Path, required=True)
    select.add_argument("--duration-weights", type=Path)
    weights = commands.add_parser("weights")
    weights.add_argument("--junit", type=Path, required=True)
    weights.add_argument("--output", type=Path, required=True)
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--junit", type=Path, required=True)
    receipt.add_argument("--output", type=Path, required=True)
    receipt.add_argument("--matrix-os", required=True)
    receipt.add_argument("--matrix-python", required=True)
    receipt.add_argument("--shard-manifest", type=Path)
    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--input-directory", type=Path, required=True)
    aggregate.add_argument("--matrix-python", required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "select":
        duration_weights = (
            _load_duration_weights(args.duration_weights, args.repository.resolve(strict=True))
            if args.duration_weights is not None
            else None
        )
        manifest = build_shard_manifest(
            repository=args.repository,
            shard_count=args.shard_count,
            shard_index=args.shard_index,
            duration_weights=duration_weights,
        )
        _write_json(args.output, manifest)
        _write_paths(args.paths_output, manifest["selected_test_files"])
    elif args.command == "weights":
        value = build_duration_weights(
            repository=args.repository,
            junit_path=args.junit,
        )
        _write_json(args.output, value)
    elif args.command == "receipt":
        receipt = build_regression_receipt(
            repository=args.repository,
            junit_path=args.junit,
            matrix_os=args.matrix_os,
            matrix_python=args.matrix_python,
            shard_manifest_path=args.shard_manifest,
        )
        _write_json(args.output, receipt)
    else:
        aggregate = aggregate_shard_receipts(
            repository=args.repository,
            input_directory=args.input_directory,
            matrix_python=args.matrix_python,
        )
        _write_json(args.output, aggregate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
