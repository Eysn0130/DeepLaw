from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from deeplaw import cli
from deeplaw.host_connect import build_host_connect_plan
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_store import initialize_knowledge_vault

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
        "{init,doctor,source,compile,reconcile,task,host,context,wiki,snapshot,forget}"
    )
    for command in (
        "init",
        "doctor",
        "source",
        "compile",
        "reconcile",
        "host",
        "task",
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


def test_sink_apply_help_matches_additive_v2_through_v5_contracts() -> None:
    knowledge = _command_parser("knowledge")
    action = _subparsers(knowledge)
    sink = action.choices["sink"]
    sink_action = _subparsers(sink)
    apply_help = sink_action.choices["apply"].format_help()
    assert "v2-v5" in apply_help
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
        rendered = str(plan["configuration"])
        assert "knowledge" in rendered
        assert "mcp" in rendered
        assert "sink" not in rendered

    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.audit_head == audit_before

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
    compatibility_contracts = next(
        item
        for item in manifest["surfaces"]
        if item["surface_id"] == "compatibility.legacy_contracts"
    )["bindings"]
    assert "contracts/host-connect-plan.v1.schema.json" in compatibility_contracts
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
