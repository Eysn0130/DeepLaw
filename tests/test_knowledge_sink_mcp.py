from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from deeplaw.compilation import compiler_profile
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    create_autonomous_snapshot,
    initialize_autonomous_core,
    restore_autonomous_snapshot,
    verify_autonomous_snapshot,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_inbox import submit_inbox_artifact
from deeplaw.knowledge_mcp_server import (
    handle_knowledge_support,
    knowledge_tool_definition,
)
from deeplaw.knowledge_sink_mcp_server import (
    handle_knowledge_sink,
    knowledge_sink_tool_definition,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes


def _ready(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="autonomous-mcp", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="codex-agent",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )["grant_id"]
        store.record_run(
            grant_id=grant_id,
            idempotency_key="test-run-record",
            run_id="run-test-1",
            task="Exercise the autonomous Knowledge Sink contract.",
            host_id="pytest",
            model_id="test-model",
            status="succeeded",
            scope="project",
            sensitivity="public",
            confirm_no_case_data=True,
        )
    return root, grant_id


def _remember_request(key: str, *, title: str = "Admission boundary") -> dict[str, object]:
    return {
        "operation": "remember",
        "idempotency_key": key,
        "confirm_no_case_data": True,
        "title": title,
        "body": "Discovery and ranking never establish authority.",
        "kind": "claim",
        "scope": "project",
        "sensitivity": "private",
        "run_id": "run-test-1",
        "model_id": "test-model",
        "tool_id": "pytest",
    }


def _record_test_run(root: Path, grant_id: str, run_id: str) -> None:
    handle_knowledge_sink(
        {
            "operation": "record_run",
            "idempotency_key": f"record:{run_id}",
            "confirm_no_case_data": True,
            "run_id": run_id,
            "task": f"Execute test task {run_id}.",
            "host_id": "pytest",
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
        },
        grant_id=grant_id,
        vault_path=root,
    )


def test_sink_is_separate_closed_write_tool_and_support_stays_read_only(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    sink = knowledge_sink_tool_definition()
    support = knowledge_tool_definition(autonomous=True)

    assert sink.name == "knowledge_sink"
    assert sink.annotations.readOnlyHint is False
    assert sink.annotations.destructiveHint is True
    assert sink.inputSchema["additionalProperties"] is False
    assert support.name == "knowledge_support"
    assert support.annotations.readOnlyHint is True
    support_schema = canonical_json(support.inputSchema)
    assert '"remember"' not in support_schema
    assert '"add_relation"' not in support_schema
    validator = Draft202012Validator(
        sink.inputSchema,
        format_checker=FormatChecker(),
    )
    validator.validate(_remember_request("sink-contract"))
    unbound_claim = _remember_request("unbound-claim")
    unbound_claim.pop("run_id")
    assert list(validator.iter_errors(unbound_claim))
    assert list(
        validator.iter_errors(
            {**_remember_request("unknown-field"), "arbitrary_path": "/tmp/escape"}
        )
    )
    assert list(
        validator.iter_errors(
            {**_remember_request("operation-kind-bypass"), "kind": "concept"}
        )
    )

    response = handle_knowledge_sink(
        _remember_request("sink-write"),
        grant_id=grant_id,
        vault_path=root,
    )

    assert response["result"]["lifecycle"] == "active"
    assert response["result"]["verification"] == "run_bound"
    assert response["boundary"] == {
        "legal_authority": False,
        "official_or_private_legal_mutation": False,
        "authority_elevation": False,
        "audit_deletion": False,
        "arbitrary_paths": False,
        "case_data_allowed": False,
        "scope_bound": True,
    }
    output_validator = Draft202012Validator(
        sink.outputSchema,
        format_checker=FormatChecker(),
    )
    output_validator.validate(response)
    assert list(
        output_validator.iter_errors(
            {
                **response,
                "result": {**response["result"], "arbitrary_path": "/tmp/escape"},
            }
        )
    )
    assert len(canonical_json(response).encode("utf-8")) <= 65_536


def test_knowledge_support_v5_extends_without_mutating_frozen_v2_to_v4() -> None:
    repository = Path(__file__).resolve().parents[1]
    v2 = json.loads(
        (repository / "contracts/knowledge-support.input.v2.schema.json").read_text()
    )
    v3 = json.loads(
        (repository / "contracts/knowledge-support.input.v3.schema.json").read_text()
    )
    v4 = json.loads(
        (repository / "contracts/knowledge-support.input.v4.schema.json").read_text()
    )

    v2_operations = set(v2["properties"]["operation"]["enum"])
    v3_operations = set(v3["properties"]["operation"]["enum"])
    assert {"identity_lookup", "gaps"}.isdisjoint(v2_operations)
    assert v3_operations == v2_operations | {"identity_lookup", "gaps"}
    assert set(v4["properties"]["operation"]["enum"]) == v3_operations | {
        "query",
        "compilation",
    }
    assert knowledge_tool_definition(autonomous=True).inputSchema["$id"].endswith(
        "knowledge-support.input.v5.schema.json"
    )
    purpose_context = {
        "operation": "context",
        "task": "Quote the exact governed statement.",
        "confirm_no_case_data": True,
        "purpose": "quote",
        "policy": "evidence-first-v1",
    }
    Draft202012Validator(
        knowledge_tool_definition(autonomous=True).inputSchema
    ).validate(purpose_context)
    assert list(Draft202012Validator(v4).iter_errors(purpose_context))


def test_read_support_fails_closed_on_local_paths_and_secret_like_content(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    local_path = handle_knowledge_sink(
        {
            **_remember_request("local-path-output", title="Local path boundary"),
            "body": "The private input was stored at /Users/example/private/project.md.",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    secret = handle_knowledge_sink(
        {
            **_remember_request("secret-output", title="Secret boundary"),
            "body": "The accidental value was api_key=abcdefghijklmnopqrstuvwx.",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]

    with pytest.raises(PermissionError, match="local absolute path"):
        handle_knowledge_support(
            operation="get",
            knowledge_id=local_path["knowledge_id"],
            plane="autonomous",
            vault_path=root,
        )
    with pytest.raises(PermissionError, match="secret-like material"):
        handle_knowledge_support(
            operation="get",
            knowledge_id=secret["knowledge_id"],
            plane="autonomous",
            vault_path=root,
        )


def test_agent_interfaces_default_to_the_selected_vault_scope(tmp_path: Path) -> None:
    root = tmp_path / "personal-vault"
    initialize_knowledge_vault(root, name="personal-default", scope="personal")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="personal-agent",
            allowed_scope="personal",
        )["grant_id"]
    request = _remember_request("personal-default-scope", title="Personal default scope")
    request.pop("scope")
    request.pop("run_id")
    request["kind"] = "decision"

    written = handle_knowledge_sink(
        request,
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    recalled = handle_knowledge_support(
        operation="recall",
        query="Personal default scope",
        plane="autonomous",
        vault_path=root,
    )["result"]

    assert written["scope"] == "personal"
    assert recalled["autonomous"]["results"][0]["knowledge_id"] == written["knowledge_id"]


def test_autonomous_read_support_exposes_federated_partitions_lineage_and_graph(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        evaluator_grant_id = store.enable_grant(
            writer_id="external-evaluator",
            operations=("record_feedback",),
            evaluator_types=("external_check",),
        )["grant_id"]
    _record_test_run(root, grant_id, "evaluation-run-1")
    first = handle_knowledge_sink(
        _remember_request("first"),
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    second_request = {
        **_remember_request("second", title="Context compiler"),
        "operation": "upsert_concept",
        "body": "A capsule selects small complete context under hard budgets.",
    }
    second_request.pop("kind")
    second = handle_knowledge_sink(
        second_request,
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    future = handle_knowledge_sink(
        {
            **_remember_request("future-exact-read", title="Future exact read"),
            "body": "This revision is not admitted before its valid-time interval begins.",
            "valid_from": "2099-01-01T00:00:00Z",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    with pytest.raises(KeyError, match="admitted scope"):
        handle_knowledge_support(
            operation="get",
            knowledge_id=future["knowledge_id"],
            vault_path=root,
        )
    relation = handle_knowledge_sink(
        {
            "operation": "add_relation",
            "idempotency_key": "link-first-second",
            "confirm_no_case_data": True,
            "subject_knowledge_id": first["knowledge_id"],
            "predicate": "related_to",
            "object_knowledge_id": second["knowledge_id"],
            "evidence_refs": [{"revision_id": first["revision_id"]}],
        },
        grant_id=grant_id,
        vault_path=root,
    )
    assert relation["result"]["lifecycle"] == "active"
    relation_update = {
        "operation": "add_relation",
        "idempotency_key": "update-link-first-second",
        "confirm_no_case_data": True,
        "subject_knowledge_id": first["knowledge_id"],
        "predicate": "related_to",
        "object_knowledge_id": second["knowledge_id"],
        "evidence_refs": [{"revision_id": first["revision_id"]}],
    }
    with pytest.raises(RuntimeError, match="relation compare-and-swap"):
        handle_knowledge_sink(
            relation_update,
            grant_id=grant_id,
            vault_path=root,
        )
    relation_update["expected_relation_revision_id"] = relation["result"][
        "relation_revision_id"
    ]
    relation = handle_knowledge_sink(
        relation_update,
        grant_id=grant_id,
        vault_path=root,
    )
    assert relation["result"]["parent_revision_id"] == relation_update[
        "expected_relation_revision_id"
    ]
    feedback_request = {
        "operation": "record_feedback",
        "idempotency_key": "feedback-first",
        "confirm_no_case_data": True,
        "knowledge_id": first["knowledge_id"],
        "expected_revision_id": first["revision_id"],
        "run_id": "evaluation-run-1",
        "outcome": "helpful",
        "evaluator_type": "external_check",
        "feedback_note": "The selected claim directly supported the task.",
    }
    with pytest.raises(PermissionError, match="evaluator type"):
        handle_knowledge_sink(
            feedback_request,
            grant_id=grant_id,
            vault_path=root,
        )
    feedback = handle_knowledge_sink(
        feedback_request,
        grant_id=evaluator_grant_id,
        vault_path=root,
    )
    assert feedback["result"]["task_success_authority"] == "external_evidence"

    search = handle_knowledge_support(
        operation="search",
        query="authority ranking",
        vault_path=root,
    )
    exact = handle_knowledge_support(
        operation="get",
        knowledge_id=first["knowledge_id"],
        vault_path=root,
    )
    lineage = handle_knowledge_support(
        operation="lineage",
        knowledge_id=first["knowledge_id"],
        vault_path=root,
    )
    graph = handle_knowledge_support(
        operation="graph",
        knowledge_id=first["knowledge_id"],
        vault_path=root,
    )
    wiki = handle_knowledge_support(
        operation="wiki_lookup",
        query="context compiler",
        vault_path=root,
    )
    explanation = handle_knowledge_support(
        operation="explain",
        query="authority ranking",
        vault_path=root,
    )
    capsule = handle_knowledge_support(
        operation="context",
        task="Explain the authority and context boundaries",
        confirm_no_case_data=True,
        vault_path=root,
    )
    inspection = handle_knowledge_support(operation="inspect", vault_path=root)
    verification = handle_knowledge_support(operation="verify", vault_path=root)

    assert search["schema_version"] == "deeplaw.knowledge-support-output/v3"
    assert search["result"]["autonomous"]["results"][0]["authority"] == ("agent_derived")
    assert search["result"]["source_derived"]["results"] == []
    assert exact["result"]["knowledge_id"] == first["knowledge_id"]
    assert lineage["result"]["current_revision_id"] == first["revision_id"]
    assert graph["result"]["relations"][0]["predicate"] == "related_to"
    assert wiki["result"]["living_wiki"]["derived_navigation_only"] is True
    assert explanation["result"]["authority_changed_by_ranking"] is False
    assert explanation["result"]["autonomous"]["query_plan_sha256"]
    assert capsule["result"]["sections"]["agent_derived_knowledge"]
    assert capsule["result"]["sections"]["official_evidence"] == []
    assert capsule["result"]["capsule_digest"]
    assert inspection["result"]["agent_ready"] is True
    assert inspection["result"]["autonomous"]["counts"]["feedback_events"] == 1
    assert verification["result"]["valid"] is True
    schema = knowledge_tool_definition(autonomous=True).outputSchema
    for response in (
        search,
        exact,
        lineage,
        graph,
        wiki,
        explanation,
        capsule,
        inspection,
        verification,
    ):
        Draft202012Validator(schema).validate(response)
        assert len(canonical_json(response).encode("utf-8")) <= 65_536


def test_source_derived_context_does_not_probe_or_report_autonomous_candidates(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    handle_knowledge_sink(
        {
            **_remember_request("excluded-autonomous-context", title="Private memory canary"),
            "body": "This autonomous canary must not enter a source-derived-only query plan.",
        },
        grant_id=grant_id,
        vault_path=root,
    )
    source = tmp_path / "source-boundary.md"
    source.write_text(
        "# Source boundary\n"
        "A source-derived-only capsule preserves authority partition boundaries.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        ingested = compile_source(
            vault,
            source,
            source_kind="document",
            sensitivity="private",
            confirm_no_case_data=True,
        )
        vault.approve_asset(ingested["asset_ids"][0], confirm_reviewed=True)
    # A writable open imports the new immutable source revision and advances the
    # autonomous ledger's bound legacy audit head before the read-only MCP snapshot.
    with AutonomousKnowledgeStore(root, read_only=False):
        pass

    capsule = handle_knowledge_support(
        operation="context",
        task="Explain the source authority partition boundary",
        plane="source_derived",
        confirm_no_case_data=True,
        vault_path=root,
    )["result"]

    assert capsule["sections"]["source_derived_knowledge"]
    assert capsule["sections"]["agent_derived_knowledge"] == []
    assert capsule["sections"]["agent_memory"] == []
    assert capsule["query_plan"]["candidate_count"] == 0
    assert capsule["query_plan"]["channels"] == []
    assert capsule["query_plan"]["budget"]["items"] == 0
    assert capsule["query_plan"]["budget"]["characters"] == 0
    assert capsule["budget"]["partitions"]["autonomous"] == {
        "items": 0,
        "characters": 0,
    }
    assert "autonomous plane was excluded" in " ".join(
        capsule["sections"]["limitations"]
    ).lower()


def test_federated_kind_filters_route_only_to_compatible_planes(tmp_path: Path) -> None:
    root, grant_id = _ready(tmp_path)
    concept_request = {
        **_remember_request("autonomous-kind", title="Autonomous concept boundary"),
        "operation": "upsert_concept",
        "body": "An autonomous concept belongs only to the Agent-derived namespace.",
    }
    concept_request.pop("kind")
    concept = handle_knowledge_sink(
        concept_request,
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Source-derived fact boundary",
            statement="A legacy fact belongs only to the source-derived namespace.",
        )
        fact = vault.approve_asset(proposal.asset_id, confirm_reviewed=True)
    with AutonomousKnowledgeStore(root, read_only=False):
        pass

    validator = Draft202012Validator(
        knowledge_tool_definition(autonomous=True).inputSchema,
        format_checker=FormatChecker(),
    )
    validator.validate(
        {"operation": "recall", "plane": "all", "query": "boundary", "kinds": ["fact"]}
    )
    validator.validate(
        {
            "operation": "recall",
            "plane": "autonomous",
            "query": "boundary",
            "kinds": ["concept"],
        }
    )
    assert list(
        validator.iter_errors(
            {
                "operation": "recall",
                "plane": "autonomous",
                "query": "boundary",
                "kinds": ["fact"],
            }
        )
    )
    assert list(
        validator.iter_errors(
            {
                "operation": "recall",
                "plane": "all",
                "query": "boundary",
                "kinds": ["concept"],
                "memory_tiers": ["project"],
            }
        )
    )
    assert list(
        validator.iter_errors(
            {
                "operation": "recall",
                "plane": "source_derived",
                "query": "boundary",
                "kinds": ["concept"],
            }
        )
    )
    assert list(
        validator.iter_errors(
            {
                "operation": "recall",
                "plane": "autonomous",
                "query": "boundary",
                "memory_tiers": ["project"],
            }
        )
    )

    source_only = handle_knowledge_support(
        operation="recall",
        query="Source-derived fact boundary",
        plane="all",
        kinds=["fact"],
        limit=2,
        max_chars=1_000,
        vault_path=root,
    )["result"]
    assert source_only["autonomous"] is None
    assert source_only["source_derived"]["results"][0]["asset_id"] == fact.asset_id
    assert source_only["source_derived"]["query_plan"]["filters"] == {
        "kinds": ["fact"],
        "memory_tiers": [],
    }
    assert source_only["source_derived"]["query_plan_sha256"] == sha256_bytes(
        canonical_json(source_only["source_derived"]["query_plan"]).encode("utf-8")
    )
    assert source_only["budget"]["partitions"] == {
        "autonomous": {"items": 0, "characters": 0},
        "source_derived": {"items": 2, "characters": 1_000},
    }

    autonomous_only = handle_knowledge_support(
        operation="recall",
        query="Autonomous concept boundary",
        plane="all",
        kinds=["concept"],
        limit=2,
        max_chars=1_000,
        vault_path=root,
    )["result"]
    assert autonomous_only["source_derived"] is None
    assert autonomous_only["autonomous"]["results"][0]["knowledge_id"] == (
        concept["knowledge_id"]
    )
    assert autonomous_only["autonomous"]["query_plan"]["filters"] == {
        "kinds": ["concept"],
        "required_tags": [],
    }
    assert autonomous_only["budget"]["partitions"] == {
        "autonomous": {"items": 2, "characters": 1_000},
        "source_derived": {"items": 0, "characters": 0},
    }

    tier_only = handle_knowledge_support(
        operation="recall",
        query="Source-derived fact boundary",
        plane="all",
        memory_tiers=["project"],
        limit=2,
        max_chars=1_000,
        vault_path=root,
    )["result"]
    assert tier_only["autonomous"] is None
    assert tier_only["source_derived"]["query_plan"]["filters"] == {
        "kinds": [],
        "memory_tiers": ["project"],
    }

    source_capsule = handle_knowledge_support(
        operation="context",
        task="Explain the source-derived fact boundary",
        plane="all",
        kinds=["fact"],
        limit=2,
        max_chars=1_000,
        confirm_no_case_data=True,
        vault_path=root,
    )["result"]
    assert source_capsule["sections"]["agent_derived_knowledge"] == []
    assert source_capsule["sections"]["agent_memory"] == []
    assert source_capsule["query_plan"]["source_derived"]["filters"] == {
        "kinds": ["fact"],
        "memory_tiers": [],
    }

    autonomous_capsule = handle_knowledge_support(
        operation="context",
        task="Explain the autonomous concept boundary",
        plane="all",
        kinds=["concept"],
        limit=2,
        max_chars=1_000,
        confirm_no_case_data=True,
        vault_path=root,
    )["result"]
    assert autonomous_capsule["sections"]["source_derived_knowledge"] == []
    assert autonomous_capsule["query_plan"]["filters"] == {
        "kinds": ["concept"],
    }


def test_source_only_recall_does_not_verify_the_excluded_autonomous_plane(
    tmp_path: Path,
) -> None:
    root, _ = _ready(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Independent source partition",
            statement="The source-derived partition has an independent integrity gate.",
        )
        fact = vault.approve_asset(proposal.asset_id, confirm_reviewed=True)
    with AutonomousKnowledgeStore(root, read_only=False):
        pass
    connection = sqlite3.connect(root / ".deeplaw" / "ledger.sqlite3")
    try:
        connection.execute(
            "UPDATE autonomous_events_v3 SET payload_json = ? WHERE sequence = 1",
            ('{"tampered":true}',),
        )
        connection.commit()
    finally:
        connection.close()

    source_only = handle_knowledge_support(
        operation="recall",
        query="Independent source partition",
        plane="source_derived",
        kinds=["fact"],
        vault_path=root,
    )["result"]
    assert source_only["source_derived"]["results"][0]["asset_id"] == fact.asset_id
    assert source_only["autonomous"] is None
    with pytest.raises(RuntimeError, match="integrity is invalid"):
        handle_knowledge_support(
            operation="recall",
            query="Independent source partition",
            plane="autonomous",
            vault_path=root,
        )


def test_exact_duplicate_is_collapsed_and_unknown_provenance_is_quarantined(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    original = handle_knowledge_sink(
        _remember_request("original"),
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    duplicate = handle_knowledge_sink(
        _remember_request("duplicate"),
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert duplicate["deduplicated"] is True
    assert duplicate["knowledge_id"] == original["knowledge_id"]
    assert duplicate["revision_id"] == original["revision_id"]

    quarantined = handle_knowledge_sink(
        {
            **_remember_request("fake-source", title="Unknown source"),
            "body": "This statement names a source that is not in the evidence ledger.",
            "source_refs": [
                {
                    "source_revision_id": "sourcerev_missing",
                    "locator": "section:1",
                }
            ],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert quarantined["lifecycle"] == "quarantined"
    assert quarantined["verification"] == "unverified"
    assert quarantined["quarantine_reasons"] == ["unverified_source_binding"]
    assert quarantined["current_revision_id"] is None


def test_skill_and_preference_contracts_preserve_lifecycle_and_statement_basis(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        evaluator_grant_id = store.enable_grant(
            writer_id="skill-external-evaluator",
            operations=("record_feedback",),
            evaluator_types=("external_check",),
        )["grant_id"]
    skill_manifest = {
        "purpose": "Compile one bounded knowledge context.",
        "applies_to": ["Explicit Knowledge OS context requests"],
        "does_not_apply_to": ["Legal adjudication", "Case data"],
        "invocation_mode": "model-invoked",
        "input_contract": {"type": "object"},
        "output_contract": {"type": "object"},
        "capabilities": ["knowledge_support.read"],
        "resource_limits": {"max_steps": 4, "max_output_bytes": 65536},
        "steps": [
            {
                "instruction": "Retrieve admitted candidates.",
                "completion_criterion": "A query-plan hash and admission reasons exist.",
            }
        ],
        "success_criteria": ["A bounded Capsule validates."],
        "failure_conditions": ["A restricted item is admitted."],
        "license": "Apache-2.0",
        "host_compatibility": ["Codex", "Claude Code", "OpenCode"],
        "verification_commands": ["uv run pytest tests/test_knowledge_sink_mcp.py -q"],
        "known_limitations": ["No legal adjudication."],
        "lifecycle": "draft",
        "source_revision_ids": [],
        "evaluation_run_ids": [],
        "supersedes_skill_revision": None,
        "deprecation_reason": None,
    }
    for run_id in (
        "skill-factory-run",
        "user-statement-run",
        "skill-eval-run-1",
        "skill-factory-promotion-run",
    ):
        _record_test_run(root, grant_id, run_id)
    skill = handle_knowledge_sink(
        {
            "operation": "save_skill",
            "idempotency_key": "skill-draft",
            "confirm_no_case_data": True,
            "title": "Compile bounded knowledge context",
            "body": "Use the ordered contract and stop when its checks pass.",
            "skill_manifest": skill_manifest,
            "run_id": "skill-factory-run",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    preference = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "direct-preference",
            "confirm_no_case_data": True,
            "title": "Communication preference",
            "body": "Use concise Chinese status reports.",
            "kind": "preference",
            "preference_basis": "direct_user_statement",
            "run_id": "user-statement-run",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.get_current(skill["knowledge_id"])["metadata"]["skill_manifest"]["lifecycle"]
            == "draft"
        )
        assert (
            store.get_current(preference["knowledge_id"])["metadata"]["preference_basis"]
            == "direct_user_statement"
        )
    with pytest.raises(ValueError, match="Skill scope and sensitivity"):
        handle_knowledge_sink(
            {
                "operation": "save_skill",
                "idempotency_key": "public-skill-private-source",
                "confirm_no_case_data": True,
                "title": "Invalid cross-sensitivity skill",
                "body": "A public Skill must not disclose a private source revision.",
                "sensitivity": "public",
                "skill_manifest": {
                    **skill_manifest,
                    "source_revision_ids": [skill["revision_id"]],
                },
            },
            grant_id=grant_id,
            vault_path=root,
        )
    with pytest.raises(ValueError, match="owner-only capability"):
        handle_knowledge_sink(
            {
                "operation": "save_skill",
                "idempotency_key": "dangerous-skill",
                "confirm_no_case_data": True,
                "title": "Dangerous skill",
                "body": "This model-invoked skill must be rejected.",
                "skill_manifest": {
                    **skill_manifest,
                    "capabilities": ["sign"],
                },
            },
            grant_id=grant_id,
            vault_path=root,
        )

    feedback = handle_knowledge_sink(
        {
            "operation": "record_feedback",
            "idempotency_key": "skill-external-evaluation",
            "confirm_no_case_data": True,
            "knowledge_id": skill["knowledge_id"],
            "expected_revision_id": skill["revision_id"],
            "run_id": "skill-eval-run-1",
            "outcome": "helpful",
            "evaluator_type": "external_check",
            "feedback_note": "The process satisfied its bounded completion criteria.",
        },
        grant_id=evaluator_grant_id,
        vault_path=root,
    )["result"]
    assert feedback["task_success_authority"] == "external_evidence"
    promoted_manifest = {
        **skill_manifest,
        "lifecycle": "promoted",
        "evaluation_run_ids": ["skill-eval-run-1"],
        "supersedes_skill_revision": skill["revision_id"],
    }
    with pytest.raises(ValueError, match="externally bound"):
        handle_knowledge_sink(
            {
                "operation": "save_skill",
                "idempotency_key": "skill-unbound-promotion",
                "confirm_no_case_data": True,
                "title": "Compile bounded knowledge context",
                "body": "This attempted promotion cites an unknown evaluation.",
                "knowledge_id": skill["knowledge_id"],
                "expected_revision_id": skill["revision_id"],
                "skill_manifest": {
                    **promoted_manifest,
                    "evaluation_run_ids": ["missing-eval-run"],
                },
            },
            grant_id=grant_id,
            vault_path=root,
        )
    promoted = handle_knowledge_sink(
        {
            "operation": "save_skill",
            "idempotency_key": "skill-promoted",
            "confirm_no_case_data": True,
            "title": "Compile bounded knowledge context",
            "body": "Use the externally evaluated process and its completion checks.",
            "knowledge_id": skill["knowledge_id"],
            "expected_revision_id": skill["revision_id"],
            "skill_manifest": promoted_manifest,
            "run_id": "skill-factory-promotion-run",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        current = store.get_current(skill["knowledge_id"])
        assert current["revision_id"] == promoted["revision_id"]
        assert current["metadata"]["skill_manifest"]["lifecycle"] == "promoted"
        assert store.verify()["valid"] is True
    exact_skill = handle_knowledge_support(
        operation="get",
        knowledge_id=skill["knowledge_id"],
        plane="autonomous",
        vault_path=root,
    )
    bounded_manifest = exact_skill["result"]["metadata"]["skill_manifest"]
    assert bounded_manifest["lifecycle"] == "promoted"
    assert bounded_manifest["canonical_manifest_omitted"] is True
    assert len(canonical_json(exact_skill).encode("utf-8")) <= 65_536


def test_mcp_budgets_temporal_reads_and_scope_admission_are_closed(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    public = handle_knowledge_sink(
        {
            **_remember_request("public-boundary", title="Public temporal alpha"),
            "body": "Alpha budget boundary is public.",
            "sensitivity": "public",
            "semantic_key": "boundary.alpha",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    private = handle_knowledge_sink(
        {
            **_remember_request("private-boundary", title="Private temporal alpha"),
            "body": "Alpha private material must remain outside public admission.",
            "sensitivity": "private",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    current = handle_knowledge_sink(
        {
            **_remember_request("public-update", title="Public temporal beta"),
            "body": "Beta replaces alpha after the recorded transaction.",
            "knowledge_id": public["knowledge_id"],
            "expected_revision_id": public["revision_id"],
            "sensitivity": "public",
            "semantic_key": "boundary.beta",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]

    public_recall = handle_knowledge_support(
        operation="recall",
        query="boundary",
        plane="autonomous",
        scope="project",
        max_sensitivity="public",
        limit=1,
        max_chars=200,
        vault_path=root,
    )["result"]
    assert public_recall["budget"] == {
        "max_items": 1,
        "selected_items": 1,
        "max_characters": 200,
        "selected_characters": public_recall["autonomous"]["budget"]["selected_characters"],
        "partitions": {
            "autonomous": {"items": 1, "characters": 200},
            "source_derived": {"items": 0, "characters": 0},
        },
    }
    assert public_recall["autonomous"]["results"][0]["knowledge_id"] == public["knowledge_id"]
    assert public_recall["autonomous"]["query_plan"]["candidate_count"] == 1
    assert public_recall["budget"]["selected_items"] <= 1
    assert public_recall["budget"]["selected_characters"] <= 200

    historical = handle_knowledge_support(
        operation="get",
        knowledge_id=public["knowledge_id"],
        as_of=public["recorded_at"],
        plane="autonomous",
        max_sensitivity="public",
        vault_path=root,
    )["result"]
    assert historical["revision_id"] == public["revision_id"]
    assert historical["semantic_key"] == "boundary.alpha"
    assert current["recorded_at"] > public["recorded_at"]
    historical_federated = handle_knowledge_support(
        operation="recall",
        query="boundary",
        as_of=public["recorded_at"],
        plane="all",
        max_sensitivity="public",
        vault_path=root,
    )["result"]
    assert historical_federated["source_derived"]["results"] == []
    assert historical_federated["source_derived"]["ranking"]["method"] == (
        "historical_source_derived_unavailable"
    )
    with pytest.raises(ValueError, match="do not support historical"):
        handle_knowledge_support(
            operation="get",
            asset_id="asset_000000000000000000000000",
            as_of=public["recorded_at"],
            plane="source_derived",
            vault_path=root,
        )
    with pytest.raises(KeyError, match="admitted scope"):
        handle_knowledge_support(
            operation="get",
            knowledge_id=private["knowledge_id"],
            plane="autonomous",
            max_sensitivity="public",
            vault_path=root,
        )
    inspection = handle_knowledge_support(
        operation="inspect",
        plane="autonomous",
        max_sensitivity="public",
        vault_path=root,
    )["result"]
    assert inspection["source_derived"] is None
    assert inspection["autonomous"]["counts"]["active_knowledge"] == 1
    assert "content_object_count" not in inspection["autonomous"]["verification"]


def test_tool_contracts_reject_irrelevant_fields_and_invalid_advertisement(
    tmp_path: Path,
) -> None:
    root, grant_id = _ready(tmp_path)
    support_validator = Draft202012Validator(
        knowledge_tool_definition(autonomous=True).inputSchema,
        format_checker=FormatChecker(),
    )
    assert list(
        support_validator.iter_errors({"operation": "inspect", "query": "ignored-but-forbidden"})
    )
    assert list(
        support_validator.iter_errors(
            {
                "operation": "get",
                "asset_id": "asset_" + "a" * 24,
                "knowledge_id": "knowledge_" + "b" * 24,
            }
        )
    )
    sink_validator = Draft202012Validator(
        knowledge_sink_tool_definition().inputSchema,
        format_checker=FormatChecker(),
    )
    assert list(
        sink_validator.iter_errors(
            {
                "operation": "add_relation",
                "idempotency_key": "closed-fields",
                "confirm_no_case_data": True,
                "subject_knowledge_id": "knowledge_" + "a" * 24,
                "predicate": "supports",
                "object_knowledge_id": "knowledge_" + "b" * 24,
                "title": "irrelevant known field",
            }
        )
    )
    assert list(
        sink_validator.iter_errors(
            {
                **_remember_request("locator-only-source-reference"),
                "source_refs": [{"locator": "section:1"}],
            }
        )
    )
    assert list(
        sink_validator.iter_errors(
            {
                **_remember_request("mixed-source-reference-identities"),
                "source_refs": [
                    {
                        "revision_id": "knowledgerev_" + "a" * 24,
                        "artifact_id": "inbox_" + "b" * 24,
                    }
                ],
            }
        )
    )
    working_memory = {
        **_remember_request("working-memory-requires-expiry"),
        "kind": "memory",
        "memory_type": "working",
    }
    assert list(sink_validator.iter_errors(working_memory))
    sink_validator.validate(
        {**working_memory, "expires_at": "2099-01-01T00:00:00Z"}
    )
    narrowed = knowledge_sink_tool_definition(operations=("remember", "expire"))
    assert narrowed.inputSchema["properties"]["operation"]["enum"] == [
        "remember",
        "expire",
    ]
    with pytest.raises(ValueError, match="advertised operations"):
        knowledge_sink_tool_definition(operations=())
    evaluator_narrowed = knowledge_sink_tool_definition(evaluator_types=("agent_self_report",))
    assert evaluator_narrowed.inputSchema["properties"]["evaluator_type"]["enum"] == [
        "agent_self_report"
    ]
    with pytest.raises(ValueError, match="advertised evaluator types"):
        knowledge_sink_tool_definition(evaluator_types=())
    with pytest.raises(ValueError, match="mutually exclusive"):
        handle_knowledge_support(
            operation="verify",
            asset_id="asset_" + "a" * 24,
            knowledge_id="knowledge_" + "b" * 24,
            vault_path=root,
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.grant_status(grant_id)["operations"]
        assert store.grant_status(grant_id)["evaluator_types"] == ["agent_self_report"]


def test_autonomous_snapshot_round_trip_restores_canonical_planes(tmp_path: Path) -> None:
    root, grant_id = _ready(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        run_artifact = submit_inbox_artifact(
            vault,
            artifact_type="run",
            payload={
                "capsule_id": "capsule_snapshot",
                "capsule_digest": "b" * 64,
                "status": "succeeded",
                "host": "pytest",
            },
            producer_name="pytest",
            producer_version="1",
            sensitivity="private",
            confirm_no_case_data=True,
        )
    first = handle_knowledge_sink(
        {
            **_remember_request("snapshot-first"),
            "source_refs": [{"artifact_id": run_artifact["artifact_id"]}],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    operations = root / "operations"
    operations.mkdir(mode=0o700, exist_ok=True)
    operator_record = operations / "snapshot-state.json"
    operator_record.write_text('{"state":"retained"}\n', encoding="utf-8")
    operator_record.chmod(0o600)
    snapshot = tmp_path / "snapshot"

    created = create_autonomous_snapshot(root, snapshot)

    assert created["valid"] is True
    assert created["operator_state_included"] is True
    assert created["derived_layers_included"] is False
    assert list((snapshot / "vault" / "inbox" / "pending").glob("*.dlrun"))
    assert (snapshot / "vault" / "operations" / operator_record.name).is_file()
    assert verify_autonomous_snapshot(snapshot)["valid"] is True
    snapshot_without_operator_state = tmp_path / "snapshot-no-operator-state"
    excluded = create_autonomous_snapshot(
        root,
        snapshot_without_operator_state,
        include_operator_state=False,
    )
    assert excluded["operator_state_included"] is False
    assert not (snapshot_without_operator_state / "vault" / "operations").exists()
    assert verify_autonomous_snapshot(snapshot_without_operator_state)["valid"] is True
    undeclared_operator = (
        snapshot_without_operator_state / "vault" / "operations" / "injected.json"
    )
    undeclared_operator.parent.mkdir(mode=0o700)
    undeclared_payload = b"{}\n"
    undeclared_operator.write_bytes(undeclared_payload)
    undeclared_manifest_path = snapshot_without_operator_state / "snapshot.json"
    undeclared_manifest = json.loads(undeclared_manifest_path.read_text(encoding="utf-8"))
    undeclared_manifest["inventory"].append(
        {
            "path": "operations/injected.json",
            "byte_size": len(undeclared_payload),
            "sha256": sha256_bytes(undeclared_payload),
        }
    )
    undeclared_manifest["inventory"] = sorted(
        undeclared_manifest["inventory"], key=lambda item: item["path"]
    )
    undeclared_manifest["file_count"] = len(undeclared_manifest["inventory"])
    undeclared_manifest["inventory_sha256"] = sha256_bytes(
        canonical_json(undeclared_manifest["inventory"]).encode("utf-8")
    )
    undeclared_body = {
        key: value
        for key, value in undeclared_manifest.items()
        if key != "snapshot_sha256"
    }
    undeclared_manifest["snapshot_sha256"] = sha256_bytes(
        canonical_json(undeclared_body).encode("utf-8")
    )
    undeclared_manifest_path.write_text(
        json.dumps(undeclared_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert verify_autonomous_snapshot(snapshot_without_operator_state)["valid"] is False
    handle_knowledge_sink(
        {
            **_remember_request("snapshot-update", title="Admission boundary updated"),
            "body": "The current state changed after the snapshot.",
            "knowledge_id": first["knowledge_id"],
            "expected_revision_id": first["revision_id"],
        },
        grant_id=grant_id,
        vault_path=root,
    )

    restored = restore_autonomous_snapshot(
        root,
        snapshot=snapshot,
        confirm=True,
    )

    assert restored["restored"] is True
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.get_current(first["knowledge_id"])["body"] == (
            "Discovery and ranking never establish authority."
        )
        verification = store.verify()
        assert verification["valid"] is True
        assert verification["derived_ready"] is False
        assert {item["code"] for item in verification["warnings"]} == {
            "derived_manifest_stale",
            "derived_search_stale",
        }

    fresh = tmp_path / "fresh-restored-vault"
    fresh_restore = restore_autonomous_snapshot(
        fresh,
        snapshot=snapshot,
        confirm=True,
    )
    assert fresh_restore["retained_previous_vault"] is None
    with AutonomousKnowledgeStore(fresh, read_only=True) as store:
        assert store.get_current(first["knowledge_id"])["revision_id"] == first["revision_id"]
    with pytest.raises(ValueError, match="must not overlap"):
        restore_autonomous_snapshot(
            snapshot / "nested-restore",
            snapshot=snapshot,
            confirm=True,
        )

    manifest_path = snapshot / "snapshot.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["derived_layers_included"] = True
    body = {key: value for key, value in manifest.items() if key != "snapshot_sha256"}
    manifest["snapshot_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    assert verify_autonomous_snapshot(snapshot)["valid"] is False


def test_stdio_sink_exposes_only_the_explicit_mutation_leaf(tmp_path: Path) -> None:
    root, grant_id = _ready(tmp_path)
    source = tmp_path / "stdio-semantic-source.md"
    source.write_text(
        "# Stdio semantic source\nA real stdio call starts the governed compiler.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    source_revision_id = compiled["identity"]["source_revision_id"]
    profile = compiler_profile("living-wiki-agent", "2")
    with AutonomousKnowledgeStore(root, read_only=False):
        pass

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "deeplaw",
                "knowledge",
                "sink",
                "mcp",
                "--stdio",
                "--vault",
                str(root),
                "--grant-id",
                grant_id,
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["knowledge_sink"]
            advertised_schema = listed.tools[0].inputSchema
            v3_branch = advertised_schema["oneOf"][0]
            legacy_branch = v3_branch["oneOf"][0]
            assert legacy_branch["properties"]["evaluator_type"]["enum"] == [
                "agent_self_report"
            ]
            result = await session.call_tool(
                "knowledge_sink",
                _remember_request("stdio-write"),
            )
            assert result.isError is False
            semantic = await session.call_tool(
                "knowledge_sink",
                {
                    "operation": "begin_compilation",
                    "idempotency_key": "stdio-begin-semantic-v2",
                    "confirm_no_case_data": True,
                    "source_revision_id": source_revision_id,
                    "compiler_profile": "living-wiki-agent",
                    "compiler_profile_version": "2",
                    "host_identity": "pytest-stdio-agent",
                    "model_identity": "deterministic-test-model",
                    "prompt_template_id": profile["prompt_template_id"],
                    "prompt_config_sha256": profile["prompt_config_sha256"],
                    "plan_configuration_sha256": profile[
                        "plan_configuration_sha256"
                    ],
                    "packet_max_fragments": 8,
                },
            )
            assert semantic.isError is False

    asyncio.run(exercise())
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        run = store.connection.execute(
            """
            SELECT compiler_profile_version, host_identity, status
            FROM source_compilation_runs_v1
            WHERE source_revision_id = ?
            """,
            (source_revision_id,),
        ).fetchone()
        assert dict(run) == {
            "compiler_profile_version": "2",
            "host_identity": "pytest-stdio-agent",
            "status": "planned",
        }


def test_stdio_autonomous_support_exposes_v5_read_operations(tmp_path: Path) -> None:
    root, grant_id = _ready(tmp_path)
    handle_knowledge_sink(
        _remember_request("stdio-purpose-aware-context"),
        grant_id=grant_id,
        vault_path=root,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        vault_id = store.vault_id

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "deeplaw",
                "knowledge",
                "mcp",
                "--stdio",
                "--vault",
                str(root),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["knowledge_support"]
            assert listed.tools[0].inputSchema["$id"].endswith(
                "knowledge-support.input.v5.schema.json"
            )
            semantic = await session.call_tool(
                "knowledge_support",
                {"operation": "semantic", "semantic_action": "profile"},
            )
            sources = await session.call_tool(
                "knowledge_support",
                {"operation": "source", "source_action": "list"},
            )
            syntheses = await session.call_tool(
                "knowledge_support",
                {"operation": "synthesis", "synthesis_action": "coverage"},
            )
            purpose_context = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "context",
                    "task": "Quote the admission boundary.",
                    "confirm_no_case_data": True,
                    "purpose": "quote",
                    "policy": "evidence-first-v1",
                    "limit": 8,
                    "max_chars": 8_000,
                },
            )
            editor = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "editor_context",
                    "editor_context": {
                        "schema_version": "deeplaw.editor-context-envelope/v1",
                        "frontend": "obsidian",
                        "frontend_version": "test-1",
                        "vault_identity": vault_id,
                        "active_note": None,
                        "selected_text": None,
                        "selection_range": None,
                        "open_tabs": [],
                        "explicit_note_references": [],
                        "backlinks": [],
                        "outlinks": [],
                        "active_canvas": None,
                        "active_bases_view": None,
                        "user_intent": "Find governed knowledge.",
                        "persistence_allowed": False,
                        "scope": "project",
                        "max_sensitivity": "private",
                        "budgets": {
                            "max_notes": 5,
                            "max_context_characters": 2000,
                            "max_selected_characters": 500,
                            "max_provider_characters": 65536,
                        },
                        "confirm_no_case_data": True,
                    },
                },
            )
            assert semantic.isError is False
            assert sources.isError is False
            assert syntheses.isError is False
            assert purpose_context.isError is False
            assert purpose_context.structuredContent["result"]["query_plan"]["purpose"] == (
                "quote"
            )
            assert purpose_context.structuredContent["result"]["query_plan"]["policy_id"] == (
                "evidence-first-v1"
            )
            assert editor.isError is False
            assert editor.structuredContent["result"]["ephemeral_context"] is True
            assert editor.structuredContent["result"]["persistence_performed"] is False

    asyncio.run(exercise())
