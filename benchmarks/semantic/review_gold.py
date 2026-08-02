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

CANONICAL_JSON_PROFILE = (
    "UTF-8 JSON; object keys sorted recursively; ensure_ascii=false; "
    "separators=(',', ':'); no trailing whitespace"
)
QUERY_SET_PROJECTION = (
    "Map cases in array order to objects {case_id, query, purpose, "
    "phase=query_phase, as_of=case.as_of or null, variants=case.query_variants or []}; "
    "serialize with "
    "canonical_json_profile; SHA-256 the exact UTF-8 bytes"
)
COMMITMENT_PROFILES = {
    "candidate_sha256": (
        "Deep-copy the complete candidate; set status to machine_review_pending and "
        "review to null; serialize with canonical_json_profile; SHA-256 the exact UTF-8 "
        "bytes"
    ),
    "fixture_manifest_sha256": (
        "For each candidate source in array order, verify bytes_sha256 equals SHA-256 of "
        "the exact fixture bytes; concatenate the lowercase 64-character bytes_sha256 "
        "values without separators as ASCII; SHA-256 those exact ASCII bytes"
    ),
    "semantic_gold_schema_sha256": (
        "SHA-256 the exact bytes of contracts/semantic-gold.v1.schema.json with no text "
        "normalization"
    ),
    "query_set_sha256": (
        "Apply query_set_projection to the complete candidate; serialize with "
        "canonical_json_profile; SHA-256 the exact UTF-8 bytes"
    ),
    "scoring_policy_sha256": (
        "Serialize candidate.scoring_policy with canonical_json_profile; SHA-256 the "
        "exact UTF-8 bytes"
    ),
    "security_challenges_sha256": (
        "Serialize candidate.security_challenges in array order with "
        "canonical_json_profile; SHA-256 the exact UTF-8 bytes"
    ),
}


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _schema() -> dict[str, Any]:
    value = json.loads(
        (_repository() / "contracts/semantic-gold.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(value)
    return value


def _validate_schema(value: dict[str, Any]) -> None:
    Draft202012Validator(
        _schema(),
        format_checker=FormatChecker(),
    ).validate(value)


def _candidate_digest(value: dict[str, Any]) -> str:
    candidate = deepcopy(value)
    candidate["status"] = "machine_review_pending"
    candidate["review"] = None
    return hashlib.sha256(canonical_json(candidate).encode("utf-8")).hexdigest()


def query_set_projection(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact, ordered public projection bound by query_set_sha256."""

    return [
        {
            "case_id": item["case_id"],
            "query": item["query"],
            "purpose": item["purpose"],
            "phase": item["query_phase"],
            "as_of": item.get("as_of"),
            "variants": item.get("query_variants", []),
        }
        for item in value["cases"]
    ]


def query_set_sha256(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(query_set_projection(value)).encode("utf-8")
    ).hexdigest()


def validate_candidate(value: dict[str, Any], *, repository: Path) -> str:
    _validate_schema(value)
    if value["status"] == "maintainer_confirmed" and value["review"] is None:
        raise ValueError("maintainer-confirmed Semantic Gold requires review metadata")
    policy = value["release_review_policy"]
    if (
        value["status"] == "machine_review_pending"
        and (
            value["review"] is not None
            or policy["human_gold_review"]["status"] != "not_required"
            or policy["maintainer_confirmed"] is not False
            or policy["reviewer_id"] is not None
            or policy["external_real_model_semantic_execution"] != "not_executed"
            or policy["competitive_claim_eligible"] is not False
        )
    ):
        raise ValueError("machine-review Semantic Gold policy is inconsistent")
    source_keys = [source["source_key"] for source in value["sources"]]
    if len(source_keys) != len(set(source_keys)):
        raise ValueError("Semantic Gold source_key values must be unique")
    case_ids = [case["case_id"] for case in value["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Semantic Gold case_id values must be unique")
    task_types = [case["task_type"] for case in value["cases"]]
    if len(task_types) != len(set(task_types)):
        raise ValueError("Semantic Gold must contain one case per task_type")
    known_sources = set(source_keys)
    challenge_types = [item["challenge_type"] for item in value["security_challenges"]]
    if len(challenge_types) != len(set(challenge_types)):
        raise ValueError("Semantic Gold security challenge types must be unique")
    required_challenges = {
        "prompt_injection",
        "unsupported_authoritative_claim",
        "restricted_disclosure",
        "unauthorized_mutation",
        "silent_fallback",
    }
    if set(challenge_types) != required_challenges:
        raise ValueError("Semantic Gold security challenge inventory is incomplete")
    hashes: list[str] = []
    for source in value["sources"]:
        relative_path = Path(source["relative_path"])
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("Semantic Gold fixture paths must be repository-relative")
        fixture = (repository / relative_path).resolve(strict=True)
        fixture.relative_to(repository.resolve(strict=True))
        actual = hashlib.sha256(fixture.read_bytes()).hexdigest()
        if actual != source["bytes_sha256"]:
            raise ValueError(
                f"fixture hash mismatch for source_key={source['source_key']}"
            )
        hashes.append(actual)
    manifest = hashlib.sha256("".join(hashes).encode("ascii")).hexdigest()
    if manifest != value["fixture_manifest_sha256"]:
        raise ValueError("Semantic Gold fixture manifest digest does not match")
    for case in value["cases"]:
        if not set(case["source_keys"]).issubset(known_sources):
            raise ValueError(f"unknown source_key in case_id={case['case_id']}")
        labels = {
            expected["label_id"] for expected in case["expected_objects"]
        }
        for left, right in case["forbidden_merges"]:
            if left not in labels or right not in labels:
                raise ValueError(
                    f"forbidden merge references an unknown label in {case['case_id']}"
                )
        for expected in case["expected_objects"]:
            for assertion in expected.get("content_assertions", []):
                if not set(assertion["source_keys"]).issubset(set(case["source_keys"])):
                    raise ValueError(
                        f"content assertion source is outside case_id={case['case_id']}"
                    )
        relation_ids: set[str] = set()
        for relation in case.get("expected_relations", []):
            if relation["relation_id"] in relation_ids:
                raise ValueError(
                    f"duplicate relation expectation in case_id={case['case_id']}"
                )
            relation_ids.add(relation["relation_id"])
            if relation["subject_label_id"] == relation["object_label_id"]:
                raise ValueError(
                    f"relation expectation cannot be a self-edge in case_id={case['case_id']}"
                )
            if {
                relation["subject_label_id"], relation["object_label_id"]
            } - labels:
                raise ValueError(
                    f"relation expectation references an unknown label in {case['case_id']}"
                )
            if not set(relation["source_keys"]).issubset(set(case["source_keys"])):
                raise ValueError(
                    f"relation expectation source is outside case_id={case['case_id']}"
                )
            if relation["valid_from"] >= relation["valid_to"]:
                raise ValueError(
                    f"relation expectation interval is invalid in case_id={case['case_id']}"
                )
            endpoints = {
                relation["subject_label_id"],
                relation["object_label_id"],
            }
            if not any(set(pair) == endpoints for pair in case["forbidden_merges"]):
                raise ValueError(
                    "relation expectation endpoints require an explicit forbidden merge "
                    f"in case_id={case['case_id']}"
                )
        if case.get("expected_relations") and "contradiction_preserved" not in case[
            "required_outcomes"
        ]:
            raise ValueError(
                f"relation expectation requires contradiction_preserved in {case['case_id']}"
            )
    for challenge in value["security_challenges"]:
        if not set(challenge["source_keys"]).issubset(known_sources):
            raise ValueError(
                f"unknown source_key in challenge_id={challenge['challenge_id']}"
            )
    identity_groups: dict[str, int] = {}
    for case in value["cases"]:
        for expected in case["expected_objects"]:
            group = expected.get("stable_identity_group")
            if group is not None:
                identity_groups[group] = identity_groups.get(group, 0) + 1
    if any(count < 2 for count in identity_groups.values()):
        raise ValueError("Semantic Gold stable identity groups must span at least two labels")
    digest = _candidate_digest(value)
    if value["status"] == "maintainer_confirmed":
        assert value["review"] is not None
        if value["review"]["gold_sha256"] != digest:
            raise ValueError("maintainer review digest does not bind the candidate labels")
    return digest


def validate_freeze(
    freeze: dict[str, Any],
    *,
    candidate: dict[str, Any],
    repository: Path,
) -> None:
    schema = json.loads(
        (repository / "contracts" / "semantic-gold-freeze.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(freeze)
    candidate_sha256 = validate_candidate(candidate, repository=repository)
    expected = {
        "gold_id": candidate["gold_id"],
        "gold_status": candidate["status"],
        "candidate_sha256": candidate_sha256,
        "fixture_manifest_sha256": candidate["fixture_manifest_sha256"],
        "semantic_gold_schema_sha256": hashlib.sha256(
            (repository / "contracts" / "semantic-gold.v1.schema.json").read_bytes()
        ).hexdigest(),
        "query_set_sha256": query_set_sha256(candidate),
        "scoring_policy_sha256": hashlib.sha256(
            canonical_json(candidate["scoring_policy"]).encode("utf-8")
        ).hexdigest(),
        "security_challenges_sha256": hashlib.sha256(
            canonical_json(candidate["security_challenges"]).encode("utf-8")
        ).hexdigest(),
        "source_count": len(candidate["sources"]),
        "case_count": len(candidate["cases"]),
        "security_challenge_count": len(candidate["security_challenges"]),
        "human_gold_review": candidate["release_review_policy"]["human_gold_review"],
        "maintainer_confirmation_required": False,
        "maintainer_confirmed": False,
        "reviewer_id": None,
        "machine_review_consensus_required": True,
        "external_real_model_semantic_execution": "not_executed",
        "competitive_claim_eligible": False,
        "canonical_json_profile": CANONICAL_JSON_PROFILE,
        "query_set_projection": QUERY_SET_PROJECTION,
        "commitment_profiles": COMMITMENT_PROFILES,
    }
    if freeze != {"schema_version": freeze["schema_version"], **expected}:
        raise ValueError("Semantic Gold freeze manifest does not bind the exact candidate")


def confirm_candidate(
    value: dict[str, Any],
    *,
    repository: Path,
    reviewer_id: str,
    reason: str,
    reviewed_at: str | None = None,
) -> dict[str, Any]:
    if value.get("status") == "machine_review_pending":
        raise ValueError(
            "owner-approved machine-consensus scope does not accept maintainer confirmation"
        )
    if value["status"] != "maintainer_review_pending" or value["review"] is not None:
        raise ValueError("only an unreviewed candidate can be confirmed")
    digest = validate_candidate(value, repository=repository)
    reviewer_id = reviewer_id.strip()
    reason = reason.strip()
    if not reviewer_id or not reason:
        raise ValueError("reviewer_id and reason must be non-empty")
    confirmed = deepcopy(value)
    confirmed["status"] = "maintainer_confirmed"
    confirmed["review"] = {
        "reviewer_id": reviewer_id,
        "reviewed_at": reviewed_at
        or datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "reason": reason,
        "gold_sha256": digest,
    }
    validate_candidate(confirmed, repository=repository)
    return confirmed


def _render(value: dict[str, Any]) -> str:
    return canonical_json(value) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the public Semantic Gold candidate or record an explicit "
            "maintainer confirmation. This tool never infers approval."
        )
    )
    parser.add_argument(
        "gold",
        nargs="?",
        type=Path,
        default=Path("benchmarks/semantic/semantic-gold-candidate-v1.json"),
    )
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--reviewer-id")
    parser.add_argument("--reason")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    repository = _repository()
    gold_path = arguments.gold
    if not gold_path.is_absolute():
        gold_path = repository / gold_path
    value = json.loads(gold_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        parser.error("Semantic Gold must contain one JSON object")
    digest = validate_candidate(value, repository=repository)
    if arguments.confirm:
        if not arguments.reviewer_id or not arguments.reason or arguments.output is None:
            parser.error(
                "--confirm requires --reviewer-id, --reason and an explicit --output"
            )
        value = confirm_candidate(
            value,
            repository=repository,
            reviewer_id=arguments.reviewer_id,
            reason=arguments.reason,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(_render(value), encoding="utf-8")
        return 0
    print(
        canonical_json(
            {
                "gold_id": value["gold_id"],
                "status": value["status"],
                "candidate_sha256": digest,
                "source_count": len(value["sources"]),
                "case_count": len(value["cases"]),
                "maintainer_confirmation_required": (
                    value["status"] == "maintainer_review_pending"
                ),
                "machine_review_consensus_required": (
                    value["status"] == "machine_review_pending"
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
