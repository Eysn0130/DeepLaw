from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from deeplaw.ingest import build_release
from deeplaw.models import SearchRequest
from deeplaw.review_overlay import apply_review_overlay
from deeplaw.search import DeepLaw
from tests.helpers import manifest_document, write_docx


def _fixture(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    root = tmp_path / "source"
    write_docx(
        root / "law.docx",
        ["测试法", "第一条 这是足够长的测试规则正文，用于验证复核覆盖与不可变发布。"],
    )
    source_document = manifest_document(root, "law.docx", title="测试法")
    manifest: dict[str, object] = {
        "package": {"name": "test"},
        "documents": [source_document],
    }
    review_document: dict[str, object] = {
        "sourceSha256": source_document["sha256"],
        "path": "law.docx",
        "title": "测试法（核对文本）",
        "documentNumber": "测试令[2026]1号",
        "aliases": ["测试法"],
        "promulgatedOn": "2026-01-01",
        "effectiveDate": "2026-02-01",
        "effectiveTo": None,
        "status": "unverified_current",
        "documentType": "law",
        "issuer": "测试机关",
        "authorityRank": 100,
        "canonicalAuthorityUrl": "https://example.gov.cn/law",
        "retrievalSourceUrl": "https://mirror.example.gov.cn/law.docx",
        "sourceReviewStatus": "ai_prechecked",
        "temporalReviewStatus": "ai_prechecked",
        "extractionReviewStatus": "unreviewed",
        "redistributionStatus": "restricted",
        "statusAsOf": "2026-07-15",
        "evidenceUrls": ["https://example.gov.cn/law"],
        "relations": [],
        "note": "仅完成 AI 预检。",
    }
    return manifest, review_document


def _write_overlay(tmp_path: Path, document: dict[str, object], **package: object) -> Path:
    payload = {
        "schemaVersion": "deeplaw.review-overlay/v1",
        "package": {
            "reviewedAsOf": "2026-07-15",
            "reviewerKind": "ai_precheck",
            "reviewScope": "metadata and temporal risk precheck",
            "temporalStatus": "partially_verified",
            "redistributionStatus": "restricted",
            **package,
        },
        "documents": [document],
    }
    path = tmp_path / "review.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_overlay_is_bound_to_path_and_hash_and_applies_narrowing_metadata(tmp_path: Path) -> None:
    manifest, review_document = _fixture(tmp_path)
    result = apply_review_overlay(manifest, _write_overlay(tmp_path, review_document))

    merged = result.manifest["documents"][0]
    assert merged["title"] == "测试法（核对文本）"
    assert merged["officialSource"] == "https://example.gov.cn/law"
    assert merged["retrievalSourceUrl"] == "https://mirror.example.gov.cn/law.docx"
    assert merged["status"] == "unverified_current"
    assert "effectiveTo" not in merged
    assert result.reviewer_kind == "ai_precheck"
    assert result.temporal_status == "partially_verified"
    assert result.covered_documents == 1
    assert len(result.overlay_sha256) == 64


def test_overlay_rejects_hash_path_confusion(tmp_path: Path) -> None:
    manifest, review_document = _fixture(tmp_path)
    review_document["path"] = "different.docx"
    with pytest.raises(ValueError, match="same source path and SHA-256"):
        apply_review_overlay(manifest, _write_overlay(tmp_path, review_document))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("aliases", ["重复别名", "重复别名"], "unique values"),
        (
            "evidenceUrls",
            ["https://example.gov.cn/law", "https://example.gov.cn/law"],
            "unique URLs",
        ),
        ("issuer", "机" * 201, "issuer"),
        ("path", "p" * 1025, "path"),
    ],
)
def test_overlay_runtime_enforces_public_schema_bounds(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    manifest, review_document = _fixture(tmp_path)
    review_document[field] = value

    with pytest.raises(ValueError, match=message):
        apply_review_overlay(manifest, _write_overlay(tmp_path, review_document))


def test_ai_overlay_cannot_claim_verified_or_redistribution_approval(tmp_path: Path) -> None:
    manifest, review_document = _fixture(tmp_path)
    with pytest.raises(ValueError, match="cannot mark temporal metadata verified"):
        apply_review_overlay(
            manifest,
            _write_overlay(tmp_path, review_document, temporalStatus="verified"),
        )
    with pytest.raises(ValueError, match="cannot approve redistribution"):
        apply_review_overlay(
            manifest,
            _write_overlay(tmp_path, review_document, redistributionStatus="approved"),
        )


def test_verified_temporal_overlay_requires_full_human_document_coverage(
    tmp_path: Path,
) -> None:
    manifest, review_document = _fixture(tmp_path)
    source = tmp_path / "source"
    write_docx(
        source / "second.docx",
        ["第二测试法", "第一条 第二份来源也必须由人工逐项核验。"],
    )
    manifest["documents"].append(
        manifest_document(source, "second.docx", title="第二测试法")
    )
    review_document.update(
        {
            "status": "verified_current",
            "temporalReviewStatus": "human_reviewed",
        }
    )

    with pytest.raises(ValueError, match="full source-manifest coverage"):
        apply_review_overlay(
            manifest,
            _write_overlay(
                tmp_path,
                review_document,
                reviewerKind="human",
                temporalStatus="verified",
            ),
        )


def test_verified_temporal_overlay_requires_human_status_and_complete_interval(
    tmp_path: Path,
) -> None:
    manifest, review_document = _fixture(tmp_path)
    review_document["status"] = "verified_current"

    with pytest.raises(ValueError, match="every document to be human-reviewed"):
        apply_review_overlay(
            manifest,
            _write_overlay(
                tmp_path,
                review_document,
                reviewerKind="human",
                temporalStatus="verified",
            ),
        )

    review_document["temporalReviewStatus"] = "human_reviewed"
    result = apply_review_overlay(
        manifest,
        _write_overlay(
            tmp_path,
            review_document,
            reviewerKind="human",
            temporalStatus="verified",
        ),
    )
    assert result.temporal_status == "verified"

    review_document["effectiveTo"] = "2026-03-01"
    with pytest.raises(ValueError, match="verified_current cannot end"):
        apply_review_overlay(
            manifest,
            _write_overlay(
                tmp_path,
                review_document,
                reviewerKind="human",
                temporalStatus="verified",
            ),
        )

    review_document.update(
        {
            "status": "repealed",
            "effectiveTo": None,
        }
    )
    with pytest.raises(ValueError, match="effectiveTo is required"):
        apply_review_overlay(
            manifest,
            _write_overlay(
                tmp_path,
                review_document,
                reviewerKind="human",
                temporalStatus="verified",
            ),
        )


def test_overlay_rejects_unbound_relation_target(tmp_path: Path) -> None:
    manifest, review_document = _fixture(tmp_path)
    review_document["relations"] = [
        {
            "predicate": "amends",
            "targetSourceSha256": "0" * 64,
            "reviewStatus": "ai_prechecked",
            "evidenceUrl": "https://example.gov.cn/amendment",
        }
    ]
    with pytest.raises(ValueError, match="not present in the source manifest"):
        apply_review_overlay(manifest, _write_overlay(tmp_path, review_document))


def test_ai_relation_proposal_cannot_create_runtime_edge_without_text_provenance(
    tmp_path: Path,
) -> None:
    manifest, review_document = _fixture(tmp_path)
    source = tmp_path / "source"
    write_docx(
        source / "target.docx",
        ["目标测试法", "第一条 本文件与前一文件正文没有相互引用。"],
    )
    target = manifest_document(source, "target.docx", title="目标测试法")
    manifest["documents"].append(target)
    review_document["relations"] = [
        {
            "predicate": "amends",
            "targetSourceSha256": target["sha256"],
            "reviewStatus": "ai_prechecked",
            "evidenceUrl": "https://example.gov.cn/proposed-relation",
        }
    ]
    manifest_path = source / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    release_dir, _ = build_release(
        source_root=source,
        manifest_path=manifest_path,
        review_overlay_path=_write_overlay(tmp_path, review_document),
        output_root=tmp_path / "releases",
    )
    connection = sqlite3.connect(release_dir / "deeplaw.sqlite3")
    try:
        edge_count = connection.execute("SELECT COUNT(*) FROM legal_edges").fetchone()[0]
    finally:
        connection.close()

    assert edge_count == 0


def test_fully_human_temporal_overlay_enables_implicit_reviewed_on_search(
    tmp_path: Path,
) -> None:
    manifest, review_document = _fixture(tmp_path)
    review_document.update(
        {
            "status": "verified_current",
            "temporalReviewStatus": "human_reviewed",
        }
    )
    source = tmp_path / "source"
    manifest_path = source / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    overlay_path = _write_overlay(
        tmp_path,
        review_document,
        reviewerKind="human",
        temporalStatus="verified",
    )
    release_dir, _ = build_release(
        source_root=source,
        manifest_path=manifest_path,
        review_overlay_path=overlay_path,
        output_root=tmp_path / "releases",
    )

    with DeepLaw(release_dir / "deeplaw.sqlite3") as law:
        response = law.search(SearchRequest(query="测试法现在有效吗"))

    assert response.evidence
    assert not response.uncertain_evidence
    assert {card.temporal_classification for card in response.evidence} == {
        "verified_in_scope"
    }
    assert response.query_plan["temporal_reference_date"] == "2026-07-15"
    assert response.query_plan["temporal_reference_source"] == "release_reviewed_on"
    assert not any(gap.code == "temporal_metadata_unverified" for gap in response.gaps)


def test_build_binds_review_overlay_and_reviewed_metadata(tmp_path: Path) -> None:
    manifest, review_document = _fixture(tmp_path)
    source = tmp_path / "source"
    manifest_path = source / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    overlay_path = _write_overlay(tmp_path, review_document)

    release_dir, report = build_release(
        source_root=source,
        manifest_path=manifest_path,
        review_overlay_path=overlay_path,
        output_root=tmp_path / "releases",
    )

    release = json.loads((release_dir / "release.json").read_text(encoding="utf-8"))
    assert release["review_overlay_schema"] == "deeplaw.review-overlay/v1"
    assert release["review_overlay_sha256"]
    assert release["reviewer_kind"] == "ai_precheck"
    assert release["review_covered_documents"] == 1
    assert release["temporal_status"] == "partially_verified"
    assert release["redistribution_status"] == "restricted"
    assert report.document_count == 1
    connection = sqlite3.connect(release_dir / "deeplaw.sqlite3")
    try:
        title, status = connection.execute("SELECT title, status FROM documents").fetchone()
    finally:
        connection.close()
    assert title == "测试法（核对文本）"
    assert status == "unverified_current"
