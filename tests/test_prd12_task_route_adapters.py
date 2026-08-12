"""Local adapter parity checks for task-text-only continuity routing."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

from adapters.claude import deeplaw_hook
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

_TASK = "Continue the adapter parity task."


def _binding(line: str) -> dict[str, Any]:
    return build_task_context_binding(
        sha256_bytes(b"adapter-project"),
        sha256_bytes(f"adapter-line:{line}".encode()),
        repository_sha256=sha256_bytes(b"adapter-repository"),
        worktree_sha256=sha256_bytes(b"adapter-worktree"),
        base_revision="a" * 40,
        dirty_state_sha256=sha256_bytes(f"adapter-dirty:{line}".encode()),
    )


def _new_vault(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "adapter-route-vault"
    initialize_knowledge_vault(root, name="adapter-route", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="adapter-route-test",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
    return root, grant_id


def _seed_line(
    store: AutonomousKnowledgeStore,
    grant_id: str,
    *,
    line: str,
) -> tuple[dict[str, Any], str]:
    binding = _binding(line)
    run = store.record_run(
        grant_id=grant_id,
        idempotency_key=f"adapter-run-{line}",
        run_id=f"adapter-run-{line}",
        task=_TASK,
        host_id="pytest",
        status="succeeded",
        metadata={"task_binding": binding},
        confirm_no_case_data=True,
    )
    checkpoint = store.remember(
        grant_id=grant_id,
        idempotency_key=f"adapter-checkpoint-{line}",
        title="Adapter continuity checkpoint",
        body=(
            "GOAL: Continue the adapter parity task.\n"
            f"CONFIRMED_DECISION: Use the {line} task line.\n"
            "CONSTRAINT: Keep task-line state isolated.\n"
            "VERIFIED_FACT: This checkpoint is owner-admitted.\n"
            "OPEN_GAP: Host identity remains an untrusted hint.\n"
            "NEXT_ACTION: Verify the bounded adapter receipt."
            "\nARTIFACT_REF: adapter-route-artifact"
        ),
        kind="memory",
        memory_type="working",
        expires_at="2099-01-01T00:00:00Z",
        run_id=run["run_id"],
        semantic_key=f"checkpoint:adapter:{line}",
        tags=["checkpoint", "adapter-route"],
        confirm_no_case_data=True,
    )
    return binding, checkpoint["knowledge_id"]


def _run_claude_hook(root: Path) -> dict[str, Any]:
    output = io.BytesIO()
    payload = json.dumps(
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": _TASK,
            "active_files": [],
            "open_tabs": [],
        }
    ).encode()
    args = [
        "--event",
        "UserPromptSubmit",
        "--vault",
        str(root),
        "--workspace-identity",
        "adapter-workspace",
        "--repository-identity",
        "adapter-repository",
    ]
    assert (
        deeplaw_hook.main(
            args,
            stdin=io.BytesIO(payload),
            stdout=output,
        )
        == 0
    )
    return json.loads(output.getvalue())


def test_claude_thin_adapter_resolves_unique_task_text_and_redacts_binding(
    tmp_path: Path,
) -> None:
    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        binding, knowledge_id = _seed_line(store, grant_id, line="only")
    result = _run_claude_hook(root)
    assert result["disposition"] == "context_injected"
    additional = json.loads(result["hookSpecificOutput"]["additionalContext"])
    capsule = additional["knowledge_capsule"]
    assert capsule["query_plan_version"] == "6"
    assert any(item.get("knowledge_id") == knowledge_id for item in capsule["statements"])
    serialized = canonical_json(result)
    assert binding["binding_sha256"] not in serialized
    assert "/" + str(root).lstrip("/") not in serialized
    assert "adapter-workspace" not in serialized
    assert "adapter-repository" not in serialized
    assert "private-feature" not in serialized
    assert "DEEPLAW_TEST_AMBIENT_SECRET" not in serialized


def test_claude_thin_adapter_reports_ambiguity_without_newest_selection(tmp_path: Path) -> None:
    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        first_binding, first_id = _seed_line(store, grant_id, line="first")
        second_binding, second_id = _seed_line(store, grant_id, line="second")
    result = _run_claude_hook(root)
    assert result["disposition"] == "context_injected"
    additional = json.loads(result["hookSpecificOutput"]["additionalContext"])
    capsule = additional["knowledge_capsule"]
    assert "task_line_ambiguous" in capsule["gaps"]
    selected = {
        item.get("knowledge_id")
        for item in capsule["statements"]
        if isinstance(item, dict)
    }
    assert not selected.intersection({first_id, second_id})
    serialized = canonical_json(result)
    assert first_binding["binding_sha256"] not in serialized
    assert second_binding["binding_sha256"] not in serialized


def test_codex_and_opencode_static_entries_use_shared_read_only_mcp_surface() -> None:
    repository = Path(__file__).resolve().parents[1]
    codex_manifest = json.loads(
        (repository / "plugins/deeplaw-knowledge-os/.codex-plugin/plugin.json").read_text(
            encoding="utf-8"
        )
    )
    codex_mcp = json.loads(
        (repository / "plugins/deeplaw-knowledge-os/.mcp.json").read_text(
            encoding="utf-8"
        )
    )
    opencode_bridge = json.loads(
        (repository / "adapters/opencode/context-bridge.json").read_text(encoding="utf-8")
    )
    assert codex_manifest["mcpServers"] == "./.mcp.json"
    server = codex_mcp["mcpServers"]["deeplaw-knowledge"]
    assert server["command"] == "deeplaw"
    assert server["args"] == ["knowledge", "mcp", "--stdio"]
    api_by_name = {entry["name"]: entry for entry in opencode_bridge["domain_apis"]}
    assert api_by_name["knowledge_support"] == {
        "name": "knowledge_support",
        "mode": "read_only",
        "grant_required": False,
    }
    assert opencode_bridge["authority"] == "none"
    assert opencode_bridge["legal_authority"] is False
    assert opencode_bridge["persistence_performed"] is False
    # Static host entries contain no task-line identity implementation or
    # capability-bearing binding fields; the Python/MCP domain seam owns it.
    assert "task_binding" not in canonical_json(codex_mcp)
    assert "task_binding" not in canonical_json(opencode_bridge)
