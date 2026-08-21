from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw import cli
from deeplaw.host_connect import build_host_connect_plan
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_store import initialize_knowledge_vault
from examples.living_wiki.run_demo import run_demo

REPOSITORY = Path(__file__).resolve().parents[1]


def _command_parser(name: str):
    parser = cli._parser()
    action = next(
        item for item in parser._actions if getattr(item, "choices", None)
    )
    return action.choices[name]


def _subparsers(parser):
    return next(
        item
        for item in parser._actions
        if isinstance(item, argparse._SubParsersAction)
    )


def test_default_root_help_is_basic_and_old_commands_still_parse() -> None:
    help_text = cli._parser().format_help()
    assert "Basic journey" in help_text
    assert "migrate-capabilities" not in help_text
    assert "challenge-trace" not in help_text
    assert "pdf-evidence" not in help_text
    assert "official" not in help_text
    assert "maintainer" not in help_text

    assert cli._parser().parse_args(["official", "status"]).command == "official"
    assert (
        cli._parser()
        .parse_args(
            [
                "migrate-capabilities",
                "--db",
                "release.sqlite3",
            ]
        )
        .command
        == "migrate-capabilities"
    )


def test_default_knowledge_help_shows_journey_not_state_machine() -> None:
    knowledge = _command_parser("knowledge")
    help_text = knowledge.format_help()
    assert "Basic journey" in help_text
    assert _subparsers(knowledge).metavar == (
        "{init,doctor,source,compile,reconcile,query,context,wiki,snapshot,forget,host,task}"
    )
    for command in (
        "init",
        "doctor",
        "source",
        "compile",
        "reconcile",
        "host",
        "task",
        "query",
        "context",
        "wiki",
        "snapshot",
        "forget",
    ):
        assert command in help_text
    for hidden in (
        "semantic",
        "synthesis",
        "backfill",
        "diagnose-retrieval",
        "retrieval-profile",
        "discovery-model",
        "sink",
    ):
        assert hidden not in help_text

    parsed = cli._parser().parse_args(
        ["knowledge", "semantic", "profile", "--vault", "vault"]
    )
    assert parsed.knowledge_command == "semantic"
    reconcile = cli._parser().parse_args(
        [
            "knowledge",
            "reconcile",
            "--vault",
            "vault",
            "--grant-id",
            "grant-test",
            "--confirm-no-case-data",
        ]
    )
    assert reconcile.knowledge_command == "reconcile"


def test_source_free_living_wiki_demo_completes_first_successful_journey(
    tmp_path: Path,
) -> None:
    result = run_demo(tmp_path / "living-wiki-demo")

    journey = result["journey"]
    assert all(item["status"] == "executed" for item in journey.values())
    assert journey["init_doctor"]["canonical_verification_valid"] is True
    assert journey["compilation_handoff"] == {
        "status": "executed",
        "write_performed": False,
        "grant_included": False,
        "model_invoked": False,
        "read_leaf": "knowledge_support",
        "write_leaf": "knowledge_sink",
    }
    assert journey["query"]["selected_statement_count"] >= 1
    assert journey["context"]["provider_content_bytes"] <= 65_536
    assert journey["wiki_exact_source_drill_down"]["source_revision_present"] is True
    assert result["compilation"]["compiler_profile_version"] == "3"


def test_layered_help_exposes_advanced_and_admin_inventory(capsys) -> None:
    with pytest.raises(SystemExit) as advanced_exit:
        cli._parser().parse_args(["knowledge", "--help-advanced"])
    assert advanced_exit.value.code == 0
    advanced = capsys.readouterr().out
    assert "semantic" in advanced
    assert "synthesis" in advanced
    assert "backfill" in advanced
    assert "sink" not in advanced

    with pytest.raises(SystemExit) as admin_exit:
        cli._parser().parse_args(["knowledge", "--help-admin"])
    assert admin_exit.value.code == 0
    admin = capsys.readouterr().out
    assert "sink" in admin
    assert "migrate" in admin


def test_sink_apply_help_matches_additive_v2_through_v6_contracts() -> None:
    knowledge = _command_parser("knowledge")
    action = _subparsers(knowledge)
    sink = action.choices["sink"]
    sink_action = _subparsers(sink)
    apply_help = sink_action.choices["apply"].format_help()
    assert "v2-v6" in apply_help
    assert "selected from the active grant" in " ".join(apply_help.split())


def test_host_connect_builds_read_only_config_without_owning_host_or_auth(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="host-connect", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        audit_before = store.audit_head

    for host in ("codex", "claude-code", "opencode"):
        plan = build_host_connect_plan(
            host=host,
            vault_path=vault,
            owner_home=tmp_path / "owner-home",
        )
        assert plan["schema_version"] == "deeplaw.host-connect-plan/v2"
        assert plan["host"] == host
        assert plan["server_leaf"] == "knowledge_support"
        assert plan["read_only"] is True
        assert plan["write_performed"] is True
        assert plan["owner_local_binding"]["configured"] is True
        assert plan["owner_local_binding"]["path_included"] is False
        assert plan["authentication_managed"] is False
        assert plan["host_runtime_managed"] is False
        assert plan["task_binding_configured"] is False
        assert plan["task_binding_sha256"] is None
        assert plan["task_handle_configured"] is False
        assert plan["task_handle_sha256"] is None
        assert plan["preflight"] == {
            "vault_ready": True,
            "canonical_valid": True,
            "autonomous_core_installed": True,
            "schema_core_installed": True,
            "read_seam_callable": True,
            "compiled_knowledge_available": False,
            "source_only_honest_gap_available": False,
            "blocked": False,
        }
        assert plan["context_preflight"]["status"] == "empty_honest_gap"
        assert plan["context_preflight"]["provider_payload_bytes"] <= 65_536
        assert plan["context_preflight"]["write_performed"] is False
        assert plan["context_preflight"]["audit_head_unchanged"] is True
        readiness = plan["readiness"]
        assert readiness["schema_version"] == "deeplaw.host-product-readiness/v1"
        assert readiness["autonomous_vault_ready"] is True
        assert readiness["mcp"] == {
            "mode": "compact_current_with_internal_compatibility",
            "input_schema": "deeplaw.knowledge-support-input/v7",
            "output_schema": "deeplaw.knowledge-support-output/v6",
            "advertised_operations": ["query", "context", "explain"],
            "compatibility_inputs": ["v1", "v2", "v3", "v4", "v5", "v6"],
            "compatibility_outputs": ["v1", "v2", "v3", "v4", "v5"],
        }
        assert [item["host"] for item in readiness["hosts"]] == [host]
        assert readiness["hosts"][0]["status"] == "owner_verification_required"
        assert {gap["code"] for gap in readiness["hosts"][0]["gaps"]} == {
            "host_plugin_load_unverified",
            "mcp_registration_unverified",
            "closed_environment_unverified",
        }
        rendered = str(plan["configuration"])
        assert "knowledge" in rendered
        assert "mcp" in rendered
        assert "sink" not in rendered
        assert "--task-binding" not in rendered
        assert "--task-handle" not in rendered

    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.audit_head == audit_before


def test_host_connect_help_is_task_neutral_and_legacy_flags_fail_with_migration() -> None:
    parser = cli._parser()
    knowledge = _command_parser("knowledge")
    host = _subparsers(knowledge).choices["host"]
    connect = _subparsers(host).choices["connect"]
    help_text = connect.format_help()
    assert "--task-binding" not in help_text
    assert "--task-handle" not in help_text

    legacy = parser.parse_args(
        [
            "knowledge",
            "host",
            "connect",
            "--host",
            "codex",
            "--vault",
            "vault",
            "--task-handle",
            "taskh_opaque",
        ]
    )
    with pytest.raises(ValueError, match=r"task-neutral.*enroll-host-session"):
        from deeplaw.knowledge_cli import handle_knowledge_command

        handle_knowledge_command(legacy)


def test_host_connect_v2_contract_forbids_task_bound_static_configuration() -> None:
    schema = json.loads(
        (REPOSITORY / "contracts/host-connect-plan.v2.schema.json").read_bytes()
    )
    Draft202012Validator.check_schema(schema)
    properties = schema["properties"]
    assert properties["task_binding_configured"] == {"const": False}
    assert properties["task_binding_sha256"] == {"type": "null"}
    assert properties["task_handle_configured"] == {"const": False}
    assert properties["task_handle_sha256"] == {"type": "null"}
    assert "readiness" in schema["required"]
    assert schema["$defs"]["stdioArguments"]["maxItems"] == 6
    assert schema["$defs"]["fullCommand"]["maxItems"] == 7
    assert schema["$defs"]["codexAddCommand"]["maxItems"] == 12

    schema = json.loads(
        (REPOSITORY / "contracts/host-connect-plan.v2.schema.json").read_bytes()
    )
    Draft202012Validator.check_schema(schema)


def test_host_connect_is_a_real_cli_surface() -> None:
    parsed = cli._parser().parse_args(
        [
            "knowledge",
            "host",
            "connect",
            "--host",
            "codex",
            "--vault",
            "vault",
        ]
    )
    assert parsed.knowledge_command == "host"
    assert parsed.host_command == "connect"

    task = cli._parser().parse_args(
        [
            "knowledge",
            "task",
            "start",
            "--project",
            "DeepLaw",
            "--task",
            "Continue the bounded task.",
        ]
    )
    assert task.knowledge_command == "task"
    assert task.task_command == "start"


def test_product_manifest_records_current_surface_and_preserves_callers() -> None:
    schema = json.loads(
        (REPOSITORY / "contracts/product-surface-manifest.v1.schema.json").read_bytes()
    )
    manifest = json.loads(
        (REPOSITORY / "governance/product-surface-manifest.v1.json").read_bytes()
    )
    Draft202012Validator(schema).validate(manifest)
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["package_version"] == project["project"]["version"]
    assert manifest["lifecycle_status"] == "source_candidate"
    assert manifest["release_ready"] is False
    assert [item["name"] for item in manifest["default_product"]] == [
        "init",
        "doctor",
        "source add",
        "compile",
        "reconcile",
        "query/context",
        "snapshot",
        "forget",
        "host connect",
        "task continuity",
    ]
    host_connect = next(
        item
        for item in manifest["surfaces"]
        if item["surface_id"] == "default.host_connect"
    )
    assert host_connect["product_role"] == "Driver"
    assert host_connect["lifecycle"] == "Active"
    assert "deeplaw knowledge host connect" in host_connect["bindings"]
    assert "contracts/host-connect-plan.v2.schema.json" in host_connect["bindings"]
    task_continuity = next(
        item
        for item in manifest["surfaces"]
        if item["surface_id"] == "default.task_continuity"
    )
    assert "enroll-host-session" in task_continuity["bindings"][0]
    assert "contracts/host-session-route-result.v2.schema.json" in task_continuity[
        "bindings"
    ]
    compatibility_contracts = next(
        item
        for item in manifest["surfaces"]
        if item["surface_id"] == "compatibility.legacy_contracts"
    )["bindings"]
    assert "contracts/host-connect-plan.v1.schema.json" in compatibility_contracts
    knowledge_support = next(
        item
        for item in manifest["external_callers"]
        if item["caller"] == "knowledge_support"
    )
    assert knowledge_support["current_bindings"] == [
        "knowledge_support leaf",
        "contracts/knowledge-support.input.v7.schema.json",
        "contracts/knowledge-support.output.v6.schema.json",
        "advertised operations: query, context, explain",
    ]
    assert knowledge_support["compatibility_bindings"] == [
        "contracts/knowledge-support.input.v1.schema.json through v6 (internal compatibility)",
        "contracts/knowledge-support.output.v1.schema.json through v5 (internal compatibility)",
    ]
    tests_contracts = next(
        item
        for item in manifest["external_callers"]
        if item["caller"] == "tests/contracts"
    )
    assert "benchmarks/release/v013-gate-classification-v9.json" in tests_contracts[
        "current_bindings"
    ]
    assert "historical Gate v1-v8 classifications and migration fixtures" in tests_contracts[
        "compatibility_bindings"
    ]
    assert all(
        "knowledge_support operation=wiki" not in binding
        for surface in manifest["surfaces"]
        for binding in surface["bindings"]
    )
    assert {item["product_role"] for item in manifest["surfaces"]} <= {
        "Core",
        "Driver",
        "Compatibility",
        "Experiment",
    }
    assert {item["lifecycle"] for item in manifest["surfaces"]} <= {
        "Active",
        "Hidden",
        "Deprecated",
        "Deferred",
        "Retired",
    }
    assert all(
        legacy not in item
        for item in manifest["surfaces"]
        for legacy in ("category", "disposition", "status")
    )
    assert {item["caller"] for item in manifest["external_callers"]} == {
        "CLI",
        "knowledge_support",
        "knowledge_sink",
        "law_support",
        "plugins/skills",
        "adapters",
        "tests/contracts",
        "historical persisted data",
    }


def test_current_documented_product_truth_cannot_drift_to_historical_state() -> None:
    active = json.loads(
        (REPOSITORY / "benchmarks/v013/active-qualification-v3.json").read_bytes()
    )
    assert active["schema_version"] == "deeplaw.v013-active-qualification/v3"
    assert active["profile"] == "kernel_release_core"
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    assert active["candidate_version"] == version
    assert active["candidate_binding"]["package_version"] == version
    expected_stage = {
        "0.12.0": ("machine_evaluation_pending", "machine_evaluation_not_executed"),
        "0.13.0": (
            "construction_candidate_machine_evaluation_pending",
            "candidate_artifact_not_built",
        ),
    }
    assert (active["status"], active["blocker"]) == expected_stage[version]
    assert active["release_ready"] is False
    assert active["claim_eligible"] is False

    prd = (REPOSITORY / "docs/PRODUCT_REQUIREMENTS.md").read_text(encoding="utf-8")
    architecture = (REPOSITORY / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    chinese = (REPOSITORY / "README.md").read_text(encoding="utf-8")
    english = (REPOSITORY / "README_EN.md").read_text(encoding="utf-8")
    assert "PRD revision: **1.3.3**" in prd
    assert "latest committed pass-specific disposition" not in prd
    assert "Gate v6" not in architecture
    assert "Pass 21" not in architecture
    for readme in (chinese, english):
        assert "V0_13_PASS" not in readme
        assert "host connect --host codex --vault ./vault" in readme
        assert "host connect --host codex --vault ./vault --task" not in readme
        assert "knowledge sink enable --vault ./vault" in readme
        assert "--operation record_run --operation remember --operation forget" in readme
        assert "knowledge task checkpoint --vault ./vault" in readme
        assert "knowledge task timeline --vault ./vault" in readme
        assert "knowledge-support input v7 / output v6" in readme
