"""Owner-side DeepLaw context and outcome binding for finite real Host tasks.

Fixture maintenance is explicit owner-directed setup, not autonomous model
learning. Only a verified provider Capsule crosses into the Host environment.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from benchmarks.hosts import maintenance_task_mcp
from benchmarks.hosts.maintenance_task_cases import (
    CONFIGURATION_ORDER,
    public_task_projection,
    score_host_trace,
)
from deeplaw.api import KnowledgeOS
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import (
    assert_provider_output_safe,
    canonical_json,
    sha256_bytes,
    strict_json_loads,
)


def digest(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode())


def prepare_context_vault(vault: Path, configuration: str) -> dict[str, Any]:
    if configuration not in CONFIGURATION_ORDER or vault.exists():
        raise ValueError("context configuration must use a fresh vault")
    initialize_knowledge_vault(vault, name="Public Host maintenance task", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="owner-host-maintenance-fixture",
            operations=("remember", "forget", "record_run", "record_feedback"),
            allowed_scope="project", max_sensitivity="public",
            evaluator_types=("external_check",),
        )["grant_id"]
    operations: list[dict[str, Any]] = []

    def write(request: dict[str, Any]) -> dict[str, Any]:
        value = handle_knowledge_sink(
            {**request, "confirm_no_case_data": True}, grant_id=grant, vault_path=vault,
        )["result"]
        operations.append({"operation": request["operation"], "result_sha256": digest(value)})
        return value

    memories: dict[str, dict[str, Any]] = {}
    if configuration != "no_memory":
        old_records = public_task_projection(
            "frozen_unmaintained", "source_update",
        )["environment"]["experience"]["records"]
        for index, record in enumerate(old_records):
            memories[record["experience_id"]] = write({
                "operation": "remember", "idempotency_key": f"seed-{index}",
                "title": "Orchid archive Amber report experience",
                "body": canonical_json(record), "kind": "experience",
                "semantic_key": f"host-maintenance:{index}",
                "scope": "project", "sensitivity": "public",
            })
        if configuration == "governed_maintenance":
            current = public_task_projection(
                configuration, "source_update",
            )["environment"]["experience"]["records"][0]
            for index, (identity, old) in enumerate(list(memories.items())):
                if identity == "experience:source-v1":
                    memories[identity] = write({
                        "operation": "remember", "idempotency_key": "maintain-source",
                        "title": "Orchid archive Amber report experience",
                        "body": canonical_json(current), "kind": "experience",
                        "knowledge_id": old["knowledge_id"],
                        "expected_revision_id": old["revision_id"],
                        "semantic_key": f"host-maintenance:{index}",
                        "scope": "project", "sensitivity": "public",
                    })
                else:
                    write({
                        "operation": "forget", "idempotency_key": f"reject-error-{index}",
                        "knowledge_id": old["knowledge_id"],
                        "expected_revision_id": old["revision_id"],
                        "reason": "Owner-directed correction of injected public wrong experience.",
                    })
        forgotten = write({
            "operation": "remember", "idempotency_key": "seed-forgettable",
            "title": "Orchid archive forgotten shortcut",
            "body": "Synthetic obsolete shortcut marker cobalt-before-forget.",
            "kind": "experience", "semantic_key": "host-maintenance:forgettable",
            "scope": "project", "sensitivity": "public",
        })
        write({
            "operation": "forget", "idempotency_key": "forget-shortcut",
            "knowledge_id": forgotten["knowledge_id"],
            "expected_revision_id": forgotten["revision_id"],
            "reason": "Owner requested forgetting before the subsequent Host tasks.",
        })
    return {"configuration_id": configuration, "grant_id": grant,
            "maintenance_mode": "explicit_owner_fixture_setup",
            "memory_provenance": "source_free_synthetic_fixture",
            "source_binding": "none",
            "operations": operations}


def capture_context(vault: Path, configuration: str, scenario: str) -> dict[str, Any]:
    task = public_task_projection(configuration, scenario)
    with KnowledgeOS.open(vault) as knowledge:
        response = knowledge.context.compile(
            task=task["task"], goal=task["goal"], confirm_no_case_data=True,
            query_plan_version="7", purpose="answer", policy="compiled-first-v1",
            scope="project", max_sensitivity="public", limit=8,
            max_chars=8000, max_tokens=6000, max_sources=12, graph_hops=1,
            retrieval_mode="hybrid",
        )
    capsule = response.get("capsule", response)
    with KnowledgeVault(vault, read_only=True) as store:
        verification = verify_capsule(capsule, vault=store)
    if verification.get("valid") is not True:
        raise ValueError("maintenance context Capsule verification failed")
    provider = capsule["provider_capsule"]
    assert_provider_output_safe(provider, interface="maintenance_host_context")
    if len(canonical_json(provider).encode()) > 65536:
        raise ValueError("maintenance context exceeds provider bound")
    return {"capsule": capsule, "provider_capsule": provider,
            "verification": verification, "context_id": capsule["capsule_id"],
            "task_input_sha256": task["input_sha256"],
            "context_provenance": "source_free_synthetic_memory_compile"}


def record_host_outcome(
    vault: Path, grant_id: str, *, configuration: str, scenario: str,
    host_run_id: str, host_id: str, model_id: str, context: dict[str, Any],
    trace_payload: Mapping[str, Any], candidate_id: str, score: dict[str, Any],
) -> dict[str, Any]:
    """Bind a verified persisted Host trace to the delivered public Capsule.

    The post-hoc ``record_run`` is a task outcome record.  It deliberately does
    not reconstruct the v2 action-state ledger for actions that already
    happened; the complete MCP trace remains the evidence for those actions.
    """
    if not isinstance(trace_payload, Mapping):
        raise ValueError("Host trace payload must be a persisted object")
    try:
        persisted = maintenance_task_mcp._validate_persisted_payload(trace_payload)
    except maintenance_task_mcp.MaintenanceMCPError as exc:
        raise ValueError("Host persisted trace binding is invalid") from exc
    if not isinstance(context, Mapping):
        raise ValueError("Host context must be an object")
    capsule = context.get("capsule")
    provider = context.get("provider_capsule")
    if not isinstance(capsule, Mapping) or not isinstance(provider, Mapping):
        raise ValueError("Host context Capsule and provider projection are required")
    local_provider = capsule.get("provider_capsule")
    if not isinstance(local_provider, Mapping) or dict(provider) != dict(local_provider):
        raise ValueError("outcome provider projection differs from local Capsule")
    task = public_task_projection(configuration, scenario)
    if context.get("task_input_sha256") != task["input_sha256"]:
        raise ValueError("outcome task input binding differs")
    if context.get("context_id") != capsule.get("capsule_id"):
        raise ValueError("outcome context_id does not match Capsule")
    if persisted.get("task") != task:
        raise ValueError("persisted trace task binding differs")
    binding = persisted.get("binding")
    if not isinstance(binding, Mapping):
        raise ValueError("Host persisted trace binding is missing")
    expected_binding = {
        "configuration_id": configuration,
        "scenario_id": scenario,
        "run_id": host_run_id,
        "candidate_id": candidate_id,
        "context_id": capsule.get("capsule_id"),
        "capsule_digest": digest(provider),
    }
    if any(binding.get(field) != value for field, value in expected_binding.items()):
        raise ValueError("Host persisted trace binding differs")
    if binding.get("capsule_digest") != digest(local_provider):
        raise ValueError("Host persisted provider digest differs")
    plan = capsule.get("query_plan")
    if not isinstance(plan, Mapping) or plan.get("scope") != "project" \
            or plan.get("max_sensitivity") != "public":
        raise ValueError("outcome Capsule scope or sensitivity is not public project")
    assert_provider_output_safe(dict(provider), interface="maintenance_host_context")
    with KnowledgeVault(vault, read_only=True) as store:
        if verify_capsule(dict(capsule), vault=store).get("valid") is not True:
            raise ValueError("outcome Capsule no longer verifies")
    events = persisted["events"]
    reopened_score = score_host_trace(configuration, scenario, events)
    if (score != reopened_score or not score["event_chain_valid"]
            or not score["state_transition_valid"]):
        raise ValueError("Host action trace or independent score differs")
    binding = build_task_context_binding(digest(configuration), digest(task["task_id"]))
    trace_digest = digest(dict(persisted))
    result = handle_knowledge_sink({
        "operation": "record_run", "idempotency_key": host_run_id,
        "confirm_no_case_data": True, "task": task["task"],
        "host_id": host_id, "model_id": model_id,
        "status": "succeeded" if score["passed"] else "partial",
        "scope": "project", "sensitivity": "public",
        "input_sha256": capsule["capsule_digest"],
        "output_sha256": trace_digest, "tool_results_sha256": trace_digest,
        "run_metadata": {"task_kind": "actual_host_public_maintenance",
                         "artifact_ids": [capsule["capsule_id"]], "task_binding": binding},
    }, grant_id=grant_id, vault_path=vault)["result"]
    used = {
        event["parameters"]["experience_id"]
        for event in events
        if event.get("status") == "succeeded"
        and isinstance(event.get("typed_result"), Mapping)
        and event["typed_result"].get("status") == "succeeded"
        and isinstance(event.get("parameters"), Mapping)
        and isinstance(event["parameters"].get("experience_id"), str)
    }
    feedback = []
    if score["passed"] is True and not score["unknown_outcome"]:
        for revision in capsule.get("knowledge_revisions", []):
            try:
                content = strict_json_loads(revision["content"])
            except (ValueError, TypeError):
                continue
            if not isinstance(content, dict) or content.get("experience_id") not in used:
                continue
            feedback.append(handle_knowledge_sink({
                "operation": "record_feedback",
                "idempotency_key": host_run_id + "-" + revision["knowledge_id"],
                "confirm_no_case_data": True,
                "knowledge_id": revision["knowledge_id"],
                "expected_revision_id": revision["revision_id"],
                "run_id": result["run_id"],
                "outcome": "helpful" if score["passed"] else "harmful",
                "evaluator_type": "external_check",
                "feedback_note": (
                    "Independent finite environment check of an explicitly referenced "
                    "experience; outcome association does not establish general causal benefit."
                ),
            }, grant_id=grant_id, vault_path=vault)["result"])
    return {"run_record": result, "score_sha256": digest(score),
            "trace_sha256": trace_digest, "task_success_authority": "external_score_only",
            "action_state_ledger": "not_recorded_by_posthoc_task_run",
            "feedback_records": feedback,
            "feedback": "recorded" if feedback else (
                "not_recorded_without_passing_case_or_successful_memory_use"
            )}
