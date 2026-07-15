from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import deeplaw.ingest as ingest_module
from deeplaw.evaluate import evaluate_file
from deeplaw.extract import ExtractionError
from deeplaw.ingest import build_release
from deeplaw.models import SearchRequest
from deeplaw.search import DeepLaw
from deeplaw.store import connect_readonly, database_sha256, resolve_active_database

from .helpers import manifest_document, sha256, write_docx, write_manifest


def _build_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    source = tmp_path / "source"
    law = source / "中华人民共和国测试法.docx"
    later = source / "测试监督办法.docx"
    write_docx(
        law,
        [
            "中华人民共和国测试法",
            "第一章 总则",
            "第一条 为了规范测试活动，防止诈骗，制定本法。",
            "第二条 开展业务应当履行客户尽职调查义务。",
        ],
    )
    write_docx(
        later,
        ["测试监督办法", "第一条 本办法规定未来监督要求。"],
    )
    manifest = write_manifest(
        source / "manifest.json",
        [
            manifest_document(source, law.name, title="中华人民共和国测试法"),
            manifest_document(
                source,
                later.name,
                title="测试监督办法",
                effective_date="2030-01-01",
            ),
        ],
    )
    output = tmp_path / "var" / "releases"
    release_dir, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=output,
        activate=True,
    )
    return release_dir, manifest, output


def test_release_is_content_addressed_readonly_and_idempotent(tmp_path: Path) -> None:
    release_dir, manifest, output = _build_fixture(tmp_path)
    database = release_dir / "deeplaw.sqlite3"
    release = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
    original_hash = sha256(database)

    assert release_dir.name.startswith("lawrel_")
    assert release["release_id"] == release_dir.name
    assert release["derivation_sha256"]
    assert release["database_sha256"] == original_hash
    release_schema = json.loads(
        (
            Path(__file__).resolve().parents[1] / "contracts/corpus-release-manifest.v2.schema.json"
        ).read_text()
    )
    Draft202012Validator(release_schema).validate(release)
    assert not database.stat().st_mode & stat.S_IWUSR
    assert (output.parent / "ACTIVE").read_text(encoding="utf-8").strip() == release_dir.name

    rebuilt, report = build_release(
        source_root=manifest.parent,
        manifest_path=manifest,
        output_root=output,
    )
    assert rebuilt == release_dir
    assert report.release_id == release_dir.name
    assert sha256(database) == original_hash

    with DeepLaw(database) as law, pytest.raises(sqlite3.OperationalError):
        law.connection.execute("DELETE FROM segments")


def test_search_is_bounded_temporal_and_receipted(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)
    database = release_dir / "deeplaw.sqlite3"

    with DeepLaw(database) as law:
        exact = law.search(SearchRequest(query="中华人民共和国测试法 第一条", limit=5))
        broad = law.search(SearchRequest(query="诈骗", limit=5))
        future = law.search(SearchRequest(query="测试监督办法 第一条", as_of="2026-07-15", limit=5))
        missing = law.search(
            SearchRequest(
                query="中华人民共和国测试法 第九千九百九十九条",
                purpose="exact_citation",
            )
        )

        assert exact.mode == "exact"
        assert len(exact.evidence) == 1
        assert exact.evidence[0].title == "中华人民共和国测试法"
        assert exact.evidence[0].article_label == "第一条"
        assert broad.mode == "navigation"
        assert len(broad.evidence) <= 3
        assert len({card.document_id for card in broad.evidence}) == len(broad.evidence)
        assert broad.total_excerpt_chars <= 1200
        assert broad.next_questions
        assert not future.evidence
        assert not missing.evidence
        assert exact.evidence[0].temporal_review_required is True
        card = exact.evidence[0]
        assert law.verify(card.segment_id, card.receipt_id)["valid"] is True
        assert law.verify(card.segment_id, "lawrcpt_" + "0" * 32)["valid"] is False


def test_exact_partial_title_does_not_mix_unrelated_documents(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = source / "target.docx"
    distractor = source / "distractor.docx"
    write_docx(
        target,
        ["关于进一步规范涉案财物处置工作的意见", "第一条 规范涉案财物处置工作。"],
    )
    write_docx(
        distractor,
        ["经济犯罪办案规定", "第一条 办案机关应当规范涉案财物处置工作。"],
    )
    target_item = manifest_document(
        source,
        target.name,
        title="关于进一步规范涉案财物处置工作的意见",
    )
    target_item["aliases"] = ["涉案财物处置工作的意见"]
    manifest = write_manifest(
        source / "manifest.json",
        [target_item, manifest_document(source, distractor.name, title="经济犯罪办案规定")],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )

    with DeepLaw(release / "deeplaw.sqlite3") as law:
        response = law.search(
            SearchRequest(
                query="涉案财物处置工作的意见",
                purpose="exact_citation",
                limit=5,
            )
        )

    assert response.evidence
    assert {card.title for card in response.evidence} == {
        "关于进一步规范涉案财物处置工作的意见"
    }


def test_exact_unknown_title_with_valid_article_fails_closed(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)

    with DeepLaw(release_dir / "deeplaw.sqlite3") as law:
        response = law.search(
            SearchRequest(
                query="完全不存在的法律 第一条",
                purpose="exact_citation",
            )
        )

    assert response.mode == "exact"
    assert not response.evidence


@pytest.mark.parametrize(
    "query",
    [
        "中华人民共和国测试法实施条例 第一条",
        "中华人民共和国测试法配套实施条例 第一条",
        "中华人民共和国测试法2025年实施条例 第一条",
        "中华人民共和国测试法解释 第一条",
        "中华人民共和国测试法相关司法解释 第一条",
        "中华人民共和国测试法修正案 第一条",
        "某省中华人民共和国测试法 第一条",
        "关于贯彻中华人民共和国测试法 第一条",
        "最高人民法院关于中华人民共和国测试法 第一条",
    ],
)
def test_exact_known_title_prefix_does_not_capture_unknown_extended_title(
    tmp_path: Path,
    query: str,
) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)

    with DeepLaw(release_dir / "deeplaw.sqlite3") as law:
        response = law.search(SearchRequest(query=query, purpose="exact_citation"))

    assert not response.evidence


def test_exact_title_normalization_ignores_connectors_and_decision_suffix(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = source / "decision.docx"
    distractor = source / "rules.docx"
    write_docx(
        target,
        ["中国人民银行关于修改和废止部分规章的决定", "第一条 修改有关规章。"],
    )
    write_docx(distractor, ["客户尽职调查办法", "第一条 修改客户调查规则。"])
    manifest = write_manifest(
        source / "manifest.json",
        [
            manifest_document(
                source,
                target.name,
                title="中国人民银行关于修改和废止部分规章的决定",
            ),
            manifest_document(source, distractor.name, title="客户尽职调查办法"),
        ],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )

    with DeepLaw(release / "deeplaw.sqlite3") as law:
        response = law.search(
            SearchRequest(
                query="中国人民银行 修改和废止部分规章 2025",
                purpose="exact_citation",
            )
        )

    assert {card.title for card in response.evidence} == {
        "中国人民银行关于修改和废止部分规章的决定"
    }
    assert not response.uncertain_evidence
    assert "temporal_status_version" not in {
        item["id"] for item in response.query_plan["obligations"]
    }


def test_exact_base_law_is_not_replaced_by_its_amendment(tmp_path: Path) -> None:
    source = tmp_path / "source"
    base = source / "base.docx"
    amendment = source / "amendment.docx"
    implementation = source / "implementation.docx"
    write_docx(base, ["中华人民共和国测试法", "第一条 基础法律条文。"])
    write_docx(amendment, ["中华人民共和国测试法修正案", "第一条 修正案条文。"])
    write_docx(
        implementation,
        ["中华人民共和国测试法实施细则", "第一条 实施细则条文。"],
    )
    manifest = write_manifest(
        source / "manifest.json",
        [
            manifest_document(source, base.name, title="中华人民共和国测试法"),
            manifest_document(
                source,
                amendment.name,
                title="中华人民共和国测试法修正案",
            ),
            manifest_document(
                source,
                implementation.name,
                title="中华人民共和国测试法实施细则",
            ),
        ],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )

    with DeepLaw(release / "deeplaw.sqlite3") as law:
        response = law.search(
            SearchRequest(
                query="中华人民共和国测试法 第一条规定什么",
                purpose="exact_citation",
            )
        )

    assert {card.title for card in response.evidence} == {"中华人民共和国测试法"}


def test_database_hash_is_streamed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "release.sqlite3"
    database.write_bytes(b"streamed database hash")
    expected = sha256(database)

    def fail_read_bytes(_: Path) -> bytes:
        raise AssertionError("database hashing must not load the entire file into memory")

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    assert database_sha256(database) == expected


def test_runtime_rejects_a_release_manifest_hash_mismatch(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)
    manifest_path = release_dir / "release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["database_sha256"] = "0" * 64
    manifest_path.chmod(0o644)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="SHA-256"):
        DeepLaw(release_dir / "deeplaw.sqlite3")


def test_runtime_rejects_unbounded_or_unknown_release_manifest_fields(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)
    manifest_path = release_dir / "release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.chmod(0o644)
    manifest["unexpected"] = "value"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="closed v2 contract"):
        DeepLaw(release_dir / "deeplaw.sqlite3")

    manifest["unexpected"] = "X" * (64 * 1024)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="64 KiB"):
        DeepLaw(release_dir / "deeplaw.sqlite3")


def test_runtime_rejects_unsupported_storage_schema_and_metadata_drift(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)
    manifest_path = release_dir / "release.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.chmod(0o644)

    unsupported = dict(original)
    unsupported["storage_schema"] = "deeplaw.sqlite/v2"
    manifest_path.write_text(json.dumps(unsupported), encoding="utf-8")
    with pytest.raises(RuntimeError, match="storage schema"):
        DeepLaw(release_dir / "deeplaw.sqlite3")

    drifted = dict(original)
    drifted["document_count"] += 1
    manifest_path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(RuntimeError, match="database metadata"):
        DeepLaw(release_dir / "deeplaw.sqlite3")


def test_runtime_rejects_forged_verified_release_without_full_human_binding(
    tmp_path: Path,
) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)
    manifest_path = release_dir / "release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_path.chmod(0o644)
    manifest["temporal_status"] = "verified"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(RuntimeError, match="complete review-overlay binding"):
        DeepLaw(release_dir / "deeplaw.sqlite3")

    manifest.update(
        {
            "reviewed_on": "2026-07-15",
            "review_overlay_schema": "deeplaw.review-overlay/v1",
            "review_overlay_sha256": "a" * 64,
            "reviewer_kind": "ai_precheck",
            "review_scope": "forged review metadata",
            "review_covered_documents": manifest["document_count"],
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RuntimeError, match="full human temporal-review coverage"):
        DeepLaw(release_dir / "deeplaw.sqlite3")


def test_search_response_conforms_to_public_schema(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)
    repository = Path(__file__).resolve().parents[1]
    output_schema = json.loads(
        (repository / "contracts/law-search-response.v2.schema.json").read_text()
    )
    card_schema = json.loads(
        (repository / "contracts/legal-evidence-card.v2.schema.json").read_text()
    )
    registry = Registry().with_resource(card_schema["$id"], Resource.from_contents(card_schema))

    with DeepLaw(release_dir / "deeplaw.sqlite3") as law:
        response = law.search(SearchRequest(query="客户尽职调查", limit=2)).to_dict()

    Draft202012Validator(output_schema, registry=registry).validate(response)


def test_reviewed_metadata_and_aliases_are_searchable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(
        document,
        ["中华人民共和国测试法", "第一条 为了验证文号和别名检索，制定本法。"],
    )
    item = manifest_document(source, document.name, title="中华人民共和国测试法")
    item.update(
        {
            "documentNumber": "国测令第1号",
            "aliases": ["测试基本法"],
            "promulgatedOn": "2019-12-01",
            "jurisdiction": "CN",
            "documentType": "law",
            "issuer": "测试发布机关",
            "authorityRank": 10,
        }
    )
    distractor = source / "other.docx"
    write_docx(
        distractor,
        ["中华人民共和国其他测试法", "第一条 本条不得覆盖精确文号和别名目标。"],
    )
    distractor_item = manifest_document(
        source,
        distractor.name,
        title="中华人民共和国其他测试法",
    )
    distractor_item["authorityRank"] = 100
    manifest = write_manifest(source / "manifest.json", [item, distractor_item])
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )

    with DeepLaw(release / "deeplaw.sqlite3") as law:
        response = law.search(SearchRequest(query="国测令第1号 第一条", purpose="exact_citation"))
        alias_response = law.search(
            SearchRequest(query="测试基本法 第一条", purpose="exact_citation")
        )

    card = response.evidence[0]
    assert card.title == "中华人民共和国测试法"
    assert card.document_number == "国测令第1号"
    assert card.promulgated_on == "2019-12-01"
    assert card.issuer == "测试发布机关"
    assert {item.title for item in response.evidence} == {"中华人民共和国测试法"}
    assert {item.title for item in alias_response.evidence} == {"中华人民共和国测试法"}


def test_manifest_hash_mismatch_fails_before_publish(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, ["测试法", "第一条 测试。"])
    item = manifest_document(source, document.name, title="测试法")
    item["sha256"] = "0" * 64
    manifest = write_manifest(source / "manifest.json", [item])

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )


def test_manifest_symbolic_link_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    actual = source / "actual.docx"
    link = source / "law.docx"
    write_docx(actual, ["测试法", "第一条 为了测试符号链接拒绝规则，制定本条。"])
    link.symlink_to(actual)
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, link.name, title="测试法")],
    )

    with pytest.raises(ValueError, match="symbolic link"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )


@pytest.mark.parametrize("invalid_date", ["20200101", "2020-W01-1", "2020-02-30"])
def test_manifest_rejects_noncanonical_dates(tmp_path: Path, invalid_date: str) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, ["测试法", "第一条 日期必须使用规范格式。"])
    item = manifest_document(source, document.name, title="测试法")
    item["effectiveDate"] = invalid_date
    manifest = write_manifest(source / "manifest.json", [item])

    with pytest.raises(ValueError, match="effectiveDate"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )


def test_search_rejects_noncanonical_as_of(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)

    with DeepLaw(release_dir / "deeplaw.sqlite3") as law, pytest.raises(
        ValueError, match="canonical"
    ):
        law.search(SearchRequest(query="测试法", as_of="20200101"))


def test_verified_status_requires_an_effective_date(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, ["测试法", "第一条 状态不得绕过时效复核。"])
    item = manifest_document(
        source,
        document.name,
        title="测试法",
        effective_date=None,
        status="verified_current",
    )
    manifest = write_manifest(source / "manifest.json", [item])

    with pytest.raises(ValueError, match="verified status requires effectiveDate"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )


def test_manifest_rejects_source_url_credentials_and_oversized_title(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, ["测试法", "第一条 限制证据卡元数据大小。"])
    item = manifest_document(source, document.name, title="测试法")
    item["officialSource"] = "https://user:secret@example.gov.cn/law"
    manifest = write_manifest(source / "manifest.json", [item])

    with pytest.raises(ValueError, match="officialSource"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )

    item["officialSource"] = "https://example.gov.cn/law"
    item["title"] = "法" * 501
    manifest = write_manifest(source / "manifest.json", [item])
    with pytest.raises(ValueError, match="title exceeds"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var2" / "releases",
        )


def test_source_change_during_extraction_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, ["测试法", "第一条 原始内容足够长以通过文档提取质量门禁。"])
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="测试法")],
    )
    original_extract = ingest_module.extract_document

    def replace_after_extract(
        path: Path,
        format_name: str,
        *,
        pdf_fallback: str = "off",
        reviewed_pages_path: Path | None = None,
    ):
        result = original_extract(
            path,
            format_name,
            pdf_fallback=pdf_fallback,
            reviewed_pages_path=reviewed_pages_path,
        )
        path.write_bytes(path.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(ingest_module, "extract_document", replace_after_extract)

    with pytest.raises(RuntimeError, match="source changed"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )


def test_rebuild_rejects_coordinated_existing_artifact_tampering(tmp_path: Path) -> None:
    release_dir, manifest, output = _build_fixture(tmp_path)
    database = release_dir / "deeplaw.sqlite3"
    release_path = release_dir / "release.json"
    database.chmod(0o644)
    database.write_bytes(database.read_bytes() + b"tampered")
    release_path.chmod(0o644)
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["database_sha256"] = database_sha256(database)
    release_path.write_text(json.dumps(release), encoding="utf-8")

    with pytest.raises(RuntimeError, match="existing immutable release failed verification"):
        build_release(
            source_root=manifest.parent,
            manifest_path=manifest,
            output_root=output,
        )


def test_rebuild_rejects_manifest_only_approval_tampering(tmp_path: Path) -> None:
    release_dir, manifest, output = _build_fixture(tmp_path)
    release_path = release_dir / "release.json"
    release_path.chmod(0o644)
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["temporal_status"] = "verified"
    release["redistribution_status"] = "approved"
    release_path.write_text(json.dumps(release), encoding="utf-8")

    with pytest.raises(RuntimeError, match="complete review-overlay binding"):
        build_release(
            source_root=manifest.parent,
            manifest_path=manifest,
            output_root=output,
        )


def test_rebuild_rejects_build_report_tampering(tmp_path: Path) -> None:
    release_dir, manifest, output = _build_fixture(tmp_path)
    report_path = release_dir / "build-report.json"
    report_path.chmod(0o644)
    report_path.write_bytes(report_path.read_bytes() + b"\n")

    with pytest.raises(RuntimeError, match="existing immutable release failed verification"):
        build_release(
            source_root=manifest.parent,
            manifest_path=manifest,
            output_root=output,
        )


def test_readonly_sqlite_uri_preserves_reserved_path_characters(tmp_path: Path) -> None:
    verified = tmp_path / "release#verified.sqlite3"
    decoy = tmp_path / "release"
    for path, marker in ((verified, "verified"), (decoy, "decoy")):
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES (?)", (marker,))
        connection.commit()
        connection.close()

    connection = connect_readonly(verified)
    try:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == "verified"
    finally:
        connection.close()


def test_active_release_rejects_symlinked_releases_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    release_id = "lawrel_" + "1" * 32
    (outside / release_id).mkdir(parents=True)
    home.mkdir()
    (home / "ACTIVE").write_text(release_id, encoding="utf-8")
    (home / "releases").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="releases directory"):
        resolve_active_database(home=home)


def test_scanned_pdf_requires_an_explicit_fallback(tmp_path: Path) -> None:
    from reportlab.pdfgen import canvas

    source = tmp_path / "source"
    source.mkdir()
    document = source / "scan.pdf"
    pdf = canvas.Canvas(str(document))
    pdf.showPage()
    pdf.save()
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="扫描法源")],
    )

    with pytest.raises(ExtractionError, match="quality gate failed"):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )


@pytest.mark.parametrize(
    ("paragraphs", "message"),
    [
        (["测试法", "第一章" + "章" * 500], "segment heading exceeds"),
        (["测试法", "第" + "一" * 100 + "条 超长条号。"], "article label exceeds"),
    ],
)
def test_build_rejects_segment_locators_outside_public_contract(
    tmp_path: Path,
    paragraphs: list[str],
    message: str,
) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, paragraphs)
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="测试法")],
    )

    with pytest.raises(ExtractionError, match=message):
        build_release(
            source_root=source,
            manifest_path=manifest,
            output_root=tmp_path / "var" / "releases",
        )


def test_navigation_locator_is_included_in_the_total_character_budget(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    heading = "第一章" + "章" * 480 + "诈骗"
    write_docx(document, ["测试法", heading, "本章用于验证导航预算。"])
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="测试法")],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )

    with DeepLaw(release / "deeplaw.sqlite3") as law:
        response = law.search(SearchRequest(query="诈骗", max_chars=500))

    assert response.evidence
    assert response.total_excerpt_chars <= 500
    assert all(len(card.excerpt) <= 320 for card in response.evidence)


def test_evaluation_checks_retrieval_and_noise_constraints(tmp_path: Path) -> None:
    release_dir, _, _ = _build_fixture(tmp_path)
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "exact",
                        "query": "中华人民共和国测试法 第一条",
                        "expected_titles": ["中华人民共和国测试法"],
                        "expected_articles": ["第一条"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "id": "future",
                        "query": "测试监督办法",
                        "purpose": "as_of_version",
                        "as_of": "2026-07-15",
                        "forbidden_titles": ["测试监督办法"],
                        "expected_empty": True,
                        "max_evidence": 3,
                    },
                    ensure_ascii=False,
                ),
            ]
        ),
        encoding="utf-8",
    )

    report = evaluate_file(release_dir / "deeplaw.sqlite3", cases)

    assert report["retrieval_pass_rate"] == 1.0
    assert report["constraint_pass_rate"] == 1.0
    assert report["overall_pass_rate"] == 1.0
    assert report["receipt_verification_pass_rate"] == 1.0
    assert report["receipt_count"] == report["verified_receipt_count"]
    assert report["receipt_count"] > 0
    assert all(item["receipt_verification_passed"] for item in report["results"])
    assert report["results"][1]["evidence_count"] == 0
