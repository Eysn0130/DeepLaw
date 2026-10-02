"""Reopen explicitly used engineering files from a fixed public 3 by 5 run.

This does not inventory or admit the whole original run root. Live Vaults,
guard/configuration files and Host workspaces are never enumerated or read.
Producer execution and cleanup claims do not establish native authority.
Callers must serialize changes to consumed files throughout validation.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from benchmarks.hosts.maintenance_context_snapshot import _read, validate_context_snapshot
from benchmarks.hosts.maintenance_host_context import digest, validate_host_outcome
from benchmarks.hosts.maintenance_task_cases import (
    CONFIGURATION_ORDER,
    FROZEN_INPUT_SHA256,
    SCENARIO_ORDER,
    public_task_projection,
    score_host_trace,
)
from benchmarks.hosts.maintenance_task_mcp import MAX_PROVIDER_CAPSULE_BYTES, MAX_TRACE_BYTES
from benchmarks.hosts.opencode_single_task_producer import validate_guard_snapshot
from benchmarks.hosts.run_maintenance_host_tasks import outcome_commit_eligible
from deeplaw.knowledge_models import canonical_timestamp
from deeplaw.util import (
    assert_provider_output_safe,
    canonical_json,
    sha256_bytes,
    strict_json_loads,
)

_MAX_CASE_BYTES = 512 * 1024
_MAX_REPORT_BYTES = 8 * 1024 * 1024
_MAX_USED_FILES = 2012
_MAX_USED_BYTES = 512 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CASE_FIELDS = {
    "configuration_id", "scenario_id", "run_id", "candidate_id", "binding", "trace", "score",
    "native_observations", "host_exit_code", "forced_kill", "guard", "elapsed_ms", "failure",
    "failure_stage", "status", "formal_admission", "model_task_executed",
    "prompt_dispatch_attempted", "prompt_dispatched", "model_execution_status",
    "zero_model_mcp_status", "state_score_status", "isolation_evidence", "knowledge_outcome",
    "context_snapshot", "evidence_files",
}
_CAPTION = ("Engineering validation of explicitly used run files only; unused run areas are not "
            "admitted. Actual native admission remains missing.")
_FILES_CAPTION = "Engineering evidence hashes; native authority is not established."


def _json(raw: bytes) -> Any:
    value = strict_json_loads(raw.decode("utf-8"))
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is float and not math.isfinite(item):
            raise ValueError("non-finite run JSON")
        if type(item) is dict:
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
    return value


def _same(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def _exception_name(value: Any) -> bool:
    return value is None or (type(value) is str and _ID.fullmatch(value) is not None)


def _outcome_claim(value: Any, context: dict, trace: dict, score: dict, binding: dict) -> None:
    """Close sanitized post-hoc declarations without claiming Ledger registration."""
    if type(value) is not dict:
        raise ValueError("outcome declaration differs")
    if value.get("status") == "not_committed":
        if set(value) != {"status", "reason"} or value["reason"] not in {
            "trace_missing_or_host_lifecycle_unverified", "case_preparation_or_execution_failed",
        }:
            raise ValueError("uncommitted outcome declaration differs")
        return
    if value.get("status") == "failed":
        required = {"status", "reason", "commit_status"}
        optional = {"grant_closure_status", "mutation_failure", "cleanup_failure"}
        if (not required <= set(value) or set(value) - required not in (set(), optional)
                or value["reason"] != "outcome_recording_failed"
                or value["commit_status"] != "unknown"
                or (optional <= set(value) and (value["grant_closure_status"] != "failed"
                    or not _exception_name(value["mutation_failure"])
                    or not _exception_name(value["cleanup_failure"])))):
            raise ValueError("failed outcome declaration differs")
        return
    if set(value) != {"run_record", "score_sha256", "trace_sha256", "task_success_authority",
                      "action_state_ledger", "feedback_records", "feedback"}:
        raise ValueError("recorded outcome declaration fields differ")
    trace_sha = digest(trace)
    if (value["score_sha256"] != digest(score) or value["trace_sha256"] != trace_sha
            or value["task_success_authority"] != "external_score_only"
            or value["action_state_ledger"] != "not_recorded_by_posthoc_task_run"):
        raise ValueError("recorded outcome declaration binding differs")
    record = value["run_record"]
    fields = {"schema_version", "run_id", "writer_id", "host_id", "model_id", "task_sha256",
              "input_sha256", "output_sha256", "tool_results_sha256", "scope", "sensitivity",
              "status", "started_at", "ended_at", "metadata", "recorded_at", "receipt_sha256",
              "idempotent_replay", "audit_head"}
    if type(record) is not dict or set(record) != fields:
        raise ValueError("run record declaration fields differ")
    expected = {
        "schema_version": "deeplaw.knowledge-run-record/v1",
        "writer_id": "owner-host-maintenance-fixture", "host_id": "opencode",
        "model_id": "deepseek-flash", "scope": "project", "sensitivity": "public",
        "status": "succeeded" if score["passed"] else "partial",
        "task_sha256": sha256_bytes(trace["task"]["task"].encode()),
        "input_sha256": context["capsule"]["capsule_digest"], "output_sha256": trace_sha,
        "tool_results_sha256": trace_sha, "metadata": {
            "task_kind": "actual_host_public_maintenance", "artifact_ids": [context["context_id"]],
            "task_binding": binding,
        },
    }
    if (any(not _same(record[key], item) for key, item in expected.items())
            or type(record["run_id"]) is not str
            or not re.fullmatch(r"run_[0-9a-f]{24}", record["run_id"])):
        raise ValueError("run record declaration identity differs")
    for field in ("started_at", "ended_at", "recorded_at"):
        canonical_timestamp(record[field], field="run timestamp")
    if (type(record["idempotent_replay"]) is not bool
            or type(record["audit_head"]) is not str or not _SHA256.fullmatch(record["audit_head"])
            or record["receipt_sha256"] != digest({key: item for key, item in record.items()
                if key not in {"receipt_sha256", "idempotent_replay", "audit_head"}})):
        raise ValueError("run record declaration digest differs")
    feedback = value["feedback_records"]
    if type(feedback) is not list or len(feedback) > 8 or value["feedback"] != (
        "recorded" if feedback else "not_recorded_without_passing_case_or_successful_memory_use"
    ):
        raise ValueError("feedback declaration differs")
    revisions = {(item["knowledge_id"], item["revision_id"])
                 for item in context["capsule"]["knowledge_revisions"]}
    for item in feedback:
        fields = {"schema_version", "feedback_id", "knowledge_id", "revision_id", "run_id",
                  "outcome", "evaluator_type", "recorded_at", "feedback_note_sha256",
                  "task_success_authority", "idempotent_replay", "audit_head"}
        if (type(item) is not dict or set(item) != fields or score["passed"] is not True
                or (item["knowledge_id"], item["revision_id"]) not in revisions
                or item["schema_version"] != "deeplaw.knowledge-feedback/v1"
                or item["run_id"] != record["run_id"] or item["outcome"] != "helpful"
                or item["evaluator_type"] != "external_check"
                or item["task_success_authority"] != "external_evidence"
                or type(item["feedback_id"]) is not str
                or not re.fullmatch(r"feedback_[0-9a-f]{24}", item["feedback_id"])
                or type(item["idempotent_replay"]) is not bool
                or any(type(item[field]) is not str or not _SHA256.fullmatch(item[field])
                       for field in ("audit_head", "feedback_note_sha256"))):
            raise ValueError("feedback declaration binding differs")
        canonical_timestamp(item["recorded_at"], field="feedback timestamp")


def _native(raw: bytes) -> list[dict]:
    records = [_json(line) for line in raw.splitlines() if line.strip()]
    if not 0 < len(records) <= 1024:
        raise ValueError("native projection bound differs")
    for item in records:
        if type(item) is not dict or type(item.get("session_sha256")) is not str \
                or not _SHA256.fullmatch(item["session_sha256"]):
            raise ValueError("native projection identity differs")
        if item.get("event") in {"session.idle", "session.error"}:
            if set(item) != {"event", "session_sha256"}:
                raise ValueError("native lifecycle projection differs")
        elif item.get("event") == "message.updated":
            if (set(item) != {
                "event", "session_sha256", "provider", "model", "finished", "cost", "tokens",
            }
                    or type(item["finished"]) is not bool or type(item["tokens"]) is not dict
                    or set(item["tokens"]) != {"input", "output", "reasoning", "cache_read"}
                    or any(value is not None and (type(value) not in (int, float)
                           or not math.isfinite(value))
                           for value in [item["cost"], *item["tokens"].values()])
                    or any(value is not None and (type(value) is not str or len(value) > 128)
                           for value in (item["provider"], item["model"]))):
                raise ValueError("native response projection differs")
        else:
            raise ValueError("native event projection differs")
    if not any(item.get("event") == "message.updated" and item.get("finished") is True
               and item.get("provider") == "deepseek" and item.get("model") == "deepseek-flash"
               and any(type(item["tokens"].get(key)) in (int, float) and item["tokens"][key] > 0
                       for key in ("output", "reasoning")) for item in records):
        raise ValueError("completed response projection is missing")
    assert_provider_output_safe(records, interface="maintenance_run_evidence")
    return records


def _validate(root: Path, *, run_id: str, candidate_id: str, binary_sha256: str) -> dict[str, Any]:
    if (not isinstance(root, Path) or type(run_id) is not str or not _ID.fullmatch(run_id)
            or type(candidate_id) is not str or not _ID.fullmatch(candidate_id)
            or type(binary_sha256) is not str or not _SHA256.fullmatch(binary_sha256)):
        raise ValueError("run expected identity differs")
    root = root.absolute()
    if ".." in root.parts or len(root.parts) > 128:
        raise ValueError("run root differs")
    retained = {}

    def read(relative, maximum, reference=None):
        raw, identity = _read(root, relative, maximum)
        actual = {"path": relative, "sha256": sha256_bytes(raw), "size": len(raw)}
        if reference is not None and not _same(reference, actual):
            raise ValueError("run file byte binding differs")
        if relative in retained and retained[relative] != (actual, identity, maximum):
            raise ValueError("reused run file differs")
        retained[relative] = (actual, identity, maximum)
        if (len(retained) > _MAX_USED_FILES
                or sum(item[0]["size"] for item in retained.values()) > _MAX_USED_BYTES):
            raise ValueError("run consumed inventory exceeds its bound")
        return raw

    frozen = _json(read("frozen-input.json", 65_536))
    if not _same(frozen, {
        "input_sha256": FROZEN_INPUT_SHA256, "configurations": list(CONFIGURATION_ORDER),
        "scenarios": list(SCENARIO_ORDER), "max_seconds_per_case": 180,
        "max_provider_requests_per_case": 6, "binary_sha256": binary_sha256,
        "candidate_id": candidate_id,
    }):
        raise ValueError("run frozen input differs")
    report = _json(read("report.json", _MAX_REPORT_BYTES))
    if (type(report) is not dict
            or set(report) != {"results", "formal_admission", "fixed_input_sha256"}
            or report["formal_admission"] is not False
            or report["fixed_input_sha256"] != FROZEN_INPUT_SHA256
            or type(report["results"]) is not list or len(report["results"]) != 15):
        raise ValueError("run report differs")
    cases = []
    for index, (configuration, scenario) in enumerate(
        ((configuration, scenario) for configuration in CONFIGURATION_ORDER
         for scenario in SCENARIO_ORDER), 1,
    ):
        item = _json(read(f"case-{index:02}.json", _MAX_CASE_BYTES))
        if (type(item) is not dict or set(item) != _CASE_FIELDS
                or not _same(item, report["results"][index - 1])):
            raise ValueError("run case/report closure differs")
        slot_run = run_id + "-" + configuration + "-" + scenario
        if (item["configuration_id"] != configuration or item["scenario_id"] != scenario
                or item["run_id"] != slot_run or item["candidate_id"] != candidate_id
                or item["status"] not in {"succeeded", "failed"}
                or item["formal_admission"] is not False
                or any(item[field] is not True for field in (
                    "model_task_executed", "prompt_dispatch_attempted", "prompt_dispatched"))
                or item["model_execution_status"] != "observed"
                or item["state_score_status"] != "observed"
                or item["zero_model_mcp_status"] is not None
                or item["isolation_evidence"] != "not_supplied_by_this_producer"
                or not _exception_name(item["failure"])
                or not _exception_name(item["failure_stage"])
                or type(item["forced_kill"]) is not bool
                or (item["host_exit_code"] is not None
                    and type(item["host_exit_code"]) is not int)
                or type(item["elapsed_ms"]) not in (int, float)
                or not math.isfinite(item["elapsed_ms"])
                or item["elapsed_ms"] < 0 or type(item["context_snapshot"]) is not dict
                or type(item["trace"]) is not dict or type(item["score"]) is not dict):
            raise ValueError("run slot execution declaration differs")
        validate_guard_snapshot(item["guard"])
        assert_provider_output_safe(item, interface="maintenance_run_evidence")
        cases.append((configuration, scenario, slot_run, item))
    slots = []
    for configuration, scenario, slot_run, item in cases:
        selected = validate_context_snapshot(
            root, item["context_snapshot"], configuration=configuration, scenario=scenario,
            run_id=slot_run, candidate_id=candidate_id,
        )
        for reference in selected["files"]:
            read(reference["path"], max(reference["size"], 1), reference)
        receipt = item["evidence_files"]
        if (type(receipt) is not dict or set(receipt) != {
            "action_trace", "provider_capsule", "native_events", "gaps", "caption",
        } or receipt["gaps"] != [] or receipt["caption"] != _FILES_CAPTION):
            raise ValueError("run raw evidence receipt differs")
        payloads = {}
        for field, name, maximum in (
            ("action_trace", "action-trace.json", MAX_TRACE_BYTES),
            ("provider_capsule", "provider-capsule.json", MAX_PROVIDER_CAPSULE_BYTES),
            ("native_events", "native-events.jsonl", 65_536),
        ):
            reference = receipt[field]
            relative = configuration + "-" + scenario + "/" + name
            if (type(reference) is not dict or set(reference) != {"path", "sha256", "size"}
                    or reference["path"] != relative or type(reference["size"]) is not int
                    or not 0 < reference["size"] <= maximum):
                raise ValueError("run raw evidence path or bound differs")
            payloads[field] = read(relative, maximum, reference)
        trace = _json(payloads["action_trace"])
        provider = _json(payloads["provider_capsule"])
        native = _native(payloads["native_events"])
        context = selected["context"]
        binding = item["context_snapshot"]["owner_binding"]
        if (not _same(trace, item["trace"]) or not _same(item["binding"], binding)
                or not _same(provider, context["provider_capsule"])
                or not _same(native, item["native_observations"])
                or trace.get("task") != public_task_projection(configuration, scenario)):
            raise ValueError("run original projection binding differs")
        score = score_host_trace(configuration, scenario, trace["events"])
        if not _same(score, item["score"]):
            raise ValueError("run independent score differs")
        declared_status = (
            "succeeded" if outcome_commit_eligible(item) and score["passed"] else "failed"
        )
        if item["status"] != declared_status:
            raise ValueError("run score and lifecycle declarations contradict their status")
        checked = validate_host_outcome(
            root / selected["vault_path"], configuration=configuration, scenario=scenario,
            host_run_id=slot_run, context=context, trace_payload=trace,
            candidate_id=candidate_id, score=score,
        )
        if not _same(checked, trace):
            raise ValueError("run persisted trace validation differs")
        _outcome_claim(
            item["knowledge_outcome"], context, trace, score,
            item["context_snapshot"]["task_binding"],
        )
        slots.append({"configuration_id": configuration, "scenario_id": scenario,
                      "run_id": slot_run, "score": score, "producer_status": item["status"]})
    for relative, (reference, identity, maximum) in retained.items():
        raw, current_identity = _read(root, relative, maximum)
        if current_identity != identity or len(raw) != reference["size"] \
                or sha256_bytes(raw) != reference["sha256"]:
            raise ValueError("run consumed bytes changed during validation")
    return {"run_id": run_id, "candidate_id": candidate_id, "binary_sha256": binary_sha256,
            "public_input_sha256": FROZEN_INPUT_SHA256, "slots": slots,
            "files": [item[0] for _, item in sorted(retained.items())],
            "execution_evidence": "producer_declarations_only",
            "formal_admission": False, "caption": _CAPTION}


def validate_run_evidence(
    root: Path, *, run_id: str, candidate_id: str, binary_sha256: str,
) -> dict[str, Any]:
    """Revalidate exactly 15 original public measurements, accepting honest task failures.

    Return independently recomputed scores and the sorted exact file closure
    actually read, without admitting unused root contents or native authority.
    No source files, grants, metadata, permissions or outcome records are written.
    """
    try:
        return _validate(root, run_id=run_id, candidate_id=candidate_id,
                         binary_sha256=binary_sha256)
    except Exception:
        raise ValueError("maintenance run evidence validation failed") from None
