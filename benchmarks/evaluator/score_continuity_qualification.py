"""Score a completed continuity observation against evaluator-only Gold."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deeplaw.util import strict_json_loads

_SCHEMA_VERSION = "deeplaw.continuity-qualification-gold/v1"
_MAX_GOLD_BYTES = 64 * 1024


def _strings(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"Gold {field} must be a non-empty string list")
    return list(value)


def load_gold(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_GOLD_BYTES:
        raise ValueError("Gold must be one bounded regular file")
    value = strict_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Gold must contain one JSON object")
    if value.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("Gold schema is unsupported")
    if (
        value.get("status") != "development_evaluator_only"
        or value.get("claim_eligible") is not False
    ):
        raise ValueError("Gold must remain evaluator-only and non-claim")
    if not isinstance(value.get("case_id"), str) or not value["case_id"]:
        raise ValueError("Gold case_id is invalid")
    if not isinstance(value.get("expected_first_action"), str):
        raise ValueError("Gold first action is invalid")
    for field in (
        "required_action_units",
        "required_decision_units",
        "required_context_units",
        "required_gap_units",
        "acceptable_gap_codes",
        "forbidden_state_units",
    ):
        _strings(value.get(field), field=field)
    duties = value.get("duties")
    if not isinstance(duties, list) or not duties:
        raise ValueError("Gold duties are invalid")
    return value


def _provider_text(provider: Mapping[str, Any]) -> tuple[str, set[str]]:
    capsule = provider.get("capsule")
    if not isinstance(capsule, Mapping):
        return "", set()
    statements = capsule.get("statements")
    statement_texts = [
        item["statement_text"]
        for item in statements
        if isinstance(statements, Sequence)
        and not isinstance(statements, (str, bytes))
        and isinstance(item, Mapping)
        and isinstance(item.get("statement_text"), str)
    ]
    gaps = capsule.get("gaps")
    gap_codes = {
        item["code"]
        for item in gaps
        if isinstance(gaps, Sequence)
        and not isinstance(gaps, (str, bytes))
        and isinstance(item, Mapping)
        and isinstance(item.get("code"), str)
    }
    return "\n".join(statement_texts), gap_codes


def _coverage(required: Sequence[str], text: str) -> float:
    return round(sum(unit.casefold() in text.casefold() for unit in required) / len(required), 6)


def score_observation(
    *, observation: Mapping[str, Any], gold: Mapping[str, Any]
) -> dict[str, Any]:
    if observation.get("case_id") != gold.get("case_id"):
        raise ValueError("observation and Gold case_id differ")
    host_output = observation.get("host_output")
    provider = observation.get("provider_capsule")
    if not isinstance(host_output, Mapping) or not isinstance(provider, Mapping):
        raise ValueError("observation must contain host output and Provider Capsule")
    provider_text, gap_codes = _provider_text(provider)
    next_step = host_output.get("next_step")
    decisions = host_output.get("preserved_decisions")
    open_gaps = host_output.get("open_gaps")
    if not isinstance(next_step, str):
        raise ValueError("host next_step is invalid")
    if not isinstance(decisions, list) or not all(isinstance(item, str) for item in decisions):
        raise ValueError("host preserved_decisions are invalid")
    if not isinstance(open_gaps, list) or not all(isinstance(item, str) for item in open_gaps):
        raise ValueError("host open_gaps are invalid")

    decision_text = "\n".join(decisions)
    gap_text = "\n".join(open_gaps)
    host_text = "\n".join(
        value
        for value in (
            host_output.get("summary"),
            next_step,
            decision_text,
            gap_text,
        )
        if isinstance(value, str)
    )
    all_observed_text = f"{provider_text}\n{host_text}"
    forbidden = _strings(gold.get("forbidden_state_units"), field="forbidden_state_units")
    wrong_state_admission = sum(
        unit.casefold() in all_observed_text.casefold() for unit in forbidden
    )
    context_units = _strings(gold.get("required_context_units"), field="required_context_units")
    present_context_units = [
        unit for unit in context_units if unit.casefold() in provider_text.casefold()
    ]
    relevant_chars = sum(len(unit) for unit in present_context_units)
    context_chars = len(provider_text)
    action_units = _strings(gold.get("required_action_units"), field="required_action_units")
    first_correct_action = float(
        next_step == gold.get("expected_first_action")
        or _coverage(action_units, next_step) == 1.0
    )

    duty_scores: list[float] = []
    for duty in gold["duties"]:
        if not isinstance(duty, Mapping):
            raise ValueError("Gold duty is invalid")
        source_text = provider_text if duty.get("source") == "provider_capsule" else host_text
        duty_scores.append(
            _coverage(
                _strings(duty.get("required_units"), field="duty.required_units"),
                source_text,
            )
        )

    required_gaps = _strings(gold.get("required_gap_units"), field="required_gap_units")
    acceptable_codes = set(
        _strings(gold.get("acceptable_gap_codes"), field="acceptable_gap_codes")
    )
    gap_unit_score = _coverage(required_gaps, gap_text)
    gap_code_score = 1.0 if not gap_codes or gap_codes.issubset(acceptable_codes) else 0.0
    metrics = {
        "first_correct_action": first_correct_action,
        "decision_preservation": _coverage(
            _strings(gold.get("required_decision_units"), field="required_decision_units"),
            decision_text,
        ),
        "wrong_state_admission": wrong_state_admission,
        "useful_context_recall": _coverage(context_units, provider_text),
        "relevant_chars": relevant_chars,
        "context_chars": context_chars,
        "relevant_chars_context_chars": round(relevant_chars / context_chars, 6)
        if context_chars
        else 0.0,
        "duty_coverage": round(sum(duty_scores) / len(duty_scores), 6),
        "gap_correctness": min(gap_unit_score, gap_code_score),
    }
    provider_bytes = observation.get("provider_bytes")
    hard_failures: list[str] = []
    if (
        not isinstance(provider_bytes, int)
        or isinstance(provider_bytes, bool)
        or not 1 <= provider_bytes <= 65_536
    ):
        hard_failures.append("provider_payload_invalid_or_overflow")
    if wrong_state_admission:
        hard_failures.append("wrong_state_admitted")
    return {
        "schema_version": "deeplaw.continuity-qualification-score/v1",
        "case_id": gold["case_id"],
        "release_ready": False,
        "claim_eligible": False,
        "metrics": metrics,
        "hard_failures": hard_failures,
    }
