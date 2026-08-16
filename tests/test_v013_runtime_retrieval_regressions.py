from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, RLock
from typing import Any

from mcp import types
from mcp.server.lowlevel.server import RequestContext, request_ctx

from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import (
    _KnowledgeRuntime,
    create_knowledge_mcp_server,
    handle_knowledge_support,
    knowledge_tool_definition,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.persistent_read_runtime import PersistentReadRuntime
from deeplaw.util import canonical_json


def _synthetic_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-runtime-retrieval", scope="project")
    initialize_autonomous_core(root)
    return root


async def _call(server: Any, runtime: Any, request_id: int, arguments: dict[str, Any]) -> Any:
    token = request_ctx.set(
        RequestContext(
            request_id=request_id,
            meta=None,
            session=None,
            lifespan_context=runtime,
        )
    )
    try:
        return await server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                params=types.CallToolRequestParams(
                    name="knowledge_support",
                    arguments=arguments,
                )
            )
        )
    finally:
        request_ctx.reset(token)


def _seed_memory(
    root: Path,
    *,
    writer_id: str = "v013-regression",
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id=writer_id,
            max_sensitivity="restricted",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )["grant_id"]
        public = store.remember(
            grant_id=grant_id,
            idempotency_key=f"{writer_id}:public",
            title="Public regression marker",
            body="public-boundary-marker needle",
            kind="memory",
            scope="project",
            sensitivity="public",
            memory_type="semantic",
            confirm_no_case_data=True,
        )
        private = store.remember(
            grant_id=grant_id,
            idempotency_key=f"{writer_id}:private",
            title="Private regression marker",
            body="private-boundary-marker needle",
            kind="memory",
            scope="project",
            sensitivity="private",
            memory_type="semantic",
            confirm_no_case_data=True,
        )
    return grant_id, public, private


def test_persistent_read_runtime_reuses_verified_snapshot_without_nested_legacy_verify(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _synthetic_vault(tmp_path)
    opened = {"knowledge_vault": 0, "autonomous_store": 0}
    verified = {"knowledge_vault": 0, "autonomous_store": 0}

    original_vault_init = KnowledgeVault.__init__
    original_store_init = AutonomousKnowledgeStore.__init__
    original_vault_verify = KnowledgeVault.verify_integrity
    original_store_verify = AutonomousKnowledgeStore.verify

    def counted_vault_init(self: KnowledgeVault, *args: Any, **kwargs: Any) -> None:
        opened["knowledge_vault"] += 1
        original_vault_init(self, *args, **kwargs)

    def counted_store_init(
        self: AutonomousKnowledgeStore,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        opened["autonomous_store"] += 1
        original_store_init(self, *args, **kwargs)

    def counted_vault_verify(
        self: KnowledgeVault,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        verified["knowledge_vault"] += 1
        return original_vault_verify(self, *args, **kwargs)

    def counted_store_verify(
        self: AutonomousKnowledgeStore,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        verified["autonomous_store"] += 1
        return original_store_verify(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgeVault, "__init__", counted_vault_init)
    monkeypatch.setattr(AutonomousKnowledgeStore, "__init__", counted_store_init)
    monkeypatch.setattr(KnowledgeVault, "verify_integrity", counted_vault_verify)
    monkeypatch.setattr(AutonomousKnowledgeStore, "verify", counted_store_verify)

    async def exercise() -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            await server.request_handlers[types.ListToolsRequest](None)
            handler = server.request_handlers[types.CallToolRequest]
            for request_id in (1, 2):
                token = request_ctx.set(
                    RequestContext(
                        request_id=request_id,
                        meta=None,
                        session=None,
                        lifespan_context=runtime,
                    )
                )
                try:
                    response = await handler(
                        types.CallToolRequest(
                            params=types.CallToolRequestParams(
                                name="knowledge_support",
                                arguments={
                                    "operation": "recall",
                                    "query": "persistent runtime probe",
                                    "plane": "autonomous",
                                },
                            )
                        )
                    )
                    assert response.root.isError is False
                finally:
                    request_ctx.reset(token)
                if request_id == 1:
                    first_opened = dict(opened)
                    first_verified = dict(verified)
        return first_opened, first_verified, dict(opened), dict(verified)

    first_opened, first_verified, final_opened, final_verified = asyncio.run(exercise())

    assert first_opened == {
        "knowledge_vault": 1,
        "autonomous_store": 1,
    }
    assert first_verified == {
        "knowledge_vault": 1,
        "autonomous_store": 1,
    }
    assert final_opened == first_opened
    assert final_verified == first_verified


def test_query_without_explicit_plan_version_uses_v6_provider_receipt(
    tmp_path: Path,
) -> None:
    root = _synthetic_vault(tmp_path)

    response = handle_knowledge_support(
        operation="query",
        query="default query plan",
        vault_path=root,
    )

    assert response["operation"] == "query"
    result = response.get("result", {})
    assert result.get("schema_version") == "deeplaw.provider-knowledge-capsule/v2"
    assert set(result.get("receipt", {})) == {"receipt_id"}


def test_v6_tool_contract_and_instructions_recommend_one_read_path(
    tmp_path: Path,
) -> None:
    root = _synthetic_vault(tmp_path)
    tool = knowledge_tool_definition(autonomous=True)
    assert tool.inputSchema["$id"].endswith("knowledge-support.input.v6.schema.json")
    assert tool.inputSchema["type"] == "object"
    query = {
        "operation": "query",
        "query": "bounded task knowledge",
        "query_plan_version": "6",
        "query_target": {"text": "bounded task knowledge"},
        "applicable_duties": ["primary_answer", "unresolved_gap"],
        "capsule_projection": "compact",
    }
    from jsonschema import Draft202012Validator

    Draft202012Validator(tool.inputSchema).validate(query)
    instructions = create_knowledge_mcp_server(vault_path=root).instructions[:512]
    for marker in (
        "query=task knowledge",
        "context=bounded Knowledge Capsule",
        "wiki=pages and navigation",
        "source=original user evidence",
        "law_support=separate Authoritative Evidence",
        "verify=complete integrity verification",
    ):
        assert marker in instructions


def test_warm_mcp_query_reuses_snapshot_and_reads_local_audit_by_receipt(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = _synthetic_vault(tmp_path)
    verified = {"legacy": 0, "autonomous": 0}
    original_vault_verify = KnowledgeVault.verify_integrity
    original_store_verify = AutonomousKnowledgeStore.verify

    def counted_vault_verify(
        self: KnowledgeVault,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        verified["legacy"] += 1
        return original_vault_verify(self, *args, **kwargs)

    def counted_store_verify(
        self: AutonomousKnowledgeStore,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        verified["autonomous"] += 1
        return original_store_verify(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgeVault, "verify_integrity", counted_vault_verify)
    monkeypatch.setattr(AutonomousKnowledgeStore, "verify", counted_store_verify)

    async def exercise() -> None:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            handler = server.request_handlers[types.CallToolRequest]

            async def call(request_id: int, arguments: dict[str, Any]) -> Any:
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

            first = await call(
                1,
                {"operation": "query", "query": "warm persistent query"},
            )
            second = await call(
                2,
                {"operation": "query", "query": "warm persistent query"},
            )
            assert first.root.isError is False
            assert second.root.isError is False
            receipt_id = second.root.structuredContent["result"]["receipt"]["receipt_id"]
            audit = await call(
                3,
                {"operation": "explain", "receipt_id": receipt_id},
            )
            assert audit.root.isError is False
            assert audit.root.structuredContent["result"]["receipt_id"] == receipt_id

    asyncio.run(exercise())
    assert verified == {"legacy": 1, "autonomous": 1}


def test_wiki_lookup_compatibility_has_explicit_deprecation_metadata(
    tmp_path: Path,
) -> None:
    root = _synthetic_vault(tmp_path)

    response = handle_knowledge_support(
        operation="wiki_lookup",
        query="legacy wiki lookup",
        vault_path=root,
    )
    result = response["result"]

    assert result.get("deprecation") == {
        "deprecated": True,
        "replacement": "wiki",
        "removal_version": "0.15.0",
    }
    assert result["living_wiki"]["lookup_via_admitted_canonical_revisions"] is True


def test_withdrawal_first_read_does_not_return_a_cached_revision(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)
    grant_id, public, _ = _seed_memory(root, writer_id="withdrawal-regression")
    arguments = {
        "operation": "context",
        "query_plan_version": "5",
        "task": "needle",
        "confirm_no_case_data": True,
        "scope": "project",
        "max_sensitivity": "public",
    }

    async def exercise() -> tuple[Any, Any]:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            first = await _call(server, runtime, 1, arguments)
            assert first.root.isError is False
            with AutonomousKnowledgeStore(root, read_only=False) as store:
                store.forget(
                    grant_id=grant_id,
                    idempotency_key="withdrawal-regression:forget",
                    knowledge_id=public["knowledge_id"],
                    expected_revision_id=public["revision_id"],
                    reason="Owner withdrawal regression test.",
                    confirm_no_case_data=True,
                )
            second = await _call(server, runtime, 2, arguments)
            return first, second

    first, second = asyncio.run(exercise())
    assert "public-boundary-marker" in str(first.root.structuredContent)
    assert second.root.isError is False
    assert "public-boundary-marker" not in str(second.root.structuredContent)


def test_sensitivity_upgrade_never_reuses_a_lower_sensitivity_response(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)
    grant_id, public_revision, _ = _seed_memory(root, writer_id="sensitivity-regression")

    async def exercise() -> tuple[str, str, str, bool, str]:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            private = await _call(
                server,
                runtime,
                1,
                {
                    "operation": "context",
                    "query_plan_version": "5",
                    "task": "needle",
                    "confirm_no_case_data": True,
                    "scope": "project",
                    "max_sensitivity": "private",
                },
            )
            public = await _call(
                server,
                runtime,
                2,
                {
                    "operation": "context",
                    "query_plan_version": "5",
                    "task": "needle",
                    "confirm_no_case_data": True,
                    "scope": "project",
                    "max_sensitivity": "public",
                },
            )
            assert private.root.isError is False
            assert public.root.isError is False
            old_snapshot = runtime.persistent.snapshot
            with AutonomousKnowledgeStore(root, read_only=False) as store:
                store.remember(
                    grant_id=grant_id,
                    idempotency_key="sensitivity-regression:upgrade",
                    title="Public regression marker",
                    body="upgraded-private-marker needle",
                    kind="memory",
                    knowledge_id=public_revision["knowledge_id"],
                    expected_revision_id=public_revision["revision_id"],
                    scope="project",
                    sensitivity="private",
                    memory_type="semantic",
                    confirm_no_case_data=True,
                )
            after_upgrade = await _call(
                server,
                runtime,
                3,
                {
                    "operation": "context",
                    "query_plan_version": "5",
                    "task": "needle",
                    "confirm_no_case_data": True,
                    "scope": "project",
                    "max_sensitivity": "public",
                },
            )
            assert after_upgrade.root.isError is False
            cache_payload = canonical_json(
                [entry[1] for entry in runtime.read_result_cache.values()]
            )
            return (
                str(private.root.structuredContent),
                str(public.root.structuredContent),
                str(after_upgrade.root.structuredContent),
                old_snapshot.closed,
                cache_payload,
            )

    private_payload, public_payload, upgraded_payload, old_closed, cache_payload = asyncio.run(
        exercise()
    )
    assert "private-boundary-marker" in private_payload
    assert "private-boundary-marker" not in public_payload
    assert "public-boundary-marker" in public_payload
    assert "public-boundary-marker" not in upgraded_payload
    assert "upgraded-private-marker" not in upgraded_payload
    assert old_closed is True
    assert "public-boundary-marker" not in cache_payload


def test_scope_request_cache_partition_does_not_cross_vault_scope(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)
    _seed_memory(root, writer_id="scope-regression")

    async def exercise() -> tuple[str, str, int]:
        server = create_knowledge_mcp_server(vault_path=root)
        async with server.lifespan(server) as runtime:
            project = await _call(
                server,
                runtime,
                1,
                {
                    "operation": "context",
                    "query_plan_version": "5",
                    "task": "needle",
                    "confirm_no_case_data": True,
                    "scope": "project",
                    "max_sensitivity": "public",
                },
            )
            domain = await _call(
                server,
                runtime,
                2,
                {
                    "operation": "context",
                    "query_plan_version": "5",
                    "task": "needle",
                    "confirm_no_case_data": True,
                    "scope": "domain",
                    "max_sensitivity": "public",
                },
            )
            assert project.root.isError is False
            assert domain.root.isError is False
            return (
                str(project.root.structuredContent),
                str(domain.root.structuredContent),
                len(runtime.read_result_cache),
            )

    project_payload, domain_payload, cache_entries = asyncio.run(exercise())
    assert "public-boundary-marker" in project_payload
    assert "public-boundary-marker" not in domain_payload
    assert cache_entries == 2


def test_eight_independent_readers_do_not_share_snapshots_or_block_writer(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)
    _seed_memory(root, writer_id="reader-regression")
    barrier = Barrier(9)

    def read_in_isolated_runtime(_: int) -> tuple[bool, bool, bool]:
        runtime = PersistentReadRuntime(root)
        try:
            first = runtime.get_snapshot()
            first_identity = first.identity
            assert first.legacy.read_only is True
            assert first.store.read_only is True
            barrier.wait()
            barrier.wait()
            second = runtime.get_snapshot()
            return first_identity != second.identity, first.closed, second.legacy.read_only
        finally:
            runtime.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(read_in_isolated_runtime, index) for index in range(8)]
        barrier.wait()
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            store.enable_grant(writer_id="writer-while-readers")
        barrier.wait()
        results = [future.result() for future in futures]

    assert all(changed and closed and read_only for changed, closed, read_only in results)


def test_restricted_admission_request_never_caches_provider_body(tmp_path: Path) -> None:
    root = _synthetic_vault(tmp_path)
    response = handle_knowledge_support(
        operation="context",
        task="restricted cache probe",
        confirm_no_case_data=True,
        vault_path=root,
    )
    response["result"]["restricted_body"] = "restricted-body-marker"
    runtime = _KnowledgeRuntime(vault_path=root, lock=RLock())
    assert runtime.retain_read_result(
        ("restricted-request",),
        response,
        max_sensitivity="restricted",
    ) is False
    assert runtime.read_result_cache == {}
    assert "restricted-body-marker" not in str(runtime.read_result_cache)
