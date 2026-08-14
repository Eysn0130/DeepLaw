"""No-model public-path acceptance for Pass 19 task continuity."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[1]
_CHECKPOINT_TASK = "Finish the bounded Pass 19 implementation task."
_REQUEST_TASK = "Restore the admitted state for this exact task line."
_PROJECT = sha256_bytes(b"pass19-owner-registered-project")
_REPOSITORY = sha256_bytes(b"pass19-repository")
_EXPIRES_AT = "2099-01-01T00:00:00Z"


def _binding(
    line: str,
    *,
    worktree: str,
    base: str = "a" * 40,
    dirty: str = "b" * 64,
    parent: str | None = None,
) -> dict[str, Any]:
    return build_task_context_binding(
        _PROJECT,
        sha256_bytes(f"pass19-line:{line}".encode()),
        parent_task_lineage_sha256=(
            sha256_bytes(f"pass19-parent:{parent}".encode()) if parent else None
        ),
        repository_sha256=_REPOSITORY,
        worktree_sha256=sha256_bytes(f"pass19-worktree:{worktree}".encode()),
        base_revision=base,
        dirty_state_sha256=dirty,
    )


def _checkpoint_body(marker: str) -> str:
    return "\n".join(
        (
            "GOAL: Finish the bounded Pass 19 implementation task.",
            f"CONFIRMED_DECISION: Restore only route marker {marker}.",
            "CONSTRAINT: Keep task lines and worktrees isolated.",
            "VERIFIED_FACT: The checkpoint was written after a succeeded Run.",
            "OPEN_GAP: Real Host/model qualification remains not executed.",
            "NEXT_ACTION: Continue only the exact admitted task line.",
            f"ARTIFACT_REF: {marker}",
        )
    )


def _structured(result: Any) -> dict[str, Any]:
    assert result.isError is False, result
    value = result.structuredContent
    assert isinstance(value, dict), result
    return value


async def _sink_call(
    session: ClientSession,
    request: dict[str, Any],
) -> dict[str, Any]:
    return _structured(await session.call_tool("knowledge_sink", request))["result"]


async def _checkpoint(
    session: ClientSession,
    *,
    line: str,
    binding: dict[str, Any],
    marker: str,
) -> tuple[str, str]:
    run_id = f"run-pass19-{line}"
    await _sink_call(
        session,
        {
            "operation": "record_run",
            "idempotency_key": f"pass19-run-{line}",
            "confirm_no_case_data": True,
            "run_id": run_id,
            "task": _CHECKPOINT_TASK,
            "host_id": "pass19-fake-host",
            "model_id": "pass19-no-model-fixture",
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "run_metadata": {
                "task_kind": "implementation",
                "artifact_ids": [marker],
                "task_binding": binding,
            },
        },
    )
    remembered = await _sink_call(
        session,
        {
            "operation": "remember",
            "idempotency_key": f"pass19-checkpoint-{line}",
            "confirm_no_case_data": True,
            "title": f"Pass 19 checkpoint {line}",
            "body": _checkpoint_body(marker),
            "kind": "memory",
            "memory_type": "working",
            "semantic_key": f"checkpoint:pass19:{line}",
            "expires_at": _EXPIRES_AT,
            "scope": "project",
            "sensitivity": "private",
            "run_id": run_id,
            "model_id": "pass19-no-model-fixture",
            "tool_id": "pass19-public-sink-fixture",
            "tags": ["checkpoint", "pass19"],
        },
    )
    return str(remembered["knowledge_id"]), str(remembered["revision_id"])


async def _read_context(
    *,
    vault: Path,
    vault_id: str,
    binding: dict[str, Any] | None,
    task: str = _REQUEST_TASK,
    request_binding: dict[str, Any] | None = None,
    expect_error: bool = False,
) -> dict[str, Any] | None:
    args = [
        "-m",
        "deeplaw",
        "knowledge",
        "mcp",
        "--closed-environment",
        "--stdio",
        "--vault",
        str(vault),
        "--expected-vault-id",
        vault_id,
    ]
    if binding is not None:
        args.extend(("--task-binding", canonical_json(binding)))
    environment = os.environ.copy()
    environment.update(
        {
            "CODEX_HOME": str(vault.parent / "ambient-codex-auth"),
            "OPENAI_API_KEY": "pass19-read-canary",
            "DEEPSEEK_API_KEY": "pass19-provider-canary",
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=args,
        cwd=REPOSITORY,
        env=environment,
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        arguments: dict[str, Any] = {
            "operation": "context",
            "task": task,
            "purpose": "answer",
            "confirm_no_case_data": True,
        }
        if request_binding is not None:
            arguments["task_binding"] = request_binding
        result = await session.call_tool(
            "knowledge_support",
            arguments,
        )
        if expect_error:
            assert result.isError is True
            serialized_error = canonical_json(
                [getattr(item, "text", "") for item in result.content]
            )
            assert str(vault) not in serialized_error
            assert "pass19-read-canary" not in serialized_error
            assert "pass19-provider-canary" not in serialized_error
            return None
        provider = _structured(result)["result"]
        serialized = canonical_json(provider)
        assert str(vault) not in serialized
        assert "pass19-read-canary" not in serialized
        assert "pass19-provider-canary" not in serialized
        return provider


def _selected_ids(provider: dict[str, Any], known: set[str]) -> set[str]:
    return {
        str(item["knowledge_id"])
        for item in provider["capsule"].get("statements", [])
        if isinstance(item, dict) and item.get("knowledge_id") in known
    }


def _gap_codes(provider: dict[str, Any]) -> set[str]:
    return {
        str(item["code"])
        for item in provider["capsule"].get("gaps", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }


def test_public_closed_mcp_task_checkpoint_continuity_and_forget(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="pass19-continuity", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        vault_id = store.vault_id
        grant_id = store.enable_grant(
            writer_id="pass19-owner-fixture",
            operations=("record_run", "remember", "forget"),
        )["grant_id"]

    main = _binding("main", worktree="main")
    fork = _binding("main", worktree="main", parent="source-thread")
    concurrent = _binding("other", worktree="concurrent")
    stale = _binding(
        "main",
        worktree="main",
        base="c" * 40,
        dirty="d" * 64,
    )
    wrong_line = _binding("wrong", worktree="main")

    async def exercise() -> None:
        sink_parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "deeplaw",
                "knowledge",
                "sink",
                "mcp",
                "--closed-environment",
                "--stdio",
                "--vault",
                str(vault),
                "--expected-vault-id",
                vault_id,
                "--grant-id",
                grant_id,
            ],
            cwd=REPOSITORY,
            env=os.environ.copy(),
        )
        async with (
            stdio_client(sink_parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as sink,
        ):
            await sink.initialize()
            main_id, main_revision = await _checkpoint(
                sink,
                line="main",
                binding=main,
                marker="expected-main",
            )
            other_id, _other_revision = await _checkpoint(
                sink,
                line="other",
                binding=concurrent,
                marker="expected-concurrent",
            )

        known = {main_id, other_id}
        with AutonomousKnowledgeStore(vault, read_only=True) as store:
            read_audit_before = store.audit_head

        unbound = await _read_context(vault=vault, vault_id=vault_id, binding=None)
        assert unbound is not None
        assert _selected_ids(unbound, known) == set()
        assert "task_binding_required" in _gap_codes(unbound)

        for lifecycle in ("new", "resume", "compaction"):
            admitted = await _read_context(vault=vault, vault_id=vault_id, binding=main)
            assert admitted is not None
            assert _selected_ids(admitted, known) == {main_id}, lifecycle
        forked = await _read_context(vault=vault, vault_id=vault_id, binding=fork)
        assert forked is not None
        assert _selected_ids(forked, known) == {main_id}

        concurrent_result = await _read_context(
            vault=vault,
            vault_id=vault_id,
            binding=concurrent,
        )
        assert concurrent_result is not None
        assert _selected_ids(concurrent_result, known) == {other_id}

        stale_result = await _read_context(vault=vault, vault_id=vault_id, binding=stale)
        assert stale_result is not None
        assert _selected_ids(stale_result, known) == set()
        assert _gap_codes(stale_result).intersection(
            {"workspace_diverged", "stale_checkpoint"}
        )

        wrong_result = await _read_context(
            vault=vault,
            vault_id=vault_id,
            binding=wrong_line,
        )
        assert wrong_result is not None
        assert _selected_ids(wrong_result, known) == set()

        ambiguous = await _read_context(
            vault=vault,
            vault_id=vault_id,
            binding=None,
            task=_CHECKPOINT_TASK,
        )
        assert ambiguous is not None
        assert _selected_ids(ambiguous, known) == set()
        assert "task_line_ambiguous" in _gap_codes(ambiguous)

        with AutonomousKnowledgeStore(vault, read_only=True) as store:
            assert store.audit_head == read_audit_before

        assert (
            await _read_context(
                vault=vault,
                vault_id=vault_id,
                binding=main,
                request_binding=concurrent,
                expect_error=True,
            )
            is None
        )
        with AutonomousKnowledgeStore(vault, read_only=True) as store:
            assert store.audit_head == read_audit_before

        async with (
            stdio_client(sink_parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as sink,
        ):
            await sink.initialize()
            forgotten = await _sink_call(
                sink,
                {
                    "operation": "forget",
                    "idempotency_key": "pass19-forget-main",
                    "confirm_no_case_data": True,
                    "knowledge_id": main_id,
                    "expected_revision_id": main_revision,
                    "reason": "Owner fixture selectively withdrew the main checkpoint.",
                },
            )
            assert forgotten["lifecycle"] == "forgotten"

        after_forget = await _read_context(vault=vault, vault_id=vault_id, binding=main)
        assert after_forget is not None
        assert _selected_ids(after_forget, known) == set()
        other_after_forget = await _read_context(
            vault=vault,
            vault_id=vault_id,
            binding=concurrent,
        )
        assert other_after_forget is not None
        assert _selected_ids(other_after_forget, known) == {other_id}

    asyncio.run(exercise())
