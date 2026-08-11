"""Score a completed token-attribution observation after candidate execution."""

from __future__ import annotations

import argparse
import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.evaluator.score_continuity_qualification import (
    load_gold,
    score_observation,
)
from deeplaw.util import canonical_json, strict_json_loads

SCHEMA_VERSION = "deeplaw.codex-token-attribution-evaluation/v1"


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _object(path: Path) -> dict[str, Any]:
    value = strict_json_loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _statement_texts(provider: Mapping[str, Any]) -> list[str]:
    capsule = provider.get("capsule")
    if not isinstance(capsule, Mapping):
        return []
    statements = capsule.get("statements")
    if not isinstance(statements, Sequence) or isinstance(statements, (str, bytes)):
        return []
    return [
        item["statement_text"]
        for item in statements
        if isinstance(item, Mapping) and isinstance(item.get("statement_text"), str)
    ]


def _duplication(provider: Mapping[str, Any]) -> tuple[int, float]:
    texts = _statement_texts(provider)
    normalized = [" ".join(text.casefold().split()) for text in texts]
    counts = Counter(normalized)
    duplicate_evidence = sum(count - 1 for count in counts.values() if count > 1)
    total_chars = sum(len(text) for text in normalized)
    duplicate_chars = sum(
        len(text) * (count - 1) for text, count in counts.items() if count > 1
    )
    redundancy = round(duplicate_chars / total_chars, 6) if total_chars else 0.0
    return duplicate_evidence, redundancy


def _empty_metrics() -> dict[str, None]:
    return {
        field: None
        for field in (
            "first_correct_action",
            "decision_preservation",
            "wrong_state_admission",
            "useful_context_recall",
            "relevant_chars",
            "context_chars",
            "relevant_chars_context_chars",
            "duty_coverage",
            "gap_correctness",
        )
    }


def evaluate(
    *, observation_path: Path, gold_path: Path, output_path: Path
) -> dict[str, Any]:
    observation = _object(observation_path)
    observation_schema = _object(
        _repository() / "contracts/codex-token-attribution-observation.v1.schema.json"
    )
    Draft202012Validator(observation_schema).validate(observation)
    gold = load_gold(gold_path)
    conditions: list[dict[str, Any]] = []
    scored = 0
    for condition in observation["conditions"]:
        provider = condition.get("provider_capsule")
        host_output = condition.get("host_output")
        failures: list[str] = []
        if condition.get("status") != "passed":
            failures.append("candidate_condition_failed")
        if not isinstance(provider, Mapping) or not isinstance(host_output, Mapping):
            failures.append("provider_capsule_missing")
            conditions.append(
                {
                    "condition_id": condition["condition_id"],
                    "candidate_status": condition["status"],
                    "scoring_status": "not_scored",
                    "metrics": _empty_metrics(),
                    "duplicate_evidence": None,
                    "redundancy": None,
                    "distractor_answer_delta": None,
                    "hard_failures": sorted(set(failures)),
                }
            )
            continue
        score = score_observation(
            observation={
                "case_id": gold["case_id"],
                "host_output": host_output,
                "provider_capsule": provider,
                "provider_bytes": condition["provider_result_bytes"],
            },
            gold=gold,
        )
        duplicate_evidence, redundancy = _duplication(provider)
        failures.extend(score["hard_failures"])
        scored += 1
        conditions.append(
            {
                "condition_id": condition["condition_id"],
                "candidate_status": condition["status"],
                "scoring_status": "scored",
                "metrics": score["metrics"],
                "duplicate_evidence": duplicate_evidence,
                "redundancy": redundancy,
                "distractor_answer_delta": None,
                "hard_failures": sorted(set(failures)),
            }
        )
    all_passed = all(
        condition["candidate_status"] == "passed"
        and condition["scoring_status"] == "scored"
        and not condition["hard_failures"]
        and condition["metrics"]["first_correct_action"] == 1.0
        and condition["metrics"]["decision_preservation"] == 1.0
        and condition["metrics"]["wrong_state_admission"] == 0
        and condition["metrics"]["useful_context_recall"] == 1.0
        and condition["metrics"]["duty_coverage"] == 1.0
        and condition["metrics"]["gap_correctness"] == 1.0
        for condition in conditions
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if all_passed else "partial" if scored else "failed",
        "release_ready": False,
        "claim_eligible": False,
        "observation": {
            "name": observation_path.name,
            "sha256": _sha256(observation_path),
            "commit": observation["binding"]["commit"],
            "tree": observation["binding"]["tree"],
        },
        "evaluator": {
            "gold_name": gold_path.name,
            "gold_sha256": _sha256(gold_path),
            "scorer_name": Path(__file__).name,
            "scorer_sha256": _sha256(Path(__file__)),
        },
        "conditions": conditions,
        "profile_change_admitted": False,
        "not_executed": ["distractor_answer_delta", "qualification_holdout", "final_blind"],
    }
    schema = _object(
        _repository() / "contracts/codex-token-attribution-evaluation.v1.schema.json"
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)
    output_path.write_text(canonical_json(report) + "\n", encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score one completed Codex token-attribution observation."
    )
    parser.add_argument("--observation", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate(
        observation_path=args.observation,
        gold_path=args.gold,
        output_path=args.output,
    )
    print(canonical_json(report))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
