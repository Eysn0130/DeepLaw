"""Machine-derived v0.13 Gate v9 Host-task evidence.

This module is deliberately separate from the historical Host parser.  The
public typed kind remains ``host_event_sequence``; only the three v0.13 task
labels dispatch here.  Old v1/v2/old-v3 Host receipts continue through the
historical parser unchanged.

The module consumes six hashed JSON source projections and derives metrics from
native Host events, lifecycle receipts, usage, frozen expectations, task
observations, and isolation observations.  It never starts a Host, reads
credentials, calls a model, or writes the Knowledge Ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deeplaw.native_host import (
    NativeHostObservationError,
    derive_native_host_receipt,
    parse_native_host_event,
)

SCHEMA_VERSION = "deeplaw.v013-host-task-evidence/v1"
TASK_CASES = ("continuity", "living_wiki", "professional_evidence")
HOSTS = ("codex", "opencode")
MAX_PROVIDER_BYTES = 65_536
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s=:\"'])/(?:Users|home|root|private|tmp|var|etc|opt|workspace|Volumes|System|Library|bin|sbin|usr|dev|proc|sys|run|mnt)(?:/|[\s\"']|$)|"
    r"(?:^|[\s=:\"'])(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)
_SENSITIVE_KEYS = frozenset(
    {
        "prompt",
        "transcript",
        "reasoning_content",
        "secret",
        "credential",
        "password",
        "authorization",
        "api_key",
        "access_token",
    }
)
_CALLER_RESULT_KEYS = frozenset(
    {
        "passed",
        "pass",
        "claim",
        "release_ready",
        "competitive",
        "superiority",
        "sota",
        "arbiter",
        "panel",
    }
)

TASK_DUTIES: dict[str, tuple[str, ...]] = {
    "continuity": (
        "first_correct_action",
        "decision_preservation",
        "wrong_state_rejection",
        "bounded_read_only_context",
        "authority_boundary",
        "failure_honesty",
        "compaction_preservation",
        "forget_semantics",
    ),
    "living_wiki": (
        "alias_same_name_identity",
        "rename_move",
        "external_edit_reconcile",
        "backlink_outlink",
        "source_successor",
        "wrong_merge_rejection",
        "user_file_protection",
        "full_incremental_noop_equivalence",
        "wiki_exact_source_drill_down",
    ),
    "professional_evidence": (
        "original_bytes",
        "original_hash",
        "document",
        "version",
        "fragment",
        "locator",
        "wrong_version_rejection",
        "effective_date",
        "exception",
        "proviso",
        "cross_reference",
        "false_authority",
        "ocr_critical_token_gap",
        "wiki_exact_source_drill_down",
    ),
}
TASK_WRONG_STATES: dict[str, tuple[str, ...]] = {
    "continuity": ("stale", "wrong_task_line", "wrong_worktree", "forgotten"),
    "living_wiki": ("wrong_merge", "stale_revision", "hidden_mutation"),
    "professional_evidence": ("wrong_version", "false_authority", "ocr_critical_token"),
}
TASK_OPERATIONS: dict[str, tuple[str, ...]] = {
    "continuity": (
        "start",
        "message",
        "resume",
        "fork",
        "compaction_pre",
        "compaction",
        "compaction_post",
        "end",
    ),
    "living_wiki": ("wiki_read", "source_read", "query_context"),
    "professional_evidence": ("source_read", "fragment_read", "wiki_read", "query_context"),
}
HOST_NATIVE_OPERATIONS = {
    "codex": ("start", "message", "compaction_pre", "compaction_post", "end"),
    "opencode": ("resume", "message", "fork", "compaction"),
}
CONTINUITY_LIFECYCLE = (
    "new",
    "resume",
    "fork",
    "compaction",
    "stale",
    "wrong_task_line",
    "forget",
    "resume_after_forget",
)
HOST_MODELS = {"codex": "gpt-5.6-luna", "opencode": "deepseek-v4-flash"}


def _host_identity_shape(
    value: Any, *, host: str, schema_version: str | None = None
) -> bool:
    if not isinstance(value, Mapping):
        return False
    if host == "codex":
        historical = (
            set(value) == {"binary_version", "binary_sha256", "request_model", "reasoning"}
            and isinstance(value["binary_version"], str)
            and 1 <= len(value["binary_version"]) <= 100
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}", value["binary_version"])
            is not None
            and isinstance(value["binary_sha256"], str)
            and _SHA256.fullmatch(value["binary_sha256"]) is not None
            and value["request_model"] == HOST_MODELS[host]
            and value["reasoning"] == "max"
        )
        current = (
            set(value)
            == {
                "binary_version",
                "binary_sha256",
                "request_model",
                "reasoning_effort",
                "auth_status_command",
                "auth_material_access",
            }
            and isinstance(value["binary_version"], str)
            and 1 <= len(value["binary_version"]) <= 100
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}", value["binary_version"])
            is not None
            and isinstance(value["binary_sha256"], str)
            and _SHA256.fullmatch(value["binary_sha256"]) is not None
            and value["request_model"] == HOST_MODELS[host]
            and value["reasoning_effort"] == "max"
            and value["auth_status_command"] == "codex login status"
            and value["auth_material_access"] == "forbidden"
        )
        if schema_version == "deeplaw.native-host-event/v2":
            return historical
        if schema_version == "deeplaw.native-host-event/v3":
            return current
        return False
    historical = (
        set(value)
        == {
            "version", "source_commit", "config_selector", "expected_response_model_id",
            "executable_sha256", "package_sha256",
        }
        and isinstance(value["version"], str)
        and 1 <= len(value["version"]) <= 100
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}", value["version"])
        is not None
        and isinstance(value["source_commit"], str)
        and _GIT.fullmatch(value["source_commit"]) is not None
        and value["config_selector"] == "deepseek/deepseek-v4-flash"
        and value["expected_response_model_id"] == HOST_MODELS[host]
        and isinstance(value["executable_sha256"], str)
        and _SHA256.fullmatch(value["executable_sha256"]) is not None
        and isinstance(value["package_sha256"], str)
        and _SHA256.fullmatch(value["package_sha256"]) is not None
    )
    current = (
        set(value)
        == {
            "version", "source_commit", "config_selector", "expected_response_model_id",
            "executable_sha256", "package_sha256", "runtime", "dotenv_policy", "secret_visibility",
        }
        and isinstance(value["version"], str)
        and 1 <= len(value["version"]) <= 100
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}", value["version"])
        is not None
        and isinstance(value["source_commit"], str)
        and _GIT.fullmatch(value["source_commit"]) is not None
        and value["config_selector"] == "deepseek/deepseek-v4-flash"
        and value["expected_response_model_id"] == HOST_MODELS[host]
        and isinstance(value["executable_sha256"], str)
        and _SHA256.fullmatch(value["executable_sha256"]) is not None
        and isinstance(value["package_sha256"], str)
        and _SHA256.fullmatch(value["package_sha256"]) is not None
        and value["runtime"] == "host_bun_runtime_only"
        and value["dotenv_policy"] == "owner_only_external_strict_parser"
        and value["secret_visibility"] == "forbidden"
    )
    if schema_version == "deeplaw.native-host-event/v2":
        return historical
    if schema_version == "deeplaw.native-host-event/v3":
        return current
    return False


def _host_identity_projection(value: Any, *, host: str) -> dict[str, Any]:
    """Normalize historical native identity fields to the v1 external-input shape."""

    item = value if isinstance(value, Mapping) else {}
    if host == "codex":
        if "reasoning_effort" in item:
            return dict(item)
        return {
            "binary_version": item.get("binary_version"),
            "binary_sha256": item.get("binary_sha256"),
            "request_model": item.get("request_model"),
            "reasoning_effort": item.get("reasoning", "max"),
            "auth_status_command": "codex login status",
            "auth_material_access": "forbidden",
        }
    if "runtime" in item:
        return dict(item)
    return {
        "version": item.get("version"),
        "source_commit": item.get("source_commit"),
        "config_selector": item.get("config_selector"),
        "expected_response_model_id": item.get("expected_response_model_id"),
        "executable_sha256": item.get("executable_sha256"),
        "package_sha256": item.get("package_sha256"),
        "runtime": "host_bun_runtime_only",
        "dotenv_policy": "owner_only_external_strict_parser",
        "secret_visibility": "forbidden",
    }
HARD_FAILURE_IDS = (
    "unsupported_task_case",
    "task_binding_mismatch",
    "host_binding_mismatch",
    "run_binding_mismatch",
    "candidate_binding_mismatch",
    "corpus_binding_mismatch",
    "event_sequence_non_contiguous",
    "lifecycle_not_derived",
    "compatibility_bridge",
    "native_host_pin_mismatch",
    "model_substitution",
    "wrong_tool_or_parameter",
    "missing_required_operation",
    "expected_task_mismatch",
    "first_correct_action_missing",
    "decision_preservation_missing",
    "wrong_state_admission",
    "required_duty_gap",
    "provider_usage_missing",
    "provider_usage_mismatch",
    "provider_bytes_overflow",
    "selected_identity_mismatch",
    "duplicate_distractor_admission",
    "hidden_mutation",
    "query_trace_in_capsule",
    "ledger_in_capsule",
    "secret_exposure",
    "private_path_disclosure",
    "cross_boundary_disclosure",
    "wrong_run_inclusion",
    "forgotten_state_admission",
    "unrelated_state_loss",
    "ledger_read_mutation",
    "provider_disclosure",
)


class HostTaskEvidenceError(ValueError):
    """A v0.13 Host task source projection is unsafe or incomplete."""


def _fail(message: str) -> None:
    raise HostTaskEvidenceError(message)


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HostTaskEvidenceError("Host task evidence is not canonical JSON") from exc


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(f"{label} is not a lowercase SHA-256 digest")
    return value


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _fail(f"{label} is not a safe identifier")
    return value


def _closed(value: Any, keys: set[str], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(f"{label} keys are not closed")
    return value


def _reject_unsafe(value: Any, *, label: str, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, str):
        if _ABSOLUTE_PATH.search(value):
            _fail(f"{label} contains a private path")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.casefold()
            if lowered in _SENSITIVE_KEYS:
                _fail(f"{label} contains a sensitive field")
            if lowered in _CALLER_RESULT_KEYS:
                _fail(f"{label} contains a caller-authored qualification field")
            if lowered in {"query_trace", "ledger"} and isinstance(item, Mapping):
                allowed = {"in_capsule", "sha256", "entry_count"}
                if set(item) != allowed:
                    _fail(f"{label}.{key_text} contains raw audit data")
            _reject_unsafe(item, label=label, path=(*path, key_text))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe(item, label=label, path=(*path, str(index)))


def _relative_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail(f"{label} path is invalid")
    selected = Path(value)
    if selected.is_absolute() or any(part in {".", ".."} for part in selected.parts):
        _fail(f"{label} path escapes evidence root")
    return selected


def _json_source(ref: Mapping[str, Any], *, root: Path, label: str) -> Mapping[str, Any]:
    if set(ref) != {"relative_path", "byte_size", "sha256", "media_type"}:
        _fail(f"{label} source keys are not closed")
    if ref["media_type"] != "application/json":
        _fail(f"{label} source media type is not JSON")
    relative = _relative_path(ref["relative_path"], label=label)
    size = ref["byte_size"]
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 64 * 1024 * 1024:
        _fail(f"{label} source byte size is invalid")
    expected_hash = _digest(ref["sha256"], label=f"{label} source hash")
    path = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            _fail(f"{label} source path contains a symlink")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
        raw = resolved.read_bytes()
    except (OSError, ValueError) as exc:
        raise HostTaskEvidenceError(f"{label} source is unavailable") from exc
    if (
        resolved.is_symlink()
        or not resolved.is_file()
        or len(raw) != size
        or _sha(raw) != expected_hash
    ):
        _fail(f"{label} source bytes do not match its reference")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HostTaskEvidenceError(f"{label} source is not strict JSON") from exc
    if not isinstance(value, Mapping):
        _fail(f"{label} source must be an object")
    _reject_unsafe(value, label=label)
    return value


def _metadata(value: Mapping[str, Any], *, artifact: str) -> tuple[str, int, str, str, str]:
    required = {"artifact_kind", "schema_version", "run_id", "workflow_run_id", "task_case", "host"}
    if not required.issubset(value):
        _fail(f"{artifact} metadata is incomplete")
    if value["artifact_kind"] != artifact or value["schema_version"] != SCHEMA_VERSION:
        _fail(f"{artifact} schema version is unsupported")
    run_id = _identifier(value["run_id"], label=f"{artifact}.run_id")
    workflow = value["workflow_run_id"]
    if isinstance(workflow, bool) or not isinstance(workflow, int) or workflow < 1:
        _fail(f"{artifact}.workflow_run_id is invalid")
    task_case = value["task_case"]
    host = value["host"]
    if task_case not in TASK_CASES:
        _fail(f"{artifact}.task_case is unsupported")
    if host not in HOSTS:
        _fail(f"{artifact}.host is unsupported")
    model = value.get("actual_response_model_id", HOST_MODELS[host])
    if model != HOST_MODELS[host]:
        _fail(f"{artifact}.actual_response_model_id is not pinned")
    return run_id, workflow, task_case, host, model


def _bind_metadata(
    metadata: tuple[str, int, str, str, str],
    *,
    expected: tuple[str, int, str, str, str],
    failures: Counter[str],
) -> None:
    for index, (observed, required) in enumerate(zip(metadata, expected, strict=True)):
        if observed != required:
            failures[
                "wrong_run_inclusion"
                if index < 2
                else "task_binding_mismatch"
                if index == 2
                else "host_binding_mismatch"
            ] += 1


def _candidate(value: Any, *, label: str) -> Mapping[str, Any]:
    item = _closed(
        value, {"commit", "tree", "lock_sha256", "wheel_sha256", "sdist_sha256"}, label=label
    )
    if not isinstance(item["commit"], str) or _GIT.fullmatch(item["commit"]) is None:
        _fail(f"{label}.commit is invalid")
    if not isinstance(item["tree"], str) or _GIT.fullmatch(item["tree"]) is None:
        _fail(f"{label}.tree is invalid")
    for field in ("lock_sha256", "wheel_sha256", "sdist_sha256"):
        _digest(item[field], label=f"{label}.{field}")
    return item


def _expected(value: Mapping[str, Any], *, task_case: str, host: str) -> Mapping[str, Any]:
    _metadata(value, artifact="expected_task")
    required = {
        "artifact_kind",
        "schema_version",
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "required_duties",
        "duty_expectations",
        "rows",
        "hard_failure_ids",
    }
    _closed(value, required, label="expected task")
    if value["task_case"] != task_case or value["host"] != host:
        _fail("expected task binding differs from event sequence")
    duties = value["required_duties"]
    if duties != list(TASK_DUTIES[task_case]) or len(duties) != len(set(duties)):
        _fail("expected task duties are not the frozen v0.13 list")
    expectations = value["duty_expectations"]
    if not isinstance(expectations, list) or len(expectations) != len(duties):
        _fail("expected duty status matrix is incomplete")
    seen_expectations: set[str] = set()
    for index, expectation in enumerate(expectations):
        item = _closed(
            expectation,
            {"duty", "allowed_statuses", "required_gap_code"},
            label=f"expected duty status {index}",
        )
        duty = _identifier(item["duty"], label=f"expected duty status {index}.duty")
        if duty in seen_expectations or duty not in duties:
            _fail("expected duty status identity is duplicated or unsupported")
        statuses = item["allowed_statuses"]
        if (
            not isinstance(statuses, list)
            or not statuses
            or len(statuses) != len(set(statuses))
            or any(status not in {"observed", "gap"} for status in statuses)
        ):
            _fail("expected duty statuses are invalid")
        gap_code = item["required_gap_code"]
        if gap_code is not None:
            _identifier(gap_code, label=f"expected duty status {index}.required_gap_code")
            if "gap" not in statuses:
                _fail("expected duty gap code requires gap status")
        seen_expectations.add(duty)
    if seen_expectations != set(duties):
        _fail("expected duty status matrix does not cover every duty")
    rows = value["rows"]
    if not isinstance(rows, list) or not rows:
        _fail("expected task rows are missing")
    seen: set[str] = set()
    for index, row in enumerate(rows):
        item = _closed(
            row,
            {"case_id", "required_duties", "required_wrong_states", "required_operations"},
            label=f"expected row {index}",
        )
        case_id = _identifier(item["case_id"], label=f"expected row {index}.case_id")
        if case_id in seen:
            _fail("expected task rows contain duplicate case identity")
        seen.add(case_id)
        if not isinstance(item["required_duties"], list) or not item["required_duties"]:
            _fail("expected row duties are missing")
        if not isinstance(item["required_wrong_states"], list) or not isinstance(
            item["required_operations"], list
        ):
            _fail("expected row state or operation list is invalid")
        if item["required_operations"] != list(TASK_OPERATIONS[task_case]):
            _fail("expected row operations are not the frozen task operation list")
    hard_ids = value["hard_failure_ids"]
    if not isinstance(hard_ids, list) or not hard_ids or len(hard_ids) != len(set(hard_ids)):
        _fail("expected task hard failure list is invalid")
    return value


def _event_metadata(
    value: Mapping[str, Any],
) -> tuple[str, int, str, str, str, list[Mapping[str, Any]]]:
    required = {
        "artifact_kind",
        "schema_version",
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "actual_response_model_id",
        "events",
    }
    _closed(value, required, label="event sequence")
    metadata = _metadata(value, artifact="event_sequence")
    events = value["events"]
    if not isinstance(events, list) or not events:
        _fail("event sequence is empty")
    return (*metadata, events)


def _lifecycle_metadata(
    value: Mapping[str, Any],
) -> tuple[str, int, str, str, str, list[Mapping[str, Any]]]:
    required = {
        "artifact_kind",
        "schema_version",
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "actual_response_model_id",
        "receipts",
    }
    _closed(value, required, label="lifecycle sequence")
    metadata = _metadata(value, artifact="lifecycle_sequence")
    receipts = value["receipts"]
    if not isinstance(receipts, list) or not receipts:
        _fail("lifecycle sequence is empty")
    return (*metadata, receipts)


def _usage(value: Mapping[str, Any], *, envelope: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    required = {
        "artifact_kind",
        "schema_version",
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "actual_response_model_id",
        "rows",
    }
    _closed(value, required, label="usage receipt")
    metadata = _metadata(value, artifact="usage_receipt")
    expected = (
        envelope["run_binding"]["run_id"],
        envelope["run_binding"]["workflow_run_id"],
        envelope["task_case"],
        envelope["host"],
        envelope["actual_response_model_id"],
    )
    failures: Counter[str] = Counter()
    _bind_metadata(metadata, expected=expected, failures=failures)
    if failures:
        _fail("usage receipt binding differs from envelope")
    rows = value["rows"]
    if not isinstance(rows, list) or not rows:
        _fail("usage receipt rows are missing")
    return rows


def _identity_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or len(value) > 256:
        _fail("selected identities are invalid")
    result: list[Mapping[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(value):
        row = _closed(item, {"kind", "identity_sha256"}, label=f"selected identity {index}")
        key = (
            _identifier(row["kind"], label=f"selected identity {index}.kind"),
            _digest(row["identity_sha256"], label=f"selected identity {index}.identity_sha256"),
        )
        if key in seen:
            _fail("selected identities contain duplicates")
        seen.add(key)
        result.append(row)
    return result


def _state_rows(
    value: Any, *, label: str, expected_states: Sequence[str]
) -> tuple[list[Mapping[str, Any]], int]:
    if not isinstance(value, list) or len(value) > 256:
        _fail(f"{label} is invalid")
    rows: list[Mapping[str, Any]] = []
    observed: dict[str, bool] = {}
    for index, item in enumerate(value):
        row = _closed(item, {"state", "admitted"}, label=f"{label}[{index}]")
        state = _identifier(row["state"], label=f"{label}[{index}].state")
        if state in observed:
            _fail(f"{label} contains duplicate state")
        if not isinstance(row["admitted"], bool):
            _fail(f"{label}[{index}].admitted is invalid")
        observed[state] = row["admitted"]
        rows.append(row)
    missing = set(expected_states) - set(observed)
    if missing:
        _fail(f"{label} omits required states: {sorted(missing)}")
    return rows, sum(1 for value in observed.values() if value)


def _duties(value: Any, *, task_case: str) -> tuple[list[Mapping[str, Any]], int]:
    if not isinstance(value, list) or len(value) != len(TASK_DUTIES[task_case]):
        _fail("task duties are incomplete")
    rows: list[Mapping[str, Any]] = []
    observed: dict[str, str] = {}
    for index, item in enumerate(value):
        row = _closed(item, {"duty", "status", "gap_code"}, label=f"task duty {index}")
        duty = _identifier(row["duty"], label=f"task duty {index}.duty")
        if duty in observed or duty not in TASK_DUTIES[task_case]:
            _fail("task duty identity is duplicated or unsupported")
        if row["status"] not in {"observed", "gap"}:
            _fail("task duty status is invalid")
        if row["status"] == "observed" and row["gap_code"] is not None:
            _fail("observed task duty cannot carry a gap")
        if row["status"] == "gap" and (not isinstance(row["gap_code"], str) or not row["gap_code"]):
            _fail("gap task duty must carry a gap code")
        observed[duty] = row["status"]
        rows.append(row)
    if set(observed) != set(TASK_DUTIES[task_case]):
        _fail("task duties do not cover the frozen duty list")
    return rows, sum(1 for status in observed.values() if status == "observed")


def _task_result(value: Mapping[str, Any], *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
    required = {
        "artifact_kind",
        "schema_version",
        "run_id",
        "workflow_run_id",
        "task_case",
        "host",
        "first_correct_action",
        "decision_preservation",
        "wrong_state_admission",
        "duties",
        "provider",
        "selected_identities",
        "duplicate_distractor",
        "no_hidden_mutation",
        "query_trace",
        "ledger",
        "lifecycle_steps",
        "observed_public_seams",
        "claim_eligible",
    }
    _closed(value, required, label="task result")
    metadata = _metadata(value, artifact="task_result")
    if value["claim_eligible"] is not False:
        _fail("task result cannot claim qualification eligibility")
    expected = (
        envelope["run_binding"]["run_id"],
        envelope["run_binding"]["workflow_run_id"],
        envelope["task_case"],
        envelope["host"],
        envelope["actual_response_model_id"],
    )
    failures = Counter()
    _bind_metadata(metadata, expected=expected, failures=failures)
    if failures:
        _fail("task result binding differs from envelope")
    steps = value["lifecycle_steps"]
    if not isinstance(steps, list) or len(steps) > len(CONTINUITY_LIFECYCLE):
        _fail("task result lifecycle steps are invalid")
    seen_steps: set[str] = set()
    for index, item in enumerate(steps):
        row = _closed(item, {"step", "observed", "gap_code"}, label=f"lifecycle step {index}")
        step = _identifier(row["step"], label=f"lifecycle step {index}.step")
        if step in seen_steps or step not in CONTINUITY_LIFECYCLE:
            _fail("task result lifecycle step is duplicated or unsupported")
        if not isinstance(row["observed"], bool):
            _fail("task result lifecycle step observation is invalid")
        if row["observed"] and row["gap_code"] is not None:
            _fail("observed lifecycle step cannot carry a gap")
        if not row["observed"] and (not isinstance(row["gap_code"], str) or not row["gap_code"]):
            _fail("unobserved lifecycle step must carry a gap")
        seen_steps.add(step)
    if metadata[2] == "continuity" and seen_steps != set(CONTINUITY_LIFECYCLE):
        _fail("continuity lifecycle is incomplete")
    if metadata[2] != "continuity" and steps:
        _fail("non-continuity task carries continuity lifecycle steps")
    seams = value["observed_public_seams"]
    allowed_seams = {
        "knowledge_support",
        "native_capsule",
        "source_read",
        "fragment_read",
        "wiki_read",
        "query_context",
    }
    if (
        not isinstance(seams, list)
        or len(seams) != len(set(seams))
        or any(seam not in allowed_seams for seam in seams)
    ):
        _fail("task result public seam projection is invalid")
    return value


def _authorized_mutation(value: Any, *, task_case: str, label: str) -> Mapping[str, Any]:
    item = _closed(
        value,
        {"observed", "operation", "owner_authorized", "receipt_sha256"},
        label=label,
    )
    if not isinstance(item["observed"], bool) or not isinstance(item["owner_authorized"], bool):
        _fail(f"{label} authorization flags are invalid")
    if item["operation"] not in {None, "owner_forget"}:
        _fail(f"{label} operation is invalid")
    receipt = item["receipt_sha256"]
    if receipt is not None:
        _digest(receipt, label=f"{label}.receipt_sha256")
    if item["observed"]:
        if (
            task_case != "continuity"
            or item["operation"] != "owner_forget"
            or not item["owner_authorized"]
            or receipt is None
        ):
            _fail(f"{label} contains an unauthorized mutation")
    elif item["operation"] is not None or item["owner_authorized"] or receipt is not None:
        _fail(f"{label} records an unobserved authorization")
    return item


def _isolation(value: Mapping[str, Any], *, envelope: Mapping[str, Any]) -> Mapping[str, Any]:
    required = {
        "artifact_kind",
        "schema_version",
        "candidate_binding",
        "run_binding",
        "corpus",
        "runner",
        "scorer",
        "host",
        "task_case",
        "secret_boundary",
        "process_boundary",
        "write_observation",
        "claim_eligible",
    }
    _closed(value, required, label="isolation receipt")
    if value["claim_eligible"] is not False:
        _fail("isolation receipt cannot claim qualification eligibility")
    if value["artifact_kind"] != "isolation_receipt" or value["schema_version"] != SCHEMA_VERSION:
        _fail("isolation receipt schema version is unsupported")
    if dict(_candidate(value["candidate_binding"], label="isolation candidate")) != dict(
        envelope["candidate_binding"]
    ):
        _fail("isolation candidate binding differs from envelope")
    if value["run_binding"] != envelope["run_binding"] or value["corpus"] != envelope["corpus"]:
        _fail("isolation run/corpus binding differs from envelope")
    for field in ("runner", "scorer"):
        item = _closed(value[field], {"identity", "sha256"}, label=f"isolation {field}")
        _identifier(item["identity"], label=f"isolation {field}.identity")
        _digest(item["sha256"], label=f"isolation {field}.sha256")
        if dict(item) != dict(envelope[field]):
            _fail(f"isolation {field} binding differs from envelope")
    if value["host"] != envelope["host"] or value["task_case"] != envelope["task_case"]:
        _fail("isolation host/task binding differs from envelope")
    secret = _closed(
        value["secret_boundary"],
        {
            "parent_secret_present",
            "child_secret_present",
            "auth_read",
            "transcript_read",
            "prompt_read",
            "reasoning_read",
            "secret_read",
        },
        label="isolation secret boundary",
    )
    process = _closed(
        value["process_boundary"],
        {"native_receipt_observed", "host_process_separated", "mcp_process_separated"},
        label="isolation process boundary",
    )
    write = _closed(
        value["write_observation"],
        {
            "hidden_mutation",
            "write_performed",
            "authorized_mutation",
            "audit_head_before_sha256",
            "audit_head_after_sha256",
        },
        label="isolation write observation",
    )
    if not isinstance(secret["parent_secret_present"], bool) or not isinstance(
        secret["child_secret_present"], bool
    ):
        _fail("isolation secret presence is invalid")
    if not all(
        isinstance(process[field], bool)
        for field in ("native_receipt_observed", "host_process_separated", "mcp_process_separated")
    ):
        _fail("isolation process boundary is invalid")
    if write["hidden_mutation"] is not False or not isinstance(write["write_performed"], bool):
        _fail("isolation hidden mutation observation is invalid")
    _digest(write["audit_head_before_sha256"], label="isolation audit head before")
    _digest(write["audit_head_after_sha256"], label="isolation audit head after")
    _authorized_mutation(
        write["authorized_mutation"],
        task_case=envelope["task_case"],
        label="isolation authorized mutation",
    )
    return value


def parse_host_task_evidence(
    envelope: Mapping[str, Any],
    *,
    root: Path,
    record_sha256: str,
    expected_corpus_sha256: str | None,
) -> dict[str, Any]:
    """Derive v0.13 Host task metrics from one v3 typed manifest."""

    if expected_corpus_sha256 is None:
        _fail("v0.13 Host task expected-source corpus binding is required")
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        _fail("v0.13 Host task payload is missing")
    refs = (
        "event_source",
        "lifecycle_source",
        "usage_source",
        "expected_source",
        "continuity_source",
        "isolation_source",
    )
    if set(payload) != set(refs):
        _fail("v0.13 Host task payload source refs are not closed")
    event_value = _json_source(
        payload["event_source"], root=root, label="v0.13 Host event sequence"
    )
    event_meta = _event_metadata(event_value)
    run_id, workflow, task_case, host, actual_model, events = event_meta
    expected_binding = (
        envelope["run_binding"]["run_id"],
        envelope["run_binding"]["workflow_run_id"],
        task_case,
        host,
        actual_model,
    )
    failures: Counter[str] = Counter()
    _bind_metadata(event_meta[:5], expected=expected_binding, failures=failures)
    if failures:
        failures["wrong_run_inclusion"] += sum(failures.values())

    lifecycle_value = _json_source(
        payload["lifecycle_source"], root=root, label="v0.13 Host lifecycle sequence"
    )
    lifecycle_meta = _lifecycle_metadata(lifecycle_value)
    _bind_metadata(lifecycle_meta[:5], expected=expected_binding, failures=failures)
    receipts = lifecycle_meta[-1]
    usage_value = _json_source(payload["usage_source"], root=root, label="v0.13 Host usage receipt")
    bound_envelope = {
        **envelope,
        "task_case": task_case,
        "host": host,
        "actual_response_model_id": actual_model,
    }
    usage_rows = _usage(usage_value, envelope=bound_envelope)
    expected_ref = payload["expected_source"]
    if expected_ref.get("sha256") != expected_corpus_sha256:
        failures["corpus_binding_mismatch"] += 1
    expected_value = _json_source(expected_ref, root=root, label="v0.13 Host expected task")
    _expected(expected_value, task_case=task_case, host=host)
    if expected_value["run_id"] != run_id or expected_value["workflow_run_id"] != workflow:
        failures["wrong_run_inclusion"] += 1
    result_value = _json_source(
        payload["continuity_source"], root=root, label="v0.13 Host task result"
    )
    _task_result(result_value, envelope=bound_envelope)
    isolation_value = _json_source(
        payload["isolation_source"], root=root, label="v0.13 Host isolation receipt"
    )
    isolation = _isolation(isolation_value, envelope=bound_envelope)

    if len(events) != len(receipts):
        _fail("v0.13 Host event/lifecycle sequences have different lengths")
    event_indices: list[int] = []
    observed_operations: list[str] = []
    observed_gaps: set[str] = set()
    host_identity_digests: set[str] = set()
    sessions: list[str] = []
    lifecycle_digests: list[str] = []
    parsed_events: list[Mapping[str, Any]] = []
    for index, (event, receipt) in enumerate(zip(events, receipts, strict=True)):
        try:
            parsed = parse_native_host_event(event)
            expected_receipt = derive_native_host_receipt(parsed)
        except (NativeHostObservationError, TypeError, ValueError) as exc:
            raise HostTaskEvidenceError(
                f"v0.13 Host event[{index}] is not a native receipt"
            ) from exc
        if dict(receipt) != expected_receipt:
            failures["lifecycle_not_derived"] += 1
        if receipt.get("event_sequence", {}).get("index") != index:
            failures["event_sequence_non_contiguous"] += 1
        if event.get("event_sequence", {}).get("index") != index:
            failures["event_sequence_non_contiguous"] += 1
        if (
            parsed["provenance_level"] != "native_plugin_hook"
            or receipt.get("provenance_level") != "native_plugin_hook"
        ):
            failures["compatibility_bridge"] += 1
        if parsed["host"] != host:
            failures["host_binding_mismatch"] += 1
        if not _host_identity_shape(
            parsed.get("host_identity"),
            host=host,
            schema_version=parsed.get("schema_version"),
        ):
            failures["native_host_pin_mismatch"] += 1
        model = parsed["host_identity"].get(
            "request_model" if host == "codex" else "expected_response_model_id"
        ) if isinstance(parsed.get("host_identity"), Mapping) else None
        if model != actual_model:
            failures["model_substitution"] += 1
        identity_digest = _sha(
            _canonical(_host_identity_projection(parsed.get("host_identity"), host=host))
        )
        host_identity_digests.add(identity_digest)
        sessions.append(parsed["session_sha256"])
        lifecycle_digests.append(_sha(_canonical(receipt)))
        observed_operations.append(str(receipt.get("operation")))
        if receipt.get("status") == "gap" and isinstance(receipt.get("gap"), Mapping):
            observed_gaps.add(str(receipt["gap"].get("code")))
        parsed_events.append(parsed)
        event_indices.append(index)
    if event_indices != list(range(len(events))):
        failures["event_sequence_non_contiguous"] += 1
    if host_identity_digests != {
        _sha(
            _canonical(
                _host_identity_projection(parsed_events[0].get("host_identity"), host=host)
            )
        )
    }:
        failures["native_host_pin_mismatch"] += 1

    result = result_value
    first = result["first_correct_action"]
    if not isinstance(first["observed"], bool) or not first["observed"]:
        failures["first_correct_action_missing"] += 1
    message_indices = [
        index
        for index, event in enumerate(parsed_events)
        if event["event_type"] in {"UserPromptSubmit", "chat.message"}
    ]
    if not message_indices or first["event_index"] != message_indices[0]:
        failures["first_correct_action_missing"] += 1
    if task_case == "continuity" and first["seam"] not in {"knowledge_support", "native_capsule"}:
        failures["first_correct_action_missing"] += 1
    if task_case == "living_wiki" and first["seam"] not in {
        "knowledge_support",
        "wiki_read",
        "source_read",
    }:
        failures["first_correct_action_missing"] += 1
    if task_case == "professional_evidence" and first["seam"] not in {
        "knowledge_support",
        "source_read",
    }:
        failures["first_correct_action_missing"] += 1

    selected = _identity_list(result["selected_identities"])
    expected_identity_hash = _sha(_canonical(selected))
    decision = result["decision_preservation"]
    if (
        not decision["observed"]
        or decision["identity_sha256"] != expected_identity_hash
        or not selected
    ):
        failures["decision_preservation_missing"] += 1

    _, wrong_admissions = _state_rows(
        result["wrong_state_admission"],
        label="wrong-state admission",
        expected_states=TASK_WRONG_STATES[task_case],
    )
    if wrong_admissions:
        failures["wrong_state_admission"] += wrong_admissions
    _, duplicate_admissions = _state_rows(
        result["duplicate_distractor"],
        label="duplicate/distractor admission",
        expected_states=("duplicate", "distractor"),
    )
    if duplicate_admissions:
        failures["duplicate_distractor_admission"] += duplicate_admissions

    duty_rows, _observed_duty_hits = _duties(result["duties"], task_case=task_case)
    expected_statuses = {item["duty"]: item for item in expected_value["duty_expectations"]}
    duty_hits = 0
    for row in duty_rows:
        expectation = expected_statuses[row["duty"]]
        status_allowed = row["status"] in expectation["allowed_statuses"]
        gap_matches = (
            expectation["required_gap_code"] is None
            or row["gap_code"] == expectation["required_gap_code"]
        )
        if status_allowed and gap_matches:
            duty_hits += 1
        else:
            failures["required_duty_gap"] += 1

    observed_seams = set(result["observed_public_seams"])
    expected_seams = {
        "continuity": {"knowledge_support", "native_capsule"},
        "living_wiki": {"wiki_read", "source_read", "query_context"},
        "professional_evidence": {"source_read", "fragment_read", "wiki_read", "query_context"},
    }[task_case]
    if not expected_seams.issubset(observed_seams):
        failures["missing_required_operation"] += len(expected_seams - observed_seams)

    provider = result["provider"]
    _closed(
        provider,
        {
            "capsule_sha256",
            "provider_bytes",
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "reasoning_tokens",
        },
        label="task provider observation",
    )
    _digest(provider["capsule_sha256"], label="task provider capsule")
    provider_bytes = provider["provider_bytes"]
    token_fields = ("input_tokens", "output_tokens", "cache_tokens", "reasoning_tokens")
    if (
        not isinstance(provider_bytes, int)
        or provider_bytes < 1
        or provider_bytes > MAX_PROVIDER_BYTES
    ):
        failures["provider_bytes_overflow"] += 1
    usage_totals = {field: 0 for field in (*token_fields, "provider_bytes")}
    usage_identity = next(iter(host_identity_digests), None)
    for index, row in enumerate(usage_rows):
        allowed = {
            "run_id",
            "workflow_run_id",
            "task_case",
            "host",
            "actual_response_model_id",
            "host_identity_sha256",
            "candidate_commit",
            "candidate_tree",
            "corpus_sha256",
            "runner_identity",
            "runner_sha256",
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "reasoning_tokens",
            "provider_bytes",
            "provider_sha256",
            "latency_ms",
            "rss_peak_bytes",
        }
        if set(row) != allowed:
            _fail(f"usage row {index} keys are not closed")
        if (
            row["run_id"] != envelope["run_binding"]["run_id"]
            or row["workflow_run_id"] != workflow
            or row["task_case"] != task_case
            or row["host"] != host
            or row["actual_response_model_id"] != actual_model
        ):
            failures["wrong_run_inclusion"] += 1
        if row["host_identity_sha256"] != usage_identity:
            failures["native_host_pin_mismatch"] += 1
        if (
            row["candidate_commit"] != envelope["candidate_binding"]["commit"]
            or row["candidate_tree"] != envelope["candidate_binding"]["tree"]
            or row["corpus_sha256"] != envelope["corpus"]["sha256"]
            or row["runner_identity"] != envelope["runner"]["identity"]
            or row["runner_sha256"] != envelope["runner"]["sha256"]
        ):
            failures["candidate_binding_mismatch"] += 1
        for field in (*token_fields, "provider_bytes"):
            number = row[field]
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                _fail(f"usage row {index}.{field} is invalid")
            usage_totals[field] += number
        _digest(row["provider_sha256"], label=f"usage row {index}.provider_sha256")
        if row["provider_bytes"] < 1 or row["provider_bytes"] > MAX_PROVIDER_BYTES:
            failures["provider_bytes_overflow"] += 1
    for field in token_fields:
        if provider[field] != usage_totals[field]:
            failures["provider_usage_mismatch"] += 1
    if provider["provider_bytes"] != usage_totals["provider_bytes"]:
        failures["provider_usage_mismatch"] += 1

    mutation = result["no_hidden_mutation"]
    _closed(
        mutation,
        {
            "hidden_mutation",
            "write_performed",
            "ledger_before_sha256",
            "ledger_after_sha256",
            "authorized_mutation",
            "process_receipt_observed",
        },
        label="task mutation observation",
    )
    _digest(mutation["ledger_before_sha256"], label="ledger before")
    _digest(mutation["ledger_after_sha256"], label="ledger after")
    authorized_mutation = _authorized_mutation(
        mutation["authorized_mutation"],
        task_case=task_case,
        label="task authorized mutation",
    )
    if mutation["hidden_mutation"] is not False or mutation["process_receipt_observed"] is not True:
        failures["hidden_mutation"] += 1
    if mutation["write_performed"] != authorized_mutation["observed"]:
        failures["hidden_mutation"] += 1
    if task_case != "continuity" and (
        authorized_mutation["observed"]
        or mutation["ledger_before_sha256"] != mutation["ledger_after_sha256"]
    ):
        failures["hidden_mutation"] += 1
    if (
        task_case == "continuity"
        and mutation["ledger_before_sha256"] != mutation["ledger_after_sha256"]
        and not authorized_mutation["observed"]
    ):
        failures["hidden_mutation"] += 1

    for field, failure_id in (
        ("query_trace", "query_trace_in_capsule"),
        ("ledger", "ledger_in_capsule"),
    ):
        projection = result[field]
        if projection["in_capsule"] is not False:
            failures[failure_id] += 1
        _digest(projection["sha256"], label=f"{field}.sha256")
        if (
            isinstance(projection["entry_count"], bool)
            or not isinstance(projection["entry_count"], int)
            or projection["entry_count"] < 0
        ):
            _fail(f"{field}.entry_count is invalid")

    secret = isolation["secret_boundary"]
    process = isolation["process_boundary"]
    write = isolation["write_observation"]
    secret_boundary_failure = (
        secret["child_secret_present"]
        or secret["auth_read"]
        or secret["transcript_read"]
        or secret["prompt_read"]
        or secret["reasoning_read"]
        or secret["secret_read"]
    )
    if secret_boundary_failure:
        failures["secret_exposure"] += 1
    process_boundary_failure = (
        not process["native_receipt_observed"]
        or not process["host_process_separated"]
        or not process["mcp_process_separated"]
    )
    if process_boundary_failure:
        failures["wrong_tool_or_parameter"] += 1
    if secret_boundary_failure or process_boundary_failure:
        failures["cross_boundary_disclosure"] += 1
    write_authorized = _authorized_mutation(
        write["authorized_mutation"],
        task_case=task_case,
        label="isolation authorized mutation",
    )
    if (
        write["hidden_mutation"] is not False
        or write["write_performed"] != write_authorized["observed"]
    ):
        failures["hidden_mutation"] += 1
    if task_case != "continuity" and (
        write_authorized["observed"]
        or write["audit_head_before_sha256"] != write["audit_head_after_sha256"]
    ):
        failures["hidden_mutation"] += 1
    if (
        task_case == "continuity"
        and write["audit_head_before_sha256"] != write["audit_head_after_sha256"]
        and not write_authorized["observed"]
    ):
        failures["hidden_mutation"] += 1
    if dict(authorized_mutation) != dict(write_authorized):
        failures["hidden_mutation"] += 1
    _digest(write["audit_head_before_sha256"], label="isolation audit head before")
    _digest(write["audit_head_after_sha256"], label="isolation audit head after")

    if task_case == "continuity":
        required_operations = set(HOST_NATIVE_OPERATIONS[host])
        if not required_operations.issubset(set(observed_operations)):
            failures["missing_required_operation"] += len(
                required_operations - set(observed_operations)
            )

    if task_case == "continuity":
        failures["forgotten_state_admission"] += failures["wrong_state_admission"]
        failures["unrelated_state_loss"] += failures["decision_preservation_missing"]
        failures["ledger_read_mutation"] += failures["hidden_mutation"]
        failures["provider_disclosure"] += (
            failures["query_trace_in_capsule"] + failures["ledger_in_capsule"]
        )

    normalized = {failure: int(failures.get(failure, 0)) for failure in HARD_FAILURE_IDS}
    model_task_failures = sum(normalized.values())

    metrics = {
        "host": host,
        "task_case": task_case,
        "run_id": run_id,
        "workflow_run_id": workflow,
        "actual_response_model_id": actual_model,
        "host_identity_sha256": usage_identity,
        "event_count": len(events),
        "lifecycle_receipt_count": len(receipts),
        "event_sequence_sha256": _sha(_canonical(event_indices)),
        "session_identity_sha256": _sha(_canonical(sessions)),
        "lifecycle_record_sha256": _sha(_canonical(lifecycle_digests)),
        "observed_operations": sorted(set(observed_operations)),
        "observed_gap_codes": sorted(observed_gaps),
        "first_correct_action_rate": 1.0 if not failures["first_correct_action_missing"] else 0.0,
        "decision_preservation_rate": 1.0 if not failures["decision_preservation_missing"] else 0.0,
        "wrong_state_admission_count": wrong_admissions,
        "duplicate_distractor_admission_count": duplicate_admissions,
        "required_duty_count": len(TASK_DUTIES[task_case]),
        "required_duty_observed_count": duty_hits,
        "required_duty_rate": duty_hits / len(TASK_DUTIES[task_case]),
        "provider_bytes": provider_bytes,
        "input_tokens": provider["input_tokens"],
        "output_tokens": provider["output_tokens"],
        "cache_tokens": provider["cache_tokens"],
        "reasoning_tokens": provider["reasoning_tokens"],
        "selected_identity_count": len(selected),
        "no_hidden_mutation": not bool(failures["hidden_mutation"]),
        "query_trace_in_capsule": False,
        "ledger_in_capsule": False,
        "isolation_observed": not bool(
            failures["secret_exposure"]
            or failures["wrong_tool_or_parameter"]
            or failures["cross_boundary_disclosure"]
        ),
        "canary_containment_rate": (
            1.0
            if not (
                failures["secret_exposure"]
                or failures["private_path_disclosure"]
                or failures["cross_boundary_disclosure"]
            )
            else 0.0
        ),
        "model_task_acceptance_rate": 1.0 if model_task_failures == 0 else 0.0,
        "timeline_pass_rate": (
            1.0
            if not (
                failures["wrong_run_inclusion"]
                or failures["private_path_disclosure"]
            )
            else 0.0
        ),
        "forgotten_state_admission_count": failures["forgotten_state_admission"],
        "unrelated_state_preservation": (
            1.0 if not failures["unrelated_state_loss"] else 0.0
        ),
        "ledger_read_invariance": (
            1.0 if not failures["ledger_read_mutation"] else 0.0
        ),
        "scenario_count": 3 if task_case == "continuity" else 0,
    }
    return {
        "schema_version": "deeplaw.typed-qualification-derived/v3",
        "kind": "host_event_sequence",
        "status": "passed" if sum(normalized.values()) == 0 else "failed",
        "metrics": metrics,
        "hard_failure_counts": normalized,
        "evidence_record_sha256": record_sha256,
    }


def is_v013_host_task_event_source(value: Any) -> bool:
    """Return whether an event source uses one of the v0.13 task labels."""

    return (
        isinstance(value, Mapping)
        and value.get("task_case") in TASK_CASES
        and value.get("artifact_kind") == "event_sequence"
    )


__all__ = [
    "HARD_FAILURE_IDS",
    "HOSTS",
    "HOST_MODELS",
    "MAX_PROVIDER_BYTES",
    "SCHEMA_VERSION",
    "TASK_CASES",
    "TASK_DUTIES",
    "TASK_OPERATIONS",
    "TASK_WRONG_STATES",
    "HostTaskEvidenceError",
    "is_v013_host_task_event_source",
    "parse_host_task_evidence",
]
