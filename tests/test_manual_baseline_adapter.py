from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.baselines.collection_gate import build_collection_report
from benchmarks.baselines.manual_adapter import (
    build_manual_execution_plan,
    seal_manual_execution,
)
from benchmarks.baselines.registry import (
    default_registry_path,
    load_registry,
    registry_sha256,
)
from deeplaw.util import canonical_json, sha256_bytes, sha256_file

REPOSITORY = Path(__file__).resolve().parents[1]


def _write_record(path: Path, body: dict[str, Any]) -> dict[str, Any]:
    record = {
        **body,
        "record_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record


def _artifact(path: Path) -> dict[str, Any]:
    return {
        "path_hint": str(path),
        "sha256": sha256_file(path),
        "byte_size": path.stat().st_size,
    }


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((REPOSITORY / "contracts" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _fixture(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    registry_path = default_registry_path().absolute()
    registry = load_registry(registry_path)
    system = next(
        item for item in registry["systems"] if item["system_id"] == "obsidian-native"
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
    reader_model = registry["shared_models"]["generation-reader"]
    reader = {
        "alias": "generation-reader",
        "model_id": reader_model["model_id"],
        "revision": reader_model["revision"],
        "artifact_manifest_sha256": sha256_bytes(b"reader-manifest"),
        "loopback_only": True,
    }
    environment_path = tmp_path / "evaluation-environment.json"
    _write_record(
        environment_path,
        {
            "schema_version": "deeplaw.baseline-evaluation-environment/v1",
            "evaluator_run_id": "manual-held-out-run-001",
            "system_id": "obsidian-native",
            "implementation_revision": system["implementation"]["revision"],
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
                    "name": "obsidian-desktop",
                    "version": "1.12.7",
                    "role": "runtime",
                    "artifact_sha256": sha256_bytes(b"obsidian-desktop-build"),
                }
            ],
            "models": [reader],
            "reader": reader,
            "network": {
                "policy": "manual-local-offline",
                "enforcement_method": "test OS application firewall",
                "build_network_used": False,
                "build_network_record_sha256": None,
                "query_network_disabled": True,
                "loopback_only_model_services": True,
            },
            "measurement": {
                "clock": "evaluator-monotonic-wall-clock-v1",
                "peak_memory": "evaluator-process-tree-peak-rss-v1",
                "disk": "evaluator-workspace-apparent-bytes-v1",
                "model_cost": "local-model-call-token-ledger-v1",
            },
        },
    )
    artifacts = tmp_path / "artifacts"
    paths = {
        "registry": registry_path,
        "corpus": corpus,
        "queries": queries,
        "environment": environment_path,
        "output": artifacts / "raw-output.jsonl",
        "resource": artifacts / "resource-record.json",
        "manual": artifacts / "manual-record.json",
        "receipt": artifacts / "receipt.json",
        "plan": tmp_path / "manual-plan.json",
    }
    plan = build_manual_execution_plan(
        registry=registry,
        registry_path=registry_path,
        system_id="obsidian-native",
        corpus=corpus,
        queries=queries,
        evaluation_environment=environment_path,
        workflow=REPOSITORY / "benchmarks" / "baselines" / "obsidian-workflow-v1.md",
        output=paths["output"],
        resource_record=paths["resource"],
        manual_record=paths["manual"],
        receipt=paths["receipt"],
    )
    paths["plan"].write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts.mkdir(parents=True)
    paths["output"].write_text(
        json.dumps(
            {
                "schema_version": "deeplaw.external-retrieval-run/v1",
                "case_id": "case-a",
                "retrieved": [
                    {"id": "source-a", "chars": 8, "provenance_valid": True}
                ],
                "latency_ms": 10.0,
                "task_success": True,
            },
            sort_keys=True,
        )
        + "\n"
        + json.dumps(
            {
                "schema_version": "deeplaw.external-retrieval-run/v1",
                "case_id": "case-b",
                "retrieved": [],
                "latency_ms": 12.0,
                "task_success": False,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    resource_body = {
        "schema_version": "deeplaw.official-baseline-resource-record/v1",
        "system_id": "obsidian-native",
        "implementation_revision": plan["implementation_revision"],
        "registry_sha256": plan["registry"]["canonical_sha256"],
        "corpus_sha256": plan["corpus"]["sha256"],
        "queries_sha256": plan["queries"]["sha256"],
        "query_case_ids_sha256": plan["queries"]["case_ids_sha256"],
        "evaluation_environment_record_sha256": plan["evaluation_environment"][
            "record_sha256"
        ],
        "case_count": 2,
        "build_seconds": 2.0,
        "query_seconds": 1.0,
        "peak_memory_bytes": 1024,
        "index_bytes": 2048,
        "workspace_bytes": 4096,
        "model_calls": 2,
        "model_input_tokens": 100,
        "model_output_tokens": 20,
        "model_cost_usd": 0.0,
        "failure_count": 1,
        "failures": [
            {
                "case_id": "case-b",
                "phase": "query",
                "kind": "error",
                "message_sha256": sha256_bytes(b"manual task failed"),
            }
        ],
    }
    _write_record(paths["resource"], resource_body)
    captures = {}
    for name, content in (
        ("screen", b"screen recording fixture"),
        ("before", b"vault before fixture"),
        ("after", b"vault after fixture"),
    ):
        capture = artifacts / f"{name}.bin"
        capture.write_bytes(content)
        captures[name] = _artifact(capture)
    manual_body = {
        "schema_version": "deeplaw.obsidian-manual-run/v1",
        "system_id": "obsidian-native",
        "implementation_revision": plan["implementation_revision"],
        "registry_sha256": plan["registry"]["canonical_sha256"],
        "corpus_sha256": plan["corpus"]["sha256"],
        "queries_sha256": plan["queries"]["sha256"],
        "query_case_ids_sha256": plan["queries"]["case_ids_sha256"],
        "evaluation_environment_record_sha256": plan["evaluation_environment"][
            "record_sha256"
        ],
        "workflow_sha256": plan["workflow"]["sha256"],
        "screen_recording": captures["screen"],
        "vault_before_archive": captures["before"],
        "vault_after_archive": captures["after"],
        "case_count": 2,
        "cases": [
            {
                "case_id": "case-a",
                "task_success": True,
                "useful_context_recall": 1.0,
                "irrelevant_context_rate": 0.0,
                "source_provenance_coverage": 1.0,
                "span_provenance_coverage": 1.0,
                "stale_leakage": False,
                "context_tokens": 100,
                "indexing_seconds": 1.0,
                "query_seconds": 0.5,
                "operator_seconds": 2.0,
                "source_locator_sha256": sha256_bytes(b"source-a:1"),
                "failure_kind": None,
                "failure_detail_sha256": None,
            },
            {
                "case_id": "case-b",
                "task_success": False,
                "useful_context_recall": 0.0,
                "irrelevant_context_rate": 0.0,
                "source_provenance_coverage": 0.0,
                "span_provenance_coverage": 0.0,
                "stale_leakage": False,
                "context_tokens": 0,
                "indexing_seconds": 1.0,
                "query_seconds": 0.5,
                "operator_seconds": 2.0,
                "source_locator_sha256": None,
                "failure_kind": "error",
                "failure_detail_sha256": sha256_bytes(b"manual task failed"),
            },
        ],
    }
    _write_record(paths["manual"], manual_body)
    return plan, paths


def test_manual_baseline_seals_complete_evidence_and_collection_accepts_it(
    tmp_path: Path,
) -> None:
    plan, paths = _fixture(tmp_path)

    receipt = seal_manual_execution(plan)

    _validator("manual-baseline-execution-plan.v1.schema.json").validate(plan)
    _validator("manual-baseline-execution-receipt.v1.schema.json").validate(receipt)
    _validator("obsidian-manual-run.v1.schema.json").validate(
        json.loads(paths["manual"].read_text(encoding="utf-8"))
    )
    assert receipt["execution_status"] == "succeeded"
    assert receipt["manual_validation"] == "passed"

    collection_body = {
        "schema_version": "deeplaw.baseline-evidence-collection/v1",
        "collection_id": "manual-held-out-run-001",
        "registry_sha256": registry_sha256(load_registry(paths["registry"])),
        "runs": [
            {
                "system_id": "obsidian-native",
                "plan_path": str(paths["plan"]),
                "receipt_path": str(paths["receipt"]),
            }
        ],
    }
    collection = {
        **collection_body,
        "record_sha256": sha256_bytes(
            canonical_json(collection_body).encode("utf-8")
        ),
    }
    collection_path = tmp_path / "collection.json"
    collection_path.write_text(
        json.dumps(collection, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = build_collection_report(
        registry_path=paths["registry"],
        collection_path=collection_path,
    )

    assert report["valid_system_ids"] == ["obsidian-native"]
    assert report["run_evidence"][0]["manual_record_sha256"] == receipt[
        "manual_record_sha256"
    ]
    assert report["collection_complete"] is False


def test_manual_baseline_fails_closed_when_case_outcomes_disagree(
    tmp_path: Path,
) -> None:
    plan, paths = _fixture(tmp_path)
    record = json.loads(paths["manual"].read_text(encoding="utf-8"))
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    body["cases"][0]["task_success"] = False
    body["cases"][0]["failure_kind"] = "error"
    body["cases"][0]["failure_detail_sha256"] = sha256_bytes(b"mismatch")
    _write_record(paths["manual"], body)

    receipt = seal_manual_execution(plan)

    assert receipt["execution_status"] == "manual_record_invalid"
    assert receipt["output_validation"] == "passed"
    assert receipt["resource_validation"] == "passed"
    assert receipt["manual_validation"] == "failed"
    assert "outcomes differ" in receipt["failure_reason"]
