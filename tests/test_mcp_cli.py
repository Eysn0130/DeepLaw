from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from referencing import Registry, Resource

from deeplaw.cli import _parser
from deeplaw.ingest import build_release
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.mcp_server import handle_support, tool_definition
from deeplaw.private_library import add_private_document, resolve_private_database
from deeplaw.util import canonical_json, provider_safe_exception, sha256_bytes

from .helpers import manifest_document, write_docx, write_manifest


def test_provider_error_projection_omits_paths_secrets_and_unsafe_unicode() -> None:
    safe = ValueError("query budget is invalid")
    assert provider_safe_exception(safe, interface="law_support") is safe

    for message in (
        "failed to open /Users/example/private/vault.sqlite3",
        "api_key=super-secret-value",
        "unsafe\u202etext",
    ):
        projected = provider_safe_exception(ValueError(message), interface="law_support")
        assert type(projected) is RuntimeError
        assert str(projected) == "law_support request failed closed; sensitive details omitted"
        assert message not in str(projected)


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


def test_law_support_v4_adds_challenges_without_mutating_frozen_v3() -> None:
    repository = Path(__file__).resolve().parents[1]
    v2 = json.loads(
        (repository / "contracts/law-support.input.v2.schema.json").read_text()
    )
    v3 = json.loads(
        (repository / "contracts/law-support.input.v3.schema.json").read_text()
    )
    v4 = json.loads(
        (repository / "contracts/law-support.input.v4.schema.json").read_text()
    )

    v2_operations = {
        branch["properties"]["operation"]["const"]
        for branch in v2["oneOf"]
    }
    v3_operations = {
        branch["properties"]["operation"]["const"]
        for branch in v3["oneOf"]
    }
    assert "federated_context" not in v2_operations
    assert v3_operations == v2_operations | {"federated_context"}
    v4_operations = {
        branch["properties"]["operation"]["const"]
        for branch in v4["oneOf"][1:]
    }
    assert v4_operations == {
        "capabilities",
        "challenge_trace",
        "challenge_get",
        "challenge_replay",
    }
    assert tool_definition().inputSchema["$id"] == "urn:deeplaw:schema:law-support-input:v4"


def test_cli_accepts_explicit_stdio_alias() -> None:
    arguments = _parser().parse_args(["mcp", "--stdio"])

    assert arguments.command == "mcp"
    assert arguments.stdio is True


def test_cli_exposes_explicit_document_model_setup_and_status_only() -> None:
    setup = _parser().parse_args(
        ["document-engine", "setup", "--local-files-only"]
    )
    status = _parser().parse_args(["document-engine", "status"])

    assert setup.document_engine_command == "setup"
    assert setup.local_files_only is True
    assert status.document_engine_command == "status"


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
        "law-release-info.v3.schema.json",
        "corpus-release-manifest.v3.schema.json",
        "law-support.output.v3.schema.json",
        "law-support.output.v4.schema.json",
        "evidence-capabilities.v1.schema.json",
        "segment-evidence-capabilities.v1.schema.json",
        "authoritative-challenge-trace.v1.schema.json",
        "authoritative-challenge-replay.v1.schema.json",
        "law-federated-context.v1.schema.json",
    )
    registry = Registry()
    for name in contract_names:
        schema = json.loads((repository / "contracts" / name).read_text())
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    output_schema = json.loads(
        (repository / "contracts/law-support.output.v4.schema.json").read_text()
    )
    validator = Draft202012Validator(output_schema, registry=registry)
    validator.validate(search)
    validator.validate(segment)
    validator.validate(verification)
    validator.validate(release_info)


def test_federated_legal_context_preserves_authority_partitions_and_tag_admission(
    tmp_path: Path,
) -> None:
    official_source = tmp_path / "official"
    official_document = official_source / "official.docx"
    write_docx(
        official_document,
        [
            "中华人民共和国联合上下文测试法",
            "第一条 联合上下文规则只用于验证官方法源分区。",
        ],
    )
    official_manifest = write_manifest(
        official_source / "manifest.json",
        [
            manifest_document(
                official_source,
                official_document.name,
                title="中华人民共和国联合上下文测试法",
            )
        ],
    )
    official_release, _ = build_release(
        source_root=official_source,
        manifest_path=official_manifest,
        output_root=tmp_path / "official-releases",
    )

    private_source = tmp_path / "private-reference.txt"
    private_source.write_text(
        "用户私有联合上下文资料\n第一条 私有唯一术语 private-only-marker。\n",
        encoding="utf-8",
    )
    private_home = tmp_path / "private-home"
    add_private_document(
        private_source,
        title="用户私有联合上下文资料",
        confirm_no_case_data=True,
        home=private_home,
    )

    vault = tmp_path / "knowledge-vault"
    initialize_knowledge_vault(vault, name="legal-interpretations", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="codex-agent",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )["grant_id"]
        store.record_run(
            grant_id=grant_id,
            idempotency_key="legal-run",
            run_id="run-legal-federation",
            task="Compile a non-authoritative interpretation for a legal context test.",
            host_id="pytest",
            model_id="test-model",
            status="succeeded",
            scope="project",
            sensitivity="public",
            confirm_no_case_data=True,
        )
        store.remember(
            grant_id=grant_id,
            idempotency_key="unrelated-knowledge",
            title="联合上下文普通知识",
            body="这条普通知识排名很高，但不能进入法律解释分区。",
            kind="claim",
            scope="project",
            sensitivity="public",
            run_id="run-legal-federation",
            tags=["not_legal"],
            confirm_no_case_data=True,
        )
        legal_revision = store.remember(
            grant_id=grant_id,
            idempotency_key="legal-interpretation",
            title="联合上下文规则的 Agent 解释",
            body="这是 Agent 派生解释，不是法律权威，也不能替代原文引用。",
            kind="claim",
            scope="project",
            sensitivity="public",
            run_id="run-legal-federation",
            tags=["legal_interpretation"],
            confirm_no_case_data=True,
        )

    result = handle_support(
        operation="federated_context",
        query="联合上下文",
        purpose="broad_topic",
        database=official_release / "deeplaw.sqlite3",
        private_database=resolve_private_database(home=private_home),
        knowledge_vault=vault,
        include_private=True,
        include_agent_interpretation=True,
        confirm_no_case_data=True,
    )

    assert result["authority_partitions_preserved"] is True
    assert result["legal_adjudication"] is False
    assert result["official"]["legal_authority"] is True
    assert result["official"]["results"]
    assert result["user_private"]["legal_authority"] is False
    assert result["user_private"]["results"]
    assert result["agent_interpretation"]["legal_authority"] is False
    assert [item["knowledge_id"] for item in result["agent_interpretation"]["results"]] == [
        legal_revision["knowledge_id"]
    ]
    assert result["agent_interpretation"]["receipt"]["query_plan"]["filters"] == {
        "kinds": ["claim", "comparison", "concept", "synthesis"],
        "required_tags": ["legal_interpretation"],
    }

    repository = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (repository / "contracts/law-federated-context.v1.schema.json").read_text()
    )
    validator = Draft202012Validator(schema)
    validator.validate(result)
    digest_body = {key: value for key, value in result.items() if key != "context_digest"}
    assert result["context_digest"] == sha256_bytes(
        canonical_json(digest_body).encode("utf-8")
    )
    mismatched_authority = json.loads(json.dumps(result))
    mismatched_authority["official"]["origin"] = "agent_derived"
    assert list(validator.iter_errors(mismatched_authority))

    private_only = handle_support(
        operation="federated_context",
        query="private-only-marker",
        database=official_release / "deeplaw.sqlite3",
        private_database=resolve_private_database(home=private_home),
        include_private=True,
        include_agent_interpretation=False,
        confirm_no_case_data=True,
    )
    assert private_only["official"]["status"] == "empty"
    assert private_only["official"]["results"] == []
    assert private_only["user_private"]["status"] == "available"
    assert private_only["user_private"]["results"]
    assert any("no fallback was relabeled" in gap for gap in private_only["gaps"])


def test_federated_legal_context_requires_explicit_case_data_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="no case data"):
        handle_support(
            operation="federated_context",
            query="法律问题",
            database=tmp_path / "missing.sqlite3",
            confirm_no_case_data=False,
        )


def test_law_support_fails_closed_before_exposing_a_local_path(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "path-boundary.docx"
    write_docx(
        document,
        ["中华人民共和国路径边界测试法", "第一条 不得暴露 /Users/example/private/source.pdf。"],
    )
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="中华人民共和国路径边界测试法")],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )

    database = release / "deeplaw.sqlite3"
    with pytest.raises(PermissionError, match="local absolute path"):
        handle_support(
            operation="search",
            query="中华人民共和国路径边界测试法 第一条",
            purpose="exact_citation",
            database=database,
        )


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
            segment_id = valid.structuredContent["evidence"][0]["segment_id"]
            capabilities = await session.call_tool(
                "law_support",
                {"operation": "capabilities", "segment_id": segment_id},
            )
            challenge = await session.call_tool(
                "law_support",
                {
                    "operation": "challenge_trace",
                    "query": "中华人民共和国测试法 第一条",
                    "purpose": "exact_citation",
                },
            )
            trace = challenge.structuredContent
            challenge_get = await session.call_tool(
                "law_support",
                {"operation": "challenge_get", "trace_id": trace["trace_id"]},
            )
            replay = await session.call_tool(
                "law_support",
                {"operation": "challenge_replay", "trace": trace},
            )

            assert unknown.isError is True
            assert irrelevant.isError is True
            assert valid.isError is False
            assert valid.structuredContent["evidence"][0]["article_label"] == "第一条"
            assert capabilities.isError is False
            assert capabilities.structuredContent["capabilities"]["integrity"] == "verified"
            assert challenge.isError is False
            assert challenge_get.structuredContent == trace
            assert replay.structuredContent["valid"] is True

    asyncio.run(exercise())
