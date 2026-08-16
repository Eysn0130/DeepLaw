from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.editor_bridge import (
    host_bridge_contract,
    host_context_envelope,
    validate_host_context,
)

REPOSITORY = Path(__file__).resolve().parents[1]


def _schema() -> dict[str, object]:
    return json.loads(
        (REPOSITORY / "contracts" / "host-context-bridge.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )


def _base_snapshot() -> dict[str, object]:
    return {
        "task": "Find the governed admission boundary.",
        "goal": "Return bounded context.",
        "workspaceIdentity": "workspace-v013",
        "repositoryIdentity": "repo-v013",
        "commit": "abc123",
        "branch": "main",
        "purpose": "answer",
        "scope": "project",
        "maxSensitivity": "private",
        "activeFiles": ["notes/active.md"],
        "openTabs": ["notes/active.md", "notes/related.md"],
        "currentNote": "notes/active.md",
    }


def test_all_editor_hosts_share_the_same_agent_context_hash() -> None:
    opencode = host_context_envelope("opencode", _base_snapshot())
    obsidian = host_context_envelope("obsidian", _base_snapshot())
    tolaria_snapshot = {
        "task": "Find the governed admission boundary.",
        "goal": "Return bounded context.",
        "workspaceIdentity": "workspace-v013",
        "repositoryIdentity": "repo-v013",
        "commit": "abc123",
        "branch": "main",
        "purpose": "answer",
        "scope": "project",
        "maxSensitivity": "private",
        "activeNote": {"path": "notes/active.md", "body": "untrusted note body"},
        "openTabs": [{"path": "notes/active.md"}, {"path": "notes/related.md"}],
        "referencedNotes": [],
    }
    tolaria = host_context_envelope("tolaria", tolaria_snapshot)

    assert obsidian == opencode == tolaria
    assert "untrusted note body" not in json.dumps(tolaria, ensure_ascii=False)
    assert validate_host_context(opencode)["envelope_sha256"] == opencode["envelope_sha256"]


def test_chat_summary_is_rejected_and_absolute_paths_are_not_admitted() -> None:
    for key in ("chat_summary", "summary", "transcript", "messages"):
        summary = _base_snapshot()
        summary[key] = "model-generated conclusion"
        with pytest.raises(ValueError, match="summaries"):
            host_context_envelope("opencode", summary)

    absolute = _base_snapshot()
    absolute["activeFiles"] = ["/private/user/project/file.py"]
    with pytest.raises(ValueError, match=r"relative|absolute"):
        host_context_envelope("opencode", absolute)


def test_host_bridge_contracts_match_closed_artifacts() -> None:
    validator = Draft202012Validator(_schema(), format_checker=FormatChecker())
    for host in ("obsidian", "opencode", "tolaria"):
        contract = host_bridge_contract(host)  # type: ignore[arg-type]
        artifact = json.loads(
            (REPOSITORY / "adapters" / host / "context-bridge.json").read_text(
                encoding="utf-8"
            )
        )
        validator.validate(contract)
        assert artifact == contract

    tolaria = host_bridge_contract("tolaria")
    assert tolaria["integration_status"] == "integration_limited"
    assert tolaria["exact_upstream"] == {
        "name": "Tolaria",
        "version": "v2026-08-11",
        "commit": "cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d",
        "plugin_api_status": "not_available",
        "stable_active_note_preview_promote": False,
    }


def test_tolaria_harness_reports_limited_steps_without_private_paths() -> None:
    completed = subprocess.run(
        [sys.executable, "adapters/tolaria/integration_harness.py"],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = json.loads(completed.stdout)
    serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
    assert len(serialized.encode("utf-8")) <= 65_536
    assert result["integration_status"] == "integration_limited"
    assert result["exact_upstream"]["version"] == "v2026-08-11"
    assert result["exact_upstream"]["commit"] == (
        "cb45f26649a7500e0bdb5dd0b8f0412e9c1daf4d"
    )
    assert result["agent_context"]["schema_version"] == "deeplaw.agent-context-envelope/v1"
    assert result["agent_context"]["authority"] == "none"
    assert result["agent_context"]["legal_authority"] is False
    assert result["authority"] == "none"
    assert result["legal_authority"] is False
    assert result["persistence_allowed"] is False
    assert result["persistence_performed"] is False
    assert result["steps"]["read_only_query_v6"]["status"] == "executed"
    for step in ("wiki_resolver_page_intent", "draft", "explicit_promotion", "refreshed_revision"):
        assert result["steps"][step]["status"] == "not_executed"
    assert "/tmp/" not in serialized
    assert "chat_summary" not in serialized
