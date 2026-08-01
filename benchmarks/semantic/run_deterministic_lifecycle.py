from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.run_living_wiki_host_harness import _safe_command
from benchmarks.release.evidence import repository_binding
from benchmarks.semantic.deterministic_gold_agent import compile_source
from benchmarks.semantic.review_gold import validate_candidate
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.util import canonical_json, sha256_bytes, stable_id


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _run_cli(prefix: list[str], *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [*prefix, "knowledge", "--format", "json", *arguments],
        cwd=_repository(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        summary = completed.stderr.decode("utf-8", errors="replace")[-2_000:]
        raise RuntimeError(f"deterministic semantic lifecycle CLI failed: {summary}")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("deterministic semantic lifecycle CLI returned a non-object")
    return value


def _binding(repository: Path) -> dict[str, Any]:
    value = repository_binding(repository)
    return {
        "commit": value["commit"],
        "tree": value["tree"],
        "package_version": value["package_version"],
        "lock_sha256": value["lock_sha256"],
        "pyproject_sha256": value["pyproject_sha256"],
        "contracts_inventory_sha256": value["contracts"]["inventory_sha256"],
        "migrations_inventory_sha256": value["migrations"]["inventory_sha256"],
        "worktree_clean": value["worktree_clean"],
    }


def run(
    *,
    gold: dict[str, Any],
    corpus: dict[str, Any],
    vault: Path,
    baseline_vault: Path,
    command: dict[str, Any],
    binding_repository: Path | None = None,
) -> dict[str, Any]:
    validate_candidate(gold, repository=_repository())
    if corpus.get("schema_version") != "deeplaw.semantic-host-corpus/v2":
        raise ValueError("deterministic semantic lifecycle requires corpus v2")
    if corpus.get("gold_id") != gold["gold_id"]:
        raise ValueError("deterministic semantic corpus does not bind Gold")
    if corpus.get("fixture_manifest_sha256") != gold["fixture_manifest_sha256"]:
        raise ValueError("deterministic semantic corpus fixture manifest changed")
    if baseline_vault.exists() or baseline_vault.is_symlink():
        raise FileExistsError("baseline query Vault must be a new non-symlink path")
    prefix = _safe_command(command)
    started = time.monotonic()
    prior_runs: dict[str, dict[str, str]] = {}
    runs: list[dict[str, Any]] = []
    baseline_sources = [item for item in corpus["sources"] if item["phase"] == "baseline"]
    for source in baseline_sources:
        compilation_started = time.monotonic()
        result = compile_source(
            vault=vault,
            grant_id=corpus["grant_id"],
            source_key=source["source_key"],
            source_revision_id=source["source_revision_id"],
            prior_runs=prior_runs,
        )
        result["compilation_latency_ms"] = round(
            (time.monotonic() - compilation_started) * 1000
        )
        runs.append(result)
        prior_runs[source["source_key"]] = {
            "source_revision_id": source["source_revision_id"],
            "compilation_run_id": result["compilation_run_id"],
        }
    baseline_snapshot = baseline_vault.with_name(f".{baseline_vault.name}-snapshot")
    if baseline_snapshot.exists() or baseline_snapshot.is_symlink():
        raise FileExistsError("baseline query snapshot path already exists")
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        baseline_audit_head = store.audit_head
    snapshot_started = time.monotonic()
    snapshot = _run_cli(
        prefix,
        "snapshot",
        "create",
        "--vault",
        str(vault),
        "--output",
        str(baseline_snapshot),
    )
    try:
        _run_cli(
            prefix,
            "snapshot",
            "restore",
            "--vault",
            str(baseline_vault),
            "--snapshot",
            str(baseline_snapshot),
            "--confirm",
        )
    finally:
        if baseline_snapshot.exists() and not baseline_snapshot.is_symlink():
            shutil.rmtree(baseline_snapshot)
    snapshot_restore_latency_ms = round((time.monotonic() - snapshot_started) * 1000)
    with AutonomousKnowledgeStore(baseline_vault, read_only=True) as baseline_store:
        baseline_verified = bool(
            baseline_store.verify()["valid"]
            and baseline_store.audit_head == baseline_audit_head
        )
    if not baseline_verified:
        raise RuntimeError("deterministic baseline query Vault verification failed")
    predecessor = next(
        item for item in corpus["sources"] if item["source_key"] == "update-v1"
    )
    successor = next(
        item for item in corpus["sources"] if item["source_key"] == "update-v2"
    )
    successor_started = time.monotonic()
    approval = _run_cli(
        prefix,
        "review",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        successor["source_id"],
        "--review-manifest-sha256",
        successor["review_manifest_sha256"],
        "--reviewer-id",
        "semantic-deterministic-fixture-maintainer",
        "--reason",
        "Activate the frozen public successor fixture for deterministic review.",
        "--confirm-reviewed",
    )
    successor_freshness = _run_cli(
        prefix,
        "compile",
        "refresh",
        "--vault",
        str(vault),
        "--grant-id",
        corpus["grant_id"],
        "--source-revision-id",
        predecessor["source_revision_id"],
        "--replacement-source-revision-id",
        successor["source_revision_id"],
        "--confirm-no-case-data",
    )
    successor_compilation_started = time.monotonic()
    successor_run = compile_source(
        vault=vault,
        grant_id=corpus["grant_id"],
        source_key=successor["source_key"],
        source_revision_id=successor["source_revision_id"],
        prior_runs=prior_runs,
    )
    successor_run["compilation_latency_ms"] = round(
        (time.monotonic() - successor_compilation_started) * 1000
    )
    runs.append(successor_run)
    incremental_refresh_latency_ms = round((time.monotonic() - successor_started) * 1000)
    withdrawn = next(
        item for item in corpus["sources"] if item["source_key"] == "retention-a"
    )
    withdrawal_started = time.monotonic()
    removal = _run_cli(
        prefix,
        "source",
        "remove",
        "--vault",
        str(vault),
        "--source-id",
        withdrawn["source_id"],
        "--reason",
        "Frozen Semantic Gold withdrawal lifecycle case.",
        "--confirm",
    )
    withdrawal_freshness = _run_cli(
        prefix,
        "compile",
        "refresh",
        "--vault",
        str(vault),
        "--grant-id",
        corpus["grant_id"],
        "--source-revision-id",
        withdrawn["source_revision_id"],
        "--confirm-no-case-data",
    )
    withdrawal_refresh_latency_ms = round((time.monotonic() - withdrawal_started) * 1000)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        verification_valid = bool(store.verify()["valid"])
    recorded_at = _timestamp()
    binding = _binding(binding_repository or _repository())
    body = {
        "binding": binding,
        "gold_id": gold["gold_id"],
        "gold_status": gold["status"],
        "corpus_id": corpus["corpus_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "agent_identity": "deeplaw-deterministic-gold-agent",
        "model_identity": None,
        "network_policy": "offline",
        "external_model_execution": "not_executed",
        "first_party_command_sha256": sha256_bytes(
            canonical_json(command).encode("utf-8")
        ),
        "baseline_query_state": {
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "audit_head": baseline_audit_head,
            "verified": baseline_verified,
        },
        "runs": sorted(runs, key=lambda item: item["source_key"]),
        "transitions": [
            {
                "operation": "activate_successor",
                "status": "passed",
                "predecessor_source_revision_id": predecessor["source_revision_id"],
                "successor_source_revision_id": successor["source_revision_id"],
                "review_receipt_sha256": approval["review_receipt"]["receipt_sha256"],
                "freshness_report_sha256": successor_freshness["report_sha256"],
            },
            {
                "operation": "withdraw_source",
                "status": "passed",
                "source_revision_id": withdrawn["source_revision_id"],
                "removal_audit_head": removal["audit_head"],
                "freshness_report_sha256": withdrawal_freshness["report_sha256"],
            },
        ],
        "vault_verification_valid": verification_valid,
        "metrics": {
            "first_compilation_latency_ms": runs[0]["compilation_latency_ms"],
            "baseline_compilation_latency_ms": sum(
                item["compilation_latency_ms"] for item in runs[:-1]
            ),
            "incremental_refresh_latency_ms": incremental_refresh_latency_ms,
            "successor_compilation_latency_ms": successor_run[
                "compilation_latency_ms"
            ],
            "withdrawal_refresh_latency_ms": withdrawal_refresh_latency_ms,
            "snapshot_restore_latency_ms": snapshot_restore_latency_ms,
            "transaction_success_rate": round(
                sum(item["transaction_status"] == "succeeded" for item in runs)
                / len(runs),
                6,
            ),
            "transition_success_rate": 1.0,
        },
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "recorded_at": recorded_at,
        "formal_release_evidence_ready": False,
        "competitive_claim_eligible": False,
    }
    status = "passed" if verification_valid and baseline_verified else "failed"
    report = {
        "schema_version": "deeplaw.deterministic-semantic-lifecycle/v1",
        "report_id": stable_id(
            "semanticdeterministic",
            gold["gold_id"],
            corpus["corpus_id"],
            canonical_json(binding),
            recorded_at,
        ),
        "status": status,
        **body,
    }
    schema = _load(
        _repository() / "contracts" / "deterministic-semantic-lifecycle.v1.schema.json"
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile and transition the frozen Semantic Gold without a model Provider."
    )
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--baseline-vault", type=Path, required=True)
    parser.add_argument("--deeplaw-command", type=Path, required=True)
    parser.add_argument(
        "--binding-repository",
        type=Path,
        help="Clean repository whose exact runtime commit produced this compiler wheel.",
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.output.exists() or arguments.output.is_symlink():
        raise FileExistsError("deterministic lifecycle output must be a new file")
    report = run(
        gold=_load(arguments.gold),
        corpus=_load(arguments.corpus),
        vault=arguments.vault,
        baseline_vault=arguments.baseline_vault,
        command=_load(arguments.deeplaw_command),
        binding_repository=arguments.binding_repository,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    print(canonical_json(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
