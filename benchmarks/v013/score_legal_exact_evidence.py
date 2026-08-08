"""Deterministic scorer for the source-only legal exact-evidence candidate.

The scorer deliberately accepts only two JSON documents: a candidate result and a
separately held Gold annotation.  It never opens the candidate source corpus,
repository code, a release database, or a provider.  A pending/non-independent
Gold can produce diagnostics, but can never make a quality or release claim
eligible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from deeplaw.util import canonical_json

SCHEMA_VERSION = "deeplaw.legal-exact-evidence-score/v1"
MAX_JSON_BYTES = 1 * 1024 * 1024
MAX_CASES = 256
MAX_FAILURES = 256
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH = re.compile(r"(?:^|[\s=:\"])/(?:Users|home|tmp|private|var)(?:[\s/\"]|$)")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/]")
_SECRET = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|secret)\s*[:=]")


class ScoreError(ValueError):
    """Raised for malformed evaluator input."""


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _load_json(value: str | Path | Mapping[str, Any], *, field: str) -> tuple[dict[str, Any], str]:
    if isinstance(value, Mapping):
        parsed = deepcopy(dict(value))
        encoded = canonical_json(parsed).encode("utf-8")
    else:
        path = Path(value).expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise ScoreError(f"{field} must be a regular non-symlink JSON file")
        if not 1 <= path.stat().st_size <= MAX_JSON_BYTES:
            raise ScoreError(f"{field} exceeds its byte bound")
        encoded = path.read_bytes()
        try:
            parsed = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ScoreError(f"{field} must be UTF-8 JSON") from error
    if not isinstance(parsed, dict):
        raise ScoreError(f"{field} must contain one JSON object")
    if len(encoded) > MAX_JSON_BYTES:
        raise ScoreError(f"{field} exceeds its byte bound")
    rendered = encoded.decode("utf-8")
    if (
        _ABSOLUTE_PATH.search(rendered)
        or _WINDOWS_PATH.search(rendered)
        or _SECRET.search(rendered)
    ):
        raise ScoreError(f"{field} contains a path or credential-like value")
    return parsed, hashlib.sha256(encoded).hexdigest()


def _bounded_cases(value: Mapping[str, Any], *, field: str) -> dict[str, dict[str, Any]]:
    cases = value.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ScoreError(f"{field}.cases is outside its bound")
    selected: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(cases):
        if not isinstance(item, Mapping) or not isinstance(item.get("case_id"), str):
            raise ScoreError(f"{field}.cases[{index}] must have a case_id")
        case_id = str(item["case_id"])
        if not case_id or len(case_id) > 160 or case_id in selected:
            raise ScoreError(f"{field}.cases has duplicate or invalid case_id")
        selected[case_id] = dict(item)
    return selected


def _contains_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key) in forbidden or _contains_key(item, forbidden)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_key(item, forbidden) for item in value)
    return False


def _validate_candidate(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if value.get("schema_version") != "deeplaw.legal-exact-evidence-candidate/v1":
        raise ScoreError("candidate schema_version is unsupported")
    if value.get("source_only") is not True or value.get("development_only") is not True:
        raise ScoreError("candidate must explicitly be source-only development output")
    if value.get("official_claimed") is True or value.get("release_claimed") is True:
        raise ScoreError("candidate cannot claim official or released status")
    if not isinstance(value.get("case_id"), str) or not value["case_id"]:
        raise ScoreError("candidate case_id is invalid")
    forbidden = {
        "gold",
        "gold_id",
        "scorer",
        "expected_ids",
        "answer_labels",
        "expected",
        "answerability",
        "required_duties",
        "blocking_gap",
        "source_text",
        "raw_text",
        "transcript",
        "prompt",
        "hidden_reasoning",
        "stdout",
        "stderr",
    }
    if _contains_key(value, forbidden):
        raise ScoreError("candidate contains evaluator or raw-source material")
    return _bounded_cases(value, field="candidate")


def _validate_gold(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    schema = value.get("schema_version")
    if schema == "deeplaw.legal-exact-evidence-owner-task-gold/v2":
        if value.get("candidate_visible_when_frozen") is not False:
            raise ScoreError("Gold must be frozen before candidate visibility")
        if value.get("claim_eligible") is not False:
            raise ScoreError("owner task Gold must remain claim-ineligible")
        expected = value.get("expected")
        if not isinstance(expected, Mapping) or set(expected) != {
            "current_exact",
            "exception_exact",
            "future_wrong_version",
        }:
            raise ScoreError("owner task Gold expected cases are invalid")
        normalized: dict[str, dict[str, Any]] = {}
        for case_id, category in (
            ("current_exact", "exact_current"),
            ("exception_exact", "exception"),
            ("future_wrong_version", "wrong_version"),
        ):
            case = expected[case_id]
            if not isinstance(case, Mapping):
                raise ScoreError("owner task Gold case is invalid")
            available = case_id != "future_wrong_version"
            normalized[case_id] = {
                "case_id": case_id,
                "category": category,
                "answerability": (
                    "duty_evidence_available" if available else "duty_not_in_corpus"
                ),
                "expected": dict(case),
            }
        hard_zero = value.get("hard_zero")
        if hard_zero != {
            "false_authority": 0,
            "invalid_quote_or_locator": 0,
            "wrong_version_primary_evidence": 0,
        }:
            raise ScoreError("owner task Gold hard-zero contract is invalid")
        required_checks = value.get("required_checks")
        if not isinstance(required_checks, list) or not required_checks:
            raise ScoreError("owner task Gold required checks are invalid")
        if value.get("agent_interpretation_origin") != "agent_derived" or value.get(
            "agent_interpretation_legal_authority"
        ) is not False:
            raise ScoreError("owner task Gold Authority contract is invalid")
        return normalized
    if schema not in {
        "deeplaw.legal-exact-evidence-gold/v1",
        "deeplaw.legal-held-out-gold/v1",
    }:
        raise ScoreError("Gold schema_version is unsupported")
    if {"candidate", "scorer", "source_text"}.intersection(value):
        raise ScoreError("Gold contains evaluator or raw-source material")
    return _bounded_cases(value, field="gold")


def _gold_review_confirmed(gold: Mapping[str, Any]) -> bool:
    status = gold.get("status")
    review = gold.get("review")
    if status == "independent_legal_human_confirmed":
        return bool(
            gold.get("independent_legal_human") is True
            or (
                isinstance(review, Mapping)
                and review.get("independent") is True
            )
        )
    if status != "expert_confirmed" or not isinstance(review, Mapping):
        return False
    if review.get("reviewer_role") not in {"legal_expert", "maintainer_with_legal_review"}:
        return False
    if not isinstance(review.get("reviewer_id"), str) or not review["reviewer_id"]:
        return False
    if not isinstance(review.get("reason"), str) or not review["reason"]:
        return False
    # The repository's held-out Gold binds review.gold_sha256 to the pending
    # annotation digest.  Reproduce that check without importing its tooling.
    unsigned = deepcopy(dict(gold))
    unsigned["status"] = "expert_review_pending"
    unsigned["review"] = None
    expected = _digest(unsigned)
    return review.get("gold_sha256") == expected


def _expected_available(gold_case: Mapping[str, Any]) -> bool:
    answerability = gold_case.get("answerability", gold_case.get("expected_outcome"))
    return answerability in {"duty_evidence_available", "evidence_available", "hit"}


def _expected_fields(gold_case: Mapping[str, Any]) -> Mapping[str, Any]:
    expected = gold_case.get("expected")
    if isinstance(expected, Mapping):
        return expected
    expected = gold_case.get("expected_evidence")
    return expected if isinstance(expected, Mapping) else {}


def _contains_gap(case: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    required = expected.get("blocking_gap", gold_case_value(case, "blocking_gap"))
    if required is None:
        required_values: list[str] = []
    elif isinstance(required, str):
        required_values = [required]
    elif isinstance(required, Sequence) and not isinstance(required, (str, bytes)):
        required_values = [str(item) for item in required[:8]]
    else:
        required_values = []
    gaps = case.get("gaps")
    if not isinstance(gaps, list):
        gaps = []
    if not gaps:
        return False
    return not required_values or all(item in gaps for item in required_values)


def gold_case_value(case: Mapping[str, Any], name: str) -> Any:
    value = case.get("expected")
    if isinstance(value, Mapping) and name in value:
        return value[name]
    return case.get(name)


def _matches_expected(selected: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    fields = {
        "title": expected.get("title", expected.get("document_title")),
        "article_label": expected.get("article_label"),
        "release_id": expected.get("release_id"),
        "segment_id": expected.get("segment_id"),
        "source_sha256": expected.get("source_sha256"),
        "segment_sha256": expected.get("segment_sha256"),
    }
    for actual_name, expected_value in fields.items():
        if expected_value is not None and selected.get(actual_name) != expected_value:
            return False
    required_quote = expected.get("required_quote")
    if required_quote is not None:
        if not isinstance(required_quote, str) or not required_quote:
            return False
        if selected.get("article_body_sha256") != hashlib.sha256(
            required_quote.encode("utf-8")
        ).hexdigest():
            return False
    return True


def _selected(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = case.get("selected")
    if not isinstance(values, list):
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)][:5]


def _case_score(
    gold_case: Mapping[str, Any], candidate_case: Mapping[str, Any]
) -> tuple[bool, bool, list[str]]:
    failures: list[str] = []
    selected = _selected(candidate_case)
    available = _expected_available(gold_case)
    expected = _expected_fields(gold_case)
    if available:
        if not selected:
            failures.append("available_case_has_no_exact_evidence")
            return False, False, failures
        if not all(_matches_expected(item, expected) for item in selected[:1]):
            failures.append("target_identity_mismatch")
        checks = candidate_case.get("checks")
        checks = checks if isinstance(checks, Mapping) else {}
        required_checks = {
            "source_segment_binding",
            "receipt_valid",
            "capabilities_exact_segment",
            "valid_citation",
            "primary_evidence_valid",
        }
        for name in required_checks:
            if checks.get(name) is not True:
                failures.append(f"{name}_failed")
        for item in selected:
            if item.get("citation_valid") is not True:
                failures.append("invalid_quote_locator_or_receipt")
        return not failures, False, failures
    # A non-answerable/uncertain duty must remain a visible gap and must not be
    # covered by a least-bad official segment.
    if selected:
        failures.append("non_answerable_case_was_covered")
    if not _contains_gap(candidate_case, expected):
        failures.append("required_gap_missing")
    category = str(gold_case.get("category", ""))
    if category in {"wrong_version", "future_wrong_version", "historical_version", "repeal"}:
        checks = candidate_case.get("checks")
        if not isinstance(checks, Mapping) or checks.get("wrong_version_excluded") is not True:
            failures.append("temporal_wrong_version_not_excluded")
    return not failures, True, failures


def score_candidate(
    gold: str | Path | Mapping[str, Any],
    candidate: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    gold_value, gold_sha256 = _load_json(gold, field="gold")
    candidate_value, candidate_sha256 = _load_json(candidate, field="candidate")
    gold_cases = _validate_gold(gold_value)
    candidate_cases = _validate_candidate(candidate_value)
    if (
        gold_value.get("schema_version")
        == "deeplaw.legal-exact-evidence-owner-task-gold/v2"
        and gold_value.get("case_id") != candidate_value.get("case_id")
    ):
        raise ScoreError("candidate case is absent from owner task Gold")
    failures: list[str] = []
    if set(gold_cases) != set(candidate_cases):
        failures.append("candidate_case_set_does_not_match_gold")
    available_total = 0
    available_correct = 0
    gap_total = 0
    gap_correct = 0
    identity_correct = 0
    hit_ranks: list[int] = []
    duty_totals = {"definition": 0, "exception": 0, "proviso": 0, "cross_reference": 0}
    duty_correct = dict.fromkeys(duty_totals, 0)
    temporal_total = 0
    temporal_correct = 0
    for case_id, gold_case in gold_cases.items():
        actual = candidate_cases.get(case_id)
        if actual is None:
            continue
        passed, is_gap, case_failures = _case_score(gold_case, actual)
        failures.extend(f"{case_id}:{failure}" for failure in case_failures)
        category = str(gold_case.get("category", ""))
        if category in duty_totals:
            duty_totals[category] += 1
            duty_correct[category] += int(passed)
        if category in {
            "historical_version",
            "effective_date",
            "amendment",
            "repeal",
            "wrong_version",
            "future_wrong_version",
        }:
            temporal_total += 1
            temporal_correct += int(passed)
        if _expected_available(gold_case):
            available_total += 1
            if passed:
                available_correct += 1
                identity_correct += 1
                hit_ranks.append(1)
        else:
            gap_total += 1
            if is_gap and passed:
                gap_correct += 1
    hard_failures = []
    raw_hard = candidate_value.get("hard_failures")
    if isinstance(raw_hard, Mapping):
        for name, value in raw_hard.items():
            if (isinstance(value, bool) and value) or (
                isinstance(value, (int, float)) and value > 0
            ):
                hard_failures.append(str(name))
    partitions = candidate_value.get("authority_partitions")
    official_partition = partitions.get("official") if isinstance(partitions, Mapping) else None
    agent_partition = (
        partitions.get("agent_interpretation") if isinstance(partitions, Mapping) else None
    )
    if (
        not isinstance(partitions, Mapping)
        or partitions.get("preserved") is not True
        or not isinstance(official_partition, Mapping)
        or official_partition.get("origin") != "official"
        or official_partition.get("legal_authority") is not True
        or not isinstance(agent_partition, Mapping)
        or agent_partition.get("origin") != "agent_derived"
        or agent_partition.get("legal_authority") is not False
    ):
        hard_failures.append("authority_partition_mixing")
    if (
        candidate_value.get("claim_eligible") is True
        or candidate_value.get("competitive_claim_eligible") is True
    ):
        hard_failures.append("candidate_claimed_eligibility")
    hard_failures = list(dict.fromkeys(hard_failures))[:MAX_FAILURES]
    failures.extend(f"hard:{item}" for item in hard_failures)
    gold_confirmed = _gold_review_confirmed(gold_value)
    all_passed = not failures and not hard_failures
    human_gold_passed = bool(gold_confirmed and all_passed)
    case_total = len(gold_cases)
    metrics = {
        "case_count": case_total,
        "document_recall": (
            round(available_correct / available_total, 6)
            if available_total
            else 1.0
        ),
        "exact_segment_recall": (
            round(available_correct / available_total, 6)
            if available_total
            else 1.0
        ),
        "target_identity_precision": (
            round(identity_correct / available_total, 6)
            if available_total
            else 1.0
        ),
        "mrr": (
            round(sum(1 / rank for rank in hit_ranks) / available_total, 6)
            if available_total
            else 1.0
        ),
        "ndcg": (
            round(sum(1 / rank.bit_length() for rank in hit_ranks) / available_total, 6)
            if available_total
            else 1.0
        ),
        "correct_gap_precision": round(gap_correct / gap_total, 6) if gap_total else 1.0,
        "correct_gap_recall": round(gap_correct / gap_total, 6) if gap_total else 1.0,
        "definition_recall": (
            round(duty_correct["definition"] / duty_totals["definition"], 6)
            if duty_totals["definition"]
            else 1.0
        ),
        "exception_recall": (
            round(duty_correct["exception"] / duty_totals["exception"], 6)
            if duty_totals["exception"]
            else 1.0
        ),
        "proviso_recall": (
            round(duty_correct["proviso"] / duty_totals["proviso"], 6)
            if duty_totals["proviso"]
            else 1.0
        ),
        "cross_reference_recall": (
            round(duty_correct["cross_reference"] / duty_totals["cross_reference"], 6)
            if duty_totals["cross_reference"]
            else 1.0
        ),
        "definition_exception_proviso_cross_reference_recall": (
            round(sum(duty_correct.values()) / sum(duty_totals.values()), 6)
            if sum(duty_totals.values())
            else 1.0
        ),
        "temporal_correctness": (
            round(temporal_correct / temporal_total, 6) if temporal_total else 1.0
        ),
        "citation_validity": (
            0.0
            if any("invalid_quote_locator_or_receipt" in item for item in failures)
            else 1.0
        ),
        "false_authority_admission": int("false_authority_admission" in hard_failures),
        "false_authority_admission_rate": float(
            "false_authority_admission" in hard_failures
        ),
        "wrong_version_inclusion": int("wrong_version_primary_evidence" in hard_failures),
        "wrong_version_inclusion_rate": float(
            "wrong_version_primary_evidence" in hard_failures
        ),
        "redundancy": 0.0,
        "relevant_chars": None,
        "context_chars": None,
    }
    status = (
        "development_passed"
        if human_gold_passed
        else "failed"
        if gold_confirmed and not all_passed
        else "not_eligible"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "gold_sha256": gold_sha256,
        "candidate_sha256": candidate_sha256,
        "gold_status": gold_value.get("status"),
        "gold_human_review_confirmed": gold_confirmed,
        "failures": failures[:MAX_FAILURES],
        "hard_failures": hard_failures,
        "metrics": metrics,
        "development_thresholds_passed": all_passed,
        "human_gold_thresholds_passed": human_gold_passed,
        "release_gate_passed": False,
        "claim_eligible": False,
        "release_eligible": False,
        "competitive_claim_eligible": False,
        "not_executed": [
            "independent_legal_human_review",
            "signed_28_source_pack",
            "real_codex_host",
            "cross_platform_release_artifact",
        ],
    }


def score(*, gold: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Keyword form retained for small deterministic evaluator call sites."""
    return score_candidate(gold, candidate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score a legal exact-evidence candidate against frozen Gold"
    )
    parser.add_argument("gold", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    result = score_candidate(arguments.gold, arguments.candidate)
    rendered = canonical_json(result) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        if arguments.output.exists() or arguments.output.is_symlink():
            raise ScoreError("score output already exists")
        arguments.output.write_text(rendered, encoding="utf-8")
    return 0 if result["development_thresholds_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
