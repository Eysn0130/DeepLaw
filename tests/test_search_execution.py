from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import deeplaw.ingest as ingest_module
import deeplaw.search as search_module
from deeplaw.evaluate import evaluate_file
from deeplaw.ingest import build_release
from deeplaw.legal_topics import LegalTopicAnchor, LegalTopicLocator
from deeplaw.models import (
    ExtractionQuality,
    ExtractionResult,
    PageExtractionEvidence,
    SearchRequest,
    TextBlock,
)
from deeplaw.search import DeepLaw

from .helpers import manifest_document, write_docx, write_manifest


def _build(
    tmp_path: Path,
    documents: list[tuple[str, list[str], dict[str, object]]],
) -> Path:
    source = tmp_path / "source"
    manifest_items: list[dict[str, object]] = []
    for filename, paragraphs, metadata in documents:
        path = source / filename
        write_docx(path, paragraphs)
        effective_date = metadata.get("effective_date", "2020-01-01")
        if effective_date is not None and not isinstance(effective_date, str):
            raise TypeError("test effective_date must be a string or null")
        item = manifest_document(
            source,
            filename,
            title=str(metadata["title"]),
            effective_date=effective_date,
            status=str(metadata.get("status", "verified_current")),
        )
        item.update(
            {
                key: value
                for key, value in metadata.items()
                if key not in {"title", "effective_date", "status"}
            }
        )
        manifest_items.append(item)
    manifest = write_manifest(source / "manifest.json", manifest_items)
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "var" / "releases",
    )
    return release / "deeplaw.sqlite3"


def _validate_search_response(response: dict[str, object]) -> None:
    repository = Path(__file__).resolve().parents[1]
    card_schema = json.loads(
        (repository / "contracts/legal-evidence-card.v2.schema.json").read_text()
    )
    response_schema = json.loads(
        (repository / "contracts/law-search-response.v2.schema.json").read_text()
    )
    registry = Registry().with_resource(
        card_schema["$id"],
        Resource.from_contents(card_schema),
    )
    Draft202012Validator(response_schema, registry=registry).validate(response)
    returned_cards = [*response["evidence"], *response["uncertain_evidence"]]
    returned_segment_ids = {card["segment_id"] for card in returned_cards}
    returned_path_ids = {path["path_id"] for path in response["graph_paths"]}
    for coverage in response["obligation_coverage"]:
        assert set(coverage["evidence_segment_ids"]) <= returned_segment_ids
        assert set(coverage["graph_path_ids"]) <= returned_path_ids
    assert response["total_excerpt_chars"] == sum(
        len(card["excerpt"]) for card in returned_cards
    )
    assert response["total_excerpt_chars"] <= response["query_plan"]["max_chars"]


def test_search_uses_bounded_provenance_graph_for_obligation_coverage(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "old.docx",
                ["旧金融办法", "第一条 金融机构应当履行客户识别义务。"],
                {"title": "旧金融办法", "documentType": "departmental_rule"},
            ),
            (
                "decision.docx",
                [
                    "关于修改和废止部分规章的决定",
                    "第一条 自本决定施行之日起废止《旧金融办法》。",
                ],
                {
                    "title": "关于修改和废止部分规章的决定",
                    "documentType": "normative_document",
                },
            ),
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query="旧金融办法有哪些例外", limit=5))
        assert response.evidence
        assert response.graph_paths
        assert len(response.graph_paths) <= 4
        path = next(path for path in response.graph_paths if path.predicate == "repeals")
        assert path.authority == "derived_navigation"
        assert path.hops == 1
        assert law.verify(path.provenance_segment_id, path.provenance_receipt_id)["valid"]
        as_of_response = law.search(
            SearchRequest(query="旧金融办法有哪些例外", as_of="2026-07-15", limit=5)
        )

    counter_coverage = next(
        item
        for item in response.obligation_coverage
        if item.obligation_id == "exceptions_counterevidence"
    )
    assert counter_coverage.status == "uncertain"
    assert path.path_id in counter_coverage.graph_path_ids
    assert any(
        gap.blocking
        and gap.code == "required_obligation_uncertain"
        and gap.obligation_id == "exceptions_counterevidence"
        for gap in response.gaps
    )
    assert response.query_plan["graph_used"] is True
    assert "legal_graph" in response.query_plan["channels"]
    assert len(response.evidence) + len(response.uncertain_evidence) <= 5
    assert response.total_excerpt_chars == sum(
        len(card.excerpt) for card in (*response.evidence, *response.uncertain_evidence)
    )
    assert not as_of_response.evidence
    assert as_of_response.uncertain_evidence
    assert not as_of_response.graph_paths
    _validate_search_response(response.to_dict())


def test_as_of_separates_missing_or_unverified_temporal_metadata(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "unknown.docx",
                ["未验证测试法", "第一条 未验证的时效元数据不得进入主证据。"],
                {
                    "title": "未验证测试法",
                    "effective_date": None,
                    "status": "unverified_current",
                },
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(
            SearchRequest(
                query="未验证测试法 第一条",
                purpose="as_of_version",
                as_of="2026-07-15",
            )
        )

    assert not response.evidence
    assert response.uncertain_evidence
    assert {
        card.temporal_classification for card in response.uncertain_evidence
    } == {"unverified_metadata"}
    assert {
        gap.code for gap in response.gaps
    } >= {"temporal_metadata_unverified", "no_primary_evidence"}
    assert any(
        item.status == "uncertain"
        for item in response.obligation_coverage
        if item.obligation_id == "temporal_status_version"
    )
    assert len(response.evidence) + len(response.uncertain_evidence) <= 5
    assert response.total_excerpt_chars <= 6000
    _validate_search_response(response.to_dict())


def test_extraction_review_required_is_separated_from_primary_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    document = source / "review-required.docx"
    safe_document = source / "safe-distractor.docx"
    write_docx(document, ["抽取复核测试法", "第一条 未完成人工对照的文本不得进入主证据。"])
    write_docx(
        safe_document,
        ["普通测试办法", "第一条 人工对照的文本属于普通测试内容。"],
    )
    manifest = write_manifest(
        source / "manifest.json",
        [
            manifest_document(source, document.name, title="抽取复核测试法"),
            manifest_document(source, safe_document.name, title="普通测试办法"),
        ],
    )

    original_extract_document = ingest_module.extract_document

    def extract_with_risk(path: Path, *args: object, **kwargs: object) -> ExtractionResult:
        if path.name != document.name:
            return original_extract_document(path, *args, **kwargs)
        return ExtractionResult(
            blocks=(
                TextBlock(text="抽取复核测试法", paragraph=1),
                TextBlock(text="第一条 未完成人工对照的文本不得进入主证据。", paragraph=2),
            ),
            quality=ExtractionQuality(
                extractor="test-ocr",
                extractor_version="test-ocr/v1",
                block_count=2,
                page_count=1,
                character_count=30,
                needs_ocr=True,
                review_required=True,
                warnings=("page 1: test review required",),
            ),
        )

    monkeypatch.setattr(
        ingest_module,
        "extract_document",
        extract_with_risk,
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "releases",
        allow_needs_ocr=True,
    )

    with DeepLaw(release / "deeplaw.sqlite3") as law:
        response = law.search(
            SearchRequest(query="未完成人工对照的文本", limit=1)
        )

    assert not response.evidence
    assert response.uncertain_evidence
    assert response.uncertain_evidence[0].title == "抽取复核测试法"
    assert all(card.extraction_review_required for card in response.uncertain_evidence)
    assert any("抽取风险" in notice for notice in response.notices)
    assert any(
        item.status == "uncertain" for item in response.obligation_coverage if item.required
    )
    _validate_search_response(response.to_dict())

    cases = tmp_path / "review-required-cases.jsonl"
    cases.write_text(
        json.dumps(
            {
                "id": "review-required",
                "query": "未完成人工对照的文本",
                "expected_titles": ["抽取复核测试法"],
                "expected_bucket": "uncertain_evidence",
                "expected_extraction_review_required": True,
                "max_evidence": 1,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    report = evaluate_file(release / "deeplaw.sqlite3", cases, limit=1)
    assert report["schema_version"] == "deeplaw.eval-report/v3"
    assert report["overall_pass_rate"] == 1.0
    assert report["results"][0]["expected_bucket"] == "uncertain_evidence"
    assert report["results"][0]["evidence_count"] == 0
    assert report["results"][0]["uncertain_evidence_count"] == 1
    assert report["results"][0]["blocking_gap_count"] >= 1
    assert report["results"][0]["serialized_response_chars"] > report["results"][0]["excerpt_chars"]


def test_page_risk_quarantines_only_segments_from_that_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    document = source / "page-scoped.docx"
    write_docx(document, ["逐页测试法", "第一条 安全文本。", "第二条 待复核文本。"])
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="逐页测试法")],
    )

    def extract_with_page_risk(*_args: object, **_kwargs: object) -> ExtractionResult:
        safe_hash = "1" * 64
        risky_hash = "2" * 64
        return ExtractionResult(
            blocks=(
                TextBlock(text="逐页测试法", page=1, source="native"),
                TextBlock(text="第一条 安全文本。", page=1, source="native"),
                TextBlock(text="第二条 待复核文本。", page=2, source="ocr"),
            ),
            quality=ExtractionQuality(
                extractor="test-page-consensus",
                extractor_version="v1",
                block_count=3,
                page_count=2,
                character_count=32,
                review_required=True,
                page_evidence=(
                    PageExtractionEvidence(
                        page=1,
                        image_sha256=safe_hash,
                        native_text_sha256=safe_hash,
                        ocr_text_sha256=None,
                        selected_text_sha256=safe_hash,
                        native_character_count=16,
                        ocr_character_count=0,
                        selected_character_count=16,
                        selected_source="native",
                        review_required=False,
                        risk_flags=(),
                    ),
                    PageExtractionEvidence(
                        page=2,
                        image_sha256=risky_hash,
                        native_text_sha256=risky_hash,
                        ocr_text_sha256=risky_hash,
                        selected_text_sha256=risky_hash,
                        native_character_count=0,
                        ocr_character_count=16,
                        selected_character_count=16,
                        selected_source="ocr",
                        review_required=True,
                        risk_flags=("ocr_requires_review",),
                    ),
                ),
            ),
        )

    monkeypatch.setattr(ingest_module, "extract_document", extract_with_page_risk)
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "releases",
    )

    with DeepLaw(release / "deeplaw.sqlite3") as law:
        safe = law.search(SearchRequest(query="逐页测试法 第一条", limit=1))
        risky = law.search(SearchRequest(query="逐页测试法 第二条", limit=1))

    assert safe.evidence and not safe.uncertain_evidence
    assert safe.evidence[0].article_label == "第一条"
    assert not safe.evidence[0].extraction_review_required
    assert not risky.evidence and risky.uncertain_evidence
    assert risky.uncertain_evidence[0].article_label == "第二条"
    assert risky.uncertain_evidence[0].extraction_warnings == ("ocr_requires_review",)


def test_as_of_excludes_known_future_interval_and_reports_gap(tmp_path: Path) -> None:
    database = _build(
        tmp_path,
        [
            (
                "future.docx",
                ["未来测试法", "第一条 本法尚未到达施行日期。"],
                {
                    "title": "未来测试法",
                    "effective_date": "2030-01-01",
                    "status": "verified_current",
                },
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(
            SearchRequest(
                query="未来测试法 第一条",
                purpose="as_of_version",
                as_of="2026-07-15",
            )
        )

    assert not response.evidence
    assert not response.uncertain_evidence
    temporal_gap = next(gap for gap in response.gaps if gap.code == "temporal_out_of_scope")
    assert temporal_gap.candidate_count >= 1
    assert temporal_gap.blocking is False
    assert response.query_plan["as_of"] == "2026-07-15"
    _validate_search_response(response.to_dict())


def test_temporal_intent_without_as_of_excludes_known_non_current_status(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "old.docx",
                ["旧测试法", "第一条 本文件已经被后续规则替代。"],
                {
                    "title": "旧测试法",
                    "effective_date": "2020-01-01",
                    "effectiveTo": "2024-01-01",
                    "status": "superseded",
                },
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query="旧测试法是否现行"))

    assert not response.evidence
    assert not response.uncertain_evidence
    temporal_gap = next(gap for gap in response.gaps if gap.code == "temporal_out_of_scope")
    assert temporal_gap.blocking is False
    assert temporal_gap.candidate_count >= 1
    assert any(gap.blocking for gap in response.gaps if gap.code == "no_primary_evidence")
    assert next(
        item
        for item in response.obligation_coverage
        if item.obligation_id == "temporal_status_version"
    ).status == "gap"
    assert not response.graph_paths
    assert response.query_plan["temporal_reference_date"] is None
    assert response.query_plan["temporal_reference_source"] == "release_review_unavailable"
    assert any("缺少 reviewed_on" in notice for notice in response.notices)
    _validate_search_response(response.to_dict())


def test_explicit_document_name_does_not_return_unrelated_temporal_noise(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "old.docx",
                [
                    "旧测试管理办法",
                    "第一条 本办法已经废止，仅用于验证时效过滤和精确文件聚焦。",
                ],
                {
                    "title": "旧测试管理办法",
                    "effective_date": "2020-01-01",
                    "effectiveTo": "2024-01-01",
                    "status": "repealed",
                },
            ),
            (
                "distractor.docx",
                ["其他测试管理办法", "第一条 其他管理办法的现行适用规则。"],
                {
                    "title": "其他测试管理办法",
                    "effective_date": "2024-01-01",
                    "status": "unverified_current",
                },
            ),
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query="旧测试管理办法是否现行"))

    assert not response.evidence
    assert not response.uncertain_evidence
    assert any(gap.code == "temporal_out_of_scope" for gap in response.gaps)
    _validate_search_response(response.to_dict())


def test_complete_title_with_status_words_is_not_treated_as_status_question(
    tmp_path: Path,
) -> None:
    title = "国务院办公厅关于停止执行有关文件的通知"
    database = _build(
        tmp_path,
        [
            (
                "notice.docx",
                [title, "第一条 本通知用于核验完整文件题名的精确检索。"],
                {
                    "title": title,
                    "status": "unverified_current",
                },
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query=title))

    obligation_ids = {item["id"] for item in response.query_plan["obligations"]}
    assert obligation_ids == {"exact_citation", "primary_rule"}
    assert response.evidence
    assert not response.uncertain_evidence
    assert {card.title for card in response.evidence} == {title}
    assert not any(gap.code == "temporal_metadata_unverified" for gap in response.gaps)
    _validate_search_response(response.to_dict())


def test_applicability_interpretation_short_name_beats_base_law(tmp_path: Path) -> None:
    database = _build(
        tmp_path,
        [
            (
                "law.docx",
                ["中华人民共和国测试程序法", "第一条 本法规定证据处理规则。"],
                {"title": "中华人民共和国测试程序法"},
            ),
            (
                "interpretation.docx",
                ["测试程序法解释", "第一条 涉案财物应当依法处理。"],
                {
                    "title": "最高人民法院关于适用《中华人民共和国测试程序法》的解释",
                    "documentType": "judicial_interpretation",
                },
            ),
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query="最高人民法院 测试程序法解释 涉案财物"))

    assert response.evidence
    assert {card.title for card in response.evidence} == {
        "最高人民法院关于适用《中华人民共和国测试程序法》的解释"
    }
    _validate_search_response(response.to_dict())


@pytest.mark.parametrize("query", ["旧测试法还适用吗", "旧测试法被新规替换"])
def test_common_temporal_phrases_cannot_bypass_non_current_bucket(
    tmp_path: Path,
    query: str,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "old.docx",
                ["旧测试法", "第一条 本文件仅用于验证常见时效问法不会绕过版本门禁。"],
                {
                    "title": "旧测试法",
                    "effective_date": "2020-01-01",
                    "effectiveTo": "2024-01-01",
                    "status": "superseded",
                },
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query=query))

    assert not response.evidence
    assert not response.uncertain_evidence
    assert any(gap.code == "temporal_out_of_scope" for gap in response.gaps)
    _validate_search_response(response.to_dict())


def test_temporal_intent_without_as_of_isolates_unverified_current_candidate(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "current.docx",
                ["测试法", "第一条 未经完整时效复核的材料不得进入主证据。"],
                {
                    "title": "测试法",
                    "effective_date": "2020-01-01",
                    "status": "unverified_current",
                },
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query="测试法是否现行"))

    assert not response.evidence
    assert response.uncertain_evidence
    assert {
        card.temporal_classification for card in response.uncertain_evidence
    } == {"unverified_metadata"}
    temporal_gap = next(
        gap for gap in response.gaps if gap.code == "temporal_metadata_unverified"
    )
    assert temporal_gap.blocking is True
    assert next(
        item
        for item in response.obligation_coverage
        if item.obligation_id == "temporal_status_version"
    ).status == "uncertain"
    assert not response.graph_paths
    _validate_search_response(response.to_dict())


def test_temporal_intent_does_not_use_non_current_graph_target_as_coverage(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "old.docx",
                ["旧金融办法", "第一条 本办法规定一项足够长且可稳定提取的旧有监管义务。"],
                {
                    "title": "旧金融办法",
                    "effective_date": "2020-01-01",
                    "effectiveTo": "2024-01-01",
                    "status": "superseded",
                },
            ),
            (
                "current.docx",
                ["现行监管规则", "第一条 本规则替代《旧金融办法》。"],
                {
                    "title": "现行监管规则",
                    "effective_date": "2024-01-01",
                    "status": "verified_current",
                },
            ),
        ],
    )

    with DeepLaw(database) as law:
        law.temporal_metadata_verified = True
        law.temporal_reviewed_on = "2026-07-15"
        response = law.search(SearchRequest(query="现行监管规则是否有效，有哪些例外"))

    assert response.evidence
    assert all(path.target_title != "旧金融办法" for path in response.graph_paths)
    counter_coverage = next(
        item
        for item in response.obligation_coverage
        if item.obligation_id == "exceptions_counterevidence"
    )
    assert counter_coverage.status == "gap"
    assert any(
        gap.blocking
        and gap.code == "required_obligation_uncovered"
        and gap.obligation_id == "exceptions_counterevidence"
        for gap in response.gaps
    )
    _validate_search_response(response.to_dict())


@pytest.mark.parametrize(
    ("query", "purpose", "obligation_id"),
    [
        ("测试法的构成要件是什么", "elements", "elements_definitions"),
        ("测试法的办理程序和期限是什么", "auto", "procedure"),
    ],
)
def test_obligation_coverage_requires_matching_evidence_signals(
    tmp_path: Path,
    query: str,
    purpose: str,
    obligation_id: str,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "law.docx",
                ["测试法", "第一条 本条只规定一般义务，不提供所询问的专门内容。"],
                {"title": "测试法"},
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query=query, purpose=purpose))

    assert response.evidence
    assert next(
        item for item in response.obligation_coverage if item.obligation_id == obligation_id
    ).status == "gap"
    assert any(
        gap.blocking
        and gap.code == "required_obligation_uncovered"
        and gap.obligation_id == obligation_id
        for gap in response.gaps
    )
    _validate_search_response(response.to_dict())


def test_case_reference_cannot_substitute_for_primary_rule(tmp_path: Path) -> None:
    database = _build(
        tmp_path,
        [
            (
                "case.docx",
                ["测试案例", "裁判摘要仅描述个案事实，不是规范性法律依据。"],
                {
                    "title": "测试案例",
                    "documentType": "case_reference",
                },
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(SearchRequest(query="测试案例有哪些法律依据和类似案例"))

    assert response.evidence
    coverage = {item.obligation_id: item for item in response.obligation_coverage}
    assert coverage["case_reference"].status == "covered"
    assert coverage["primary_rule"].status == "gap"
    assert any(
        gap.blocking
        and gap.code == "required_obligation_uncovered"
        and gap.obligation_id == "primary_rule"
        for gap in response.gaps
    )
    _validate_search_response(response.to_dict())


def test_cross_reference_to_missing_content_cannot_fake_complete_coverage(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "law.docx",
                [
                    "测试法",
                    "第一条 构成要件另有规定，具体内容见尚未收录的附件，"
                    "本文件不载明任何成立条件。",
                ],
                {"title": "测试法"},
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(
            SearchRequest(query="测试法的构成要件和例外是什么", purpose="elements")
        )

    coverage = {item.obligation_id: item.status for item in response.obligation_coverage}
    assert coverage["primary_rule"] == "gap"
    assert coverage["elements_definitions"] == "gap"
    assert coverage["exceptions_counterevidence"] == "gap"
    assert any(gap.blocking for gap in response.gaps)
    assert {
        gap.obligation_id
        for gap in response.gaps
        if gap.code == "required_obligation_uncovered"
    } >= {"primary_rule", "elements_definitions", "exceptions_counterevidence"}
    _validate_search_response(response.to_dict())


def test_source_bound_fraud_topic_rejects_neighboring_offences_and_standards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noise_articles = [
        (
            f"第{index}条 诈骗行为构成要件研究中的数额标准和立案标准材料，"
            "仅用于形成高词面相关性的非目标候选。"
        )
        for index in range(1, 111)
    ]
    criminal_law_title = "中华人民共和国刑法（2020年修正）"
    database = _build(
        tmp_path,
        [
            (
                "criminal-law.docx",
                [
                    criminal_law_title,
                    "第一百九十三条 有下列情形之一，以非法占有为目的，"
                    "诈骗银行或者其他金融机构的贷款，数额较大的，构成贷款诈骗罪。",
                    "第二百六十六条 诈骗公私财物，数额较大的，处三年以下有期徒刑、"
                    "拘役或者管制，并处或者单处罚金；本法另有规定的，依照规定。",
                ],
                {"title": criminal_law_title, "documentType": "law"},
            ),
            (
                "aml.docx",
                [
                    "中华人民共和国反洗钱法",
                    "第二条 本法所称反洗钱，是指预防通过各种方式掩饰、隐瞒诈骗违法"
                    "所得及其收益来源和性质的活动。",
                ],
                {"title": "中华人民共和国反洗钱法", "documentType": "law"},
            ),
            (
                "loan-fraud-case.docx",
                [
                    "贷款诈骗案例",
                    "裁判要旨：行为人骗取银行贷款一百万元，认定贷款诈骗罪。",
                ],
                {"title": "贷款诈骗案例", "documentType": "case_reference"},
            ),
            (
                "securities-standard.docx",
                [
                    "证券违法立案追诉标准",
                    "第一条 欺诈发行证券造成投资者损失金额五十万元以上的，应予立案追诉。",
                ],
                {
                    "title": "证券违法立案追诉标准",
                    "documentType": "judicial_interpretation",
                },
            ),
            (
                "lexical-noise.docx",
                ["非目标主题研究资料", *noise_articles],
                {"title": "非目标主题研究资料", "documentType": "normative_document"},
            ),
        ],
    )

    with DeepLaw(database) as law:
        source_sha256 = law.connection.execute(
            "SELECT source_sha256 FROM documents WHERE title = ?",
            (criminal_law_title,),
        ).fetchone()["source_sha256"]
        fraud_anchor = LegalTopicAnchor(
            canonical_term="诈骗罪",
            query_aliases=("诈骗",),
            document_title=criminal_law_title,
            source_sha256=source_sha256,
            article_label="第二百六十六条",
        )
        checked_in_resolver = search_module.resolve_legal_topic

        def resolve_fixture_topic(topic: str) -> LegalTopicAnchor | None:
            if topic in fraud_anchor.query_terms:
                return fraud_anchor
            return checked_in_resolver(topic)

        monkeypatch.setattr(search_module, "resolve_legal_topic", resolve_fixture_topic)

        ordinary_candidates = law._candidate_rows(
            SearchRequest(query="诈骗罪构成要件").normalized(),
            "research",
        )
        assert len(ordinary_candidates) == 100
        assert not any(
            row["title"] == criminal_law_title
            and row["article_label"] == "第二百六十六条"
            for row in ordinary_candidates
        )

        elements = law.search(SearchRequest(query="诈骗罪构成要件"))
        screened_alias = law.search(
            SearchRequest(query="诈骗", purpose="legal_issue_screen")
        )
        ambiguous = law.search(SearchRequest(query="诈骗犯罪构成要件"))
        amount = law.search(SearchRequest(query="诈骗罪 数额标准"))
        filing = law.search(SearchRequest(query="诈骗罪 立案标准"))

    assert elements.evidence
    assert {card.title for card in elements.evidence} == {criminal_law_title}
    assert {card.article_label for card in elements.evidence} == {"第二百六十六条"}
    assert all(
        "洗钱" not in card.title and "贷款诈骗" not in card.title
        for card in elements.evidence
    )
    assert not any(gap.code == "query_focus_unresolved" for gap in elements.gaps)

    assert [(card.title, card.article_label) for card in screened_alias.evidence] == [
        (criminal_law_title, "第二百六十六条")
    ]

    assert not ambiguous.evidence
    assert not ambiguous.uncertain_evidence
    assert any(
        gap.blocking
        and gap.code == "query_focus_unresolved"
        and gap.obligation_id == "query_focus"
        for gap in ambiguous.gaps
    )

    for response in (amount, filing):
        assert response.evidence
        assert {card.title for card in response.evidence} == {criminal_law_title}
        assert {card.article_label for card in response.evidence} == {"第二百六十六条"}
        threshold_coverage = next(
            item
            for item in response.obligation_coverage
            if item.obligation_id == "threshold_standard"
        )
        assert threshold_coverage.required is True
        assert threshold_coverage.status == "gap"
        assert any(
            gap.blocking
            and gap.code == "required_obligation_uncovered"
            and gap.obligation_id == "threshold_standard"
            for gap in response.gaps
        )
        assert not any(
            "贷款诈骗" in card.title or "证券" in card.title
            for card in (*response.evidence, *response.uncertain_evidence)
        )

    for response in (elements, screened_alias, ambiguous, amount, filing):
        _validate_search_response(response.to_dict())


def test_primary_and_supporting_topic_locators_keep_distinct_evidence_roles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    criminal_law_title = "中华人民共和国刑法（2020年修正）"
    standard_title = "公安机关管辖的刑事案件立案追诉标准"
    database = _build(
        tmp_path,
        [
            (
                "criminal-law.docx",
                [
                    criminal_law_title,
                    "第一百九十三条 以非法占有为目的，诈骗银行或者其他金融机构的贷款，"
                    "数额较大的，构成贷款诈骗罪并依法追究刑事责任。",
                ],
                {"title": criminal_law_title, "documentType": "law"},
            ),
            (
                "prosecution-standard.docx",
                [
                    standard_title,
                    "第四十五条（贷款诈骗案）数额在五万元以上的，应予立 案追诉。",
                ],
                {"title": standard_title, "documentType": "prosecution_standard"},
            ),
        ],
    )

    with DeepLaw(database) as law:
        hashes = {
            row["title"]: row["source_sha256"]
            for row in law.connection.execute(
                "SELECT title, source_sha256 FROM documents"
            ).fetchall()
        }
        anchor = LegalTopicAnchor(
            canonical_term="贷款诈骗罪",
            query_aliases=(),
            document_title=criminal_law_title,
            source_sha256=hashes[criminal_law_title],
            article_label="第一百九十三条",
            supporting_locators=(
                LegalTopicLocator(
                    document_title=standard_title,
                    source_sha256=hashes[standard_title],
                    article_label="第四十五条",
                ),
            ),
        )
        monkeypatch.setattr(
            search_module,
            "resolve_legal_topic",
            lambda topic: anchor if topic == "贷款诈骗罪" else None,
        )

        bare = law.search(SearchRequest(query="贷款诈骗罪"))
        screened_bare = law.search(
            SearchRequest(query="贷款诈骗罪", purpose="legal_issue_screen")
        )
        elements = law.search(SearchRequest(query="贷款诈骗罪 构成要件"))
        filing = law.search(SearchRequest(query="贷款诈骗罪 立案标准"))
        filtered = law.search(
            SearchRequest(
                query="贷款诈骗罪 构成要件",
                document_types=("case_reference",),
            )
        )

    assert [(card.title, card.article_label) for card in bare.evidence] == [
        (criminal_law_title, "第一百九十三条")
    ]
    assert [(card.title, card.article_label) for card in screened_bare.evidence] == [
        (criminal_law_title, "第一百九十三条")
    ]
    assert [(card.title, card.article_label) for card in elements.evidence] == [
        (criminal_law_title, "第一百九十三条")
    ]
    assert [(card.title, card.article_label) for card in filing.evidence] == [
        (criminal_law_title, "第一百九十三条"),
        (standard_title, "第四十五条"),
    ]
    filing_coverage = {item.obligation_id: item for item in filing.obligation_coverage}
    assert filing_coverage["primary_rule"].evidence_segment_ids == (
        filing.evidence[0].segment_id,
    )
    assert filing_coverage["threshold_standard"].status == "covered"
    assert filing_coverage["threshold_standard"].evidence_segment_ids == (
        filing.evidence[1].segment_id,
    )
    assert not filtered.evidence
    assert not filtered.uncertain_evidence
    assert any(
        gap.code == "query_focus_unresolved" and gap.blocking for gap in filtered.gaps
    )

    for response in (bare, screened_bare, elements, filing, filtered):
        _validate_search_response(response.to_dict())


def test_navigation_uses_a_substantive_title_anchor_before_higher_authority_noise(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "target.docx",
                [
                    "中华人民共和国反电信网络诈骗法",
                    "第一章 总则",
                    "第一条 为了治理电信网络诈骗活动，制定本法。",
                ],
                {
                    "title": "中华人民共和国反电信网络诈骗法",
                    "authorityRank": 70,
                },
            ),
            (
                "noise.docx",
                [
                    "支付管理条例",
                    "第五条 支付机构应当防范电信网络诈骗活动。",
                ],
                {"title": "支付管理条例", "authorityRank": 100},
            ),
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(
            SearchRequest(query="电信网络诈骗", purpose="broad_topic", limit=3)
        )

    assert response.mode == "navigation"
    assert response.evidence[0].title == "中华人民共和国反电信网络诈骗法"
    assert response.evidence[0].article_label == "第一条"
    assert response.evidence_compilation["duty_witnesses"][0] == {
        "duty_id": "query_focus",
        "role": "identity",
        "required": True,
        "status": "covered",
        "candidate_id": response.evidence[0].segment_id,
    }
    _validate_search_response(response.to_dict())


def test_research_focus_is_scored_against_body_after_removing_document_title(
    tmp_path: Path,
) -> None:
    database = _build(
        tmp_path,
        [
            (
                "fx.docx",
                [
                    "中华人民共和国外汇管理条例",
                    "第三十九条 有逃汇行为的，应当依法处理。",
                    "第五十二条 本条例下列用语的含义包括境内机构和境内个人。",
                ],
                {"title": "中华人民共和国外汇管理条例"},
            )
        ],
    )

    with DeepLaw(database) as law:
        response = law.search(
            SearchRequest(query="外汇管理条例 逃汇", purpose="legal_issue_screen")
        )

    assert response.evidence[0].title == "中华人民共和国外汇管理条例"
    assert response.evidence[0].article_label == "第三十九条"
    assert all(card.article_label != "第五十二条" for card in response.evidence)
    _validate_search_response(response.to_dict())
