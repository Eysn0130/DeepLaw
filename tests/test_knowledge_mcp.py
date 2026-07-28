from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_mcp_server import (
    handle_knowledge_support,
    knowledge_tool_definition,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.mcp_server import tool_definition as legal_tool_definition
from deeplaw.util import canonical_json


def _ready_vault(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="mcp", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="constraint",
            memory_tier="project",
            title="MCP read boundary",
            statement="The Knowledge Asset MCP surface must remain read-only.",
            semantic_key="mcp.read-boundary",
            sensitivity="internal",
            tags=tuple(f"tag-{index}" for index in range(12)),
            origin_uri=str(tmp_path / "private-local-origin"),
        )
        asset = vault.approve_asset(proposal.asset_id, confirm_reviewed=True)
    return root, asset.asset_id


def test_knowledge_mcp_is_a_separate_single_read_only_tool(tmp_path: Path) -> None:
    root, asset_id = _ready_vault(tmp_path)
    tool = knowledge_tool_definition()

    assert tool.name == "knowledge_support"
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.inputSchema["additionalProperties"] is False
    assert tool.inputSchema["required"] == ["operation"]
    assert list(Draft202012Validator(tool.inputSchema).iter_errors({}))
    assert tool.inputSchema["properties"]["operation"]["enum"] == [
        "search",
        "get",
        "context",
        "verify",
        "inspect",
    ]
    assert "remember" not in tool.inputSchema["properties"]["operation"]["enum"]
    assert "learn" not in tool.inputSchema["properties"]["operation"]["enum"]
    assert legal_tool_definition().name == "law_support"
    assert legal_tool_definition().inputSchema["type"] == "object"
    assert legal_tool_definition().outputSchema["type"] == "object"
    assert tool.outputSchema["type"] == "object"
    assert "capsule" in tool.outputSchema["$defs"]
    input_validator = Draft202012Validator(tool.inputSchema)
    assert list(
        input_validator.iter_errors(
            {
                "operation": "context",
                "task": "compile project context",
            }
        )
    )
    input_validator.validate(
        {
            "operation": "context",
            "task": "compile project context",
            "confirm_no_case_data": True,
        }
    )

    result = handle_knowledge_support(
        operation="get",
        asset_id=asset_id,
        max_chars=20,
        vault_path=root,
    )
    assert result["result"]["content_truncated"] is True
    assert result["result"]["status"] == "active"
    assert len(result["result"]["tags"]) == 8
    assert result["result"]["tag_count"] == 12
    assert result["result"]["tags_truncated"] is True
    assert result["result"]["origin_uri"] is None
    assert result["result"]["legal_authority"] is False
    assert result["authority_boundary"] == {
        "legal_authority": False,
        "official_legal_sources_tool": "law_support",
        "persistent_writes": "local_cli_only",
        "case_data_allowed": False,
    }
    assert str(tmp_path) not in canonical_json(result)

    repository = Path(__file__).resolve().parents[1]
    schema = __import__("json").loads(
        (repository / "contracts/knowledge-support.output.v1.schema.json").read_text()
    )
    Draft202012Validator(schema).validate(result)


def test_knowledge_mcp_search_context_verify_and_inspect_are_bounded(
    tmp_path: Path,
) -> None:
    root, asset_id = _ready_vault(tmp_path)

    search = handle_knowledge_support(
        operation="search",
        query="MCP read boundary",
        limit=100,
        max_chars=100_000,
        vault_path=root,
    )
    context = handle_knowledge_support(
        operation="context",
        task="preserve MCP read boundary",
        confirm_no_case_data=True,
        limit=100,
        max_chars=100_000,
        vault_path=root,
    )
    verification = handle_knowledge_support(
        operation="verify",
        asset_id=asset_id,
        vault_path=root,
    )
    inspection = handle_knowledge_support(
        operation="inspect",
        vault_path=root,
    )

    assert len(search["result"]["results"]) <= 5
    assert search["result"]["total_excerpt_chars"] <= 6_000
    assert search["result"]["results"][0]["tag_count"] == 12
    assert search["result"]["results"][0]["tags_truncated"] is True
    assert "score" not in search["result"]["results"][0]
    assert search["result"]["results"][0]["rank"] == 1
    assert search["result"]["ranking"]["method"] == (
        "evidence_governed_retrieval_fabric"
    )
    assert search["result"]["ranking"]["numeric_confidence_exposed"] is False
    assert context["result"]["budget"]["max_items"] == 8
    assert context["result"]["budget"]["max_chars"] == 8_000
    assert context["result"]["budget"]["payload_chars"] <= 64_000
    assert context["result"]["trust_boundary"]["automatic_memory_write"] is False
    assert verification["result"]["valid"] is True
    assert inspection["result"]["agent_ready"] is True
    assert "path" not in inspection["result"]
    for response in (search, context, verification, inspection):
        Draft202012Validator(knowledge_tool_definition().outputSchema).validate(response)
        assert len(canonical_json(response)) <= 65_536


def test_knowledge_mcp_search_and_context_use_the_retrieval_fabric(
    tmp_path: Path,
) -> None:
    root, asset_id = _ready_vault(tmp_path)

    search = handle_knowledge_support(
        operation="search",
        query=asset_id,
        vault_path=root,
    )
    context = handle_knowledge_support(
        operation="context",
        task=f"Load the exact reviewed knowledge item {asset_id}",
        confirm_no_case_data=True,
        vault_path=root,
    )

    assert [item["asset_id"] for item in search["result"]["results"]] == [asset_id]
    assert search["result"]["results"][0]["hit_reason"] == (
        "retrieval_fabric:exact_id"
    )
    capsule_items = [
        item
        for field in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in context["result"][field]
    ]
    selected = next(item for item in capsule_items if item["asset_id"] == asset_id)
    assert selected["selection_reason"] == "retrieval_fabric:exact_id"
    Draft202012Validator(knowledge_tool_definition().outputSchema).validate(search)
    Draft202012Validator(knowledge_tool_definition().outputSchema).validate(context)


def test_knowledge_mcp_cannot_fetch_restricted_assets_by_identifier(
    tmp_path: Path,
) -> None:
    root, _ = _ready_vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Restricted secret",
            statement="This asset must never cross the Agent MCP boundary.",
            sensitivity="restricted",
        )
        restricted = vault.approve_asset(proposal.asset_id, confirm_reviewed=True)

    for operation in ("get", "verify"):
        with pytest.raises(PermissionError, match="unavailable"):
            handle_knowledge_support(
                operation=operation,
                asset_id=restricted.asset_id,
                vault_path=root,
            )


def test_knowledge_mcp_stops_agent_reads_when_the_audit_chain_is_invalid(
    tmp_path: Path,
) -> None:
    root, _ = _ready_vault(tmp_path)
    connection = sqlite3.connect(root / "vault.sqlite3")
    try:
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE sequence = 1",
            ('{"tampered":true}',),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="Agent reads stopped"):
        handle_knowledge_support(
            operation="search",
            query="MCP read boundary",
            vault_path=root,
        )
    inspection = handle_knowledge_support(operation="inspect", vault_path=root)
    assert inspection["result"]["audit"]["valid"] is False
    Draft202012Validator(knowledge_tool_definition().outputSchema).validate(inspection)


def test_knowledge_mcp_stops_on_state_tampering_even_when_event_hashes_are_valid(
    tmp_path: Path,
) -> None:
    root, asset_id = _ready_vault(tmp_path)
    connection = sqlite3.connect(root / "vault.sqlite3")
    try:
        connection.execute(
            "UPDATE assets SET statement = ? WHERE asset_id = ?",
            ("Tampered statement.", asset_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="integrity is invalid"):
        handle_knowledge_support(
            operation="get",
            asset_id=asset_id,
            vault_path=root,
        )
    inspection = handle_knowledge_support(operation="inspect", vault_path=root)
    assert inspection["result"]["audit"]["valid"] is True
    assert inspection["result"]["integrity"]["state"]["valid"] is False
    assert inspection["result"]["agent_ready"] is False


def test_knowledge_mcp_context_requires_explicit_case_boundary_confirmation(
    tmp_path: Path,
) -> None:
    root, _ = _ready_vault(tmp_path)

    with pytest.raises(ValueError, match="no Analytix case material"):
        handle_knowledge_support(
            operation="context",
            task="compile project context",
            vault_path=root,
        )


def test_knowledge_mcp_does_not_disclose_an_unavailable_vault_path(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "private" / "missing-vault"

    with pytest.raises(RuntimeError) as captured:
        handle_knowledge_support(operation="inspect", vault_path=missing)

    assert str(tmp_path) not in str(captured.value)
    assert "unavailable or unsafe" in str(captured.value)


def test_knowledge_mcp_get_rejects_an_asset_with_missing_source_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="source-integrity", scope="project")
    source = tmp_path / "source.md"
    source.write_text(
        "# MCP evidence\nCurrent source evidence must remain content-verifiable.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        asset = vault.approve_asset(
            compiled["asset_ids"][0],
            confirm_reviewed=True,
        )
        vault.source_file_path(asset.source_refs[0].source_id).unlink()

    with pytest.raises(RuntimeError, match="source/integrity"):
        handle_knowledge_support(
            operation="get",
            asset_id=asset.asset_id,
            vault_path=root,
        )


def test_stdio_knowledge_mcp_rejects_write_and_unknown_arguments(tmp_path: Path) -> None:
    root, _ = _ready_vault(tmp_path)

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "deeplaw",
                "knowledge",
                "mcp",
                "--stdio",
                "--vault",
                str(root),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["knowledge_support"]

            valid = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "search",
                    "query": "MCP read boundary",
                },
            )
            write = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "remember",
                    "query": "persist this",
                },
            )
            unknown = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "inspect",
                    "unexpected": True,
                },
            )

            assert valid.isError is False
            assert valid.structuredContent["result"]["results"]
            assert write.isError is True
            assert unknown.isError is True

    asyncio.run(exercise())


def test_stdio_knowledge_mcp_starts_without_a_vault_and_fails_reads_safely(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "private" / "missing-vault"

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "deeplaw",
                "knowledge",
                "mcp",
                "--stdio",
                "--vault",
                str(missing),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=os.environ.copy(),
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["knowledge_support"]

            unavailable = await session.call_tool(
                "knowledge_support",
                {"operation": "inspect"},
            )
            assert unavailable.isError is True
            message = " ".join(
                getattr(item, "text", "") for item in unavailable.content
            )
            assert "unavailable or unsafe" in message
            assert str(tmp_path) not in message

    asyncio.run(exercise())
