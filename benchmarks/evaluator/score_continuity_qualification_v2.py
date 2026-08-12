"""Evaluator-only structural scoring for the Pass 12 continuity lane.

Pass 12 deliberately scores a closed machine contract.  The candidate may use
any language in its explanation, but correctness is established only by
statement identities, closed action/release fields, and gap codes.  This module
does not import or reinterpret the Pass 11 scorer or Gold.
"""

from __future__ import annotations

import argparse
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

SCHEMA_VERSION = "deeplaw.continuity-qualification-score/v2"
GOLD_SCHEMA_VERSION = "deeplaw.continuity-qualification-gold/v2"
HUMAN_REVIEW_SCHEMA_VERSION = "deeplaw.continuity-human-review/v1"
PROVIDER_HARD_LIMIT = 65_536
MAX_CANDIDATE_BYTES = 256 * 1024
MAX_GOLD_BYTES = 65_536
MAX_REVIEW_BYTES = 65_536
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROVIDER_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "contracts"
    / "provider-knowledge-capsule.v2.schema.json"
)


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must contain one JSON object")
    return dict(value)


def _non_empty_strings(value: Any, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field} must be a non-empty unique string list")
    return list(value)


def _optional_strings(value: Any, *, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field} must be a unique string list")
    return list(value)


def _closed_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _load_bounded(path: Path, *, maximum: int, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be one bounded regular file")
    if path.stat().st_size > maximum:
        raise ValueError(f"{label} exceeds its byte bound")
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{label} must be UTF-8 JSON") from error
    return _object(value, label=label)


def _validate_release_state(value: Any, *, field: str) -> dict[str, Any]:
    state = _closed_mapping(value, field=field)
    expected = {"package_version", "release_ready", "claim_eligible"}
    if set(state) != expected:
        raise ValueError(f"{field} must contain only package_version/release_ready/claim_eligible")
    if not isinstance(state["package_version"], str) or not state["package_version"]:
        raise ValueError(f"{field}.package_version is invalid")
    if state["release_ready"] is not False or state["claim_eligible"] is not False:
        raise ValueError(f"{field} must remain release-closed")
    return state


def _validate_gold(value: Mapping[str, Any]) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "gold_id",
        "status",
        "claim_eligible",
        "candidate_visible_when_frozen",
        "case_id",
        "expected_statement_ids",
        "forbidden_statement_ids",
        "expected_action",
        "expected_release_state",
        "required_gap_codes",
        "acceptable_gap_codes",
        "required_duties",
        "human_rubric",
        "historical_evidence_binding",
    }
    if set(value) != expected_fields:
        raise ValueError("Gold fields are not closed")
    if value.get("schema_version") != GOLD_SCHEMA_VERSION:
        raise ValueError("Gold schema is unsupported")
    if value.get("status") != "development_evaluator_only":
        raise ValueError("Gold must remain evaluator-only")
    if value.get("claim_eligible") is not False:
        raise ValueError("Gold must remain claim-ineligible")
    if not isinstance(value.get("gold_id"), str) or not value["gold_id"]:
        raise ValueError("Gold gold_id is invalid")
    if value.get("candidate_visible_when_frozen") is not False:
        raise ValueError("Gold must be frozen before candidate visibility")
    if not isinstance(value.get("case_id"), str) or not value["case_id"]:
        raise ValueError("Gold case_id is invalid")
    _non_empty_strings(value.get("expected_statement_ids"), field="Gold expected_statement_ids")
    _optional_strings(value.get("forbidden_statement_ids"), field="Gold forbidden_statement_ids")
    expected_ids = set(value["expected_statement_ids"])
    forbidden_ids = set(value["forbidden_statement_ids"])
    if expected_ids & forbidden_ids:
        raise ValueError("Gold expected and forbidden Statement IDs overlap")
    action = value.get("expected_action")
    if not isinstance(action, str) or not action or action not in {
        "preserve_release_hold",
        "verify_current_state",
    }:
        raise ValueError("Gold expected_action is not a closed action")
    _validate_release_state(
        value.get("expected_release_state"), field="Gold expected_release_state"
    )
    _non_empty_strings(value.get("required_gap_codes"), field="Gold required_gap_codes")
    _non_empty_strings(value.get("acceptable_gap_codes"), field="Gold acceptable_gap_codes")
    required_gaps = set(value["required_gap_codes"])
    acceptable_gaps = set(value["acceptable_gap_codes"])
    if not required_gaps <= acceptable_gaps:
        raise ValueError("Gold required gap codes must be acceptable")
    duties = value.get("required_duties")
    if not isinstance(duties, list) or not duties or len(duties) > 16:
        raise ValueError("Gold required_duties is invalid")
    duty_labels: list[str] = []
    for index, raw_duty in enumerate(duties):
        duty = _closed_mapping(raw_duty, field=f"Gold required_duties[{index}]")
        if set(duty) != {
            "duty_label",
            "required_statement_ids",
            "required_gap_codes",
        }:
            raise ValueError("Gold required duty fields are not closed")
        label = duty.get("duty_label")
        if not isinstance(label, str) or not label:
            raise ValueError("Gold duty_label is invalid")
        duty_labels.append(label)
        duty_statement_ids = _optional_strings(
            duty.get("required_statement_ids"),
            field=f"Gold required_duties[{index}].required_statement_ids",
        )
        duty_gap_codes = _optional_strings(
            duty.get("required_gap_codes"),
            field=f"Gold required_duties[{index}].required_gap_codes",
        )
        if not duty_statement_ids and not duty_gap_codes:
            raise ValueError("Gold required duty must bind a Statement or Gap")
        if not set(duty_statement_ids) <= expected_ids:
            raise ValueError("Gold required duty binds an unexpected Statement")
        if not set(duty_gap_codes) <= required_gaps:
            raise ValueError("Gold required duty binds a non-required Gap")
    if len(set(duty_labels)) != len(duty_labels):
        raise ValueError("Gold duty labels must be unique")
    rubric = _closed_mapping(value.get("human_rubric"), field="Gold human_rubric")
    if set(rubric) != {"en", "zh"}:
        raise ValueError("Gold human_rubric must contain independent en and zh entries")
    for language in ("en", "zh"):
        entry = _closed_mapping(rubric[language], field=f"Gold human_rubric.{language}")
        if set(entry) != {"criterion_ids", "criteria", "pass_condition"}:
            raise ValueError(f"Gold human_rubric.{language} is not closed")
        criterion_ids = _non_empty_strings(
            entry.get("criterion_ids"),
            field=f"Gold human_rubric.{language}.criterion_ids",
        )
        criteria = _non_empty_strings(
            entry.get("criteria"), field=f"Gold human_rubric.{language}.criteria"
        )
        if not isinstance(entry.get("pass_condition"), str) or not entry["pass_condition"]:
            raise ValueError(f"Gold human_rubric.{language}.pass_condition is invalid")
        if (
            len(criteria) > 16
            or len(criterion_ids) != len(criteria)
            or len(entry["pass_condition"]) > 1_000
        ):
            raise ValueError(f"Gold human_rubric.{language} exceeds its bound")
    # There is intentionally no Pass 11 binding in this Gold.  This marker is
    # a structural guard against accidentally treating historical evidence as a
    # v2 candidate source.
    if value.get("historical_evidence_binding") != []:
        raise ValueError("Gold must not bind historical evidence")
    return dict(value)


def load_gold(path: Path) -> dict[str, Any]:
    """Load and validate an evaluator-only v2 Gold file."""

    return _validate_gold(_load_bounded(path, maximum=MAX_GOLD_BYTES, label="Gold"))


def _review_entry(
    value: Any,
    *,
    language: str,
    expected_criterion_ids: set[str],
) -> dict[str, Any]:
    entry = _closed_mapping(value, field=f"Human review {language}")
    required = {"reviewer_id", "independent", "decision", "criterion_results"}
    if set(entry) != required:
        raise ValueError(f"Human review {language} fields are not closed")
    if not isinstance(entry["reviewer_id"], str) or not entry["reviewer_id"]:
        raise ValueError(f"Human review {language}.reviewer_id is invalid")
    if entry["independent"] is not True:
        raise ValueError(f"Human review {language} is not independent")
    if entry["decision"] not in {"pass", "fail"}:
        raise ValueError(f"Human review {language}.decision is invalid")
    criteria = _closed_mapping(
        entry["criterion_results"], field=f"Human review {language}.criterion_results"
    )
    if not criteria or any(not isinstance(item, bool) for item in criteria.values()):
        raise ValueError(f"Human review {language}.criterion_results is invalid")
    if set(criteria) != expected_criterion_ids:
        raise ValueError(
            f"Human review {language}.criterion_results do not bind the Gold rubric"
        )
    return entry


def _validate_human_review(value: Mapping[str, Any], *, gold: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "review_id",
        "status",
        "gold_id",
        "gold_sha256",
        "case_id",
        "candidate_sha256",
        "reviews",
        "claim_eligible",
    }
    if set(value) != required:
        raise ValueError("Human review fields are not closed")
    if value.get("schema_version") != HUMAN_REVIEW_SCHEMA_VERSION:
        raise ValueError("Human review schema is unsupported")
    if value.get("status") != "independent_bilingual_review_complete":
        raise ValueError("Human review is not complete")
    if value.get("gold_id") != gold.get("gold_id"):
        raise ValueError("Human review does not bind this Gold")
    if value.get("case_id") != gold.get("case_id"):
        raise ValueError("Human review does not bind this case")
    if value.get("claim_eligible") is not False:
        raise ValueError("Human review must remain claim-ineligible")
    for field in ("review_id", "gold_sha256", "candidate_sha256"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(f"Human review {field} is invalid")
    if not _SHA256_PATTERN.fullmatch(str(value["gold_sha256"])) or not (
        _SHA256_PATTERN.fullmatch(str(value["candidate_sha256"]))
    ):
        raise ValueError("Human review digest is invalid")
    reviews = _closed_mapping(value.get("reviews"), field="Human review reviews")
    if set(reviews) != {"en", "zh"}:
        raise ValueError("Human review must contain both en and zh")
    english = _review_entry(
        reviews["en"],
        language="en",
        expected_criterion_ids=set(gold["human_rubric"]["en"]["criterion_ids"]),
    )
    chinese = _review_entry(
        reviews["zh"],
        language="zh",
        expected_criterion_ids=set(gold["human_rubric"]["zh"]["criterion_ids"]),
    )
    if english["reviewer_id"] == chinese["reviewer_id"]:
        raise ValueError("Human review languages must have independent reviewers")
    return {**dict(value), "reviews": {"en": english, "zh": chinese}}


def load_human_review(path: Path, *, gold: Mapping[str, Any]) -> dict[str, Any]:
    """Load one bounded, independently authored bilingual review."""

    return _validate_human_review(
        _load_bounded(path, maximum=MAX_REVIEW_BYTES, label="Human review"),
        gold=_validate_gold(gold),
    )


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _capsule(provider: Any) -> Mapping[str, Any] | None:
    if not isinstance(provider, Mapping):
        return None
    nested = provider.get("capsule")
    if isinstance(nested, Mapping):
        return nested
    return provider


def _statement_ids(provider: Any) -> tuple[list[str], bool]:
    outer = provider if isinstance(provider, Mapping) else None
    capsule = _capsule(provider)
    if capsule is None:
        return [], False
    values: list[Any] = []
    statements = capsule.get("statements")
    if isinstance(statements, Sequence) and not isinstance(statements, (str, bytes)):
        for statement in statements:
            if not isinstance(statement, Mapping) or not isinstance(
                statement.get("statement_id"), str
            ):
                return [], False
            values.append(statement["statement_id"])
    selected = capsule.get("selected_statement_ids")
    if selected is None and outer is not None:
        selected = outer.get("selected_statement_ids")
    if selected is None and isinstance(capsule.get("selected_statements"), list):
        selected = [
            item.get("statement_id")
            for item in capsule["selected_statements"]
            if isinstance(item, Mapping)
        ]
    if isinstance(selected, list):
        if any(not isinstance(item, str) or not item for item in selected):
            return [], False
        if values and list(selected) != values:
            return [], False
        values = list(selected)
    if len(set(values)) != len(values):
        return [], False
    return values, True


def _gap_codes(provider: Any, host_output: Mapping[str, Any]) -> tuple[list[str], bool]:
    values: list[str] = []
    capsule = _capsule(provider)
    if capsule is not None:
        gaps = capsule.get("gaps", [])
        if not isinstance(gaps, list):
            return [], False
        for gap in gaps:
            if (
                not isinstance(gap, Mapping)
                or not isinstance(gap.get("code"), str)
                or not gap["code"]
            ):
                return [], False
            values.append(gap["code"])
    host_gaps = host_output.get("gap_codes", [])
    if not isinstance(host_gaps, list) or any(
        not isinstance(item, str) or not item for item in host_gaps
    ):
        return [], False
    values.extend(host_gaps)
    return sorted(set(values)), True


def _observed_provider_bytes(observation: Mapping[str, Any]) -> int | None:
    value = observation.get("provider_bytes")
    if value is None:
        value = observation.get("provider_result_bytes")
    if value is None:
        value = observation.get("provider_payload_bytes")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _provider_measurement(
    provider: Any,
) -> tuple[int | None, str | None, list[str]]:
    failures: list[str] = []
    if not isinstance(provider, Mapping):
        return None, None, ["provider_capsule_schema_invalid"]
    capsule = provider.get("capsule")
    if not isinstance(capsule, Mapping):
        return None, None, ["provider_capsule_schema_invalid"]
    content = canonical_json(capsule)
    provider_bytes = len(content.encode("utf-8"))
    provider_sha256 = sha256_bytes(content.encode("utf-8"))
    try:
        schema = strict_json_loads(_PROVIDER_SCHEMA_PATH.read_bytes())
        if not isinstance(schema, Mapping):
            raise ValueError("Provider Capsule schema is invalid")
        Draft202012Validator.check_schema(schema)
        error = next(Draft202012Validator(schema).iter_errors(provider), None)
        if error is not None:
            failures.append("provider_capsule_schema_invalid")
    except (OSError, ValueError):
        failures.append("provider_capsule_schema_invalid")
    delivery = provider.get("delivery")
    if not isinstance(delivery, Mapping) or (
        delivery.get("provider_content_bytes") != provider_bytes
    ):
        failures.append("provider_delivery_size_mismatch")
    return provider_bytes, provider_sha256, failures


def _tool_call_metrics(
    observation: Mapping[str, Any],
    *,
    provider_bytes: int | None,
    provider_sha256: str | None,
) -> tuple[bool, int, list[str]]:
    failures: list[str] = []
    receipt = observation.get("actual_event_receipt")
    calls = receipt.get("tool_calls") if isinstance(receipt, Mapping) else None
    if not isinstance(calls, list):
        return False, 0, ["knowledge_support_call_receipt_invalid"]
    retry_count = max(0, len(calls) - 1)
    if not calls:
        return False, 0, ["knowledge_support_call_missing"]
    if len(calls) > 2:
        failures.append("knowledge_support_call_budget_exceeded")

    call_validities: list[bool] = []
    call_bytes: list[int] = []
    for ordinal, call in enumerate(calls, start=1):
        if not isinstance(call, Mapping):
            failures.append("knowledge_support_call_receipt_invalid")
            call_validities.append(False)
            continue
        tool = call.get("tool")
        safe_leaf = isinstance(tool, str) and (
            tool == "knowledge_support" or tool.endswith("_knowledge_support")
        )
        safe = bool(
            call.get("ordinal") == ordinal
            and safe_leaf
            and call.get("operation") == "context"
            and call.get("read_only") is True
            and call.get("write_performed") is False
            and call.get("status") in {"completed", "failed"}
        )
        if not safe:
            failures.append("unsafe_or_write_tool_call")
        value = call.get("provider_bytes")
        digest = call.get("provider_content_sha256")
        bounded = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= (
            PROVIDER_HARD_LIMIT
        )
        digest_valid = isinstance(digest, str) and bool(_SHA256_PATTERN.fullmatch(digest))
        if not bounded or not digest_valid:
            failures.append("tool_call_provider_binding_invalid")
        if bounded:
            call_bytes.append(value)
        result_valid = bool(
            safe
            and bounded
            and digest_valid
            and call.get("status") == "completed"
            and call.get("result_valid") is True
            and value == provider_bytes
            and digest == provider_sha256
        )
        call_validities.append(result_valid)

    if not call_validities or not call_validities[-1]:
        failures.append("final_provider_result_unbound")
    if len(call_bytes) == 2 and all(
        value > PROVIDER_HARD_LIMIT // 2 for value in call_bytes
    ):
        failures.append("repeated_large_provider_payload")
    if sum(call_bytes) > PROVIDER_HARD_LIMIT + PROVIDER_HARD_LIMIT // 2:
        failures.append("tool_call_provider_budget_exceeded")
    return bool(call_validities and call_validities[0]), retry_count, failures


def _candidate_input_values(observation: Mapping[str, Any]) -> list[str]:
    """Return candidate-supplied leaves for isolation checks only.

    The provider output is intentionally excluded: it must contain the selected
    IDs that the evaluator checks.  This function never decides correctness.
    """

    values: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, Mapping):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    for key in (
        "candidate",
        "candidate_fixture",
        "fixture",
        "prompt",
        "prompt_text",
        "candidate_prompt",
        "task",
        "task_text",
        "input",
    ):
        if key in observation:
            visit(observation[key])
    return values


def _isolation_failures(observation: Mapping[str, Any], gold: Mapping[str, Any]) -> list[str]:
    suspicious_keys = {
        "gold",
        "gold_id",
        "gold_name",
        "gold_sha256",
        "expected_action",
        "expected_answer",
        "expected_statement_ids",
        "forbidden_statement_ids",
        "expected_release_state",
        "human_rubric",
        "scorer",
        "scorer_name",
        "marker",
    }

    def suspicious_key(value: Any) -> bool:
        if isinstance(value, Mapping):
            if suspicious_keys & set(value):
                return True
            return any(suspicious_key(nested) for nested in value.values())
        if isinstance(value, list):
            return any(suspicious_key(nested) for nested in value)
        return False

    for key in (
        "candidate",
        "candidate_fixture",
        "fixture",
        "prompt",
        "prompt_text",
        "candidate_prompt",
        "task",
        "task_text",
        "input",
    ):
        if suspicious_key(observation.get(key)):
            return ["candidate_gold_material_exposed"]
    protected = [
        str(gold["gold_id"]),
        str(gold["expected_action"]),
        *gold["expected_statement_ids"],
        *gold["forbidden_statement_ids"],
    ]
    candidate_values = _candidate_input_values(observation)
    if any(protected_value in value for value in candidate_values for protected_value in protected):
        return ["candidate_gold_material_exposed"]
    return []


def _review_failures(
    human_review: Any,
    gold: Mapping[str, Any],
    *,
    gold_sha256: str | None,
    candidate_sha256: str | None,
) -> list[str]:
    if human_review is None:
        return ["human_review_missing"]
    if not isinstance(human_review, Mapping):
        return ["human_review_invalid"]
    try:
        review = _validate_human_review(human_review, gold=gold)
    except ValueError:
        if human_review.get("status") != "independent_bilingual_review_complete":
            return ["human_review_incomplete"]
        if (
            human_review.get("gold_id") != gold.get("gold_id")
            or human_review.get("case_id") != gold.get("case_id")
        ):
            return ["human_review_binding_mismatch"]
        reviews = human_review.get("reviews")
        if not isinstance(reviews, Mapping) or set(reviews) != {"en", "zh"}:
            return ["human_review_not_independent_bilingual"]
        return ["human_review_invalid"]
    reviews = review["reviews"]
    if gold_sha256 is None or candidate_sha256 is None:
        return ["human_review_digest_binding_unverified"]
    if (
        review["gold_sha256"] != gold_sha256
        or review["candidate_sha256"] != candidate_sha256
    ):
        return ["human_review_digest_binding_mismatch"]
    if any(entry["decision"] != "pass" for entry in reviews.values()):
        return ["human_review_rejected"]
    if any(not all(entry["criterion_results"].values()) for entry in reviews.values()):
        return ["human_review_criteria_failed"]
    return []


def _host_action(host_output: Mapping[str, Any]) -> str | None:
    value = host_output.get("action")
    if isinstance(value, str):
        return value
    for field in ("action_code", "first_action_code"):
        value = host_output.get(field)
        if isinstance(value, str):
            return value
    # ``first_action`` is a closed field in the candidate result.  Natural
    # language ``next_step`` and summaries are deliberately not considered.
    value = host_output.get("first_action")
    if isinstance(value, Mapping):
        value = value.get("code")
    return value if isinstance(value, str) else None


def score_observation(
    *,
    observation: Mapping[str, Any],
    gold: Mapping[str, Any],
    human_review: Mapping[str, Any] | None = None,
    gold_sha256: str | None = None,
    candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Score one candidate observation using only structural fields.

    Missing Human review is represented as an explicit hard failure.  The
    report remains release-closed even when all machine checks pass.
    """

    validated_gold = _validate_gold(gold)
    if observation.get("case_id") != validated_gold["case_id"]:
        raise ValueError("observation and Gold case_id differ")
    host_output = _mapping_or_none(observation.get("host_output"))
    provider = observation.get("provider_capsule")
    failures = _isolation_failures(observation, validated_gold)
    provider_bytes, provider_sha256, provider_failures = _provider_measurement(provider)
    failures.extend(provider_failures)
    if observation.get("claim_eligible") is True:
        failures.append("candidate_claim_eligible")
    if observation.get("release_ready") is True:
        failures.append("candidate_release_ready")
    if observation.get("write_performed") is True:
        failures.append("candidate_write_performed")
    failures.extend(
        _review_failures(
            human_review,
            validated_gold,
            gold_sha256=gold_sha256,
            candidate_sha256=candidate_sha256,
        )
    )

    ids, ids_valid = _statement_ids(provider)
    if not ids_valid:
        failures.append("statement_identity_projection_invalid")
    provider_id_set = set(ids)
    expected_ids = set(validated_gold["expected_statement_ids"])
    forbidden_ids = set(validated_gold["forbidden_statement_ids"])
    forbidden_present = sorted(provider_id_set & forbidden_ids)
    if forbidden_present:
        failures.append("forbidden_statement_id_admitted")
    missing_ids = sorted(expected_ids - provider_id_set)
    unexpected_ids = sorted(provider_id_set - expected_ids - forbidden_ids)
    if missing_ids:
        failures.append("expected_statement_id_missing")
    if unexpected_ids:
        failures.append("unexpected_statement_id_admitted")
    useful_recall = (
        round(len(expected_ids & provider_id_set) / len(expected_ids), 6)
        if ids_valid
        else None
    )
    hit_ranks = [
        index
        for index, statement_id in enumerate(ids, start=1)
        if statement_id in expected_ids
    ]
    precision_at_k = (
        round(len(expected_ids & provider_id_set) / len(ids), 6) if ids else 0.0
    )
    mrr = round(1.0 / hit_ranks[0], 6) if hit_ranks else 0.0
    dcg = sum(1.0 / math.log2(rank + 1) for rank in hit_ranks)
    ideal_count = min(len(expected_ids), len(ids))
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    ndcg = round(dcg / ideal_dcg, 6) if ideal_dcg else 0.0
    capsule = _capsule(provider)
    statement_rows = (
        capsule.get("statements", []) if isinstance(capsule, Mapping) else []
    )
    text_by_id = {
        row["statement_id"]: row.get("statement_text", "")
        for row in statement_rows
        if isinstance(row, Mapping)
        and isinstance(row.get("statement_id"), str)
        and isinstance(row.get("statement_text", ""), str)
    }
    context_chars = (
        len(canonical_json(capsule)) if isinstance(capsule, Mapping) else 0
    )
    relevant_chars = sum(
        len(text_by_id.get(statement_id, ""))
        for statement_id in ids
        if statement_id in expected_ids
    )
    selected_texts = [text_by_id.get(statement_id, "") for statement_id in ids]
    nonempty_texts = [text for text in selected_texts if text]
    redundancy = (
        round((len(nonempty_texts) - len(set(nonempty_texts))) / len(nonempty_texts), 6)
        if nonempty_texts
        else 0.0
    )
    evidence_keys = [
        canonical_json(reference)
        for row in statement_rows
        if isinstance(row, Mapping) and isinstance(row.get("source_refs", []), list)
        for reference in row.get("source_refs", [])
        if isinstance(reference, Mapping)
    ]
    duplicate_evidence = len(evidence_keys) - len(set(evidence_keys))

    action = _host_action(host_output) if host_output is not None else None
    first_correct = (
        float(action == validated_gold["expected_action"]) if action is not None else 0.0
    )
    if action != validated_gold["expected_action"]:
        failures.append("expected_action_mismatch")

    release_state = host_output.get("release_state") if host_output is not None else None
    release_valid = isinstance(release_state, Mapping)
    release_match = release_valid and dict(release_state) == validated_gold[
        "expected_release_state"
    ]
    if not release_match:
        failures.append("wrong_release_state_admitted")

    gaps, gaps_valid = _gap_codes(provider, host_output or {})
    required_gaps = set(validated_gold["required_gap_codes"])
    acceptable_gaps = set(validated_gold["acceptable_gap_codes"])
    gap_set = set(gaps)
    missing_gaps = (
        sorted(required_gaps - gap_set)
        if gaps_valid
        else list(validated_gold["required_gap_codes"])
    )
    unacceptable_gaps = sorted(gap_set - acceptable_gaps) if gaps_valid else []
    if missing_gaps:
        failures.append("required_gap_code_missing")
    if unacceptable_gaps:
        failures.append("unacceptable_gap_code")
    gap_correct = float(not missing_gaps and not unacceptable_gaps and gaps_valid)
    satisfied_duties = [
        str(duty["duty_label"])
        for duty in validated_gold["required_duties"]
        if set(duty["required_statement_ids"]) <= provider_id_set
        and set(duty["required_gap_codes"]) <= gap_set
    ]
    duty_coverage = round(
        len(satisfied_duties) / len(validated_gold["required_duties"]), 6
    )
    if duty_coverage != 1.0:
        failures.append("required_duty_unsatisfied")

    observed_provider_bytes = _observed_provider_bytes(observation)
    observed_provider_sha256 = observation.get("provider_content_sha256")
    if observed_provider_bytes is None:
        failures.append("provider_payload_size_missing")
    elif observed_provider_bytes != provider_bytes:
        failures.append("provider_payload_size_mismatch")
    if provider_bytes is None or provider_bytes > PROVIDER_HARD_LIMIT:
        failures.append("provider_payload_overflow")
    if (
        not isinstance(observed_provider_sha256, str)
        or not _SHA256_PATTERN.fullmatch(observed_provider_sha256)
        or observed_provider_sha256 != provider_sha256
    ):
        failures.append("provider_payload_hash_mismatch")
    first_call_validity, retry_count, tool_failures = _tool_call_metrics(
        observation,
        provider_bytes=provider_bytes,
        provider_sha256=provider_sha256,
    )
    failures.extend(tool_failures)

    wrong_state_count = len(forbidden_present) + len(unexpected_ids) + int(not release_match)
    if wrong_state_count:
        failures.append("wrong_state_admitted")
    machine_metrics = {
        "first_correct_action": first_correct,
        "decision_preservation": float(release_match),
        "wrong_state_admission": wrong_state_count,
        "useful_context_recall": useful_recall,
        "recall_at_k": useful_recall,
        "precision_at_k": precision_at_k,
        "mrr": mrr,
        "ndcg": ndcg,
        "relevant_chars": relevant_chars,
        "context_chars": context_chars,
        "relevant_chars_ratio": (
            round(relevant_chars / context_chars, 6) if context_chars else 0.0
        ),
        "redundancy": redundancy,
        "duplicate_evidence": duplicate_evidence,
        "duty_coverage": duty_coverage,
        "satisfied_duty_labels": satisfied_duties,
        "gap_correctness": gap_correct,
        "provider_bytes": provider_bytes,
        "observed_provider_bytes": observed_provider_bytes,
        "provider_content_sha256": provider_sha256,
        "first_call_validity": first_call_validity,
        "retry_count": retry_count,
    }
    review_failures = _review_failures(
        human_review,
        validated_gold,
        gold_sha256=gold_sha256,
        candidate_sha256=candidate_sha256,
    )
    scored = not review_failures and not failures
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": validated_gold["case_id"],
        "status": "passed" if scored else "failed",
        "scoring_status": "scored" if not review_failures else "not_scored",
        "release_ready": False,
        "claim_eligible": False,
        "human_review_passed": not review_failures,
        "artifact_binding_verified": bool(
            not review_failures
            and human_review is not None
            and gold_sha256 is not None
            and candidate_sha256 is not None
            and human_review.get("gold_sha256") == gold_sha256
            and human_review.get("candidate_sha256") == candidate_sha256
        ),
        "selected_statement_ids": ids,
        "missing_statement_ids": missing_ids,
        "unexpected_statement_ids": unexpected_ids,
        "forbidden_statement_ids": forbidden_present,
        "observed_gap_codes": gaps,
        "missing_gap_codes": missing_gaps,
        "unacceptable_gap_codes": unacceptable_gaps,
        "metrics": machine_metrics,
        "hard_failures": sorted(set(failures)),
        "isolation": {
            "candidate_gold_material_exposed": "candidate_gold_material_exposed" in failures,
            "gold_candidate_separated": "candidate_gold_material_exposed" not in failures,
        },
    }


def score(
    *,
    observation: Mapping[str, Any],
    gold: Mapping[str, Any],
    human_review: Mapping[str, Any] | None = None,
    gold_sha256: str | None = None,
    candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Short alias for :func:`score_observation`."""

    return score_observation(
        observation=observation,
        gold=gold,
        human_review=human_review,
        gold_sha256=gold_sha256,
        candidate_sha256=candidate_sha256,
    )


def score_files(
    observation_path: str | Path,
    gold_path: str | Path,
    human_review_path: str | Path | None = None,
) -> dict[str, Any]:
    selected_observation_path = Path(observation_path)
    selected_gold_path = Path(gold_path)
    observation = _load_bounded(
        selected_observation_path, maximum=MAX_CANDIDATE_BYTES, label="candidate"
    )
    gold = load_gold(selected_gold_path)
    human_review = (
        load_human_review(Path(human_review_path), gold=gold)
        if human_review_path is not None
        else None
    )
    return score_observation(
        observation=observation,
        gold=gold,
        human_review=human_review,
        gold_sha256=sha256_bytes(selected_gold_path.read_bytes()),
        candidate_sha256=sha256_bytes(selected_observation_path.read_bytes()),
    )


def evaluate(
    *,
    observation_path: Path,
    gold_path: Path,
    output_path: Path,
    human_review_path: Path | None = None,
) -> dict[str, Any]:
    report = score_files(observation_path, gold_path, human_review_path)
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("score output already exists")
    output_path.write_text(canonical_json(report) + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score one Pass 12 continuity candidate")
    parser.add_argument("observation")
    parser.add_argument("gold")
    parser.add_argument("--human-review")
    parser.add_argument("--output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    report = score_files(arguments.observation, arguments.gold, arguments.human_review)
    encoded = canonical_json(report)
    if arguments.output is None:
        print(encoded)
    else:
        output = Path(arguments.output)
        if output.exists() or output.is_symlink():
            raise ValueError("score output already exists")
        output.write_text(encoded + "\n", encoding="utf-8")
    return 0


# A descriptive compatibility alias keeps call sites readable without exposing
# the historical Pass 11 module.
score_continuity = score_observation


if __name__ == "__main__":
    raise SystemExit(main())
