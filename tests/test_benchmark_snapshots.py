from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path


def _assert_snapshot(repository: Path, summary_name: str) -> None:
    summary_path = repository / "benchmarks" / summary_name
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    cases_path = repository / summary["cases_path"]
    cases_bytes = cases_path.read_bytes()
    cases = [
        json.loads(line)
        for line in cases_bytes.decode("utf-8").splitlines()
        if line.strip()
    ]
    buckets = Counter(case.get("expected_bucket", "evidence") for case in cases)

    assert hashlib.sha256(cases_bytes).hexdigest() == summary["cases_sha256"]
    assert len(cases) == summary["case_count"]
    assert dict(buckets) == summary["expected_bucket_counts"]


def test_current_and_historical_benchmark_fixtures_are_immutable() -> None:
    repository = Path(__file__).resolve().parents[1]

    _assert_snapshot(repository, "core-v0.4.0-candidate-2026-07-26.json")
    _assert_snapshot(repository, "core-v0.4.0-candidate-2026-07-25.json")
    _assert_snapshot(repository, "core-v5-candidate-2026-07-15.json")

    current = json.loads(
        (
            repository / "benchmarks" / "core-v0.4.0-candidate-2026-07-26.json"
        ).read_text(encoding="utf-8")
    )
    implementation = current["implementation"]
    assert (
        hashlib.sha256((repository / "pyproject.toml").read_bytes()).hexdigest()
        == implementation["pyproject_sha256"]
    )
    assert (
        hashlib.sha256((repository / "uv.lock").read_bytes()).hexdigest()
        == implementation["uv_lock_sha256"]
    )
    source_tree = hashlib.sha256()
    for path in sorted((repository / "src" / "deeplaw").rglob("*.py")):
        source_tree.update(path.relative_to(repository).as_posix().encode("utf-8"))
        source_tree.update(b"\0")
        source_tree.update(path.read_bytes())
        source_tree.update(b"\0")
    assert source_tree.hexdigest() == implementation["python_source_tree_sha256"]

    bound_files = {
        "ingest_sha256": "ingest.py",
        "search_sha256": "search.py",
        "evaluator_sha256": "evaluate.py",
        "query_plan_sha256": "query_plan.py",
        "legal_topics_sha256": "legal_topics.py",
        "evidence_compiler_sha256": "evidence_compiler.py",
        "store_sha256": "store.py",
        "mcp_server_sha256": "mcp_server.py",
        "document_engine_models_sha256": "document_engine_models.py",
        "document_engine_cli_sha256": "document_engine_cli.py",
    }
    for field, filename in bound_files.items():
        payload = (repository / "src" / "deeplaw" / filename).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == implementation[field]
