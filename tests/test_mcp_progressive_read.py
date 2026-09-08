"""Public MCP-only progressive reading; synthetic development evidence only."""

from __future__ import annotations

import json
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .test_v013_query_v6_unseen_development import _build_case


@pytest.fixture(scope="module")
def case(tmp_path_factory):
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
    from deeplaw.projection import rebuild_living_wiki

    result = _build_case(tmp_path_factory.mktemp("progressive-read"))
    with AutonomousKnowledgeStore(result["root"], read_only=False) as store:
        rebuild_living_wiki(store)
    return result


@asynccontextmanager
async def client(root):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "deeplaw", "knowledge", "mcp", "--stdio", "--vault", str(root)],
        cwd=Path(__file__).resolve().parents[1],
    )
    async with (
        stdio_client(parameters) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        initialized = await session.initialize()
        yield session, initialized


def test_initialize_recommendations_are_advertised(case):
    async def exercise():
        async with client(case["root"]) as (session, initialized):
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["knowledge_support"]
            schema = listed.tools[0].inputSchema
            assert schema.get("type") == "object"
            operations = {
                item["properties"]["operation"]["const"]
                for item in schema["$defs"].values()
                if isinstance(item, dict) and "operation" in item.get("properties", {})
            }
            recommended = set(re.findall(r"\b(\w+)=", initialized.instructions or ""))
            assert recommended - {"law_support"} <= operations

    anyio.run(exercise)


def test_mcp_only_progressive_read(case):
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    with AutonomousKnowledgeStore(case["root"], read_only=True) as store:
        before = (store.audit_head, store.legacy_audit_head)

    async def exercise():
        async with client(case["root"]) as (session, _):
            result = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "query",
                    "query": "Policy Alpha archive",
                    "scope": "project",
                    "max_sensitivity": "public",
                },
            )
            assert not result.isError
            capsule = json.loads(result.content[0].text)
            from deeplaw.util import canonical_json

            assert (
                result.structuredContent["schema_version"] == "deeplaw.knowledge-support-output/v6"
            )
            assert result.content[0].text == canonical_json(
                result.structuredContent["result"]["capsule"]
            )
            # All read selectors below must come from this public response.

            schema = (await session.list_tools()).tools[0].inputSchema
            assert "read" in schema["$defs"], "public MCP has no exact read route"
            from jsonschema import Draft202012Validator

            output = (await session.list_tools()).tools[0].outputSchema
            Draft202012Validator(output).validate(result.structuredContent)
            statement = capsule["statements"][0]
            target = {
                "kind": "knowledge",
                "knowledge_id": statement["knowledge_id"],
                "revision_id": statement["knowledge_revision_id"],
            }

            async def read(target, **extra):
                request = {
                    "operation": "read",
                    "target": target,
                    "scope": "project",
                    "max_sensitivity": "public",
                    **extra,
                }
                Draft202012Validator(schema).validate(request)
                response = await session.call_tool("knowledge_support", request)
                assert not response.isError, response
                Draft202012Validator(output).validate(response.structuredContent)
                assert json.loads(response.content[0].text) == response.structuredContent
                return response.structuredContent["result"]

            knowledge = await read(target)
            assert statement["statement_text"] in knowledge["content"]
            assert knowledge["source_refs"]
            wiki = await read({**target, "kind": "wiki"})
            assert statement["statement_text"] in wiki["content"]
            reference = knowledge["source_refs"][0]
            fragment = await read(
                {
                    "kind": "source_fragment",
                    "source_revision_id": reference["source_revision_id"],
                    "fragment_id": reference["fragment_id"],
                }
            )
            assert statement["statement_text"] in fragment["content"]
            assert fragment["locator"] == reference["locator"]
            assert fragment["budget"]["read_calls"] == 3
            wrong_source = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "read",
                    "scope": "project",
                    "max_sensitivity": "public",
                    "target": {
                        "kind": "source_fragment",
                        "source_revision_id": "sourcerev_" + "0" * 24,
                        "fragment_id": reference["fragment_id"],
                    },
                },
            )
            assert wrong_source.isError
            explain = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "explain",
                    "receipt_id": capsule["receipt_id"],
                },
            )
            assert not explain.isError
            Draft202012Validator(output).validate(explain.structuredContent)

    anyio.run(exercise)
    with AutonomousKnowledgeStore(case["root"], read_only=True) as store:
        assert (store.audit_head, store.legacy_audit_head) == before


def test_pagination_policy_and_connection_budget(case):
    async def exercise():
        async with client(case["root"]) as (session, _):
            query = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "context",
                    "task": "Policy Alpha archive",
                    "confirm_no_case_data": True,
                    "max_sensitivity": "public",
                    "scope": "project",
                },
            )
            assert not query.isError
            capsule = json.loads(query.content[0].text)
            item = capsule["statements"][0]
            target = {
                "kind": "wiki",
                "knowledge_id": item["knowledge_id"],
                "revision_id": item["knowledge_revision_id"],
            }
            request = {
                "operation": "read",
                "target": target,
                "scope": "project",
                "max_sensitivity": "public",
                "max_chars": 200,
            }
            first = await session.call_tool("knowledge_support", request)
            assert not first.isError
            page = first.structuredContent["result"]
            assert page["next_offset"] == 200
            total_bytes = len(first.content[0].text.encode())
            second = await session.call_tool(
                "knowledge_support",
                {
                    **request,
                    "offset": 200,
                    "content_sha256": page["content_sha256"],
                },
            )
            assert not second.isError
            total_bytes += len(second.content[0].text.encode())
            assert second.structuredContent["result"]["offset"] == 200
            assert second.structuredContent["result"]["budget"]["read_content_bytes"] == total_bytes
            for change in (
                {"scope": "domain"},
                {"offset": 200},
                {"offset": 200, "content_sha256": "0" * 64},
                {"target": {**target, "revision_id": "knowledgerev_" + "0" * 24}},
                {"target": {**target, "knowledge_id": "knowledge_" + "0" * 24}},
                {"max_chars": 12001},
                {"undeclared": True},
            ):
                denied = await session.call_tool("knowledge_support", {**request, **change})
                assert denied.isError
            for count in range(3, 33):
                response = await session.call_tool("knowledge_support", request)
                assert not response.isError
                total_bytes += len(response.content[0].text.encode())
                assert response.structuredContent["result"]["budget"]["read_calls"] == count
                assert (
                    response.structuredContent["result"]["budget"]["read_content_bytes"]
                    == total_bytes
                )
            assert (await session.call_tool("knowledge_support", request)).isError
        # New MCP lifespan resets the explicit ephemeral budget.
        async with client(case["root"]) as (session, _):
            response = await session.call_tool("knowledge_support", request)
            assert not response.isError
            assert response.structuredContent["result"]["budget"]["read_calls"] == 1

    anyio.run(exercise)


@pytest.mark.parametrize("change", ["remove", "sensitivity", "successor", "pending"])
def test_source_policy_rechecked_between_pages(case, tmp_path, change):
    import shutil

    from deeplaw.knowledge_store import KnowledgeVault

    root = tmp_path / "vault"
    replacement = tmp_path / "replacement"
    shutil.copytree(case["root"], root)
    shutil.copytree(case["root"], replacement)

    async def exercise():
        async with client(root) as (session, _):
            response = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "query",
                    "query": "Policy Alpha archive",
                    "scope": "project",
                    "max_sensitivity": "public",
                },
            )
            item = json.loads(response.content[0].text)["statements"][0]
            reference = item["source_refs"][0]
            targets = [
                {
                    "kind": kind,
                    "knowledge_id": item["knowledge_id"],
                    "revision_id": item["knowledge_revision_id"],
                }
                for kind in ("knowledge", "wiki")
            ] + [
                {
                    "kind": "source_fragment",
                    "source_revision_id": reference["source_revision_id"],
                    "fragment_id": reference["fragment_id"],
                }
            ]
            requests = []
            for target in targets:
                request = {
                    "operation": "read",
                    "target": target,
                    "scope": "project",
                    "max_sensitivity": "public",
                    "max_chars": 200,
                }
                initial = await session.call_tool("knowledge_support", request)
                assert not initial.isError
                requests.append(
                    {
                        **request,
                        "offset": 1,
                        "content_sha256": initial.structuredContent["result"]["content_sha256"],
                    }
                )
            # Explicit owner mutation of this isolated fixture, outside the Agent journey.
            with KnowledgeVault(replacement, read_only=False) as vault:
                source_id = case["source"]["source_id"]
                if change == "remove":
                    vault.remove_source(source_id, reason="development withdrawal", confirm=True)
                elif change == "pending":
                    from deeplaw.knowledge_identity import record_governance_revision

                    info = vault.source_info(source_id)
                    prior = info["governance"]
                    record_governance_revision(
                        vault.connection,
                        subject_kind="source_revision",
                        subject_id=info["source_revision_id"],
                        trust=prior["trust"],
                        sensitivity=prior["sensitivity"],
                        policy_id="deeplaw.local-source-governance/v2",
                        review_status=prior["review_status"],
                        lifecycle_status="pending",
                        activation_status="active",
                        reviewer_id="owner-test",
                        recorded_at="2099-01-01T00:00:00Z",
                    )
                    vault._append_identity_snapshot(
                        reason="governance_recorded",
                        source_revision_id=info["source_revision_id"],
                    )
                    vault.connection.commit()
                    current = vault.source_info(source_id)["governance"]
                    assert current["lifecycle_status"] == "pending"
                elif change == "successor":
                    from deeplaw.knowledge_compiler import compile_source

                    old = vault.source_info(source_id)
                    path = tmp_path / "successor.md"
                    path.write_text("# Revised policy\nPolicy Alpha now requires 45 days.\n")
                    new = compile_source(
                        vault,
                        path,
                        source_kind="document",
                        sensitivity="public",
                        source_key=old["source_key"],
                        logical_path=old["logical_path"],
                        confirm_no_case_data=True,
                    )["source"]
                    review = vault.source_review_manifest(new["source_id"])
                    vault.approve_source_assets(
                        new["source_id"],
                        confirm_reviewed=True,
                        review_manifest_sha256=review["review_manifest_sha256"],
                        reviewer_id="owner-test",
                        review_reason="development successor",
                    )
                else:
                    vault.update_source_governance(
                        source_id,
                        trust=vault.source_info(source_id)["trust"],
                        sensitivity="private",
                        reviewer_id="owner-test",
                        reason="development restriction",
                        confirm_reviewed=True,
                        export_allowed=False,
                    )
            root.rename(tmp_path / "previous")
            replacement.rename(root)
            for request in requests:
                denied = await session.call_tool("knowledge_support", request)
                assert denied.isError, denied
                assert item["statement_text"] not in str(denied)

    anyio.run(exercise)


def test_v7_stays_closed():
    from jsonschema import Draft202012Validator

    schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "contracts/knowledge-support.input.v7.schema.json"
        ).read_text()
    )
    validator = Draft202012Validator(schema)
    assert not validator.is_valid({"operation": "read", "target": {}})
    assert not validator.is_valid({"operation": "query", "query": "x", "target": {}})


def test_exact_revision_forget_and_byte_budget(tmp_path):
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    from .test_autonomous_knowledge import _grant, _vault

    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = _grant(store)
        item = store.remember(
            grant_id=grant,
            idempotency_key="long-read",
            title="Public long concept",
            body="知识正文。" * 3000,
            kind="concept",
            operation="upsert_concept",
            sensitivity="public",
            confirm_no_case_data=True,
        )

    async def exercise():
        async with client(root) as (session, _):
            request = {
                "operation": "read",
                "target": {
                    "kind": "knowledge",
                    "knowledge_id": item["knowledge_id"],
                    "revision_id": item["revision_id"],
                },
                "scope": "project",
                "max_sensitivity": "public",
                "max_chars": 12000,
            }
            total = 0
            for _ in range(32):
                result = await session.call_tool("knowledge_support", request)
                if result.isError:
                    assert "byte budget exhausted" in result.content[0].text
                    break
                size = len(result.content[0].text.encode())
                assert size <= 65536
                # MCP has two copies plus a bounded JSON envelope, not a 64KiB wire cap.
                assert len(result.model_dump_json(by_alias=True).encode()) <= 150000
                total += size
                assert result.structuredContent["result"]["budget"]["read_content_bytes"] == total
                assert total <= 262144
            else:
                pytest.fail("byte budget did not stop long reads")
        async with client(root) as (session, _):
            initial = await session.call_tool("knowledge_support", {**request, "max_chars": 200})
            assert not initial.isError
            initial_page = initial.structuredContent["result"]
            with AutonomousKnowledgeStore(root, read_only=False) as store:
                updated = store.remember(
                    grant_id=grant,
                    idempotency_key="revise-read",
                    title="Public long concept",
                    body="Revised private content.",
                    kind="concept",
                    operation="upsert_concept",
                    knowledge_id=item["knowledge_id"],
                    expected_revision_id=item["revision_id"],
                    sensitivity="private",
                    confirm_no_case_data=True,
                )
            denied = await session.call_tool(
                "knowledge_support",
                {
                    **request,
                    "offset": 200,
                    "content_sha256": initial_page["content_sha256"],
                },
            )
            assert denied.isError
            current_request = {
                **request,
                "target": {**request["target"], "revision_id": updated["revision_id"]},
            }
            assert (await session.call_tool("knowledge_support", current_request)).isError
            current_request["max_sensitivity"] = "private"
            assert not (await session.call_tool("knowledge_support", current_request)).isError
            with AutonomousKnowledgeStore(root, read_only=False) as store:
                store.forget(
                    grant_id=grant,
                    idempotency_key="forget-read",
                    knowledge_id=item["knowledge_id"],
                    expected_revision_id=updated["revision_id"],
                    reason="Owner development test",
                    confirm_no_case_data=True,
                )
            assert (await session.call_tool("knowledge_support", current_request)).isError

    anyio.run(exercise)


def test_output_nested_fields_are_closed(case):
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    async def exercise():
        async with client(case["root"]) as (session, _):
            query = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "query",
                    "query": "Policy Alpha",
                    "scope": "project",
                    "max_sensitivity": "public",
                },
            )
            item = json.loads(query.content[0].text)["statements"][0]
            read = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "read",
                    "scope": "project",
                    "max_sensitivity": "public",
                    "target": {
                        "kind": "knowledge",
                        "knowledge_id": item["knowledge_id"],
                        "revision_id": item["knowledge_revision_id"],
                    },
                },
            )
            assert not read.isError
            validator = Draft202012Validator((await session.list_tools()).tools[0].outputSchema)
            for field in ("governance", "source_refs"):
                altered = deepcopy(read.structuredContent)
                value = altered["result"][field]
                if isinstance(value, list):
                    value = value[0]
                value["unadvertised_private_metadata"] = "not allowed"
                assert not validator.is_valid(altered)

    anyio.run(exercise)


@pytest.mark.parametrize("boundary", ["split_path", "expired", "memory"])
def test_exact_read_rejects_unsafe_or_task_only_knowledge(tmp_path, boundary):
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    from .test_autonomous_knowledge import _grant, _vault

    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = _grant(store)
        extra = {}
        if boundary == "expired":
            extra["valid_to"] = "2000-01-01T00:00:00Z"
        if boundary == "memory":
            extra["memory_type"] = "semantic"
        body = (
            ("x" * 194 + " /Users/synthetic/private.txt")
            if boundary == "split_path"
            else "Test text"
        )
        item = store.remember(
            grant_id=grant,
            idempotency_key="read-boundary",
            title="Read boundary",
            body=body,
            kind="memory" if boundary == "memory" else "concept",
            operation="remember" if boundary == "memory" else "upsert_concept",
            sensitivity="public",
            confirm_no_case_data=True,
            **extra,
        )

    async def exercise():
        async with client(root) as (session, _):
            for kind in ("knowledge", "wiki"):
                result = await session.call_tool(
                    "knowledge_support",
                    {
                        "operation": "read",
                        "target": {
                            "kind": kind,
                            "knowledge_id": item["knowledge_id"],
                            "revision_id": item["revision_id"],
                        },
                        "scope": "project",
                        "max_sensitivity": "public",
                        "max_chars": 200,
                    },
                )
                assert result.isError
                assert "/Users/synthetic" not in str(result)
            result = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "read",
                    "target": {"kind": "knowledge", "knowledge_id": body},
                    "scope": "project",
                    "max_sensitivity": "public",
                },
            )
            assert result.isError
            assert body not in str(result)

    anyio.run(exercise)


@pytest.mark.parametrize("change", ["forget", "sensitivity"])
def test_registered_knowledge_lineage_is_readable_and_rechecked(tmp_path, change):
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    from .test_autonomous_knowledge import _grant, _vault

    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = _grant(store)
        original = store.remember(
            grant_id=grant,
            idempotency_key="lineage-original",
            title="Original procedure",
            body="One governed step.",
            kind="procedure",
            sensitivity="public",
            confirm_no_case_data=True,
        )
        derived = store.remember(
            grant_id=grant,
            idempotency_key="lineage-derived",
            title="Derived procedure",
            body="Follow the original governed step.",
            kind="procedure",
            sensitivity="public",
            source_refs=[{"revision_id": original["revision_id"]}],
            confirm_no_case_data=True,
        )

    async def exercise():
        async with client(root) as (session, _):
            request = {
                "operation": "read",
                "scope": "project",
                "max_sensitivity": "public",
                "target": {
                    "kind": "knowledge",
                    "knowledge_id": derived["knowledge_id"],
                    "revision_id": derived["revision_id"],
                },
            }
            response = await session.call_tool("knowledge_support", request)
            assert not response.isError, response
            ref = response.structuredContent["result"]["source_refs"][0]
            assert ref["revision_id"] == original["revision_id"]
            assert ref["knowledge_id"] == original["knowledge_id"]
            predecessor = await session.call_tool(
                "knowledge_support",
                {
                    **request,
                    "target": {
                        "kind": "knowledge",
                        "knowledge_id": ref["knowledge_id"],
                        "revision_id": ref["revision_id"],
                    },
                },
            )
            assert not predecessor.isError
            with AutonomousKnowledgeStore(root, read_only=False) as store:
                if change == "forget":
                    store.forget(
                        grant_id=grant,
                        idempotency_key="forget-lineage",
                        knowledge_id=original["knowledge_id"],
                        expected_revision_id=original["revision_id"],
                        reason="Owner test",
                        confirm_no_case_data=True,
                    )
                else:
                    store.remember(
                        grant_id=grant,
                        idempotency_key="private-lineage",
                        knowledge_id=original["knowledge_id"],
                        expected_revision_id=original["revision_id"],
                        title="Original procedure",
                        body="Private governed step.",
                        kind="procedure",
                        sensitivity="private",
                        confirm_no_case_data=True,
                    )
            assert (await session.call_tool("knowledge_support", request)).isError

    anyio.run(exercise)


def _canonical_input_schema():
    return json.loads((Path(__file__).resolve().parents[1]
                       / "contracts/knowledge-support.input.v8.schema.json").read_text())


def test_v8_root_properties_exactly_cover_existing_operation_fields():
    schema = _canonical_input_schema()
    names = {name for operation in ("query", "context", "explain", "read")
             for name in schema["$defs"][operation]["properties"]}
    assert schema.get("type") == "object"
    assert schema.get("properties") == {name: {} for name in names}


@pytest.mark.parametrize("arguments,wrong_field,other_field", [
    ({"operation": "query", "query": "Public procedure"}, "query", "receipt_id"),
    ({"operation": "context", "task": "Public procedure", "confirm_no_case_data": True},
     "task", "target"),
    ({"operation": "explain", "receipt_id": "queryreceipt_" + "a" * 24}, "receipt_id", "query"),
    ({"operation": "read", "target": {"kind": "knowledge", "knowledge_id": "knowledge_" + "a" * 24,
                                      "revision_id": "knowledgerev_" + "b" * 24},
      "scope": "project", "max_sensitivity": "public"}, "target", "task"),
])
def test_v8_exact_opencode_conversion_preserves_branch_constraints(
    arguments, wrong_field, other_field,
):
    from copy import deepcopy

    from jsonschema import Draft202012Validator

    schema = _canonical_input_schema()
    previous = deepcopy(schema)
    previous.pop("type", None)
    previous.pop("properties", None)
    # a3647 McpCatalog.convertTool keeps the root properties and closes extras.
    converted = {**schema, "type": "object", "properties": schema.get("properties", {}),
                 "additionalProperties": False}
    validators = [Draft202012Validator(value) for value in (previous, schema, converted)]
    for validator in validators:
        validator.validate(arguments)
        assert not validator.is_valid({**arguments, "unrelated_field": True})
        assert not validator.is_valid({**arguments, wrong_field: []})
        assert not validator.is_valid({**arguments, other_field: "not_for_this_operation"})
