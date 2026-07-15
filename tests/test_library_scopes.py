from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import deeplaw.official as official_module
from deeplaw.catalog_signing import (
    export_trust_store,
    initialize_signing_key,
    sign_catalog_file,
)
from deeplaw.ingest import build_release
from deeplaw.mcp_server import handle_support, tool_definition
from deeplaw.models import SearchRequest
from deeplaw.official import (
    bundled_catalog_path,
    disable_official,
    enable_official,
    official_status,
    sync_official,
    uninstall_official,
)
from deeplaw.private_library import (
    add_private_document,
    delete_private_document,
    list_private_documents,
    private_home,
    resolve_private_database,
)
from deeplaw.search import DeepLaw
from deeplaw.store import resolve_active_database

from .helpers import manifest_document, write_docx, write_manifest


def _write_catalog(
    path: Path,
    documents: list[dict[str, object]],
    *,
    sequence: int,
) -> Path:
    value = {
        "schemaVersion": "deeplaw.official-catalog/v1",
        "catalogId": "deeplaw-test-official",
        "sequence": sequence,
        "version": f"2026.07.15.{sequence}",
        "publishedOn": "2026-07-15",
        "package": {
            "name": "DeepLaw official test catalog",
            "retrievedOn": "2026-07-15",
            "reviewedOn": "2026-07-15",
            "documentCount": len(documents),
        },
        "documents": documents,
    }
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def test_bundled_official_catalog_matches_its_public_contract() -> None:
    repository = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (repository / "contracts/official-catalog.v1.schema.json").read_text(encoding="utf-8")
    )
    catalog = json.loads(bundled_catalog_path().read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(catalog)
    assert catalog["catalogId"] == "deeplaw-cn-official"
    assert catalog["package"]["documentCount"] == 28
    assert len(catalog["documents"]) == 28
    assert catalog["buildPolicy"] == {
        "pdfFallback": "vision-consensus",
        "allowNeedsOcr": True,
    }
    assert catalog["reviewOverlay"]["resource"] == "core-2026-07-14.ai-review.json"


def test_official_install_update_disable_enable_and_uninstall(tmp_path: Path) -> None:
    source = tmp_path / "source"
    first = source / "中华人民共和国测试法.docx"
    write_docx(first, ["中华人民共和国测试法", "第一条 官方目录第一版。"])
    documents = [manifest_document(source, first.name, title="中华人民共和国测试法")]
    catalog_v1 = _write_catalog(tmp_path / "catalog-v1.json", documents, sequence=1)
    home = tmp_path / "home"

    installed = sync_official(
        catalog_source=catalog_v1,
        source_root=source,
        home=home,
        allow_unsigned_local_catalog=True,
    )
    release_v1 = installed["active_release_id"]
    database_v1 = resolve_active_database(home=home)
    with DeepLaw(database_v1, expected_scope="official") as law:
        result = law.search(SearchRequest(query="中华人民共和国测试法 第一条"))
    assert result.evidence[0].title == "中华人民共和国测试法"
    assert official_status(home=home)["catalog"]["sequence"] == 1

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            official_module,
            "build_release",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("unchanged catalog must reuse its immutable release")
            ),
        )
        unchanged = sync_official(
            catalog_source=catalog_v1,
            source_root=source,
            update=True,
            home=home,
            allow_unsigned_local_catalog=True,
        )
    assert unchanged["changed"] is False
    assert unchanged["restart_required"] is False
    assert unchanged["report"]["cached"] is True

    second = source / "测试实施办法.docx"
    write_docx(second, ["测试实施办法", "第一条 官方目录新增法律资料用于版本更新验证。"])
    documents_v2 = [
        *documents,
        manifest_document(source, second.name, title="测试实施办法"),
    ]
    catalog_v2 = _write_catalog(tmp_path / "catalog-v2.json", documents_v2, sequence=2)
    updated = sync_official(
        catalog_source=catalog_v2,
        source_root=source,
        update=True,
        home=home,
        allow_unsigned_local_catalog=True,
    )
    release_v2 = updated["active_release_id"]

    assert release_v2 != release_v1
    assert (home / "releases" / release_v1 / "deeplaw.sqlite3").is_file()
    assert resolve_active_database(home=home).parent.name == release_v2
    with DeepLaw(resolve_active_database(home=home), expected_scope="official") as law:
        result = law.search(SearchRequest(query="测试实施办法 第一条"))
    assert result.evidence[0].title == "测试实施办法"

    disable_official(home=home)
    with pytest.raises(FileNotFoundError):
        resolve_active_database(home=home)
    assert (home / "releases" / release_v2 / "deeplaw.sqlite3").is_file()

    enable_official(home=home)
    assert resolve_active_database(home=home).parent.name == release_v2

    rewritten = json.loads(catalog_v2.read_text(encoding="utf-8"))
    rewritten["version"] = "rewritten"
    rewritten_path = tmp_path / "rewritten.json"
    rewritten_path.write_text(json.dumps(rewritten), encoding="utf-8")
    with pytest.raises(ValueError, match="rewritten"):
        sync_official(
            catalog_source=rewritten_path,
            source_root=source,
            update=True,
            home=home,
            allow_unsigned_local_catalog=True,
        )

    uninstalled = uninstall_official(home=home)
    assert set(uninstalled["removed_release_ids"]) == {release_v1, release_v2}
    assert not (home / "official").exists()
    with pytest.raises(FileNotFoundError):
        resolve_active_database(home=home)


def test_official_install_downloads_catalog_sources_and_verifies_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "downloaded.docx"
    write_docx(source, ["官方下载测试法", "第一条 下载内容必须通过大小和哈希校验。"])
    document = manifest_document(tmp_path, source.name, title="官方下载测试法")
    document["officialSource"] = "https://example.gov.cn/downloaded.docx"
    catalog_path = _write_catalog(tmp_path / "catalog.json", [document], sequence=1)
    catalog_payload = catalog_path.read_bytes()
    key_path = tmp_path / "signing" / "catalog-key.pem"
    trust_path = tmp_path / "trust.json"
    initialize_signing_key(key_path)
    export_trust_store(trust_path, key_path=key_path)
    signature_path = tmp_path / "catalog.json.sig"
    sign_catalog_file(catalog_path, signature_path=signature_path, key_path=key_path)
    signature_payload = signature_path.read_bytes()
    source_payload = source.read_bytes()
    calls: list[str] = []

    def fake_download(url: str, *, maximum: int, timeout: float = 60.0) -> bytes:
        calls.append(url)
        assert maximum > 0
        assert timeout > 0
        if url.endswith("catalog.json"):
            return catalog_payload
        if url.endswith("catalog.json.sig"):
            return signature_payload
        raise AssertionError(f"unexpected download URL: {url}")

    def fake_source_download(item: dict[str, object], destination: Path) -> None:
        calls.append(str(item["officialSource"]))
        assert item["byteSize"] == len(source_payload)
        destination.write_bytes(source_payload)

    monkeypatch.setattr(official_module, "_download_bytes", fake_download)
    monkeypatch.setattr(official_module, "_download_source", fake_source_download)
    home = tmp_path / "home"
    result = sync_official(
        catalog_source="https://catalog.example/catalog.json",
        home=home,
        trust_store_path=trust_path,
    )

    assert result["report"]["document_count"] == 1
    assert result["report"]["warning_count"] == 0
    assert "documents" not in result["report"]
    assert calls == [
        "https://catalog.example/catalog.json",
        "https://catalog.example/catalog.json.sig",
        "https://example.gov.cn/downloaded.docx",
    ]
    cached_sources = list((home / "official" / "sources").iterdir())
    assert len(cached_sources) == 1
    assert cached_sources[0].read_bytes() == source_payload


def test_official_source_download_streams_and_fails_closed_on_hash_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.docx"
    write_docx(source, ["流式下载测试法", "第一条 下载器不得把大型法源一次性装入内存。"])
    payload = source.read_bytes()
    document = manifest_document(tmp_path, source.name, title="流式下载测试法")
    document["officialSource"] = "https://example.gov.cn/source.docx"

    class FakeResponse:
        def __init__(self, value: bytes):
            self.value = value
            self.offset = 0

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def geturl(self) -> str:
            return str(document["officialSource"])

        def read(self, size: int) -> bytes:
            chunk = self.value[self.offset : self.offset + min(size, 17)]
            self.offset += len(chunk)
            return chunk

    def fake_urlopen(*_: object, **__: object) -> FakeResponse:
        return FakeResponse(payload)

    monkeypatch.setattr(official_module, "urlopen", fake_urlopen)
    destination = tmp_path / "cache" / "source.docx"
    destination.parent.mkdir()
    official_module._download_source(document, destination)
    assert destination.read_bytes() == payload

    destination.unlink()
    document["sha256"] = "0" * 64
    with pytest.raises(RuntimeError, match="SHA-256"):
        official_module._download_source(document, destination)
    assert not destination.exists()
    assert not list(destination.parent.glob(".*.tmp"))


def test_official_source_materialization_cleans_failed_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "path": "failed.docx",
        "officialSource": "https://example.gov.cn/failed.docx",
        "byteSize": 123,
        "sha256": "1" * 64,
    }
    catalog = {"documents": [document]}
    home = tmp_path / "home"
    monkeypatch.setattr(
        official_module,
        "_download_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("network failed")),
    )

    with pytest.raises(RuntimeError, match="source download failed"):
        official_module._materialize_downloaded_sources(catalog, home=home)

    official_root = home / "official"
    assert not list(official_root.glob(".official-sources-*"))


def test_national_laws_database_download_envelope_is_resolved_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "path": "中华人民共和国测试法.docx",
        "officialSource": (
            "https://flk.npc.gov.cn/law-search/download/pc?format=docx&bbbs=test"
        ),
    }
    signed_url = (
        "https://flkoss.obs-bj2.cucloud.cn/prod/test.docx?"
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=test"
    )
    observed: dict[str, object] = {}

    def fake_download(url: str, *, maximum: int, timeout: float = 60.0) -> bytes:
        observed.update(url=url, maximum=maximum, timeout=timeout)
        return json.dumps({"code": 200, "data": {"url": signed_url}}).encode()

    monkeypatch.setattr(official_module, "_download_bytes", fake_download)

    assert official_module._resolve_source_download_url(document) == signed_url
    assert observed == {
        "url": document["officialSource"],
        "maximum": 64 * 1024,
        "timeout": 60.0,
    }

    monkeypatch.setattr(
        official_module,
        "_download_bytes",
        lambda *_args, **_kwargs: json.dumps(
            {
                "code": 200,
                "data": {"url": "https://attacker.example/test.docx"},
            }
        ).encode(),
    )
    with pytest.raises(RuntimeError, match="unsafe"):
        official_module._resolve_source_download_url(document)


def test_official_download_retries_only_transient_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    delays: list[float] = []

    class Response:
        def close(self) -> None:
            return None

    def fake_urlopen(*_args: object, **_kwargs: object) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise HTTPError("https://example.gov.cn/source", 502, "bad gateway", {}, None)
        return Response()

    monkeypatch.setattr(official_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(official_module.time, "sleep", delays.append)
    response = official_module._urlopen_with_retry(
        official_module.Request("https://example.gov.cn/source"),
        timeout=1.0,
    )

    assert isinstance(response, Response)
    assert attempts == 3
    assert delays == [0.5, 1.0]


def test_private_library_is_physical_separate_explicit_and_deletable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    official_source = tmp_path / "official-source"
    official_document = official_source / "official.docx"
    write_docx(official_document, ["中华人民共和国官方测试法", "第一条 官方内容。"])
    official_manifest = write_manifest(
        official_source / "manifest.json",
        [
            manifest_document(
                official_source,
                official_document.name,
                title="中华人民共和国官方测试法",
            )
        ],
    )
    official_release, _ = build_release(
        source_root=official_source,
        manifest_path=official_manifest,
        output_root=home / "releases",
        activate=True,
    )
    official_hash = (official_release / "release.json").read_bytes()

    private_source = tmp_path / "user-upload.docx"
    write_docx(
        private_source,
        ["用户研究资料", "第一条 用户私有法律资料内容用于隔离检索验证。"],
    )
    with pytest.raises(ValueError, match="confirmation"):
        add_private_document(private_source, home=home)

    added = add_private_document(
        private_source,
        title="用户研究资料",
        confirm_no_case_data=True,
        home=home,
    )
    document_id = added["document"]["document_id"]
    private_database = resolve_private_database(home=home)
    root = private_home(home)

    assert private_database.parent.parent == root / "releases"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "library.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(private_database.stat().st_mode) == 0o400
    private_sources = list((root / "sources").iterdir())
    assert len(private_sources) == 1
    assert stat.S_IMODE(private_sources[0].stat().st_mode) == 0o600
    assert str(private_source) not in (root / "library.json").read_text(encoding="utf-8")
    assert (official_release / "release.json").read_bytes() == official_hash

    with DeepLaw(private_database, expected_scope="user_private") as law:
        response = law.search(SearchRequest(query="用户研究资料 第一条"))
        card = response.evidence[0]
        assert response.notices[0].startswith("当前结果来自用户私有资料库")
        assert card.authority_rank == 0
        assert card.official_source.startswith("private://source/")
    with pytest.raises(RuntimeError, match="expected official"):
        DeepLaw(private_database, expected_scope="official")

    mcp_result = handle_support(
        operation="private_search",
        query="用户研究资料 第一条",
        private_database=private_database,
    )
    assert mcp_result["release_id"] == private_database.parent.name
    assert mcp_result["evidence"][0]["title"] == "用户研究资料"
    assert list_private_documents(home=home)["document_count"] == 1

    deleted = delete_private_document(document_id, home=home)
    assert deleted["document_count"] == 0
    assert deleted["restart_required"] is True
    assert not (root / "ACTIVE").exists()
    assert not list((root / "sources").iterdir())
    assert not list((root / "releases").iterdir())
    with pytest.raises(FileNotFoundError):
        resolve_private_database(home=home)
    assert resolve_active_database(home=home) == official_release / "deeplaw.sqlite3"
    assert (official_release / "release.json").read_bytes() == official_hash


def test_official_update_and_uninstall_do_not_mutate_private_library(tmp_path: Path) -> None:
    home = tmp_path / "home"
    official_source = tmp_path / "official-source"
    first = official_source / "official-v1.docx"
    write_docx(first, ["官方第一版测试法", "第一条 官方第一版内容。"])
    first_documents = [manifest_document(official_source, first.name, title="官方第一版测试法")]
    catalog_v1 = _write_catalog(tmp_path / "catalog-v1.json", first_documents, sequence=1)
    sync_official(
        catalog_source=catalog_v1,
        source_root=official_source,
        home=home,
        allow_unsigned_local_catalog=True,
    )

    private_source = tmp_path / "private-reference.txt"
    private_source.write_text(
        "用户私有参考规范\n第一条 官方目录更新和卸载不得改动本资料。\n",
        encoding="utf-8",
    )
    add_private_document(
        private_source,
        title="用户私有参考规范",
        confirm_no_case_data=True,
        home=home,
    )
    private_root = private_home(home)
    private_database = resolve_private_database(home=home)
    state_before = (private_root / "library.json").read_bytes()
    database_before = private_database.read_bytes()

    second = official_source / "official-v2.docx"
    write_docx(second, ["官方第二版测试法", "第一条 官方目录新增内容。"])
    catalog_v2 = _write_catalog(
        tmp_path / "catalog-v2.json",
        [
            *first_documents,
            manifest_document(official_source, second.name, title="官方第二版测试法"),
        ],
        sequence=2,
    )
    sync_official(
        catalog_source=catalog_v2,
        source_root=official_source,
        update=True,
        home=home,
        allow_unsigned_local_catalog=True,
    )
    uninstall_official(home=home)

    assert (private_root / "library.json").read_bytes() == state_before
    assert private_database.read_bytes() == database_before
    assert resolve_private_database(home=home) == private_database
    with DeepLaw(private_database, expected_scope="user_private") as law:
        response = law.search(SearchRequest(query="用户私有参考规范"))
    assert response.evidence[0].title == "用户私有参考规范"


def test_private_txt_is_supported_but_legacy_doc_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    text_source = tmp_path / "reference.txt"
    text_source.write_text("用户法律参考资料\n第一条 UTF-8 文本资料可以检索。\n", encoding="utf-8")

    result = add_private_document(
        text_source,
        confirm_no_case_data=True,
        home=home,
    )
    with DeepLaw(resolve_private_database(home=home), expected_scope="user_private") as law:
        response = law.search(SearchRequest(query="UTF-8 文本资料"))
    assert result["document"]["format"] == "TXT"
    assert response.evidence

    legacy = tmp_path / "legacy.doc"
    legacy.write_bytes(b"not a supported legacy Word file")
    with pytest.raises(ValueError, match="legacy DOC"):
        add_private_document(legacy, confirm_no_case_data=True, home=home)


def test_mcp_private_surface_stays_read_only_and_explicit() -> None:
    tool = tool_definition()
    operations = {
        branch["properties"]["operation"].get("const", "search")
        for branch in tool.inputSchema["oneOf"]
    }

    assert {"private_search", "private_get", "private_verify", "private_info"} <= operations
    assert not {"add", "upload", "delete", "update", "remember"} & operations
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False


def test_mcp_rejects_private_reads_after_the_snapshot_is_deleted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = tmp_path / "private.docx"
    write_docx(source, ["私有删除测试资料", "第一条 删除后旧 MCP 进程必须拒绝继续读取。"])
    added = add_private_document(source, confirm_no_case_data=True, home=home)

    async def exercise() -> None:
        environment = {**os.environ, "DEEPLAW_HOME": str(home)}
        environment.pop("DEEPLAW_DB", None)
        environment.pop("DEEPLAW_PRIVATE_DB", None)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deeplaw", "mcp", "--stdio"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            before = await session.call_tool(
                "law_support",
                {"operation": "private_search", "query": "私有删除测试资料 第一条"},
            )
            assert before.isError is False

            delete_private_document(added["document"]["document_id"], home=home)
            after = await session.call_tool(
                "law_support",
                {"operation": "private_info"},
            )
            assert after.isError is True
            assert "restart" in str(after.content).lower()

    asyncio.run(exercise())


def test_mcp_rejects_official_reads_after_the_catalog_is_disabled(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source_root = tmp_path / "source"
    source = source_root / "official.docx"
    write_docx(source, ["官方停用测试法", "第一条 停用后旧 MCP 进程必须拒绝继续读取。"])
    catalog = _write_catalog(
        tmp_path / "catalog.json",
        [manifest_document(source_root, source.name, title="官方停用测试法")],
        sequence=1,
    )
    sync_official(
        catalog_source=catalog,
        source_root=source_root,
        home=home,
        allow_unsigned_local_catalog=True,
    )

    async def exercise() -> None:
        environment = {**os.environ, "DEEPLAW_HOME": str(home)}
        environment.pop("DEEPLAW_DB", None)
        environment.pop("DEEPLAW_PRIVATE_DB", None)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "deeplaw", "mcp", "--stdio"],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
        )
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            before = await session.call_tool(
                "law_support",
                {"operation": "release_info"},
            )
            assert before.isError is False

            disable_official(home=home)
            after = await session.call_tool(
                "law_support",
                {"operation": "release_info"},
            )
            assert after.isError is True
            assert "restart" in str(after.content).lower()

    asyncio.run(exercise())
