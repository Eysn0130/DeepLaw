"""Ordinary source-free knowledge through the advertised public MCP seams."""

import json

import anyio
import pytest
from jsonschema import Draft202012Validator

from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import KnowledgeVault

from .test_knowledge_sink_mcp import _ready
from .test_mcp_progressive_read import client


def test_active_source_free_remember_is_visible_to_default_query(tmp_path):
    root, grant_id = _ready(tmp_path)
    written = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "ordinary-procedure",
            "confirm_no_case_data": True,
            "title": "Orchid archive procedure",
            "body": "Orchid archives use the amber retention label.",
            "kind": "procedure",
            "scope": "project",
            "sensitivity": "public",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    assert written["lifecycle"] == "active"
    response = handle_knowledge_support(
        operation="query",
        query="Orchid archive procedure",
        scope="project",
        max_sensitivity="public",
        vault_path=root,
    )
    items = response["result"]["capsule"].get("knowledge_revisions", [])
    assert [item["knowledge_id"] for item in items] == [written["knowledge_id"]]
    assert items[0]["source_free"] is True
    assert items[0]["epistemic_state"] == "tentative"
    assert items[0]["legal_authority"] is False

    async def exercise():
        from benchmarks.hosts.opencode_single_task_producer import ReadBudget

        async with client(root) as (session, _):
            output_schema = (await session.list_tools()).tools[0].outputSchema
            context = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "context",
                    "task": "Orchid archive procedure",
                    "scope": "project",
                    "max_sensitivity": "public",
                    "confirm_no_case_data": True,
                },
            )
            assert not context.isError
            Draft202012Validator(output_schema).validate(context.structuredContent)
            payload = json.loads(context.content[0].text)
            assert payload["knowledge_revisions"] == items
            assert payload["statements"] == []
            assert payload["selection_explanation"]["discovery_exhaustive"] is False
            guard = ReadBudget()
            guard.request({
                "operation": "context", "task": "Orchid archive procedure",
                "scope": "project", "max_sensitivity": "public",
                "confirm_no_case_data": True,
            })
            guard.response(context.model_dump(mode="json", by_alias=True, exclude_none=True))
            exact_target = {
                "kind": "knowledge", "knowledge_id": items[0]["knowledge_id"],
                "revision_id": items[0]["revision_id"],
            }
            assert guard.targets == [exact_target]
            guard.request({
                "operation": "read", "scope": "project", "max_sensitivity": "public",
                "target": exact_target,
            })
            read = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "read",
                    "scope": "project",
                    "max_sensitivity": "public",
                    "target": {
                        "kind": "knowledge",
                        "knowledge_id": items[0]["knowledge_id"],
                        "revision_id": items[0]["revision_id"],
                    },
                },
            )
            assert not read.isError
            Draft202012Validator(output_schema).validate(read.structuredContent)
            assert read.structuredContent["result"]["content"] == items[0]["content"]
            guard.response(
                read.model_dump(mode="json", by_alias=True, exclude_none=True),
                expected_target=exact_target,
            )

    anyio.run(exercise)

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        before = store.audit_head
        capsule = store.build_capsule(
            task="Orchid archive procedure",
            scope="project",
            max_sensitivity="public",
            confirm_no_case_data=True,
        )
        assert capsule["schema_version"] == "deeplaw.knowledge-capsule/v4"
        assert capsule["knowledge_revisions"] == items
        assert store.audit_head == before
    with KnowledgeVault(root, read_only=True) as vault:
        assert verify_capsule(capsule, vault=vault)["valid"]
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.forget(
            grant_id=grant_id,
            idempotency_key="forget-orchid",
            knowledge_id=written["knowledge_id"],
            expected_revision_id=written["revision_id"],
            reason="Synthetic owner withdrawal",
            confirm_no_case_data=True,
        )
        store.rebuild_derived()
        assert store.verify()["valid"]
    with KnowledgeVault(root, read_only=True) as vault:
        assert not verify_capsule(capsule, vault=vault)["valid"]
    after = handle_knowledge_support(
        operation="query",
        query="Orchid archive procedure",
        scope="project",
        max_sensitivity="public",
        vault_path=root,
    )
    assert after["result"]["capsule"]["knowledge_revisions"] == []


@pytest.mark.parametrize("control", ["quote", "verify", "legal", "scope", "sensitivity", "v6"])
def test_ordinary_revision_does_not_bypass_evidence_or_policy(tmp_path, control):
    root, grant_id = _ready(tmp_path)
    handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "semantic-memory",
            "confirm_no_case_data": True,
            "kind": "memory",
            "memory_type": "semantic",
            "title": "Orchid archive label",
            "body": "Orchid archives use amber labels.",
            "scope": "project",
            "sensitivity": "private" if control == "sensitivity" else "public",
        },
        grant_id=grant_id,
        vault_path=root,
    )
    options = {}
    if control in {"quote", "verify", "legal"}:
        options["purpose"] = control
    if control == "v6":
        options["query_plan_version"] = "6"
    result = handle_knowledge_support(
        operation="query",
        query="Orchid archive label",
        vault_path=root,
        scope="domain" if control == "scope" else "project",
        max_sensitivity="public",
        **options,
    )["result"]["capsule"]
    assert not result.get("knowledge_revisions")
    assert not result["statements"]
    assert not result.get("evidence")
    if control in {"scope", "sensitivity", "legal"}:
        assert "Orchid" not in json.dumps(result)


def test_semantic_memory_uses_ordinary_partition_with_bounded_body(tmp_path):
    root, grant_id = _ready(tmp_path)
    written = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "long-semantic-memory",
            "confirm_no_case_data": True,
            "kind": "memory",
            "memory_type": "semantic",
            "title": "Orchid archive label",
            "body": ("Orchid archives use amber labels. " * 100).strip(),
            "scope": "project",
            "sensitivity": "public",
        },
        grant_id=grant_id,
        vault_path=root,
    )["result"]
    result = handle_knowledge_support(
        operation="query",
        query="Orchid archive label",
        vault_path=root,
        scope="project",
        max_sensitivity="public",
    )
    item = result["result"]["capsule"]["knowledge_revisions"][0]
    assert item["knowledge_id"] == written["knowledge_id"]
    assert item["content_truncated"]
    assert len(item["content"]) == 2000
    bounded = handle_knowledge_support(
        operation="query",
        query="Orchid archive label",
        vault_path=root,
        scope="project",
        max_sensitivity="public",
        max_chars=200,
    )
    capsule = bounded["result"]["capsule"]
    assert capsule["knowledge_revisions"] == []
    assert capsule["selection_explanation"]["omitted_revisions"] == [
        {"revision_id": written["revision_id"], "reason": "character_budget"}
    ]

    token_bounded = handle_knowledge_support(
        operation="query",
        query="Orchid archive label",
        vault_path=root,
        scope="project",
        max_sensitivity="public",
        max_tokens=128,
    )["result"]["capsule"]
    assert token_bounded["knowledge_revisions"] == []
    assert token_bounded["selection_explanation"]["omitted_revisions"] == [
        {"revision_id": written["revision_id"], "reason": "token_budget"}
    ]
