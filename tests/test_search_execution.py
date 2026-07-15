from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

import deeplaw.ingest as ingest_module
from deeplaw.evaluate import evaluate_file
from deeplaw.ingest import build_release
from deeplaw.models import ExtractionQuality, ExtractionResult, SearchRequest, TextBlock
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
    assert response.query_plan["vector_used"] is False
    assert response.query_plan["wiki_used"] is False
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
    assert report["schema_version"] == "deeplaw.eval-report/v2"
    assert report["overall_pass_rate"] == 1.0
    assert report["results"][0]["expected_bucket"] == "uncertain_evidence"
    assert report["results"][0]["evidence_count"] == 0
    assert report["results"][0]["uncertain_evidence_count"] == 1


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
        gap.obligation_id for gap in response.gaps if gap.code == "required_obligation_uncovered"
    } >= {"primary_rule", "elements_definitions", "exceptions_counterevidence"}
    _validate_search_response(response.to_dict())
