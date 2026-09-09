"""Bounded selection accounting and lifespan-bound continuation, never denied inventory."""
from copy import deepcopy
from threading import RLock

import anyio
import pytest

from deeplaw.knowledge_mcp_server import _KnowledgeRuntime
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.util import canonical_json, sha256_bytes

from .test_knowledge_sink_mcp import _ready
from .test_mcp_progressive_read import client


def test_selection_pages_are_bounded_authenticated_and_expire_with_receipt(tmp_path):
    entries = [
        {"candidate_id": f"statement_{number:024x}", "kind": "statement",
         "state": "omitted", "reason": "selection_budget"}
        for number in range(45)
    ]
    receipt = {
        "schema_version": "deeplaw.query-audit-receipt/v2",
        "receipt_id": "queryreceipt_" + "a" * 24,
        **{key: "b" * 64 for key in (
            "query_plan_sha256", "query_sha256", "input_audit_head", "input_legacy_audit_head")},
        "ranking_authority_changed": False, "write_performed": False,
        "selection_entries": entries,
        "selection_sha256": sha256_bytes(canonical_json(entries).encode()),
        "discovery_exhaustive": False, "selection_truncated": False,
        "scope": "bounded_admitted_candidates_only",
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json(receipt).encode())
    runtime = _KnowledgeRuntime(tmp_path, RLock())
    runtime.retain_query_receipt(receipt)
    first = runtime.read_selection_page(receipt["receipt_id"])
    assert first["selection_entries"] == entries[:20]
    reference = first["next_receipt_id"]
    second = runtime.read_selection_page(reference)
    assert second["selection_entries"] == entries[20:40]
    last = runtime.read_selection_page(second["next_receipt_id"])
    assert last["selection_entries"] == entries[40:]
    assert last["next_receipt_id"] is None
    assert runtime.read_selection_page(reference) == second
    with pytest.raises(ValueError, match="integrity"):
        runtime.read_selection_page(reference.replace(".20.", ".40."))
    other = _KnowledgeRuntime(tmp_path, RLock())
    other.retain_query_receipt(receipt)
    with pytest.raises(ValueError, match="integrity"):
        other.read_selection_page(reference)
    runtime.clear_query_traces()
    with pytest.raises(KeyError, match="unavailable"):
        runtime.read_selection_page(reference)
    tampered = deepcopy(receipt)
    tampered["selection_entries"][0]["candidate_id"] = "private_identifier"
    with pytest.raises(RuntimeError, match="integrity"):
        runtime.retain_query_receipt(tampered)


def test_public_query_explain_accounts_only_for_admitted_memory(tmp_path):
    root, grant = _ready(tmp_path)
    items = []
    for sensitivity in ("public", "private"):
        items.append(handle_knowledge_sink({
            "operation": "remember", "idempotency_key": f"selection-{sensitivity}",
            "confirm_no_case_data": True, "kind": "procedure",
            "title": "Orchid selection procedure",
            "body": ("Orchid procedure uses amber labels. " * 60).strip(),
            "scope": "project", "sensitivity": sensitivity,
        }, grant_id=grant, vault_path=root)["result"])

    async def exercise():
        async with client(root) as (session, _):
            query = await session.call_tool("knowledge_support", {
                "operation": "query", "query": "Orchid selection procedure",
                "scope": "project", "max_sensitivity": "public", "max_tokens": 256,
            })
            assert not query.isError
            provider = query.structuredContent["result"]
            body = provider["capsule"]
            explanation = body["selection_explanation"]
            assert explanation["selection_entries"] == [{
                "candidate_id": items[0]["revision_id"], "kind": "knowledge_revision",
                "state": "omitted", "reason": "token_budget",
            }]
            explained = await session.call_tool("knowledge_support", {
                "operation": "explain", "receipt_id": body["receipt_id"],
            })
            assert not explained.isError
            page = explained.structuredContent["result"]
            assert page["selection_entries"] == explanation["selection_entries"]
            assert page["selection_sha256"] == explanation["selection_sha256"]
            assert page["discovery_exhaustive"] is False
            assert page["next_receipt_id"] is None
            serialized = canonical_json(page)
            assert items[1]["revision_id"] not in serialized
            assert items[1]["knowledge_id"] not in serialized
            assert "candidate_count" not in serialized
            assert "rejections" not in serialized
    anyio.run(exercise)
