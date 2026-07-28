from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from deeplaw.util import (
    canonical_json,
    normalize_text,
    sha256_bytes,
    sha256_file,
    strict_json_loads,
)

INPUT_SCHEMA = "deeplaw.typed-compiler-benchmark-input/v1"
REPORT_SCHEMA = "deeplaw.typed-compiler-benchmark/v1"
_MAX_INPUT_BYTES = 64 * 1024 * 1024
_MAX_CASES = 100_000
_MAX_CLAIMS_PER_CASE = 10_000
_SUPPORT_LABELS = frozenset({"supported", "unsupported", "hallucinated"})
_REVIEW_DECISIONS = frozenset({"accept", "reject", "not_reviewed"})


def _bounded_text(value: Any, *, field: str, maximum: int = 20_000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be a bounded canonical string")
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _validate_refs(value: Any, *, known: set[str], field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 1_000
        or len(set(value)) != len(value)
        or any(not isinstance(item, str) or item not in known for item in value)
    ):
        raise ValueError(f"{field} must contain unique known source-ref IDs")
    return tuple(value)


def _validate_case(case: Any) -> dict[str, Any]:
    if not isinstance(case, dict) or set(case) != {
        "case_id",
        "source_refs",
        "gold_claims",
        "predicted_claims",
    }:
        raise ValueError("typed-compiler case does not match its closed contract")
    case_id = _bounded_text(case["case_id"], field="case_id", maximum=200)
    refs = case["source_refs"]
    if not isinstance(refs, list) or not refs or len(refs) > 10_000:
        raise ValueError(f"{case_id}.source_refs must be a bounded non-empty list")
    ref_ids: set[str] = set()
    source_ids: dict[str, str] = {}
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {
            "ref_id",
            "source_id",
            "locator",
            "text_sha256",
        }:
            raise ValueError(f"{case_id}.source_ref does not match its closed contract")
        ref_id = _bounded_text(ref["ref_id"], field=f"{case_id}.ref_id", maximum=200)
        if ref_id in ref_ids:
            raise ValueError(f"{case_id} contains a duplicate source-ref ID")
        ref_ids.add(ref_id)
        source_ids[ref_id] = _bounded_text(
            ref["source_id"], field=f"{case_id}.source_id", maximum=200
        )
        _bounded_text(ref["locator"], field=f"{case_id}.locator", maximum=2_000)
        digest = ref["text_sha256"]
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"{case_id}.text_sha256 is invalid")
        try:
            int(digest, 16)
        except ValueError as error:
            raise ValueError(f"{case_id}.text_sha256 is invalid") from error

    gold = case["gold_claims"]
    predicted = case["predicted_claims"]
    if (
        not isinstance(gold, list)
        or not isinstance(predicted, list)
        or len(gold) > _MAX_CLAIMS_PER_CASE
        or len(predicted) > _MAX_CLAIMS_PER_CASE
    ):
        raise ValueError(f"{case_id} claim inventories exceed their bounds")
    gold_ids: set[str] = set()
    validated_gold: list[dict[str, Any]] = []
    for claim in gold:
        if not isinstance(claim, dict) or set(claim) != {
            "claim_id",
            "kind",
            "statement",
            "source_ref_ids",
            "cross_document",
        }:
            raise ValueError(f"{case_id}.gold_claim does not match its closed contract")
        claim_id = _bounded_text(
            claim["claim_id"], field=f"{case_id}.claim_id", maximum=200
        )
        if claim_id in gold_ids:
            raise ValueError(f"{case_id} contains a duplicate gold claim ID")
        gold_ids.add(claim_id)
        claim_refs = _validate_refs(
            claim["source_ref_ids"], known=ref_ids, field=f"{case_id}.gold.source_ref_ids"
        )
        if not isinstance(claim["cross_document"], bool):
            raise ValueError(f"{case_id}.gold.cross_document must be boolean")
        if claim["cross_document"] != (len({source_ids[item] for item in claim_refs}) > 1):
            raise ValueError(f"{case_id}.gold.cross_document conflicts with its source refs")
        validated_gold.append(
            {
                **claim,
                "kind": _bounded_text(claim["kind"], field=f"{case_id}.gold.kind", maximum=80),
                "statement": normalize_text(
                    _bounded_text(
                        claim["statement"], field=f"{case_id}.gold.statement"
                    )
                ),
                "source_ref_ids": claim_refs,
            }
        )

    prediction_ids: set[str] = set()
    validated_predictions: list[dict[str, Any]] = []
    for prediction in predicted:
        if not isinstance(prediction, dict) or set(prediction) != {
            "prediction_id",
            "kind",
            "statement",
            "source_ref_ids",
            "matched_gold_claim_id",
            "claim_equivalent",
            "support_label",
            "review_decision",
            "cross_document",
        }:
            raise ValueError(f"{case_id}.prediction does not match its closed contract")
        prediction_id = _bounded_text(
            prediction["prediction_id"], field=f"{case_id}.prediction_id", maximum=200
        )
        if prediction_id in prediction_ids:
            raise ValueError(f"{case_id} contains a duplicate prediction ID")
        prediction_ids.add(prediction_id)
        matched = prediction["matched_gold_claim_id"]
        if matched is not None and (not isinstance(matched, str) or matched not in gold_ids):
            raise ValueError(f"{case_id}.matched_gold_claim_id is unknown")
        if not isinstance(prediction["claim_equivalent"], bool):
            raise ValueError(f"{case_id}.claim_equivalent must be boolean")
        if prediction["claim_equivalent"] and matched is None:
            raise ValueError(f"{case_id} cannot mark an unmatched prediction equivalent")
        if prediction["support_label"] not in _SUPPORT_LABELS:
            raise ValueError(f"{case_id}.support_label is invalid")
        if prediction["review_decision"] not in _REVIEW_DECISIONS:
            raise ValueError(f"{case_id}.review_decision is invalid")
        prediction_refs = _validate_refs(
            prediction["source_ref_ids"],
            known=ref_ids,
            field=f"{case_id}.prediction.source_ref_ids",
        )
        if not isinstance(prediction["cross_document"], bool):
            raise ValueError(f"{case_id}.prediction.cross_document must be boolean")
        if prediction["cross_document"] != (
            len({source_ids[item] for item in prediction_refs}) > 1
        ):
            raise ValueError(
                f"{case_id}.prediction.cross_document conflicts with its source refs"
            )
        validated_predictions.append(
            {
                **prediction,
                "kind": _bounded_text(
                    prediction["kind"], field=f"{case_id}.prediction.kind", maximum=80
                ),
                "statement": normalize_text(
                    _bounded_text(
                        prediction["statement"], field=f"{case_id}.prediction.statement"
                    )
                ),
                "source_ref_ids": prediction_refs,
            }
        )
    return {
        "case_id": case_id,
        "gold": validated_gold,
        "predicted": validated_predictions,
    }


def _score_inventory(gold: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> dict[str, Any]:
    gold_by_id = {claim["claim_id"]: claim for claim in gold}
    true_predictions: list[dict[str, Any]] = []
    for prediction in predicted:
        matched = prediction["matched_gold_claim_id"]
        if (
            matched is not None
            and prediction["claim_equivalent"]
            and prediction["support_label"] == "supported"
            and prediction["kind"] == gold_by_id[matched]["kind"]
        ):
            true_predictions.append(prediction)
    matched_gold = {item["matched_gold_claim_id"] for item in true_predictions}
    true_positive = len(matched_gold)
    precision = _ratio(true_positive, len(predicted))
    recall = _ratio(true_positive, len(gold))
    exact_span_gold = {
        prediction["matched_gold_claim_id"]
        for prediction in true_predictions
        if set(prediction["source_ref_ids"])
        == set(gold_by_id[prediction["matched_gold_claim_id"]]["source_ref_ids"])
    }
    normalized_keys = [
        (
            item.get("_case_id"),
            item["kind"],
            item["statement"].casefold(),
            tuple(sorted(item["source_ref_ids"])),
        )
        for item in predicted
    ]
    duplicate_count = len(normalized_keys) - len(set(normalized_keys))
    reviewed = [item for item in predicted if item["review_decision"] != "not_reviewed"]
    cross_gold = {item["claim_id"] for item in gold if item["cross_document"]}
    cross_predictions = [item for item in predicted if item["cross_document"]]
    correct_cross = {
        item["matched_gold_claim_id"]
        for item in true_predictions
        if item["cross_document"]
        and item["matched_gold_claim_id"] in cross_gold
        and set(item["source_ref_ids"])
        == set(gold_by_id[item["matched_gold_claim_id"]]["source_ref_ids"])
    }
    cross_precision = _ratio(len(correct_cross), len(cross_predictions))
    cross_recall = _ratio(len(correct_cross), len(cross_gold))
    counts = {
        "gold_claims": len(gold),
        "predicted_claims": len(predicted),
        "true_positive_gold_claims": true_positive,
        "true_positive_predictions": len(true_predictions),
        "hallucinated_predictions": sum(
            item["support_label"] == "hallucinated" for item in predicted
        ),
        "unsupported_predictions": sum(
            item["support_label"] != "supported" for item in predicted
        ),
        "exact_source_span_claims": len(exact_span_gold),
        "duplicate_predictions": duplicate_count,
        "reviewed_predictions": len(reviewed),
        "accepted_predictions": sum(
            item["review_decision"] == "accept" for item in reviewed
        ),
        "cross_document_gold_claims": len(cross_gold),
        "cross_document_predictions": len(cross_predictions),
        "correct_cross_document_claims": len(correct_cross),
    }
    return {
        "counts": counts,
        "metrics": {
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
            "hallucinated_claim_rate": _ratio(
                counts["hallucinated_predictions"], len(predicted)
            ),
            "unsupported_claim_rate": _ratio(
                counts["unsupported_predictions"], len(predicted)
            ),
            "source_span_correctness": _ratio(len(exact_span_gold), true_positive),
            "duplicate_claim_rate": _ratio(duplicate_count, len(predicted)),
            "review_acceptance_rate": _ratio(counts["accepted_predictions"], len(reviewed)),
            "cross_document_synthesis_correctness": _f1(
                cross_precision, cross_recall
            ),
        },
    }


def score_suite(path: str | Path) -> dict[str, Any]:
    selected = Path(path)
    if selected.is_symlink() or not selected.is_file():
        raise ValueError("typed-compiler suite must be a regular non-symlink file")
    if not 1 <= selected.stat().st_size <= _MAX_INPUT_BYTES:
        raise ValueError("typed-compiler suite exceeds its byte bound")
    value = strict_json_loads(selected.read_bytes())
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "suite_id",
        "candidate_line",
        "compiler_identity",
        "cases",
        "claim_eligible",
        "limitations",
    }:
        raise ValueError("typed-compiler suite does not match its closed root contract")
    if value["schema_version"] != INPUT_SCHEMA:
        raise ValueError("typed-compiler input schema is unsupported")
    suite_id = _bounded_text(value["suite_id"], field="suite_id", maximum=200)
    candidate_line = _bounded_text(
        value["candidate_line"], field="candidate_line", maximum=100
    )
    compiler_identity = _bounded_text(
        value["compiler_identity"], field="compiler_identity", maximum=500
    )
    if value["claim_eligible"] is not False:
        raise ValueError("development typed-compiler suites must remain claim-ineligible")
    limitations = value["limitations"]
    if (
        not isinstance(limitations, list)
        or not limitations
        or len(limitations) > 100
        or any(not isinstance(item, str) or not item or len(item) > 1_000 for item in limitations)
    ):
        raise ValueError("typed-compiler limitations must be explicit and bounded")
    cases = value["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= _MAX_CASES:
        raise ValueError("typed-compiler cases must be a bounded non-empty list")
    validated = [_validate_case(case) for case in cases]
    case_ids = [case["case_id"] for case in validated]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("typed-compiler case IDs must be unique")
    per_case: list[dict[str, Any]] = []
    all_gold: list[dict[str, Any]] = []
    all_predicted: list[dict[str, Any]] = []
    for case in validated:
        scored = _score_inventory(case["gold"], case["predicted"])
        per_case.append({"case_id": case["case_id"], **scored})
        all_gold.extend(
            {**claim, "claim_id": f"{case['case_id']}::{claim['claim_id']}"}
            for claim in case["gold"]
        )
        all_predicted.extend(
            {
                **prediction,
                "_case_id": case["case_id"],
                "matched_gold_claim_id": (
                    f"{case['case_id']}::{prediction['matched_gold_claim_id']}"
                    if prediction["matched_gold_claim_id"] is not None
                    else None
                ),
            }
            for prediction in case["predicted"]
        )
    aggregate = _score_inventory(all_gold, all_predicted)
    body = {
        "schema_version": REPORT_SCHEMA,
        "suite_id": suite_id,
        "suite_sha256": sha256_file(selected),
        "candidate_line": candidate_line,
        "compiler_identity": compiler_identity,
        "scorer_sha256": sha256_file(Path(__file__)),
        "case_count": len(validated),
        **aggregate,
        "per_case": per_case,
        "claim_eligible": False,
        "limitations": limitations,
    }
    if any(
        not math.isfinite(metric) or not 0.0 <= metric <= 1.0
        for metric in body["metrics"].values()
    ):
        raise RuntimeError("typed-compiler scorer produced an invalid metric")
    return {
        **body,
        "report_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a closed typed-compiler benchmark suite")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = score_suite(args.suite)
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        if args.output.exists() or args.output.is_symlink():
            print("typed-compiler output must be a new path", file=sys.stderr)
            return 1
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
