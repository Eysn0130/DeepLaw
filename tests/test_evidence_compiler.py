from __future__ import annotations

from itertools import permutations

import pytest

from deeplaw.evidence_compiler import (
    MAX_COMPILER_CHARS,
    MAX_COMPILER_ITEMS,
    EvidenceCandidate,
    EvidenceDuty,
    compile_evidence,
)


def _duty(duty_id: str, *, required: bool = True) -> EvidenceDuty:
    return EvidenceDuty(duty_id=duty_id, role="support", required=required)


def _candidate(
    candidate_id: str,
    *duty_ids: str,
    document_id: str | None = None,
    score: float = 1.0,
    chars: int = 100,
    uncertain: bool = False,
    authority_rank: int = 50,
) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=candidate_id,
        document_id=document_id or f"doc-{candidate_id}",
        score=score,
        chars=chars,
        is_uncertain=uncertain,
        duty_ids=tuple(duty_ids),
        authority_rank=authority_rank,
    )


def _rejected(result: object) -> dict[str, int]:
    return {item.reason: item.count for item in result.rejected}  # type: ignore[attr-defined]


def test_unrelated_high_score_cannot_displace_required_coverage() -> None:
    result = compile_evidence(
        (_duty("primary_rule"),),
        (
            _candidate("relevant", "primary_rule", score=0.1, authority_rank=1),
            _candidate("irrelevant", score=1_000_000, authority_rank=100),
        ),
        max_items=1,
        max_chars=500,
    )

    assert result.selected_ids == ("relevant",)
    assert result.duty_witnesses[0].status == "covered"
    assert result.duty_witnesses[0].candidate_id == "relevant"
    assert _rejected(result) == {"no_duty_coverage": 1}


def test_uncertain_candidate_cannot_replace_an_admissible_candidate() -> None:
    result = compile_evidence(
        (_duty("primary_rule"),),
        (
            _candidate("risky", "primary_rule", score=1000, uncertain=True),
            _candidate("safe", "primary_rule", score=0.01, authority_rank=0),
        ),
        max_items=1,
        max_chars=500,
    )

    assert result.selected_ids == ("safe",)
    assert result.duty_witnesses[0].status == "covered"
    assert _rejected(result) == {"uncertainty_gate": 1}


def test_uncertain_candidate_is_explicit_fallback_when_it_is_the_only_witness() -> None:
    result = compile_evidence(
        (_duty("temporal_status"),),
        (_candidate("only-risky", "temporal_status", uncertain=True),),
        max_items=1,
        max_chars=500,
    )

    assert result.selected_ids == ("only-risky",)
    assert result.selections[0].risk_status == "uncertain_fallback"
    assert result.duty_witnesses[0].status == "uncertain"
    assert result.uncertain_duty_ids == ("temporal_status",)
    assert not result.uncovered_duty_ids


def test_earlier_uncertain_duty_is_not_displaced_by_later_safe_duty() -> None:
    result = compile_evidence(
        (_duty("earlier"), _duty("later")),
        (
            _candidate("risky-earlier", "earlier", uncertain=True),
            _candidate("safe-later", "later", score=1_000),
        ),
        max_items=1,
        max_chars=500,
    )

    assert result.selected_ids == ("risky-earlier",)
    assert tuple(
        (witness.duty_id, witness.status) for witness in result.duty_witnesses
    ) == (("earlier", "uncertain"), ("later", "uncovered"))
    assert result.uncertain_duty_ids == ("earlier",)
    assert result.uncovered_duty_ids == ("later",)
    assert _rejected(result) == {"item_budget": 1}


def test_required_coverage_precedes_optional_and_stays_within_budgets() -> None:
    duties = (_duty("primary_rule"), _duty("case_reference", required=False))
    result = compile_evidence(
        duties,
        (
            _candidate("optional", "case_reference", score=100, chars=40),
            _candidate("required", "primary_rule", score=1, chars=70),
        ),
        max_items=2,
        max_chars=100,
    )

    assert result.selected_ids == ("required",)
    assert result.total_chars == 70
    assert len(result.selected_ids) <= 2
    assert result.total_chars <= 100
    assert result.uncovered_duty_ids == ("case_reference",)
    assert _rejected(result) == {"character_budget": 1}


def test_one_candidate_covering_more_duties_produces_a_smaller_evidence_set() -> None:
    duties = (_duty("primary_rule"), _duty("elements"))
    result = compile_evidence(
        duties,
        (
            _candidate("both", "primary_rule", "elements", chars=120),
            _candidate("one", "primary_rule", chars=60),
            _candidate("two", "elements", chars=60),
        ),
        max_items=3,
        max_chars=500,
    )

    assert result.selected_ids == ("both",)
    assert all(witness.candidate_id == "both" for witness in result.duty_witnesses)
    assert _rejected(result) == {"redundant_coverage": 2}


def test_bounded_greedy_behavior_is_not_a_global_set_cover_guarantee() -> None:
    duties = tuple(_duty(f"duty-{letter}") for letter in "abcde")
    broad_first = _candidate("broad-first", "duty-a", "duty-b", "duty-c")
    alternative_left = _candidate("alternative-left", "duty-a", "duty-d")
    alternative_right = _candidate(
        "alternative-right", "duty-b", "duty-c", "duty-e"
    )

    result = compile_evidence(
        duties,
        (broad_first, alternative_left, alternative_right),
        max_items=2,
        max_chars=500,
    )

    # Characterize the deterministic bounded greedy policy. The two
    # alternatives cover every duty together, so this is deliberately not an
    # assertion that the compiler finds a globally optimal set cover.
    assert set(alternative_left.duty_ids) | set(alternative_right.duty_ids) == {
        duty.duty_id for duty in duties
    }
    assert result.selected_ids == ("broad-first", "alternative-left")
    assert result.uncovered_duty_ids == ("duty-e",)


def test_document_diversity_breaks_equal_coverage_ties() -> None:
    duties = (_duty("primary_rule"), _duty("interpretation", required=False))
    result = compile_evidence(
        duties,
        (
            _candidate("primary", "primary_rule", document_id="doc-a"),
            _candidate(
                "same-document",
                "interpretation",
                document_id="doc-a",
                score=100,
            ),
            _candidate(
                "different-document",
                "interpretation",
                document_id="doc-b",
                score=1,
            ),
        ),
        max_items=2,
        max_chars=500,
    )

    assert result.selected_ids == ("primary", "different-document")
    assert result.selections[1].adds_document_diversity is True


def test_candidate_order_does_not_change_selection_or_digests() -> None:
    duties = (
        _duty("primary_rule"),
        _duty("elements"),
        _duty("counterevidence", required=False),
    )
    candidates = (
        _candidate("a", "primary_rule", score=3, chars=90),
        _candidate("b", "elements", score=2, chars=80),
        _candidate("c", "counterevidence", score=1, chars=70),
    )
    results = [
        compile_evidence(duties, tuple(order), max_items=3, max_chars=500)
        for order in permutations(candidates)
    ]

    first = results[0]
    assert all(result == first for result in results)
    assert all(result.candidate_digest == first.candidate_digest for result in results)
    assert all(result.result_digest == first.result_digest for result in results)


def test_default_card_bound_is_hard_even_when_more_duties_have_candidates() -> None:
    duties = tuple(_duty(f"duty-{index}") for index in range(6))
    candidates = tuple(
        _candidate(f"candidate-{index}", f"duty-{index}") for index in range(6)
    )

    result = compile_evidence(duties, candidates, max_chars=1000)

    assert len(result.selected_ids) == MAX_COMPILER_ITEMS
    assert len(result.uncovered_duty_ids) == 1
    assert _rejected(result) == {"item_budget": 1}


@pytest.mark.parametrize(
    ("max_items", "max_chars", "message"),
    (
        (MAX_COMPILER_ITEMS + 1, 100, "max_items"),
        (1, MAX_COMPILER_CHARS + 1, "max_chars"),
    ),
)
def test_compiler_rejects_unbounded_output_budgets(
    max_items: int,
    max_chars: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        compile_evidence((), (), max_items=max_items, max_chars=max_chars)


def test_compiler_rejects_unknown_duty_coverage() -> None:
    with pytest.raises(ValueError, match="unknown duties"):
        compile_evidence(
            (_duty("known"),),
            (_candidate("candidate", "unknown"),),
        )
