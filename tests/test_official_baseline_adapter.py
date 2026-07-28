from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from benchmarks.baselines.collection_gate import build_collection_report
from benchmarks.baselines.official_adapter import (
    build_execution_plan,
    execute_plan,
)
from benchmarks.baselines.registry import load_registry, registry_sha256
from deeplaw.util import canonical_json, sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[1]


def _git(checkout: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=checkout,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write_wrapper(path: Path) -> None:
    path.write_text(
        """from __future__ import annotations

import json
import hashlib
import os
import sys
import time
from pathlib import Path

mode = sys.argv[1]
print(f"wrapper stdout secret={bool(os.environ.get('DEEPLAW_TEST_SECRET'))}", flush=True)
print("wrapper stderr retained", file=sys.stderr, flush=True)
if mode == "fail":
    raise SystemExit(7)
if mode == "timeout":
    time.sleep(10)
frozen_queries = [
    json.loads(line)
    for line in Path(os.environ["DEEPLAW_BASELINE_QUERIES"]).read_text().splitlines()
    if line.strip()
]
queries = list(frozen_queries)
if mode == "missing":
    queries = queries[:-1]
elif mode == "extra":
    queries.append({"case_id": "case-extra", "query": "extra"})
runs = []
for query in queries:
    runs.append(
        {
            "schema_version": "deeplaw.external-retrieval-run/v1",
            "case_id": query["case_id"],
            "retrieved": [
                {"id": "source-a", "chars": 8, "provenance_valid": True}
            ],
            "latency_ms": float("nan") if mode == "nonfinite" else 1.25,
            "task_success": (
                False
                if mode == "unretained-failure" and query["case_id"] == "case-a"
                else None
            ),
        }
    )
Path(os.environ["DEEPLAW_BASELINE_OUTPUT"]).write_text(
    "".join(json.dumps(run, sort_keys=True) + "\\n" for run in runs),
    encoding="utf-8",
)
resource = {
    "schema_version": "deeplaw.official-baseline-resource-record/v1",
    "system_id": os.environ["DEEPLAW_BASELINE_SYSTEM_ID"],
    "implementation_revision": os.environ["DEEPLAW_BASELINE_IMPLEMENTATION_REVISION"],
    "registry_sha256": os.environ["DEEPLAW_BASELINE_REGISTRY_SHA256"],
    "corpus_sha256": os.environ["DEEPLAW_BASELINE_CORPUS_SHA256"],
    "queries_sha256": os.environ["DEEPLAW_BASELINE_QUERIES_SHA256"],
    "query_case_ids_sha256": os.environ["DEEPLAW_BASELINE_QUERY_CASE_IDS_SHA256"],
    "evaluation_environment_record_sha256": os.environ[
        "DEEPLAW_BASELINE_EVALUATION_ENVIRONMENT_RECORD_SHA256"
    ],
    "case_count": len(frozen_queries),
    "build_seconds": 0.5,
    "query_seconds": 0.25,
    "peak_memory_bytes": 1048576,
    "index_bytes": 4096,
    "workspace_bytes": 8192,
    "model_calls": 0,
    "model_input_tokens": 0,
    "model_output_tokens": 0,
    "model_cost_usd": 0.0,
    "failure_count": 0,
    "failures": [],
}
if mode == "bad-resource":
    resource["registry_sha256"] = "0" * 64
canonical = json.dumps(
    resource,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
resource["record_sha256"] = hashlib.sha256(canonical).hexdigest()
Path(os.environ["DEEPLAW_BASELINE_RESOURCE_RECORD"]).write_text(
    json.dumps(resource, ensure_ascii=False, indent=2, sort_keys=True) + "\\n",
    encoding="utf-8",
)
""",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, mode: str = "valid") -> tuple[dict[str, Any], dict[str, Path]]:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "--quiet")
    _git(checkout, "config", "user.email", "evaluator@example.invalid")
    _git(checkout, "config", "user.name", "Independent Evaluator")
    (checkout / "tracked.txt").write_text("pinned baseline\n", encoding="utf-8")
    _git(checkout, "add", "tracked.txt")
    _git(checkout, "commit", "--quiet", "-m", "pinned baseline")
    revision = _git(checkout, "rev-parse", "HEAD")

    registry = deepcopy(load_registry())
    system = next(
        item for item in registry["systems"] if item["system_id"] == "baseline/bm25"
    )
    system["implementation"]["revision"] = revision
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    registry = load_registry(registry_path)

    environment_body = {
        "schema_version": "deeplaw.baseline-evaluation-environment/v1",
        "evaluator_run_id": "independent-held-out-run-001",
        "system_id": "baseline/bm25",
        "implementation_revision": revision,
        "hardware": {
            "host_id": "fixed-host-001",
            "os_name": "TestOS",
            "os_version": "1.0",
            "architecture": "test64",
            "cpu_model": "Fixed test CPU",
            "logical_cpu_count": 8,
            "memory_bytes": 17_179_869_184,
            "accelerator": None,
            "storage": "fixed local SSD",
        },
        "software": [
            {
                "name": "python",
                "version": "test-runtime-1",
                "role": "runtime",
                "artifact_sha256": sha256_bytes(b"test-runtime"),
            }
        ],
        "models": [],
        "reader": {
            "alias": "generation-reader",
            "model_id": registry["shared_models"]["generation-reader"]["model_id"],
            "revision": registry["shared_models"]["generation-reader"]["revision"],
            "artifact_manifest_sha256": sha256_bytes(b"generation-reader-files"),
            "loopback_only": True,
        },
        "network": {
            "policy": "build-network-recorded-query-offline",
            "enforcement_method": "test OS network namespace",
            "build_network_used": True,
            "build_network_record_sha256": sha256_bytes(b"build-network-record"),
            "query_network_disabled": True,
            "loopback_only_model_services": True,
        },
        "measurement": {
            "clock": "evaluator-monotonic-wall-clock-v1",
            "peak_memory": "evaluator-process-tree-peak-rss-v1",
            "disk": "evaluator-workspace-apparent-bytes-v1",
            "model_cost": "local-model-call-token-ledger-v1",
        },
    }
    environment = {
        **environment_body,
        "record_sha256": sha256_bytes(
            canonical_json(environment_body).encode("utf-8")
        ),
    }
    environment_path = tmp_path / "evaluation-environment.json"
    environment_path.write_text(
        json.dumps(environment, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        '{"id":"source-a","text":"Alpha evidence","title":"Alpha"}\n',
        encoding="utf-8",
    )
    queries = tmp_path / "queries.jsonl"
    queries.write_text(
        '{"case_id":"case-a","query":"alpha"}\n'
        '{"case_id":"case-b","query":"beta"}\n',
        encoding="utf-8",
    )
    wrapper = tmp_path / "wrapper.py"
    _write_wrapper(wrapper)
    artifact_root = tmp_path / "artifacts"
    paths = {
        "checkout": checkout,
        "registry": registry_path,
        "corpus": corpus,
        "queries": queries,
        "evaluation_environment": environment_path,
        "wrapper": wrapper,
        "output": artifact_root / "raw-output.jsonl",
        "resource": artifact_root / "resource-record.json",
        "stdout": artifact_root / "stdout.log",
        "stderr": artifact_root / "stderr.log",
        "receipt": artifact_root / "receipt.json",
    }
    plan = build_execution_plan(
        registry=registry,
        registry_path=registry_path,
        system_id="baseline/bm25",
        checkout=checkout,
        corpus=corpus,
        queries=queries,
        evaluation_environment=environment_path,
        output=paths["output"],
        resource_record=paths["resource"],
        stdout_log=paths["stdout"],
        stderr_log=paths["stderr"],
        receipt=paths["receipt"],
        wrapper=wrapper,
        command=[sys.executable, str(wrapper), mode],
    )
    return plan, paths


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((REPOSITORY / "contracts" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _collection_manifest(
    tmp_path: Path,
    *,
    plan: dict[str, Any],
    paths: dict[str, Path],
) -> Path:
    plan_path = tmp_path / "execution-plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    registry = load_registry(paths["registry"])
    body = {
        "schema_version": "deeplaw.baseline-evidence-collection/v1",
        "collection_id": "independent-held-out-run-001",
        "registry_sha256": registry_sha256(registry),
        "runs": [
            {
                "system_id": "baseline/bm25",
                "plan_path": str(plan_path),
                "receipt_path": str(paths["receipt"]),
            }
        ],
    }
    manifest = {
        **body,
        "record_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }
    manifest_path = tmp_path / "evidence-collection.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _validator("baseline-evidence-collection.v1.schema.json").validate(manifest)
    return manifest_path


def test_official_adapter_binds_inputs_and_retains_success_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPLAW_TEST_SECRET", "must-not-leak")
    plan, paths = _fixture(tmp_path)

    _validator("official-baseline-execution-plan.v2.schema.json").validate(plan)
    _validator("baseline-evaluation-environment.v1.schema.json").validate(
        json.loads(paths["evaluation_environment"].read_text(encoding="utf-8"))
    )
    assert plan["network_control"] == {
        "policy": "build-network-recorded-query-offline",
        "runner_enforces_network_isolation": False,
        "required_external_enforcement": "evaluator-provided-os-sandbox",
    }
    assert "DEEPLAW_TEST_SECRET" not in plan["environment_contract"]["inherited_names"]

    receipt = execute_plan(plan, timeout_seconds=30)

    _validator("official-baseline-execution-receipt.v2.schema.json").validate(receipt)
    assert receipt["execution_status"] == "succeeded"
    assert receipt["output_validation"] == "passed"
    assert receipt["resource_validation"] == "passed"
    assert receipt["query_case_count"] == receipt["output_case_count"] == 2
    assert receipt["query_case_ids_sha256"] == receipt["output_case_ids_sha256"]
    resource = json.loads(paths["resource"].read_text(encoding="utf-8"))
    _validator("official-baseline-resource-record.v1.schema.json").validate(resource)
    assert receipt["resource_record_sha256"] == resource["record_sha256"]
    assert paths["stdout"].read_text(encoding="utf-8").strip().endswith("secret=False")
    assert paths["stderr"].read_text(encoding="utf-8").strip() == (
        "wrapper stderr retained"
    )
    assert json.loads(paths["receipt"].read_text(encoding="utf-8")) == receipt
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    assert receipt["receipt_sha256"] == sha256_bytes(
        canonical_json(body).encode("utf-8")
    )


@pytest.mark.parametrize("mode", ["missing", "extra", "nonfinite"])
def test_official_adapter_fails_closed_on_invalid_or_incomplete_output(
    tmp_path: Path,
    mode: str,
) -> None:
    plan, paths = _fixture(tmp_path, mode=mode)

    receipt = execute_plan(plan, timeout_seconds=30)

    _validator("official-baseline-execution-receipt.v2.schema.json").validate(receipt)
    assert receipt["execution_status"] == "output_invalid"
    assert receipt["output_validation"] == "failed"
    assert receipt["output_case_count"] is None
    assert receipt["failure_reason"]
    assert receipt["raw_output"] is not None
    assert receipt["resource_validation"] == "passed"
    assert paths["output"].is_file()
    assert paths["receipt"].is_file()


def test_official_adapter_retains_logs_and_receipt_for_command_failure(
    tmp_path: Path,
) -> None:
    plan, paths = _fixture(tmp_path, mode="fail")

    receipt = execute_plan(plan, timeout_seconds=30)

    assert receipt["execution_status"] == "command_failed"
    assert receipt["exit_code"] == 7
    assert receipt["output_validation"] == "not_run"
    assert receipt["resource_validation"] == "not_run"
    assert receipt["raw_output"] is None
    assert "wrapper stdout" in paths["stdout"].read_text(encoding="utf-8")
    assert "wrapper stderr retained" in paths["stderr"].read_text(encoding="utf-8")
    assert paths["receipt"].is_file()


def test_official_adapter_retains_logs_and_receipt_for_timeout(tmp_path: Path) -> None:
    plan, paths = _fixture(tmp_path, mode="timeout")

    receipt = execute_plan(plan, timeout_seconds=1)

    _validator("official-baseline-execution-receipt.v2.schema.json").validate(receipt)
    assert receipt["execution_status"] == "bounded_subprocess_failed"
    assert "timed out" in receipt["failure_reason"]
    assert receipt["stdout"]["truncated"] is False
    assert receipt["stderr"]["truncated"] is False
    assert receipt["resource_validation"] == "not_run"
    assert "wrapper stdout" in paths["stdout"].read_text(encoding="utf-8")
    assert "wrapper stderr retained" in paths["stderr"].read_text(encoding="utf-8")
    assert paths["receipt"].is_file()


@pytest.mark.parametrize(
    "drift",
    [
        "registry",
        "checkout",
        "corpus",
        "queries",
        "evaluation_environment",
        "wrapper",
    ],
)
def test_official_adapter_rechecks_every_frozen_binding_before_launch(
    tmp_path: Path,
    drift: str,
) -> None:
    plan, paths = _fixture(tmp_path)
    if drift == "registry":
        registry = json.loads(paths["registry"].read_text(encoding="utf-8"))
        registry["purpose"] += " Exact-byte drift."
        paths["registry"].write_text(json.dumps(registry), encoding="utf-8")
    elif drift == "checkout":
        (paths["checkout"] / "tracked.txt").write_text("drift\n", encoding="utf-8")
        _git(paths["checkout"], "add", "tracked.txt")
        _git(paths["checkout"], "commit", "--quiet", "-m", "drift")
    else:
        selected = paths[drift]
        selected.write_bytes(selected.read_bytes() + b"\n")

    with pytest.raises(RuntimeError, match="changed after planning"):
        execute_plan(plan, timeout_seconds=30)

    assert not paths["stdout"].exists()
    assert not paths["stderr"].exists()
    assert not paths["receipt"].exists()


def test_official_adapter_fails_closed_on_invalid_resource_record(
    tmp_path: Path,
) -> None:
    plan, paths = _fixture(tmp_path, mode="bad-resource")

    receipt = execute_plan(plan, timeout_seconds=30)

    _validator("official-baseline-execution-receipt.v2.schema.json").validate(receipt)
    assert receipt["execution_status"] == "resource_invalid"
    assert receipt["output_validation"] == "passed"
    assert receipt["resource_validation"] == "failed"
    assert receipt["resource_record"] is not None
    assert receipt["resource_record_sha256"] is None
    assert paths["resource"].is_file()
    assert paths["receipt"].is_file()


def test_official_adapter_requires_raw_task_failures_in_resource_inventory(
    tmp_path: Path,
) -> None:
    plan, paths = _fixture(tmp_path, mode="unretained-failure")

    receipt = execute_plan(plan, timeout_seconds=30)

    assert receipt["execution_status"] == "resource_invalid"
    assert receipt["output_validation"] == "passed"
    assert receipt["resource_validation"] == "failed"
    assert "task failures" in receipt["failure_reason"]
    assert paths["resource"].is_file()


def test_baseline_collection_gate_verifies_retained_run_and_stays_incomplete(
    tmp_path: Path,
) -> None:
    plan, paths = _fixture(tmp_path)
    execute_plan(plan, timeout_seconds=30)
    manifest_path = _collection_manifest(tmp_path, plan=plan, paths=paths)

    report = build_collection_report(
        registry_path=paths["registry"],
        collection_path=manifest_path,
    )

    _validator("baseline-evidence-collection-report.v1.schema.json").validate(report)
    assert report["valid_system_ids"] == ["baseline/bm25"]
    assert report["successful_system_ids"] == ["baseline/bm25"]
    assert report["valid_run_count"] == report["successful_run_count"] == 1
    assert len(report["missing_system_ids"]) == 16
    assert report["collection_complete"] is False
    assert report["claim_eligible"] is False
    assert all(check["passed"] is False for check in report["fairness_checks"])


def test_baseline_collection_gate_rejects_post_receipt_artifact_drift(
    tmp_path: Path,
) -> None:
    plan, paths = _fixture(tmp_path)
    execute_plan(plan, timeout_seconds=30)
    manifest_path = _collection_manifest(tmp_path, plan=plan, paths=paths)
    paths["output"].write_bytes(paths["output"].read_bytes() + b"\n")

    report = build_collection_report(
        registry_path=paths["registry"],
        collection_path=manifest_path,
    )

    assert report["valid_run_count"] == 0
    assert report["invalid_runs"] == [
        {
            "system_id": "baseline/bm25",
            "code": "artifact_drift",
            "reason": "raw output bytes differ from the receipt",
        }
    ]
    assert "baseline/bm25" in report["missing_system_ids"]


def test_official_adapter_reloads_registry_after_recomputed_plan_tamper(
    tmp_path: Path,
) -> None:
    plan, _ = _fixture(tmp_path)
    tampered = deepcopy(plan)
    tampered["system"]["display_name"] = "Unregistered replacement"
    body = {key: value for key, value in tampered.items() if key != "plan_sha256"}
    tampered["plan_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))

    with pytest.raises(RuntimeError, match="registry entry changed"):
        execute_plan(tampered, timeout_seconds=30)
