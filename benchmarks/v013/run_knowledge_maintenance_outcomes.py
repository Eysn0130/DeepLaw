"""Bounded development evaluator for governed knowledge maintenance outcomes.

This runner is intentionally a small, deterministic synthetic exercise.  It
freezes typed task inputs and environment results before any configuration is
run, then drives the existing Knowledge Sink and Knowledge Support read seams
against isolated temporary Vaults.  It does not run a model or a real Host and
does not produce release, holdout, or comparative evidence.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import tempfile
import time
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from deeplaw.api import KnowledgeOS
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

SCHEMA_VERSION = "deeplaw.v013-knowledge-maintenance-outcomes/v1"
PROFILE = "knowledge_maintenance_outcomes_development"
QUERY_PLAN_VERSION = "7"
REPEAT_COUNT = 1
CONFIGURATION_ORDER = (
    "no_memory",
    "frozen_unmaintained",
    "governed_maintenance",
)
CASE_ORDER = (
    "useful_experience",
    "inapplicable_experience",
    "source_change",
    "status_change",
    "wrong_experience",
    "repair_reuse",
    "forget_nonrevive",
    "failed_once",
    "unknown_once",
)
_MAINTENANCE_PHASES = frozenset(
    {
        "source_change",
        "status_change",
        "wrong_experience",
        "repair_reuse",
        "forget_nonrevive",
    }
)
_RUN_FEEDBACK_PHASES = frozenset({"run_record", "feedback"})

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:/Users/|/home/|/private/var/|/tmp/|/var/folders/|[A-Za-z]:[\\/]|\\\\)"
)
_SECRET_MARKER = re.compile(
    r"(?i)(?:api[_ -]?key|password|credential|secret|private[_ -]?key)\s*[:=]"
)
_VALID_ERROR_CODES = frozenset(
    {
        "attribute_error",
        "key_error",
        "lookup_error",
        "permission_error",
        "runtime_error",
        "type_error",
        "value_error",
        "other_error",
    }
)


def _case(
    case_id: str,
    task: str,
    environment: Mapping[str, Any],
    expected_action: str,
    expected_parameters: Mapping[str, Any],
    expected_result: Mapping[str, Any],
    fallback_action: str,
    fallback_parameters: Mapping[str, Any],
    *,
    damage_class: str = "none",
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "input": {
            "task": task,
            "goal": "Apply only an applicable retained experience and record the typed outcome.",
            "environment": dict(environment),
            "fallback_action": fallback_action,
            "fallback_parameters": dict(fallback_parameters),
        },
        "expected": {
            "action": expected_action,
            "parameters": dict(expected_parameters),
            "typed_result": dict(expected_result),
            "damage_class": damage_class,
        },
    }


_CASE_SPECS: tuple[dict[str, Any], ...] = (
    _case(
        "useful_experience",
        "Prepare the daily report with the warm cache.",
        {"source_version": "v1", "service_status": "active"},
        "use_warm_cache",
        {"cache": "warm", "mode": "prepare"},
        {
            "status": "succeeded",
            "result_code": "prepared",
            "retry_allowed": False,
            "state": {"source_version": "v1", "service_status": "active"},
        },
        "use_warm_cache",
        {"cache": "warm", "mode": "prepare"},
    ),
    _case(
        "inapplicable_experience",
        "Process the cold archive export where warm cache is out of scope.",
        {"source_version": "v1", "service_status": "active"},
        "skip_warm_cache",
        {"reason": "scope_mismatch"},
        {
            "status": "succeeded",
            "result_code": "skipped",
            "retry_allowed": False,
            "state": {"source_version": "v1", "service_status": "active"},
        },
        "skip_warm_cache",
        {"reason": "scope_mismatch"},
    ),
    _case(
        "source_change",
        "Process the report after the source policy changed to v2.",
        {"source_version": "v2", "service_status": "active"},
        "use_cold_cache",
        {"cache": "cold", "policy_version": "v2"},
        {
            "status": "succeeded",
            "result_code": "processed",
            "retry_allowed": False,
            "state": {"source_version": "v2", "service_status": "active"},
        },
        "use_cold_cache",
        {"cache": "cold", "policy_version": "v2"},
        damage_class="benign",
    ),
    _case(
        "status_change",
        "Process the service after its status changed to paused.",
        {"source_version": "v2", "service_status": "paused"},
        "stop_processing",
        {"status": "paused"},
        {
            "status": "succeeded",
            "result_code": "stopped",
            "retry_allowed": False,
            "state": {"source_version": "v2", "service_status": "paused"},
        },
        "stop_processing",
        {"status": "paused"},
        damage_class="benign",
    ),
    _case(
        "wrong_experience",
        "Validate the checksum despite the shortcut experience.",
        {"source_version": "v2", "service_status": "active"},
        "verify_checksum",
        {"verification": "checksum"},
        {
            "status": "succeeded",
            "result_code": "verified",
            "retry_allowed": False,
            "state": {"source_version": "v2", "service_status": "active"},
        },
        "verify_checksum",
        {"verification": "checksum"},
        damage_class="benign",
    ),
    _case(
        "repair_reuse",
        "Reuse the repaired checksum procedure for the next report.",
        {"source_version": "v2", "service_status": "active"},
        "verify_checksum",
        {"verification": "checksum"},
        {
            "status": "succeeded",
            "result_code": "verified",
            "retry_allowed": False,
            "state": {"source_version": "v2", "service_status": "active"},
        },
        "verify_checksum",
        {"verification": "checksum"},
    ),
    _case(
        "forget_nonrevive",
        "Validate the checksum after forgetting the obsolete shortcut.",
        {"source_version": "v2", "service_status": "active"},
        "verify_checksum",
        {"verification": "checksum"},
        {
            "status": "succeeded",
            "result_code": "verified",
            "retry_allowed": False,
            "state": {"source_version": "v2", "service_status": "active"},
        },
        "verify_checksum",
        {"verification": "checksum"},
        damage_class="benign",
    ),
    _case(
        "failed_once",
        "Refresh the index once after a provider failure.",
        {"source_version": "v2", "service_status": "active"},
        "refresh_index",
        {"scope": "project"},
        {
            "status": "failed",
            "result_code": "provider_unavailable",
            "retry_allowed": False,
            "state": {"source_version": "v2", "service_status": "active"},
        },
        "refresh_index",
        {"scope": "project"},
    ),
    _case(
        "unknown_once",
        "Submit the export once when the result is unknown.",
        {"source_version": "v2", "service_status": "active"},
        "submit_export",
        {"request_id": "export-7"},
        {
            "status": "unknown",
            "result_code": "result_unavailable",
            "retry_allowed": False,
            "state": {"source_version": "v2", "service_status": "active"},
        },
        "submit_export",
        {"request_id": "export-7"},
    ),
)


_SEEDS: tuple[dict[str, Any], ...] = (
    {
        "memory_key": "cache_policy",
        "title": "Warm cache preparation experience",
        "semantic_key": "maintenance.cache_policy",
        "payload": {
            "knowledge_key": "cache_policy",
            "version": "v1",
            "priority": 5,
            "applies_to": ["useful_experience", "source_change"],
            "action": "use_warm_cache",
            "parameters": {"cache": "warm", "mode": "prepare"},
            "source_version": "v1",
            "service_status": "active",
            "truth": "supported",
        },
    },
    {
        "memory_key": "status_policy",
        "title": "Service active status experience",
        "semantic_key": "maintenance.status_policy",
        "payload": {
            "knowledge_key": "status_policy",
            "version": "v1",
            "priority": 5,
            "applies_to": ["status_change"],
            "action": "process_active",
            "parameters": {"status": "active"},
            "source_version": "v1",
            "service_status": "active",
            "truth": "supported",
        },
    },
    {
        "memory_key": "checksum_policy",
        "title": "Checksum shortcut experience",
        "semantic_key": "maintenance.checksum_policy",
        "payload": {
            "knowledge_key": "checksum_policy",
            "version": "v1",
            "priority": 5,
            "applies_to": ["wrong_experience", "repair_reuse"],
            "action": "skip_checksum",
            "parameters": {"verification": "skipped"},
            "source_version": "v1",
            "service_status": "active",
            "truth": "wrong",
        },
    },
    {
        "memory_key": "obsolete_shortcut",
        "title": "Obsolete shortcut experience",
        "semantic_key": "maintenance.obsolete_shortcut",
        "payload": {
            "knowledge_key": "obsolete_shortcut",
            "version": "v1",
            "priority": 5,
            "applies_to": ["forget_nonrevive"],
            "action": "skip_checksum",
            "parameters": {"verification": "skipped"},
            "source_version": "v1",
            "service_status": "active",
            "truth": "obsolete",
        },
    },
)


def _body(payload: Mapping[str, Any]) -> str:
    return canonical_json(dict(payload))


def _frozen_case_payload() -> list[dict[str, Any]]:
    return [
        {
            "case_id": case["case_id"],
            "input": copy.deepcopy(case["input"]),
            "expected": copy.deepcopy(case["expected"]),
        }
        for case in _CASE_SPECS
    ]


FROZEN_INPUT_SHA256 = sha256_bytes(canonical_json(_frozen_case_payload()).encode("utf-8"))
CASES = tuple(copy.deepcopy(case) for case in _CASE_SPECS)

# This is the closed synthetic environment contract.  It is deliberately
# separate from ``expected`` so an observed result is produced by the local
# environment contract rather than copied from the evaluator oracle.
_ACTION_CONTRACTS: dict[str, dict[str, Any]] = {
    "use_warm_cache": {
        "parameters": {"cache": "warm", "mode": "prepare"},
        "status": "succeeded",
        "result_code": "prepared",
        "retry_allowed": False,
    },
    "skip_warm_cache": {
        "parameters": {"reason": "scope_mismatch"},
        "status": "succeeded",
        "result_code": "skipped",
        "retry_allowed": False,
    },
    "use_cold_cache": {
        "parameters": {"cache": "cold", "policy_version": "v2"},
        "status": "succeeded",
        "result_code": "processed",
        "retry_allowed": False,
    },
    "stop_processing": {
        "parameters": {"status": "paused"},
        "status": "succeeded",
        "result_code": "stopped",
        "retry_allowed": False,
    },
    "verify_checksum": {
        "parameters": {"verification": "checksum"},
        "status": "succeeded",
        "result_code": "verified",
        "retry_allowed": False,
    },
    "refresh_index": {
        "parameters": {"scope": "project"},
        "status": "failed",
        "result_code": "provider_unavailable",
        "retry_allowed": False,
    },
    "submit_export": {
        "parameters": {"request_id": "export-7"},
        "status": "unknown",
        "result_code": "result_unavailable",
        "retry_allowed": False,
    },
}


class _CallRecorder:
    def __init__(self) -> None:
        self.api_calls: list[dict[str, Any]] = []
        self.maintenance_actions: list[dict[str, Any]] = []

    def support(self, request: Mapping[str, Any], *, vault: Path) -> dict[str, Any] | None:
        started = time.perf_counter_ns()
        response: dict[str, Any] | None = None
        error_code: str | None = None
        try:
            value = handle_knowledge_support(vault_path=vault, **dict(request))
            response = value if isinstance(value, dict) else None
        except BaseException as error:  # Keep failures typed and bounded in the report.
            error_code = _error_code(error)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        self.api_calls.append(
            {
                "surface": "knowledge_support",
                "request": copy.deepcopy(dict(request)),
                "status": "completed" if response is not None else "failed",
                "error_code": error_code,
                "elapsed_ms": elapsed_ms,
            }
        )
        return response

    def context(self, request: Mapping[str, Any], *, vault: Path) -> dict[str, Any] | None:
        started = time.perf_counter_ns()
        response: dict[str, Any] | None = None
        error_code: str | None = None
        try:
            context_request = dict(request)
            context_request.pop("operation", None)
            context_request.pop("confirm_no_case_data", None)
            task = str(context_request.pop("task"))
            opened = KnowledgeOS.open(vault)
            try:
                response = opened.context.compile(
                    task=task,
                    confirm_no_case_data=True,
                    **context_request,
                )
            finally:
                opened.close()
        except BaseException as error:  # Keep failures typed and bounded in the report.
            error_code = _error_code(error)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        self.api_calls.append(
            {
                "surface": "knowledge_os.context",
                "request": copy.deepcopy(dict(request)),
                "status": "completed" if response is not None else "failed",
                "error_code": error_code,
                "elapsed_ms": elapsed_ms,
            }
        )
        return response

    def sink(
        self,
        request: Mapping[str, Any],
        *,
        grant_id: str,
        vault: Path,
        phase: str,
    ) -> dict[str, Any] | None:
        started = time.perf_counter_ns()
        response: dict[str, Any] | None = None
        error_code: str | None = None
        try:
            value = handle_knowledge_sink(dict(request), grant_id=grant_id, vault_path=vault)
            response = value if isinstance(value, dict) else None
        except BaseException as error:  # Keep failures typed and bounded in the report.
            error_code = _error_code(error)
        action = {
            "phase": phase,
            "surface": "knowledge_sink",
            "request": copy.deepcopy(dict(request)),
            "status": "completed" if response is not None else "failed",
            "error_code": error_code,
            "elapsed_ms": (time.perf_counter_ns() - started) / 1_000_000,
            "result": _result_summary(response),
        }
        self.maintenance_actions.append(action)
        return response


def _error_code(error: BaseException) -> str:
    if isinstance(error, PermissionError):
        return "permission_error"
    if isinstance(error, KeyError):
        return "key_error"
    if isinstance(error, ValueError):
        return "value_error"
    if isinstance(error, TypeError):
        return "type_error"
    if isinstance(error, RuntimeError):
        return "runtime_error"
    if isinstance(error, LookupError):
        return "lookup_error"
    if isinstance(error, AttributeError):
        return "attribute_error"
    return "other_error"


def _result_body(response: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(response, Mapping):
        return None
    result = response.get("result")
    return dict(result) if isinstance(result, Mapping) else None


_RESULT_SUMMARY_FIELDS = (
    "knowledge_id",
    "revision_id",
    "lifecycle",
    "run_id",
    "host_id",
    "model_id",
    "task_sha256",
    "receipt_sha256",
    "feedback_id",
    "status",
    "input_sha256",
    "output_sha256",
    "tool_results_sha256",
    "outcome",
    "evaluator_type",
    "task_success_authority",
)


def _result_summary(response: Mapping[str, Any] | None) -> dict[str, Any] | None:
    result = _result_body(response)
    if result is None:
        return None
    return {field: result[field] for field in _RESULT_SUMMARY_FIELDS if field in result}


def _seed_request(seed: Mapping[str, Any], *, suffix: str = "seed") -> dict[str, Any]:
    return {
        "operation": "remember",
        "idempotency_key": f"maintenance-{seed['memory_key']}-{suffix}",
        "confirm_no_case_data": True,
        "title": seed["title"],
        "body": _body(seed["payload"]),
        "kind": "experience",
        "semantic_key": seed["semantic_key"],
    }


def _memory_update_request(
    current: Mapping[str, Any],
    seed: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    suffix: str,
) -> dict[str, Any]:
    return {
        "operation": "remember",
        "idempotency_key": f"maintenance-{seed['memory_key']}-{suffix}",
        "confirm_no_case_data": True,
        "title": seed["title"],
        "body": _body(payload),
        "kind": "experience",
        "knowledge_id": current["knowledge_id"],
        "expected_revision_id": current["revision_id"],
        "semantic_key": seed["semantic_key"],
    }


def _forget_request(current: Mapping[str, Any], *, memory_key: str) -> dict[str, Any]:
    return {
        "operation": "forget",
        "idempotency_key": f"maintenance-{memory_key}-forget",
        "confirm_no_case_data": True,
        "knowledge_id": current["knowledge_id"],
        "expected_revision_id": current["revision_id"],
        "reason": "Synthetic maintenance fixture marked this experience obsolete.",
    }


def _memory_record(response: Mapping[str, Any] | None) -> dict[str, Any] | None:
    result = _result_body(response)
    if result is None:
        return None
    if not all(isinstance(result.get(field), str) for field in ("knowledge_id", "revision_id")):
        return None
    return result


def _create_vault(root: Path, *, name: str) -> Path:
    initialize_knowledge_vault(root, name=name, scope="project")
    initialize_autonomous_core(root)
    return root


def _grant(vault: Path) -> str:
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        result = store.enable_grant(
            writer_id="v013-maintenance-evaluator",
            operations=("forget", "remember", "record_run", "record_feedback"),
        )
    return str(result["grant_id"])


def _seed_memories(
    recorder: _CallRecorder,
    *,
    vault: Path,
    grant_id: str,
) -> dict[str, dict[str, Any]]:
    memories: dict[str, dict[str, Any]] = {}
    for seed in _SEEDS:
        response = recorder.sink(
            _seed_request(seed),
            grant_id=grant_id,
            vault=vault,
            phase="fixture_setup",
        )
        record = _memory_record(response)
        if record is not None:
            memories[seed["memory_key"]] = record
    return memories


def _successful_sink_result(response: Mapping[str, Any] | None) -> dict[str, Any] | None:
    result = _result_body(response)
    if result is None or not isinstance(result.get("lifecycle"), str):
        return None
    return result


def _maintain_before_case(
    case_id: str,
    *,
    recorder: _CallRecorder,
    memories: dict[str, dict[str, Any]],
    vault: Path,
    grant_id: str,
) -> list[dict[str, Any]]:
    actions_before = len(recorder.maintenance_actions)
    if case_id == "source_change" and "cache_policy" in memories:
        seed = _SEEDS[0]
        payload = dict(seed["payload"])
        payload.update(
            {
                "version": "v2",
                "priority": 10,
                "action": "use_cold_cache",
                "parameters": {"cache": "cold", "policy_version": "v2"},
                "source_version": "v2",
            }
        )
        response = recorder.sink(
            _memory_update_request(memories["cache_policy"], seed, payload, suffix="source-v2"),
            grant_id=grant_id,
            vault=vault,
            phase="source_change",
        )
        updated = _successful_sink_result(response)
        if updated is not None:
            memories["cache_policy"] = updated
    elif case_id == "status_change" and "status_policy" in memories:
        seed = _SEEDS[1]
        payload = dict(seed["payload"])
        payload.update(
            {
                "version": "v2",
                "priority": 10,
                "action": "stop_processing",
                "parameters": {"status": "paused"},
                "source_version": "v2",
                "service_status": "paused",
            }
        )
        response = recorder.sink(
            _memory_update_request(
                memories["status_policy"], seed, payload, suffix="status-paused"
            ),
            grant_id=grant_id,
            vault=vault,
            phase="status_change",
        )
        updated = _successful_sink_result(response)
        if updated is not None:
            memories["status_policy"] = updated
    elif case_id == "wrong_experience" and "checksum_policy" in memories:
        response = recorder.sink(
            _forget_request(memories["checksum_policy"], memory_key="checksum_policy"),
            grant_id=grant_id,
            vault=vault,
            phase="wrong_experience",
        )
        if _successful_sink_result(response) is not None:
            memories.pop("checksum_policy", None)
    elif case_id == "repair_reuse":
        seed = {
            "memory_key": "checksum_repair",
            "title": "Repaired checksum verification experience",
            "semantic_key": "maintenance.checksum_repair",
            "payload": {
                "knowledge_key": "checksum_policy",
                "version": "v2",
                "priority": 10,
                "applies_to": ["repair_reuse"],
                "action": "verify_checksum",
                "parameters": {"verification": "checksum"},
                "source_version": "v2",
                "service_status": "active",
                "truth": "supported",
            },
        }
        response = recorder.sink(
            _seed_request(seed, suffix="repair"),
            grant_id=grant_id,
            vault=vault,
            phase="repair_reuse",
        )
        record = _memory_record(response)
        if record is not None:
            memories[seed["memory_key"]] = record
    elif case_id == "forget_nonrevive" and "obsolete_shortcut" in memories:
        response = recorder.sink(
            _forget_request(memories["obsolete_shortcut"], memory_key="obsolete_shortcut"),
            grant_id=grant_id,
            vault=vault,
            phase="forget_nonrevive",
        )
        if _successful_sink_result(response) is not None:
            memories.pop("obsolete_shortcut", None)
    return copy.deepcopy(recorder.maintenance_actions[actions_before:])


def _valid_memory_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    required = {
        "knowledge_key",
        "version",
        "priority",
        "applies_to",
        "action",
        "parameters",
        "source_version",
        "service_status",
        "truth",
    }
    if set(value) != required:
        return None
    if (
        not isinstance(value["knowledge_key"], str)
        or not isinstance(value["version"], str)
        or isinstance(value["priority"], bool)
        or not isinstance(value["priority"], int)
        or not isinstance(value["applies_to"], list)
        or any(not isinstance(item, str) for item in value["applies_to"])
        or not isinstance(value["action"], str)
        or not isinstance(value["parameters"], dict)
        or not isinstance(value["source_version"], str)
        or not isinstance(value["service_status"], str)
        or value["truth"] not in {"supported", "wrong", "obsolete"}
    ):
        return None
    return dict(value)


def _payload_from_revision(revision: Mapping[str, Any]) -> dict[str, Any] | None:
    content = revision.get("content")
    if not isinstance(content, str) or revision.get("content_truncated") is True:
        return None
    try:
        return _valid_memory_payload(json.loads(content))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _capsule_from_response(response: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(response, Mapping):
        return None
    if isinstance(response.get("capsule"), Mapping):
        return dict(response["capsule"])
    result = response.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("capsule"), Mapping):
        return dict(result["capsule"])
    if isinstance(result, Mapping):
        return dict(result)
    return dict(response)


def _context_summary(
    response: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(response, Mapping):
        return {"status": "failed", "error_code": "context_unavailable"}, []
    capsule = _capsule_from_response(response)
    if not isinstance(capsule, Mapping):
        return {
            "status": "failed",
            "error_code": "context_capsule_unavailable",
            "response_schema_version": response.get("schema_version"),
        }, []
    revisions = capsule.get("knowledge_revisions")
    if not isinstance(revisions, list):
        return {"status": "failed", "error_code": "knowledge_revisions_unavailable"}, []
    selected: list[dict[str, Any]] = []
    for revision in revisions:
        if not isinstance(revision, Mapping):
            continue
        if not all(
            isinstance(revision.get(field), str) for field in ("knowledge_id", "revision_id")
        ):
            continue
        selected.append(
            {
                "knowledge_id": revision["knowledge_id"],
                "revision_id": revision["revision_id"],
                "kind": revision.get("kind"),
                "title": revision.get("title"),
                "content": revision.get("content"),
                "content_truncated": revision.get("content_truncated"),
                "source_free": revision.get("source_free"),
                "epistemic_state": revision.get("epistemic_state"),
                "lifecycle": revision.get("lifecycle"),
            }
        )
    budget = capsule.get("budget") if isinstance(capsule, Mapping) else None
    budget_summary = {
        field: budget.get(field)
        for field in ("selected_tokens", "token_count_mode")
        if isinstance(budget, Mapping) and field in budget
    }
    summary = {
        "status": "completed",
        "response_schema_version": response.get("schema_version"),
        "query_plan_version": QUERY_PLAN_VERSION,
        "capsule_schema_version": capsule.get("schema_version"),
        "selected_revision_count": len(selected),
        "selected_revision_ids": [item["revision_id"] for item in selected],
        "knowledge_revision_projection": copy.deepcopy(selected),
    }
    if budget_summary:
        summary["budget"] = budget_summary
    return summary, selected


def _verify_local_capsule(
    response: Mapping[str, Any] | None,
    *,
    vault: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    capsule = _capsule_from_response(response)
    if capsule is None:
        return None, {"valid": False, "error_code": "capsule_unavailable"}
    try:
        with KnowledgeVault(vault, read_only=True) as legacy_vault:
            verification = verify_capsule(capsule, vault=legacy_vault)
    except BaseException as error:
        return None, {
            "valid": False,
            "capsule_id": capsule.get("capsule_id"),
            "capsule_digest": capsule.get("capsule_digest"),
            "error_code": _error_code(error),
        }
    fields = (
        "schema_version",
        "capsule_id",
        "digest_valid",
        "id_valid",
        "query_plan_valid",
        "provider_schema_valid",
        "selection_identity_valid",
        "vault_matches",
        "audit_anchor_valid",
        "autonomous_integrity_valid",
        "valid",
    )
    summary = {field: verification[field] for field in fields if field in verification}
    summary["capsule_digest"] = capsule.get("capsule_digest")
    return (capsule if verification.get("valid") is True else None), summary


def _synthetic_task_binding(case: Mapping[str, Any], *, vault: Path) -> dict[str, Any]:
    with KnowledgeVault(vault, read_only=True) as legacy_vault:
        vault_id = legacy_vault.vault_id
    task = str(case["input"]["task"])
    case_id = str(case["case_id"])
    return build_task_context_binding(
        sha256_bytes(f"synthetic-project\0{vault_id}".encode()),
        sha256_bytes(f"synthetic-task\0{case_id}\0{task}".encode()),
        parent_task_lineage_sha256=sha256_bytes(
            b"synthetic-parent\0knowledge-maintenance-outcomes"
        ),
        repository_sha256=sha256_bytes(f"synthetic-repository\0{vault_id}".encode()),
        worktree_sha256=sha256_bytes(f"synthetic-workspace\0{vault_id}".encode()),
        base_revision=sha256_bytes(f"synthetic-base\0{vault_id}".encode())[:40],
        dirty_state_sha256=sha256_bytes(f"synthetic-dirty\0{vault_id}".encode()),
    )


def _canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _run_status_for_observation(value: Any) -> str:
    status = value.get("status") if isinstance(value, Mapping) else None
    return {
        "succeeded": "succeeded",
        "failed": "failed",
        "unknown": "partial",
    }.get(status, "aborted")


def _feedback_outcome(score: Mapping[str, Any]) -> str:
    if score.get("case_passed") is True:
        return "helpful"
    if score.get("outcome_unknown") is True:
        return "neutral"
    if (
        score.get("environment_failure") is True
        or score.get("stale_usage")
        or score.get("constraint_violations")
    ):
        return "harmful"
    return "noisy"


def _capsule_summary(
    capsule: Mapping[str, Any] | None,
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    summary = {
        "capsule_id": capsule.get("capsule_id") if capsule is not None else None,
        "capsule_digest": capsule.get("capsule_digest") if capsule is not None else None,
        "verification": copy.deepcopy(dict(verification)),
    }
    return summary


def _select_memory(
    case_id: str,
    revisions: Sequence[Mapping[str, Any]],
    *,
    recorder: _CallRecorder,
    vault: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    candidates: list[tuple[int, int, str, Mapping[str, Any], dict[str, Any]]] = []
    for revision in revisions:
        payload = _payload_from_revision(revision)
        if payload is None or case_id not in payload["applies_to"]:
            continue
        version_text = payload["version"].removeprefix("v")
        version_number = int(version_text) if version_text.isdigit() else -1
        candidates.append(
            (
                int(payload["priority"]),
                version_number,
                str(revision["revision_id"]),
                revision,
                payload,
            )
        )
    if not candidates:
        return None, None, {"status": "not_selected", "candidate_count": 0}
    _, _, _, selected_revision, payload = max(candidates, key=lambda item: item[:3])
    read_request = {
        "operation": "get",
        "knowledge_id": selected_revision["knowledge_id"],
        "scope": "project",
        "max_sensitivity": "private",
        "max_chars": 12_000,
    }
    read_response = recorder.support(read_request, vault=vault)
    read_result = read_response.get("result") if isinstance(read_response, Mapping) else None
    if (
        not isinstance(read_result, Mapping)
        or read_result.get("revision_id") != selected_revision["revision_id"]
    ):
        return (
            None,
            None,
            {
                "status": "failed",
                "candidate_count": len(candidates),
                "read_revision_id": selected_revision["revision_id"],
            },
        )
    read_body = read_result.get("body")
    try:
        read_payload = (
            _valid_memory_payload(json.loads(read_body)) if isinstance(read_body, str) else None
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        read_payload = None
    if read_payload is None:
        return None, None, {"status": "failed", "candidate_count": len(candidates)}
    reference = {
        "knowledge_id": selected_revision["knowledge_id"],
        "revision_id": selected_revision["revision_id"],
        "knowledge_key": read_payload["knowledge_key"],
        "version": read_payload["version"],
        "priority": read_payload["priority"],
        "applies_to": list(read_payload["applies_to"]),
        "source_version": read_payload["source_version"],
        "service_status": read_payload["service_status"],
        "truth": read_payload["truth"],
    }
    return (
        read_payload,
        reference,
        {
            "status": "selected",
            "candidate_count": len(candidates),
            "selected_revision_id": selected_revision["revision_id"],
            "read_schema_version": read_response.get("schema_version"),
        },
    )


def _environment_result(
    case: Mapping[str, Any],
    *,
    action: str,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    contract = _ACTION_CONTRACTS.get(action)
    environment = case["input"]["environment"]
    if contract is not None and dict(parameters) == contract["parameters"]:
        return {
            "status": contract["status"],
            "result_code": contract["result_code"],
            "retry_allowed": contract["retry_allowed"],
            "state": copy.deepcopy(environment),
        }
    return {
        "status": environment.get("terminal_status", "failed"),
        "result_code": "wrong_parameters" if contract is not None else "wrong_action",
        "retry_allowed": False,
        "state": copy.deepcopy(environment),
    }


def _run_case(
    case: Mapping[str, Any],
    *,
    recorder: _CallRecorder,
    memories: Mapping[str, Mapping[str, Any]],
    vault: Path,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    context_request = {
        "operation": "context",
        "task": case["input"]["task"],
        "goal": case["input"]["goal"],
        "confirm_no_case_data": True,
        "query_plan_version": QUERY_PLAN_VERSION,
        "purpose": "answer",
        "policy": "compiled-first-v1",
        "scope": "project",
        "max_sensitivity": "private",
        "limit": 8,
        "max_chars": 8_000,
        "max_tokens": 6_000,
        "max_sources": 12,
        "graph_hops": 1,
        "retrieval_mode": "hybrid",
    }
    context_response = recorder.context(context_request, vault=vault)
    context, revisions = _context_summary(context_response)
    capsule, capsule_verification = _verify_local_capsule(context_response, vault=vault)
    payload, memory_reference, selection = _select_memory(
        case["case_id"], revisions, recorder=recorder, vault=vault
    )
    if payload is None:
        action = case["input"]["fallback_action"]
        parameters = copy.deepcopy(case["input"]["fallback_parameters"])
    else:
        action = payload["action"]
        parameters = copy.deepcopy(payload["parameters"])
    result = _environment_result(case, action=action, parameters=parameters)
    attempt = {
        "ordinal": 1,
        "action": action,
        "parameters": parameters,
        "memory": memory_reference,
        "environment_result": result,
    }
    observation = {
        "case_id": case["case_id"],
        "execution_status": "executed",
        "attempts": [attempt],
        "context": context,
        "capsule": _capsule_summary(capsule, capsule_verification),
        "capsule_verification": copy.deepcopy(capsule_verification),
        "_verified_capsule": capsule,
        "memory_selection": selection,
        "environment_result": result,
        "elapsed_ms": (time.perf_counter_ns() - started) / 1_000_000,
        "api_calls": copy.deepcopy(recorder.api_calls),
    }
    return observation


def _expected_parts(expected: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    action = expected.get("action", expected.get("expected_action"))
    parameters = expected.get("parameters", expected.get("expected_parameters"))
    result = expected.get("typed_result", expected.get("expected_typed_result"))
    return action, parameters, result


def _attempt_result(attempt: Mapping[str, Any]) -> Any:
    for key in ("environment_result", "typed_result", "result"):
        if key in attempt:
            return attempt[key]
    return None


def _attempts(observed: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    values = observed.get("attempts")
    if isinstance(values, list):
        return [item for item in values if isinstance(item, Mapping)]
    return [observed]


def score_case_observation(
    expected: Mapping[str, Any], observed: Mapping[str, Any]
) -> dict[str, Any]:
    """Score one typed observation using exact action/parameter/result equality."""

    expected_action, expected_parameters, expected_result = _expected_parts(expected)
    attempts = _attempts(observed)
    failure_codes: list[str] = []
    repeated_actions: list[dict[str, Any]] = []
    stale_usage: list[dict[str, Any]] = []
    constraint_violations: list[str] = []
    benign_damage: list[str] = []
    if not attempts:
        failure_codes.append("missing_action")
        first: Mapping[str, Any] = {}
    else:
        first = attempts[0]
    action_correct = first.get("action") == expected_action
    parameters_correct = isinstance(expected_parameters, Mapping) and first.get(
        "parameters"
    ) == dict(expected_parameters)
    result_value = _attempt_result(first)
    typed_result_correct = result_value == expected_result
    if not action_correct:
        failure_codes.append("wrong_action")
    if not parameters_correct:
        failure_codes.append("wrong_parameters")
    if not typed_result_correct:
        failure_codes.append("typed_result_mismatch")
    context = observed.get("context")
    if isinstance(context, Mapping) and context.get("status") != "completed":
        failure_codes.append("context_failed")
    memory_selection = observed.get("memory_selection")
    if isinstance(memory_selection, Mapping) and memory_selection.get("status") == "failed":
        failure_codes.append("memory_read_failed")
    if len(attempts) > 1:
        failure_codes.append("repeated_action")
        first_seen: dict[str, Any] = {}
        for attempt in attempts:
            action = attempt.get("action")
            if isinstance(action, str) and action in first_seen:
                repeated_actions.append(
                    {
                        "first_ordinal": first_seen[action],
                        "repeat_ordinal": attempt.get("ordinal"),
                        "action": action,
                    }
                )
            elif isinstance(action, str):
                first_seen[action] = attempt.get("ordinal")
        expected_status = (
            expected_result.get("status") if isinstance(expected_result, Mapping) else None
        )
        if expected_status in {"failed", "unknown"}:
            failure_codes.append("redo_after_non_success")
    memory = first.get("memory")
    expected_state = expected_result.get("state") if isinstance(expected_result, Mapping) else None
    if isinstance(memory, Mapping):
        applies_to = memory.get("applies_to")
        case_id = observed.get("case_id")
        if isinstance(applies_to, list) and case_id not in applies_to:
            constraint_violations.append("inapplicable_memory_used")
        if memory.get("truth") in {"wrong", "obsolete"}:
            constraint_violations.append("known_bad_memory_used")
        if isinstance(expected_state, Mapping):
            for field in ("source_version", "service_status"):
                if (
                    memory.get(field) is not None
                    and expected_state.get(field) is not None
                    and memory.get(field) != expected_state.get(field)
                ):
                    stale_usage.append(
                        {
                            "field": field,
                            "memory_value": memory.get(field),
                            "current_value": expected_state.get(field),
                        }
                    )
    if constraint_violations:
        failure_codes.append("constraint_violation")
    if stale_usage:
        failure_codes.append("stale_memory_used")
    if (not action_correct or stale_usage or constraint_violations) and expected.get(
        "damage_class"
    ) == "benign":
        benign_damage.append("benign_environment_damage")
    expected_status = (
        expected_result.get("status") if isinstance(expected_result, Mapping) else None
    )
    observed_status = result_value.get("status") if isinstance(result_value, Mapping) else None
    if observed_status == "failed":
        failure_codes.append("environment_failure")
    elif observed_status == "unknown":
        failure_codes.append("unknown_result")
    retry_correct = (
        expected_status in {"failed", "unknown"}
        and observed_status == expected_status
        and len(attempts) == 1
    )
    successful_outcome = observed_status == "succeeded" and not failure_codes
    return {
        "case_passed": successful_outcome,
        "passed": successful_outcome,
        "action_correct": action_correct,
        "parameters_correct": parameters_correct,
        "typed_result_correct": typed_result_correct,
        "termination_correct": (
            retry_correct if expected_status in {"failed", "unknown"} else successful_outcome
        ),
        "terminal_result_recorded": observed_status in {"failed", "unknown"},
        "retry_correct": retry_correct,
        "environment_failure": observed_status == "failed",
        "outcome_unknown": observed_status == "unknown",
        "failure_codes": failure_codes,
        "repeated_actions": repeated_actions,
        "stale_usage": stale_usage,
        "constraint_violations": constraint_violations,
        "benign_damage": benign_damage,
    }


score_case = score_case_observation


def _record_run_and_feedback(
    case: Mapping[str, Any],
    *,
    configuration_id: str,
    recorder: _CallRecorder,
    observation: dict[str, Any],
    vault: Path,
    grant_id: str,
) -> None:
    capsule = observation.pop("_verified_capsule", None)
    capsule_verification = observation.get("capsule_verification", {})
    typed_result = observation.get("environment_result")
    output_sha256 = _canonical_digest(typed_result)
    run_record: dict[str, Any] = {
        "status": "not_attempted",
        "error_code": None,
        "run_id": None,
        "receipt_id": None,
        "receipt_sha256": None,
        "host_id": "synthetic-knowledge-maintenance-host",
        "model_id": None,
        "recorded_status": None,
        "input_sha256": None,
        "output_sha256": output_sha256,
        "tool_results_sha256": output_sha256,
    }
    feedback_record: dict[str, Any] = {
        "status": "not_attempted",
        "error_code": None,
        "feedback_id": None,
        "knowledge_id": None,
        "revision_id": None,
        "run_id": None,
        "outcome": None,
        "evaluator_type": None,
        "task_success_authority": None,
    }
    action_start = len(recorder.maintenance_actions)
    run_result: dict[str, Any] | None = None
    if not isinstance(capsule, dict) or capsule_verification.get("valid") is not True:
        run_record["status"] = "failed"
        run_record["error_code"] = "capsule_verification_failed"
    else:
        input_sha256 = capsule.get("capsule_digest")
        run_status = _run_status_for_observation(typed_result)
        binding = _synthetic_task_binding(case, vault=vault)
        run_request = {
            "operation": "record_run",
            "idempotency_key": f"maintenance-{configuration_id}-{case['case_id']}-run",
            "confirm_no_case_data": True,
            "task": case["input"]["task"],
            "host_id": "synthetic-knowledge-maintenance-host",
            "status": run_status,
            "scope": "project",
            "sensitivity": "private",
            "input_sha256": input_sha256,
            "output_sha256": output_sha256,
            "tool_results_sha256": output_sha256,
            "run_metadata": {
                "task_kind": "synthetic_maintenance_fixture",
                "artifact_ids": [capsule["capsule_id"]],
                "task_binding": binding,
            },
        }
        run_response = recorder.sink(
            run_request,
            grant_id=grant_id,
            vault=vault,
            phase="run_record",
        )
        run_result = _result_body(run_response)
        run_record.update(
            {
                "status": "completed" if run_result is not None else "failed",
                "error_code": None
                if run_result is not None
                else recorder.maintenance_actions[-1].get("error_code"),
                "input_sha256": input_sha256,
                "requested_status": run_status,
            }
        )
        if run_result is not None:
            run_record.update(
                {
                    "run_id": run_result.get("run_id"),
                    "receipt_id": run_result.get("run_id"),
                    "receipt_sha256": run_result.get("receipt_sha256"),
                    "host_id": run_result.get("host_id"),
                    "model_id": run_result.get("model_id"),
                    "recorded_status": run_result.get("status"),
                    "output_sha256": run_result.get("output_sha256"),
                    "tool_results_sha256": run_result.get("tool_results_sha256"),
                }
            )
            if not (
                isinstance(run_result.get("run_id"), str)
                and isinstance(run_result.get("receipt_sha256"), str)
                and run_result.get("status") == run_status
                and run_result.get("input_sha256") == input_sha256
                and run_result.get("output_sha256") == output_sha256
                and run_result.get("tool_results_sha256") == output_sha256
            ):
                run_record["status"] = "failed"
                run_record["error_code"] = "run_receipt_mismatch"

    memory = None
    attempts = observation.get("attempts")
    if isinstance(attempts, list) and attempts and isinstance(attempts[0], Mapping):
        memory = attempts[0].get("memory")
    if not isinstance(memory, Mapping):
        feedback_record["error_code"] = "no_selected_knowledge"
    elif run_record["status"] != "completed" or not isinstance(run_result, Mapping):
        feedback_record["error_code"] = "run_record_unavailable"
    else:
        outcome = _feedback_outcome(observation["score"])
        feedback_request = {
            "operation": "record_feedback",
            "idempotency_key": (f"maintenance-{configuration_id}-{case['case_id']}-feedback"),
            "confirm_no_case_data": True,
            "knowledge_id": memory["knowledge_id"],
            "expected_revision_id": memory["revision_id"],
            "run_id": run_result["run_id"],
            "outcome": outcome,
            "evaluator_type": "agent_self_report",
            "feedback_note": "Deterministic synthetic evaluator recorded an exact typed outcome.",
        }
        feedback_response = recorder.sink(
            feedback_request,
            grant_id=grant_id,
            vault=vault,
            phase="feedback",
        )
        feedback_result = _result_body(feedback_response)
        feedback_record.update(
            {
                "status": "completed" if feedback_result is not None else "failed",
                "error_code": None
                if feedback_result is not None
                else recorder.maintenance_actions[-1].get("error_code"),
                "knowledge_id": memory["knowledge_id"],
                "revision_id": memory["revision_id"],
                "run_id": run_result["run_id"],
                "outcome": outcome,
                "evaluator_type": "agent_self_report",
                "task_success_authority": (
                    feedback_result.get("task_success_authority")
                    if feedback_result is not None
                    else None
                ),
            }
        )
        if feedback_result is not None:
            feedback_record["feedback_id"] = feedback_result.get("feedback_id")

    try:
        with AutonomousKnowledgeStore(vault, read_only=True) as store:
            ledger = store.verify()
        ledger_verification = {
            "valid": ledger.get("valid") is True,
            "failure_codes": [
                item.get("code")
                for item in ledger.get("failures", [])
                if isinstance(item, Mapping) and isinstance(item.get("code"), str)
            ],
            "warning_codes": [
                item.get("code")
                for item in ledger.get("warnings", [])
                if isinstance(item, Mapping) and isinstance(item.get("code"), str)
            ],
        }
    except BaseException as error:
        ledger_verification = {
            "valid": False,
            "failure_codes": [_error_code(error)],
            "warning_codes": [],
        }
    observation["run_record"] = run_record
    observation["feedback"] = feedback_record
    observation["ledger_verification"] = ledger_verification
    observation["run_feedback_actions"] = copy.deepcopy(recorder.maintenance_actions[action_start:])


def _aggregate(
    results: Sequence[Mapping[str, Any]],
    maintenance_actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    scores = [item.get("score", {}) for item in results]
    status_counts = Counter(
        str(item.get("environment_result", {}).get("status"))
        for item in results
        if isinstance(item.get("environment_result"), Mapping)
    )
    failures = Counter(
        code for score in scores for code in score.get("failure_codes", []) if isinstance(code, str)
    )
    return {
        "case_count": len(results),
        "passed_case_count": sum(bool(score.get("case_passed")) for score in scores),
        "correct_action_count": sum(bool(score.get("action_correct")) for score in scores),
        "environment_success_count": sum(
            bool(score.get("case_passed"))
            and item.get("environment_result", {}).get("status") == "succeeded"
            for item, score in zip(results, scores, strict=True)
        ),
        "environment_failure_count": status_counts.get("failed", 0),
        "unknown_result_count": status_counts.get("unknown", 0),
        "repeated_action_count": sum(len(score.get("repeated_actions", [])) for score in scores),
        "redo_after_non_success_count": sum(
            "redo_after_non_success" in score.get("failure_codes", []) for score in scores
        ),
        "stale_usage_count": sum(len(score.get("stale_usage", [])) for score in scores),
        "constraint_violation_count": sum(
            len(score.get("constraint_violations", [])) for score in scores
        ),
        "benign_damage_count": sum(len(score.get("benign_damage", [])) for score in scores),
        "maintenance_action_count": len(maintenance_actions),
        "candidate_maintenance_action_count": sum(
            item.get("phase") in _MAINTENANCE_PHASES for item in maintenance_actions
        ),
        "run_record_count": sum(item.get("phase") == "run_record" for item in maintenance_actions),
        "feedback_count": sum(item.get("phase") == "feedback" for item in maintenance_actions),
        "maintenance_failure_count": sum(
            item.get("status") == "failed" for item in maintenance_actions
        ),
        "environment_status_counts": dict(sorted(status_counts.items())),
        "failure_code_counts": dict(sorted(failures.items())),
        "elapsed_ms": sum(float(item.get("elapsed_ms", 0.0)) for item in results),
    }


def _run_configuration(configuration_id: str, *, parent: Path | None) -> dict[str, Any]:
    with _temporary_vault(parent, name=f"maintenance-{configuration_id}") as vault:
        grant_id = _grant(vault)
        recorder = _CallRecorder()
        memories = (
            _seed_memories(recorder, vault=vault, grant_id=grant_id)
            if configuration_id != "no_memory"
            else {}
        )
        fixture_setup_failures = (
            ["seed_result_unavailable"]
            if configuration_id != "no_memory" and len(memories) != len(_SEEDS)
            else []
        )
        fixture_setup_failures.extend(
            action.get("error_code") or "sink_failed"
            for action in recorder.maintenance_actions
            if action.get("phase") == "fixture_setup" and action.get("status") == "failed"
        )
        results: list[dict[str, Any]] = []
        for case in _CASE_SPECS:
            maintenance = (
                _maintain_before_case(
                    case["case_id"],
                    recorder=recorder,
                    memories=memories,
                    vault=vault,
                    grant_id=grant_id,
                )
                if configuration_id == "governed_maintenance"
                else []
            )
            api_start = len(recorder.api_calls)
            observation = _run_case(
                case,
                recorder=recorder,
                memories=memories,
                vault=vault,
            )
            score = score_case_observation(case["expected"], observation)
            if any(action.get("status") == "failed" for action in maintenance):
                score["failure_codes"].append("maintenance_failed")
                score["case_passed"] = False
                score["passed"] = False
            observation["score"] = score
            _record_run_and_feedback(
                case,
                configuration_id=configuration_id,
                recorder=recorder,
                observation=observation,
                vault=vault,
                grant_id=grant_id,
            )
            observation["maintenance_actions"] = maintenance
            observation["api_calls"] = copy.deepcopy(recorder.api_calls[api_start:])
            observation["failures"] = list(score["failure_codes"])
            observation["repeated_actions"] = copy.deepcopy(score["repeated_actions"])
            observation["stale_usage"] = copy.deepcopy(score["stale_usage"])
            observation["constraint_violations"] = list(score["constraint_violations"])
            observation["benign_damage"] = list(score["benign_damage"])
            results.append(observation)
        return {
            "configuration_id": configuration_id,
            "execution_status": "failed" if fixture_setup_failures else "executed",
            "candidate_mode": "deterministic_synthetic_policy",
            "learned_from_model": False,
            "model_execution": "not_executed",
            "token_cost": {
                "input_tokens": "unavailable",
                "output_tokens": "unavailable",
                "total_tokens": "unavailable",
                "monetary_cost": "unavailable",
            },
            "repeat_count": REPEAT_COUNT,
            "case_order": list(CASE_ORDER),
            "fixture_setup_failures": fixture_setup_failures,
            "fixture_setup_actions": [
                action
                for action in recorder.maintenance_actions
                if action.get("phase") == "fixture_setup"
            ],
            "maintenance_actions": copy.deepcopy(recorder.maintenance_actions),
            "run_feedback_actions": [
                action
                for action in recorder.maintenance_actions
                if action.get("phase") in _RUN_FEEDBACK_PHASES
            ],
            "per_case": results,
            "aggregate": _aggregate(results, recorder.maintenance_actions),
        }


@contextmanager
def _temporary_vault(parent: Path | None, *, name: str) -> Iterator[Path]:
    if parent is None:
        with tempfile.TemporaryDirectory(prefix="deeplaw-maintenance-") as temporary:
            yield _create_vault(Path(temporary) / "vault", name=name)
        return
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fixture-", dir=parent) as temporary:
        yield _create_vault(Path(temporary) / "vault", name=name)


def _failed_configuration(configuration_id: str, error: BaseException) -> dict[str, Any]:
    code = _error_code(error)
    return {
        "configuration_id": configuration_id,
        "execution_status": "failed",
        "candidate_mode": "deterministic_synthetic_policy",
        "learned_from_model": False,
        "model_execution": "not_executed",
        "token_cost": {
            "input_tokens": "unavailable",
            "output_tokens": "unavailable",
            "total_tokens": "unavailable",
            "monetary_cost": "unavailable",
        },
        "repeat_count": REPEAT_COUNT,
        "case_order": list(CASE_ORDER),
        "fixture_setup_failures": [f"configuration_error:{code}"],
        "fixture_setup_actions": [],
        "maintenance_actions": [],
        "run_feedback_actions": [],
        "per_case": [
            {
                "case_id": case_id,
                "execution_status": "not_executed",
                "attempts": [],
                "maintenance_actions": [],
                "failures": [f"configuration_error:{code}"],
                "repeated_actions": [],
                "stale_usage": [],
                "constraint_violations": [],
                "benign_damage": [],
                "elapsed_ms": 0.0,
                "score": {
                    "case_passed": False,
                    "passed": False,
                    "failure_codes": [f"configuration_error:{code}"],
                },
            }
            for case_id in CASE_ORDER
        ],
        "aggregate": {
            "case_count": len(CASE_ORDER),
            "passed_case_count": 0,
            "correct_action_count": 0,
            "environment_success_count": 0,
            "environment_failure_count": 0,
            "unknown_result_count": 0,
            "repeated_action_count": 0,
            "redo_after_non_success_count": 0,
            "stale_usage_count": 0,
            "constraint_violation_count": 0,
            "benign_damage_count": 0,
            "maintenance_action_count": 0,
            "candidate_maintenance_action_count": 0,
            "run_record_count": 0,
            "feedback_count": 0,
            "maintenance_failure_count": 0,
            "environment_status_counts": {},
            "failure_code_counts": {f"configuration_error:{code}": len(CASE_ORDER)},
            "elapsed_ms": 0.0,
        },
    }


def build_report(workspace: Path | None = None) -> dict[str, Any]:
    """Run the fixed synthetic configurations and return a claim-ineligible report."""

    if workspace is not None and not isinstance(workspace, Path):
        workspace = Path(workspace)
    configurations: list[dict[str, Any]] = []
    for configuration_id in CONFIGURATION_ORDER:
        try:
            configurations.append(_run_configuration(configuration_id, parent=workspace))
        except BaseException as error:  # Preserve a bounded candidate result per configuration.
            configurations.append(_failed_configuration(configuration_id, error))
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "evaluation_mode": "deterministic_development",
        "status": (
            "executed"
            if all(item["execution_status"] == "executed" for item in configurations)
            else "failed"
        ),
        "claim_eligible": False,
        "release_gate_passed": False,
        "independent_holdout": False,
        "real_host_execution": False,
        "comparative_superiority_claim_eligible": False,
        "model_execution": "not_executed",
        "network_used": False,
        "authentication": "not_requested",
        "frozen_input": {
            "frozen_before_candidate_execution": True,
            "case_count": len(CASES),
            "case_order": list(CASE_ORDER),
            "repeat_count": REPEAT_COUNT,
            "input_sha256": FROZEN_INPUT_SHA256,
            "cases": _frozen_case_payload(),
        },
        "public_surfaces": [
            "knowledge_sink.remember",
            "knowledge_sink.forget",
            "knowledge_sink.record_run",
            "knowledge_sink.record_feedback",
            "knowledge_os.context.query_plan_v7",
            "knowledge_support.get",
        ],
        "configurations": configurations,
        "cost": {
            "input_tokens": "unavailable",
            "output_tokens": "unavailable",
            "total_tokens": "unavailable",
            "monetary_cost": "unavailable",
        },
        "limitations": [
            "Development-only pure local synthetic state; frozen cases and typed "
            "expectations are evaluator-visible.",
            "The deterministic transitions are harness policy actions and do not "
            "establish model learning.",
            "Fallback actions and typed environment contracts are preprogrammed "
            "controls; their agreement with frozen expectations is not a learned "
            "maintenance result.",
            "No real Host, model, competitor, independent holdout, statistical lead, "
            "release, or formal qualification was executed.",
            "Source and status changes are typed local environment transitions; no "
            "external source Authority is inferred.",
            "Capsules are verified through the modern autonomous Capsule/Run/feedback "
            "Ledger; the legacy knowledge_feedback.py asset-Capsule-v1 path is not used.",
            "Token usage and monetary cost are unavailable because no model ran.",
        ],
    }
    report["report_sha256"] = sha256_bytes(canonical_json(_digest_body(report)).encode("utf-8"))
    return report


build_knowledge_maintenance_outcomes_report = build_report
build_outcomes_report = build_report


def _digest_body(report: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(report))
    body.pop("report_sha256", None)
    return body


def _validate_score(expected: Mapping[str, Any], observation: Mapping[str, Any]) -> list[str]:
    if observation.get("execution_status") == "not_executed":
        stored = observation.get("score")
        failures = stored.get("failure_codes") if isinstance(stored, Mapping) else None
        if (
            isinstance(stored, Mapping)
            and stored.get("case_passed") is False
            and isinstance(failures, list)
            and len(failures) == 1
            and isinstance(failures[0], str)
            and failures[0].startswith("configuration_error:")
        ):
            return []
    actual = score_case_observation(expected, observation)
    stored = observation.get("score")
    if not isinstance(stored, Mapping):
        return ["case score is missing"]
    if dict(stored) != actual:
        return ["case score does not match exact observation"]
    return []


_RUN_ID_PATTERN = re.compile(r"^run_[0-9a-f]{24}$")
_BASE_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _validate_run_feedback(case: Mapping[str, Any], observation: Mapping[str, Any]) -> list[str]:
    if observation.get("execution_status") == "not_executed":
        return []
    errors: list[str] = []
    capsule = observation.get("capsule")
    capsule_verification = observation.get("capsule_verification")
    capsule_digest: str | None = None
    capsule_id: str | None = None
    if not isinstance(capsule, Mapping):
        errors.append("per-case capsule is missing")
    else:
        capsule_id = capsule.get("capsule_id")
        capsule_digest = capsule.get("capsule_digest")
        if not isinstance(capsule_id, str) or not capsule_id:
            errors.append("per-case capsule ID is invalid")
        if not isinstance(capsule_digest, str) or not _SHA256.fullmatch(capsule_digest):
            errors.append("per-case capsule digest is invalid")
    if (
        not isinstance(capsule_verification, Mapping)
        or capsule_verification.get("valid") is not True
    ):
        errors.append("per-case capsule verification is not valid")
    elif capsule_verification.get("capsule_digest") != capsule_digest:
        errors.append("per-case capsule verification digest mismatch")

    environment_result = observation.get("environment_result")
    expected_output_digest = _canonical_digest(environment_result)
    run_record = observation.get("run_record")
    if not isinstance(run_record, Mapping):
        errors.append("per-case run record is missing")
        run_record = {}
    run_status = run_record.get("status")
    if run_status not in {"completed", "failed", "not_attempted"}:
        errors.append("per-case run record status is invalid")
    run_feedback_actions = observation.get("run_feedback_actions")
    if not isinstance(run_feedback_actions, list):
        errors.append("per-case run/feedback action inventory is missing")
        run_feedback_actions = []
    run_actions = [
        action
        for action in run_feedback_actions
        if isinstance(action, Mapping) and action.get("phase") == "run_record"
    ]
    feedback_actions = [
        action
        for action in run_feedback_actions
        if isinstance(action, Mapping) and action.get("phase") == "feedback"
    ]
    if any(
        not isinstance(action, Mapping) or action.get("phase") not in _RUN_FEEDBACK_PHASES
        for action in run_feedback_actions
    ):
        errors.append("per-case run/feedback action phase is invalid")

    if run_status == "completed":
        if len(run_actions) != 1:
            errors.append("completed run record does not have one sink action")
        else:
            action = run_actions[0]
            request = action.get("request")
            result = action.get("result")
            if action.get("status") != "completed":
                errors.append("run record sink action did not complete")
            if not isinstance(request, Mapping) or request.get("operation") != "record_run":
                errors.append("run record request is invalid")
            else:
                expected_run_status = _run_status_for_observation(environment_result)
                if request.get("task") != case["input"]["task"]:
                    errors.append("run record task is not the case task")
                if request.get("host_id") != "synthetic-knowledge-maintenance-host":
                    errors.append("run record host is not the synthetic host")
                if request.get("model_id") is not None:
                    errors.append("run record model is not None")
                if request.get("status") != expected_run_status:
                    errors.append("run record status does not match typed outcome")
                if request.get("input_sha256") != capsule_digest:
                    errors.append("run record input digest does not match Capsule")
                if request.get("output_sha256") != expected_output_digest:
                    errors.append("run record output digest does not match observation")
                if request.get("tool_results_sha256") != expected_output_digest:
                    errors.append("run record tool digest does not match observation")
                metadata = request.get("run_metadata")
                if not isinstance(metadata, Mapping):
                    errors.append("run record metadata is missing")
                else:
                    if metadata.get("task_kind") != "synthetic_maintenance_fixture":
                        errors.append("run record task kind is invalid")
                    if metadata.get("artifact_ids") != [capsule_id]:
                        errors.append("run record artifact binding is invalid")
                    binding = metadata.get("task_binding")
                    if not isinstance(binding, Mapping):
                        errors.append("run record task binding is missing")
                    else:
                        if binding.get("schema_version") != "deeplaw.task-context-binding/v1":
                            errors.append("run record task binding schema is invalid")
                        for field in (
                            "project_sha256",
                            "task_lineage_sha256",
                            "parent_task_lineage_sha256",
                            "repository_sha256",
                            "worktree_sha256",
                            "dirty_state_sha256",
                            "binding_sha256",
                        ):
                            value = binding.get(field)
                            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                                errors.append(f"run record task binding {field} is invalid")
                        base_revision = binding.get("base_revision")
                        base_revision_valid = isinstance(base_revision, str) and bool(
                            _BASE_REVISION_PATTERN.fullmatch(base_revision)
                        )
                        if not base_revision_valid:
                            errors.append("run record task binding base revision is invalid")
            if not isinstance(result, Mapping):
                errors.append("run record result summary is missing")
            else:
                if result.get("status") != _run_status_for_observation(environment_result):
                    errors.append("run record result status does not match typed outcome")
                if result.get("input_sha256") != capsule_digest:
                    errors.append("run result input digest does not match Capsule")
                if result.get("output_sha256") != expected_output_digest:
                    errors.append("run result output digest does not match observation")
                if result.get("tool_results_sha256") != expected_output_digest:
                    errors.append("run result tool digest does not match observation")
                if not isinstance(result.get("run_id"), str) or not _RUN_ID_PATTERN.fullmatch(
                    result["run_id"]
                ):
                    errors.append("run result ID is invalid")
                if result.get("host_id") != "synthetic-knowledge-maintenance-host":
                    errors.append("run result host is not the synthetic host")
                if result.get("model_id") is not None:
                    errors.append("run result model is not None")
                if not isinstance(result.get("receipt_sha256"), str) or not _SHA256.fullmatch(
                    result["receipt_sha256"]
                ):
                    errors.append("run receipt digest is invalid")
        recorded_status = run_record.get("recorded_status")
        expected_run_status = _run_status_for_observation(environment_result)
        if recorded_status != expected_run_status:
            errors.append("recorded run status does not match typed outcome")
        if run_record.get("input_sha256") != capsule_digest:
            errors.append("stored run input digest does not match Capsule")
        if run_record.get("output_sha256") != expected_output_digest:
            errors.append("stored run output digest does not match observation")
        if run_record.get("tool_results_sha256") != expected_output_digest:
            errors.append("stored run tool digest does not match observation")
        run_id = run_record.get("run_id")
        if not isinstance(run_id, str) or not _RUN_ID_PATTERN.fullmatch(run_id):
            errors.append("stored run ID is invalid")
        if run_record.get("receipt_id") != run_id:
            errors.append("stored receipt ID does not match run ID")
        if not isinstance(run_record.get("receipt_sha256"), str) or not _SHA256.fullmatch(
            run_record["receipt_sha256"]
        ):
            errors.append("stored receipt digest is invalid")
        if run_record.get("host_id") != "synthetic-knowledge-maintenance-host":
            errors.append("stored run host is not the synthetic host")
        if run_record.get("model_id") is not None:
            errors.append("stored run model is not None")
        observed_status = (
            environment_result.get("status") if isinstance(environment_result, Mapping) else None
        )
        if observed_status in {"unknown", "failed"} and recorded_status == "succeeded":
            errors.append(f"{observed_status} typed outcome was recorded as succeeded")

    feedback = observation.get("feedback")
    if not isinstance(feedback, Mapping):
        errors.append("per-case feedback record is missing")
        feedback = {}
    attempts = observation.get("attempts")
    memory = None
    if isinstance(attempts, list) and attempts and isinstance(attempts[0], Mapping):
        memory = attempts[0].get("memory")
    if isinstance(memory, Mapping) and run_status == "completed":
        if feedback.get("status") != "completed":
            errors.append("selected knowledge feedback did not complete")
        if len(feedback_actions) != 1:
            errors.append("selected knowledge does not have one feedback action")
        else:
            action = feedback_actions[0]
            request = action.get("request")
            result = action.get("result")
            if action.get("status") != "completed":
                errors.append("feedback sink action did not complete")
            if not isinstance(request, Mapping) or request.get("operation") != "record_feedback":
                errors.append("feedback request is invalid")
            else:
                if request.get("knowledge_id") != memory.get("knowledge_id"):
                    errors.append("feedback knowledge ID does not match selection")
                if request.get("expected_revision_id") != memory.get("revision_id"):
                    errors.append("feedback revision ID does not match selection")
                if request.get("run_id") != run_record.get("run_id"):
                    errors.append("feedback run ID does not match run record")
                expected_outcome = _feedback_outcome(observation.get("score", {}))
                if request.get("outcome") != expected_outcome:
                    errors.append("feedback outcome does not match score")
                if request.get("evaluator_type") != "agent_self_report":
                    errors.append("feedback evaluator type is invalid")
            if not isinstance(result, Mapping):
                errors.append("feedback result summary is missing")
            else:
                for field in ("feedback_id", "knowledge_id", "revision_id", "run_id"):
                    if not isinstance(result.get(field), str) or not result[field]:
                        errors.append(f"feedback result {field} is invalid")
                if result.get("knowledge_id") != memory.get("knowledge_id"):
                    errors.append("feedback result knowledge ID does not match selection")
                if result.get("revision_id") != memory.get("revision_id"):
                    errors.append("feedback result revision ID does not match selection")
                if result.get("run_id") != run_record.get("run_id"):
                    errors.append("feedback result run ID does not match run record")
                if result.get("outcome") != feedback.get("outcome"):
                    errors.append("feedback result outcome does not match record")
                if result.get("evaluator_type") != "agent_self_report":
                    errors.append("feedback result evaluator type is invalid")
                if result.get("task_success_authority") != "self_report_only":
                    errors.append("feedback result elevated task success authority")
        if feedback.get("knowledge_id") != memory.get("knowledge_id"):
            errors.append("stored feedback knowledge ID does not match selection")
        if feedback.get("revision_id") != memory.get("revision_id"):
            errors.append("stored feedback revision ID does not match selection")
        if feedback.get("run_id") != run_record.get("run_id"):
            errors.append("stored feedback run ID does not match run record")
        if feedback.get("evaluator_type") != "agent_self_report":
            errors.append("stored feedback evaluator type is invalid")
        if feedback.get("task_success_authority") != "self_report_only":
            errors.append("stored feedback elevated task success authority")
    elif not isinstance(memory, Mapping):
        if feedback.get("status") != "not_attempted":
            errors.append("unselected knowledge has a feedback record")
        if feedback.get("error_code") != "no_selected_knowledge":
            errors.append("unselected knowledge feedback reason is invalid")
        if feedback_actions:
            errors.append("unselected knowledge has a feedback sink action")

    ledger = observation.get("ledger_verification")
    if not isinstance(ledger, Mapping) or ledger.get("valid") is not True:
        errors.append("per-case Ledger verification is not valid")
    elif not isinstance(ledger.get("failure_codes"), list) or not isinstance(
        ledger.get("warning_codes"), list
    ):
        errors.append("per-case Ledger verification inventories are invalid")
    return errors


def verify_report(value: Any) -> dict[str, Any]:
    """Verify the closed report envelope and every per-case exact score."""

    errors: list[str] = []
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["report must be an object"]}
    report = dict(value)
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema version is unsupported")
    if report.get("evaluation_mode") != "deterministic_development":
        errors.append("evaluation mode is not deterministic_development")
    if report.get("status") not in {"executed", "failed"}:
        errors.append("report execution status is invalid")
    if report.get("claim_eligible") is not False:
        errors.append("claim eligibility is not fail-closed")
    for field in (
        "release_gate_passed",
        "independent_holdout",
        "real_host_execution",
        "comparative_superiority_claim_eligible",
        "network_used",
    ):
        if report.get(field) is not False:
            errors.append(f"{field} is not fail-closed")
    if report.get("model_execution") != "not_executed":
        errors.append("model execution is not not_executed")
    frozen = report.get("frozen_input")
    if not isinstance(frozen, Mapping):
        errors.append("frozen input is missing")
    else:
        if frozen.get("frozen_before_candidate_execution") is not True:
            errors.append("candidate execution was not preceded by frozen inputs")
        if frozen.get("input_sha256") != FROZEN_INPUT_SHA256:
            errors.append("frozen input digest mismatch")
        if (
            frozen.get("case_order") != list(CASE_ORDER)
            or frozen.get("repeat_count") != REPEAT_COUNT
        ):
            errors.append("case order or repeat count drifted")
        if frozen.get("cases") != _frozen_case_payload():
            errors.append("frozen case definitions drifted")
    cost = report.get("cost")
    if not isinstance(cost, Mapping) or any(
        cost.get(field) != "unavailable"
        for field in ("input_tokens", "output_tokens", "total_tokens", "monetary_cost")
    ):
        errors.append("unavailable model cost fields are invalid")
    configurations = report.get("configurations")
    if not isinstance(configurations, list) or [
        item.get("configuration_id") for item in configurations if isinstance(item, Mapping)
    ] != list(CONFIGURATION_ORDER):
        errors.append("configuration order is invalid")
    else:
        for configuration in configurations:
            if not isinstance(configuration, Mapping):
                errors.append("configuration is not an object")
                continue
            if configuration.get("repeat_count") != REPEAT_COUNT:
                errors.append("configuration repeat count drifted")
            if configuration.get("execution_status") not in {"executed", "failed"}:
                errors.append("configuration execution status is invalid")
            if configuration.get("model_execution") != "not_executed":
                errors.append("configuration model execution is not not_executed")
            if not isinstance(configuration.get("fixture_setup_failures"), list):
                errors.append("fixture setup failure inventory is missing")
            configuration_cost = configuration.get("token_cost")
            if not isinstance(configuration_cost, Mapping) or any(
                configuration_cost.get(field) != "unavailable"
                for field in ("input_tokens", "output_tokens", "total_tokens", "monetary_cost")
            ):
                errors.append("configuration cost fields are invalid")
            per_case = configuration.get("per_case")
            if not isinstance(per_case, list) or [
                item.get("case_id") for item in per_case if isinstance(item, Mapping)
            ] != list(CASE_ORDER):
                errors.append("per-case order is invalid")
                continue
            for case, observation in zip(_CASE_SPECS, per_case, strict=True):
                if not isinstance(observation, Mapping):
                    errors.append("per-case observation is not an object")
                    continue
                errors.extend(_validate_score(case["expected"], observation))
                errors.extend(_validate_run_feedback(case, observation))
                elapsed = observation.get("elapsed_ms")
                if (
                    isinstance(elapsed, bool)
                    or not isinstance(elapsed, (int, float))
                    or elapsed < 0
                ):
                    errors.append("per-case elapsed time is invalid")
                for field in (
                    "failures",
                    "repeated_actions",
                    "stale_usage",
                    "constraint_violations",
                    "benign_damage",
                ):
                    if not isinstance(observation.get(field), list):
                        errors.append(f"per-case {field} is missing")
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if _ABSOLUTE_PATH.search(serialized):
        errors.append("report contains a local absolute path")
    if _SECRET_MARKER.search(serialized):
        errors.append("report contains a credential or secret marker")
    if report.get("report_sha256") != sha256_bytes(
        canonical_json(_digest_body(report)).encode("utf-8")
    ):
        errors.append("report digest mismatch")
    return {"valid": not errors, "errors": errors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the claim-ineligible synthetic knowledge maintenance evaluator."
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--workspace", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(args.workspace)
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if verify_report(report)["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
