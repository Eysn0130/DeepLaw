"""Public-seam metamorphic checks for Query v6 expression identity stability.

This is tuning-used development material.  The source fixture is built through the
repository-visible Source -> SemanticCompilationService path; reads then cross the
public Python, CLI, and MCP context boundaries.  It is not Gold, holdout, or claim
qualification evidence.
"""

from __future__ import annotations

from typing import Any

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.util import canonical_json, query_identity_anchor_match, query_target_anchors
from tests.test_v013_query_v6_unseen_development import _build_case, _run_cli

_BASE_TASK = "Please summarize Policy Alpha requirements"
_ALPHA_MARKERS = (
    "ALPHA_BOTH",
    "ALPHA_EXCEPTION",
    "ALPHA_CONTRADICTION",
)
_ALPHA_KEYS = frozenset(
    f"claim:unseen-query-v6:{marker.lower()}" for marker in _ALPHA_MARKERS
)
_FALSE_TARGET_MARKERS = frozenset(
    {
        "ALPHABET_SUBSTRING",
        "BETA_ONLY",
        "TRANSLATED_DISTRACTOR",
        "ALIAS_BETA",
        "HOMONYM_PLANET",
    }
)
_OTHER_MARKERS = frozenset({"ALIAS_ALPHA", "ALPHA_MULTILINGUAL"})

# These labels stand in for the human-labelled target set used by the
# metamorphic check.  Optional labels are intentionally kept separate from
# required labels: a query may discover an alias without making it a required
# Policy Alpha answer, but it must not lose a required identity.
_HUMAN_LABELS: tuple[tuple[str, str], ...] = (
    ("ALPHA_BOTH", "required"),
    ("ALPHA_EXCEPTION", "required"),
    ("ALPHA_CONTRADICTION", "required"),
    ("ALPHA_MULTILINGUAL", "required"),
    ("ALIAS_ALPHA", "optional"),
    ("ALIAS_BETA", "optional"),
)
_HUMAN_REQUIRED_KEYS = frozenset(
    f"claim:unseen-query-v6:{marker.lower()}"
    for marker, requirement in _HUMAN_LABELS
    if requirement == "required"
)
_HUMAN_OPTIONAL_KEYS = frozenset(
    f"claim:unseen-query-v6:{marker.lower()}"
    for marker, requirement in _HUMAN_LABELS
    if requirement == "optional"
)

_ALPHA_SOURCE = "\n".join(
    [
        "# Alpha both",
        "ALPHA_BOTH: Policy Alpha and Policy Beta are both mentioned. "
        "Policy Alpha requires 30 days for the Alpha archive, while Policy Beta "
        "requires 60 days for the Beta archive.",
        "",
        "# Alpha exception",
        "ALPHA_EXCEPTION: Policy Alpha does not require 30 days for temporary drafts; "
        "this exception overrides the general Alpha archive rule.",
        "",
        "# Alpha contradiction",
        "ALPHA_CONTRADICTION: Policy Alpha requires 30 days in the general archive; "
        "the temporary-draft exception says Alpha does not require 30 days there.",
        "",
        "# Alpha multilingual proper noun",
        "ALPHA_MULTILINGUAL: 阿尔法政策 governs the 星河项目 (Xinghe Project), "
        "a multilingual proper noun used for the Alpha archive.",
        "",
        "# Alpha alias collision",
        "ALIAS_ALPHA: Alpha Archive registers a governed proper-name alias for the Alpha record.",
        "",
        "# Beta alias collision",
        "ALIAS_BETA: Beta Archive registers the same governed proper-name alias for a separate "
        "Beta record.",
        "",
        "# Beta-only target",
        "BETA_ONLY: Policy Beta alone requires 60 days for Beta-only records.",
        "",
        "# Alphabet substring distractor",
        "ALPHABET_SUBSTRING: Alphabetical indexing is unrelated metadata and contains no "
        "retention rule.",
        "",
        "# Irrelevant translated-keyword distractor",
        "TRANSLATED_DISTRACTOR: 无关 Gamma 只讨论 unrelated translation-keyword material.",
        "",
        "# Unrelated homonym",
        "HOMONYM_PLANET: Mercury is a planet and an unrelated astronomy reference with no "
        "policy requirement.",
    ]
) + "\n"


@pytest.fixture(scope="module")
def alpha_case(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    case = _build_case(
        tmp_path_factory.mktemp("query-v6-expression-invariance"),
        source_text=_ALPHA_SOURCE,
    )
    # The fixture helper uses the public source/review and semantic compilation
    # path.  Re-read its run through the public service as a guard against a
    # test that accidentally bypasses SemanticCompilationService.
    status = SemanticCompilationService(case["root"]).status(case["compilation_run_id"])
    assert status["verification"]["valid"] is True
    return case


def _statement_rows(payload: dict[str, Any]) -> set[tuple[str, str, str]]:
    rows: set[tuple[str, str, str]] = set()
    for statement in payload.get("statements", []):
        summary = statement.get("object_summary")
        if not isinstance(summary, dict):
            continue
        semantic_key = summary.get("semantic_key")
        knowledge_id = statement.get("knowledge_id")
        revision_id = statement.get("knowledge_revision_id")
        if all(isinstance(value, str) for value in (semantic_key, knowledge_id, revision_id)):
            rows.add((semantic_key, knowledge_id, revision_id))
    return rows


def _alpha_rows(payload: dict[str, Any]) -> set[tuple[str, str, str]]:
    return {row for row in _statement_rows(payload) if row[0] in _ALPHA_KEYS}


def _human_rows(
    payload: dict[str, Any],
    *,
    required: bool,
) -> set[tuple[str, str, str]]:
    keys = _HUMAN_REQUIRED_KEYS if required else _HUMAN_OPTIONAL_KEYS
    return {row for row in _statement_rows(payload) if row[0] in keys}


def _semantic_keys(rows: set[tuple[str, str, str]]) -> set[str]:
    return {semantic_key for semantic_key, _knowledge_id, _revision_id in rows}


def _labels(payload: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for statement in payload.get("statements", []):
        summary = statement.get("object_summary")
        title = summary.get("title", "") if isinstance(summary, dict) else ""
        text = f"{title} {statement.get('statement_text', '')}"
        labels.update(
            marker
            for marker in (*_ALPHA_MARKERS, *_FALSE_TARGET_MARKERS, *_OTHER_MARKERS)
            if marker in text
        )
    return labels


def _context_triplet(
    case: dict[str, Any],
    task: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    root = case["root"]
    with KnowledgeOS.open(root) as knowledge_os:
        python_context = knowledge_os.context.compile(
            task=task,
            purpose="verify",
            scope="project",
            max_sensitivity="public",
            limit=13,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
            confirm_no_case_data=True,
        )
    cli_context = _run_cli(
        case["cli_home"],
        "context",
        "--vault",
        str(root),
        "--task",
        task,
        "--purpose",
        "verify",
        "--max-items",
        "13",
        "--max-chars",
        "8000",
        "--max-tokens",
        "6000",
        "--max-sources",
        "12",
        "--confirm-no-case-data",
    )
    mcp_context = handle_knowledge_support(
        operation="context",
        task=task,
        purpose="verify",
        limit=13,
        max_chars=8_000,
        max_tokens=6_000,
        max_sources=12,
        scope="project",
        max_sensitivity="public",
        confirm_no_case_data=True,
        vault_path=root,
    )
    return python_context, cli_context, mcp_context["result"]["capsule"]


def test_v6_case_punctuation_quotes_and_cjk_preserve_required_identities(
    alpha_case: dict[str, Any],
) -> None:
    baseline = _context_triplet(alpha_case, _BASE_TASK)
    expected = _alpha_rows(baseline[0])
    assert len(expected) == len(_ALPHA_KEYS)
    assert not _labels(baseline[0]) & _FALSE_TARGET_MARKERS
    with KnowledgeOS.open(alpha_case["root"]) as knowledge_os:
        title_case_query = knowledge_os.retrieval.query(
            "Please Summarize Policy Alpha Requirements",
            purpose="verify",
            scope="project",
            max_sensitivity="public",
            limit=13,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
            projection="audit",
        )
    assert not any(
        item.get("reason") == "identity_anchor_mismatch"
        for item in title_case_query["local_audit"]["rejections"]
    )
    assert not any(
        item.get("reason") == "target_relevance"
        for item in title_case_query["local_audit"]["suppressions"]
    )

    variants = (
        "please summarize policy alpha requirements",
        "Please Summarize Policy Alpha Requirements",
        "Please summarize Policy Alpha requirements!",
        'Please summarize "Policy Alpha" requirements.',
        "请总结 Policy Alpha 的要求",
    )
    for task in variants:
        for seam, payload in zip(
            ("python", "cli", "mcp"),
            _context_triplet(alpha_case, task),
            strict=True,
        ):
            # Compare stable semantic/knowledge/revision identities only.  Receipts,
            # capsule digests, ordering, and byte payloads are invocation artifacts.
            assert _alpha_rows(payload) == expected, (seam, task, _labels(payload))
            assert not _labels(payload) & _FALSE_TARGET_MARKERS, (seam, task)
            if "budget" in payload:
                assert payload["budget"]["provider_payload_bytes"] <= 65_536
            else:
                assert payload["hard_limit_bytes"] == 65_536
                assert len(canonical_json(payload).encode("utf-8")) <= 65_536


def test_v6_human_required_identity_set_is_expression_invariant_across_seams(
    alpha_case: dict[str, Any],
) -> None:
    baseline_triplet = _context_triplet(alpha_case, _BASE_TASK)
    expected_required = _human_rows(baseline_triplet[0], required=True)
    assert _semantic_keys(expected_required) == _HUMAN_REQUIRED_KEYS
    assert _human_rows(baseline_triplet[0], required=False).isdisjoint(expected_required)

    variants = (
        "please summarize policy alpha requirements",
        "Please summarize POLICY ALPHA obligations",
        "Please summarize Policy Alpha requirements!",
        'Please summarize "Policy Alpha" obligations.',
        "请总结“Policy Alpha”的义务",
        "请总结 Policy Alpha 的要求",
    )
    for task in variants:
        for seam, payload in zip(
            ("python", "cli", "mcp"),
            _context_triplet(alpha_case, task),
            strict=True,
        ):
            observed_required = _human_rows(payload, required=True)
            missing_required = expected_required - observed_required
            extra_required = observed_required - expected_required
            assert not missing_required and not extra_required, (
                f"{seam} {task!r} drifted human-required identities; "
                f"missing_required={sorted(_semantic_keys(missing_required))}; "
                f"extra_required={sorted(_semantic_keys(extra_required))}; "
                f"observed_labels={sorted(_labels(payload))}"
            )


def test_v6_inferred_anchor_is_ranking_only_for_related_nonmatching_candidate(
    alpha_case: dict[str, Any],
) -> None:
    task = "Please summarize Policy Alpha and 星河项目 requirements"
    with KnowledgeOS.open(alpha_case["root"]) as knowledge_os:
        query = knowledge_os.retrieval.query(
            task,
            purpose="verify",
            scope="project",
            max_sensitivity="public",
            limit=13,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
            projection="audit",
        )
    multilingual = next(
        statement
        for statement in query["statements"]
        if "ALPHA_MULTILINGUAL" in statement["statement_text"]
    )
    surface = " ".join(
        (
            multilingual["object_summary"]["title"],
            multilingual["statement_text"],
        )
    )
    assert not any(
        query_identity_anchor_match(anchor, surface)
        for anchor in query_target_anchors(task)[0]
    )
    assert not any(
        item.get("reason") == "target_relevance"
        for item in query["local_audit"]["suppressions"]
    )


def test_v6_single_token_cjk_anchor_preserves_related_statement(
    alpha_case: dict[str, Any],
) -> None:
    for payload in _context_triplet(alpha_case, "Alpha 指什么？"):  # noqa: RUF001
        labels = _labels(payload)
        assert "ALPHA_BOTH" in labels
        assert _alpha_rows(payload)
        assert not any(gap.get("code") == "no_answer" for gap in payload.get("gaps", []))


def test_v6_multitarget_exception_alias_and_opaque_unknown_metamorphisms(
    alpha_case: dict[str, Any],
) -> None:
    multi_target, _cli_multi, _mcp_multi = _context_triplet(
        alpha_case,
        "Please summarize Policy Alpha and Policy Beta requirements",
    )
    multi_labels = _labels(multi_target)
    assert set(_ALPHA_MARKERS) <= multi_labels
    assert "BETA_ONLY" in multi_labels
    assert not multi_labels & {"ALPHABET_SUBSTRING", "TRANSLATED_DISTRACTOR", "HOMONYM_PLANET"}

    exception, _cli_exception, _mcp_exception = _context_triplet(
        alpha_case,
        "What does Policy Alpha not require for temporary drafts?",
    )
    exception_labels = _labels(exception)
    assert {"ALPHA_EXCEPTION", "ALPHA_CONTRADICTION"} <= exception_labels

    alias, _cli_alias, _mcp_alias = _context_triplet(alpha_case, "North Star")
    assert _labels(alias) == {"ALIAS_ALPHA", "ALIAS_BETA"}

    multilingual, _cli_multilingual, _mcp_multilingual = _context_triplet(
        alpha_case,
        "请总结 Policy Alpha 的 星河项目 (Xinghe Project) 要求",
    )
    assert "ALPHA_MULTILINGUAL" in _labels(multilingual)
    assert set(_ALPHA_MARKERS) <= _labels(multilingual)

    unknown, _cli_unknown, _mcp_unknown = _context_triplet(alpha_case, "opaque unknown")
    assert _statement_rows(unknown) == set()
    assert any(gap.get("code") == "duty_unresolved" for gap in unknown.get("gaps", []))


def test_v6_explicit_semantic_knowledge_and_revision_targets_remain_strict(
    alpha_case: dict[str, Any],
) -> None:
    baseline, _cli_baseline, _mcp_baseline = _context_triplet(alpha_case, _BASE_TASK)
    target = next(
        statement
        for statement in baseline["statements"]
        if statement["object_summary"]["semantic_key"]
        == "claim:unseen-query-v6:alpha_both"
    )
    target_identity = (
        target["object_summary"]["semantic_key"],
        target["knowledge_id"],
        target["knowledge_revision_id"],
    )
    targets = (
        {"semantic_key": target_identity[0], "text": "Policy Alpha"},
        {"knowledge_id": target_identity[1], "text": "Policy Alpha"},
        {"revision_id": target_identity[2], "text": "Policy Alpha"},
    )
    for query_target in targets:
        with KnowledgeOS.open(alpha_case["root"]) as knowledge_os:
            context = knowledge_os.context.compile(
                task=_BASE_TASK,
                purpose="verify",
                scope="project",
                max_sensitivity="public",
                limit=13,
                max_chars=8_000,
                max_tokens=6_000,
                max_sources=12,
                query_target=query_target,
                confirm_no_case_data=True,
            )
        assert _statement_rows(context) == {target_identity}
