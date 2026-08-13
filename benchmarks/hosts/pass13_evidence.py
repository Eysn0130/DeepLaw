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
    rb'(?:^|[\s=:\"\'])/(?!/)[A-Za-z0-9._~-]+(?:/[^\s\"\'\\]*)?|'
    rb'(?:^|[\s="\'(])[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+[\\/]'
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


def isolation_receipt(*, host: str) -> dict[str, Any]:
    """Return a path-free receipt for one closed temporary Host profile."""

    if host not in {"codex", "opencode"}:
        raise EvidenceValidationError("Host isolation receipt has an unsupported host")
    return {
        "profile_kind": "temporary_closed",
        "home_isolated": True,
        "codex_home_isolated": host == "codex",
        "xdg_config_home_isolated": True,
        "xdg_data_home_isolated": True,
        "ambient_host_state_inherited": False,
        "ambient_plugins_inherited": False,
        "ambient_apps_inherited": False,
        "ambient_hooks_inherited": False,
        "secret_values_retained": False,
        "auth_class": "chatgpt_login" if host == "codex" else "deepseek_api_key",
    }


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
    evidence = capsule.get("evidence", [])
    if not isinstance(evidence, list):
        raise EvidenceValidationError("Provider Capsule evidence is invalid")
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
    evidence_keys = [
        (
            item.get("source_revision_id"),
            item.get("fragment_id"),
            item.get("content_sha256"),
        )
        for item in evidence
        if isinstance(item, Mapping)
    ]
    duplicate_evidence_count = len(evidence_keys) - len(set(evidence_keys))
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
        "relevant_chars": 0,
        "context_chars": len(provider_text),
        "relevant_chars_context_chars": 0.0 if provider_text else None,
        "evidence_count": len(evidence_keys),
        "duplicate_evidence_count": duplicate_evidence_count,
        "duplicate_evidence_rate": (
            duplicate_evidence_count / len(evidence_keys) if evidence_keys else None
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


def bind_relevant_chars(
    safe_read: Mapping[str, Any],
    tool_outputs: Sequence[Mapping[str, Any]],
    relevant_text: Sequence[str],
) -> dict[str, Any]:
    """Bind exact task-relevant character spans to measured Capsule characters.

    This is a character measurement, not a token estimate.  Overlapping spans
    are counted once, and the Provider text is checked against the already
    retained digest before the derived ratio is returned.
    """

    payloads = safe_read.get("provider_payloads")
    if not isinstance(payloads, list) or len(payloads) != len(tool_outputs):
        raise EvidenceValidationError("Provider payload relevance inputs are inconsistent")
    markers = tuple(
        dict.fromkeys(
            item for item in relevant_text if isinstance(item, str) and item
        )
    )
    measured: list[dict[str, Any]] = []
    for payload, output in zip(payloads, tool_outputs, strict=True):
        if not isinstance(payload, Mapping) or not isinstance(output, Mapping):
            raise EvidenceValidationError("Provider relevance input is invalid")
        provider_text, _structured = _provider_text(output)
        provider_bytes = provider_text.encode("utf-8")
        if (
            payload.get("provider_sha256") != _sha256(provider_bytes)
            or payload.get("provider_bytes") != len(provider_bytes)
            or payload.get("context_chars") != len(provider_text)
        ):
            raise EvidenceValidationError("Provider relevance text does not match its receipt")
        covered: set[int] = set()
        for marker in markers:
            start = 0
            while True:
                position = provider_text.find(marker, start)
                if position < 0:
                    break
                covered.update(range(position, position + len(marker)))
                start = position + max(1, len(marker))
        relevant_chars = len(covered)
        row = dict(payload)
        row["relevant_chars"] = relevant_chars
        row["relevant_chars_context_chars"] = (
            relevant_chars / len(provider_text) if provider_text else None
        )
        measured.append(row)
    result = dict(safe_read)
    result["provider_payloads"] = measured
    return result


_EXPECTED_KNOWLEDGE_OPERATIONS = (
    "compilation",
    "context",
    "editor_context",
    "explain",
    "gaps",
    "get",
    "graph",
    "identity_lookup",
    "inspect",
    "lineage",
    "query",
    "recall",
    "search",
    "semantic",
    "source",
    "synthesis",
    "verify",
    "wiki",
    "wiki_lookup",
)


def _operation_names(schema: object) -> tuple[str, ...]:
    operations: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            properties = value.get("properties")
            if isinstance(properties, Mapping):
                operation = properties.get("operation")
                if isinstance(operation, Mapping):
                    constant = operation.get("const")
                    if isinstance(constant, str):
                        operations.add(constant)
                    choices = operation.get("enum")
                    if isinstance(choices, Sequence) and not isinstance(choices, str):
                        operations.update(item for item in choices if isinstance(item, str))
            for item in value.values():
                walk(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                walk(item)

    walk(schema)
    return tuple(sorted(operations))


def knowledge_support_tool_schema_receipt(tools: Sequence[Any]) -> dict[str, Any]:
    """Measure the exact self-contained inputSchema returned by MCP tools/list."""

    if len(tools) != 1:
        raise EvidenceValidationError("tools/list must expose only knowledge_support")
    tool = tools[0]
    name = tool.get("name") if isinstance(tool, Mapping) else getattr(tool, "name", None)
    schema = (
        tool.get("inputSchema")
        if isinstance(tool, Mapping)
        else getattr(tool, "inputSchema", None)
    )
    if name != "knowledge_support" or not isinstance(schema, Mapping):
        raise EvidenceValidationError("tools/list omitted the knowledge_support inputSchema")
    schema_bytes = _encoded(schema)
    # The schema itself legitimately names closed fields such as task_binding
    # and can contain URI identifiers.  It is measured in memory and only its
    # byte count and digest are retained; raw schema bytes are never reported.
    operations = _operation_names(schema)
    if operations != _EXPECTED_KNOWLEDGE_OPERATIONS:
        raise EvidenceValidationError("tools/list returned an unexpected operation inventory")
    return {
        "tools_list_observed": True,
        "tool_name": "knowledge_support",
        "input_schema_bytes": len(schema_bytes),
        "input_schema_sha256": _sha256(schema_bytes),
        "operation_count": len(operations),
        "operations": list(operations),
        "measurement_kind": "canonical_utf8_bytes_not_tokens",
        "provider_observed_schema_tokens": None,
    }


def native_lifecycle_receipt(
    *,
    semantic_task_family: str,
    transport: str,
    request_seam: str,
    requested_operation: str,
    sanitized_request: Mapping[str, Any],
    observation_kind: str,
    methods_observed: Sequence[str],
    sanitized_observation: Mapping[str, Any] | bytes,
    current_identity: str,
    parent_identity: str | None,
    root_identity: str,
    relation: str,
    actual_provider_usage: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build one path-free receipt from a real native request/observation seam."""

    request_bytes = _encoded(sanitized_request)
    observation_bytes = (
        sanitized_observation
        if isinstance(sanitized_observation, bytes)
        else _encoded(sanitized_observation)
    )
    _scan_artifact(request_bytes, forbidden_values=())
    _scan_artifact(observation_bytes, forbidden_values=())
    methods = list(dict.fromkeys(methods_observed))
    if not methods or any(not isinstance(method, str) or not method for method in methods):
        raise EvidenceValidationError("native receipt omitted an observed method or response")
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    if actual_provider_usage is not None and any(
        not isinstance(actual_provider_usage.get(field), int)
        or isinstance(actual_provider_usage.get(field), bool)
        or actual_provider_usage[field] < 0
        for field in fields
    ):
        raise EvidenceValidationError("native receipt has invalid actual Provider usage")
    if not current_identity or not root_identity:
        raise EvidenceValidationError("native receipt omitted session identity lineage")
    return {
        "semantic_task_family": semantic_task_family,
        "transport": transport,
        "request_seam": request_seam,
        "requested_operation": requested_operation,
        "sanitized_request": {
            "bytes": len(request_bytes),
            "sha256": _sha256(request_bytes),
        },
        "observation_kind": observation_kind,
        "methods_observed": methods,
        "sanitized_raw_observation": {
            "bytes": len(observation_bytes),
            "sha256": _sha256(observation_bytes),
        },
        "identity_lineage": {
            "current_sha256": _sha256(current_identity.encode("utf-8")),
            "parent_sha256": (
                _sha256(parent_identity.encode("utf-8")) if parent_identity else None
            ),
            "root_sha256": _sha256(root_identity.encode("utf-8")),
            "relation": relation,
        },
        "actual_provider_usage": (
            {field: int(actual_provider_usage[field]) for field in fields}
            if actual_provider_usage is not None
            else None
        ),
        "claim_eligible": False,
    }


def _scan_artifact(data: bytes, *, forbidden_values: Sequence[str]) -> None:
    if _ABSOLUTE_PATH.search(data):
        raise EvidenceValidationError("artifact contains an absolute path")
    lowered = data.lower()
    if any(field in lowered for field in _FORBIDDEN_ARTIFACT_FIELDS):
        raise EvidenceValidationError("artifact contains a forbidden evidence field")
    credential_scan = (
        data.replace(b'"secret_leak":false', b'"safe_flag":false')
        .replace(b'"authentication_material_retained":false', b'"safe_flag":false')
        .replace(b'"secret_values_retained":false', b'"safe_flag":false')
    )
    if _CREDENTIAL_FIELD.search(credential_scan):
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


_QUALIFICATION_SCENARIOS = ("cold_start", "resume_fork", "compaction_forget")
_DIAGNOSTIC_SCENARIOS = ("development_diagnostic",)
_NATIVE_REQUESTS = {
    "codex": {
        "cold_start": {"thread/start"},
        "resume_fork": {"thread/start", "thread/resume", "thread/fork"},
        "compaction_forget": {"thread/start", "thread/compact/start"},
        "development_diagnostic": {
            "thread/start",
            "thread/resume",
            "thread/fork",
            "thread/compact/start",
        },
    },
    "opencode": {
        "cold_start": {"cli.run", "session.get"},
        "resume_fork": {
            "cli.run",
            "cli.run.session",
            "cli.run.fork",
            "session.get",
        },
        "compaction_forget": {
            "cli.run",
            "cli.run.session",
            "session.get",
            "session.summarize",
            "session.messages",
        },
        "development_diagnostic": {
            "cli.run",
            "cli.run.session",
            "cli.run.fork",
            "session.get",
            "session.summarize",
            "session.messages",
        },
    },
}
_NATIVE_OBSERVATIONS = {
    "codex": {
        "cold_start": {"thread/start"},
        "resume_fork": {"thread/start", "thread/resume", "thread/fork"},
        "compaction_forget": {
            "thread/start",
            "thread/compact/start",
            "item/started",
            "item/completed",
        },
        "development_diagnostic": {
            "thread/start",
            "thread/resume",
            "thread/fork",
            "thread/compact/start",
            "item/started",
            "item/completed",
        },
    },
    "opencode": {
        "cold_start": {"cli.run.json", "session.get"},
        "resume_fork": {"cli.run.json", "session.get"},
        "compaction_forget": {
            "cli.run.json",
            "session.get",
            "session.summarize",
            "session.messages",
        },
        "development_diagnostic": {
            "cli.run.json",
            "session.get",
            "session.summarize",
            "session.messages",
        },
    },
}
_NATIVE_RECEIPT_SEQUENCE = {
    "codex": {
        "cold_start": (("thread/start", "thread/start"),),
        "resume_fork": (
            ("thread/start", "thread/start"),
            ("thread/resume", "thread/resume"),
            ("thread/fork", "thread/fork"),
        ),
        "compaction_forget": (
            ("thread/start", "thread/start"),
            ("thread/compact/start", "thread/compact/start"),
            ("thread/compact/start", "item/started"),
            ("thread/compact/start", "item/completed"),
        ),
        "development_diagnostic": (
            ("thread/start", "thread/start"),
            ("thread/resume", "thread/resume"),
            ("thread/fork", "thread/fork"),
            ("thread/compact/start", "thread/compact/start"),
            ("thread/compact/start", "item/started"),
            ("thread/compact/start", "item/completed"),
        ),
    },
    "opencode": {
        "cold_start": (
            ("cli.run", "cli.run.json"),
            ("session.get", "session.get"),
        ),
        "resume_fork": (
            ("cli.run", "cli.run.json"),
            ("session.get", "session.get"),
            ("cli.run.session", "cli.run.json"),
            ("session.get", "session.get"),
            ("cli.run.fork", "cli.run.json"),
            ("session.get", "session.get"),
        ),
        "compaction_forget": (
            ("cli.run", "cli.run.json"),
            ("session.get", "session.get"),
            ("session.summarize", "session.summarize"),
            ("session.messages", "session.messages"),
            ("cli.run.session", "cli.run.json"),
            ("session.get", "session.get"),
            ("cli.run.session", "cli.run.json"),
            ("session.get", "session.get"),
        ),
        "development_diagnostic": (
            ("cli.run", "cli.run.json"),
            ("session.get", "session.get"),
            ("cli.run.session", "cli.run.json"),
            ("session.get", "session.get"),
            ("cli.run.fork", "cli.run.json"),
            ("session.get", "session.get"),
            ("session.summarize", "session.summarize"),
            ("session.messages", "session.messages"),
            ("cli.run.session", "cli.run.json"),
            ("session.get", "session.get"),
        ),
    },
}
_NATIVE_RECEIPT_RULES = {
    "codex": {
        "thread/start": {
            "transport": "codex_app_server_jsonrpc",
            "request_seams": {"thread/start"},
            "observations": {"thread/start"},
            "observation_kind": "native_response",
            "relation": "new",
        },
        "thread/resume": {
            "transport": "codex_app_server_jsonrpc",
            "request_seams": {"thread/resume"},
            "observations": {"thread/resume"},
            "observation_kind": "native_response",
            "relation": "resume",
        },
        "thread/fork": {
            "transport": "codex_app_server_jsonrpc",
            "request_seams": {"thread/fork"},
            "observations": {"thread/fork"},
            "observation_kind": "native_response",
            "relation": "fork",
        },
        "thread/compact/start": {
            "transport": "codex_app_server_jsonrpc",
            "request_seams": {
                "thread/compact/start",
                "thread/compact/start notifications",
            },
            "observations": {
                "thread/compact/start",
                "item/started",
                "item/completed",
            },
            "observation_kind": None,
            "relation": "same_session",
        },
    },
    "opencode": {
        "cli.run": {
            "transport": "opencode_cli",
            "request_seams": {"opencode run --format json"},
            "observations": {"cli.run.json"},
            "observation_kind": "cli_json_record",
            "relation": "new",
        },
        "cli.run.session": {
            "transport": "opencode_cli",
            "request_seams": {"opencode run --format json"},
            "observations": {"cli.run.json"},
            "observation_kind": "cli_json_record",
            "relation": "resume",
        },
        "cli.run.fork": {
            "transport": "opencode_cli",
            "request_seams": {"opencode run --format json"},
            "observations": {"cli.run.json"},
            "observation_kind": "cli_json_record",
            "relation": "fork",
        },
        "session.get": {
            "transport": "opencode_loopback_http",
            "request_seams": {"GET session/:sessionID"},
            "observations": {"session.get"},
            "observation_kind": "native_response",
            "relation": "same_session",
        },
        "session.summarize": {
            "transport": "opencode_loopback_http",
            "request_seams": {"POST session/:sessionID/summarize"},
            "observations": {"session.summarize"},
            "observation_kind": "native_response",
            "relation": "same_session",
        },
        "session.messages": {
            "transport": "opencode_loopback_http",
            "request_seams": {"GET session/:sessionID/message"},
            "observations": {"session.messages"},
            "observation_kind": "native_response",
            "relation": "same_session",
        },
    },
}
_TURN_METHODS = {
    "codex": {
        "cold_start": ("thread/start",),
        "resume_fork": ("thread/start", "thread/resume", "thread/fork"),
        "compaction_forget": (
            "thread/start",
            "thread/compact/start",
            "thread/compact/start",
        ),
        "development_diagnostic": (
            "thread/start",
            "thread/resume",
            "thread/fork",
            "thread/compact/start",
        ),
    },
    "opencode": {
        "cold_start": ("cli.run",),
        "resume_fork": ("cli.run", "cli.run.session", "cli.run.fork"),
        "compaction_forget": ("cli.run", "cli.run.session", "cli.run.session"),
        "development_diagnostic": (
            "cli.run",
            "cli.run.session",
            "cli.run.fork",
            "cli.run.session",
        ),
    },
}
_MUTATION_KINDS = {
    "codex": {
        "cold_start": ("seed_checkpoint",),
        "resume_fork": ("seed_checkpoint",),
        "compaction_forget": ("seed_checkpoint", "forget"),
        "development_diagnostic": ("seed_checkpoint",),
    },
    "opencode": {
        "cold_start": ("seed_checkpoint",),
        "resume_fork": ("seed_checkpoint",),
        "compaction_forget": ("seed_checkpoint", "forget"),
        "development_diagnostic": ("seed_checkpoint",),
    },
}


def native_lifecycle_requirements(host: str) -> dict[str, frozenset[str]]:
    """Return only the actual native observation vocabulary for one Host."""

    scenarios = _NATIVE_OBSERVATIONS.get(host)
    if scenarios is None:
        raise EvidenceValidationError("native lifecycle Host is unsupported")
    return {name: frozenset(value) for name, value in scenarios.items()}


_V1_SCENARIO_MATRIX = {
    "codex": _QUALIFICATION_SCENARIOS,
    "opencode": _QUALIFICATION_SCENARIOS,
}
_V1_CODEX_METHODS = {
    "cold_start": {"thread/start"},
    "resume_fork": {"thread/start", "thread/resume", "thread/fork"},
    "compaction_forget": {
        "thread/start",
        "thread/compact/start",
        "item/started",
        "item/completed",
    },
}
_V1_CODEX_TURN_METHODS = {
    "cold_start": ("thread/start",),
    "resume_fork": ("thread/start", "thread/resume", "thread/fork"),
    "compaction_forget": (
        "thread/start",
        "thread/compact/start",
        "thread/compact/start",
    ),
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
        "mutation_boundaries": [
            {
                "kind": boundary.get("kind"),
                "audit_head_before": boundary.get("audit_head_before"),
                "audit_head_after": boundary.get("audit_head_after"),
                "receipt_sha256": boundary.get("receipt_sha256"),
                "target_sha256": boundary.get("target_sha256"),
            }
            for boundary in run.get("mutation_boundaries", [])
            if isinstance(boundary, Mapping)
        ],
        "checks": {key: value for key, value in metrics.items() if key != "evidence_sha256"},
    }
    if "native_receipts" in run:
        payload["native_receipts"] = [
            {
                "requested_operation": receipt.get("requested_operation"),
                "methods_observed": receipt.get("methods_observed"),
                "sanitized_request": receipt.get("sanitized_request"),
                "sanitized_raw_observation": receipt.get(
                    "sanitized_raw_observation"
                ),
                "identity_lineage": receipt.get("identity_lineage"),
                "actual_provider_usage": receipt.get("actual_provider_usage"),
            }
            for receipt in run.get("native_receipts", [])
            if isinstance(receipt, Mapping)
        ]
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


def validate_historical_host_report_consistency_v1(report: Mapping[str, Any]) -> None:
    """Validate retained v1 bytes only; v1 is invalid for current qualification."""

    _validate_contract(
        "host-continuity-qualification.v1.schema.json",
        report,
        label="Host qualification report",
    )

    host = report.get("host")
    expected_scenarios = _V1_SCENARIO_MATRIX.get(host)
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
            expected_method_set = _V1_CODEX_METHODS[str(run["scenario"])]
            if (run_passed and set(methods) != expected_method_set) or (
                not run_passed
                and methods != ["not_applicable"]
                and not set(methods).issubset(expected_method_set)
            ):
                raise EvidenceValidationError("Codex run lifecycle method set is invalid")
        if host == "opencode":
            expected_method_set = _V1_CODEX_METHODS[str(run["scenario"])]
            if (run_passed and set(methods) != expected_method_set) or (
                not run_passed
                and methods != ["not_applicable"]
                and not set(methods).issubset(expected_method_set)
            ):
                raise EvidenceValidationError("OpenCode run lifecycle method set is invalid")
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
        expected_turn_methods = _V1_CODEX_TURN_METHODS[str(run["scenario"])]
        if run_passed and turn_methods != expected_turn_methods:
            raise EvidenceValidationError("Host turn lifecycle sequence is invalid")
        if not run_passed and turn_methods != ("not_applicable",) and (
            turn_methods != expected_turn_methods[: len(turn_methods)]
        ):
            raise EvidenceValidationError("failed Host turn lifecycle prefix is invalid")
        if run_passed and run.get("new_thread") is not True:
            raise EvidenceValidationError("qualification scenarios require distinct new tasks")
        if run_passed:
            thread_ids = [turn.get("thread_id_sha256") for turn in turns]
            turn_ids = [turn.get("turn_id_sha256") for turn in turns]
            if any(value is None for value in (*thread_ids, *turn_ids)):
                raise EvidenceValidationError("Host lifecycle identities are missing")
            if len(set(turn_ids)) != len(turn_ids):
                raise EvidenceValidationError("Host turn identities must be unique")
            if run["scenario"] == "resume_fork" and thread_ids[-1] == thread_ids[-2]:
                raise EvidenceValidationError("fork did not create a distinct thread")
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
                cache_write_input_tokens = usage.get("cache_write_input_tokens")
                output_tokens = usage.get("output_tokens")
                reasoning_output_tokens = usage.get("reasoning_output_tokens")
                total = usage.get("total_tokens")
                if (
                    isinstance(input_tokens, int)
                    and not isinstance(input_tokens, bool)
                    and isinstance(output_tokens, int)
                    and not isinstance(output_tokens, bool)
                    and host == "codex"
                    and total != input_tokens + output_tokens
                ):
                    raise EvidenceValidationError("provider token arithmetic is inconsistent")
                opencode_components = (
                    input_tokens,
                    cached_input_tokens,
                    cache_write_input_tokens,
                    output_tokens,
                    reasoning_output_tokens,
                )
                if (
                    host == "opencode"
                    and all(
                        isinstance(value, int) and not isinstance(value, bool)
                        for value in opencode_components
                    )
                    and total != sum(opencode_components)
                ):
                    raise EvidenceValidationError("OpenCode token arithmetic is inconsistent")
                if host == "codex" and (
                    isinstance(cached_input_tokens, int)
                    and not isinstance(cached_input_tokens, bool)
                    and isinstance(input_tokens, int)
                    and not isinstance(input_tokens, bool)
                    and cached_input_tokens > input_tokens
                ):
                    raise EvidenceValidationError("cached input tokens exceed input tokens")
                if host == "codex" and (
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
        if run_passed and host in {"codex", "opencode"}:
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
        # OpenCode now exercises the same three lifecycle families and outcome
        # gates as Codex.  Do not accept the historical projection/source/provider
        # smoke scenarios here.

    if host == "opencode":
        opencode_thread_ids = [
            run["turns"][0].get("thread_id_sha256")
            for run in runs
            if isinstance(run, Mapping)
            and run.get("status") == "passed"
            and isinstance(run.get("turns"), list)
            and run["turns"]
            and isinstance(run["turns"][0], Mapping)
        ]
        if len(set(opencode_thread_ids)) != len(opencode_thread_ids):
            raise EvidenceValidationError("OpenCode qualification tasks are not distinct")

    lifecycle = report.get("lifecycle")
    root_methods = lifecycle.get("methods_observed") if isinstance(lifecycle, Mapping) else None
    required_root = set().union(*_V1_CODEX_METHODS.values())
    observed_method_union = {
        method
        for run in runs
        for method in run.get("methods_observed", [])
        if isinstance(method, str)
    }
    if not isinstance(root_methods, list) or set(root_methods) != observed_method_union:
        raise EvidenceValidationError("root Host lifecycle does not match run evidence")
    if report.get("status") == "executed" and set(root_methods) != required_root:
        raise EvidenceValidationError("root Host lifecycle coverage is incomplete")
    allowed_root = required_root | (
        {"not_applicable"} if report.get("status") != "executed" else set()
    )
    if not set(root_methods).issubset(allowed_root):
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
                "cache_write_input_tokens",
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
        authentication = attestation.get("authentication")
        if (
            not isinstance(authentication, Mapping)
            or authentication.get("checked") is not True
            or not isinstance(authentication.get("raw_sha256"), str)
            or authentication.get("raw_bytes", 0) <= 0
        ):
            raise EvidenceValidationError("executed Host report lacks authentication proof")
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
        if host == "opencode":
            availability = attestation.get("availability")
            if not isinstance(availability, Mapping) or availability.get("status") != "available":
                raise EvidenceValidationError(
                    "executed OpenCode report lacks a successful model availability probe"
                )


def _validate_actual_usage(host: str, usage: Mapping[str, Any]) -> None:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    if any(
        not isinstance(usage.get(field), int)
        or isinstance(usage.get(field), bool)
        or usage[field] < 0
        for field in fields
    ):
        raise EvidenceValidationError("actual Provider token usage is missing")
    if host == "codex":
        if (
            usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
            or usage["cached_input_tokens"] > usage["input_tokens"]
            or usage["reasoning_output_tokens"] > usage["output_tokens"]
        ):
            raise EvidenceValidationError("Codex Provider token arithmetic is inconsistent")
    elif usage["total_tokens"] != sum(usage[field] for field in fields[:-1]):
        raise EvidenceValidationError("OpenCode Provider token arithmetic is inconsistent")


def _validate_native_lineage_sequence(
    host: str, receipts: Sequence[Mapping[str, Any]]
) -> None:
    """Require native identity continuity across each observed request seam."""

    active: str | None = None
    for receipt in receipts:
        requested = receipt.get("requested_operation")
        lineage = receipt.get("identity_lineage")
        if not isinstance(requested, str) or not isinstance(lineage, Mapping):
            raise EvidenceValidationError("native lineage receipt is invalid")
        current = lineage.get("current_sha256")
        parent = lineage.get("parent_sha256")
        root = lineage.get("root_sha256")
        if not isinstance(current, str) or not isinstance(root, str):
            raise EvidenceValidationError("native lineage digest is missing")
        if requested in {"thread/start", "cli.run"}:
            if active is not None or current != root or parent is not None:
                raise EvidenceValidationError("native new-session lineage is invalid")
            active = current
        elif requested in {"thread/resume", "cli.run.session"}:
            if active is None or current != active or parent != active:
                raise EvidenceValidationError("native resume lineage is invalid")
        elif requested in {"thread/fork", "cli.run.fork"}:
            if active is None or parent != active or current == active:
                raise EvidenceValidationError("native fork lineage is invalid")
            active = current
        elif requested == "session.get":
            if active is None or current != active:
                raise EvidenceValidationError("session.get did not bind the active session")
            if host == "opencode" and parent is not None:
                raise EvidenceValidationError(
                    "OpenCode 1.18.16 session.get claimed an unsupported parent lineage"
                )
        elif requested in {
            "thread/compact/start",
            "session.summarize",
            "session.messages",
        } and (active is None or current != active):
            raise EvidenceValidationError("native compaction lineage is invalid")


def validate_host_report_consistency(report: Mapping[str, Any]) -> None:
    """Validate the current v2 report without conflating Host vocabularies."""

    _validate_contract(
        "host-continuity-qualification.v2.schema.json",
        report,
        label="current Host receipt",
    )
    host = report.get("host")
    mode = report.get("execution_mode")
    if host not in {"codex", "opencode"} or mode not in {"qualification", "diagnostic"}:
        raise EvidenceValidationError("Host receipt mode or identity is invalid")
    binding = report.get("binding")
    contract_digests = (
        binding.get("contract_digests") if isinstance(binding, Mapping) else None
    )
    current_contract = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "host-continuity-qualification.v2.schema.json"
    )
    if (
        not isinstance(contract_digests, Mapping)
        or contract_digests.get("host-continuity-qualification.v2.schema.json")
        != _sha256(current_contract.read_bytes())
    ):
        raise EvidenceValidationError(
            "Host receipt is not bound to the current v2 contract bytes"
        )
    expected_scenarios = (
        _QUALIFICATION_SCENARIOS if mode == "qualification" else _DIAGNOSTIC_SCENARIOS
    )
    expected_evidence = (
        "qualification_holdout" if mode == "qualification" else "development_diagnostic"
    )
    if report.get("evidence_class") != expected_evidence:
        raise EvidenceValidationError("Host receipt evidence class is invalid")
    runs = report.get("runs")
    if not isinstance(runs, list) or len(runs) != len(expected_scenarios):
        raise EvidenceValidationError("Host receipt has an invalid scenario matrix")
    if tuple(run.get("scenario") for run in runs if isinstance(run, Mapping)) != (
        expected_scenarios
    ):
        raise EvidenceValidationError("Host receipt scenario order is invalid")
    if tuple(run.get("run_index") for run in runs if isinstance(run, Mapping)) != tuple(
        range(1, len(runs) + 1)
    ):
        raise EvidenceValidationError("Host receipt run indexes are invalid")

    tool_schema = report.get("tool_schema")
    if not isinstance(tool_schema, Mapping) or (
        tuple(tool_schema.get("operations", ())) != _EXPECTED_KNOWLEDGE_OPERATIONS
        or tool_schema.get("operation_count") != len(_EXPECTED_KNOWLEDGE_OPERATIONS)
    ):
        raise EvidenceValidationError("Host receipt tools/list measurement is invalid")
    attestation = report.get("host_attestation")
    security = report.get("security")
    if not isinstance(attestation, Mapping) or not isinstance(security, Mapping):
        raise EvidenceValidationError("Host attestation or security receipt is missing")
    expected_host = {
        "codex": ("codex", "gpt-5.6-luna", "max"),
        "opencode": ("opencode", "deepseek/deepseek-v4-flash", "max"),
    }[host]
    if tuple(
        attestation.get(field) for field in ("binary_name", "model", "reasoning_effort")
    ) != expected_host:
        raise EvidenceValidationError("Host attestation identity is invalid")

    passed_runs = 0
    provider_bytes = 0
    first_call_valid_runs = 0
    bounded_retry_runs = 0
    requested_union: set[str] = set()
    observed_union: set[str] = set()
    transports: set[str] = set()
    usage_fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    for run in runs:
        if not isinstance(run, Mapping):
            raise EvidenceValidationError("Host receipt run is invalid")
        scenario = str(run["scenario"])
        if run.get("task_family") != scenario:
            raise EvidenceValidationError("semantic task family is not explicit")
        run_passed = run.get("status") == "passed"
        if run_passed:
            passed_runs += 1
            if run.get("failure_codes"):
                raise EvidenceValidationError("passed Host run retained failure codes")
        elif not run.get("failure_codes"):
            raise EvidenceValidationError("failed Host run omitted its failure code")

        methods = run.get("methods_observed")
        receipts = run.get("native_receipts")
        if not isinstance(methods, list) or not isinstance(receipts, list):
            raise EvidenceValidationError("Host run omitted native lifecycle evidence")
        run_requested: set[str] = set()
        run_observed: set[str] = set()
        run_relations: set[str] = set()
        run_roots: set[str] = set()
        receipt_sequence: list[tuple[str, str]] = []
        native_usage_totals = {field: 0 for field in usage_fields}
        for receipt in receipts:
            if not isinstance(receipt, Mapping):
                raise EvidenceValidationError("native lifecycle receipt is invalid")
            if receipt.get("semantic_task_family") != scenario:
                raise EvidenceValidationError("native receipt changed semantic task family")
            transport = receipt.get("transport")
            allowed_transports = (
                {"codex_app_server_jsonrpc"}
                if host == "codex"
                else {"opencode_cli", "opencode_loopback_http"}
            )
            if transport not in allowed_transports:
                raise EvidenceValidationError("native receipt used another Host transport")
            transports.add(str(transport))
            requested = receipt.get("requested_operation")
            observed = receipt.get("methods_observed")
            if not isinstance(requested, str) or not isinstance(observed, list):
                raise EvidenceValidationError("native request or observation is invalid")
            rule = _NATIVE_RECEIPT_RULES[host].get(requested)
            if (
                rule is None
                or len(observed) != 1
                or observed[0] not in rule["observations"]
                or transport != rule["transport"]
                or receipt.get("request_seam") not in rule["request_seams"]
                or receipt.get("identity_lineage", {}).get("relation")
                != rule["relation"]
            ):
                raise EvidenceValidationError(
                    "native receipt request and observation are not correlated"
                )
            expected_kind = rule["observation_kind"]
            observed_kind = receipt.get("observation_kind")
            if requested == "thread/compact/start":
                expected_kind = (
                    "native_response"
                    if observed[0] == "thread/compact/start"
                    else "native_event"
                )
            if observed_kind != expected_kind:
                raise EvidenceValidationError("native observation kind is invalid")
            run_requested.add(requested)
            run_observed.update(str(item) for item in observed)
            receipt_sequence.append((requested, str(observed[0])))
            usage = receipt.get("actual_provider_usage")
            lineage = receipt.get("identity_lineage")
            if not isinstance(lineage, Mapping):
                raise EvidenceValidationError("native receipt omitted identity lineage")
            usage_required = (
                host == "codex"
                and requested in {"thread/start", "thread/resume", "thread/fork"}
            ) or (
                host == "opencode"
                and (requested.startswith("cli.run") or requested == "session.messages")
            )
            if usage_required:
                if not isinstance(usage, Mapping):
                    raise EvidenceValidationError(
                        "usage-bearing native observation omitted Provider accounting"
                    )
                _validate_actual_usage(host, usage)
                for field in usage_fields:
                    native_usage_totals[field] += int(usage[field])
            elif usage is not None:
                raise EvidenceValidationError(
                    "non-usage native observation claimed Provider accounting"
                )
            run_relations.add(str(lineage.get("relation")))
            root_sha256 = lineage.get("root_sha256")
            if isinstance(root_sha256, str):
                run_roots.add(root_sha256)
        expected_receipt_sequence = _NATIVE_RECEIPT_SEQUENCE[host][scenario]
        if run_passed and tuple(receipt_sequence) != expected_receipt_sequence:
            raise EvidenceValidationError("passed Host run lacks exact native receipt sequence")
        if not run_passed and tuple(receipt_sequence) != expected_receipt_sequence[
            : len(receipt_sequence)
        ]:
            raise EvidenceValidationError("failed Host run claims a non-native receipt prefix")
        expected_requests = _NATIVE_REQUESTS[host][scenario]
        expected_observations = _NATIVE_OBSERVATIONS[host][scenario]
        if run_passed and (
            run_requested != expected_requests or run_observed != expected_observations
        ):
            raise EvidenceValidationError("passed Host run lacks exact native lifecycle coverage")
        if not run_passed and (
            not run_requested.issubset(expected_requests)
            or not run_observed.issubset(expected_observations)
        ):
            raise EvidenceValidationError("failed Host run claims an unexpected native operation")
        if set(methods) != run_observed:
            raise EvidenceValidationError("methods_observed is not an actual observation union")
        if len(run_roots) > 1:
            raise EvidenceValidationError("native session lineage has multiple roots")
        if run_passed:
            _validate_native_lineage_sequence(host, receipts)
        required_relations = {
            "cold_start": {"new", "same_session"},
            "resume_fork": {"new", "resume", "fork", "same_session"},
            "compaction_forget": {"new", "resume", "same_session"},
            "development_diagnostic": {"new", "resume", "fork", "same_session"},
        }[scenario]
        if run_passed and not ({"new"} <= run_relations <= required_relations):
            raise EvidenceValidationError("native session lineage relation is invalid")
        if run_passed and scenario in {
            "resume_fork",
            "development_diagnostic",
        } and not {"resume", "fork"}.issubset(run_relations):
            raise EvidenceValidationError("resume/fork lineage is incomplete")

        turns = run.get("turns")
        if not isinstance(turns, list) or not turns:
            raise EvidenceValidationError("Host run omitted turn evidence")
        turn_statuses = [turn.get("status") for turn in turns if isinstance(turn, Mapping)]
        if run_passed and (len(turn_statuses) != len(turns) or set(turn_statuses) != {"passed"}):
            raise EvidenceValidationError("passed Host run contains a failed turn")
        if not run_passed and "failed" not in turn_statuses:
            raise EvidenceValidationError("failed Host run lacks a failed turn")
        turn_methods = tuple(
            turn.get("lifecycle_method") for turn in turns if isinstance(turn, Mapping)
        )
        expected_turn_methods = _TURN_METHODS[host][scenario]
        if run_passed and turn_methods != expected_turn_methods:
            raise EvidenceValidationError("Host turn request sequence is invalid")
        if not run_passed and turn_methods != ("not_applicable",) and (
            turn_methods != expected_turn_methods[: len(turn_methods)]
        ):
            raise EvidenceValidationError("failed Host turn request prefix is invalid")
        if run_passed and run.get("new_thread") is not True:
            raise EvidenceValidationError("Host run did not begin with a new native session")
        turn_thread_ids = {
            turn.get("thread_id_sha256")
            for turn in turns
            if isinstance(turn, Mapping) and isinstance(turn.get("thread_id_sha256"), str)
        }
        receipt_thread_ids = {
            receipt.get("identity_lineage", {}).get("current_sha256")
            for receipt in receipts
            if isinstance(receipt, Mapping)
            and isinstance(receipt.get("identity_lineage"), Mapping)
        }
        if run_passed and not receipt_thread_ids.issubset(turn_thread_ids):
            raise EvidenceValidationError("native receipt lineage is not bound to turn identity")
        first_read: Mapping[str, Any] | None = None
        retried = False
        turn_usage_totals = {field: 0 for field in usage_fields}
        for turn in turns:
            if not isinstance(turn, Mapping):
                raise EvidenceValidationError("Host turn evidence is invalid")
            before = turn.get("ledger_audit_head_before")
            after = turn.get("ledger_audit_head_after")
            unchanged = turn.get("ledger_unchanged")
            if unchanged is not (before == after):
                raise EvidenceValidationError("turn ledger unchanged flag is inconsistent")
            if turn.get("status") == "passed" and unchanged is not True:
                raise EvidenceValidationError("passed read-only turn changed the Ledger")
            safe_read = turn.get("safe_read")
            if not isinstance(safe_read, Mapping):
                raise EvidenceValidationError("Host turn omitted safe-read evidence")
            count = safe_read.get("call_count")
            operations = safe_read.get("safe_read_operations")
            payloads = safe_read.get("provider_payloads")
            if not isinstance(operations, list) or not isinstance(payloads, list):
                raise EvidenceValidationError("safe-read arrays are invalid")
            if count != len(operations) or count != len(payloads):
                raise EvidenceValidationError("safe-read call count is inconsistent")
            if turn.get("status") == "passed" and count not in {1, 2}:
                raise EvidenceValidationError("passed turn requires one or two safe reads")
            if safe_read.get("bounded_retry_used") is not (count == 2):
                raise EvidenceValidationError("bounded retry flag is inconsistent")
            if turn.get("status") == "passed" and safe_read.get("first_call_valid") is not True:
                raise EvidenceValidationError("passed turn lacks first-call validity")
            if first_read is None:
                first_read = safe_read
            retried = retried or count == 2
            for payload in payloads:
                if not isinstance(payload, Mapping):
                    raise EvidenceValidationError("Provider payload measurement is invalid")
                context_chars = payload.get("context_chars")
                relevant_chars = payload.get("relevant_chars")
                ratio = payload.get("relevant_chars_context_chars")
                evidence_count = payload.get("evidence_count")
                duplicate_evidence_count = payload.get("duplicate_evidence_count")
                duplicate_evidence_rate = payload.get("duplicate_evidence_rate")
                if (
                    not isinstance(context_chars, int)
                    or not isinstance(relevant_chars, int)
                    or relevant_chars > context_chars
                    or ratio
                    != (relevant_chars / context_chars if context_chars else None)
                    or not isinstance(evidence_count, int)
                    or not isinstance(duplicate_evidence_count, int)
                    or duplicate_evidence_count > evidence_count
                    or duplicate_evidence_rate
                    != (
                        duplicate_evidence_count / evidence_count
                        if evidence_count
                        else None
                    )
                ):
                    raise EvidenceValidationError(
                        "Provider character or duplicate-evidence accounting is inconsistent"
                    )
                provider_bytes += int(payload.get("provider_bytes", 0))
            usage = turn.get("usage")
            if turn.get("status") == "passed":
                if not isinstance(usage, Mapping):
                    raise EvidenceValidationError("passed turn omitted Provider usage")
                _validate_actual_usage(host, usage)
                for field in usage_fields:
                    turn_usage_totals[field] += int(usage[field])
        if host == "opencode" and run_passed and native_usage_totals != turn_usage_totals:
            raise EvidenceValidationError(
                "OpenCode native Provider usage does not reconcile with turn evidence"
            )
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
        expected_kinds = _MUTATION_KINDS[host][scenario]
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

        metrics = run.get("metrics")
        if not isinstance(metrics, Mapping) or metrics.get("evidence_sha256") != _metric_evidence(
            run
        ):
            raise EvidenceValidationError("scenario metrics are not bound to receipt evidence")
        metric_fields = (
            "first_correct_action",
            "decision_preservation",
            "wrong_state_admission",
            "stale_state_rejected",
            "forgotten_state_admission",
            "gap_observed",
            "projection_state_correct",
            "retention_wording_correct",
            "provider_boundary_correct",
        )
        if mode == "diagnostic":
            if any(metrics.get(field) is not None for field in metric_fields):
                raise EvidenceValidationError("diagnostic contains qualification scoring")
        elif run_passed:
            required_common = {
                "first_correct_action": True,
                "wrong_state_admission": 0,
                "stale_state_rejected": True,
                "provider_boundary_correct": True,
            }
            if any(metrics.get(field) != value for field, value in required_common.items()):
                raise EvidenceValidationError("passed qualification metric is not satisfied")
            if scenario == "resume_fork" and metrics.get("decision_preservation") is not True:
                raise EvidenceValidationError("resume/fork decision was not preserved")
            if scenario == "compaction_forget" and (
                metrics.get("forgotten_state_admission") != 0
                or metrics.get("gap_observed") is not True
            ):
                raise EvidenceValidationError("compaction/forget admission is invalid")
        requested_union.update(run_requested)
        observed_union.update(run_observed)

    lifecycle = report.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        raise EvidenceValidationError("root native lifecycle receipt is missing")
    if set(lifecycle.get("common_task_families", [])) != set(expected_scenarios):
        raise EvidenceValidationError("root semantic task families are inconsistent")
    if set(lifecycle.get("transport_seams", [])) != transports:
        raise EvidenceValidationError("root transport seams are inconsistent")
    if set(lifecycle.get("requested_operations", [])) != requested_union:
        raise EvidenceValidationError("root requested operations are inconsistent")
    if set(lifecycle.get("methods_observed", [])) != observed_union:
        raise EvidenceValidationError("root native observations are inconsistent")

    aggregate = report.get("aggregate")
    if not isinstance(aggregate, Mapping):
        raise EvidenceValidationError("Host aggregate is missing")
    expected_aggregate = {
        "passed_runs": passed_runs,
        "failed_runs": len(runs) - passed_runs,
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
                "cache_write_input_tokens",
                "output_tokens",
                "reasoning_output_tokens",
                "total_tokens",
            )
        },
    }
    if any(aggregate.get(field) != value for field, value in expected_aggregate.items()):
        raise EvidenceValidationError("Host aggregate does not match receipt evidence")
    expected_status = (
        "executed"
        if passed_runs == len(runs)
        else "failed"
        if passed_runs == 0
        else "partial"
    )
    if report.get("status") != expected_status:
        raise EvidenceValidationError("Host receipt status does not match its runs")
    expected_qualification_status = (
        "not_applicable"
        if mode == "diagnostic"
        else "passed"
        if expected_status == "executed"
        else "failed"
        if expected_status == "failed"
        else "partial"
    )
    if report.get("qualification_status") != expected_qualification_status:
        raise EvidenceValidationError("qualification status is inconsistent")
    if mode == "diagnostic" and not {"qualification", "Human Gold"}.issubset(
        set(report.get("not_executed", []))
    ):
        raise EvidenceValidationError("diagnostic did not exclude qualification and Human Gold")

    if report.get("status") == "executed":
        authentication = attestation.get("authentication")
        if (
            not isinstance(authentication, Mapping)
            or authentication.get("checked") is not True
            or not isinstance(authentication.get("raw_sha256"), str)
            or authentication.get("raw_bytes", 0) <= 0
        ):
            raise EvidenceValidationError("executed Host receipt lacks authentication proof")
        inventories = (attestation.get("model_inventory"), attestation.get("mcp_inventory"))
        if any(
            not isinstance(item, Mapping)
            or item.get("checked") is not True
            or item.get("selected_present") is not True
            for item in inventories
        ):
            raise EvidenceValidationError("executed Host receipt lacks inventory proof")
        required_security = {
            "mcp_child_closed_environment": True,
            "only_knowledge_support_enabled": True,
            "absolute_path_leak": False,
            "secret_leak": False,
            "cleanup_complete": True,
        }
        if any(security.get(field) != value for field, value in required_security.items()):
            raise EvidenceValidationError("executed Host receipt failed a security boundary")
        if host == "opencode":
            availability = attestation.get("availability")
            if not isinstance(availability, Mapping) or availability.get("status") != "available":
                raise EvidenceValidationError(
                    "executed OpenCode receipt lacks a successful availability probe"
                )
