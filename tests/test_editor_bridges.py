from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.editor_bridge import (
    bridge_contract,
    context_for_editor,
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
            (REPOSITORY / "adapters" / frontend / "manifest.json").read_text(
                encoding="utf-8"
            )
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
    mock = (REPOSITORY / "adapters" / "obsidian" / "mock-bridge.ts").read_text(
        encoding="utf-8"
    )
    assert mock.index("app.workspace.onLayoutReady") < mock.index(
        'app.vault.on("create"'
    )
    assert 'import { TFile, type App, type EventRef } from "obsidian";' in mock
    assert "knowledge/" not in mock
    assert ".deeplaw/" not in mock

    overlay = (
        REPOSITORY / "adapters" / "opencode" / "knowledge-compiler.example.jsonc"
    ).read_text(encoding="utf-8")
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
        before = connection.execute(
            "SELECT COUNT(*) FROM autonomous_events_v3"
        ).fetchone()[0]

    result = context_for_editor(root, _envelope(initialized["vault_id"]))

    assert result["ephemeral_context"] is True
    assert result["persistence_requested"] is False
    assert result["persistence_performed"] is False
    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT COUNT(*) FROM autonomous_events_v3"
        ).fetchone()[0]
    assert after == before
    mismatched = _envelope("vault_" + "0" * 24)
    with pytest.raises(PermissionError, match="another Vault"):
        context_for_editor(root, mismatched)
