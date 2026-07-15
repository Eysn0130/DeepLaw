from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from referencing import Registry, Resource

from deeplaw.cli import _parser
from deeplaw.ingest import build_release
from deeplaw.mcp_server import handle_support, tool_definition

from .helpers import manifest_document, write_docx, write_manifest


def test_mcp_exposes_one_bounded_leaf_tool() -> None:
    tools = [tool_definition()]

    assert [tool.name for tool in tools] == ["law_support"]
    schema = tools[0].inputSchema
    search_schema = schema["oneOf"][0]
    assert search_schema["additionalProperties"] is False
    assert search_schema["properties"]["limit"]["default"] == 5
    assert search_schema["properties"]["limit"]["maximum"] == 5
    assert search_schema["properties"]["purpose"]["enum"] == [
        "auto",
        "exact_citation",
        "as_of_version",
        "elements",
        "legal_issue_screen",
        "citation_verify",
        "broad_topic",
    ]
    assert "operation" in search_schema["properties"]
    assert tools[0].outputSchema["oneOf"]


def test_cli_accepts_explicit_stdio_alias() -> None:
    arguments = _parser().parse_args(["mcp", "--stdio"])

    assert arguments.command == "mcp"
    assert arguments.stdio is True


def test_cli_build_default_uses_shared_deeplaw_home(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "shared-law"
    monkeypatch.setenv("DEEPLAW_HOME", str(home))

    arguments = _parser().parse_args(
        ["build", "--source-root", "source", "--manifest", "manifest.json"]
    )

    assert arguments.output_root == home / "releases"


def test_single_tool_routes_search_get_and_verify(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "中华人民共和国测试法.docx"
    write_docx(
        document,
        ["中华人民共和国测试法", "第一条 为了验证公共法律检索契约，制定本测试规则。"],
    )
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="中华人民共和国测试法")],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )
    database = release / "deeplaw.sqlite3"

    search = handle_support(
        operation="search",
        query="中华人民共和国测试法 第一条",
        purpose="exact_citation",
        database=database,
    )
    card = search["evidence"][0]
    segment = handle_support(
        operation="get",
        segment_id=card["segment_id"],
        database=database,
    )
    verification = handle_support(
        operation="verify",
        segment_id=card["segment_id"],
        receipt_id=card["receipt_id"],
        database=database,
    )
    release_info = handle_support(operation="release_info", database=database)

    assert segment["text"].startswith("第一条")
    assert not any(key.endswith("_json") for key in segment)
    assert verification["valid"] is True
    assert release_info["database_sha256"]

    repository = Path(__file__).resolve().parents[1]
    contract_names = (
        "legal-evidence-card.v2.schema.json",
        "law-search-response.v2.schema.json",
        "law-segment.v2.schema.json",
        "law-verification.v1.schema.json",
        "law-release-info.v2.schema.json",
        "corpus-release-manifest.v2.schema.json",
    )
    registry = Registry()
    for name in contract_names:
        schema = json.loads((repository / "contracts" / name).read_text())
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    output_schema = json.loads(
        (repository / "contracts/law-support.output.v2.schema.json").read_text()
    )
    validator = Draft202012Validator(output_schema, registry=registry)
    validator.validate(search)
    validator.validate(segment)
    validator.validate(verification)
    validator.validate(release_info)


def test_stdio_mcp_rejects_unknown_and_operation_irrelevant_arguments(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, ["中华人民共和国测试法", "第一条 MCP 必须执行闭合契约。"])
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="中华人民共和国测试法")],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deeplaw", "mcp", "--stdio"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "DEEPLAW_DB": str(release / "deeplaw.sqlite3")},
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == ["law_support"]
            assert listed.tools[0].inputSchema["oneOf"][0]["additionalProperties"] is False
            assert "$defs" in listed.tools[0].outputSchema

            unknown = await session.call_tool(
                "law_support",
                {"operation": "release_info", "unexpected": "value"},
            )
            irrelevant = await session.call_tool(
                "law_support",
                {"operation": "release_info", "as_of": "2020-01-01"},
            )
            valid = await session.call_tool(
                "law_support",
                {
                    "operation": "search",
                    "query": "中华人民共和国测试法 第一条",
                    "purpose": "exact_citation",
                },
            )

            assert unknown.isError is True
            assert irrelevant.isError is True
            assert valid.isError is False
            assert valid.structuredContent["evidence"][0]["article_label"] == "第一条"

    asyncio.run(exercise())
