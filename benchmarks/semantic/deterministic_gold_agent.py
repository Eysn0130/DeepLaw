from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.finalization import (
    CONTENT_DUTY_OBSERVATION_KINDS,
    CONTENT_OUTPUT_DUTIES,
    RELATION_OUTPUT_DUTY,
)
from deeplaw.compilation.profiles import REQUIRED_SEMANTIC_DUTIES, SEMANTIC_DUTIES
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.util import canonical_json, sha256_bytes


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


OBJECT_SPECS: dict[str, list[dict[str, Any]]] = {
    "cross-packet-entity": [
        {
            "kind": "entity",
            "semantic_key": "entity:meridian-research-cooperative",
            "title": "Meridian Research Cooperative",
            "body": (
                "Meridian Research Cooperative is the organization also named MRC, "
                "Meridian Research, and the Cooperative."
            ),
            "aliases": [
                "MRC",
                "Meridian Research",
                "the Cooperative",
                "子午线研究合作社",
                "子午线研究",
                "该合作社",
            ],
        }
    ],
    "ambiguous-entities": [
        {
            "kind": "entity",
            "semantic_key": "entity:jordan-lee:harbor-city-attorney",
            "title": "Jordan Lee, Harbor City attorney",
            "body": "Jordan Lee is the Harbor City attorney who joined North Quay Legal in 2021.",
            "aliases": ["Jordan Lee", "乔丹·李", "港城律师乔丹·李"],
        },
        {
            "kind": "entity",
            "semantic_key": "entity:jordan-lee:summit-city-robotics-engineer",
            "title": "Jordan Lee, Summit City robotics engineer",
            "body": (
                "Jordan Lee is the Summit City robotics engineer who joined Alloy Works in 2022."
            ),
            "aliases": ["Jordan Lee", "乔丹·李", "峰城机器人工程师乔丹·李"],
        },
    ],
    "concept-procedure-events": [
        {
            "kind": "concept",
            "semantic_key": "concept:evidence-admission",
            "title": "Evidence admission",
            "body": (
                "Evidence admission accepts a source only after identity, lifecycle, scope, "
                "sensitivity, and provenance checks succeed. Ranking never establishes Authority. "
                "Its governed specification says to verify the exact Source Revision bytes and "
                "validate every fragment locator and quote hash. Its timeline records that the "
                "admission policy was drafted, locator validation became mandatory, and silent "
                "fallback was prohibited."
            ),
            "aliases": [
                "admission policy",
                "证据准入政策",
                "证据接纳政策",
                "证据接纳规则",
                "证据准入",
            ],
        },
        {
            "kind": "procedure",
            "semantic_key": "procedure:evidence-admission-workflow",
            "title": "Evidence admission workflow",
            "body": (
                "1. Verify the exact Source Revision bytes.\n"
                "2. Check scope and sensitivity.\n"
                "3. Validate every fragment locator and quote hash.\n"
                "4. Admit the source or return an explicit gap."
            ),
            "aliases": ["证据准入工作流", "证据接纳流程", "证据纳入流程"],
            "fragment_contains": "Verify the exact Source Revision bytes",
        },
        {
            "kind": "event",
            "semantic_key": "event:evidence-admission:drafted:2025-01-10",
            "title": "Evidence admission policy drafted on 2025-01-10",
            "body": "The evidence admission policy was drafted on 2025-01-10.",
            "aliases": ["Admission policy drafted", "证据准入政策起草"],
            "valid_from": "2025-01-10T00:00:00Z",
            "fragment_contains": "2025-01-10",
        },
        {
            "kind": "event",
            "semantic_key": "event:locator-validation:mandatory:2025-03-15",
            "title": "Locator validation became mandatory on 2025-03-15",
            "body": "Locator validation became mandatory on 2025-03-15.",
            "aliases": ["Locator validation became mandatory", "定位器验证成为强制要求"],
            "valid_from": "2025-03-15T00:00:00Z",
            "fragment_contains": "2025-03-15",
        },
        {
            "kind": "event",
            "semantic_key": "event:silent-fallback:prohibited:2025-05-20",
            "title": "Silent fallback was prohibited on 2025-05-20",
            "body": "Silent fallback was prohibited on 2025-05-20.",
            "aliases": ["Silent fallback was prohibited", "禁止静默回退"],
            "valid_from": "2025-05-20T00:00:00Z",
            "fragment_contains": "2025-05-20",
        },
    ],
    "retention-a": [
        {
            "kind": "claim",
            "semantic_key": "claim:atlas:public-api-diagnostic-log-retention:policy-a:2026",
            "title": "Diagnostic log retention is 30 days",
            "body": (
                "For the Atlas production service, Policy A requires ordinary diagnostic logs "
                "generated by public API requests in the worldwide tenant population to be "
                "retained for exactly 30 days after collection during 2026. Restricted payloads "
                "are never included in diagnostic logs."
            ),
            "aliases": [
                "Policy A",
                "政策甲",
                "政策A",
                "30天日志留存",
                "Policy A diagnostic log retention",
            ],
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2027-01-01T00:00:00Z",
            "epistemic_state": "contested",
        }
    ],
    "retention-b": [
        {
            "kind": "claim",
            "semantic_key": "claim:atlas:public-api-diagnostic-log-retention:policy-b:2026",
            "title": "Diagnostic log retention is 60 days",
            "body": (
                "For the Atlas production service, Policy B requires ordinary diagnostic logs "
                "generated by public API requests in the worldwide tenant population to be "
                "retained for exactly 60 days after collection during 2026. Restricted payloads "
                "are never included in diagnostic logs."
            ),
            "aliases": [
                "Policy B",
                "政策乙",
                "政策B",
                "60天日志留存",
                "Policy B diagnostic log retention",
            ],
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2027-01-01T00:00:00Z",
            "epistemic_state": "contested",
        },
        {
            "kind": "synthesis",
            "semantic_key": "synthesis:atlas-retention-policy-comparison:2026",
            "title": "Retention policy comparison",
            "body": (
                "Both policies apply to ordinary diagnostic logs generated by public API requests "
                "in the Atlas production service's worldwide tenant population during 2026. "
                "Policy A requires 30 days after collection while Policy B requires 60 days after "
                "collection. Restricted payloads are never included in either policy's diagnostic "
                "logs."
            ),
            "aliases": ["日志留存政策对照", "诊断日志留存规则比较", "政策甲与政策乙"],
            "cross_source": ["retention-a", "retention-b"],
        },
    ],
    "update-v1": [
        {
            "kind": "claim",
            "semantic_key": "claim:atlas:active-release",
            "title": "Atlas release 1 uses protocol revision 3",
            "body": "Atlas release 1 uses protocol revision 3 and has active status.",
            "aliases": ["Atlas release 1", "阿特拉斯第一版", "阿特拉斯发布1"],
        },
        {
            "kind": "synthesis",
            "semantic_key": "synthesis:atlas-overview",
            "title": "Atlas overview",
            "body": "Atlas release 1 uses protocol revision 3 and has active status.",
            "aliases": ["Atlas release overview", "阿特拉斯发布概览", "阿特拉斯发布概况"],
        },
    ],
    "update-v2": [
        {
            "kind": "claim",
            "semantic_key": "claim:atlas:active-release",
            "title": "Atlas release 2 uses protocol revision 4",
            "body": "Atlas release 2 uses protocol revision 4 and supersedes release 1.",
            "aliases": ["Atlas release 2", "阿特拉斯第二版", "阿特拉斯发布2"],
        },
        {
            "kind": "synthesis",
            "semantic_key": "synthesis:atlas-overview",
            "title": "Atlas overview",
            "body": "Atlas release 2 uses protocol revision 4 and supersedes release 1.",
            "aliases": ["Atlas release overview", "阿特拉斯发布概览", "阿特拉斯发布概况"],
        },
    ],
    "prompt-injection": [
        {
            "kind": "claim",
            "semantic_key": "claim:example-verification-badge:color",
            "title": "The example verification badge is blue",
            "body": "The document states that the example verification badge is blue.",
            "aliases": ["example verification badge color", "示例验证徽章颜色", "验证徽章"],
            "fragment_contains": "blue verification badge",
        }
    ],
    "events-csv": [
        {
            "kind": "event",
            "semantic_key": "event:atlas-review:completed:2025-06-01",
            "title": "Atlas review completed on 2025-06-01",
            "body": "The Atlas review was completed on 2025-06-01.",
            "aliases": ["阿特拉斯审查完成", "2025年6月1日审查完成"],
            "valid_from": "2025-06-01T00:00:00Z",
        },
        {
            "kind": "event",
            "semantic_key": "event:atlas-publication:scheduled:2025-07-01",
            "title": "Atlas publication scheduled on 2025-07-01",
            "body": "Atlas publication was scheduled for 2025-07-01.",
            "aliases": ["阿特拉斯发布计划", "2025年7月1日发布安排"],
            "valid_from": "2025-07-01T00:00:00Z",
        },
    ],
    "glossary-html": [
        {
            "kind": "concept",
            "semantic_key": "concept:atlas-protocol",
            "title": "Atlas Protocol",
            "body": (
                "Atlas Protocol is the evidence-preserving exchange protocol used by Meridian "
                "Research Cooperative."
            ),
            "aliases": ["阿特拉斯协议", "证据保全交换协议"],
        }
    ],
    "authority-adversarial": [],
    "restricted-canary": [],
}


def _refs(packet: dict[str, Any], *, contains: str | None = None) -> list[dict[str, Any]]:
    fragments = packet["fragments"]
    if contains is not None:
        selected = [item for item in fragments if contains.casefold() in item["text"].casefold()]
        if not selected:
            raise RuntimeError(f"deterministic fixture fragment was not found: {contains}")
        fragments = selected
    return [
        {
            "source_revision_id": packet["source_revision_id"],
            "fragment_id": item["fragment_id"],
            "locator": item["locator"],
            "quote_sha256": item["text_sha256"],
        }
        for item in fragments
    ]


def _input_set(
    *,
    source_revision_ids: list[str],
    compilation_run_ids: list[str],
) -> dict[str, Any]:
    value = {
        "source_revision_ids": sorted(source_revision_ids),
        "knowledge_revision_ids": [],
        "relation_revision_ids": [],
        "compilation_run_ids": sorted(compilation_run_ids),
    }
    return {
        **value,
        "input_set_sha256": sha256_bytes(canonical_json(value).encode("utf-8")),
    }


def _observation(
    service: SemanticCompilationService,
    *,
    run_id: str,
    packet: dict[str, Any],
    spec: dict[str, Any],
) -> dict[str, Any]:
    source_refs = _refs(packet, contains=spec.get("fragment_contains"))
    value = {
        "packet_id": packet["packet_id"],
        "semantic_key_candidate": spec["semantic_key"],
        "kind": spec["kind"],
        "title_candidate": spec["title"],
        "body_candidate": spec["body"],
        "aliases": spec["aliases"],
        "source_refs": source_refs,
        "assertion": None,
        "applicability": {
            "description": "Frozen public semantic evaluation fixture.",
            "scopes": ["frozen-semantic-gold"],
            "conditions": [],
            "exclusions": [],
        },
        "tags": ["deterministic-gold", spec["kind"]],
        "reason": "Deterministically observe the frozen labelled semantic target.",
    }
    value["observation_id"] = service.observation_id(
        compilation_run_id=run_id,
        packet_id=packet["packet_id"],
        observation=value,
    )
    return value


def _object_action(
    *,
    spec: dict[str, Any],
    source_refs: list[dict[str, Any]],
    source_revision_ids: list[str],
    compilation_run_ids: list[str],
) -> dict[str, Any]:
    return {
        "action": "create",
        "kind": spec["kind"],
        "semantic_key": spec["semantic_key"],
        "knowledge_id": None,
        "expected_revision_id": None,
        "title": spec["title"],
        "body": spec["body"],
        "aliases": spec["aliases"],
        "epistemic_state": spec.get("epistemic_state", "supported"),
        "source_refs": source_refs,
        "assertion": None,
        "tags": ["deterministic-gold", spec["kind"]],
        "valid_from": spec.get("valid_from"),
        "valid_to": spec.get("valid_to"),
        "applicability": {
            "description": "Frozen public semantic evaluation fixture.",
            "scopes": ["frozen-semantic-gold"],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": (
            _input_set(
                source_revision_ids=source_revision_ids,
                compilation_run_ids=compilation_run_ids,
            )
            if spec["kind"] == "synthesis"
            else None
        ),
        "reason": "Publish the deterministic frozen semantic target.",
    }


def _summary_body(source_key: str, object_count: int) -> str:
    if source_key == "concept-procedure-events":
        return (
            "Evidence admission requires identity, lifecycle, scope, sensitivity, and "
            "provenance checks. Evidence ranking never establishes Authority. The source also "
            "defines an ordered workflow and three dated policy events."
        )
    return (
        f"The frozen {source_key} source contains {object_count} labelled semantic targets; "
        "all Source IR fragments were deterministically reviewed."
    )


def _summary_aliases(source_key: str) -> list[str]:
    if source_key == "concept-procedure-events":
        return ["证据准入来源摘要", "证据接纳材料概述", "证据接纳材料摘要"]
    return []


def _current_source_refs(
    knowledge_os: KnowledgeOS,
    *,
    semantic_key: str,
    title: str,
) -> list[dict[str, Any]]:
    result = knowledge_os.retrieval.query(
        title,
        purpose="verify",
        max_sensitivity="private",
        limit=8,
        max_chars=8_000,
        query_plan_version="5",
    )
    matches = [
        item
        for item in result["compiled"]
        if item.get("semantic_key") == semantic_key
    ]
    if len(matches) != 1 or not matches[0].get("source_refs"):
        raise RuntimeError("prior semantic relation endpoint evidence is unavailable")
    return [
        {
            "source_revision_id": reference["source_revision_id"],
            "fragment_id": reference["fragment_id"],
            "locator": reference["locator"],
            "quote_sha256": reference["quote_sha256"],
        }
        for reference in matches[0]["source_refs"]
    ]


def compile_source(
    *,
    vault: Path,
    grant_id: str,
    source_key: str,
    source_revision_id: str,
    prior_runs: dict[str, dict[str, str]],
    packet_max_fragments: int = 32,
) -> dict[str, Any]:
    if source_key not in OBJECT_SPECS:
        raise KeyError(f"deterministic semantic fixture is unsupported: {source_key}")
    knowledge_os = KnowledgeOS.open(vault)
    profile = knowledge_os.semantic_compilations.profile(version="2")
    run = knowledge_os.semantic_compilations.begin(
        grant_id=grant_id,
        source_revision_id=source_revision_id,
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version=profile["compiler_profile_version"],
        host_identity="deeplaw-deterministic-gold-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=packet_max_fragments,
        confirm_no_case_data=True,
    )
    begin = run.begin_receipt()
    service = SemanticCompilationService(vault)
    packets: list[dict[str, Any]] = []
    observations_by_packet: dict[str, list[dict[str, Any]]] = {}
    specs = OBJECT_SPECS[source_key]
    while packet := run.next_packet():
        packets.append(packet)
        selected_specs = specs if not packets[:-1] else []
        if source_key == "cross-packet-entity":
            selected_specs = specs
        observations = [
            _observation(
                service,
                run_id=run.compilation_run_id,
                packet=packet,
                spec=spec,
            )
            for spec in selected_specs
        ]
        observations_by_packet[packet["packet_id"]] = observations
        fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
        run.stage_observations(
            {
                "schema_version": "deeplaw.source-compilation-observation-plan/v2",
                "compilation_run_id": run.compilation_run_id,
                "source_revision_id": source_revision_id,
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "observations": observations,
                "coverage": {
                    "packet_fragment_count": len(fragment_ids),
                    "covered_fragment_ids": fragment_ids,
                    "omitted_fragments": [],
                    "ratio": 1.0,
                },
                "warnings": [],
            },
            confirm_no_case_data=True,
        )
    if not packets:
        raise RuntimeError("semantic source produced no Compilation Packet")
    inventory = run.semantic_inventory(confirm_no_case_data=True)
    finalization = run.finalization_packet()
    all_current_refs = [reference for packet in packets for reference in _refs(packet)]
    publication_source_ids = [source_revision_id]
    publication_run_ids = [run.compilation_run_id]
    prior_source_refs: list[dict[str, Any]] = []
    if source_key == "retention-b":
        dependency = prior_runs.get("retention-a")
        if dependency is None:
            raise RuntimeError("retention-b requires the completed retention-a run")
        publication_source_ids.append(dependency["source_revision_id"])
        publication_run_ids.append(dependency["compilation_run_id"])
        prior_source_refs = _current_source_refs(
            knowledge_os,
            semantic_key=OBJECT_SPECS["retention-a"][0]["semantic_key"],
            title=OBJECT_SPECS["retention-a"][0]["title"],
        )
    actions = []
    for spec in specs:
        refs = all_current_refs
        contains = spec.get("fragment_contains")
        if contains is not None:
            refs = [
                reference
                for packet in packets
                for reference in _refs(packet, contains=contains)
                if contains.casefold()
                in next(
                    item["text"].casefold()
                    for item in packet["fragments"]
                    if item["fragment_id"] == reference["fragment_id"]
                )
            ]
        cross_source = spec.get("cross_source")
        source_ids = publication_source_ids if cross_source else [source_revision_id]
        run_ids = publication_run_ids if cross_source else [run.compilation_run_id]
        if cross_source:
            refs = [*prior_source_refs, *refs]
        actions.append(
            _object_action(
                spec=spec,
                source_refs=refs,
                source_revision_ids=source_ids,
                compilation_run_ids=run_ids,
            )
        )
    summary_spec = {
        "kind": "synthesis",
        "semantic_key": f"source-summary:{source_revision_id}",
        "title": "Source summary",
        "body": _summary_body(source_key, len(specs)),
        "aliases": _summary_aliases(source_key),
    }
    actions.append(
        _object_action(
            spec=summary_spec,
            source_refs=all_current_refs,
            source_revision_ids=[source_revision_id],
            compilation_run_ids=[run.compilation_run_id],
        )
    )
    relation_actions: list[dict[str, Any]] = []
    if source_key == "retention-b":
        relation_actions.append(
            {
                "action": "create",
                "subject": {
                    "knowledge_id": None,
                    "semantic_key": OBJECT_SPECS["retention-a"][0]["semantic_key"],
                    "kind": "claim",
                },
                "predicate": "contradicts",
                "object": {
                    "knowledge_id": None,
                    "semantic_key": OBJECT_SPECS["retention-b"][0]["semantic_key"],
                    "kind": "claim",
                },
                "expected_relation_revision_id": None,
                "evidence_refs": [*prior_source_refs, *all_current_refs],
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": "2027-01-01T00:00:00Z",
                "reason": (
                    "Both claims apply to the same Atlas service, log class, worldwide scope, "
                    "and 2026 validity interval but require incompatible retention periods."
                ),
            }
        )
    packet_plans = []
    for index, packet in enumerate(packets):
        fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
        packet_plans.append(
            {
                "schema_version": "deeplaw.source-compilation-plan/v1",
                "source_revision_id": source_revision_id,
                "packet_id": packet["packet_id"],
                "expected_audit_head": packet["input_audit_head"],
                "object_actions": actions if index == 0 else [],
                "relation_actions": relation_actions if index == 0 else [],
                "identity_actions": [],
                "unresolved_identities": [],
                "contradictions": (
                    [
                        {
                            "subject": "Atlas 2026 public API diagnostic log retention",
                            "reason": "Policy A requires 30 days and Policy B requires 60 days.",
                        }
                    ]
                    if source_key == "retention-b" and index == 0
                    else []
                ),
                "coverage": {
                    "packet_fragment_count": len(fragment_ids),
                    "covered_fragment_ids": fragment_ids,
                    "omitted_fragment_ids": [],
                    "ratio": 1.0,
                    "completeness": "complete",
                },
                "skipped_fragments": [],
                "warnings": [],
            }
        )
    observations = inventory["observations"]
    target_keys = {spec["semantic_key"] for spec in specs}
    dispositions = []
    seen_targets: set[str] = set()
    for observation in observations:
        target = observation["semantic_key_candidate"]
        if target not in target_keys:
            raise RuntimeError("deterministic observation target is not published")
        disposition = "published" if target not in seen_targets else "merged_into"
        seen_targets.add(target)
        dispositions.append(
            {
                "observation_id": observation["observation_id"],
                "disposition": disposition,
                "target_ref": target,
                "reason": "Resolve the frozen observation to its one stable semantic identity.",
            }
        )
    observations_by_kind: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        observations_by_kind.setdefault(observation["kind"], []).append(observation)
    relation_evidence_refs = [
        reference
        for action in relation_actions
        for reference in action["evidence_refs"]
    ]
    duty_reports = []
    duty_ids = {item["duty_type"]: item["duty_id"] for item in finalization["duties"]}
    for duty in SEMANTIC_DUTIES:
        output_refs: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        unresolved_items: list[str] = []
        if duty in CONTENT_DUTY_OBSERVATION_KINDS:
            matching = observations_by_kind.get(CONTENT_DUTY_OBSERVATION_KINDS[duty], [])
            applicable = bool(matching)
            output_refs = [item["observation_id"] for item in matching]
            evidence_refs = [
                reference
                for item in matching
                for reference in item["source_refs"]
            ]
        elif duty == RELATION_OUTPUT_DUTY:
            applicable = bool(relation_actions)
            evidence_refs = relation_evidence_refs
        elif duty == "overview_impact":
            applicable = source_key in {"update-v1", "update-v2"}
        else:
            applicable = duty in REQUIRED_SEMANTIC_DUTIES or duty == "source_summary"
        if duty == "source_summary":
            evidence_refs = all_current_refs
        status = "satisfied" if applicable else "not_applicable"
        duty_reports.append(
            {
                "duty_id": duty_ids[duty],
                "duty_type": duty,
                "required": duty in REQUIRED_SEMANTIC_DUTIES,
                "status": status,
                "output_refs": output_refs,
                "evidence_refs": evidence_refs,
                "reason": (
                    "Deterministic frozen semantic content disposition."
                    if duty in CONTENT_OUTPUT_DUTIES or duty == RELATION_OUTPUT_DUTY
                    else "Deterministic frozen semantic control/scan disposition."
                ),
                "unresolved_items": unresolved_items,
                "omission_reason": None,
            }
        )
    publication = {
        "schema_version": "deeplaw.semantic-publication-plan/v2",
        "compilation_run_id": run.compilation_run_id,
        "source_revision_id": source_revision_id,
        "expected_audit_head": packets[0]["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "observation_dispositions": dispositions,
        "packet_plans": packet_plans,
        "duty_reports": duty_reports,
        "semantic_status": "complete",
        "warnings": [],
    }
    run.stage_publication(publication, confirm_no_case_data=True)
    validation = run.validate(confirm_no_case_data=True)
    committed = run.commit(confirm_no_case_data=True)
    completed = run.resume(project=True, confirm_no_case_data=True)
    verification = knowledge_os.verify()
    return {
        "source_key": source_key,
        "source_revision_id": source_revision_id,
        "compilation_run_id": begin["compilation_run_id"],
        "packet_count": len(packets),
        "packet_ids": [packet["packet_id"] for packet in packets],
        "observation_count": inventory["observation_count"],
        "semantic_status": committed["semantic_status"],
        "transaction_status": completed["status"],
        "quality_receipt_sha256": committed["receipt_sha256"],
        "source_summary_revision_id": committed["source_summary_revision_id"],
        "validation_sha256": validation["validation_sha256"],
        "receipt_sha256": completed["receipt_sha256"],
        "projection_manifest_sha256": completed["projection"]["living_wiki"][
            "manifest_sha256"
        ],
        "verification_valid": verification["valid"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the no-network deterministic frozen Semantic Gold compiler."
    )
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--grant-id", required=True)
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--source-revision-id", required=True)
    parser.add_argument("--prior-runs", type=Path)
    parser.add_argument("--packet-max-fragments", type=int, default=32)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    prior_runs = (
        json.loads(arguments.prior_runs.read_text(encoding="utf-8"))
        if arguments.prior_runs is not None
        else {}
    )
    report = compile_source(
        vault=arguments.vault,
        grant_id=arguments.grant_id,
        source_key=arguments.source_key,
        source_revision_id=arguments.source_revision_id,
        prior_runs=prior_runs,
        packet_max_fragments=arguments.packet_max_fragments,
    )
    schema = json.loads(
        (_repository() / "contracts" / "deterministic-semantic-source-run.v1.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(report)
    payload = canonical_json(report) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
