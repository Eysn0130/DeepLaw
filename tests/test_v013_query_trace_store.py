from __future__ import annotations

import asyncio
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError
from mcp import types
from mcp.server.lowlevel.server import RequestContext, request_ctx

import deeplaw.knowledge_mcp_server as mcp_server
from deeplaw.knowledge_autonomy import initialize_autonomous_core
from deeplaw.knowledge_mcp_server import _KnowledgeRuntime, create_knowledge_mcp_server
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes


def _identity(audit_head: str = "a" * 64, legacy_head: str = "b" * 64) -> Any:
    return SimpleNamespace(
        autonomous_audit_head=audit_head,
        legacy_audit_head=legacy_head,
    )


def _audit(
    receipt_id: str,
    *,
    audit_head: str = "a" * 64,
    legacy_head: str = "b" * 64,
    large: bool = False,
) -> dict[str, Any]:
    statement_id = "statement_" + "1" * 24
    source_key = canonical_json(
        {
            "source_revision_id": "sourcerev_" + "2" * 24,
            "fragment_id": "fragment_" + "3" * 24,
            "locator": "/Users/private/source.md",
            "quote_sha256": "c" * 64,
        }
    )
    body: dict[str, Any] = {
        "schema_version": "deeplaw.query-audit-receipt/v1",
        "receipt_id": receipt_id,
        "query_plan_sha256": "d" * 64,
        "query_sha256": "e" * 64,
        "input_audit_head": audit_head,
        "input_legacy_audit_head": legacy_head,
        "candidate_count": 1,
        "admitted_statement_count": 1,
        "selected_statement_ids": [statement_id],
        "fallback": [
            {
                "duty": "primary_answer",
                "query_sha256": "f" * 64,
                "candidate_count": 1,
                "selected_source_count": 1,
                "source_keys": [source_key],
            }
        ],
        "deduplications": [{"source_key": source_key, "reason": "duplicate_source_reference"}],
        "suppressions": [{"candidate_id": source_key, "reason": "source_budget"}],
        "rejections": [{"statement_id": statement_id, "reason": "query_mismatch"}],
        "residual_gap_ids": ["querygap_" + "4" * 24],
        "ranking_authority_changed": False,
        "write_performed": False,
        "candidates": [{"statement_id": statement_id, "score": [0, 0, 0, statement_id]}],
    }
    if large:
        body["rejections"] = [
            {"statement_id": statement_id, "reason": f"query_mismatch_{index:04d}"}
            for index in range(512)
        ]
        body["suppressions"] = [
            {"candidate_id": source_key, "reason": f"source_budget_{index:04d}"}
            for index in range(512)
        ]
        body["candidates"] = [
            {"statement_id": statement_id, "score": [index, index, index, statement_id]}
            for index in range(512)
        ]
    body["receipt_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    return body


def _runtime() -> _KnowledgeRuntime:
    runtime = _KnowledgeRuntime(vault_path=Path("/tmp/deeplaw-query-trace-test"), lock=RLock())
    runtime.sync_read_identity(_identity())
    return runtime


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


def _synthetic_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="query-trace", scope="project")
    initialize_autonomous_core(root)
    return root


def test_query_trace_is_redacted_bounded_and_hash_verified() -> None:
    runtime = _runtime()
    receipt_id = "queryreceipt_" + "1" * 24
    runtime.retain_query_receipt(_audit(receipt_id))

    stored = next(iter(runtime.query_receipts.values()))
    serialized = canonical_json(stored)
    assert "/Users/private/source.md" not in serialized
    assert "source_key_sha256" in serialized
    assert "score" not in serialized
    assert runtime.query_receipts_bytes > 0
    assert runtime.read_query_receipt(receipt_id)["receipt_id"] == receipt_id

    stored["trace_sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="integrity"):
        runtime.read_query_receipt(receipt_id)
    assert receipt_id not in runtime.query_receipts
    assert runtime.query_receipts_bytes == 0


def test_query_trace_contract_rejects_unwhitelisted_nested_payload() -> None:
    runtime = _runtime()
    receipt_id = "queryreceipt_" + "1" * 24
    runtime.retain_query_receipt(_audit(receipt_id))
    result = {
        "schema_version": "deeplaw.query-audit-read/v1",
        "receipt_id": receipt_id,
        "audit": runtime.read_query_receipt(receipt_id),
        "write_performed": False,
    }
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "contracts" / "query-audit-read.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(result)

    result["audit"]["fallback"][0]["source_body"] = "must not cross the trace boundary"
    with pytest.raises(ValidationError, match="Additional properties"):
        Draft202012Validator(schema).validate(result)


def test_query_trace_lru_ttl_and_owner_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    clock = [100.0]
    monkeypatch.setattr(mcp_server.time, "monotonic", lambda: clock[0])
    for index in range(17):
        receipt_id = f"queryreceipt_{index:024x}"
        runtime.retain_query_receipt(_audit(receipt_id))
    assert len(runtime.query_receipts) == 16
    assert "queryreceipt_" + f"{0:024x}" not in runtime.query_receipts

    clock[0] += mcp_server._QUERY_TRACE_TTL_SECONDS + 1
    with pytest.raises(KeyError, match="unavailable"):
        runtime.read_query_receipt("queryreceipt_" + f"{16:024x}")
    assert len(runtime.query_receipts) == 0
    assert runtime.query_receipts_bytes == 0

    runtime.retain_query_receipt(_audit("queryreceipt_" + "2" * 24))
    runtime.clear_query_traces()
    assert runtime.query_receipts == {}
    assert runtime.query_receipts_bytes == 0
    runtime.retain_query_receipt(_audit("queryreceipt_" + "3" * 24))
    runtime.close()
    assert runtime.query_receipts == {}
    assert runtime.query_receipts_bytes == 0


def test_query_trace_byte_rotation_and_identity_invalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime()
    first = "queryreceipt_" + "1" * 24
    second = "queryreceipt_" + "2" * 24
    runtime.retain_query_receipt(_audit(first))
    payload_size = runtime.query_receipts_bytes
    monkeypatch.setattr(mcp_server, "_MAX_QUERY_TRACE_BYTES", payload_size + 1)
    runtime.retain_query_receipt(_audit(second))
    assert list(runtime.query_receipts) == [second]

    runtime.retain_query_receipt(_audit(first))
    runtime.sync_read_identity(_identity(audit_head="f" * 64))
    assert runtime.query_receipts == {}
    assert runtime.query_receipts_bytes == 0


def test_provider_v2_and_audit_read_contracts_are_closed(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    provider_schema = json.loads(
        (root / "contracts/provider-knowledge-capsule.v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    audit_schema = json.loads(
        (root / "contracts/query-audit-read.v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(provider_schema)
    Draft202012Validator.check_schema(audit_schema)
    provider_validator = Draft202012Validator(provider_schema)
    audit_validator = Draft202012Validator(audit_schema)

    vault = _synthetic_vault(tmp_path)

    async def exercise() -> None:
        server = create_knowledge_mcp_server(vault_path=vault)
        async with server.lifespan(server) as runtime:
            query = await _call(
                server,
                runtime,
                1,
                {"operation": "query", "query": "trace contract probe"},
            )
            assert query.root.isError is False
            provider = query.root.structuredContent["result"]
            provider_validator.validate(provider)
            assert set(provider["receipt"]) == {"receipt_id"}
            receipt_id = provider["receipt"]["receipt_id"]
            explained = await _call(
                server,
                runtime,
                2,
                {"operation": "explain", "receipt_id": receipt_id},
            )
            assert explained.root.isError is False
            audit = explained.root.structuredContent["result"]
            audit_validator.validate(audit)
            assert "trace contract probe" not in canonical_json(audit)
            assert "score" not in canonical_json(audit)

            audit_projection = await _call(
                server,
                runtime,
                3,
                {
                    "operation": "query",
                    "query": "provider audit projection probe",
                    "query_plan_version": "6",
                    "capsule_projection": "audit",
                },
            )
            assert audit_projection.root.isError is False
            projected = audit_projection.root.structuredContent["result"]
            provider_validator.validate(projected)
            assert projected["capsule"]["projection"] == "standard"
            assert "audit" not in projected["capsule"]
            assert "score" not in canonical_json(projected)

    asyncio.run(exercise())


def test_failed_provider_validation_does_not_retain_orphan_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = _synthetic_vault(tmp_path)

    def fail_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("forced provider validation failure")

    monkeypatch.setattr(mcp_server, "_autonomous_v6_response", fail_response)

    async def exercise() -> None:
        server = create_knowledge_mcp_server(vault_path=vault)
        async with server.lifespan(server) as runtime:
            response = await _call(
                server,
                runtime,
                1,
                {"operation": "query", "query": "orphan trace probe"},
            )
            assert response.root.isError is True
            assert runtime.query_receipts == {}
            assert runtime.query_receipts_bytes == 0

    asyncio.run(exercise())


def test_explain_over_limit_fails_closed_without_payload_leak(tmp_path: Path) -> None:
    vault = _synthetic_vault(tmp_path)

    async def exercise() -> None:
        server = create_knowledge_mcp_server(vault_path=vault)
        async with server.lifespan(server) as runtime:
            warm = await _call(
                server,
                runtime,
                1,
                {"operation": "query", "query": "warm trace"},
            )
            assert warm.root.isError is False
            identity = runtime.read_cache_identity
            assert identity is not None
            receipt_id = "queryreceipt_" + "9" * 24
            runtime.retain_query_receipt(
                _audit(
                    receipt_id,
                    audit_head=identity.autonomous_audit_head,
                    legacy_head=identity.legacy_audit_head,
                    large=True,
                )
            )
            explained = await _call(
                server,
                runtime,
                2,
                {"operation": "explain", "receipt_id": receipt_id},
            )
            assert explained.root.isError is True
            assert "query_mismatch_0001" not in " ".join(
                getattr(item, "text", "") for item in explained.root.content
            )

    asyncio.run(exercise())
