from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from deeplaw.query_plan import (
    MAX_OBLIGATIONS,
    MAX_QUERY_CHARS,
    MAX_QUERY_CUES_PER_OBLIGATION,
    ObligationId,
    ObligationRole,
    PlanPurpose,
    PlanRoute,
    QueryObligation,
    QueryPlan,
    compile_query_plan,
)


def _obligations(plan: QueryPlan) -> dict[ObligationId, QueryObligation]:
    return {obligation.id: obligation for obligation in plan.obligations}


def test_public_enum_values_are_stable() -> None:
    assert tuple(purpose.value for purpose in PlanPurpose) == (
        "auto",
        "exact_citation",
        "as_of_version",
        "elements",
        "legal_issue_screen",
        "citation_verify",
        "broad_topic",
    )
    assert tuple(route.value for route in PlanRoute) == ("exact", "navigation", "research")
    assert tuple(obligation.value for obligation in ObligationId) == (
        "exact_citation",
        "primary_rule",
        "temporal_status_version",
        "elements_definitions",
        "interpretation",
        "procedure",
        "threshold_standard",
        "exceptions_counterevidence",
        "case_reference",
    )


def test_exact_as_of_plan_is_closed_required_and_deterministic() -> None:
    plan = compile_query_plan(
        "中华人民共和国刑法 第二百六十六条在该时点是否有效",
        "exact_citation",
        "exact",
        "2020-01-01",
    )
    repeated = compile_query_plan(
        "  中华人民共和国刑法   第二百六十六条在该时点是否有效  ",
        PlanPurpose.EXACT_CITATION,
        PlanRoute.EXACT,
        "2020-01-01",
    )

    assert plan == repeated
    assert plan.plan_id == repeated.plan_id
    assert plan.plan_id == "lawplan_e56c7ffe80f63423ba58a0748c1f5b20"
    assert [obligation.id for obligation in plan.obligations] == [
        ObligationId.EXACT_CITATION,
        ObligationId.PRIMARY_RULE,
        ObligationId.TEMPORAL_STATUS_VERSION,
    ]
    obligations = _obligations(plan)
    assert obligations[ObligationId.EXACT_CITATION].role is ObligationRole.IDENTITY
    assert obligations[ObligationId.EXACT_CITATION].required is True
    assert obligations[ObligationId.TEMPORAL_STATUS_VERSION].role is ObligationRole.TEMPORAL
    assert obligations[ObligationId.TEMPORAL_STATUS_VERSION].query_cues[0] == "as_of"
    assert "text:是否有效" in obligations[ObligationId.TEMPORAL_STATUS_VERSION].query_cues

    payload = plan.to_dict()
    assert payload["plan_id"].startswith("lawplan_")
    assert payload["query"] == "中华人民共和国刑法 第二百六十六条在该时点是否有效"
    assert payload["purpose"] == "exact_citation"
    assert payload["route"] == "exact"
    assert payload["bounds"] == {
        "max_query_chars": MAX_QUERY_CHARS,
        "max_obligations": MAX_OBLIGATIONS,
        "max_query_cues_per_obligation": MAX_QUERY_CUES_PER_OBLIGATION,
    }


def test_chinese_cues_compile_every_substantive_obligation() -> None:
    plan = compile_query_plan(
        "该罪的构成要件和定义应如何理解，立案程序有何例外，相关指导性案例是什么?",
        "legal_issue_screen",
        "research",
    )
    obligations = _obligations(plan)

    assert tuple(obligations) == (
        ObligationId.PRIMARY_RULE,
        ObligationId.ELEMENTS_DEFINITIONS,
        ObligationId.INTERPRETATION,
        ObligationId.PROCEDURE,
        ObligationId.EXCEPTIONS_COUNTEREVIDENCE,
        ObligationId.CASE_REFERENCE,
    )
    assert all(obligation.required for obligation in obligations.values())
    assert obligations[ObligationId.ELEMENTS_DEFINITIONS].query_cues == (
        "purpose:legal_issue_screen",
        "text:构成要件",
        "text:要件",
        "text:定义",
    )
    assert obligations[ObligationId.EXCEPTIONS_COUNTEREVIDENCE].role is (
        ObligationRole.COUNTEREVIDENCE
    )
    assert "text:例外" in obligations[ObligationId.EXCEPTIONS_COUNTEREVIDENCE].query_cues
    assert obligations[ObligationId.CASE_REFERENCE].role is ObligationRole.REFERENCE


def test_broad_topic_keeps_counterevidence_optional() -> None:
    plan = compile_query_plan("诈骗", "broad_topic", "navigation")
    obligations = _obligations(plan)

    assert tuple(obligations) == (
        ObligationId.PRIMARY_RULE,
        ObligationId.EXCEPTIONS_COUNTEREVIDENCE,
    )
    assert obligations[ObligationId.PRIMARY_RULE].required is True
    assert obligations[ObligationId.EXCEPTIONS_COUNTEREVIDENCE].required is False
    assert obligations[ObligationId.EXCEPTIONS_COUNTEREVIDENCE].query_cues == (
        "purpose:broad_topic",
    )


def test_text_cues_work_without_special_purpose() -> None:
    plan = compile_query_plan(
        "现行规则何时生效，如何解释申请期限，是否另有规定，有无相关判决?",
        "auto",
        "navigation",
    )

    assert [obligation.id for obligation in plan.obligations] == [
        ObligationId.PRIMARY_RULE,
        ObligationId.TEMPORAL_STATUS_VERSION,
        ObligationId.INTERPRETATION,
        ObligationId.PROCEDURE,
        ObligationId.EXCEPTIONS_COUNTEREVIDENCE,
        ObligationId.CASE_REFERENCE,
    ]
    assert all(obligation.required for obligation in plan.obligations)


@pytest.mark.parametrize(
    ("query", "expected_cue"),
    [
        ("诈骗罪 数额标准", "text:数额标准"),
        ("诈骗罪 立案标准", "text:立案标准"),
    ],
)
def test_offence_threshold_questions_compile_a_distinct_required_obligation(
    query: str,
    expected_cue: str,
) -> None:
    plan = compile_query_plan(query, "auto", "research")
    obligations = _obligations(plan)

    assert obligations[ObligationId.THRESHOLD_STANDARD].required is True
    assert expected_cue in obligations[ObligationId.THRESHOLD_STANDARD].query_cues
    assert ObligationId.PROCEDURE not in obligations


@pytest.mark.parametrize(
    "query",
    [
        "旧金融办法是否被替代",
        "旧金融办法是否被取代",
        "旧金融办法是否停止执行",
        "旧金融办法是否仍然有效",
        "旧金融办法当前是否适用",
        "旧金融办法还适用吗",
        "旧金融办法现在能否适用",
        "旧金融办法是否继续适用",
        "旧金融办法还有效吗",
        "旧金融办法现在有效吗",
        "旧金融办法被新规替换",
        "旧金融办法是最新的吗",
        "旧金融办法现在还能用吗",
        "旧金融办法已经不再适用了",
        "旧金融办法已经作废了吗",
        "旧金融办法已被撤销",
        "旧金融办法已被废除",
        "旧金融办法已被新规取而代之",
        "旧金融办法已经终止执行",
    ],
)
def test_non_current_phrases_always_compile_temporal_obligation(query: str) -> None:
    plan = compile_query_plan(query, "auto", "research")

    assert ObligationId.TEMPORAL_STATUS_VERSION in _obligations(plan)


@pytest.mark.parametrize(
    "title_query",
    [
        "中华人民共和国刑法修正案（十二）",
        "中国人民银行关于修改和废止部分规章的决定（2025）",
    ],
)
def test_status_words_inside_document_titles_do_not_imply_temporal_question(
    title_query: str,
) -> None:
    plan = compile_query_plan(title_query, "auto", "research")

    assert ObligationId.TEMPORAL_STATUS_VERSION not in _obligations(plan)


def test_known_document_title_suppresses_incidental_intent_terms() -> None:
    plan = compile_query_plan(
        "国务院办公厅关于停止执行有关文件的通知",
        "auto",
        "exact",
        document_title_only=True,
    )

    assert tuple(_obligations(plan)) == (
        ObligationId.EXACT_CITATION,
        ObligationId.PRIMARY_RULE,
    )


def test_document_title_hint_must_be_boolean() -> None:
    with pytest.raises(ValueError, match="document_title_only must be a boolean"):
        compile_query_plan(  # type: ignore[arg-type]
            "测试通知",
            "auto",
            "exact",
            document_title_only="yes",
        )


@pytest.mark.parametrize("query", ["", " ", "\n\t"])
def test_empty_query_fails_closed(query: str) -> None:
    with pytest.raises(ValueError, match="query is required"):
        compile_query_plan(query, "auto", "research")


def test_query_length_boundary_is_bounded() -> None:
    accepted = compile_query_plan("法" * MAX_QUERY_CHARS, "auto", "navigation")

    assert len(accepted.to_dict()["query"]) == MAX_QUERY_CHARS
    assert len(accepted.obligations) <= MAX_OBLIGATIONS
    assert all(
        len(obligation.query_cues) <= MAX_QUERY_CUES_PER_OBLIGATION
        for obligation in accepted.obligations
    )
    with pytest.raises(ValueError, match="must not exceed"):
        compile_query_plan("法" * (MAX_QUERY_CHARS + 1), "auto", "navigation")


@pytest.mark.parametrize(
    ("purpose", "route", "message"),
    [
        ("unknown", "research", "unsupported query plan purpose"),
        ("auto", "unknown", "unsupported query plan route"),
    ],
)
def test_unknown_enum_values_fail_closed(purpose: str, route: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        compile_query_plan("测试规则", purpose, route)


@pytest.mark.parametrize("as_of", ["20200101", "2020-02-30", " 2020-01-01"])
def test_noncanonical_as_of_fails_closed(as_of: str) -> None:
    with pytest.raises(ValueError):
        compile_query_plan("测试规则", "as_of_version", "exact", as_of)


def test_plan_and_obligations_are_frozen() -> None:
    plan = compile_query_plan("测试规则", "auto", "research")

    with pytest.raises(FrozenInstanceError):
        plan.query = "changed"
    with pytest.raises(FrozenInstanceError):
        plan.obligations[0].required = False


def test_fullwidth_and_whitespace_normalization_is_deterministic() -> None:
    normalized = compile_query_plan("刑法 第266条", "auto", "exact")
    fullwidth = compile_query_plan("  刑法　　第２６６条  ", "auto", "exact")

    assert normalized == fullwidth
    assert normalized.to_dict() == fullwidth.to_dict()
