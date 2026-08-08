"""Deterministic development scoring for the continuity candidate lanes."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deeplaw.util import canonical_json, sha256_bytes

SCHEMA_VERSION = "deeplaw.continuity-score/v1"
_REQUIRED_THRESHOLD_FIELDS = frozenset(
    {
        "first_correct_action",
        "decision_preservation",
        "maximum_stale_decision_inclusion",
        "minimum_useful_context_recall",
        "minimum_relevant_chars_context_chars",
        "maximum_false_memory_admission",
        "minimum_contradiction_gap_coverage",
        "maximum_provider_bytes",
        "maximum_local_context_latency_ms",
        "minimum_useful_context_recall_gain_over_host_only",
        "minimum_first_correct_action_gain_over_host_only",
    }
)

_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_PATH = re.compile(
    r"(?:^|[\s=:\"])/(?:Users|home|tmp|private|var)(?:[\s/\"]|$)|[A-Za-z]:[\\/]"
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|secret)\s*[\"']?\s*[:=]"
)


def _load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("score input must be a regular file")
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("score input must contain one JSON object")
    return value


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 1.0
    return round(float(numerator) / float(denominator), 6)


def _ids(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {str(item) for item in value if isinstance(item, (str, int))}


def _candidate_hash(value: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _validate_candidate(value: Mapping[str, Any], *, mode: str) -> None:
    if value.get("schema_version") != "deeplaw.continuity-candidate/v1":
        raise ValueError("candidate schema version is invalid")
    if value.get("mode") != mode:
        raise ValueError(f"candidate mode must be {mode}")
    if value.get("claim_eligible") is not False or value.get(
        "competitive_claim_eligible"
    ) is not False:
        raise ValueError("continuity candidates cannot claim eligibility")
    hashes = value.get("source_hashes")
    if not isinstance(hashes, Mapping):
        raise ValueError("candidate source hashes are missing")
    expected_keys = {"thread_b"} if mode == "host-only" else {"thread_a", "thread_b"}
    if set(hashes) != expected_keys or any(
        not isinstance(item, str) or _HASH.fullmatch(item) is None for item in hashes.values()
    ):
        raise ValueError("candidate source hashes are invalid")
    if value.get("input_roles") != sorted(expected_keys):
        raise ValueError("candidate input scope is invalid")
    selected = value.get("selected_statements")
    provider_selected = value.get("provider_selected_statements")
    if not isinstance(selected, list) or not isinstance(provider_selected, list):
        raise ValueError("candidate statement projections are invalid")
    if len(selected) > 20 or len(provider_selected) > 20:
        raise ValueError("candidate statement projections exceed their bound")
    for item in [*selected, *provider_selected]:
        if not isinstance(item, Mapping):
            raise ValueError("candidate statement is invalid")
        for field in ("statement_id", "knowledge_id", "knowledge_revision_id"):
            if item.get(field) is not None and (
                not isinstance(item.get(field), str)
                or _ID.fullmatch(item[field]) is None
            ):
                raise ValueError("candidate statement identity is invalid")
        text = item.get("statement_text", "")
        if (
            not isinstance(text, str)
            or len(text) > 2_000
            or _PATH.search(text)
            or _SECRET.search(text)
        ):
            raise ValueError("candidate statement text is unsafe")
        source_refs = item.get("source_refs", [])
        if not isinstance(source_refs, list) or len(source_refs) > 2:
            raise ValueError("candidate source references are invalid")
        for reference in source_refs:
            if not isinstance(reference, Mapping) or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or _ID.fullmatch(value) is None
                for key, value in reference.items()
            ):
                raise ValueError("candidate source references are invalid")
    gap_codes = value.get("gap_codes", [])
    if not isinstance(gap_codes, list) or len(gap_codes) > 32 or any(
        not isinstance(item, str) or _ID.fullmatch(item) is None for item in gap_codes
    ):
        raise ValueError("candidate gap codes are invalid")
    contradictions = value.get("contradictions", [])
    if not isinstance(contradictions, list) or len(contradictions) > 16:
        raise ValueError("candidate contradictions are invalid")
    provider_bytes = value.get("provider_bytes")
    local_bytes = value.get("local_bytes")
    if not isinstance(provider_bytes, int) or not 0 <= provider_bytes <= 65_536:
        raise ValueError("candidate provider bytes are invalid")
    if not isinstance(local_bytes, int) or not 0 <= local_bytes <= 262_144:
        raise ValueError("candidate local bytes are invalid")
    serialized = canonical_json(value)
    if _PATH.search(serialized) or _SECRET.search(serialized):
        raise ValueError("candidate contains disallowed path or secret-like material")


def _text_units(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item or len(item) > 500 for item in value
    ):
        raise ValueError(f"Gold {field} is invalid")
    return list(value)


def _validate_gold(gold: Mapping[str, Any], *, case_id: str) -> dict[str, float]:
    if not isinstance(gold.get("status"), str) or not gold["status"]:
        raise ValueError("Gold status is required")
    if gold.get("claim_eligible") is not False:
        raise ValueError("development Gold must remain claim-ineligible")
    if gold.get("candidate_visible_when_frozen") is not False:
        raise ValueError("Gold must have been frozen before candidate visibility")
    if gold.get("case_id") != case_id:
        raise ValueError("candidate case is absent from Gold")
    expected_action = gold.get("expected_first_action")
    if not isinstance(expected_action, str) or not expected_action:
        raise ValueError("Gold expected_first_action is invalid")
    for field in (
        "required_goal_units",
        "required_decision_units",
        "required_constraint_units",
        "required_gap_units",
        "required_next_action_units",
        "required_artifact_units",
        "forbidden_stale_units",
        "forbidden_distractor_units",
    ):
        _text_units(gold.get(field), field=field)
    thresholds = gold.get("frozen_thresholds")
    if not isinstance(thresholds, Mapping) or set(thresholds) != _REQUIRED_THRESHOLD_FIELDS:
        raise ValueError("Gold frozen_thresholds are invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
        for value in thresholds.values()
    ):
        raise ValueError("Gold frozen_thresholds contain an invalid value")
    return {key: float(value) for key, value in thresholds.items()}


def _selected_text(candidate: Mapping[str, Any]) -> str:
    return "\n".join(
        str(item["statement_text"])
        for item in candidate.get("selected_statements", [])
        if isinstance(item, Mapping) and isinstance(item.get("statement_text"), str)
    )


def _present_units(text: str, units: Sequence[str]) -> list[str]:
    return [item for item in units if item in text]


def _lane_metrics(candidate: Mapping[str, Any], gold: Mapping[str, Any]) -> dict[str, Any]:
    text = _selected_text(candidate)
    goals = _text_units(gold["required_goal_units"], field="required_goal_units")
    decisions = _text_units(
        gold["required_decision_units"], field="required_decision_units"
    )
    constraints = _text_units(
        gold["required_constraint_units"], field="required_constraint_units"
    )
    gaps = _text_units(gold["required_gap_units"], field="required_gap_units")
    actions = _text_units(
        gold["required_next_action_units"], field="required_next_action_units"
    )
    artifacts = _text_units(
        gold["required_artifact_units"], field="required_artifact_units"
    )
    stale = _text_units(gold["forbidden_stale_units"], field="forbidden_stale_units")
    distractors = _text_units(
        gold["forbidden_distractor_units"], field="forbidden_distractor_units"
    )
    useful = [*goals, *decisions, *constraints, *gaps, *actions, *artifacts]
    present = _present_units(text, useful)
    present_decisions = _present_units(text, decisions)
    present_gaps = _present_units(text, gaps)
    stale_present = _present_units(text, stale)
    distractors_present = _present_units(text, distractors)
    relevant_chars = sum(len(item) for item in present)
    context_chars = len(text)
    expected_action = gold["expected_first_action"]
    return {
        "first_correct_action": float(candidate.get("first_action") == expected_action),
        "decision_preservation": _ratio(len(present_decisions), len(decisions)),
        "stale_decision_inclusion": float(
            bool(stale_present) or bool(candidate.get("stale_revision_selected"))
        ),
        "useful_context_recall": _ratio(len(present), len(useful)),
        "relevant_chars": relevant_chars,
        "context_chars": context_chars,
        "relevant_chars_context_chars": (
            _ratio(relevant_chars, context_chars) if context_chars else 0.0
        ),
        "false_memory_admission": float(
            bool(distractors_present) or bool(candidate.get("distractor_selected"))
        ),
        "distractor_inclusion": float(bool(distractors_present)),
        "contradiction_gap_coverage": _ratio(len(present_gaps), len(gaps)),
        "provider_bytes": candidate["provider_bytes"],
        "latency_ms": candidate.get("latency_ms", 0.0),
        "selected_statement_count": len(candidate.get("selected_statements", [])),
        "gap_codes": sorted(_ids(candidate.get("gap_codes", []))),
        "current_revision_id": candidate.get("current_revision_id"),
        "context_recovered": bool(candidate.get("context_recovered")),
        "present_unit_count": len(present),
        "required_unit_count": len(useful),
    }


def _gain(plus: Mapping[str, Any], host: Mapping[str, Any], field: str) -> float:
    return round(float(plus.get(field, 0.0)) - float(host.get(field, 0.0)), 6)


def score_continuity(
    *,
    host_only: Mapping[str, Any],
    host_plus_deeplaw: Mapping[str, Any],
    gold: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_candidate(host_only, mode="host-only")
    _validate_candidate(host_plus_deeplaw, mode="host-plus-deeplaw")
    case_id = host_only.get("case_id")
    if case_id != host_plus_deeplaw.get("case_id") or not isinstance(case_id, str):
        raise ValueError("candidate case identities do not match")
    thresholds = _validate_gold(gold, case_id=case_id)
    host_metrics = _lane_metrics(host_only, gold)
    plus_metrics = _lane_metrics(host_plus_deeplaw, gold)
    relative_gain = {
        field: _gain(plus_metrics, host_metrics, field)
        for field in (
            "first_correct_action",
            "decision_preservation",
            "useful_context_recall",
            "relevant_chars_context_chars",
            "contradiction_gap_coverage",
        )
    }
    threshold_pass = {
        "first_correct_action": plus_metrics["first_correct_action"]
        >= thresholds["first_correct_action"],
        "decision_preservation": plus_metrics["decision_preservation"]
        >= thresholds["decision_preservation"],
        "stale_decision_inclusion": plus_metrics["stale_decision_inclusion"]
        <= thresholds["maximum_stale_decision_inclusion"],
        "useful_context_recall": plus_metrics["useful_context_recall"]
        >= thresholds["minimum_useful_context_recall"],
        "relevant_chars_context_chars": plus_metrics["relevant_chars_context_chars"]
        >= thresholds["minimum_relevant_chars_context_chars"],
        "false_memory_admission": plus_metrics["false_memory_admission"]
        <= thresholds["maximum_false_memory_admission"],
        "contradiction_gap_coverage": plus_metrics["contradiction_gap_coverage"]
        >= thresholds["minimum_contradiction_gap_coverage"],
        "provider_bytes": plus_metrics["provider_bytes"]
        <= thresholds["maximum_provider_bytes"],
        "latency": plus_metrics["latency_ms"]
        <= thresholds["maximum_local_context_latency_ms"],
        "useful_context_recall_gain": relative_gain["useful_context_recall"]
        >= thresholds["minimum_useful_context_recall_gain_over_host_only"],
        "first_correct_action_gain": relative_gain["first_correct_action"]
        >= thresholds["minimum_first_correct_action_gain_over_host_only"],
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "gold": {
            "status": gold["status"],
            "sha256": _candidate_hash(gold),
        },
        "candidate_hashes": {
            "host_only": _candidate_hash(host_only),
            "host_plus_deeplaw": _candidate_hash(host_plus_deeplaw),
        },
        "lanes": {
            "host-only": host_metrics,
            "host-plus-deeplaw": plus_metrics,
        },
        "relative_gain": relative_gain,
        "thresholds": thresholds,
        "thresholds_sha256": sha256_bytes(
            canonical_json(thresholds).encode("utf-8")
        ),
        "threshold_pass": threshold_pass,
        "development_thresholds_passed": all(threshold_pass.values()),
        "release_gate_passed": False,
        "claim_eligible": False,
        "competitive_claim_eligible": False,
        "not_executed": ["human_gold_external_review", "real_provider_host_tasks"],
        "known_limitations": [
            "development candidates are not a release or comparative claim",
            "model/provider output is not used as label evidence",
        ],
    }


def score_files(
    host_only_path: str | Path,
    host_plus_deeplaw_path: str | Path,
    gold_path: str | Path,
) -> dict[str, Any]:
    return score_continuity(
        host_only=_load_json(host_only_path),
        host_plus_deeplaw=_load_json(host_plus_deeplaw_path),
        gold=_load_json(gold_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score two continuity development candidates")
    parser.add_argument("host_only")
    parser.add_argument("host_plus_deeplaw")
    parser.add_argument("gold")
    parser.add_argument("--output")
    return parser


def _write_json(value: Mapping[str, Any], output: str | None) -> None:
    encoded = canonical_json(value)
    if output is None:
        print(encoded)
        return
    output_path = Path(output)
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("score output already exists")
    output_path.write_text(encoded + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    report = score_files(arguments.host_only, arguments.host_plus_deeplaw, arguments.gold)
    _write_json(report, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
