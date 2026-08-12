"""Host-neutral, sanitized evidence helpers for Pass 13 qualification.

The helpers accept actual in-memory MCP results, recompute the exact
provider-visible text bytes, and return only hashes, counts, and bounded labels.
They never persist raw tool output, task bindings, transcripts, or Query Traces.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

PROVIDER_HARD_LIMIT_BYTES = 65_536
SAFE_READ_OPERATIONS = frozenset({"context", "query"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATH = re.compile(
    rb'(?:^|[\s=:\"\'])/(?:Users|home|tmp|private|var)(?:[\s/\"\']|$)|'
    rb"[A-Za-z]:[\\/]"
)
_FORBIDDEN_ARTIFACT_FIELDS = (
    b'"auth_file"',
    b'"authentication_file"',
    b'"capability_token"',
    b'"grant_id"',
    b'"hidden_reasoning"',
    b'"query_trace"',
    b'"route_identity"',
    b'"task_binding"',
    b'"transcript"',
)
_CREDENTIAL_FIELD = re.compile(
    rb'"(?:[A-Za-z0-9_]*(?:api_key|authorization|cookie|credential|password|secret|'
    rb'capability_token)[A-Za-z0-9_]*|token)"\s*:',
    re.IGNORECASE,
)
_SAFE_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
_SAFE_ARTIFACT_ROLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_MAX_STRUCTURED_OUTPUT_BYTES = 256 * 1024
_MAX_RETAINED_ARTIFACT_BYTES = 8 * 1024 * 1024
_MAX_BUNDLE_BYTES = 32 * 1024 * 1024


class EvidenceValidationError(ValueError):
    """Qualification evidence was incomplete, inconsistent, or unsafe."""


def canonical_json(value: Any) -> str:
    """Serialize one JSON value using DeepLaw's canonical JSON shape."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceValidationError("qualification value is not canonical JSON") from exc


def _encoded(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@lru_cache(maxsize=4)
def _contract_validator(name: str) -> Draft202012Validator:
    schema_path = Path(__file__).resolve().parents[2] / "contracts" / name
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvidenceValidationError("qualification contract is unavailable") from exc
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_contract(name: str, value: Mapping[str, Any], *, label: str) -> None:
    errors = sorted(
        _contract_validator(name).iter_errors(value),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    if errors:
        raise EvidenceValidationError(f"{label} does not satisfy its current contract")


def _require_hash(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise EvidenceValidationError(f"{field} must be one SHA-256 digest")
    return value


def _require_nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EvidenceValidationError(f"{field} must be a non-negative integer")
    return value


def _provider_text(tool_output: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    content = tool_output.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise EvidenceValidationError("MCP result must expose exactly one Provider text block")
    text_block = content[0]
    if (
        not isinstance(text_block, Mapping)
        or text_block.get("type") != "text"
        or not isinstance(text_block.get("text"), str)
    ):
        raise EvidenceValidationError("MCP Provider content is not one text block")
    structured = tool_output.get("structuredContent")
    if not isinstance(structured, Mapping):
        raise EvidenceValidationError("MCP structured output is missing")
    return text_block["text"], structured


def _analyze_call(
    observation: Mapping[str, Any], tool_output: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    if observation.get("server") != "deeplaw":
        raise EvidenceValidationError("safe read used an unexpected MCP server")
    if observation.get("tool_name") != "knowledge_support":
        raise EvidenceValidationError("safe read used an unexpected tool")
    if observation.get("status") != "completed":
        raise EvidenceValidationError("safe read did not complete")
    _require_hash(observation.get("call_id_sha256"), field="call_id_sha256")
    _require_hash(observation.get("arguments_sha256"), field="arguments_sha256")
    _require_nonnegative_int(observation.get("arguments_bytes"), field="arguments_bytes")

    result_bytes = _encoded(tool_output)
    if (
        observation.get("result_sha256") != _sha256(result_bytes)
        or observation.get("result_bytes") != len(result_bytes)
    ):
        raise EvidenceValidationError("MCP result observation does not match in-memory output")

    provider_text, structured = _provider_text(tool_output)
    structured_bytes = _encoded(structured)
    if len(structured_bytes) > _MAX_STRUCTURED_OUTPUT_BYTES:
        raise EvidenceValidationError("structured MCP output exceeds its local bound")
    if (
        observation.get("structured_content_sha256") != _sha256(structured_bytes)
        or observation.get("structured_content_bytes") != len(structured_bytes)
    ):
        raise EvidenceValidationError("structured output observation does not match MCP result")
    if structured.get("schema_version") != "deeplaw.knowledge-support-output/v6":
        raise EvidenceValidationError("safe read must use the current MCP output schema")
    operation = structured.get("operation")
    if operation not in SAFE_READ_OPERATIONS:
        raise EvidenceValidationError("knowledge_support operation is not a safe read")
    provider = structured.get("result")
    if (
        not isinstance(provider, Mapping)
        or provider.get("schema_version") != "deeplaw.provider-knowledge-capsule/v2"
    ):
        raise EvidenceValidationError("current Provider Capsule is missing")
    capsule = provider.get("capsule")
    delivery = provider.get("delivery")
    if not isinstance(capsule, Mapping) or not isinstance(delivery, Mapping):
        raise EvidenceValidationError("Provider Capsule delivery is invalid")

    expected_text = canonical_json(capsule)
    if provider_text != expected_text:
        raise EvidenceValidationError("Provider text is not the exact canonical inner Capsule")
    provider_bytes = provider_text.encode("utf-8")
    _scan_artifact(provider_bytes, forbidden_values=())
    if (
        delivery.get("provider_content_bytes") != len(provider_bytes)
        or delivery.get("hard_limit_bytes") != PROVIDER_HARD_LIMIT_BYTES
        or len(provider_bytes) > PROVIDER_HARD_LIMIT_BYTES
    ):
        raise EvidenceValidationError("Provider byte accounting does not match delivery")
    if delivery.get("write_performed") is not False:
        raise EvidenceValidationError("read-only Provider delivery reported a write")
    statements = capsule.get("statements")
    gaps = capsule.get("gaps")
    if not isinstance(statements, list) or not isinstance(gaps, list):
        raise EvidenceValidationError("Provider Capsule statements or gaps are invalid")
    if delivery.get("projection") != capsule.get("projection"):
        raise EvidenceValidationError("Provider projection does not match delivery")
    _validate_contract(
        "knowledge-support.output.v6.schema.json",
        structured,
        label="MCP structured output",
    )
    _validate_contract(
        "provider-knowledge-capsule.v2.schema.json",
        provider,
        label="Provider Capsule",
    )
    return str(operation), {
        "operation": operation,
        "provider_bytes": len(provider_bytes),
        "provider_sha256": _sha256(provider_bytes),
        "structured_output_bytes": len(structured_bytes),
        "structured_output_sha256": _sha256(structured_bytes),
        "delivery_match": True,
        "write_performed": False,
        "statement_count": len(statements),
        "gap_count": len(gaps),
        "gap_codes": sorted(
            {
                gap["code"]
                for gap in gaps
                if isinstance(gap, Mapping) and isinstance(gap.get("code"), str)
            }
        ),
    }


def analyze_safe_read_calls(
    observations: Sequence[Mapping[str, Any]],
    tool_outputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one first read plus at most one safe bounded retry."""

    if len(observations) not in {1, 2} or len(tool_outputs) != len(observations):
        raise EvidenceValidationError("qualification requires one or two safe read calls")
    call_ids = [observation.get("call_id_sha256") for observation in observations]
    if len(set(call_ids)) != len(call_ids):
        raise EvidenceValidationError("safe read call identities must be unique")
    operations: list[str] = []
    payloads: list[dict[str, Any]] = []
    for observation, tool_output in zip(observations, tool_outputs, strict=True):
        if not isinstance(observation, Mapping) or not isinstance(tool_output, Mapping):
            raise EvidenceValidationError("safe read observation is invalid")
        operation, payload = _analyze_call(observation, tool_output)
        operations.append(operation)
        payloads.append(payload)
    if len(payloads) == 2 and payloads[0]["gap_count"] == 0:
        raise EvidenceValidationError(
            "bounded retry requires an insufficient first Provider Capsule"
        )
    return {
        "call_count": len(observations),
        "first_call_valid": True,
        "bounded_retry_used": len(observations) == 2,
        "safe_read_operations": operations,
        "provider_payloads": payloads,
    }


def _scan_artifact(data: bytes, *, forbidden_values: Sequence[str]) -> None:
    if _ABSOLUTE_PATH.search(data):
        raise EvidenceValidationError("artifact contains an absolute path")
    lowered = data.lower()
    if any(field in lowered for field in _FORBIDDEN_ARTIFACT_FIELDS):
        raise EvidenceValidationError("artifact contains a forbidden evidence field")
    if _CREDENTIAL_FIELD.search(data):
        raise EvidenceValidationError("artifact contains a credential-bearing field")
    if b"file://" in lowered or re.search(rb'(?:^|[\s=:\"\'])\\\\[^\s\"\']+', data):
        raise EvidenceValidationError("artifact contains an absolute path")
    for value in forbidden_values:
        if isinstance(value, str) and value and value.encode("utf-8") in data:
            raise EvidenceValidationError("artifact contains a forbidden value")


def write_retained_artifact(
    path: Path,
    data: bytes,
    *,
    output_root: Path,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Scan one in-memory artifact before creating its retained file."""

    if not isinstance(output_root, Path) or output_root.is_symlink() or not output_root.is_dir():
        raise EvidenceValidationError("retained artifact root is invalid")
    try:
        root = output_root.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
    except OSError as exc:
        raise EvidenceValidationError("retained artifact root is unavailable") from exc
    if (
        not isinstance(path, Path)
        or _SAFE_ARTIFACT_NAME.fullmatch(path.name) is None
        or parent != root
        or path.parent.is_symlink()
    ):
        raise EvidenceValidationError("retained artifact path is invalid")
    if (
        not isinstance(data, bytes)
        or not data
        or len(data) > _MAX_RETAINED_ARTIFACT_BYTES
    ):
        raise EvidenceValidationError("retained artifact bytes are invalid")
    _scan_artifact(data, forbidden_values=forbidden_values)
    with path.open("xb") as stream:
        stream.write(data)
    return {"name": path.name, "bytes": len(data), "sha256": _sha256(data)}


def build_bundle_manifest(
    *,
    host: str,
    commit: str,
    tree: str,
    artifacts: Mapping[str, Path],
    output_root: Path,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a path-free SHA manifest after scanning every retained artifact."""

    if host not in {"codex", "opencode"}:
        raise EvidenceValidationError("bundle host is unsupported")
    if _GIT_OID.fullmatch(commit) is None or _GIT_OID.fullmatch(tree) is None:
        raise EvidenceValidationError("bundle Git binding is invalid")
    if not isinstance(output_root, Path) or output_root.is_symlink() or not output_root.is_dir():
        raise EvidenceValidationError("bundle root is invalid")
    try:
        root = output_root.resolve(strict=True)
    except OSError as exc:
        raise EvidenceValidationError("bundle root is unavailable") from exc
    if not artifacts or len(artifacts) > 64:
        raise EvidenceValidationError("bundle artifact inventory is invalid")
    rows: list[dict[str, Any]] = []
    names: set[str] = set()
    roles: set[str] = set()
    total_bytes = 0
    for role, path in sorted(artifacts.items()):
        if not isinstance(role, str) or _SAFE_ARTIFACT_ROLE.fullmatch(role) is None:
            raise EvidenceValidationError("bundle artifact role is invalid")
        if role in roles:
            raise EvidenceValidationError("bundle artifact role is duplicated")
        roles.add(role)
        if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
            raise EvidenceValidationError("bundle artifact must be one regular file")
        try:
            if path.parent.resolve(strict=True) != root:
                raise EvidenceValidationError("bundle artifact is outside its output root")
        except OSError as exc:
            raise EvidenceValidationError("bundle artifact root is unavailable") from exc
        name = path.name
        if name in names or _SAFE_ARTIFACT_NAME.fullmatch(name) is None:
            raise EvidenceValidationError("bundle artifact name is invalid or duplicated")
        names.add(name)
        data = path.read_bytes()
        if not data or len(data) > _MAX_RETAINED_ARTIFACT_BYTES:
            raise EvidenceValidationError("bundle artifact size is invalid")
        total_bytes += len(data)
        if total_bytes > _MAX_BUNDLE_BYTES:
            raise EvidenceValidationError("bundle exceeds its retained byte bound")
        _scan_artifact(data, forbidden_values=forbidden_values)
        rows.append(
            {
                "role": role,
                "name": name,
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        )
    required_roles = {
        "qualification_report",
        "sanitized_events_run_1",
        "sanitized_events_run_2",
        "sanitized_events_run_3",
    }
    if host == "opencode":
        required_roles.add("preflight_receipt")
    if roles != required_roles:
        raise EvidenceValidationError("bundle artifact role set is incomplete or unexpected")
    rows.sort(key=lambda row: row["name"])
    return {
        "schema_version": "deeplaw.host-qualification-bundle-manifest/v1",
        "host": host,
        "commit": commit,
        "tree": tree,
        "artifacts": rows,
    }


_SCENARIO_MATRIX = {
    "codex": ("cold_start", "resume_fork", "compaction_forget"),
    "opencode": ("projection_status", "source_forget", "provider_boundary"),
}
_CODEX_METHODS = {
    "cold_start": {"thread/start"},
    "resume_fork": {"thread/start", "thread/resume", "thread/fork"},
    "compaction_forget": {
        "thread/start",
        "thread/compact/start",
        "thread/compacted",
    },
}
_CODEX_TURN_METHODS = {
    "cold_start": ("thread/start",),
    "resume_fork": ("thread/start", "thread/resume", "thread/fork"),
    "compaction_forget": (
        "thread/start",
        "thread/compact/start",
        "thread/compact/start",
    ),
}
_MUTATION_KINDS = {
    "codex": {
        "cold_start": ("seed_checkpoint",),
        "resume_fork": ("seed_checkpoint",),
        "compaction_forget": ("seed_checkpoint", "forget"),
    },
    "opencode": {
        "projection_status": ("seed_checkpoint",),
        "source_forget": ("seed_checkpoint", "forget"),
        "provider_boundary": ("none",),
    },
}


def _metric_evidence(run: Mapping[str, Any]) -> str:
    metrics = run.get("metrics")
    if not isinstance(metrics, Mapping):
        raise EvidenceValidationError("Host run omitted scenario metrics")
    payload = {
        "scenario": run.get("scenario"),
        "task_sha256": run.get("task_sha256"),
        "turns": [
            {
                "final_response_sha256": turn.get("final_response_sha256"),
                "provider_sha256": [
                    item.get("provider_sha256")
                    for item in turn.get("safe_read", {}).get("provider_payloads", [])
                    if isinstance(item, Mapping)
                ],
            }
            for turn in run.get("turns", [])
            if isinstance(turn, Mapping)
        ],
        "checks": {key: value for key, value in metrics.items() if key != "evidence_sha256"},
    }
    return _sha256(_encoded(payload))


def metric_evidence_sha256(run: Mapping[str, Any]) -> str:
    """Bind scenario checks to the exact retained response/Capsule hashes."""

    return _metric_evidence(run)


def _token_aggregate(runs: Sequence[Mapping[str, Any]], field: str) -> int | str:
    values = [
        turn.get("usage", {}).get(field)
        for run in runs
        for turn in run.get("turns", [])
        if isinstance(turn, Mapping)
    ]
    if values and all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return sum(values)
    return "unreported"


def validate_host_report_consistency(report: Mapping[str, Any]) -> None:
    """Validate the full report contract and recompute cross-field facts."""

    _validate_contract(
        "host-continuity-qualification.v1.schema.json",
        report,
        label="Host qualification report",
    )

    host = report.get("host")
    expected_scenarios = _SCENARIO_MATRIX.get(host)
    runs = report.get("runs")
    if expected_scenarios is None or not isinstance(runs, list) or len(runs) != 3:
        raise EvidenceValidationError("Host report must contain its exact scenario matrix")
    attestation = report.get("host_attestation")
    security = report.get("security")
    if not isinstance(attestation, Mapping) or not isinstance(security, Mapping):
        raise EvidenceValidationError("Host attestation or security receipt is missing")
    expected_host = {
        "codex": ("codex", "gpt-5.6-luna", "max"),
        "opencode": ("opencode", "deepseek/deepseek-v4-flash", "max"),
    }[str(host)]
    attested_host = tuple(
        attestation.get(field) for field in ("binary_name", "model", "reasoning_effort")
    )
    if attested_host != expected_host:
        raise EvidenceValidationError("Host attestation identity is invalid")
    observed = tuple(run.get("scenario") for run in runs if isinstance(run, Mapping))
    indexes = tuple(run.get("run_index") for run in runs if isinstance(run, Mapping))
    if observed != expected_scenarios or indexes != (1, 2, 3):
        raise EvidenceValidationError("Host report scenario matrix or run indexes are invalid")

    passed_runs = 0
    provider_bytes = 0
    first_call_valid_runs = 0
    bounded_retry_runs = 0
    for run in runs:
        if not isinstance(run, Mapping):
            raise EvidenceValidationError("Host report run is invalid")
        run_passed = run.get("status") == "passed"
        if run_passed:
            passed_runs += 1
            if run.get("failure_codes"):
                raise EvidenceValidationError("passed Host run retained failure codes")
        elif not run.get("failure_codes"):
            raise EvidenceValidationError("failed Host run must retain a failure code")
        methods = run.get("methods_observed")
        if not isinstance(methods, list) or not methods:
            raise EvidenceValidationError("Host run omitted lifecycle methods")
        if host == "codex":
            expected_method_set = _CODEX_METHODS[str(run["scenario"])]
            if (run_passed and set(methods) != expected_method_set) or not set(
                methods
            ).issubset(expected_method_set):
                raise EvidenceValidationError("Codex run lifecycle method set is invalid")
        if host == "opencode" and set(methods) != {"opencode/run"}:
            raise EvidenceValidationError("OpenCode run lifecycle method is invalid")
        turns = run.get("turns")
        if not isinstance(turns, list) or not turns:
            raise EvidenceValidationError("Host run omitted turn evidence")
        turn_statuses = [turn.get("status") for turn in turns if isinstance(turn, Mapping)]
        if run_passed and (len(turn_statuses) != len(turns) or set(turn_statuses) != {"passed"}):
            raise EvidenceValidationError("passed Host run contains a failed turn")
        if not run_passed and "failed" not in turn_statuses:
            raise EvidenceValidationError("failed Host run does not contain a failed turn")
        turn_methods = tuple(
            turn.get("lifecycle_method") for turn in turns if isinstance(turn, Mapping)
        )
        expected_turn_methods = (
            _CODEX_TURN_METHODS[str(run["scenario"])] if host == "codex" else ("opencode/run",)
        )
        if run_passed and turn_methods != expected_turn_methods:
            raise EvidenceValidationError("Host turn lifecycle sequence is invalid")
        if not run_passed and turn_methods != expected_turn_methods[: len(turn_methods)]:
            raise EvidenceValidationError("failed Host turn lifecycle prefix is invalid")
        if run.get("new_thread") is not True:
            raise EvidenceValidationError("qualification scenarios require distinct new tasks")
        if host == "codex" and run_passed:
            thread_ids = [turn.get("thread_id_sha256") for turn in turns]
            turn_ids = [turn.get("turn_id_sha256") for turn in turns]
            if any(value is None for value in (*thread_ids, *turn_ids)):
                raise EvidenceValidationError("Codex lifecycle identities are missing")
            if len(set(turn_ids)) != len(turn_ids):
                raise EvidenceValidationError("Codex turn identities must be unique")
            if run["scenario"] == "resume_fork" and thread_ids[-1] == thread_ids[-2]:
                raise EvidenceValidationError("Codex fork did not create a distinct thread")
        first_read: Mapping[str, Any] | None = None
        retried = False
        for turn in turns:
            if not isinstance(turn, Mapping):
                raise EvidenceValidationError("Host turn evidence is invalid")
            before = turn.get("ledger_audit_head_before")
            after = turn.get("ledger_audit_head_after")
            unchanged = turn.get("ledger_unchanged")
            if unchanged is not (before == after):
                raise EvidenceValidationError("turn ledger unchanged flag is inconsistent")
            if turn.get("status") == "passed" and unchanged is not True:
                raise EvidenceValidationError("passed read-only turn changed the ledger")
            safe_read = turn.get("safe_read")
            if not isinstance(safe_read, Mapping):
                raise EvidenceValidationError("Host turn omitted safe-read evidence")
            count = safe_read.get("call_count")
            operations = safe_read.get("safe_read_operations")
            payloads = safe_read.get("provider_payloads")
            if not isinstance(operations, list) or not isinstance(payloads, list):
                raise EvidenceValidationError("safe-read arrays are invalid")
            if count != len(operations) or count != len(payloads):
                raise EvidenceValidationError("safe-read call count does not match its payloads")
            if turn.get("status") == "passed" and count not in {1, 2}:
                raise EvidenceValidationError("passed turn requires one or two safe reads")
            if safe_read.get("bounded_retry_used") is not (count == 2):
                raise EvidenceValidationError("bounded retry flag is inconsistent")
            if turn.get("status") == "passed" and safe_read.get("first_call_valid") is not True:
                raise EvidenceValidationError("passed turn lacks first-call validity")
            if first_read is None:
                first_read = safe_read
            retried = retried or count == 2
            provider_bytes += sum(
                payload.get("provider_bytes", 0)
                for payload in payloads
                if isinstance(payload, Mapping)
            )
            usage = turn.get("usage")
            if isinstance(usage, Mapping):
                input_tokens = usage.get("input_tokens")
                cached_input_tokens = usage.get("cached_input_tokens")
                output_tokens = usage.get("output_tokens")
                reasoning_output_tokens = usage.get("reasoning_output_tokens")
                total = usage.get("total_tokens")
                if (
                    isinstance(input_tokens, int)
                    and not isinstance(input_tokens, bool)
                    and isinstance(output_tokens, int)
                    and not isinstance(output_tokens, bool)
                    and total != input_tokens + output_tokens
                ):
                    raise EvidenceValidationError("provider token arithmetic is inconsistent")
                if (
                    isinstance(cached_input_tokens, int)
                    and not isinstance(cached_input_tokens, bool)
                    and isinstance(input_tokens, int)
                    and not isinstance(input_tokens, bool)
                    and cached_input_tokens > input_tokens
                ):
                    raise EvidenceValidationError("cached input tokens exceed input tokens")
                if (
                    isinstance(reasoning_output_tokens, int)
                    and not isinstance(reasoning_output_tokens, bool)
                    and isinstance(output_tokens, int)
                    and not isinstance(output_tokens, bool)
                    and reasoning_output_tokens > output_tokens
                ):
                    raise EvidenceValidationError("reasoning tokens exceed output tokens")
        if first_read is not None and first_read.get("first_call_valid") is True:
            first_call_valid_runs += 1
        if retried:
            bounded_retry_runs += 1

        boundaries = run.get("mutation_boundaries")
        if not isinstance(boundaries, list):
            raise EvidenceValidationError("Host run omitted mutation boundaries")
        kinds = tuple(
            boundary.get("kind") for boundary in boundaries if isinstance(boundary, Mapping)
        )
        expected_kinds = _MUTATION_KINDS[str(host)][str(run["scenario"])]
        if len(kinds) != len(boundaries) or (
            run_passed and kinds != expected_kinds
        ) or (not run_passed and kinds != expected_kinds[: len(kinds)]):
            raise EvidenceValidationError("Host mutation boundary sequence is invalid")
        for boundary in boundaries:
            if not isinstance(boundary, Mapping):
                raise EvidenceValidationError("Host mutation boundary is invalid")
            changed = boundary.get("audit_head_before") != boundary.get("audit_head_after")
            if boundary.get("audit_changed") is not changed:
                raise EvidenceValidationError("mutation audit change flag is inconsistent")
            if boundary["kind"] == "none":
                if boundary.get("owner_enabled") is not False:
                    raise EvidenceValidationError("no-mutation boundary claims owner enablement")
            elif (
                boundary.get("owner_enabled") is not True
                or boundary.get("receipt_sha256") is None
                or boundary.get("target_sha256") is None
            ):
                raise EvidenceValidationError("owner mutation lacks receipt binding")

        metrics = run.get("metrics")
        if not isinstance(metrics, Mapping) or metrics.get(
            "evidence_sha256"
        ) != _metric_evidence(run):
            raise EvidenceValidationError("scenario metrics are not bound to response evidence")
        if run_passed and host == "codex":
            required_common = {
                "first_correct_action": True,
                "wrong_state_admission": 0,
                "stale_state_rejected": True,
                "provider_boundary_correct": True,
            }
            for field, value in required_common.items():
                if metrics.get(field) != value:
                    raise EvidenceValidationError("passed scenario metric is not satisfied")
            if (
                run["scenario"] == "resume_fork"
                and metrics.get("decision_preservation") is not True
            ):
                raise EvidenceValidationError("resume/fork decision was not preserved")
            if run["scenario"] == "compaction_forget" and (
                metrics.get("forgotten_state_admission") != 0
                or metrics.get("gap_observed") is not True
            ):
                raise EvidenceValidationError("compaction/forget admission is invalid")
        if run_passed and host == "opencode":
            provider_payloads = [
                payload
                for turn in turns
                if isinstance(turn, Mapping)
                for payload in turn.get("safe_read", {}).get("provider_payloads", [])
                if isinstance(payload, Mapping)
            ]
            observed_gap_codes = {
                code
                for payload in provider_payloads
                for code in payload.get("gap_codes", [])
                if isinstance(code, str)
            }
            if metrics.get("provider_boundary_correct") is not True:
                raise EvidenceValidationError("OpenCode Provider boundary is invalid")
            if run["scenario"] == "projection_status" and metrics.get(
                "projection_state_correct"
            ) is not True:
                raise EvidenceValidationError("projection status was not reported correctly")
            if (
                run["scenario"] == "projection_status"
                and "uncompiled_source" not in observed_gap_codes
            ):
                raise EvidenceValidationError("projection Gap evidence is missing")
            if run["scenario"] == "source_forget" and metrics.get(
                "retention_wording_correct"
            ) is not True:
                raise EvidenceValidationError("source forget wording was not preserved")
            if run["scenario"] == "source_forget" and (
                metrics.get("forgotten_state_admission") != 0
                or metrics.get("gap_observed") is not True
                or any(payload.get("statement_count") != 0 for payload in provider_payloads)
                or not any(payload.get("gap_count", 0) > 0 for payload in provider_payloads)
            ):
                raise EvidenceValidationError("source forget admission is invalid")

    lifecycle = report.get("lifecycle")
    root_methods = lifecycle.get("methods_observed") if isinstance(lifecycle, Mapping) else None
    required_root = (
        set().union(*_CODEX_METHODS.values()) if host == "codex" else {"not_applicable"}
    )
    observed_method_union = (
        {
            method
            for run in runs
            for method in run.get("methods_observed", [])
            if isinstance(method, str)
        }
        if host == "codex"
        else {"not_applicable"}
    )
    if not isinstance(root_methods, list) or set(root_methods) != observed_method_union:
        raise EvidenceValidationError("root Host lifecycle does not match run evidence")
    if report.get("status") == "executed" and set(root_methods) != required_root:
        raise EvidenceValidationError("root Host lifecycle coverage is incomplete")
    if not set(root_methods).issubset(required_root):
        raise EvidenceValidationError("root Host lifecycle contains unexpected methods")

    aggregate = report.get("aggregate")
    if not isinstance(aggregate, Mapping):
        raise EvidenceValidationError("Host aggregate is missing")
    expected_aggregate = {
        "passed_runs": passed_runs,
        "failed_runs": 3 - passed_runs,
        "first_call_valid_runs": first_call_valid_runs,
        "bounded_retry_runs": bounded_retry_runs,
        "provider_bytes": provider_bytes,
        "host_elapsed_ms": sum(
            turn.get("host_elapsed_ms", 0)
            for run in runs
            for turn in run.get("turns", [])
            if isinstance(turn, Mapping)
        ),
        **{
            field: _token_aggregate(runs, field)
            for field in (
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "total_tokens",
            )
        },
    }
    if any(aggregate.get(field) != value for field, value in expected_aggregate.items()):
        raise EvidenceValidationError("Host aggregate does not match recomputed run evidence")
    expected_status = (
        "executed" if passed_runs == 3 else "failed" if passed_runs == 0 else "partial"
    )
    if report.get("status") != expected_status:
        raise EvidenceValidationError("Host report status does not match its runs")
    if report.get("status") == "executed":
        inventories = (attestation.get("model_inventory"), attestation.get("mcp_inventory"))
        if any(
            not isinstance(item, Mapping)
            or item.get("checked") is not True
            or item.get("selected_present") is not True
            for item in inventories
        ):
            raise EvidenceValidationError("executed Host report lacks current inventory proof")
        required_security = {
            "mcp_child_closed_environment": True,
            "only_knowledge_support_enabled": True,
            "absolute_path_leak": False,
            "secret_leak": False,
        }
        if any(security.get(field) != value for field, value in required_security.items()):
            raise EvidenceValidationError("executed Host report failed a security boundary")
