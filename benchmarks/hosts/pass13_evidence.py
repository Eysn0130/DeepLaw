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
from pathlib import Path
from typing import Any

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
    }


def analyze_safe_read_calls(
    observations: Sequence[Mapping[str, Any]],
    tool_outputs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one first read plus at most one safe bounded retry."""

    if len(observations) not in {1, 2} or len(tool_outputs) != len(observations):
        raise EvidenceValidationError("qualification requires one or two safe read calls")
    operations: list[str] = []
    payloads: list[dict[str, Any]] = []
    for observation, tool_output in zip(observations, tool_outputs, strict=True):
        if not isinstance(observation, Mapping) or not isinstance(tool_output, Mapping):
            raise EvidenceValidationError("safe read observation is invalid")
        operation, payload = _analyze_call(observation, tool_output)
        operations.append(operation)
        payloads.append(payload)
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
    for value in forbidden_values:
        if isinstance(value, str) and value and value.encode("utf-8") in data:
            raise EvidenceValidationError("artifact contains a forbidden value")


def write_retained_artifact(
    path: Path,
    data: bytes,
    *,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Scan one in-memory artifact before creating its retained file."""

    if not isinstance(path, Path) or path.name != str(path.name) or not path.name:
        raise EvidenceValidationError("retained artifact path is invalid")
    if not isinstance(data, bytes) or not data or len(data) > 8 * 1024 * 1024:
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
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a path-free SHA manifest after scanning every retained artifact."""

    if host not in {"codex", "opencode"}:
        raise EvidenceValidationError("bundle host is unsupported")
    if _GIT_OID.fullmatch(commit) is None or _GIT_OID.fullmatch(tree) is None:
        raise EvidenceValidationError("bundle Git binding is invalid")
    if not artifacts or len(artifacts) > 64:
        raise EvidenceValidationError("bundle artifact inventory is invalid")
    rows: list[dict[str, Any]] = []
    names: set[str] = set()
    for role, path in sorted(artifacts.items()):
        if not isinstance(role, str) or not role or len(role) > 100:
            raise EvidenceValidationError("bundle artifact role is invalid")
        if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
            raise EvidenceValidationError("bundle artifact must be one regular file")
        name = path.name
        if name in names or Path(name).name != name:
            raise EvidenceValidationError("bundle artifact name is invalid or duplicated")
        names.add(name)
        data = path.read_bytes()
        _scan_artifact(data, forbidden_values=forbidden_values)
        rows.append(
            {
                "role": role,
                "name": name,
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        )
    rows.sort(key=lambda row: row["name"])
    return {
        "schema_version": "deeplaw.host-qualification-bundle-manifest/v1",
        "host": host,
        "commit": commit,
        "tree": tree,
        "artifacts": rows,
    }
