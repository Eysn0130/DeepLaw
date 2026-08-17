from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel.server import RequestContext, request_ctx

from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_mcp_server import create_knowledge_mcp_server
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.persistent_read_runtime import PersistentReadRuntime
from deeplaw.util import canonical_json


def _synthetic_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-persistent-runtime", scope="project")
    initialize_autonomous_core(root)
    return root


async def _call(
    server: Any,
    runtime: Any,
    request_id: int,
    arguments: dict[str, Any],
) -> types.CallToolResult:
    handler = server.request_handlers[types.CallToolRequest]
    token = request_ctx.set(
        RequestContext(
            request_id=request_id,
            meta=None,
            session=None,
            lifespan_context=runtime,
        )
    )
    try:
        return await handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(
                    name="knowledge_support",
                    arguments=arguments,
                )
            )
        )
    finally:
        request_ctx.reset(token)


def test_runtime_reopens_after_autonomous_commit_and_reuses_afterward(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)

    async def exercise() -> tuple[Any, Any, Any, bool]:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            first = await _call(
                server,
                runtime,
                1,
                {"operation": "query", "query": "runtime"},
            )
            assert first.root.isError is False
            first_snapshot = runtime.persistent.snapshot
            with AutonomousKnowledgeStore(root, read_only=False) as store:
                store.enable_grant(writer_id="runtime-test")
            second = await _call(
                server,
                runtime,
                2,
                {"operation": "query", "query": "runtime"},
            )
            assert second.root.isError is False
            second_snapshot = runtime.persistent.snapshot
            third = await _call(
                server,
                runtime,
                3,
                {"operation": "query", "query": "runtime"},
            )
            assert third.root.isError is False
            return (
                first_snapshot,
                second_snapshot,
                runtime.persistent.snapshot,
                first_snapshot.closed,
            )

    first_snapshot, second_snapshot, third_snapshot, first_closed = asyncio.run(exercise())
    assert first_closed is True
    assert second_snapshot is third_snapshot
    assert first_snapshot is not second_snapshot


def test_runtime_manifest_tamper_fails_closed_before_query(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.rebuild_derived()

    async def exercise() -> bool:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            first = await _call(
                server,
                runtime,
                1,
                {"operation": "query", "query": "runtime"},
            )
            assert first.root.isError is False
            manifest_path = root / ".deeplaw" / "derived" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["manifest_sha256"] = "0" * 64
            manifest_path.write_text(
                json.dumps(manifest, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            second = await _call(
                server,
                runtime,
                2,
                {"operation": "query", "query": "runtime"},
            )
            return bool(second.root.isError)

    assert asyncio.run(exercise()) is True


def test_runtime_lifespan_close_is_deterministic(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)
    holder: dict[str, Any] = {}

    async def exercise() -> None:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            await _call(
                server,
                runtime,
                1,
                {"operation": "query", "query": "runtime"},
            )
            holder["runtime"] = runtime
            holder["snapshot"] = runtime.persistent.snapshot
        runtime.close()

    asyncio.run(exercise())
    assert holder["snapshot"].closed is True


def test_explicit_verify_bypasses_legacy_integrity_cache(tmp_path: Path, monkeypatch: Any) -> None:
    root = _synthetic_vault(tmp_path)
    audit_calls = 0
    state_calls = 0
    original_audit = KnowledgeVault.verify_audit_chain
    original_state = KnowledgeVault.verify_state_integrity

    def counted_audit(self: KnowledgeVault) -> dict[str, Any]:
        nonlocal audit_calls
        audit_calls += 1
        return original_audit(self)

    def counted_state(self: KnowledgeVault) -> dict[str, Any]:
        nonlocal state_calls
        state_calls += 1
        return original_state(self)

    monkeypatch.setattr(KnowledgeVault, "verify_audit_chain", counted_audit)
    monkeypatch.setattr(KnowledgeVault, "verify_state_integrity", counted_state)

    def exercise() -> None:
        runtime = PersistentReadRuntime(root)
        try:
            initial_audit = audit_calls
            initial_state = state_calls
            runtime.get_snapshot(operation="verify")
            assert audit_calls == initial_audit + 1
            assert state_calls == initial_state + 1
        finally:
            runtime.close()

    exercise()


def test_root_manifest_tamper_invalidates_first_read_and_clears_cache(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)

    async def exercise() -> tuple[bool, bool, int, int]:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            first = await _call(
                server,
                runtime,
                1,
                {"operation": "query", "query": "root manifest probe"},
            )
            assert first.root.isError is False
            old_snapshot = runtime.persistent.snapshot
            manifest_path = root / ".deeplaw" / "manifest.json"
            manifest_path.write_text("{}\n", encoding="utf-8")
            second = await _call(
                server,
                runtime,
                2,
                {"operation": "query", "query": "root manifest probe"},
            )
            return (
                bool(second.root.isError),
                old_snapshot.closed,
                len(runtime.read_result_cache),
                len(runtime.query_receipts),
            )

    failed, old_closed, cache_entries, receipt_count = asyncio.run(exercise())
    assert failed is True
    assert old_closed is True
    assert cache_entries == 0
    assert receipt_count == 0


def test_read_result_cache_is_exactly_partitioned_and_bounded(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)

    async def exercise() -> tuple[int, int, str, str]:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            for request_id, sensitivity in enumerate(("public", "private"), start=1):
                response = await _call(
                    server,
                    runtime,
                    request_id,
                    {
                        "operation": "query",
                        "query": "partition probe",
                        "scope": "project",
                        "max_sensitivity": sensitivity,
                    },
                )
                assert response.root.isError is False
            keys = list(runtime.read_result_cache)
            assert len(keys) == 2
            assert keys[0] != keys[1]
            for request_id in range(3, 24):
                response = await _call(
                    server,
                    runtime,
                    request_id,
                    {"operation": "query", "query": f"partition probe {request_id}"},
                )
                assert response.root.isError is False
            return (
                len(runtime.read_result_cache),
                runtime.read_result_cache_bytes,
                canonical_json([entry[1] for entry in runtime.read_result_cache.values()]),
                canonical_json(runtime.query_receipts),
            )

    entries, cache_bytes, cache_payload, receipt_payload = asyncio.run(exercise())
    assert entries <= 16
    assert cache_bytes <= 1 * 1024 * 1024
    assert "restricted" not in cache_payload.lower()
    assert "receipt_id" in receipt_payload
