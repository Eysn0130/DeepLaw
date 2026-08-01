from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.semantic.review_gold import validate_candidate
from deeplaw.util import canonical_json, sha256_bytes, stable_id


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def build_packet(
    *,
    gold: dict[str, Any],
    compiler_report: dict[str, Any],
    query_report: dict[str, Any],
) -> dict[str, Any]:
    gold_sha256 = validate_candidate(gold, repository=_repository())
    schema = _load(_repository() / "contracts" / "semantic-query-run.v1.schema.json")
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(query_report)
    if query_report["gold_id"] != gold["gold_id"]:
        raise ValueError("human review query report does not bind Gold")
    if (
        query_report["gold_sha256"] != gold_sha256
        or query_report["fixture_manifest_sha256"] != gold["fixture_manifest_sha256"]
    ):
        raise ValueError("human review query report does not bind exact frozen Gold bytes")
    if query_report["compiler_report_id"] != compiler_report.get("report_id"):
        raise ValueError("human review query report does not bind compiler evidence")
    actual_by_case = {item["case_id"]: item for item in query_report["cases"]}
    cases = []
    for case in gold["cases"]:
        actual = actual_by_case[case["case_id"]]
        metrics = {
            key: actual[key]
            for key in (
                "recall_at_k",
                "target_scoped_precision_at_k",
                "reciprocal_rank",
                "ndcg_at_k",
                "citation_validity",
                "claim_evidence_binding_accuracy",
                "cold_latency_ms",
                "warm_latency_ms",
                "provider_payload_bytes",
                "context_provider_payload_bytes",
                "repeat_reused",
            )
        }
        cases.append(
            {
                "case_id": case["case_id"],
                "review_purpose": case["task_type"],
                "frozen_query": case["query"],
                "expected_result": {
                    "objects": case["expected_objects"],
                    "required_outcomes": case["required_outcomes"],
                    "expected_sequence": case.get("expected_sequence", []),
                    "query_phase": case["query_phase"],
                },
                "actual_retrieved_result": {
                    "status": actual["status"],
                    "objects": actual["actual_objects"],
                    "selected_source_revision_ids": actual[
                        "selected_source_revision_ids"
                    ],
                    "gap_codes": actual["gap_codes"],
                },
                "expected_object_stable_ids": [
                    item["label_id"] for item in case["expected_objects"]
                ],
                "actual_object_stable_ids": [
                    item["knowledge_id"] for item in actual["actual_objects"]
                ],
                "citations": actual["citation_checks"],
                "claim_evidence_checks": actual["claim_evidence_checks"],
                "query_plan": actual["query_plan"],
                "query_plan_sha256": actual["query_plan_sha256"],
                "metrics": metrics,
                "discrepancy": actual["failure_reason"],
                "machine_precheck_recommendation": (
                    "CONFIRM" if actual["status"] == "passed" else "RETURN_FOR_FIX"
                ),
                "independent_auditor_recommendation": None,
                "human_final_decision": None,
            }
        )
    challenge_failures = [
        item["challenge_id"]
        for item in query_report["challenges"]
        if item["status"] != "passed"
    ]
    body = {
        "schema_version": "deeplaw.semantic-human-review-packet/v1",
        "status": "awaiting_independent_audit_and_owner_decision",
        "gold_id": gold["gold_id"],
        "gold_status": gold["status"],
        "gold_sha256": gold_sha256,
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "compiler_report_id": compiler_report["report_id"],
        "compiler_report_sha256": sha256_bytes(
            canonical_json(compiler_report).encode("utf-8")
        ),
        "query_report_id": query_report["report_id"],
        "query_report_sha256": sha256_bytes(
            canonical_json(query_report).encode("utf-8")
        ),
        "scoring_definitions": gold["scoring_policy"],
        "aggregate_metrics": query_report["metrics"],
        "security_challenges": query_report["challenges"],
        "challenge_failure_ids": challenge_failures,
        "cases": cases,
        "independent_auditor_identity": None,
        "independent_auditor_overall_recommendation": None,
        "human_reviewer_id": None,
        "human_final_decision": None,
        "competitive_claim_eligible": False,
    }
    packet_id = stable_id(
        "semantichumanreview",
        gold["gold_id"],
        compiler_report["report_id"],
        query_report["report_id"],
    )
    digest_body = {"packet_id": packet_id, **body}
    packet = {
        **digest_body,
        "packet_sha256": sha256_bytes(canonical_json(digest_body).encode("utf-8")),
    }
    packet_schema = _load(
        _repository() / "contracts" / "semantic-human-review-packet.v1.schema.json"
    )
    Draft202012Validator.check_schema(packet_schema)
    Draft202012Validator(packet_schema).validate(packet)
    return packet


def _render_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# Semantic Gold Human Review Packet",
        "",
        f"- Packet: `{packet['packet_id']}`",
        f"- Gold: `{packet['gold_id']}` / `{packet['gold_sha256']}`",
        f"- Fixture manifest: `{packet['fixture_manifest_sha256']}`",
        f"- Status: `{packet['status']}`",
        "- Human final decision: **UNSET**",
        "- Competitive claim eligible: `false`",
        "",
        "| Case | Purpose | Actual IDs | Recall@K | Target precision@K | "
        "Citation validity | Precheck | Auditor | Human |",
        "|---|---|---|---:|---:|---:|---|---|---|",
    ]
    for case in packet["cases"]:
        actual_ids = "<br>".join(f"`{item}`" for item in case["actual_object_stable_ids"])
        lines.append(
            "| {case_id} | {purpose} | {actual_ids} | {recall} | {precision} | "
            "{citations} | {precheck} | UNSET | UNSET |".format(
                case_id=case["case_id"],
                purpose=case["review_purpose"],
                actual_ids=actual_ids or "—",
                recall=case["metrics"]["recall_at_k"],
                precision=case["metrics"]["target_scoped_precision_at_k"],
                citations=case["metrics"]["citation_validity"],
                precheck=case["machine_precheck_recommendation"],
            )
        )
    lines.extend(
        [
            "",
            "The JSON companion contains each frozen query, expected result, actual object, "
            "exact citation, Query Plan, metrics, discrepancy, and the deliberately unset "
            "independent-auditor and human-decision fields.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a source-free 15-case Semantic Gold owner review packet."
    )
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--compiler-report", type=Path, required=True)
    parser.add_argument("--query-report", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output_directory
    if output.exists() or output.is_symlink():
        raise FileExistsError("human review packet output must be a new directory")
    packet = build_packet(
        gold=_load(arguments.gold),
        compiler_report=_load(arguments.compiler_report),
        query_report=_load(arguments.query_report),
    )
    output.mkdir(parents=True)
    (output / "human-review-packet.json").write_text(
        canonical_json(packet) + "\n", encoding="utf-8"
    )
    (output / "human-review-packet.md").write_text(
        _render_markdown(packet), encoding="utf-8"
    )
    print(canonical_json({"packet_id": packet["packet_id"], "sha256": packet["packet_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
