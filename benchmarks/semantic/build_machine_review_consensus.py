from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.semantic.review_gold import validate_candidate
from deeplaw.util import canonical_json

AUDITOR_ROLES = (
    "semantic_gold_formal_auditor",
    "claim_entailment_auditor",
    "retrieval_completeness_auditor",
    "chinese_adversarial_auditor",
    "authority_security_auditor",
    "reproducibility_release_auditor",
)
CASE_IDS = tuple(f"semantic-case-{index:02d}" for index in range(1, 16))
CHALLENGE_TYPES = {
    "prompt_injection",
    "unsupported_authoritative_claim",
    "restricted_disclosure",
    "unauthorized_mutation",
    "silent_fallback",
}
HUMAN_REVIEW_POLICY = {
    "status": "not_required",
    "reason": "owner-approved deterministic machine-consensus release scope",
}
TRANSLATION_IDENTITY_VERSION = "deeplaw-owner-review-translation/1"
CANDIDATE_VERSION = "0.12.0"


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"review artifact must be one JSON object: {path.name}")
    return value


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def candidate_binding(repository: Path) -> dict[str, Any]:
    candidate = _load(repository / "benchmarks/semantic/semantic-gold-candidate-v1.json")
    freeze = _load(repository / "benchmarks/semantic/semantic-gold-freeze-v1.json")
    version = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]
    binding = {
        "commit": _git(repository, "rev-parse", "HEAD"),
        "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "version": version,
        "gold_canonical_sha256": validate_candidate(
            candidate,
            repository=repository,
        ),
        "fixture_manifest_sha256": freeze["fixture_manifest_sha256"],
        "semantic_gold_schema_sha256": _file_sha256(
            repository / "contracts/semantic-gold.v1.schema.json"
        ),
        "semantic_gold_freeze_sha256": _file_sha256(
            repository / "benchmarks/semantic/semantic-gold-freeze-v1.json"
        ),
    }
    if binding["version"] != CANDIDATE_VERSION:
        raise ValueError(
            f"machine review candidate version must be {CANDIDATE_VERSION}"
        )
    return binding


def _validate_schema(repository: Path, name: str, value: dict[str, Any]) -> None:
    schema = _load(repository / "contracts" / name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    ).validate(value)


def _without_digest(value: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: item for name, item in value.items() if name != key}


def case_evidence_sha256(case: dict[str, Any]) -> str:
    return _sha256(
        {
            key: value
            for key, value in case.items()
            if key not in {"recommendation", "discrepancy", "evidence_sha256"}
        }
    )


def challenge_evidence_sha256(challenge: dict[str, Any]) -> str:
    return _sha256(_without_digest(challenge, "evidence_sha256"))


def packet_evidence_sha256(packet: dict[str, Any]) -> str:
    return _sha256(
        {
            "auditor_role": packet["auditor_role"],
            "auditor_identity": packet["auditor_identity"],
            "isolation": packet["isolation"],
            "candidate_binding": packet["candidate_binding"],
            "case_evidence_sha256": [
                item["evidence_sha256"] for item in packet["cases"]
            ],
            "security_evidence_sha256": [
                item["evidence_sha256"] for item in packet["security_challenges"]
            ],
            "commands": packet["commands"],
        }
    )


def _assert_public(value: dict[str, Any]) -> None:
    payload = canonical_json(value)
    forbidden = (
        "/Users/",
        "/private/",
        "/home/",
        "C:\\Users\\",
        "PRIVATE KEY",
        "api_key",
    )
    if any(marker.casefold() in payload.casefold() for marker in forbidden):
        raise ValueError("review packet contains private paths or secret material")


def validate_packet(
    packet: dict[str, Any],
    *,
    repository: Path,
    binding: dict[str, Any],
) -> None:
    _validate_schema(repository, "semantic-machine-review-packet.v1.schema.json", packet)
    _assert_public(packet)
    if packet["candidate_binding"] != binding:
        raise ValueError("machine review packet does not bind the exact candidate")
    expected_digest = _sha256(_without_digest(packet, "packet_sha256"))
    if packet["packet_sha256"] != expected_digest:
        raise ValueError("machine review packet digest is invalid")
    candidate = _load(repository / "benchmarks/semantic/semantic-gold-candidate-v1.json")
    candidate_cases = {item["case_id"]: item for item in candidate["cases"]}
    if {item["case_id"] for item in packet["cases"]} != set(CASE_IDS):
        raise ValueError("machine review packet does not cover all 15 cases")
    if len({item["case_id"] for item in packet["cases"]}) != len(packet["cases"]):
        raise ValueError("machine review packet repeats a semantic case")
    chinese_review = packet["auditor_role"] == "chinese_adversarial_auditor"
    for case in packet["cases"]:
        gold_case = candidate_cases[case["case_id"]]
        canonical_query = gold_case["query"]
        chinese_variants = gold_case.get("query_variants", [])
        expected_review_query = (
            chinese_variants[0]["query"]
            if chinese_review and chinese_variants
            else canonical_query
        )
        if (
            case["canonical_query_sha256"]
            != hashlib.sha256(canonical_query.encode("utf-8")).hexdigest()
            or case["query_language"] != ("zh-CN" if chinese_review else "en")
            or case["frozen_query"] != expected_review_query
        ):
            raise ValueError("machine review packet query binding is invalid")
        if (
            case["query_plan_sha256"] != _sha256(case["query_plan"])
            or case["evidence_sha256"] != case_evidence_sha256(case)
        ):
            raise ValueError("machine review case evidence digest is invalid")
        commands = "\n".join(case["commands"])
        if not all(
            operation in commands
            for operation in (
                "knowledge query",
                "knowledge context",
                "knowledge verify-capsule",
            )
        ):
            raise ValueError("machine review case did not execute every first-party CLI seam")
        expected_content = [
            assertion["statement"]
            for item in gold_case["expected_objects"]
            for assertion in item.get("content_assertions", [])
        ]
        if (
            case["recommendation"] == "CONFIRM"
            and gold_case["expected_objects"]
            and (
                not case["expected_stable_ids"]
                or not set(case["expected_stable_ids"]).issubset(
                    case["actual_stable_ids"]
                )
                or not case["citations"]
                or case["extraction_completeness"] != 1
                or case["source_coverage"] != 1
            )
        ):
            raise ValueError("CONFIRM case lacks complete stable-ID or citation evidence")
        if (
            case["recommendation"] == "CONFIRM"
            and expected_content
            and (
                not set(case["expected_claims"]).issubset(case["actual_claims"])
                or not set(expected_content).issubset(case["expected_claims"])
                or case["claim_entailment"] != "entailed"
            )
        ):
            raise ValueError("CONFIRM case lacks claim-level entailment evidence")
    if {item["challenge_type"] for item in packet["security_challenges"]} != (
        CHALLENGE_TYPES
    ):
        raise ValueError("machine review packet security challenge inventory is incomplete")
    if any(
        challenge["evidence_sha256"] != challenge_evidence_sha256(challenge)
        for challenge in packet["security_challenges"]
    ):
        raise ValueError("machine review security evidence digest is invalid")
    if packet["evidence_sha256"] != packet_evidence_sha256(packet):
        raise ValueError("machine review packet evidence digest is invalid")
    if packet["status"] == "CONFIRM":
        if any(
            item["recommendation"] != "CONFIRM" or item["discrepancy"] is not None
            for item in packet["cases"]
        ):
            raise ValueError("CONFIRM packet contains a rejected or discrepant case")
        if any(
            not item["passed"] or item["failure_count"] != 0
            for item in packet["security_challenges"]
        ):
            raise ValueError("CONFIRM packet contains a failed security challenge")
        if any(
            not citation["valid"]
            for case in packet["cases"]
            for citation in case["citations"]
        ):
            raise ValueError("CONFIRM packet contains an invalid citation")


def build_consensus(
    packets: list[dict[str, Any]],
    *,
    repository: Path,
) -> dict[str, Any]:
    binding = candidate_binding(repository)
    if len(packets) != len(AUDITOR_ROLES):
        raise ValueError("machine consensus requires exactly six packets")
    roles = [packet["auditor_role"] for packet in packets]
    if set(roles) != set(AUDITOR_ROLES) or len(set(roles)) != len(roles):
        raise ValueError("machine consensus requires six distinct auditor roles")
    for packet in packets:
        validate_packet(packet, repository=repository, binding=binding)
    if any(packet["status"] != "CONFIRM" for packet in packets):
        raise ValueError("machine consensus cannot use RETURN_FOR_FIX or BLOCKED packets")
    cases = [
        {
            "case_id": case_id,
            "auditor_confirmations": sum(
                1
                for packet in packets
                for case in packet["cases"]
                if case["case_id"] == case_id and case["recommendation"] == "CONFIRM"
            ),
            "discrepancy": None,
        }
        for case_id in CASE_IDS
    ]
    if any(item["auditor_confirmations"] != 6 for item in cases):
        raise ValueError("machine reviewers did not independently confirm every case")
    consensus = {
        "schema_version": "deeplaw.semantic-machine-review-consensus/v1",
        "machine_review_consensus": "confirmed",
        "candidate_binding": binding,
        "auditor_packets": [
            {
                "auditor_role": role,
                "packet_sha256": packet["packet_sha256"],
                "evidence_sha256": packet["evidence_sha256"],
                "status": packet["status"],
            }
            for role in AUDITOR_ROLES
            for packet in packets
            if packet["auditor_role"] == role
        ],
        "cases": cases,
        "material_divergence": False,
        "human_gold_review": HUMAN_REVIEW_POLICY,
        "maintainer_confirmed": False,
        "reviewer_id": None,
        "independent_machine_review": {
            "status": "confirmed",
            "auditor_count": 6,
            "unanimity_required": True,
        },
        "external_real_model_semantic_execution": "not_executed",
        "competitive_claim_eligible": False,
    }
    consensus["consensus_sha256"] = _sha256(consensus)
    _validate_schema(
        repository,
        "semantic-machine-review-consensus.v1.schema.json",
        consensus,
    )
    return consensus


def _owner_case(
    *,
    gold_case: dict[str, Any],
    packets: list[dict[str, Any]],
    language: str,
) -> dict[str, Any]:
    reviews = [
        case
        for packet in packets
        for case in packet["cases"]
        if case["case_id"] == gold_case["case_id"]
    ]
    query_plan_sha256 = _sha256(sorted(item["query_plan_sha256"] for item in reviews))
    expected_ids = sorted(
        {
            stable_id
            for review in reviews
            for stable_id in review["expected_stable_ids"]
        }
    )
    actual_ids = sorted(
        {
            stable_id
            for review in reviews
            for stable_id in review["actual_stable_ids"]
        }
    )
    citation_count = sum(len(item["citations"]) for item in reviews)
    valid_citation_count = sum(
        1 for item in reviews for citation in item["citations"] if citation["valid"]
    )
    review_purpose = (
        f"独立机器审核：{gold_case['task_type']}"
        if language == "zh-CN"
        else f"Independent machine review: {gold_case['task_type']}"
    )
    chinese_review = next(
        case
        for packet in packets
        if packet["auditor_role"] == "chinese_adversarial_auditor"
        for case in packet["cases"]
        if case["case_id"] == gold_case["case_id"]
    )
    citations = sorted(
        {
            canonical_json(citation)
            for review in reviews
            for citation in review["citations"]
        }
    )
    expected_claims = sorted(
        {
            claim
            for review in reviews
            for claim in review["expected_claims"]
        }
    )
    actual_claims = sorted(
        {
            claim
            for review in reviews
            for claim in review["actual_claims"]
        }
    )
    query_plans = {
        item["query_plan_sha256"]: item["query_plan"] for item in reviews
    }
    return {
        "case_id": gold_case["case_id"],
        "review_purpose": review_purpose,
        "frozen_query": (
            chinese_review["frozen_query"]
            if language == "zh-CN"
            else gold_case["query"]
        ),
        "expected_result": {
            "stable_ids": expected_ids,
            "claims": expected_claims,
            "required_outcomes": gold_case["required_outcomes"],
        },
        "actual_result": {
            "stable_ids": actual_ids,
            "claims": actual_claims,
            "independent_confirmations": 6,
        },
        "citation_summary": {
            "citation_count": citation_count,
            "valid_citation_count": valid_citation_count,
            "citations": [json.loads(item) for item in citations],
        },
        "query_plan": {
            "independent_plan_count": len(query_plans),
            "plans_by_sha256": query_plans,
        },
        "query_plan_sha256": query_plan_sha256,
        "metric_result": {
            "claim_entailment_confirmations": sum(
                item["claim_entailment"] in {"entailed", "not_applicable"}
                for item in reviews
            ),
            "minimum_source_coverage": min(item["source_coverage"] for item in reviews),
        },
        "discrepancy": None,
        "machine_consensus": "CONFIRM",
    }


def build_owner_packet(
    *,
    language: str,
    consensus: dict[str, Any],
    packets: list[dict[str, Any]],
    candidate: dict[str, Any],
    counterpart_packet_sha256: str | None,
    repository: Path,
) -> dict[str, Any]:
    packet = {
        "schema_version": "deeplaw.semantic-owner-review-packet/v1",
        "artifact_class": "derived_artifact",
        "language": language,
        "candidate_binding": consensus["candidate_binding"],
        "canonical_gold_sha256": consensus["candidate_binding"][
            "gold_canonical_sha256"
        ],
        "machine_review_consensus_sha256": consensus["consensus_sha256"],
        "counterpart_packet_sha256": counterpart_packet_sha256,
        "translation_identity_version": TRANSLATION_IDENTITY_VERSION,
        "cases": [
            _owner_case(gold_case=case, packets=packets, language=language)
            for case in candidate["cases"]
        ],
        "human_final_decision": "not_required",
        "maintainer_confirmed": False,
        "reviewer_id": None,
    }
    packet["packet_sha256"] = _sha256(packet)
    _validate_schema(repository, "semantic-owner-review-packet.v1.schema.json", packet)
    return packet


def _markdown_owner_packet(packet: dict[str, Any]) -> str:
    chinese = packet["language"] == "zh-CN"
    title = "DeepLaw Owner 语义审核包" if chinese else "DeepLaw Owner Semantic Review Packet"
    lines = [
        f"# {title}",
        "",
        f"- commit: `{packet['candidate_binding']['commit']}`",
        f"- tree: `{packet['candidate_binding']['tree']}`",
        f"- canonical Gold SHA-256: `{packet['canonical_gold_sha256']}`",
        f"- machine consensus SHA-256: `{packet['machine_review_consensus_sha256']}`",
        "- human final decision: `not_required`",
        "- maintainer_confirmed: `false`",
        "- reviewer_id: `null`",
        "",
        "| Case | Purpose | Query | Citations | Consensus |",
        "|---|---|---|---:|---|",
    ]
    for case in packet["cases"]:
        query = case["frozen_query"].replace("|", "\\|").replace("\n", " ")
        purpose = case["review_purpose"].replace("|", "\\|")
        lines.append(
            f"| {case['case_id']} | {purpose} | {query} | "
            f"{case['citation_summary']['valid_citation_count']}/"
            f"{case['citation_summary']['citation_count']} | CONFIRM |"
        )
    lines.extend(("", f"Packet SHA-256: `{packet['packet_sha256']}`", ""))
    return "\n".join(lines)


def _markdown_consensus(consensus: dict[str, Any]) -> str:
    lines = [
        "# DeepLaw Semantic Machine Review Consensus",
        "",
        f"- exact commit: `{consensus['candidate_binding']['commit']}`",
        f"- exact tree: `{consensus['candidate_binding']['tree']}`",
        "- consensus: `confirmed`",
        "- independent auditors: `6`",
        "- material divergence: `false`",
        "- human_gold_review: `not_required`",
        "- external_real_model_semantic_execution: `not_executed`",
        "- competitive_claim_eligible: `false`",
        "",
    ]
    lines.extend(
        f"- {item['auditor_role']}: `{item['packet_sha256']}`"
        for item in consensus["auditor_packets"]
    )
    lines.extend(("", f"Consensus SHA-256: `{consensus['consensus_sha256']}`", ""))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate six isolated machine reviews and build unanimous consensus artifacts."
    )
    parser.add_argument("--packet", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    repository = _repository()
    packets = [_load(path) for path in arguments.packet]
    consensus = build_consensus(packets, repository=repository)
    candidate = _load(repository / "benchmarks/semantic/semantic-gold-candidate-v1.json")
    english = build_owner_packet(
        language="en",
        consensus=consensus,
        packets=packets,
        candidate=candidate,
        counterpart_packet_sha256=None,
        repository=repository,
    )
    chinese = build_owner_packet(
        language="zh-CN",
        consensus=consensus,
        packets=packets,
        candidate=candidate,
        counterpart_packet_sha256=english["packet_sha256"],
        repository=repository,
    )
    output = arguments.output.expanduser().absolute()
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "machine-review-consensus.json": canonical_json(consensus) + "\n",
        "machine-review-consensus.md": _markdown_consensus(consensus),
        "owner-review-packet.en.json": canonical_json(english) + "\n",
        "owner-review-packet.en.md": _markdown_owner_packet(english),
        "owner-review-packet.zh-CN.json": canonical_json(chinese) + "\n",
        "owner-review-packet.zh-CN.md": _markdown_owner_packet(chinese),
    }
    for name, content in artifacts.items():
        (output / name).write_text(content, encoding="utf-8")
    print(
        canonical_json(
            {
                "consensus": consensus["consensus_sha256"],
                "english_owner_packet": english["packet_sha256"],
                "chinese_owner_packet": chinese["packet_sha256"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
