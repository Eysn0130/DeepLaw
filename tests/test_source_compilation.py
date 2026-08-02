from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.hosts.deterministic_fake_agent import compile_with_fake_agent
from benchmarks.living_wiki.run_quality_gate import _score_case
from deeplaw.api import (
    KnowledgeOS,
    KnowledgeOSPermissionError,
    KnowledgeOSValidationError,
)
from deeplaw.backfill import BackfillService
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.compilation.models import (
    COMPILER_GRANT_OPERATIONS,
    MAX_PACKET_PROVIDER_BYTES,
    SEMANTIC_COMPILER_GRANT_OPERATIONS,
)
from deeplaw.compilation.profiles import REQUIRED_SEMANTIC_DUTIES, SEMANTIC_DUTIES
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.compilation.synthesis_refresh import SynthesisRefreshService
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    autonomous_core_installed,
    create_autonomous_snapshot,
    initialize_autonomous_core,
    migrate_autonomous_core,
    restore_autonomous_snapshot,
    rollback_autonomous_core,
    verify_autonomous_snapshot,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_mcp_server import (
    handle_knowledge_support,
    knowledge_tool_definition,
)
from deeplaw.knowledge_sink_mcp_server import (
    handle_knowledge_sink,
    knowledge_sink_tool_definition,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.retrieval.purpose import _policy_designator_conflicts, _policy_designators
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads


def _semantic_observation_plan(
    service: SemanticCompilationService,
    packet: dict,
) -> dict:
    observations = []
    for fragment in packet["fragments"]:
        observation = {
            "packet_id": packet["packet_id"],
            "semantic_key_candidate": "entity:deeplaw",
            "kind": "entity",
            "title_candidate": "DeepLaw",
            "body_candidate": "A source-bound entity observation.",
            "aliases": ["Deep Law"],
            "source_refs": [
                {
                    "source_revision_id": packet["source_revision_id"],
                    "fragment_id": fragment["fragment_id"],
                    "locator": fragment["locator"],
                    "quote_sha256": fragment["text_sha256"],
                }
            ],
            "assertion": None,
            "applicability": {
                "description": "This source revision.",
                "scopes": [],
                "conditions": [],
                "exclusions": [],
            },
            "tags": ["semantic-v2"],
            "reason": "Observe one cross-packet entity candidate.",
        }
        observation["observation_id"] = service.observation_id(
            compilation_run_id=packet["compilation_run_id"],
            packet_id=packet["packet_id"],
            observation=observation,
        )
        observations.append(observation)
    fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-observation-plan/v2",
        "compilation_run_id": packet["compilation_run_id"],
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "observations": observations,
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragments": [],
            "ratio": 1.0,
        },
        "warnings": [],
    }


def _ready_source(tmp_path: Path, *, section_count: int = 4) -> tuple[Path, dict, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="compiler", scope="project")
    initialize_autonomous_core(root)
    source = tmp_path / "source.md"
    source.write_text(
        "\n\n".join(
            f"# Section {index}\nDurable source statement {index}."
            for index in range(1, section_count + 1)
        ),
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deterministic-fake-agent",
            operations=COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    return root, compiled, grant_id


def _plan(packet: dict, *, expected_audit_head: str) -> dict:
    actions = []
    for fragment in packet["fragments"]:
        ordinal = fragment["ordinal"]
        actions.append(
            {
                "action": "create",
                "kind": "claim",
                "semantic_key": f"source-claim:{packet['source_revision_id']}:{ordinal}",
                "knowledge_id": None,
                "expected_revision_id": None,
                "title": f"Compiled source claim {ordinal}",
                "body": fragment["text"],
                "aliases": [],
                "epistemic_state": "supported",
                "source_refs": [
                    {
                        "source_revision_id": packet["source_revision_id"],
                        "fragment_id": fragment["fragment_id"],
                        "locator": fragment["locator"],
                        "quote_sha256": fragment["text_sha256"],
                    }
                ],
                "assertion": None,
                "tags": ["compiled"],
                "valid_from": None,
                "valid_to": None,
                "applicability": {
                    "description": "This source revision.",
                    "scopes": [],
                    "conditions": [],
                    "exclusions": [],
                },
                "synthesis_inputs": None,
                "reason": "Persist a reusable source-bound claim.",
            }
        )
    fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": expected_audit_head,
        "object_actions": actions,
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [],
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragment_ids": [],
            "ratio": 1.0,
            "completeness": "complete",
        },
        "skipped_fragments": [],
        "warnings": [],
    }


def _stage_all(
    coordinator: CompilationCoordinator,
    *,
    grant_id: str,
    begun: dict,
) -> None:
    while packet := coordinator.next_packet(begun["compilation_run_id"]):
        coordinator.stage(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            plan=_plan(packet, expected_audit_head=begun["input_audit_head"]),
            confirm_no_case_data=True,
        )


def _cli_json(*arguments: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def test_knowledge_cli_failure_is_stable_and_does_not_leak_paths(
    tmp_path: Path,
) -> None:
    root, _compiled, _grant_id = _ready_source(tmp_path, section_count=1)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "deeplaw",
            "knowledge",
            "--format",
            "json",
            "query",
            "--vault",
            str(root),
            "--query",
            "bounded failure",
            "--purpose",
            "answer",
            "--max-chars",
            "1",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == (
        "deeplaw: The Knowledge OS request does not match its public contract.\n"
    )
    assert "Traceback" not in result.stderr
    assert str(root) not in result.stderr
    assert str(Path(__file__).resolve().parents[1]) not in result.stderr


def test_semantic_relation_evidence_may_bind_current_endpoint_sources_only(
    tmp_path: Path,
) -> None:
    root, _compiled, _grant_id = _ready_source(tmp_path, section_count=1)
    coordinator = CompilationCoordinator(root)
    profile = KnowledgeOS.open(root).compilations.profile(version="2")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        semantic_grant = store.enable_grant(
            writer_id="relation-endpoint-evidence-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
        source_revision_id = store.connection.execute(
            "SELECT source_revision_id FROM source_revisions_v2 LIMIT 1"
        ).fetchone()["source_revision_id"]
    begun = coordinator.begin(
        grant_id=semantic_grant,
        source_revision_id=source_revision_id,
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version=profile["compiler_profile_version"],
        host_identity="relation-endpoint-evidence-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        confirm_no_case_data=True,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=begun["input_audit_head"])
    current_reference = plan["object_actions"][0]["source_refs"][0]
    prior_reference = {
        "source_revision_id": "sourcerev_" + "a" * 24,
        "fragment_id": "fragment_" + "b" * 24,
        "locator": "section:prior",
        "quote_sha256": "c" * 64,
    }
    plan["relation_actions"] = [
        {
            "action": "create",
            "subject": {
                "knowledge_id": None,
                "semantic_key": plan["object_actions"][0]["semantic_key"],
                "kind": "claim",
            },
            "predicate": "contradicts",
            "object": {
                "knowledge_id": None,
                "semantic_key": "claim:prior-endpoint",
                "kind": "claim",
            },
            "expected_relation_revision_id": None,
            "evidence_refs": [current_reference, prior_reference],
            "valid_from": None,
            "valid_to": None,
            "reason": "Bind both exact endpoint evidence sets.",
        }
    ]
    allowed_prior = {
        prior_reference["fragment_id"]: {
            **prior_reference,
            "text_sha256": prior_reference["quote_sha256"],
        }
    }
    CompilationCoordinator._validate_plan_against_packet(
        plan=plan,
        packet=packet,
        allowed_relation_fragments=allowed_prior,
    )
    plan["relation_actions"][0]["evidence_refs"].append(
        {
            "source_revision_id": "sourcerev_" + "d" * 24,
            "fragment_id": "fragment_" + "e" * 24,
            "locator": "section:unrelated",
            "quote_sha256": "f" * 64,
        }
    )
    with pytest.raises(ValueError, match="current endpoints"):
        CompilationCoordinator._validate_plan_against_packet(
            plan=plan,
            packet=packet,
            allowed_relation_fragments=allowed_prior,
        )


def test_v011_compilation_check_domains_migrate_without_reimport(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="old-check-domain", scope="project")
    initialize_autonomous_core(root)
    source_path = tmp_path / "v011-source.md"
    source_path.write_text("# Existing\nPreserved compilation artifact.", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source_path,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v011-compiler",
            operations=COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"v011-migration-fixture")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="v011-migration-fixture",
        compiler_profile_version="1",
        host_identity="v011-compiler",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=begun)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    database = root / ".deeplaw" / "ledger.sqlite3"
    with sqlite3.connect(database) as connection:
        artifact_count = connection.execute(
            "SELECT COUNT(*) FROM source_compilation_artifacts_v1"
        ).fetchone()[0]
        usage_count = connection.execute(
            "SELECT COUNT(*) FROM source_compilation_usage_v1"
        ).fetchone()[0]
    tables = {
        "source_compilation_artifacts_v1": "semantic_receipt",
        "source_compilation_usage_v1": "freeze_semantic_inventory",
        "source_compilation_mcp_replays_v1": "abort_synthesis_refresh",
    }
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        for table in tables:
            current_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            old_sql = re.sub(
                r",\s*'observation_plan'.*?'synthesis_receipt'",
                "",
                current_sql,
                flags=re.DOTALL,
            )
            old_sql = re.sub(
                r",\s*'stage_semantic_observations'.*?'validate_synthesis_refresh'",
                "",
                old_sql,
                flags=re.DOTALL,
            )
            legacy = f"_{table}_v011"
            old_sql = old_sql.replace(
                f"CREATE TABLE {table}",
                f"CREATE TABLE {legacy}",
                1,
            )
            connection.execute(old_sql)
            columns = ", ".join(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
            connection.execute(f"INSERT INTO {legacy}({columns}) SELECT {columns} FROM {table}")
            connection.execute(f"DROP TABLE {table}")
            connection.execute(f"ALTER TABLE {legacy} RENAME TO {table}")
        connection.commit()
    finally:
        connection.close()

    migrated = initialize_autonomous_core(root, migration_source="v0.11-to-v0.12")
    assert migrated["verification"]["valid"] is True
    connection = sqlite3.connect(database)
    try:
        for table, marker in tables.items():
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            assert marker in sql
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_compilation_artifacts_v1"
            ).fetchone()[0]
            == artifact_count
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_compilation_usage_v1"
            ).fetchone()[0]
            == usage_count
        )
    finally:
        connection.close()


def test_quality_scoring_does_not_credit_duplicate_channel_hits() -> None:
    score = _score_case(
        ["Expected", "Expected", "Irrelevant"],
        ["Expected"],
        3,
    )

    assert score["hit_count"] == 1
    assert score["recall_at_k"] == 1.0
    assert score["precision_at_k"] == pytest.approx(1 / 3)
    assert score["ndcg"] == 1.0


def test_semantic_v2_observes_across_packets_and_publishes_atomically(
    tmp_path: Path,
) -> None:
    root, compiled, _grant_id = _ready_source(tmp_path, section_count=3)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deterministic-semantic-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
        unrelated_grant = store.enable_grant(
            writer_id="unrelated-semantic-agent",
            operations=("freeze_semantic_inventory",),
        )["grant_id"]
    profile = KnowledgeOS.open(root).compilations.profile(version="2")
    run = KnowledgeOS.open(root).compilations.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version=profile["compiler_profile_version"],
        host_identity="deterministic-semantic-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=1,
        confirm_no_case_data=True,
    )
    service = SemanticCompilationService(root)
    packets = []
    while packet := run.next_packet():
        packets.append(packet)
        observation_plan = _semantic_observation_plan(service, packet)
        observation_request = {
            "operation": "stage_semantic_observations",
            "idempotency_key": f"observe:{packet['packet_id']}",
            "confirm_no_case_data": True,
            "compilation_run_id": run.compilation_run_id,
            "plan": observation_plan,
        }
        semantic_tool = knowledge_sink_tool_definition(
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
            evaluator_types=("agent_self_report",),
        )
        Draft202012Validator(semantic_tool.inputSchema).validate(observation_request)
        if len(packets) == 1:
            plan_path = tmp_path / "semantic-observation-plan.json"
            plan_path.write_text(canonical_json(observation_plan), encoding="utf-8")
            observed = _cli_json(
                "knowledge",
                "semantic",
                "observe",
                "--vault",
                str(root),
                "--grant-id",
                grant_id,
                "--run-id",
                run.compilation_run_id,
                "--plan",
                str(plan_path),
                "--confirm-no-case-data",
            )
            assert observed["schema_version"] == "deeplaw.semantic-observation-batch/v1"
        else:
            observed = handle_knowledge_sink(
                observation_request,
                grant_id=grant_id,
                vault_path=root,
            )
            assert observed["schema_version"] == "deeplaw.knowledge-sink-output/v4"
        with AutonomousKnowledgeStore(root, read_only=True) as store:
            assert store.recall("DeepLaw", limit=20)["results"] == []
    assert len(packets) == 3
    assert packets[1]["semantic_protocol"]["prior_inventory"]["observation_count"] == 1

    with pytest.raises(KnowledgeOSPermissionError, match="granted boundary"):
        handle_knowledge_sink(
            {
                "operation": "freeze_semantic_inventory",
                "idempotency_key": "unauthorized-freeze",
                "confirm_no_case_data": True,
                "compilation_run_id": run.compilation_run_id,
            },
            grant_id=unrelated_grant,
            vault_path=root,
        )
    inventory = _cli_json(
        "knowledge",
        "semantic",
        "inventory",
        "--vault",
        str(root),
        "--grant-id",
        grant_id,
        "--run-id",
        run.compilation_run_id,
        "--confirm-no-case-data",
    )
    sink_inventory = handle_knowledge_sink(
        {
            "operation": "freeze_semantic_inventory",
            "idempotency_key": "freeze-semantic-inventory",
            "confirm_no_case_data": True,
            "compilation_run_id": run.compilation_run_id,
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert sink_inventory["inventory_sha256"] == inventory["inventory_sha256"]
    assert inventory["observation_count"] == 3
    assert inventory["duplicate_clusters"][0]["observation_ids"] == sorted(
        item["observation_id"] for item in inventory["observations"]
    )
    finalization_packet = _cli_json(
        "knowledge",
        "semantic",
        "finalization",
        "--vault",
        str(root),
        "--grant-id",
        grant_id,
        "--run-id",
        run.compilation_run_id,
    )
    assert len(finalization_packet["duties"]) == 15

    all_source_refs = [
        reference
        for observation in inventory["observations"]
        for reference in observation["source_refs"]
    ]
    source_revision_id = compiled["identity"]["source_revision_id"]
    input_set_body = {
        "source_revision_ids": [source_revision_id],
        "knowledge_revision_ids": [],
        "relation_revision_ids": [],
        "compilation_run_ids": [run.compilation_run_id],
    }
    first_plan = _plan(packets[0], expected_audit_head=packets[0]["input_audit_head"])
    first_plan["object_actions"] = [
        {
            "action": "create",
            "kind": "entity",
            "semantic_key": "entity:deeplaw",
            "knowledge_id": None,
            "expected_revision_id": None,
            "title": "DeepLaw",
            "body": "DeepLaw is observed consistently across this source.",
            "aliases": ["Deep Law"],
            "epistemic_state": "supported",
            "source_refs": all_source_refs,
            "assertion": None,
            "tags": ["semantic-v2"],
            "valid_from": None,
            "valid_to": None,
            "applicability": {
                "description": "This source revision.",
                "scopes": [],
                "conditions": [],
                "exclusions": [],
            },
            "synthesis_inputs": None,
            "reason": "Merge exact duplicate cross-packet observations.",
        },
        {
            "action": "create",
            "kind": "synthesis",
            "semantic_key": f"source-summary:{source_revision_id}",
            "knowledge_id": None,
            "expected_revision_id": None,
            "title": "Source summary",
            "body": "The source contains three evidence-bound statements about DeepLaw.",
            "aliases": [],
            "epistemic_state": "supported",
            "source_refs": all_source_refs,
            "assertion": None,
            "tags": ["source-summary"],
            "valid_from": None,
            "valid_to": None,
            "applicability": {
                "description": "This source revision.",
                "scopes": [],
                "conditions": [],
                "exclusions": [],
            },
            "synthesis_inputs": {
                **input_set_body,
                "input_set_sha256": sha256_bytes(canonical_json(input_set_body).encode("utf-8")),
            },
            "reason": "Publish the canonical source-bound summary.",
        },
    ]
    packet_plans = [first_plan]
    for packet in packets[1:]:
        packet_plan = _plan(packet, expected_audit_head=packet["input_audit_head"])
        packet_plan["object_actions"] = []
        packet_plans.append(packet_plan)
    duty_reports = []
    for duty in SEMANTIC_DUTIES:
        duty_reports.append(
            {
                "duty_id": finalization_packet["duties"][
                    [item["duty_type"] for item in finalization_packet["duties"]].index(duty)
                ]["duty_id"],
                "duty_type": duty,
                "required": duty in REQUIRED_SEMANTIC_DUTIES,
                "status": "satisfied" if duty == "source_summary" else "not_applicable",
                "output_refs": [],
                "evidence_refs": all_source_refs if duty == "source_summary" else [],
                "reason": "Deterministic fixture duty decision.",
                "unresolved_items": [],
                "omission_reason": None,
            }
        )
    publication_plan = {
        "schema_version": "deeplaw.semantic-publication-plan/v2",
        "compilation_run_id": run.compilation_run_id,
        "source_revision_id": source_revision_id,
        "expected_audit_head": packets[0]["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "observation_dispositions": [
            {
                "observation_id": observation["observation_id"],
                "disposition": "published" if index == 0 else "merged_into",
                "target_ref": "entity:deeplaw",
                "reason": "Merge the exact semantic identity cluster.",
            }
            for index, observation in enumerate(inventory["observations"])
        ],
        "packet_plans": packet_plans,
        "duty_reports": duty_reports,
        "semantic_status": "complete",
        "warnings": [],
    }
    publication_path = tmp_path / "semantic-publication-plan.json"
    publication_path.write_text(canonical_json(publication_plan), encoding="utf-8")
    staged = _cli_json(
        "knowledge",
        "semantic",
        "finalize",
        "--vault",
        str(root),
        "--grant-id",
        grant_id,
        "--run-id",
        run.compilation_run_id,
        "--plan",
        str(publication_path),
        "--confirm-no-case-data",
    )
    sink_staged = handle_knowledge_sink(
        {
            "operation": "finalize_semantic_compilation",
            "idempotency_key": "finalize-semantic-compilation",
            "confirm_no_case_data": True,
            "compilation_run_id": run.compilation_run_id,
            "plan": publication_plan,
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert staged["semantic_status"] == "complete"
    assert sink_staged["publication_plan_sha256"] == staged["publication_plan_sha256"]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.recall("DeepLaw", limit=20)["results"] == []
    assert run.validate(confirm_no_case_data=True)["valid"] is True
    receipt = run.commit(confirm_no_case_data=True)
    assert receipt["semantic_status"] == "complete"
    assert receipt["observation_count"] == receipt["disposition_count"] == 3
    assert receipt["source_summary_revision_id"].startswith("knowledgerev_")
    status = run.status()
    assert status["semantic_status"] == "complete"
    assert status["gaps"] == []

    support_tool = knowledge_tool_definition(autonomous=True)
    support_validator = Draft202012Validator(support_tool.inputSchema)
    for request in (
        {
            "operation": "semantic",
            "semantic_action": "status",
            "compilation_run_id": run.compilation_run_id,
        },
        {"operation": "source", "source_action": "list"},
        {"operation": "wiki", "wiki_action": "browse_kind", "kind": "entity"},
        {
            "operation": "query",
            "query": "DeepLaw",
            "query_plan_version": "5",
            "purpose": "answer",
        },
    ):
        support_validator.validate(request)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        audit_head = store.audit_head
    semantic_status = handle_knowledge_support(
        operation="semantic",
        semantic_action="status",
        compilation_run_id=run.compilation_run_id,
        vault_path=root,
    )
    source_list = handle_knowledge_support(
        operation="source",
        source_action="list",
        vault_path=root,
    )
    wiki_browse = handle_knowledge_support(
        operation="wiki",
        wiki_action="browse_kind",
        wiki_kind="entity",
        vault_path=root,
    )
    api_wiki_browse = KnowledgeOS.open(root).wiki.browse_kind("entity")
    cli_wiki_browse = _cli_json(
        "knowledge",
        "wiki",
        "browse-kind",
        "--vault",
        str(root),
        "--kind",
        "entity",
    )
    semantic_query = handle_knowledge_support(
        operation="query",
        query="DeepLaw",
        query_plan_version="5",
        purpose="answer",
        vault_path=root,
    )
    assert semantic_status["result"]["semantic_status"] == "complete"
    assert source_list["result"]["source_count"] == 0
    assert wiki_browse["result"]["items"][0]["kind"] == "entity"
    assert api_wiki_browse["items"] == wiki_browse["result"]["items"]
    assert cli_wiki_browse["items"] == wiki_browse["result"]["items"]
    assert semantic_query["schema_version"] == "deeplaw.knowledge-support-output/v5"
    assert semantic_query["result"]["schema_version"] == (
        "deeplaw.provider-knowledge-capsule/v1"
    )
    assert semantic_query["result"]["receipt"]["query_plan_sha256"]
    assert "query_plan" not in semantic_query["result"]
    assert all(
        "channels" not in item
        for item in semantic_query["result"]["compiled"]
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.audit_head == audit_head


def test_compilation_batches_remain_invisible_until_one_atomic_commit(tmp_path: Path) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=40)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"deterministic-fake-agent/v1")
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        other_grant_id = store.enable_grant(
            writer_id="another-compiler",
            operations=COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-default",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    assert begun["source_key"] == compiled["identity"]["source_key"]
    replay = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-default",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    assert replay["compilation_run_id"] == begun["compilation_run_id"]
    assert replay["idempotent_replay"] is True
    with pytest.raises(PermissionError, match="bound to another grant"):
        coordinator.begin(
            grant_id=other_grant_id,
            source_revision_id=compiled["identity"]["source_revision_id"],
            compiler_profile="living-wiki-default",
            compiler_profile_version="1",
            host_identity="another-compiler",
            model_identity=None,
            prompt_template_id="deeplaw.compile.fake/v1",
            prompt_config_sha256=configuration_sha256,
            plan_configuration_sha256=configuration_sha256,
            confirm_no_case_data=True,
            packet_max_fragments=8,
        )

    _stage_all(coordinator, grant_id=grant_id, begun=begun)

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.recall("Durable source statement", limit=20)["results"] == []

    validated = coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert validated["valid"] is True
    assert validated["staged_object_count"] == 40

    receipt = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert receipt["committed_object_count"] == 40
    assert receipt["committed_relation_count"] == 0
    assert receipt["status"] == "projection_pending"
    assert receipt["idempotent_replay"] is False
    replayed_receipt = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert replayed_receipt["idempotent_replay"] is True
    assert replayed_receipt["output_set_sha256"] == receipt["output_set_sha256"]

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.connection.execute(
                """
            SELECT COUNT(*)
            FROM knowledge_objects_v3
            JOIN knowledge_revisions_v3
              ON knowledge_revisions_v3.revision_id =
                 knowledge_objects_v3.current_revision_id
            WHERE knowledge_revisions_v3.lifecycle = 'active'
            """
            ).fetchone()[0]
            == 40
        )
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]


def test_retained_only_compilation_has_a_verifiable_empty_output_set(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=1)
    coordinator = CompilationCoordinator(root)
    first_configuration = sha256_bytes(b"retain-only/seed-v1")
    first = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="retain-only-seed",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=first_configuration,
        plan_configuration_sha256=first_configuration,
        confirm_no_case_data=True,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=first)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=first["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=first["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.resume(
        grant_id=grant_id,
        compilation_run_id=first["compilation_run_id"],
        confirm_no_case_data=True,
        project=True,
    )

    retain_configuration = sha256_bytes(b"retain-only/no-op-v1")
    retained = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="retain-only-no-op",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=retain_configuration,
        plan_configuration_sha256=retain_configuration,
        confirm_no_case_data=True,
    )
    packet = coordinator.next_packet(retained["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=retained["input_audit_head"])
    plan["object_actions"][0]["action"] = "retain"
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=retained["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=retained["compilation_run_id"],
        confirm_no_case_data=True,
    )
    receipt = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=retained["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert receipt["committed_object_count"] == 0
    assert receipt["committed_relation_count"] == 0
    completed = coordinator.resume(
        grant_id=grant_id,
        compilation_run_id=retained["compilation_run_id"],
        confirm_no_case_data=True,
        project=True,
    )
    assert completed["status"] == "succeeded"
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]


def test_cli_api_and_mcp_share_one_compilation_domain_result(tmp_path: Path) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=2)
    vault_args = ("--vault", str(root))
    begun = _cli_json(
        "knowledge",
        "compile",
        "begin",
        *vault_args,
        "--grant-id",
        grant_id,
        "--source-revision-id",
        compiled["identity"]["source_revision_id"],
        "--host-identity",
        "cli-contract-test",
        "--packet-max-fragments",
        "8",
        "--confirm-no-case-data",
    )
    run_id = begun["compilation_run_id"]
    packet = _cli_json(
        "knowledge",
        "compile",
        "packet",
        *vault_args,
        "--grant-id",
        grant_id,
        "--run-id",
        run_id,
    )
    plan_path = tmp_path / "compilation-plan.json"
    plan_path.write_text(
        canonical_json(_plan(packet, expected_audit_head=begun["input_audit_head"])),
        encoding="utf-8",
    )
    staged = _cli_json(
        "knowledge",
        "compile",
        "stage",
        *vault_args,
        "--grant-id",
        grant_id,
        "--run-id",
        run_id,
        "--plan",
        str(plan_path),
        "--confirm-no-case-data",
    )
    assert staged["object_count"] == 2
    assert (
        _cli_json(
            "knowledge",
            "compile",
            "validate",
            *vault_args,
            "--grant-id",
            grant_id,
            "--run-id",
            run_id,
            "--confirm-no-case-data",
        )["valid"]
        is True
    )
    receipt = _cli_json(
        "knowledge",
        "compile",
        "commit",
        *vault_args,
        "--grant-id",
        grant_id,
        "--run-id",
        run_id,
        "--confirm-no-case-data",
    )
    assert receipt["committed_object_count"] == 2
    cli_status = _cli_json(
        "knowledge",
        "compile",
        "status",
        *vault_args,
        "--run-id",
        run_id,
    )
    api_status = KnowledgeOS.open(root).compilations.status(run_id)
    mcp_status = handle_knowledge_support(
        operation="compilation",
        compilation_action="status",
        compilation_run_id=run_id,
        confirm_no_case_data=True,
        vault_path=root,
    )["result"]
    assert cli_status == api_status == mcp_status
    human = subprocess.run(
        [
            sys.executable,
            "-m",
            "deeplaw",
            "knowledge",
            "--format",
            "human",
            "compile",
            "status",
            *vault_args,
            "--run-id",
            run_id,
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert human.returncode == 0, human.stderr
    assert run_id in human.stdout


def test_compiler_grant_profile_is_least_privilege_and_api_requires_case_confirmation(
    tmp_path: Path,
) -> None:
    root, compiled, _grant_id = _ready_source(tmp_path, section_count=1)
    grant = _cli_json(
        "knowledge",
        "sink",
        "enable",
        "--vault",
        str(root),
        "--writer-id",
        "profiled-compiler",
        "--scope",
        "project",
        "--profile",
        "compiler",
    )
    status = _cli_json(
        "knowledge",
        "sink",
        "status",
        "--vault",
        str(root),
        "--grant-id",
        grant["grant_id"],
    )
    assert status["operations"] == list(COMPILER_GRANT_OPERATIONS)
    assert "remember" not in status["operations"]
    assert "promote_knowledge_draft" not in status["operations"]

    knowledge_os = KnowledgeOS.open(root)
    profile = knowledge_os.compilations.profile()
    with pytest.raises(KnowledgeOSValidationError):
        knowledge_os.compilations.begin(
            grant_id=grant["grant_id"],
            source_revision_id=compiled["identity"]["source_revision_id"],
            compiler_profile=profile["compiler_profile"],
            compiler_profile_version=profile["compiler_profile_version"],
            host_identity="case-boundary-test",
            prompt_template_id=profile["prompt_template_id"],
            prompt_config_sha256=profile["prompt_config_sha256"],
            plan_configuration_sha256=profile["plan_configuration_sha256"],
        )


def test_deterministic_fake_agent_executes_real_source_to_knowledge_e2e(
    tmp_path: Path,
) -> None:
    root, compiled, _grant_id = _ready_source(tmp_path, section_count=35)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deeplaw-deterministic-fake-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    report = compile_with_fake_agent(
        vault=root,
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        packet_max_fragments=5,
    )
    assert report["status"] == "succeeded"
    assert report["packet_count"] == 7
    assert report["observation_count"] == 35
    assert report["staged_object_count"] == 36
    assert report["semantic_status"] == "complete"
    assert report["compiled_result_count"] > 0
    assert report["verification_valid"] is True
    assert report["network_used"] is False
    source_page = (
        root / "wiki/sources" / f"{compiled['identity']['source_revision_id']}.md"
    ).read_text(encoding="utf-8")
    assert "transaction `succeeded` · semantic `complete`" in source_page
    assert "`source_summary`: `satisfied` (required)" in source_page
    assert "`identity_resolution`: `not_applicable` (required)" in source_page
    assert report["external_credentials_used"] is False


def test_living_wiki_shards_keep_more_than_300_objects_discoverable(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=305)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"deterministic-fake-agent/sharding-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-sharding",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=64,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=begun)
    validated = coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert validated["staged_object_count"] == 305
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    completed = coordinator.resume(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
        project=True,
    )
    assert completed["status"] == "succeeded"
    living_wiki = completed["projection"]["living_wiki"]
    assert living_wiki["knowledge_count"] == 305
    assert living_wiki["index_shard_count"] == 2
    assert len(canonical_json(completed["projection"]).encode("utf-8")) < 65_536
    pages = sorted((root / "wiki" / "claims").glob("knowledge_*.md"))
    assert len(pages) == 305
    index_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "wiki" / "indexes").glob("claim-*.md"))
    )
    fragment_indexes = sorted((root / "wiki" / "indexes").glob("source-*-fragments-*.md"))
    source_page = (
        root / "wiki" / "sources" / f"{compiled['identity']['source_revision_id']}.md"
    ).read_text(encoding="utf-8")
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        knowledge_ids = [
            row["knowledge_id"]
            for row in store.connection.execute(
                "SELECT knowledge_id FROM knowledge_objects_v3 ORDER BY knowledge_id"
            )
        ]
        first_fragment_id = store.connection.execute(
            """
            SELECT legacy_fragment_bindings_v2.fragment_id
            FROM legacy_fragment_bindings_v2
            JOIN fragments_v2 USING(fragment_revision_id)
            ORDER BY fragments_v2.ordinal
            LIMIT 1
            """
        ).fetchone()["fragment_id"]
        verification = store.verify()
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuilt = store.rebuild_derived()
    assert all(knowledge_id in index_text for knowledge_id in knowledge_ids)
    assert len(fragment_indexes) == 5
    assert "## Exact evidence drill-down" in source_page
    assert f"`{begun['compilation_run_id']}` · `succeeded`" in source_page
    assert all(
        f"[[{path.relative_to(root).with_suffix('').as_posix()}" in source_page
        for path in fragment_indexes
    )
    assert first_fragment_id in fragment_indexes[0].read_text(encoding="utf-8")
    assert rebuilt["living_wiki"]["manifest_sha256"] == living_wiki["manifest_sha256"]
    assert (root / "wiki" / "overview.md").read_bytes() != (root / "wiki" / "index.md").read_bytes()
    assert verification["valid"] is True, verification["failures"]


def test_source_removal_invalidates_dependencies_and_recall(tmp_path: Path) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=3)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"deterministic-fake-agent/freshness-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-freshness",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=3,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=begun["input_audit_head"])
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        overview_semantic_key = f"overview:{store.vault_id}"
    synthesis_inputs = {
        "source_revision_ids": [compiled["identity"]["source_revision_id"]],
        "knowledge_revision_ids": [],
        "relation_revision_ids": [],
        "compilation_run_ids": [begun["compilation_run_id"]],
    }
    plan["object_actions"].append(
        {
            **plan["object_actions"][0],
            "semantic_key": overview_semantic_key,
            "title": "Removal-aware Overview",
            "body": "This synthesis is invalidated when its only source is withdrawn.",
            "kind": "synthesis",
            "synthesis_inputs": {
                **synthesis_inputs,
                "input_set_sha256": sha256_bytes(
                    canonical_json(synthesis_inputs).encode("utf-8")
                ),
            },
            "reason": "Exercise source-withdrawal synthesis refresh triggering.",
        }
    )
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    source_page = root / "wiki" / "sources" / f"{compiled['identity']['source_revision_id']}.md"
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.rebuild_derived()
        assert store.recall("Durable source statement", limit=20)["results"]
    assert source_page.is_file()
    with KnowledgeVault(root, read_only=False) as vault:
        vault.remove_source(
            compiled["source"]["source_id"],
            reason="Freshness propagation regression.",
            confirm=True,
        )
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert report["source_status"] == "removed"
    assert len(report["affected_knowledge_revision_ids"]) == 4
    assert len(report["missing_fragment_ids"]) == 3
    assert len(report["synthesis_refresh_task_ids"]) == 1
    task = SynthesisRefreshService(root).tasks(status="planned")[0]
    assert task["refresh_task_id"] == report["synthesis_refresh_task_ids"][0]
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.rebuild_derived()
        states = {
            row["freshness"]
            for row in store.connection.execute("SELECT freshness FROM knowledge_dependencies_v1")
        }
        assert states == {"invalidated"}
        assert store.recall("Durable source statement", limit=20)["results"] == []
        verification = store.verify()
    assert source_page.exists() is False
    assert verification["valid"] is True, verification["failures"]


def test_source_successor_stales_only_changed_fragments_and_carries_exact_matches(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="successor-freshness", scope="project")
    initialize_autonomous_core(root)
    source = tmp_path / "source.md"
    source.write_text(
        "\n\n".join(
            (
                "# One\nDurable unchanged statement one.",
                "# Two\nDurable original statement two.",
                "# Three\nDurable unchanged statement three.",
            )
        ),
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(first["source"]["source_id"])
        vault.approve_source_assets(
            first["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="freshness-test",
            review_reason="Activate the exact initial Source Revision.",
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deterministic-fake-agent",
            operations=COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"successor-freshness-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=first["identity"]["source_revision_id"],
        compiler_profile="living-wiki-successor",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=begun)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )

    source.write_text(
        "\n\n".join(
            (
                "# One\nDurable unchanged statement one.",
                "# Two\nDurable revised statement two.",
                "# Three\nDurable unchanged statement three.",
            )
        ),
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        second = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(second["source"]["source_id"])
        vault.approve_source_assets(
            second["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="freshness-test",
            review_reason="Activate the exact successor Source Revision.",
        )
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=first["identity"]["source_revision_id"],
        replacement_source_revision_id=second["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert len(report["changed_fragment_ids"]) == 1
    assert len(report["unchanged_fragment_ids"]) == 2
    assert report["added_fragment_ids"] == []
    assert report["moved_fragment_ids"] == []
    assert report["missing_fragment_ids"] == []
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        counts = {
            row["freshness"]: row["count"]
            for row in store.connection.execute(
                """
                SELECT freshness, COUNT(*) AS count
                FROM knowledge_dependencies_v1
                GROUP BY freshness
                """
            )
        }
        current = [
            store.get_current(row["knowledge_id"])
            for row in store.connection.execute(
                "SELECT knowledge_id FROM knowledge_objects_v3 ORDER BY knowledge_id"
            )
        ]
        admitted_titles = {
            item["title"] for item in current if store.revision_provenance_admitted(item)
        }
        verification = store.verify()
    assert counts == {"fresh": 2, "stale": 1}
    assert "Compiled source claim 1" in admitted_titles
    assert "Compiled source claim 3" in admitted_titles
    assert "Compiled source claim 2" not in admitted_titles
    stale_query = PurposeAwareRetrievalService(root).query(
        "Durable original statement two",
        purpose="answer",
    )
    assert any(gap["code"] == "stale_knowledge" for gap in stale_query["gaps"])
    assert stale_query["metrics"]["stale_selection_prevented_count"] == 1
    assert verification["valid"] is True, verification["failures"]


def test_source_structural_diff_reports_added_and_moved_fragments(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="structural-diff", scope="project")
    initialize_autonomous_core(root)
    source = tmp_path / "structure.md"
    source.write_text(
        "# One\nAlpha.\n\n# Two\nBeta.\n\n# Three\nGamma.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(first["source"]["source_id"])
        vault.approve_source_assets(
            first["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="structural-diff-test",
            review_reason="Activate the initial structural fixture.",
        )
    source.write_text(
        "# Three\nGamma.\n\n# One\nAlpha.\n\n# Two\nBeta.\n\n# Four\nDelta.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        successor = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(successor["source"]["source_id"])
        vault.approve_source_assets(
            successor["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="structural-diff-test",
            review_reason="Activate the reordered and extended structural fixture.",
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deterministic-fake-agent",
            operations=COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    report = CompilationCoordinator(root).refresh(
        grant_id=grant_id,
        source_revision_id=first["identity"]["source_revision_id"],
        replacement_source_revision_id=successor["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert len(report["added_fragment_ids"]) == 1
    assert len(report["moved_fragment_ids"]) == 3
    assert report["changed_fragment_ids"] == []
    assert report["missing_fragment_ids"] == []


def test_synthesis_records_exact_inputs_and_transitively_stales(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="synthesis-dependencies", scope="project")
    initialize_autonomous_core(root)
    first_source = tmp_path / "first.md"
    second_source = tmp_path / "second.md"
    first_source.write_text("# First\nStable upstream statement.", encoding="utf-8")
    second_source.write_text("# Second\nStable synthesis statement.", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            first_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        second = compile_source(
            vault,
            second_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deterministic-fake-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"synthesis-dependencies-v1")

    first_run = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=first["identity"]["source_revision_id"],
        compiler_profile="living-wiki-synthesis",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=first_run)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=first_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    first_receipt = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=first_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    upstream_revision_id = first_receipt["knowledge_revision_ids"][0]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        overview_semantic_key = f"overview:{store.vault_id}"

    second_run = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=second["identity"]["source_revision_id"],
        compiler_profile="living-wiki-synthesis",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    packet = coordinator.next_packet(second_run["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=second_run["input_audit_head"])
    input_set = {
        "source_revision_ids": sorted(
            (
                first["identity"]["source_revision_id"],
                second["identity"]["source_revision_id"],
            )
        ),
        "knowledge_revision_ids": [upstream_revision_id],
        "relation_revision_ids": [],
        "compilation_run_ids": sorted(
            (
                first_run["compilation_run_id"],
                second_run["compilation_run_id"],
            )
        ),
    }
    plan["object_actions"].append(
        {
            **plan["object_actions"][0],
            "semantic_key": overview_semantic_key,
            "title": "Living Wiki Overview",
            "body": "A governed Synthesis over the exact registered inputs.",
            "kind": "synthesis",
            "synthesis_inputs": {
                **input_set,
                "input_set_sha256": sha256_bytes(canonical_json(input_set).encode("utf-8")),
            },
            "reason": "Register the exact full Synthesis input set.",
        }
    )
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=second_run["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=second_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    second_receipt = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=second_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        synthesis = store.connection.execute(
            """
            SELECT knowledge_revisions_v3.*
            FROM knowledge_revisions_v3
            WHERE kind = 'synthesis'
            """
        ).fetchone()
        assert synthesis is not None
        assert synthesis["verification"] == "revision_bound"
        synthesis_detail = store.get_current(synthesis["knowledge_id"])
        detail_schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "contracts"
                / "knowledge-revision-detail.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(detail_schema).validate(synthesis_detail)
        synthesis_revision_id = synthesis["revision_id"]
        registered = store.connection.execute(
            """
            SELECT * FROM synthesis_input_sets_v1
            WHERE synthesis_revision_id = ?
            """,
            (synthesis_revision_id,),
        ).fetchone()
        assert registered is not None
        assert registered["input_set_sha256"] == sha256_bytes(
            canonical_json(input_set).encode("utf-8")
        )
        dependency_inputs = {
            (row["input_kind"], row["input_id"])
            for row in store.connection.execute(
                """
                SELECT input_kind, input_id FROM revision_dependencies_v1
                WHERE consumer_revision_id = ?
                """,
                (synthesis_revision_id,),
            )
        }
        assert ("knowledge_revision", upstream_revision_id) in dependency_inputs
        assert (
            "compilation_run",
            first_run["compilation_run_id"],
        ) in dependency_inputs
        assert (
            "compilation_run",
            second_run["compilation_run_id"],
        ) in dependency_inputs
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]
    assert synthesis_revision_id in second_receipt["knowledge_revision_ids"]

    first_source.write_text("# First\nChanged upstream statement.", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        successor = compile_source(
            vault,
            first_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(successor["source"]["source_id"])
        vault.approve_source_assets(
            successor["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="synthesis-freshness-test",
            review_reason="Activate the changed Source Revision.",
        )
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=first["identity"]["source_revision_id"],
        replacement_source_revision_id=successor["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert synthesis_revision_id in report["affected_knowledge_revision_ids"]
    assert len(report["synthesis_refresh_task_ids"]) == 1
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        revision_dependency = store.connection.execute(
            """
            SELECT freshness FROM revision_dependencies_v1
            WHERE consumer_revision_id = ?
              AND input_kind = 'knowledge_revision'
              AND input_id = ?
            """,
            (synthesis_revision_id, upstream_revision_id),
        ).fetchone()
        assert revision_dependency is not None
        assert revision_dependency["freshness"] == "stale"
        synthesis_current = store._revision_row(synthesis, include_body=False)
        assert store.revision_provenance_admitted(synthesis_current) is False
        assert all(
            item["revision_id"] != synthesis_revision_id
            for item in store.recall("governed Synthesis", limit=20)["results"]
        )

    refresh_service = SynthesisRefreshService(root)
    task = refresh_service.tasks(status="planned")[0]
    assert task["refresh_task_id"] == report["synthesis_refresh_task_ids"][0]
    cli_tasks = _cli_json(
        "knowledge",
        "synthesis",
        "list-stale",
        "--vault",
        str(root),
    )
    assert cli_tasks["tasks"][0]["refresh_task_id"] == task["refresh_task_id"]
    prompt_sha256 = sha256_bytes(b"synthesis-refresh-prompt/v1")
    refresh_begin_request = {
        "refresh_task_id": task["refresh_task_id"],
        "source_revision_ids": sorted(
            (
                successor["identity"]["source_revision_id"],
                second["identity"]["source_revision_id"],
            )
        ),
        "knowledge_revision_ids": [],
        "relation_revision_ids": [],
        "host_identity": "deterministic-fake-agent",
        "profile_id": "deeplaw.synthesis-refresh.fake/v1",
        "prompt_sha256": prompt_sha256,
        "config_sha256": prompt_sha256,
    }
    begin_path = tmp_path / "synthesis-refresh-begin.json"
    begin_path.write_text(canonical_json(refresh_begin_request), encoding="utf-8")
    refresh_run = _cli_json(
        "knowledge",
        "synthesis",
        "begin",
        "--vault",
        str(root),
        "--grant-id",
        grant_id,
        "--request",
        str(begin_path),
        "--confirm-no-case-data",
    )
    sink_refresh_run = handle_knowledge_sink(
        {
            "operation": "begin_synthesis_refresh",
            "idempotency_key": "begin-synthesis-refresh",
            "confirm_no_case_data": True,
            **refresh_begin_request,
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert sink_refresh_run["synthesis_refresh_run_id"] == refresh_run["synthesis_refresh_run_id"]
    refresh_packet = _cli_json(
        "knowledge",
        "synthesis",
        "packet",
        "--vault",
        str(root),
        "--refresh-run-id",
        refresh_run["synthesis_refresh_run_id"],
    )
    assert refresh_packet is not None
    refresh_input_set = refresh_packet["synthesis_refresh"]["input_set"]
    refresh_plan = _plan(
        refresh_packet,
        expected_audit_head=refresh_packet["input_audit_head"],
    )
    refresh_plan["object_actions"] = [
        {
            **refresh_plan["object_actions"][0],
            "action": "revise",
            "kind": "synthesis",
            "semantic_key": overview_semantic_key,
            "knowledge_id": synthesis["knowledge_id"],
            "expected_revision_id": synthesis_revision_id,
            "title": "Living Wiki Overview",
            "body": "A refreshed governed Synthesis over the successor inputs.",
            "synthesis_inputs": refresh_input_set,
            "reason": "Refresh the stale Overview from the exact successor input set.",
        }
    ]
    refresh_publication_plan = {
        "schema_version": "deeplaw.synthesis-refresh-plan/v1",
        "synthesis_refresh_run_id": refresh_run["synthesis_refresh_run_id"],
        "compilation_run_id": refresh_run["transaction"]["compilation_run_id"],
        "target_knowledge_id": synthesis["knowledge_id"],
        "expected_revision_id": synthesis_revision_id,
        "input_set_sha256": refresh_input_set["input_set_sha256"],
        "packet_plans": [refresh_plan],
        "reason": "Deterministic governed Overview refresh.",
        "warnings": [],
    }
    refresh_plan_path = tmp_path / "synthesis-refresh-plan.json"
    refresh_plan_path.write_text(canonical_json(refresh_publication_plan), encoding="utf-8")
    staged_refresh = _cli_json(
        "knowledge",
        "synthesis",
        "stage",
        "--vault",
        str(root),
        "--grant-id",
        grant_id,
        "--refresh-run-id",
        refresh_run["synthesis_refresh_run_id"],
        "--plan",
        str(refresh_plan_path),
        "--confirm-no-case-data",
    )
    sink_staged_refresh = handle_knowledge_sink(
        {
            "operation": "stage_synthesis_refresh",
            "idempotency_key": "stage-synthesis-refresh",
            "confirm_no_case_data": True,
            "synthesis_refresh_run_id": refresh_run["synthesis_refresh_run_id"],
            "plan": refresh_publication_plan,
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert staged_refresh["staged_packet_count"] == 1
    assert sink_staged_refresh["input_set_sha256"] == staged_refresh["input_set_sha256"]
    assert (
        _cli_json(
            "knowledge",
            "synthesis",
            "validate",
            "--vault",
            str(root),
            "--grant-id",
            grant_id,
            "--refresh-run-id",
            refresh_run["synthesis_refresh_run_id"],
            "--confirm-no-case-data",
        )["valid"]
        is True
    )
    assert (
        handle_knowledge_sink(
            {
                "operation": "validate_synthesis_refresh",
                "idempotency_key": "validate-synthesis-refresh",
                "confirm_no_case_data": True,
                "synthesis_refresh_run_id": refresh_run["synthesis_refresh_run_id"],
            },
            grant_id=grant_id,
            vault_path=root,
        )["result"]["valid"]
        is True
    )
    refresh_receipt = _cli_json(
        "knowledge",
        "synthesis",
        "commit",
        "--vault",
        str(root),
        "--grant-id",
        grant_id,
        "--refresh-run-id",
        refresh_run["synthesis_refresh_run_id"],
        "--confirm-no-case-data",
    )
    sink_refresh_receipt = handle_knowledge_sink(
        {
            "operation": "commit_synthesis_refresh",
            "idempotency_key": "commit-synthesis-refresh",
            "confirm_no_case_data": True,
            "synthesis_refresh_run_id": refresh_run["synthesis_refresh_run_id"],
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert refresh_receipt["previous_revision_id"] == synthesis_revision_id
    assert sink_refresh_receipt["input_set_sha256"] == refresh_receipt["input_set_sha256"]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        current = store.connection.execute(
            "SELECT current_revision_id FROM knowledge_objects_v3 WHERE knowledge_id = ?",
            (synthesis["knowledge_id"],),
        ).fetchone()["current_revision_id"]
        assert current != synthesis_revision_id
        current_row = store.connection.execute(
            "SELECT * FROM knowledge_revisions_v3 WHERE revision_id = ?",
            (current,),
        ).fetchone()
        assert current_row["verification"] == "revision_bound"
        assert (
            store.revision_provenance_admitted(
                store._revision_row(
                    current_row,
                    include_body=False,
                )
            )
            is True
        )
    assert (
        refresh_service.tasks(status="completed")[0]["refresh_task_id"] == task["refresh_task_id"]
    )
    assert (
        _cli_json("knowledge", "synthesis", "coverage", "--vault", str(root))[
            "stale_current_synthesis_count"
        ]
        == 0
    )


def test_compilation_recovers_before_and_after_atomic_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=3)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"deterministic-fake-agent/recovery-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-recovery",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=2,
    )
    coordinator = CompilationCoordinator(root)
    assert coordinator.status(begun["compilation_run_id"])["status"] == "planned"
    first_packet = coordinator.next_packet(begun["compilation_run_id"])
    assert first_packet is not None
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=_plan(first_packet, expected_audit_head=begun["input_audit_head"]),
        confirm_no_case_data=True,
    )
    resumed_packet = CompilationCoordinator(root).next_packet(begun["compilation_run_id"])
    assert resumed_packet is not None
    assert resumed_packet["ordinal"] == 2
    assert coordinator.status(begun["compilation_run_id"])["status"] == "staging"
    _stage_all(coordinator, grant_id=grant_id, begun=begun)
    coordinator = CompilationCoordinator(root)
    assert coordinator.status(begun["compilation_run_id"])["status"] == "validating"
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert coordinator.status(begun["compilation_run_id"])["status"] == "ready_to_commit"

    original_commit_object = CompilationCoordinator._commit_object
    calls = 0

    def fail_second_object(
        store: AutonomousKnowledgeStore,
        *,
        run: object,
        grant: object,
        value: dict,
    ) -> None:
        nonlocal calls
        calls += 1
        original_commit_object(store, run=run, grant=grant, value=value)
        if calls == 2:
            raise RuntimeError("injected pre-commit crash")

    monkeypatch.setattr(
        CompilationCoordinator,
        "_commit_object",
        staticmethod(fail_second_object),
    )
    with pytest.raises(RuntimeError, match="injected pre-commit crash"):
        coordinator.commit(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.connection.execute("SELECT COUNT(*) FROM knowledge_objects_v3").fetchone()[0] == 0
        )
    monkeypatch.setattr(
        CompilationCoordinator,
        "_commit_object",
        staticmethod(original_commit_object),
    )

    original_finish_materialization = CompilationCoordinator._finish_materialization

    def fail_before_materialization(
        _store: AutonomousKnowledgeStore,
        *,
        compilation_run_id: str,
        revision_ids: list[str],
    ) -> None:
        del compilation_run_id, revision_ids
        raise RuntimeError("injected crash after canonical commit")

    monkeypatch.setattr(
        CompilationCoordinator,
        "_finish_materialization",
        staticmethod(fail_before_materialization),
    )
    with pytest.raises(RuntimeError, match="injected crash after canonical commit"):
        coordinator.commit(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.connection.execute("SELECT COUNT(*) FROM knowledge_objects_v3").fetchone()[0] == 3
        )
        assert (
            store.connection.execute(
                """
                SELECT status FROM source_compilation_runs_v1
                WHERE compilation_run_id = ?
                """,
                (begun["compilation_run_id"],),
            ).fetchone()["status"]
            == "committed"
        )
    monkeypatch.setattr(
        CompilationCoordinator,
        "_finish_materialization",
        staticmethod(original_finish_materialization),
    )

    original_materialize = AutonomousKnowledgeStore._materialize_pending

    def fail_materialization(
        _store: AutonomousKnowledgeStore,
        _revision_id: str,
    ) -> None:
        raise RuntimeError("injected post-commit crash")

    monkeypatch.setattr(
        AutonomousKnowledgeStore,
        "_materialize_pending",
        fail_materialization,
    )
    with pytest.raises(RuntimeError, match="injected post-commit crash"):
        coordinator.resume(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.connection.execute("SELECT COUNT(*) FROM knowledge_objects_v3").fetchone()[0] == 3
        )
        assert (
            store.connection.execute("SELECT COUNT(*) FROM pending_materializations_v3").fetchone()[
                0
            ]
            == 3
        )
    monkeypatch.setattr(
        AutonomousKnowledgeStore,
        "_materialize_pending",
        original_materialize,
    )
    resumed = coordinator.resume(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert resumed["status"] == "projection_pending"
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]

    original_rebuild = AutonomousKnowledgeStore.rebuild_derived

    def fail_projection(
        _store: AutonomousKnowledgeStore,
        *,
        run_status_overrides: dict[str, str] | None = None,
    ) -> dict:
        del run_status_overrides
        raise RuntimeError("injected projection crash")

    monkeypatch.setattr(
        AutonomousKnowledgeStore,
        "rebuild_derived",
        fail_projection,
    )
    with pytest.raises(RuntimeError, match="injected projection crash"):
        coordinator.resume(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            confirm_no_case_data=True,
            project=True,
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        run = store.connection.execute(
            """
            SELECT status, failure_stage FROM source_compilation_runs_v1
            WHERE compilation_run_id = ?
            """,
            (begun["compilation_run_id"],),
        ).fetchone()
        assert run is not None
        assert dict(run) == {
            "status": "projection_pending",
            "failure_stage": "projection",
        }
        assert (
            store.connection.execute("SELECT COUNT(*) FROM knowledge_objects_v3").fetchone()[0] == 3
        )
    monkeypatch.setattr(
        AutonomousKnowledgeStore,
        "rebuild_derived",
        original_rebuild,
    )
    completed = coordinator.resume(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
        project=True,
    )
    assert completed["status"] == "succeeded"
    assert completed["projection"]["living_wiki"]["knowledge_count"] == 3
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]


def test_compilation_abort_is_idempotent_before_commit_and_forbidden_after_commit(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=1)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"abort-boundary-v1")
    abortable = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-abortable",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
    )
    abortable_packet = coordinator.next_packet(abortable["compilation_run_id"])
    assert abortable_packet is not None
    abortable_plan = _plan(
        abortable_packet,
        expected_audit_head=abortable["input_audit_head"],
    )
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=abortable["compilation_run_id"],
        plan=abortable_plan,
        confirm_no_case_data=True,
    )
    aborted = coordinator.abort(
        grant_id=grant_id,
        compilation_run_id=abortable["compilation_run_id"],
        reason="Owner cancelled the pre-commit compilation.",
        confirm_no_case_data=True,
    )
    assert aborted["status"] == "aborted"
    assert aborted["idempotent_replay"] is False
    replay = coordinator.abort(
        grant_id=grant_id,
        compilation_run_id=abortable["compilation_run_id"],
        reason="Owner cancelled the pre-commit compilation.",
        confirm_no_case_data=True,
    )
    assert replay["idempotent_replay"] is True
    assert coordinator.next_packet(abortable["compilation_run_id"]) is None
    with pytest.raises(RuntimeError, match="cannot be resumed"):
        coordinator.resume(
            grant_id=grant_id,
            compilation_run_id=abortable["compilation_run_id"],
            confirm_no_case_data=True,
        )
    with pytest.raises(RuntimeError, match="no longer accepts"):
        coordinator.stage(
            grant_id=grant_id,
            compilation_run_id=abortable["compilation_run_id"],
            plan=abortable_plan,
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.recall("Durable source statement", limit=20)["results"] == []

    committed_configuration_sha256 = sha256_bytes(b"abort-boundary-committed-v1")
    committed = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-abort-committed",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=committed_configuration_sha256,
        plan_configuration_sha256=committed_configuration_sha256,
        confirm_no_case_data=True,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=committed)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=committed["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=committed["compilation_run_id"],
        confirm_no_case_data=True,
    )
    with pytest.raises(RuntimeError, match="cannot be aborted"):
        coordinator.abort(
            grant_id=grant_id,
            compilation_run_id=committed["compilation_run_id"],
            reason="This must not erase a canonical commit.",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]


def test_purpose_aware_query_is_compiled_first_and_read_only(tmp_path: Path) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=3)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"deterministic-fake-agent/query-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-query",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=3,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=begun["input_audit_head"])
    plan["object_actions"][0]["source_refs"] = [
        reference
        for action in plan["object_actions"]
        for reference in action["source_refs"]
    ]
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        audit_head = store.audit_head
        object_count = store.connection.execute(
            "SELECT COUNT(*) FROM knowledge_objects_v3"
        ).fetchone()[0]

    result = PurposeAwareRetrievalService(root).query(
        "Durable source statement",
        purpose="answer",
    )
    api_result = KnowledgeOS.open(root).retrieval.query(
        "Durable source statement",
        purpose="answer",
    )
    v5_result = KnowledgeOS.open(root).retrieval.query(
        "Durable source statement",
        purpose="answer",
        query_plan_version="5",
    )

    assert result["policy_id"] == "compiled-first-v1"
    assert result["compiled"]
    assert result["evidence"] == []
    assert result["query_plan"]["fallback"]["used"] is False
    assert result["write_performed"] is False
    assert result["metrics"]["compiled_hit"] is True
    assert api_result["query_plan"]["query_sha256"] == result["query_plan"]["query_sha256"]
    assert [item["revision_id"] for item in api_result["compiled"]] == [
        item["revision_id"] for item in result["compiled"]
    ]
    assert v5_result["schema_version"] == "deeplaw.purpose-aware-retrieval/v2"
    assert v5_result["query_plan"]["schema_version"] == ("deeplaw.knowledge-query-plan/v5")
    assert [item["duty"] for item in v5_result["query_plan"]["knowledge_duties"]] == [
        "primary_answer",
        "definition",
        "temporal_freshness",
        "contradiction_or_counterevidence",
        "limitation",
        "source_evidence",
        "applicability",
        "unresolved_gap",
    ]
    assert v5_result["query_plan"]["knowledge_partitions"]["source_bound_compiled"]
    assert v5_result["metrics"]["source_free_selection_rate"] == 0.0
    assert v5_result["metrics"]["provider_payload_bytes"] <= 65_536
    assert v5_result["delivery"]["provider_visible_bytes"] <= 65_536
    assert v5_result["delivery"]["hard_limit_bytes"] == 65_536
    assert v5_result["delivery"]["suppressed_candidate_count"] >= 0
    assert v5_result["delivery"]["deduplicated_object_count"] == 0
    assert v5_result["delivery"]["continuation_available"] is True
    assert v5_result["compiled"][0]["evidence_drill_down"]
    hydrated = next(
        item for item in v5_result["compiled"] if item["source_ref_count"] == 3
    )
    assert len(hydrated["source_refs"]) == 3
    assert hydrated["source_refs_truncated"] is False
    assert "channels" not in v5_result["compiled"][0]
    assert "reranker" not in v5_result["compiled"][0]
    assert v5_result["query_plan"]["provider_surface"] == "knowledge_capsule"
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.audit_head == audit_head
        assert (
            store.connection.execute("SELECT COUNT(*) FROM knowledge_objects_v3").fetchone()[0]
            == object_count
        )

    legal = PurposeAwareRetrievalService(root).query(
        "Durable source statement",
        purpose="legal",
    )
    assert legal["compiled"] == []
    assert legal["evidence"] == []
    assert legal["gaps"][0]["code"] == "law_support_required"


def test_raw_evidence_fallback_retains_exact_identity_v2_receipt(
    tmp_path: Path,
) -> None:
    root, compiled, _grant_id = _ready_source(tmp_path, section_count=2)
    with KnowledgeVault(root, read_only=False) as vault:
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="purpose-evidence-test",
            review_reason="Activate exact source evidence for fallback verification.",
        )

    result = PurposeAwareRetrievalService(root).query(
        "Durable source statement 1",
        purpose="answer",
        limit=5,
    )

    assert result["compiled"] == []
    assert result["evidence"]
    assert result["metrics"]["source_fallback_used"] is True
    assert result["query_plan"]["fallback"]["used"] is True
    assert any(gap["code"] == "source_fallback" for gap in result["gaps"])
    reference = result["evidence"][0]["source_refs"][0]
    assert set(reference) == {
        "source_revision_id",
        "fragment_revision_id",
        "locator",
        "quote_sha256",
    }
    assert reference["fragment_revision_id"].startswith("irfragment_")
    assert reference["source_revision_id"] == compiled["identity"]["source_revision_id"]
    source_id = compiled["source"]["source_id"]
    with KnowledgeVault(root, read_only=True) as vault:
        fragment_id = vault.connection.execute(
            "SELECT fragment_id FROM source_fragments WHERE source_id = ? ORDER BY ordinal LIMIT 1",
            (source_id,),
        ).fetchone()["fragment_id"]
    mcp_source = handle_knowledge_support(
        operation="source",
        source_action="get",
        source_id=source_id,
        vault_path=root,
    )["result"]
    api_source = KnowledgeOS.open(root).sources.get(source_id)
    cli_source = _cli_json(
        "knowledge",
        "source",
        "get",
        "--vault",
        str(root),
        "--source-id",
        source_id,
    )
    assert mcp_source == api_source == cli_source
    api_fragment = KnowledgeOS.open(root).sources.fragment(fragment_id)
    cli_fragment = _cli_json(
        "knowledge",
        "source",
        "fragment",
        "--vault",
        str(root),
        "--fragment-id",
        fragment_id,
    )
    assert api_fragment == cli_fragment
    revision_fragment = KnowledgeOS.open(root).sources.fragment(
        reference["fragment_revision_id"]
    )
    assert revision_fragment == api_fragment
    assert (
        api_fragment["fragment"]["fragment_revision_id"]
        == reference["fragment_revision_id"]
    )
    assert api_fragment["fragment"]["source_revision_id"] == reference["source_revision_id"]

    unanswerable = PurposeAwareRetrievalService(root).query(
        "NO-SUCH-FACT-CHI",
        purpose="answer",
        limit=5,
    )
    assert unanswerable["compiled"] == []
    assert unanswerable["evidence"] == []
    assert any(gap["code"] == "evidence_gap" for gap in unanswerable["gaps"])


def test_source_fragment_supports_bounded_deterministic_continuation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="fragment-pagination", scope="project")
    source = tmp_path / "long-source.md"
    source.write_text("# Long evidence\n" + ("bounded-evidence " * 200), encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        fragment_id = vault.connection.execute(
            "SELECT fragment_id FROM source_fragments ORDER BY ordinal LIMIT 1"
        ).fetchone()["fragment_id"]
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="fragment-pagination-test",
            review_reason="Activate exact source evidence for continuation verification.",
        )
    initialize_autonomous_core(root)

    first = KnowledgeOS.open(root).sources.fragment(
        fragment_id,
        offset=0,
        max_chars=200,
    )
    assert first["fragment"]["content_characters"] == 200
    assert first["fragment"]["content_truncated"] is True
    assert first["fragment"]["next_offset"] == 200
    assert first["fragment"]["continuation"] == {
        "action": "fragment",
        "fragment_id": first["fragment"]["fragment_revision_id"],
        "offset": 200,
        "max_chars": 200,
    }
    second = handle_knowledge_support(
        operation="source",
        source_action="fragment",
        fragment_id=first["fragment"]["fragment_revision_id"],
        offset=200,
        max_chars=200,
        vault_path=root,
    )["result"]
    cli_second = _cli_json(
        "knowledge",
        "source",
        "fragment",
        "--vault",
        str(root),
        "--fragment-id",
        first["fragment"]["fragment_revision_id"],
        "--offset",
        "200",
        "--max-chars",
        "200",
    )
    assert second == cli_second
    assert second["fragment"]["content_offset"] == 200
    assert second["fragment"]["text"] != first["fragment"]["text"]
    assert second["fragment"]["source_revision_id"] == compiled["identity"][
        "source_revision_id"
    ]


def test_dense_only_low_relevance_candidates_trigger_visible_fallback(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=3)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"dense-relevance-floor-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-relevance-floor",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=begun)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )

    result = PurposeAwareRetrievalService(root).query(
        "NO-SUCH-FACT-CHI",
        purpose="answer",
        limit=5,
    )

    assert result["compiled"] == []
    assert result["metrics"]["source_fallback_used"] is True
    assert result["query_plan"]["fallback"]["used"] is True
    assert any(gap["code"] == "source_fallback" for gap in result["gaps"])
    assert any(gap["code"] == "retrieval_gap" for gap in result["gaps"])


def test_weak_single_term_lexical_match_cannot_answer_unknown_identifier(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=1)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"lexical-relevance-floor-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-lexical-relevance-floor",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=begun["input_audit_head"])
    plan["object_actions"][0]["title"] = "Known Fact Alpha"
    plan["object_actions"][0]["body"] = "A known fact applies only to ALPHA."
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )

    result = PurposeAwareRetrievalService(root).query(
        "NO-SUCH-FACT-CHI",
        purpose="answer",
        limit=5,
    )

    assert result["compiled"] == []
    assert any(gap["code"] == "retrieval_gap" for gap in result["gaps"])


def test_exact_policy_query_rejects_different_policy_designator() -> None:
    policy_b = {
        "title": "Diagnostic log retention is 60 days",
        "semantic_key": "claim:atlas:retention:policy-b:2026",
        "content": "Policy B requires 60-day retention.",
        "metadata": {"aliases": []},
    }
    comparison = {
        "title": "Policy A and Policy B comparison",
        "semantic_key": "synthesis:policy-a-policy-b",
        "content": "Policy A requires 30 days and Policy B requires 60 days.",
        "metadata": {"aliases": []},
    }

    assert _policy_designator_conflicts(
        _policy_designators("What retention period does Policy A currently support?"),
        policy_b,
    )
    assert not _policy_designator_conflicts(
        _policy_designators("Compare Policy A and Policy B retention."), comparison
    )
    assert not _policy_designator_conflicts(
        _policy_designators("What happened in the policy timeline?"), policy_b
    )


def test_purpose_aware_query_keeps_exact_identity_ahead_of_kind_priority(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=2)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"exact-identity-order-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-exact-identity",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=begun["input_audit_head"])
    synthesis_action, claim_action = plan["object_actions"]
    synthesis_action["kind"] = "synthesis"
    synthesis_action["semantic_key"] = "exact-order:synthesis"
    synthesis_action["title"] = "Higher kind-priority synthesis"
    synthesis_input_set = {
        "source_revision_ids": [compiled["identity"]["source_revision_id"]],
        "knowledge_revision_ids": [],
        "relation_revision_ids": [],
        "compilation_run_ids": [begun["compilation_run_id"]],
    }
    synthesis_action["synthesis_inputs"] = {
        **synthesis_input_set,
        "input_set_sha256": sha256_bytes(canonical_json(synthesis_input_set).encode("utf-8")),
    }
    claim_action["semantic_key"] = "exact-order:claim"
    claim_action["title"] = "Exact identity claim"
    plan["relation_actions"] = [
        {
            "action": "create",
            "subject": {
                "knowledge_id": None,
                "semantic_key": synthesis_action["semantic_key"],
                "kind": "synthesis",
            },
            "predicate": "supports",
            "object": {
                "knowledge_id": None,
                "semantic_key": claim_action["semantic_key"],
                "kind": "claim",
            },
            "expected_relation_revision_id": None,
            "evidence_refs": claim_action["source_refs"],
            "valid_from": None,
            "valid_to": None,
            "reason": "Connect the exact claim to a graph neighbor.",
        }
    ]
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        claim_id = store.connection.execute(
            """
            SELECT knowledge_id FROM knowledge_revisions_v3
            WHERE semantic_key = ?
            """,
            (claim_action["semantic_key"],),
        ).fetchone()["knowledge_id"]

    result = PurposeAwareRetrievalService(root).query(
        claim_id,
        purpose="answer",
        limit=2,
        graph_hops=1,
        query_plan_version="5",
    )

    assert [item["kind"] for item in result["compiled"]] == ["claim", "synthesis"]
    assert result["compiled"][0]["knowledge_id"] == claim_id
    assert result["compiled"][0]["selection_reason"] == "exact_identity"
    assert "channels" not in result["compiled"][0]
    assert "reranker" not in result["compiled"][0]
    synthesis = result["compiled"][1]
    assert "synthesis_evidence_receipt" not in synthesis


@pytest.mark.parametrize(
    ("policy", "expected_compiled_items", "expected_evidence_items"),
    [
        ("compiled-first-v1", 1, 0),
        ("evidence-first-v1", 0, 1),
        ("balanced-v1", 1, 0),
    ],
)
def test_purpose_aware_single_item_budget_is_not_double_allocated(
    policy: str,
    expected_compiled_items: int,
    expected_evidence_items: int,
) -> None:
    compiled, evidence = PurposeAwareRetrievalService._partition_budget(
        policy,
        limit=1,
        max_chars=200,
    )

    assert compiled == {
        "items": expected_compiled_items,
        "characters": 200 if expected_compiled_items else 0,
    }
    assert evidence == {
        "items": expected_evidence_items,
        "characters": 200 if expected_evidence_items else 0,
    }
    assert compiled["items"] + evidence["items"] == 1
    assert compiled["characters"] + evidence["characters"] == 200


@pytest.mark.parametrize(
    "policy",
    ["compiled-first-v1", "evidence-first-v1", "balanced-v1"],
)
@pytest.mark.parametrize("limit", [1, 2, 3, 8, 20])
@pytest.mark.parametrize("max_chars", [200, 399, 400, 8_000, 20_000])
def test_purpose_aware_partition_never_exceeds_caller_budgets(
    policy: str,
    limit: int,
    max_chars: int,
) -> None:
    compiled, evidence = PurposeAwareRetrievalService._partition_budget(
        policy,
        limit=limit,
        max_chars=max_chars,
    )

    assert compiled["items"] + evidence["items"] <= limit
    assert compiled["characters"] + evidence["characters"] <= max_chars
    for partition in (compiled, evidence):
        assert (partition["items"] == 0) == (partition["characters"] == 0)
        if partition["items"]:
            assert partition["characters"] >= 200


def test_purpose_aware_query_enforces_single_item_budget_across_channels(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=2)
    with KnowledgeVault(root, read_only=False) as vault:
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="purpose-budget-test",
            review_reason="Activate exact source evidence for the cross-channel budget.",
        )
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"cross-channel-budget-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-cross-channel-budget",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=begun)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )

    result = PurposeAwareRetrievalService(root).query(
        "Durable source statement",
        purpose="verify",
        limit=1,
        max_chars=200,
    )

    assert result["policy_id"] == "evidence-first-v1"
    assert result["budget"]["selected_items"] <= 1
    assert result["budget"]["selected_characters"] <= 200
    assert len(canonical_json(result).encode("utf-8")) <= 65_536


def test_query_backfill_requires_draft_validation_and_explicit_promotion(
    tmp_path: Path,
) -> None:
    root, _compiled, _compiler_grant_id = _ready_source(tmp_path, section_count=1)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="backfill-reviewer",
            operations=(
                "promote_knowledge_draft",
                "propose_knowledge_backfill",
            ),
            evaluator_types=("user",),
        )["grant_id"]
    service = BackfillService(root)
    with pytest.raises(ValueError, match="durable"):
        service.propose(
            grant_id=grant_id,
            idempotency_key="rejected",
            query="What should be retained?",
            title="Reusable query synthesis",
            body="This synthesis remains useful across future tasks.",
            kind="synthesis",
            durable=False,
            reusable=True,
            novel=True,
            non_duplicate=True,
            contains_case_data=False,
            source_refs=None,
            source_free=True,
            scope="project",
            sensitivity="private",
            confirm_no_case_data=True,
        )
    proposed = service.propose(
        grant_id=grant_id,
        idempotency_key="accepted",
        query="What should be retained?",
        title="Reusable query synthesis",
        body="This synthesis remains useful across future tasks.",
        kind="synthesis",
        durable=True,
        reusable=True,
        novel=True,
        non_duplicate=True,
        contains_case_data=False,
        source_refs=None,
        source_free=True,
        scope="project",
        sensitivity="private",
        semantic_key="reusable-query-synthesis",
        confirm_no_case_data=True,
    )
    replay = service.propose(
        grant_id=grant_id,
        idempotency_key="accepted",
        query="What should be retained?",
        title="Reusable query synthesis",
        body="This synthesis remains useful across future tasks.",
        kind="synthesis",
        durable=True,
        reusable=True,
        novel=True,
        non_duplicate=True,
        contains_case_data=False,
        source_refs=None,
        source_free=True,
        scope="project",
        sensitivity="private",
        semantic_key="reusable-query-synthesis",
        confirm_no_case_data=True,
    )
    assert replay["draft_id"] == proposed["draft_id"]
    assert replay["idempotent_replay"] is True
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.connection.execute("SELECT COUNT(*) FROM knowledge_objects_v3").fetchone()[0] == 0
        )

    validated = service.validate(
        grant_id=grant_id,
        draft_id=proposed["draft_id"],
        confirm_no_case_data=True,
    )
    assert validated["valid"] is True
    promoted = service.promote(
        grant_id=grant_id,
        draft_id=proposed["draft_id"],
        idempotency_key="promote-accepted",
        evaluator_type="user",
        evaluator_id="owner-review",
        evaluation_reason="Reusable and suitable for governed Agent memory.",
        confirm_no_case_data=True,
    )
    assert promoted["origin"] == "agent_derived"
    assert promoted["authority"] == "agent_derived"
    assert promoted["legal_authority"] is False
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        current = store.get_current(promoted["knowledge_id"])
        assert current["origin"] == "agent_derived"
        assert current["authority"] == "agent_derived"
        assert current["legal_authority"] is False
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]


def test_compilation_capable_sink_reuses_the_domain_coordinator(tmp_path: Path) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=3)
    profile = KnowledgeOS.open(root).compilations.profile()
    begin_request = {
        "operation": "begin_compilation",
        "idempotency_key": "begin-mcp-compilation",
        "confirm_no_case_data": True,
        "source_revision_id": compiled["identity"]["source_revision_id"],
        "compiler_profile": profile["compiler_profile"],
        "compiler_profile_version": profile["compiler_profile_version"],
        "host_identity": "deterministic-fake-agent",
        "prompt_template_id": profile["prompt_template_id"],
        "prompt_config_sha256": profile["prompt_config_sha256"],
        "plan_configuration_sha256": profile["plan_configuration_sha256"],
        "packet_max_fragments": 3,
    }
    definition = knowledge_sink_tool_definition(
        operations=COMPILER_GRANT_OPERATIONS,
        evaluator_types=("agent_self_report",),
    )
    Draft202012Validator(definition.inputSchema).validate(begin_request)
    begun = handle_knowledge_sink(
        begin_request,
        grant_id=grant_id,
        vault_path=root,
    )
    assert begun["schema_version"] == "deeplaw.knowledge-sink-output/v3"
    run_id = begun["result"]["compilation_run_id"]
    replayed_begin = handle_knowledge_sink(
        begin_request,
        grant_id=grant_id,
        vault_path=root,
    )
    assert replayed_begin["result"]["compilation_run_id"] == run_id
    assert replayed_begin["result"]["idempotent_replay"] is True
    with pytest.raises(RuntimeError, match="idempotency key"):
        handle_knowledge_sink(
            {**begin_request, "host_identity": "changed-host"},
            grant_id=grant_id,
            vault_path=root,
        )
    packet = CompilationCoordinator(root).next_packet(run_id)
    assert packet is not None
    stage = handle_knowledge_sink(
        {
            "operation": "stage_compilation_batch",
            "idempotency_key": "stage-mcp-compilation",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
            "plan": _plan(
                packet,
                expected_audit_head=begun["result"]["input_audit_head"],
            ),
        },
        grant_id=grant_id,
        vault_path=root,
    )
    assert stage["result"]["object_count"] == 3
    validated = handle_knowledge_sink(
        {
            "operation": "validate_compilation",
            "idempotency_key": "validate-mcp-compilation",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
        },
        grant_id=grant_id,
        vault_path=root,
    )
    assert validated["result"]["valid"] is True
    committed = handle_knowledge_sink(
        {
            "operation": "commit_compilation",
            "idempotency_key": "commit-mcp-compilation",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
        },
        grant_id=grant_id,
        vault_path=root,
    )
    assert committed["result"]["committed_object_count"] == 3
    assert committed["result"]["idempotent_replay"] is False
    committed_replay = handle_knowledge_sink(
        {
            "operation": "commit_compilation",
            "idempotency_key": "commit-mcp-compilation",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
        },
        grant_id=grant_id,
        vault_path=root,
    )
    assert committed_replay["result"]["idempotent_replay"] is True
    Draft202012Validator(definition.outputSchema).validate(committed)
    resumed = handle_knowledge_sink(
        {
            "operation": "resume_compilation",
            "idempotency_key": "resume-mcp-compilation",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
            "project": True,
        },
        grant_id=grant_id,
        vault_path=root,
    )
    assert resumed["result"]["status"] == "succeeded"
    assert resumed["result"]["projection"]["living_wiki"]["knowledge_count"] == 3
    assert len(canonical_json(resumed).encode("utf-8")) < 65_536
    Draft202012Validator(definition.outputSchema).validate(resumed)
    support = knowledge_tool_definition(autonomous=True)
    status = handle_knowledge_support(
        operation="compilation",
        compilation_action="status",
        compilation_run_id=run_id,
        confirm_no_case_data=True,
        vault_path=root,
    )
    assert status["schema_version"] == "deeplaw.knowledge-support-output/v4"
    assert status["result"]["status"] == "succeeded"
    query = handle_knowledge_support(
        operation="query",
        query="Durable source statement",
        purpose="answer",
        vault_path=root,
    )
    assert query["result"]["policy_id"] == "compiled-first-v1"
    assert query["result"]["compiled"]
    Draft202012Validator(support.outputSchema).validate(status)
    Draft202012Validator(support.outputSchema).validate(query)
    assert KnowledgeOS.open(root).verify()["valid"] is True
    with pytest.raises(
        KnowledgeOSValidationError,
        match="public contract",
    ):
        KnowledgeOS.open(root).context.compile(task="Durable source statement")

    with pytest.raises(
        KnowledgeOSValidationError,
        match="registered compiler profile",
    ):
        KnowledgeOS.open(root).compilations.begin(
            grant_id=grant_id,
            source_revision_id=compiled["identity"]["source_revision_id"],
            compiler_profile=profile["compiler_profile"],
            compiler_profile_version=profile["compiler_profile_version"],
            host_identity="tampered-profile",
            prompt_template_id=profile["prompt_template_id"],
            prompt_config_sha256="0" * 64,
            plan_configuration_sha256=profile["plan_configuration_sha256"],
            confirm_no_case_data=True,
        )


def test_compiler_profile_and_uncompiled_inventory_are_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "deeplaw.knowledge_compiler.mimetypes.guess_type",
        lambda _name: ("application/x-host-specific", None),
    )
    root, compiled, _grant_id = _ready_source(tmp_path, section_count=2)

    profile = handle_knowledge_support(
        operation="compilation",
        compilation_action="profile",
        confirm_no_case_data=True,
        vault_path=root,
    )["result"]
    assert profile["compiler_profile"] == "living-wiki-agent"
    assert profile["prompt_template_id"] == "deeplaw.living-wiki-compile/v1"
    assert len(profile["prompt_config_sha256"]) == 64
    assert len(profile["plan_configuration_sha256"]) == 64

    inventory = handle_knowledge_support(
        operation="compilation",
        compilation_action="list_uncompiled",
        confirm_no_case_data=True,
        limit=1,
        vault_path=root,
    )["result"]
    assert inventory["sources"] == [
        {
            "source_revision_id": compiled["identity"]["source_revision_id"],
            "title": "source",
            "source_kind": "document",
            "media_type": "text/markdown",
            "media_identity": "text/markdown",
            "content_sha256": compiled["source"]["content_sha256"],
            "byte_size": compiled["source"]["byte_size"],
            "instruction_risk": False,
            "status": "pending",
        }
    ]
    assert inventory["next_after_source_revision_id"] is None


def test_sink_advertises_the_complete_closed_compilation_plan_contract() -> None:
    definition = knowledge_sink_tool_definition(
        operations=COMPILER_GRANT_OPERATIONS,
        evaluator_types=("agent_self_report",),
    )
    schema = definition.inputSchema
    stage_branch = next(
        branch
        for branch in schema["oneOf"]
        if "allOf" in branch
        and branch["allOf"][1]["properties"]["operation"].get("const") == "stage_compilation_batch"
    )
    assert stage_branch["allOf"][1]["properties"]["plan"] == {"$ref": "#/$defs/compilationPlan"}
    plan = schema["$defs"]["compilationPlan"]
    assert plan["additionalProperties"] is False
    assert {
        "source_revision_id",
        "packet_id",
        "expected_audit_head",
        "object_actions",
        "relation_actions",
        "identity_actions",
        "coverage",
    }.issubset(plan["required"])
    assert plan["$defs"]["objectAction"]["additionalProperties"] is False


def test_staged_packet_can_be_atomically_replaced_before_validation(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=2)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"restage-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-restage",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    first = _plan(packet, expected_audit_head=begun["input_audit_head"])
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=first,
        confirm_no_case_data=True,
    )
    replacement = _plan(packet, expected_audit_head=begun["input_audit_head"])
    replacement["object_actions"][0]["title"] = "Corrected compiled source claim"
    replacement["object_actions"][0]["body"] += "\n\nValidated correction."
    restaged = coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=replacement,
        confirm_no_case_data=True,
    )
    assert restaged["idempotent_replay"] is False
    validated = coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert validated["staged_object_count"] == 2


def test_validation_fails_closed_when_a_staged_plan_changes_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=2)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"validation-cas-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-validation-cas",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    original_plan = _plan(packet, expected_audit_head=begun["input_audit_head"])
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=original_plan,
        confirm_no_case_data=True,
    )
    replacement = _plan(packet, expected_audit_head=begun["input_audit_head"])
    replacement["object_actions"][0]["body"] += "\n\nConcurrent corrected plan."
    original_prepare = CompilationCoordinator._prepare_object
    replaced = False

    def replace_during_validation(
        self: CompilationCoordinator,
        store: AutonomousKnowledgeStore,
        *,
        run: object,
        grant: object,
        row: object,
        action: dict,
        recorded_at: str,
    ) -> dict:
        nonlocal replaced
        if not replaced:
            replaced = True
            CompilationCoordinator(root).stage(
                grant_id=grant_id,
                compilation_run_id=begun["compilation_run_id"],
                plan=replacement,
                confirm_no_case_data=True,
            )
        return original_prepare(
            self,
            store,
            run=run,
            grant=grant,
            row=row,
            action=action,
            recorded_at=recorded_at,
        )

    monkeypatch.setattr(
        CompilationCoordinator,
        "_prepare_object",
        replace_during_validation,
    )
    with pytest.raises(RuntimeError, match="validation precondition changed"):
        coordinator.validate(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            confirm_no_case_data=True,
        )
    monkeypatch.setattr(
        CompilationCoordinator,
        "_prepare_object",
        original_prepare,
    )
    validated = coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert validated["valid"] is True
    assert len(validated["plan_inventory_sha256"]) == 64


def test_empty_semantic_compilation_cannot_report_success(tmp_path: Path) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=1)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"empty-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-empty",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
    empty = {
        **_plan(packet, expected_audit_head=begun["input_audit_head"]),
        "object_actions": [],
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": [],
            "omitted_fragment_ids": fragment_ids,
            "ratio": 0.0,
            "completeness": "empty",
        },
        "skipped_fragments": [
            {"fragment_id": item, "reason": "No reusable semantic output."} for item in fragment_ids
        ],
    }
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=empty,
        confirm_no_case_data=True,
    )
    with pytest.raises(ValueError, match="semantic output"):
        coordinator.validate(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            confirm_no_case_data=True,
        )


def test_packet_byte_budget_is_hard_and_packet_policy_changes_run_identity(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=12)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"packet-budget-v1")
    common = {
        "grant_id": grant_id,
        "source_revision_id": compiled["identity"]["source_revision_id"],
        "compiler_profile": "living-wiki-packet-budget",
        "compiler_profile_version": "1",
        "host_identity": "deterministic-fake-agent",
        "model_identity": None,
        "prompt_template_id": "deeplaw.compile.fake/v1",
        "prompt_config_sha256": configuration_sha256,
        "plan_configuration_sha256": configuration_sha256,
        "confirm_no_case_data": True,
    }
    wide = coordinator.begin(**common, packet_max_fragments=64)
    narrow = coordinator.begin(**common, packet_max_fragments=1)
    assert wide["compilation_run_id"] != narrow["compilation_run_id"]
    packet = coordinator.next_packet(wide["compilation_run_id"])
    assert packet is not None
    assert len(canonical_json(packet).encode("utf-8")) <= MAX_PACKET_PROVIDER_BYTES


def test_compiler_reuses_exact_identity_and_preserves_explicit_ambiguity(
    tmp_path: Path,
) -> None:
    root, compiled, compiler_grant_id = _ready_source(tmp_path, section_count=1)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        identity_grant_id = store.enable_grant(
            writer_id="identity-fixture",
            operations=("upsert_entity",),
        )["grant_id"]
        canonical = store.remember(
            grant_id=identity_grant_id,
            idempotency_key="canonical-acme",
            title="Acme Corporation",
            body="The existing canonical entity.",
            kind="entity",
            operation="upsert_entity",
            semantic_key="acme-canonical",
            aliases=["ACME"],
            confirm_no_case_data=True,
        )

    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"identity-exact-v1")
    exact_run = coordinator.begin(
        grant_id=compiler_grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-identity-exact",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    exact_packet = coordinator.next_packet(exact_run["compilation_run_id"])
    assert exact_packet is not None
    exact_plan = _plan(
        exact_packet,
        expected_audit_head=exact_run["input_audit_head"],
    )
    exact_action = exact_plan["object_actions"][0]
    exact_action.update(
        {
            "kind": "entity",
            "semantic_key": "acme-canonical",
            "title": "Acme Corporation",
            "aliases": ["ACME", "Acme Corp."],
            "body": "The existing canonical entity now has exact source-bound evidence.",
        }
    )
    coordinator.stage(
        grant_id=compiler_grant_id,
        compilation_run_id=exact_run["compilation_run_id"],
        plan=exact_plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=compiler_grant_id,
        compilation_run_id=exact_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    exact_receipt = coordinator.commit(
        grant_id=compiler_grant_id,
        compilation_run_id=exact_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    assert exact_receipt["committed_object_count"] == 1
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        entities = store.connection.execute(
            """
            SELECT knowledge_id, current_revision_id
            FROM knowledge_objects_v3 WHERE kind = 'entity'
            """
        ).fetchall()
        assert len(entities) == 1
        assert entities[0]["knowledge_id"] == canonical["knowledge_id"]
        assert entities[0]["current_revision_id"] != canonical["revision_id"]

    ambiguous_configuration_sha256 = sha256_bytes(b"identity-ambiguous-v1")
    ambiguous_run = coordinator.begin(
        grant_id=compiler_grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-identity-ambiguous",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=ambiguous_configuration_sha256,
        plan_configuration_sha256=ambiguous_configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    ambiguous_packet = coordinator.next_packet(ambiguous_run["compilation_run_id"])
    assert ambiguous_packet is not None
    ambiguous_plan = _plan(
        ambiguous_packet,
        expected_audit_head=ambiguous_run["input_audit_head"],
    )
    ambiguous_action = ambiguous_plan["object_actions"][0]
    ambiguous_action.update(
        {
            "kind": "entity",
            "semantic_key": "acme-unrelated-source-identity",
            "title": "ACME",
            "aliases": [],
            "body": "A same-name source entity that must remain independently identified.",
        }
    )
    ambiguous_plan["identity_actions"] = [
        {
            "action": "possible_duplicate",
            "subject": {
                "knowledge_id": None,
                "semantic_key": "acme-unrelated-source-identity",
                "kind": "entity",
            },
            "objects": [
                {
                    "knowledge_id": canonical["knowledge_id"],
                    "semantic_key": None,
                    "kind": "entity",
                }
            ],
            "evidence_refs": ambiguous_action["source_refs"],
            "reason": "The exact alias is shared, but the source does not prove identity.",
        }
    ]
    coordinator.stage(
        grant_id=compiler_grant_id,
        compilation_run_id=ambiguous_run["compilation_run_id"],
        plan=ambiguous_plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=compiler_grant_id,
        compilation_run_id=ambiguous_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=compiler_grant_id,
        compilation_run_id=ambiguous_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM knowledge_objects_v3 WHERE kind = 'entity'"
            ).fetchone()[0]
            == 2
        )
        candidate = store.connection.execute(
            """
            SELECT status, candidate_json
            FROM source_compilation_identity_candidates_v1
            WHERE compilation_run_id = ?
            """,
            (ambiguous_run["compilation_run_id"],),
        ).fetchone()
        assert candidate is not None
        assert candidate["status"] == "ambiguous"
        assert strict_json_loads(candidate["candidate_json"])["action"] == ("possible_duplicate")


def test_relation_freshness_propagates_from_changed_endpoint_revision(
    tmp_path: Path,
) -> None:
    root, compiled, grant_id = _ready_source(tmp_path, section_count=2)
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"relation-dependency-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-relation-dependency",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    plan = _plan(packet, expected_audit_head=begun["input_audit_head"])
    assert len(plan["object_actions"]) == 2
    subject_action, object_action = plan["object_actions"]
    plan["relation_actions"] = [
        {
            "action": "create",
            "subject": {
                "knowledge_id": None,
                "semantic_key": subject_action["semantic_key"],
                "kind": "claim",
            },
            "predicate": "supports",
            "object": {
                "knowledge_id": None,
                "semantic_key": object_action["semantic_key"],
                "kind": "claim",
            },
            "expected_relation_revision_id": None,
            "evidence_refs": object_action["source_refs"],
            "valid_from": None,
            "valid_to": None,
            "reason": "The second unchanged section supports the first claim.",
        }
    ]
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    receipt = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    relation_revision_id = receipt["relation_revision_ids"][0]
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        subject_revision = store.connection.execute(
            """
            SELECT revision_id FROM knowledge_revisions_v3
            WHERE semantic_key = ?
            """,
            (subject_action["semantic_key"],),
        ).fetchone()
        assert subject_revision is not None
        subject_revision_id = subject_revision["revision_id"]
        endpoint_dependencies = {
            row["input_id"]
            for row in store.connection.execute(
                """
                SELECT input_id FROM revision_dependencies_v1
                WHERE consumer_kind = 'relation_revision'
                  AND consumer_revision_id = ?
                  AND input_kind = 'knowledge_revision'
                """,
                (relation_revision_id,),
            )
        }
        assert subject_revision_id in endpoint_dependencies

    synthesis_source = tmp_path / "relation-synthesis.md"
    synthesis_source.write_text(
        "# Relation synthesis\nThe Overview binds the exact relation revision.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled_synthesis_source = compile_source(
            vault,
            synthesis_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    synthesis_run = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled_synthesis_source["identity"]["source_revision_id"],
        compiler_profile="living-wiki-relation-synthesis",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    synthesis_packet = coordinator.next_packet(synthesis_run["compilation_run_id"])
    assert synthesis_packet is not None
    synthesis_plan = _plan(
        synthesis_packet,
        expected_audit_head=synthesis_run["input_audit_head"],
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        synthesis_key = f"overview:{store.vault_id}"
    synthesis_input_set = {
        "source_revision_ids": [
            compiled_synthesis_source["identity"]["source_revision_id"]
        ],
        "knowledge_revision_ids": [],
        "relation_revision_ids": [relation_revision_id],
        "compilation_run_ids": sorted(
            (begun["compilation_run_id"], synthesis_run["compilation_run_id"])
        ),
    }
    synthesis_plan["object_actions"].append(
        {
            **synthesis_plan["object_actions"][0],
            "semantic_key": synthesis_key,
            "title": "Relation-aware Overview",
            "body": "This Overview depends on one exact governed relation revision.",
            "kind": "synthesis",
            "synthesis_inputs": {
                **synthesis_input_set,
                "input_set_sha256": sha256_bytes(
                    canonical_json(synthesis_input_set).encode("utf-8")
                ),
            },
            "reason": "Exercise relation-to-Synthesis freshness propagation.",
        }
    )
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=synthesis_run["compilation_run_id"],
        plan=synthesis_plan,
        confirm_no_case_data=True,
    )
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=synthesis_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    synthesis_receipt = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=synthesis_run["compilation_run_id"],
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        synthesis_revision_id = store.connection.execute(
            "SELECT revision_id FROM knowledge_revisions_v3 WHERE semantic_key = ?",
            (synthesis_key,),
        ).fetchone()["revision_id"]
    assert synthesis_revision_id in synthesis_receipt["knowledge_revision_ids"]

    source = tmp_path / "source.md"
    source.write_text(
        "# Section 1\nChanged durable source statement 1.\n\n"
        "# Section 2\nDurable source statement 2.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        successor = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(successor["source"]["source_id"])
        vault.approve_source_assets(
            successor["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="relation-freshness-test",
            review_reason="Activate the changed Source Revision.",
        )
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        replacement_source_revision_id=successor["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert relation_revision_id in report["affected_relation_revision_ids"]
    assert synthesis_revision_id in report["affected_knowledge_revision_ids"]
    assert len(report["synthesis_refresh_task_ids"]) == 1
    assert SynthesisRefreshService(root).tasks(status="planned")[0][
        "target_revision_id"
    ] == synthesis_revision_id
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        endpoint_dependency = store.connection.execute(
            """
            SELECT freshness FROM revision_dependencies_v1
            WHERE consumer_kind = 'relation_revision'
              AND consumer_revision_id = ?
              AND input_kind = 'knowledge_revision'
              AND input_id = ?
            """,
            (relation_revision_id, subject_revision_id),
        ).fetchone()
        assert endpoint_dependency is not None
        assert endpoint_dependency["freshness"] == "stale"
        assert all(
            relation["relation_revision_id"] != relation_revision_id
            for relation in store.graph(limit=100)["relations"]
        )

    refresh_service = SynthesisRefreshService(root)
    refresh_task = refresh_service.tasks(status="planned")[0]
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        synthesis_grant_id = store.enable_grant(
            writer_id="deterministic-synthesis-recovery-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    refresh_digest = sha256_bytes(b"relation-synthesis-recovery/v1")
    refresh_run = refresh_service.begin(
        grant_id=synthesis_grant_id,
        refresh_task_id=refresh_task["refresh_task_id"],
        source_revision_ids=[successor["identity"]["source_revision_id"]],
        knowledge_revision_ids=[],
        relation_revision_ids=[],
        host_identity="deterministic-synthesis-recovery-agent",
        model_identity=None,
        profile_id="deeplaw.synthesis-recovery.test/v1",
        prompt_sha256=refresh_digest,
        config_sha256=refresh_digest,
        confirm_no_case_data=True,
    )
    resumed = refresh_service.resume(
        grant_id=synthesis_grant_id,
        synthesis_refresh_run_id=refresh_run["synthesis_refresh_run_id"],
        project=False,
        confirm_no_case_data=True,
    )
    assert resumed["transaction"]["status"] == "planned"
    aborted = refresh_service.abort(
        grant_id=synthesis_grant_id,
        synthesis_refresh_run_id=refresh_run["synthesis_refresh_run_id"],
        reason="Exercise recoverable pre-commit synthesis abort.",
        confirm_no_case_data=True,
    )
    assert aborted["transaction"]["status"] == "aborted"
    assert refresh_service.tasks(status="blocked")[0]["refresh_task_id"] == (
        refresh_task["refresh_task_id"]
    )
    with pytest.raises(RuntimeError, match="cannot be resumed"):
        refresh_service.resume(
            grant_id=synthesis_grant_id,
            synthesis_refresh_run_id=refresh_run["synthesis_refresh_run_id"],
            project=False,
            confirm_no_case_data=True,
        )


def test_old_vault_migration_snapshot_restore_and_rollback_preserve_compilation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "legacy-vault"
    initialize_knowledge_vault(root, name="compiler-migration", scope="project")
    source = tmp_path / "migration-source.md"
    source.write_text("# Migration\nDurable migrated source statement.", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    assert autonomous_core_installed(root) is False

    backup = tmp_path / "pre-compiler-backup"
    migration = migrate_autonomous_core(root, backup_output=backup)
    assert migration["verification"]["valid"] is True
    assert migration["backup_path"] == str(backup)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deterministic-fake-agent",
            operations=COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    coordinator = CompilationCoordinator(root)
    configuration_sha256 = sha256_bytes(b"migration-round-trip-v1")
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile="living-wiki-migration",
        compiler_profile_version="1",
        host_identity="deterministic-fake-agent",
        model_identity=None,
        prompt_template_id="deeplaw.compile.fake/v1",
        prompt_config_sha256=configuration_sha256,
        plan_configuration_sha256=configuration_sha256,
        confirm_no_case_data=True,
        packet_max_fragments=8,
    )
    _stage_all(coordinator, grant_id=grant_id, begun=begun)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    completed = coordinator.resume(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        project=True,
        confirm_no_case_data=True,
    )
    assert completed["status"] == "succeeded"

    snapshot = tmp_path / "compiler-snapshot"
    create_autonomous_snapshot(root, snapshot)
    assert verify_autonomous_snapshot(snapshot)["valid"] is True
    restored = tmp_path / "restored-vault"
    restore_autonomous_snapshot(restored, snapshot=snapshot, confirm=True)
    restored_os = KnowledgeOS.open(restored)
    assert restored_os.compilations.status(begun["compilation_run_id"])["status"] == "succeeded"
    assert restored_os.retrieval.query(
        "Durable migrated source statement.",
        purpose="answer",
    )["compiled"]

    rolled_back = rollback_autonomous_core(root, backup=backup, confirm=True)
    assert rolled_back["autonomous_core_present_after_rollback"] is False
    with KnowledgeVault(root, read_only=True) as vault:
        assert vault.verify_integrity()["valid"] is True
