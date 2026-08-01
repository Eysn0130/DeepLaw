from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.util import canonical_json


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _schema() -> dict[str, Any]:
    value = json.loads(
        (_repository() / "contracts/legal-held-out-gold.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(value)
    return value


def _candidate_digest(value: dict[str, Any]) -> str:
    candidate = deepcopy(value)
    candidate["status"] = "expert_review_pending"
    candidate["review"] = None
    return hashlib.sha256(canonical_json(candidate).encode("utf-8")).hexdigest()


def validate_candidate(value: dict[str, Any]) -> str:
    Draft202012Validator(
        _schema(), format_checker=FormatChecker()
    ).validate(value)
    case_ids = [case["case_id"] for case in value["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("legal held-out case_id values must be unique")
    development = set(value["split"]["development_case_ids"])
    held_out = set(value["split"]["held_out_case_ids"])
    if development & held_out or development | held_out != set(case_ids):
        raise ValueError("development and held-out splits must be disjoint and exhaustive")
    digest = _candidate_digest(value)
    if value["status"] == "expert_confirmed":
        review = value["review"]
        if review is None:
            raise ValueError("expert-confirmed Gold requires review metadata")
        if review["gold_sha256"] != digest:
            raise ValueError("expert review digest does not bind the candidate labels")
        for case in value["cases"]:
            expected = case["expected"]
            if case["answerability"] == "duty_evidence_available" and any(
                expected[field] is None
                for field in (
                    "release_id",
                    "segment_id",
                    "source_sha256",
                    "segment_sha256",
                )
            ):
                raise ValueError(
                    "expert-confirmed available cases require exact release, segment and hashes"
                )
    elif value["review"] is not None:
        raise ValueError("unconfirmed legal Gold cannot contain review metadata")
    return digest


def confirm_candidate(
    value: dict[str, Any],
    *,
    reviewer_id: str,
    reviewer_role: str,
    reason: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    if value["status"] != "expert_review_pending" or value["review"] is not None:
        raise ValueError("only an unreviewed legal Gold candidate can be confirmed")
    if reviewer_role not in {"legal_expert", "maintainer_with_legal_review"}:
        raise ValueError("reviewer_role must establish an explicit legal-review basis")
    reviewer_id = reviewer_id.strip()
    reason = reason.strip()
    if not reviewer_id or not reason:
        raise ValueError("reviewer_id and reason must be non-empty")
    digest = validate_candidate(value)
    confirmed = deepcopy(value)
    confirmed["status"] = "expert_confirmed"
    confirmed["review"] = {
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer_role,
        "reviewed_at": reviewed_at
        or datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "reason": reason,
        "gold_sha256": digest,
    }
    validate_candidate(confirmed)
    return confirmed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate legal held-out annotations or record explicit expert review. "
            "This tool never infers or self-assigns expert status."
        )
    )
    parser.add_argument(
        "gold",
        nargs="?",
        type=Path,
        default=Path("benchmarks/legal/held-out-candidate-v1.json"),
    )
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--reviewer-id")
    parser.add_argument(
        "--reviewer-role",
        choices=("legal_expert", "maintainer_with_legal_review"),
    )
    parser.add_argument("--reason")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    path = arguments.gold
    if not path.is_absolute():
        path = _repository() / path
    value = json.loads(path.read_text(encoding="utf-8"))
    digest = validate_candidate(value)
    if arguments.confirm:
        if not all(
            (
                arguments.reviewer_id,
                arguments.reviewer_role,
                arguments.reason,
                arguments.output,
            )
        ):
            parser.error(
                "--confirm requires --reviewer-id, --reviewer-role, --reason and --output"
            )
        value = confirm_candidate(
            value,
            reviewer_id=arguments.reviewer_id,
            reviewer_role=arguments.reviewer_role,
            reason=arguments.reason,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(canonical_json(value) + "\n", encoding="utf-8")
        return 0
    print(
        canonical_json(
            {
                "gold_id": value["gold_id"],
                "status": value["status"],
                "candidate_sha256": digest,
                "case_count": len(value["cases"]),
                "expert_confirmation_required": value["status"] != "expert_confirmed",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
