from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import deeplaw.source_connectors as source_connectors
from benchmarks.baselines.registry import (
    REQUIRED_SYSTEM_IDS,
    freeze_candidate_registry,
    load_registry,
    registry_report,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_jobs import create_ingest_job
from deeplaw.knowledge_maintenance import create_knowledge_snapshot
from deeplaw.knowledge_markdown import export_knowledge_markdown
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval_fabric import retrieve
from deeplaw.review_workflow import transform_review_proposals

REPOSITORY = Path(__file__).resolve().parents[1]
CONTRACTS = REPOSITORY / "contracts"


def _schema(name: str) -> dict[str, object]:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    schema = _schema(name)
    Draft202012Validator.check_schema(schema)
    resources = []
    for path in CONTRACTS.glob("*.schema.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if "$id" in value:
            resources.append((value["$id"], Resource.from_contents(value)))
    return Draft202012Validator(schema, registry=Registry().with_resources(resources))


def _active_source(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v0.7 contracts", scope="project")
    source = tmp_path / "guide.md"
    source.write_text(
        "# Constraint\nThe release must preserve exact source evidence.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            typed_extraction="deterministic-v2",
        )
        manifest = vault.source_review_manifest(result["source"]["source_id"])
        vault.approve_source_assets(
            result["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        return root, result


def test_all_v070_json_schemas_are_valid() -> None:
    names = (
        "source-ir.v1.schema.json",
        "knowledge-identity.v2.schema.json",
        "knowledge-query-plan.v1.schema.json",
        "knowledge-retrieval-trace.v1.schema.json",
        "knowledge-inbox-artifact.v1.schema.json",
        "knowledge-ingest-job.v1.schema.json",
        "knowledge-ingest-job.v2.schema.json",
        "knowledge-lineage-review.v1.schema.json",
        "knowledge-review-transform.v1.schema.json",
        "source-snapshot.v1.schema.json",
        "knowledge-snapshot.v1.schema.json",
        "knowledge-projection.v2.schema.json",
        "baseline-adapter-registry.v1.schema.json",
        "baseline-evaluation-environment.v1.schema.json",
        "baseline-evidence-collection.v1.schema.json",
        "baseline-evidence-collection-report.v1.schema.json",
        "official-baseline-execution-plan.v2.schema.json",
        "official-baseline-execution-receipt.v2.schema.json",
        "official-baseline-resource-record.v1.schema.json",
        "manual-baseline-execution-plan.v1.schema.json",
        "manual-baseline-execution-receipt.v1.schema.json",
        "obsidian-manual-run.v1.schema.json",
        "document-engine-actual-pdf-diagnostic.v1.schema.json",
        "local-reranker-manifest.v1.schema.json",
        "retrieval-profile.v1.schema.json",
        "retrieval-regression-suite.v1.schema.json",
        "retrieval-fabric-scale-diagnostic.v1.schema.json",
        "skill-bundle.v1.schema.json",
        "reproducible-build-report.v1.schema.json",
        "reproducible-build-report.v2.schema.json",
        "commercial-release-manifest.v1.schema.json",
        "installed-license-inventory.v1.schema.json",
        "typed-compiler-benchmark.v1.schema.json",
        "typed-compiler-benchmark-input.v1.schema.json",
        "relation-carry-forward.v1.schema.json",
        "benchmark-corpus-commitment.v1.schema.json",
        "evaluator-model-manifest.v1.schema.json",
        "evaluator-retrieval-profile-commitment.v1.schema.json",
        "external-evaluator-kit.v1.schema.json",
        "external-evaluator-kit-attestation.v1.schema.json",
        "internal-baseline-gate.v1.schema.json",
        "codex-plugin-host-smoke.v1.schema.json",
    )
    for name in names:
        _validator(name)


def test_atomic_review_transform_matches_published_contract(tmp_path: Path) -> None:
    root, compiled = _active_source(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        transformed = transform_review_proposals(
            vault,
            action="edit",
            asset_ids=(compiled["asset_ids"][0],),
            reviewer_id="contract-reviewer",
            reason="The exact source-bound wording was reviewed before proposal creation.",
            confirm_reviewed=True,
            title="Reviewed source evidence",
            statement="Preserve the exact reviewed source evidence.",
        )
        assert vault.verify_integrity()["valid"] is True
    _validator("knowledge-review-transform.v1.schema.json").validate(transformed)


def test_source_identity_ir_query_plan_and_trace_match_contracts(tmp_path: Path) -> None:
    root, result = _active_source(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        source = vault.source_info(result["source"]["source_id"])
        identity = {
            "schema_version": "deeplaw.knowledge-identity/v2",
            "collection_id": source["collection_id"],
            "logical_path": source["logical_path"],
            "source_key": source["canonical_source_key"],
            "source_revision_id": source["source_revision_id"],
            "compilation_id": source["compilation_id"],
            "proposal_set_id": source["proposal_set_id"],
            "governance_revision": source["governance_revision"],
        }
        _validator("knowledge-identity.v2.schema.json").validate(identity)

        row = vault.connection.execute(
            "SELECT * FROM source_ir_nodes_v2 ORDER BY ordinal LIMIT 1"
        ).fetchone()
        assert row is not None
        node = vault._source_ir_row(row, include_text=True)
        _validator("source-ir.v1.schema.json").validate(node)

        retrieval = retrieve(vault, "exact source evidence", mode="lexical", explain=True)
        _validator("knowledge-query-plan.v1.schema.json").validate(
            retrieval["trace"]["query_plan"]
        )
        _validator("knowledge-retrieval-trace.v1.schema.json").validate(
            retrieval["trace"]
        )


def test_job_snapshot_projection_and_baseline_registry_match_contracts(
    tmp_path: Path,
) -> None:
    root, _ = _active_source(tmp_path)
    queued = tmp_path / "queued.md"
    queued.write_text("# Question\nWhich source is current?\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        job = create_ingest_job(vault, queued, register_for_sync=False)
        _validator("knowledge-ingest-job.v2.schema.json").validate(job)

    snapshot_path = tmp_path / "snapshot"
    projection_path = tmp_path / "projection"
    with KnowledgeVault(root, read_only=True) as vault:
        create_knowledge_snapshot(vault, snapshot_path)
        export_knowledge_markdown(vault, projection_path)
    snapshot = json.loads((snapshot_path / "snapshot.json").read_text(encoding="utf-8"))
    projection = json.loads((projection_path / "manifest.json").read_text(encoding="utf-8"))
    _validator("knowledge-snapshot.v1.schema.json").validate(snapshot)
    _validator("knowledge-projection.v2.schema.json").validate(projection)

    registry = load_registry()
    _validator("baseline-adapter-registry.v1.schema.json").validate(registry)
    assert {item["system_id"] for item in registry["systems"]} >= REQUIRED_SYSTEM_IDS
    report = registry_report(registry)
    assert report["claim_eligible"] is False
    assert report["ready_for_external_execution"] is False
    assert all(item["results_status"] == "pending_execution" for item in registry["systems"])


def test_external_registry_can_bind_a_frozen_candidate_without_claiming_results(
    tmp_path: Path,
) -> None:
    unreleased = load_registry()
    frozen = freeze_candidate_registry(
        unreleased,
        candidate_commit="a" * 40,
        reviewed_at="2026-07-28",
    )
    path = tmp_path / "frozen-registry.json"
    path.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    loaded = load_registry(path)
    _validator("baseline-adapter-registry.v1.schema.json").validate(loaded)
    report = registry_report(loaded)

    assert unreleased["candidate_line"] == "0.7.0-unreleased"
    assert report["ready_for_external_execution"] is True
    assert report["claim_eligible"] is False
    assert all(item["results_status"] == "pending_execution" for item in loaded["systems"])
    assert not any("not frozen" in blocker for blocker in report["blockers"])


def test_https_source_snapshot_matches_its_published_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="snapshot contract", scope="project")
    requested = "https://example.com/contract.md"
    content = b"# Contract\nThe source snapshot remains review-gated.\n"
    monkeypatch.setattr(
        source_connectors,
        "_download_https",
        lambda *_args, **_kwargs: (
            requested,
            content,
            "text/markdown",
            [requested],
            ["93.184.216.34"],
        ),
    )

    with KnowledgeVault(root, read_only=False) as vault:
        snapshot = source_connectors.capture_https_source(
            vault,
            requested,
            confirm_network=True,
        )
    stored = {key: value for key, value in snapshot.items() if key != "valid"}
    _validator("source-snapshot.v1.schema.json").validate(stored)
