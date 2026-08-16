from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from deeplaw.api import KnowledgeOS
from deeplaw.compilation.profiles import REQUIRED_SEMANTIC_DUTIES, SEMANTIC_DUTIES
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.evidence.statements import build_input_set_sha256, statement_sha256
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.util import canonical_json, sha256_bytes

SCHEMA_VERSION = "deeplaw.deterministic-fake-agent-compile/v2"
MCP_SCHEMA_VERSION = "deeplaw.deterministic-fake-mcp-agent-compile/v1"


def _observation_plan(
    service: SemanticCompilationService,
    packet: dict[str, Any],
) -> dict[str, Any]:
    observations = []
    for fragment in packet["fragments"]:
        title = next(
            (
                line.lstrip("# ").strip()
                for line in fragment["text"].splitlines()
                if line.strip()
            ),
            f"Fragment {fragment['ordinal']}",
        )[:500]
        observation = {
            "packet_id": packet["packet_id"],
            "semantic_key_candidate": (
                f"deterministic-fake-agent:{packet['source_revision_id']}:"
                f"{fragment['fragment_id']}"
            ),
            "kind": "claim",
            "title_candidate": title,
            "body_candidate": fragment["text"],
            "aliases": [],
            "source_refs": [
                {
                    "source_revision_id": packet["source_revision_id"],
                    "fragment_id": fragment["fragment_id"],
                    "locator": fragment["locator"],
                    "quote_sha256": fragment["text_sha256"],
                }
            ],
            "assertion": None,
            "applicability": {
                "description": "Bound to the exact compiled Source Revision.",
                "scopes": [],
                "conditions": [],
                "exclusions": [],
            },
            "tags": ["deterministic-fake-agent", "source-compiled"],
            "reason": "Deterministically observe one evidence-bound source claim.",
        }
        observation["observation_id"] = service.observation_id(
            compilation_run_id=packet["compilation_run_id"],
            packet_id=packet["packet_id"],
            observation=observation,
        )
        observations.append(observation)
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-observation-plan/v2",
        "compilation_run_id": packet["compilation_run_id"],
        "source_revision_id": packet["source_revision_id"],
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
    }


def _packet_plan(
    packet: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    actions = [
        {
            "action": "create",
            "kind": "claim",
            "semantic_key": observation["semantic_key_candidate"],
            "knowledge_id": None,
            "expected_revision_id": None,
            "title": observation["title_candidate"],
            "body": observation["body_candidate"],
            "aliases": [],
            "epistemic_state": "supported",
            "source_refs": observation["source_refs"],
            "assertion": None,
            "tags": observation["tags"],
            "valid_from": None,
            "valid_to": None,
            "applicability": observation["applicability"],
            "synthesis_inputs": None,
            "reason": "Publish one deterministic evidence-bound claim.",
        }
        for observation in observations
    ]
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": actions,
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [],
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


def _publication_plan(
    *,
    run_id: str,
    source_revision_id: str,
    packets: list[dict[str, Any]],
    inventory: dict[str, Any],
    finalization: dict[str, Any],
) -> dict[str, Any]:
    by_packet: dict[str, list[dict[str, Any]]] = {
        packet["packet_id"]: [] for packet in packets
    }
    for observation in inventory["observations"]:
        by_packet[observation["packet_id"]].append(observation)
    packet_plans = [
        _packet_plan(packet, by_packet[packet["packet_id"]]) for packet in packets
    ]
    all_refs = [
        reference
        for observation in inventory["observations"]
        for reference in observation["source_refs"]
    ]
    input_set = {
        "source_revision_ids": [source_revision_id],
        "knowledge_revision_ids": [],
        "relation_revision_ids": [],
        "compilation_run_ids": [run_id],
    }
    packet_plans[0]["object_actions"].append(
        {
            "action": "create",
            "kind": "synthesis",
            "semantic_key": f"source-summary:{source_revision_id}",
            "knowledge_id": None,
            "expected_revision_id": None,
            "title": "Source summary",
            "body": (
                f"The source contains {inventory['observation_count']} "
                "deterministically preserved evidence-bound statements."
            ),
            "aliases": [],
            "epistemic_state": "supported",
            "source_refs": all_refs,
            "assertion": None,
            "tags": ["source-summary", "deterministic-fake-agent"],
            "valid_from": None,
            "valid_to": None,
            "applicability": {
                "description": "Bound to the exact compiled Source Revision.",
                "scopes": [],
                "conditions": [],
                "exclusions": [],
            },
            "synthesis_inputs": {
                **input_set,
                "input_set_sha256": sha256_bytes(
                    canonical_json(input_set).encode("utf-8")
                ),
            },
            "reason": "Publish the required deterministic Source Summary.",
        }
    )
    duty_ids = {item["duty_type"]: item["duty_id"] for item in finalization["duties"]}
    content_duty_kinds = {
        "key_claims": "claim",
        "entities": "entity",
        "concepts": "concept",
        "events": "event",
        "procedures": "procedure",
        "comparisons": "comparison",
    }
    observations_by_kind: dict[str, list[dict[str, Any]]] = {}
    for observation in inventory["observations"]:
        observations_by_kind.setdefault(observation["kind"], []).append(observation)
    relation_actions = [
        action
        for packet_plan in packet_plans
        for action in packet_plan["relation_actions"]
    ]
    duty_reports = []
    for duty in SEMANTIC_DUTIES:
        output_refs: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        if duty == "source_summary":
            status = "satisfied"
            evidence_refs = all_refs
        elif duty in content_duty_kinds:
            matching = observations_by_kind.get(content_duty_kinds[duty], [])
            status = "satisfied" if matching else "not_applicable"
            if matching:
                witness = matching[0]
                output_refs = [witness["observation_id"]]
                evidence_refs = witness["source_refs"]
        elif duty == "typed_relations":
            status = "satisfied" if relation_actions else "not_applicable"
            evidence_refs = [
                reference
                for action in relation_actions
                for reference in action["evidence_refs"]
            ]
        else:
            status = "satisfied" if duty == "source_coverage" else "not_applicable"
        duty_reports.append(
            {
                "duty_id": duty_ids[duty],
                "duty_type": duty,
                "required": duty in REQUIRED_SEMANTIC_DUTIES,
                "status": status,
                "output_refs": output_refs,
                "evidence_refs": evidence_refs,
                "reason": "Deterministic duty.",
                "unresolved_items": [],
                "omission_reason": None,
            }
        )
    return {
        "schema_version": "deeplaw.semantic-publication-plan/v2",
        "compilation_run_id": run_id,
        "source_revision_id": source_revision_id,
        "expected_audit_head": packets[0]["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "observation_dispositions": [
            {
                "observation_id": observation["observation_id"],
                "disposition": "published",
                "target_ref": observation["semantic_key_candidate"],
                "reason": "Publish the exact deterministic observation.",
            }
            for observation in inventory["observations"]
        ],
        "packet_plans": packet_plans,
        "duty_reports": duty_reports,
        "semantic_status": "complete",
        "warnings": [],
    }


def _statement(body: str, source_refs: list[dict[str, Any]]) -> dict[str, Any]:
    gaps: list[dict[str, str]] = []
    return {
        "ordinal": 1,
        "char_start": 0,
        "char_end": len(body),
        "statement_text": body,
        "statement_sha256": statement_sha256(body),
        "statement_type": "factual",
        "support_status": "supported",
        "source_refs": source_refs,
        "knowledge_revision_refs": [],
        "relation_revision_refs": [],
        "valid_from": None,
        "valid_to": None,
        "limitation": None,
        "gaps": gaps,
        "input_set_sha256": build_input_set_sha256(
            source_refs=source_refs,
            knowledge_revision_refs=[],
            relation_revision_refs=[],
            valid_from=None,
            valid_to=None,
            statement_type="factual",
            support_status="supported",
            limitation=None,
            gaps=gaps,
        ),
    }


def _publication_plan_v3(
    *,
    run_id: str,
    source_revision_id: str,
    packets: list[dict[str, Any]],
    inventory: dict[str, Any],
    finalization: dict[str, Any],
) -> dict[str, Any]:
    base = _publication_plan(
        run_id=run_id,
        source_revision_id=source_revision_id,
        packets=packets,
        inventory=inventory,
        finalization=finalization,
    )
    statement_plans = [
        {
            "packet_id": packet_plan["packet_id"],
            "object_action_ordinal": ordinal,
            "statements": [_statement(action["body"], action["source_refs"])],
        }
        for packet_plan in base["packet_plans"]
        for ordinal, action in enumerate(packet_plan["object_actions"], start=1)
    ]
    observations = inventory["observations"]
    all_refs = [reference for item in observations for reference in item["source_refs"]]
    observations_by_kind: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        observations_by_kind.setdefault(observation["kind"], []).append(observation)
    content_kinds = {
        "key_claims": "claim",
        "entities": "entity",
        "concepts": "concept",
        "events": "event",
        "procedures": "procedure",
        "comparisons": "comparison",
    }
    duty_reports = []
    for duty in finalization["duties"]:
        duty_type = duty["duty_type"]
        applicability = duty["applicability"]
        output_refs: list[str] = []
        evidence_refs: list[dict[str, Any]] = []
        unresolved_items: list[str] = []
        omission_reason: str | None = None
        if applicability == "unknown":
            status = "unresolved"
            unresolved_items = [
                "Deterministic applicability is unknown; the fake Host does not invent a result."
            ]
        elif applicability == "not_applicable":
            status = "omitted_with_reason"
            omission_reason = "The frozen deterministic facts mark this duty not applicable."
        else:
            status = "satisfied"
            if duty_type == "source_summary":
                evidence_refs = all_refs
            elif duty_type in content_kinds:
                matching = observations_by_kind.get(content_kinds[duty_type], [])
                output_refs = [item["observation_id"] for item in matching]
                evidence_refs = [
                    reference for item in matching for reference in item["source_refs"]
                ]
        duty_reports.append(
            {
                "duty_id": duty["duty_id"],
                "duty_type": duty_type,
                "required": duty["required"],
                "applicability": applicability,
                "status": status,
                "output_refs": output_refs,
                "evidence_refs": evidence_refs,
                "reason": "Deterministic fake-Host duty decision from frozen facts.",
                "unresolved_items": unresolved_items,
                "omission_reason": omission_reason,
                "deterministic_basis": duty["deterministic_basis"],
            }
        )
    semantic_status = (
        "partial"
        if any(item["applicability"] == "unknown" for item in finalization["duties"])
        else "complete"
    )
    return {
        "schema_version": "deeplaw.semantic-publication-plan/v3",
        "compiler_profile_version": "3",
        "compilation_run_id": run_id,
        "source_revision_id": source_revision_id,
        "expected_audit_head": packets[0]["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "finalization_packet_id": finalization["finalization_packet_id"],
        "applicability_policy_sha256": finalization["applicability_policy_sha256"],
        "applicability_digest": finalization["applicability_digest"],
        "packet_plans": base["packet_plans"],
        "statement_plans": statement_plans,
        "observation_dispositions": base["observation_dispositions"],
        "duty_reports": duty_reports,
        "semantic_status": semantic_status,
        "warnings": (
            ["Deterministic applicability contains an explicit unknown duty."]
            if semantic_status == "partial"
            else []
        ),
    }


def compile_with_fake_agent(
    *,
    vault: str | Path,
    grant_id: str,
    source_revision_id: str,
    packet_max_fragments: int = 8,
) -> dict[str, Any]:
    knowledge_os = KnowledgeOS.open(vault)
    profile = knowledge_os.semantic_compilations.profile(version="2")
    run = knowledge_os.semantic_compilations.begin(
        grant_id=grant_id,
        source_revision_id=source_revision_id,
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version=profile["compiler_profile_version"],
        host_identity="deeplaw-deterministic-fake-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=packet_max_fragments,
        confirm_no_case_data=True,
    )
    begin = run.begin_receipt()
    service = SemanticCompilationService(vault)
    packets = []
    while packet := run.next_packet():
        packets.append(packet)
        run.stage_observations(
            _observation_plan(service, packet),
            confirm_no_case_data=True,
        )
    inventory = run.semantic_inventory(confirm_no_case_data=True)
    finalization = run.finalization_packet()
    publication = _publication_plan(
        run_id=run.compilation_run_id,
        source_revision_id=source_revision_id,
        packets=packets,
        inventory=inventory,
        finalization=finalization,
    )
    run.stage_publication(publication, confirm_no_case_data=True)
    validation = run.validate(confirm_no_case_data=True)
    committed = run.commit(confirm_no_case_data=True)
    completed = run.resume(project=True, confirm_no_case_data=True)
    verification = knowledge_os.verify()
    retrieval = knowledge_os.retrieval.query(
        inventory["observations"][0]["body_candidate"][:500],
        purpose="answer",
        limit=8,
        max_chars=8_000,
        max_tokens=4_000,
        query_plan_version="5",
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "host_identity": "deeplaw-deterministic-fake-agent",
        "model_identity": None,
        "source_revision_id": source_revision_id,
        "compilation_run_id": begin["compilation_run_id"],
        "packet_count": len(packets),
        "observation_count": inventory["observation_count"],
        "staged_object_count": inventory["observation_count"] + 1,
        "semantic_status": committed["semantic_status"],
        "inventory_sha256": inventory["inventory_sha256"],
        "quality_receipt_sha256": committed["receipt_sha256"],
        "source_summary_revision_id": committed["source_summary_revision_id"],
        "validation_sha256": validation["validation_sha256"],
        "receipt_sha256": completed["receipt_sha256"],
        "projection_manifest_sha256": completed["projection"]["living_wiki"][
            "manifest_sha256"
        ],
        "compiled_result_count": len(retrieval["compiled"]),
        "status": completed["status"],
        "verification_valid": verification["valid"],
        "network_used": False,
        "external_credentials_used": False,
    }
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "deterministic-fake-agent-compile.v2.schema.json"
    )
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        report
    )
    return report


def compile_with_fake_mcp_agent(
    *,
    vault: str | Path,
    grant_id: str,
    source_revision_id: str,
    packet_max_fragments: int = 8,
) -> dict[str, Any]:
    knowledge_os = KnowledgeOS.open(vault)
    profile = handle_knowledge_support(
        operation="semantic",
        semantic_action="profile",
        compiler_profile="living-wiki-agent",
        compiler_profile_version="3",
        vault_path=vault,
    )["result"]
    begin = handle_knowledge_sink(
        {
            "operation": "begin_compilation",
            "idempotency_key": f"fake-host-begin:{source_revision_id}",
            "confirm_no_case_data": True,
            "source_revision_id": source_revision_id,
            "compiler_profile": profile["compiler_profile"],
            "compiler_profile_version": profile["compiler_profile_version"],
            "host_identity": "deeplaw-deterministic-fake-agent",
            "prompt_template_id": profile["prompt_template_id"],
            "prompt_config_sha256": profile["prompt_config_sha256"],
            "plan_configuration_sha256": profile["plan_configuration_sha256"],
            "packet_max_fragments": packet_max_fragments,
        },
        grant_id=grant_id,
        vault_path=vault,
    )["result"]
    run_id = begin["compilation_run_id"]
    service = SemanticCompilationService(vault)
    packets = []
    while True:
        packet = handle_knowledge_support(
            operation="semantic",
            semantic_action="next_packet",
            compilation_run_id=run_id,
            vault_path=vault,
        )["result"]
        if packet.get("complete") is True:
            break
        packets.append(packet)
        handle_knowledge_sink(
            {
                "operation": "stage_semantic_observations",
                "idempotency_key": f"fake-host-observe:{packet['packet_id']}",
                "confirm_no_case_data": True,
                "compilation_run_id": run_id,
                "plan": _observation_plan(service, packet),
            },
            grant_id=grant_id,
            vault_path=vault,
        )
    inventory = handle_knowledge_sink(
        {
            "operation": "freeze_semantic_inventory",
            "idempotency_key": f"fake-host-inventory:{run_id}",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
        },
        grant_id=grant_id,
        vault_path=vault,
    )["result"]
    finalization = handle_knowledge_support(
        operation="semantic",
        semantic_action="finalization",
        compilation_run_id=run_id,
        vault_path=vault,
    )["result"]
    publication = _publication_plan_v3(
        run_id=run_id,
        source_revision_id=source_revision_id,
        packets=packets,
        inventory=inventory,
        finalization=finalization,
    )
    handle_knowledge_sink(
        {
            "operation": "finalize_semantic_compilation",
            "idempotency_key": f"fake-host-finalize:{run_id}",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
            "plan": publication,
        },
        grant_id=grant_id,
        vault_path=vault,
    )
    validation = handle_knowledge_sink(
        {
            "operation": "validate_compilation",
            "idempotency_key": f"fake-host-validate:{run_id}",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
        },
        grant_id=grant_id,
        vault_path=vault,
    )["result"]
    committed = handle_knowledge_sink(
        {
            "operation": "commit_compilation",
            "idempotency_key": f"fake-host-commit:{run_id}",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
        },
        grant_id=grant_id,
        vault_path=vault,
    )["result"]
    completed = handle_knowledge_sink(
        {
            "operation": "resume_compilation",
            "idempotency_key": f"fake-host-resume:{run_id}",
            "confirm_no_case_data": True,
            "compilation_run_id": run_id,
            "project": True,
        },
        grant_id=grant_id,
        vault_path=vault,
    )["result"]
    verification = knowledge_os.verify()
    retrieval = handle_knowledge_support(
        operation="query",
        query=inventory["observations"][0]["body_candidate"][:500],
        purpose="answer",
        limit=8,
        max_chars=8_000,
        max_tokens=4_000,
        query_plan_version="6",
        vault_path=vault,
    )
    report = {
        "schema_version": MCP_SCHEMA_VERSION,
        "host_identity": "deeplaw-deterministic-fake-agent",
        "model_identity": None,
        "read_leaf": "knowledge_support",
        "write_leaf": "knowledge_sink",
        "compiler_profile_version": profile["compiler_profile_version"],
        "query_plan_version": "6",
        "source_revision_id": source_revision_id,
        "compilation_run_id": run_id,
        "packet_count": len(packets),
        "observation_count": inventory["observation_count"],
        "staged_object_count": inventory["observation_count"] + 1,
        "semantic_status": committed["semantic_status"],
        "inventory_sha256": inventory["inventory_sha256"],
        "quality_receipt_sha256": committed["receipt_sha256"],
        "source_summary_revision_id": committed["source_summary_revision_id"],
        "validation_sha256": validation["validation_sha256"],
        "receipt_sha256": completed["receipt_sha256"],
        "projection_manifest_sha256": completed["projection"]["living_wiki"][
            "manifest_sha256"
        ],
        "compiled_result_count": len(retrieval["result"]["capsule"]["statements"]),
        "provider_payload_bytes": retrieval["result"]["delivery"][
            "provider_content_bytes"
        ],
        "status": completed["status"],
        "verification_valid": verification["valid"],
        "network_used": False,
        "external_credentials_used": False,
    }
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "deterministic-fake-mcp-agent-compile.v1.schema.json"
    )
    Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        report
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the no-network deterministic semantic compilation Agent."
    )
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--grant-id", required=True)
    parser.add_argument("--source-revision-id", required=True)
    parser.add_argument("--packet-max-fragments", type=int, default=8)
    parser.add_argument(
        "--public-mcp",
        action="store_true",
        help="Use the split knowledge_support/knowledge_sink public Host seams",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    compile_agent = compile_with_fake_mcp_agent if args.public_mcp else compile_with_fake_agent
    report = compile_agent(
        vault=args.vault,
        grant_id=args.grant_id,
        source_revision_id=args.source_revision_id,
        packet_max_fragments=args.packet_max_fragments,
    )
    payload = canonical_json(report) + "\n"
    if args.output is not None:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
