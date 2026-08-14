from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.api import KnowledgeOS
from deeplaw.editor_bridge import (
    bridge_contract,
    context_for_editor,
    merge_standard_mcp_config,
    tolaria_context_envelope,
    tolaria_mcp_servers,
    tolaria_open_note_request,
    validate_editor_context,
    validate_editor_write_target,
)
from deeplaw.knowledge_autonomy import initialize_autonomous_core
from deeplaw.knowledge_store import initialize_knowledge_vault

REPOSITORY = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict[str, object]:
    return json.loads((REPOSITORY / "contracts" / name).read_text(encoding="utf-8"))


def _envelope(vault_id: str, *, frontend: str = "obsidian") -> dict[str, object]:
    return {
        "schema_version": "deeplaw.editor-context-envelope/v1",
        "frontend": frontend,
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
        "user_intent": "Find the governed definition of the admission boundary.",
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
    }


def test_editor_context_and_bridge_manifests_match_closed_contracts() -> None:
    envelope = _envelope("vault_" + "a" * 24)
    assert validate_editor_context(envelope) == envelope
    envelope["selected_text"] = "x" * 501
    envelope["selection_range"] = {"start": 0, "end": 501}
    with pytest.raises(ValueError, match="declared budget"):
        validate_editor_context(envelope)
    envelope["selected_text"] = "bounded"
    envelope["selection_range"] = {"start": 10, "end": 16}
    with pytest.raises(ValueError, match="does not bind"):
        validate_editor_context(envelope)

    for frontend, schema_name in (
        ("obsidian", "obsidian-bridge.v1.schema.json"),
        ("tolaria", "tolaria-bridge.v1.schema.json"),
    ):
        expected = bridge_contract(frontend)  # type: ignore[arg-type]
        manifest = json.loads(
            (REPOSITORY / "adapters" / frontend / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest == expected
        Draft202012Validator(
            _schema(schema_name),
            format_checker=FormatChecker(),
        ).validate(manifest)


@pytest.mark.parametrize(
    ("frontend", "path"),
    (
        ("obsidian", "drafts/review.md"),
        ("obsidian", "notes/personal.md"),
        ("obsidian", "sources/inbox/source.md"),
        ("tolaria", "drafts/review.md"),
        ("tolaria", "notes/personal.md"),
    ),
)
def test_editor_write_policy_allows_only_declared_noncanonical_roots(
    frontend: str,
    path: str,
) -> None:
    assert validate_editor_write_target(frontend, path) == path  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("frontend", "path"),
    (
        ("obsidian", "/absolute.md"),
        ("obsidian", "../knowledge/escape.md"),
        ("obsidian", "knowledge/claim.md"),
        ("obsidian", "wiki/index.md"),
        ("obsidian", ".deeplaw/ledger.sqlite3"),
        ("tolaria", "sources/inbox/source.md"),
        ("tolaria", "knowledge/claim.md"),
        ("tolaria", "canvas/graph.canvas"),
        ("tolaria", "unmounted/file.md"),
    ),
)
def test_editor_write_policy_rejects_escape_and_deeplaw_owned_roots(
    frontend: str,
    path: str,
) -> None:
    with pytest.raises((PermissionError, ValueError)):
        validate_editor_write_target(frontend, path)  # type: ignore[arg-type]


def test_obsidian_mock_waits_for_layout_and_opencode_overlay_is_least_privilege() -> None:
    mock = (REPOSITORY / "adapters" / "obsidian" / "mock-bridge.ts").read_text(encoding="utf-8")
    assert mock.index("app.workspace.onLayoutReady") < mock.index('app.vault.on("create"')
    assert 'import { TFile, type App, type EventRef } from "obsidian";' in mock
    assert "knowledge/" not in mock
    assert ".deeplaw/" not in mock

    overlay = (REPOSITORY / "adapters" / "opencode" / "knowledge-compiler.example.jsonc").read_text(
        encoding="utf-8"
    )
    parsed = json.loads(overlay)
    assert parsed["permission"]["*"] == "deny"
    assert parsed["permission"]["deeplaw_knowledge_knowledge_support"] == "allow"
    assert parsed["permission"]["deeplaw_knowledge_sink_knowledge_sink"] == "allow"
    assert "shell" not in parsed["permission"]


def test_editor_context_is_ephemeral_and_does_not_mutate_the_ledger(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialized = initialize_knowledge_vault(root, name="editor-bridge", scope="project")
    initialize_autonomous_core(root)
    database = root / ".deeplaw" / "ledger.sqlite3"
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT COUNT(*) FROM autonomous_events_v3").fetchone()[0]

    result = context_for_editor(root, _envelope(initialized["vault_id"]))
    api_result = KnowledgeOS.open(root).editor_context.compile(_envelope(initialized["vault_id"]))
    envelope_path = tmp_path / "editor-context.json"
    envelope_path.write_text(json.dumps(_envelope(initialized["vault_id"])), encoding="utf-8")
    cli = subprocess.run(
        [
            sys.executable,
            "-m",
            "deeplaw",
            "knowledge",
            "editor",
            "context",
            "--vault",
            str(root),
            "--envelope",
            str(envelope_path),
        ],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    )
    cli_result = json.loads(cli.stdout)

    assert result["ephemeral_context"] is True
    assert result["persistence_requested"] is False
    assert result["persistence_performed"] is False
    api_plan = dict(api_result["retrieval"]["query_plan"])
    direct_plan = dict(result["retrieval"]["query_plan"])
    assert api_plan.pop("created_at").endswith("Z")
    assert direct_plan.pop("created_at").endswith("Z")
    assert api_plan == direct_plan
    assert cli_result["retrieval"]["query_plan"]["query_sha256"] == result[
        "retrieval"
    ]["query_plan"]["query_sha256"]
    assert cli_result["retrieval"]["compiled"] == result["retrieval"]["compiled"]
    with sqlite3.connect(database) as connection:
        after = connection.execute("SELECT COUNT(*) FROM autonomous_events_v3").fetchone()[0]
    assert after == before
    mismatched = _envelope("vault_" + "0" * 24)
    with pytest.raises(PermissionError, match="another Vault"):
        context_for_editor(root, mismatched)


def test_tolaria_mcp_merge_preserves_settings_and_fails_on_collision(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="tolaria-host", scope="project")
    initialize_autonomous_core(vault)
    existing = {
        "theme": "system",
        "mcpServers": {
            "tolaria": {"command": "node", "args": ["index.js"]},
            "unrelated": {"command": "example", "args": []},
        },
    }
    servers = tolaria_mcp_servers(
        deeplaw_executable="deeplaw",
        vault_path=vault,
        compiler_grant_id="grant_" + "a" * 24,
        include_law_support=True,
        owner_home=tmp_path / "owner-home",
    )
    merged = merge_standard_mcp_config(existing, servers)
    assert merged["theme"] == "system"
    assert merged["mcpServers"]["tolaria"] == existing["mcpServers"]["tolaria"]
    assert merged["mcpServers"]["unrelated"] == existing["mcpServers"]["unrelated"]
    assert set(servers) == {
        "deeplaw_knowledge",
        "deeplaw_knowledge_sink",
        "deeplaw_law",
    }
    rendered = json.dumps(servers, sort_keys=True)
    assert "--closed-environment" in rendered
    assert "--expected-vault-id" in rendered
    assert "--vault" not in rendered
    assert str(vault.resolve()) not in rendered
    assert existing["mcpServers"].keys() == {"tolaria", "unrelated"}
    with pytest.raises(FileExistsError, match="different settings"):
        merge_standard_mcp_config(
            {"mcpServers": {"deeplaw_knowledge": {"command": "other"}}},
            servers,
        )


def test_tolaria_context_mapping_and_open_note_intent_are_bounded() -> None:
    body = "# Active\nBounded Tolaria fixture"
    envelope = tolaria_context_envelope(
        {
            "activeNote": {"path": "notes/active.md", "body": body},
            "openTabs": [
                {"path": "notes/active.md"},
                {"path": "notes/related.md"},
            ],
            "referencedNotes": [{"path": "notes/evidence.md"}],
        },
        vault_identity="vault_" + "a" * 24,
        user_intent="Find the current governed answer.",
        frontend_version="v2026-06-23",
    )
    assert envelope["frontend"] == "tolaria"
    assert envelope["active_note"]["note_id"] == "notes/active.md"
    assert envelope["active_note"]["content_sha256"] == sha256(body.encode()).hexdigest()
    assert envelope["open_tabs"] == ["notes/related.md"]
    assert envelope["explicit_note_references"] == ["notes/evidence.md"]
    assert envelope["selected_text"] is None
    assert envelope["persistence_allowed"] is False

    request = tolaria_open_note_request("wiki/index.md", vault_path="/tmp/vault")
    assert request["tool"] == "open_note"
    assert request["mutation"] == "ui_only"
    with pytest.raises(PermissionError):
        tolaria_open_note_request(".deeplaw/ledger.sqlite3", vault_path="/tmp/vault")
    with pytest.raises(ValueError):
        tolaria_open_note_request("../wiki/index.md", vault_path="/tmp/vault")


def test_tolaria_temporary_vault_harness_is_source_free_and_ephemeral() -> None:
    harness = REPOSITORY / "adapters" / "tolaria" / "integration_harness.py"
    source = harness.read_text(encoding="utf-8")
    assert "TemporaryDirectory" in source
    assert "public_synthetic" in source
    assert "context_for_editor" in source
    assert "persistence_performed" in source


def test_tolaria_setup_cli_writes_new_private_config_without_echoing_contents(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "existing.json"
    output = tmp_path / "merged.json"
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="tolaria-setup", scope="project")
    initialize_autonomous_core(vault)
    environment = {**os.environ, "DEEPLAW_HOME": str(tmp_path / "owner-home")}
    existing.write_text(
        json.dumps(
            {
                "private_setting": "must-not-be-echoed",
                "mcpServers": {"tolaria": {"command": "node", "args": ["index.js"]}},
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY / "adapters" / "tolaria" / "setup.py"),
            "merge-mcp",
            "--existing",
            str(existing),
            "--output",
            str(output),
            "--vault",
            str(vault),
        ],
        cwd=REPOSITORY,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    receipt = json.loads(completed.stdout)
    assert receipt["existing_settings_preserved"] is True
    assert "must-not-be-echoed" not in completed.stdout
    if os.name == "nt":
        from deeplaw.windows_acl import native_windows_path_acl_report

        assert receipt["output_security"] == "windows_native_acl_owner_only"
        assert native_windows_path_acl_report(output)["permissions_verified"] is True
    else:
        assert receipt["output_security"] == "posix_mode_0600"
        assert output.stat().st_mode & 0o777 == 0o600
    merged = json.loads(output.read_text(encoding="utf-8"))
    assert merged["private_setting"] == "must-not-be-echoed"
    assert "tolaria" in merged["mcpServers"]
    assert "deeplaw_knowledge" in merged["mcpServers"]

    repeated = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY / "adapters" / "tolaria" / "setup.py"),
            "merge-mcp",
            "--existing",
            str(existing),
            "--output",
            str(output),
            "--vault",
            str(vault),
        ],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert repeated.returncode != 0
    assert "already exists" in repeated.stderr
