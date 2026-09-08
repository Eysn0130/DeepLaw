"""Read-only task-domain driver for the frozen v0.13 Wiki/evidence tasks.

This module is a collector seam, not a Host harness. A collector supplies a
frozen seed and an already prepared Knowledge Vault; this driver only performs
bounded source, Wiki, and public v7 knowledge_support reads. It never creates
task evidence, invokes a model, or writes a Ledger.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from benchmarks.hosts.run_v013_host_task_qualification import load_task_cases
from deeplaw.closed_mcp_launcher import closed_mcp_environment
from deeplaw.host_runtime import build_closed_mcp_argv, observed_knowledge_vault_id
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, _validate_contract
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.read_services import SourceReadService, WikiReadService
from deeplaw.util import canonical_json, sha256_bytes, sha256_file, strict_json_loads

SCHEMA_VERSION = "deeplaw.v013-task-domain-driver/v1"
SEED_SCHEMA_VERSION = "deeplaw.v013-task-domain-seed/v1"
DRIVER_KIND = "task_domain_driver"
TASK_CASES = ("living_wiki", "professional_evidence")
_IDENTITIES = {
    "knowledge_id": re.compile(r"^knowledge_[0-9a-f]{24}$"),
    "knowledge_revision_id": re.compile(r"^knowledgerev_[0-9a-f]{24}$"),
    "source_id": re.compile(r"^source_[0-9a-f]{24}$"),
    "source_revision_id": re.compile(r"^sourcerev_[0-9a-f]{24}$"),
    "fragment_id": re.compile(r"^fragment_[0-9a-f]{24}$"),
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_DUTIES = frozenset(
    {
        "primary_answer",
        "identity",
        "definition",
        "current_state",
        "temporal_freshness",
        "procedure",
        "exception",
        "contradiction",
        "applicability",
        "limitation",
        "source_evidence",
        "unresolved_gap",
    }
)
_FORBIDDEN_TEXT = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|secret)\s*[:=]|"
    r"(?:^|[\s=])/(?:Users|home|root|tmp|private|var|etc|opt|workspace|Volumes)(?:[\s/]|$)|"
    r"[A-Za-z]:[\\/]"
)
_MAX_TASK_BYTES = 5_000
_MAX_SEED_BYTES = 64 * 1024
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_CALLER = "task_domain_driver"
_V6_AUTHORITY_BOUNDARY = {
    "legal_authority": False,
    "official_legal_sources_tool": "law_support",
    "persistent_writes": "separate_explicit_knowledge_sink",
    "case_data_allowed": False,
    "authority_from_ranking": False,
}


class TaskDomainDriverError(ValueError):
    """A frozen seed, read observation, or public v7 response was rejected."""


def _text(value: Any, *, field: str, maximum: int = 500) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
        or _FORBIDDEN_TEXT.search(value)
    ):
        raise TaskDomainDriverError(f"{field} is invalid")
    return value


def _identity(value: Any, *, field: str) -> str:
    selected = _text(value, field=field, maximum=80)
    pattern = _IDENTITIES.get(field)
    if pattern is None or pattern.fullmatch(selected) is None:
        raise TaskDomainDriverError(f"{field} identity is invalid")
    return selected


def _sha(value: Any, *, field: str) -> str:
    selected = _text(value, field=field, maximum=64)
    if _SHA256.fullmatch(selected) is None or selected == "0" * 64:
        raise TaskDomainDriverError(f"{field} digest is invalid")
    return selected


def _json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def _io_record(
    *,
    operation: str,
    action: str,
    request: Mapping[str, Any],
    response: Mapping[str, Any],
    structured_response: Mapping[str, Any] | None = None,
    provider_content: tuple[int, str] | None = None,
) -> dict[str, Any]:
    request_bytes = _json_bytes(request)
    projection_bytes = _json_bytes(response)
    if len(projection_bytes) > 24_576:
        raise TaskDomainDriverError("bounded read response exceeds its observation limit")
    record = {
        "caller": _CALLER,
        "operation": operation,
        "action": action,
        "request": json.loads(request_bytes),
        "request_byte_size": len(request_bytes),
        "request_sha256": sha256_bytes(request_bytes),
        "response_kind": "bounded_projection",
        "projection": json.loads(projection_bytes),
        "projection_byte_size": len(projection_bytes),
        "projection_sha256": sha256_bytes(projection_bytes),
    }
    if structured_response is not None:
        structured_bytes = _json_bytes(structured_response)
        record.update(
            {
                "structured_response_kind": "canonical_json",
                "structured_response_byte_size": len(structured_bytes),
                "structured_response_sha256": sha256_bytes(structured_bytes),
            }
        )
    if provider_content is not None:
        provider_byte_size, provider_digest = provider_content
        record.update(
            {
                "provider_content_kind": "mcp_text_content",
                "provider_content_bytes": provider_byte_size,
                "provider_content_sha256": provider_digest,
            }
        )
    return record


def _frozen_case(task_case: str) -> dict[str, Any]:
    if task_case not in TASK_CASES:
        raise TaskDomainDriverError("task case is not a frozen source-backed task")
    catalog = load_task_cases()
    row = next(
        (item for item in catalog["task_cases"] if item["task_case"] == task_case),
        None,
    )
    if not isinstance(row, Mapping):
        raise TaskDomainDriverError("frozen task case is unavailable")
    return {
        "required_duties": list(row["required_duties"]),
        "required_wrong_states": list(row["required_wrong_states"]),
        "required_operations": list(row["required_operations"]),
    }


def build_task_seed(
    task_case: str,
    *,
    task: str,
    knowledge_id: str,
    knowledge_revision_id: str,
    source_id: str,
    source_revision_id: str,
    fragment_id: str,
    locator: str,
    quote_sha256: str,
    content_sha256: str,
    expected_exclude: Mapping[str, Sequence[str]] | None = None,
    expected_gaps: Sequence[Mapping[str, str]] = (),
    public_duties: Sequence[str] = ("source_evidence", "limitation"),
    scope: str = "project",
    max_sensitivity: str = "private",
) -> dict[str, Any]:
    """Build one closed, pre-execution source-bound seed."""

    frozen = _frozen_case(task_case)
    task = _text(task, field="task", maximum=_MAX_TASK_BYTES)
    if scope not in {"personal", "project", "domain"}:
        raise TaskDomainDriverError("task scope is invalid")
    if max_sensitivity not in {"public", "internal", "private"}:
        raise TaskDomainDriverError("task sensitivity is invalid")
    selected_duties = [_text(item, field="public duty", maximum=40) for item in public_duties]
    if (
        not selected_duties
        or len(selected_duties) != len(set(selected_duties))
        or any(item not in _PUBLIC_DUTIES for item in selected_duties)
        or "source_evidence" not in selected_duties
    ):
        raise TaskDomainDriverError("public duty set is invalid")
    excluded = expected_exclude or {"knowledge_ids": (), "source_revision_ids": ()}
    if set(excluded) != {"knowledge_ids", "source_revision_ids"}:
        raise TaskDomainDriverError("expected exclusion set is invalid")
    excluded_ids = {
        "knowledge_ids": [
            _identity(value, field="knowledge_id") for value in excluded["knowledge_ids"]
        ],
        "source_revision_ids": [
            _identity(value, field="source_revision_id")
            for value in excluded["source_revision_ids"]
        ],
    }
    for values in excluded_ids.values():
        if len(values) != len(set(values)):
            raise TaskDomainDriverError("expected exclusion set contains duplicates")
    included = {
        "knowledge_id": _identity(knowledge_id, field="knowledge_id"),
        "knowledge_revision_id": _identity(
            knowledge_revision_id, field="knowledge_revision_id"
        ),
        "source_id": _identity(source_id, field="source_id"),
        "source_revision_id": _identity(source_revision_id, field="source_revision_id"),
        "fragment_id": _identity(fragment_id, field="fragment_id"),
        "locator": _text(locator, field="locator", maximum=500),
        "quote_sha256": _sha(quote_sha256, field="quote_sha256"),
        "content_sha256": _sha(content_sha256, field="content_sha256"),
        "authority": "agent_derived",
        "legal_authority": False,
        "verification": "source_bound",
    }
    if included["knowledge_id"] in excluded_ids["knowledge_ids"] or included[
        "source_revision_id"
    ] in excluded_ids["source_revision_ids"]:
        raise TaskDomainDriverError("included identity is also excluded")
    gaps: list[dict[str, str]] = []
    for item in expected_gaps:
        if not isinstance(item, Mapping) or set(item) != {"code", "duty"}:
            raise TaskDomainDriverError("expected Gap is invalid")
        code = _text(item["code"], field="Gap code", maximum=80)
        duty = _text(item["duty"], field="Gap duty", maximum=40)
        if duty not in _PUBLIC_DUTIES:
            raise TaskDomainDriverError("Gap duty is invalid")
        gaps.append({"code": code, "duty": duty})
    if len({(item["code"], item["duty"]) for item in gaps}) != len(gaps):
        raise TaskDomainDriverError("expected Gap contains duplicates")
    value = {
        "schema_version": SEED_SCHEMA_VERSION,
        "status": "frozen_task_domain_seed",
        "formal_admission": False,
        "claim_eligible": False,
        "driver_kind": DRIVER_KIND,
        "task_case": task_case,
        "task": task,
        "scope": scope,
        "max_sensitivity": max_sensitivity,
        "public_duties": selected_duties,
        "source_policy": {"scope": scope, "sensitivity": max_sensitivity},
        "expected": {
            "include": included,
            "exclude": excluded_ids,
            "duties": frozen["required_duties"],
            "wrong_states": frozen["required_wrong_states"],
            "operations": frozen["required_operations"],
            "gaps": gaps,
        },
    }
    return validate_task_seed(value)


def validate_task_seed(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an external frozen seed before opening any read seam."""

    if not isinstance(value, Mapping):
        raise TaskDomainDriverError("task seed is not an object")
    required = {
        "schema_version", "status", "formal_admission", "claim_eligible", "driver_kind",
        "task_case", "task", "scope", "max_sensitivity", "public_duties",
        "source_policy", "expected",
    }
    if set(value) != required:
        raise TaskDomainDriverError("task seed contract is not closed")
    if (
        value["schema_version"] != SEED_SCHEMA_VERSION
        or value["status"] != "frozen_task_domain_seed"
        or value["formal_admission"] is not False
        or value["claim_eligible"] is not False
        or value["driver_kind"] != DRIVER_KIND
    ):
        raise TaskDomainDriverError("task seed status is invalid")
    task_case = value["task_case"]
    frozen = _frozen_case(task_case)
    _text(value["task"], field="task", maximum=_MAX_TASK_BYTES)
    scope = value["scope"]
    sensitivity = value["max_sensitivity"]
    if scope not in {"personal", "project", "domain"}:
        raise TaskDomainDriverError("task scope is invalid")
    if sensitivity not in {"public", "internal", "private"}:
        raise TaskDomainDriverError("task sensitivity is invalid")
    duties = value["public_duties"]
    if (
        not isinstance(duties, list)
        or not duties
        or any(not isinstance(item, str) for item in duties)
        or len(duties) != len(set(duties))
        or any(item not in _PUBLIC_DUTIES for item in duties)
        or "source_evidence" not in duties
    ):
        raise TaskDomainDriverError("public duty set is invalid")
    policy = value["source_policy"]
    if (
        not isinstance(policy, Mapping)
        or set(policy) != {"scope", "sensitivity"}
        or policy["scope"] != scope
        or policy["sensitivity"] != sensitivity
    ):
        raise TaskDomainDriverError("source admission policy is invalid")
    expected = value["expected"]
    if not isinstance(expected, Mapping) or set(expected) != {
        "include", "exclude", "duties", "wrong_states", "operations", "gaps"
    }:
        raise TaskDomainDriverError("task expectations are not closed")
    if expected["duties"] != frozen["required_duties"]:
        raise TaskDomainDriverError("task duties differ from the frozen catalog")
    if expected["wrong_states"] != frozen["required_wrong_states"]:
        raise TaskDomainDriverError("task wrong states differ from the frozen catalog")
    if expected["operations"] != frozen["required_operations"]:
        raise TaskDomainDriverError("task operations differ from the frozen catalog")
    include = expected["include"]
    if not isinstance(include, Mapping) or set(include) != {
        "knowledge_id", "knowledge_revision_id", "source_id", "source_revision_id",
        "fragment_id", "locator", "quote_sha256", "content_sha256", "authority",
        "legal_authority", "verification",
    }:
        raise TaskDomainDriverError("expected identity binding is not closed")
    for field in (
        "knowledge_id", "knowledge_revision_id", "source_id",
        "source_revision_id", "fragment_id",
    ):
        _identity(include[field], field=field)
    _text(include["locator"], field="locator", maximum=500)
    _sha(include["quote_sha256"], field="quote_sha256")
    _sha(include["content_sha256"], field="content_sha256")
    if (
        include["authority"] != "agent_derived"
        or include["legal_authority"] is not False
        or include["verification"] != "source_bound"
    ):
        raise TaskDomainDriverError("source-bound authority contract is invalid")
    exclude = expected["exclude"]
    if not isinstance(exclude, Mapping) or set(exclude) != {
        "knowledge_ids", "source_revision_ids"
    }:
        raise TaskDomainDriverError("expected exclusion set is invalid")
    for field, identity_field in (
        ("knowledge_ids", "knowledge_id"), ("source_revision_ids", "source_revision_id")
    ):
        if not isinstance(exclude[field], list):
            raise TaskDomainDriverError("expected exclusion set is invalid")
        for item in exclude[field]:
            _identity(item, field=identity_field)
        if len(exclude[field]) != len(set(exclude[field])):
            raise TaskDomainDriverError("expected exclusion set contains duplicates")
    if include["knowledge_id"] in exclude["knowledge_ids"] or include[
        "source_revision_id"
    ] in exclude["source_revision_ids"]:
        raise TaskDomainDriverError("included identity is also excluded")
    gaps = expected["gaps"]
    if not isinstance(gaps, list):
        raise TaskDomainDriverError("expected Gap set is invalid")
    for item in gaps:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"code", "duty"}
        ):
            raise TaskDomainDriverError("expected Gap is invalid")
        _text(item["code"], field="Gap code", maximum=80)
        duty = _text(item["duty"], field="Gap duty", maximum=40)
        if duty not in _PUBLIC_DUTIES:
            raise TaskDomainDriverError("expected Gap duty is invalid")
    if len({(item["code"], item["duty"]) for item in gaps}) != len(gaps):
        raise TaskDomainDriverError("expected Gap contains duplicates")
    return json.loads(canonical_json(dict(value)))


def _read_identity(vault: Path) -> dict[str, str]:
    try:
        with AutonomousKnowledgeStore(vault, read_only=True) as store:
            if not store.verify().get("valid"):
                raise TaskDomainDriverError("Knowledge Vault integrity is invalid")
            return {
                "audit_head": store.audit_head,
                "legacy_audit_head": store.legacy_audit_head,
            }
    except TaskDomainDriverError:
        raise
    except Exception as error:
        raise TaskDomainDriverError("Knowledge Vault read identity is unavailable") from error


def _source_observation(seed: Mapping[str, Any], vault: Path) -> dict[str, Any]:
    include = seed["expected"]["include"]
    get_request = {
        "action": "get",
        "source_id": include["source_id"],
        "scope": seed["scope"],
        "max_sensitivity": seed["max_sensitivity"],
    }
    fragment_request = {
        "action": "fragment",
        "fragment_id": include["fragment_id"],
        "scope": seed["scope"],
        "max_sensitivity": seed["max_sensitivity"],
        "max_chars": 12_000,
    }
    try:
        service = SourceReadService(vault)
        card = service.execute(
            **get_request,
        )
        fragment_result = service.execute(
            **fragment_request,
        )
        with KnowledgeVault(vault, read_only=True) as store:
            source = store.source_info(include["source_id"])
            source_path = store.source_file_path(include["source_id"])
            if source_path.is_symlink() or not source_path.is_file():
                raise TaskDomainDriverError("source bytes are not a regular file")
            source_size = source_path.stat().st_size
            if source_size > _MAX_SOURCE_BYTES:
                raise TaskDomainDriverError("source bytes exceed the read bound")
            source_digest = sha256_file(source_path)
    except TaskDomainDriverError:
        raise
    except Exception as error:
        raise TaskDomainDriverError("source read was unavailable or not admitted") from error
    selected = card.get("source")
    fragment = fragment_result.get("fragment")
    if not isinstance(selected, Mapping) or not isinstance(fragment, Mapping):
        raise TaskDomainDriverError("source read shape is invalid")
    checks = {
        "source_id": selected.get("source_id") == include["source_id"],
        "source_revision_id": selected.get("source_revision_id") == include["source_revision_id"],
        "content_sha256": selected.get("content_sha256") == include["content_sha256"],
        "original_bytes_sha256": source_digest == include["content_sha256"],
        "byte_size": selected.get("byte_size") == source_size == source["byte_size"],
        "fragment_id": fragment.get("fragment_id") == include["fragment_id"],
        "fragment_source_revision_id": (
            fragment.get("source_revision_id") == include["source_revision_id"]
        ),
        "locator": fragment.get("locator") == include["locator"],
        "quote_sha256": fragment.get("text_sha256") == include["quote_sha256"],
        "fragment_text_sha256": (
            isinstance(fragment.get("text"), str)
            and sha256_bytes(fragment["text"].encode("utf-8")) == include["quote_sha256"]
        ),
        "fragment_complete": fragment.get("content_truncated") is False,
        "source_write_performed": card.get("write_performed") is False,
        "fragment_write_performed": fragment_result.get("write_performed") is False,
    }
    if not all(checks.values()):
        raise TaskDomainDriverError("source read does not match the frozen identity")
    get_response = {
        "source": {
            "source_id": selected["source_id"],
            "source_revision_id": selected["source_revision_id"],
            "content_sha256": selected["content_sha256"],
            "byte_size": selected["byte_size"],
        },
        "write_performed": False,
    }
    fragment_response = {
        "fragment": {
            "fragment_id": fragment["fragment_id"],
            "source_revision_id": fragment["source_revision_id"],
            "locator": fragment["locator"],
            "text_sha256": fragment["text_sha256"],
            "content_truncated": False,
        },
        "write_performed": False,
    }
    return {
        "caller": _CALLER,
        "source_id": include["source_id"],
        "source_revision_id": include["source_revision_id"],
        "content_sha256": include["content_sha256"],
        "byte_size": source_size,
        "fragment_id": include["fragment_id"],
        "locator": include["locator"],
        "quote_sha256": include["quote_sha256"],
        "checks": checks,
        "calls": [
            _io_record(
                operation="SourceReadService",
                action="get",
                request=get_request,
                response=get_response,
            ),
            _io_record(
                operation="SourceReadService",
                action="fragment",
                request=fragment_request,
                response=fragment_response,
            ),
        ],
        "write_performed": False,
    }


def _wiki_observation(seed: Mapping[str, Any], vault: Path) -> dict[str, Any]:
    include = seed["expected"]["include"]
    try:
        result = WikiReadService(vault).execute(
            action="page",
            knowledge_id=include["knowledge_id"],
            scope=seed["scope"],
            max_sensitivity=seed["max_sensitivity"],
        )
    except Exception as error:
        raise TaskDomainDriverError("Wiki page was unavailable or not admitted") from error
    content = result.get("content")
    if not isinstance(content, str):
        raise TaskDomainDriverError("Wiki page read shape is invalid")
    markers = {
        "knowledge_id": include["knowledge_id"] in content,
        "knowledge_revision_id": include["knowledge_revision_id"] in content,
        "source_revision_id": include["source_revision_id"] in content,
        "fragment_id": include["fragment_id"] in content,
        "locator": include["locator"] in content,
        "quote_sha256": include["quote_sha256"] in content,
    }
    if (
        not all(markers.values())
        or result.get("write_performed") is not False
        or not isinstance(result.get("content_sha256"), str)
    ):
        raise TaskDomainDriverError("Wiki page does not bind the exact source identity")
    response = {
        "wiki_path": result.get("wiki_path"),
        "content_sha256": result["content_sha256"],
        "content_characters": result.get("content_characters"),
        "binding_markers": markers,
        "write_performed": False,
    }
    request = {
        "action": "page",
        "knowledge_id": include["knowledge_id"],
        "scope": seed["scope"],
        "max_sensitivity": seed["max_sensitivity"],
    }
    return {
        "caller": _CALLER,
        "wiki_path": result.get("wiki_path"),
        "content_sha256": result["content_sha256"],
        "content_characters": result.get("content_characters"),
        "binding_markers": markers,
        "calls": [
            _io_record(
                operation="WikiReadService", action="page", request=request, response=response
            )
        ],
        "write_performed": False,
    }


def _support_envelope(value: Any, *, operation: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TaskDomainDriverError(f"public {operation} structured output is invalid")
    if value.get("schema_version") != "deeplaw.knowledge-support-output/v6":
        raise TaskDomainDriverError(f"public {operation} did not return Query Plan v6")
    if value.get("operation") != operation:
        raise TaskDomainDriverError(f"public {operation} operation identity differs")
    boundary = value.get("authority_boundary")
    if not isinstance(boundary, Mapping) or set(boundary) != set(_V6_AUTHORITY_BOUNDARY):
        raise TaskDomainDriverError(f"public {operation} authority boundary is not closed")
    if (
        boundary.get("legal_authority") is not False
        or boundary.get("official_legal_sources_tool")
        != _V6_AUTHORITY_BOUNDARY["official_legal_sources_tool"]
        or boundary.get("persistent_writes")
        != _V6_AUTHORITY_BOUNDARY["persistent_writes"]
        or boundary.get("case_data_allowed") is not False
        or boundary.get("authority_from_ranking") is not False
    ):
        raise TaskDomainDriverError(f"public {operation} authority boundary is invalid")
    return value


def _structured(result: Any, *, operation: str) -> Mapping[str, Any]:
    if getattr(result, "isError", False):
        raise TaskDomainDriverError(f"public knowledge_support {operation} failed")
    value = getattr(result, "structuredContent", None)
    return _support_envelope(value, operation=operation)


def _provider_content(
    result: Any, capsule: Mapping[str, Any], *, operation: str
) -> tuple[int, str]:
    content = getattr(result, "content", None)
    if not isinstance(content, list) or len(content) != 1:
        raise TaskDomainDriverError(
            f"public {operation} provider content is not one TextContent"
        )
    item = content[0]
    provider_text = getattr(item, "text", None)
    if getattr(item, "type", None) != "text" or not isinstance(provider_text, str):
        raise TaskDomainDriverError(
            f"public {operation} provider content is not one TextContent"
        )
    expected_text = canonical_json(capsule)
    if provider_text != expected_text:
        raise TaskDomainDriverError(
            f"public {operation} provider content differs from its structured capsule"
        )
    provider_bytes = provider_text.encode("utf-8")
    if len(provider_bytes) > 65_536:
        raise TaskDomainDriverError(f"public {operation} provider content exceeds its byte bound")
    return len(provider_bytes), sha256_bytes(provider_bytes)


def _validate_capsule_schema(capsule: Any, *, operation: str) -> None:
    if not isinstance(capsule, Mapping):
        raise TaskDomainDriverError(f"public {operation} capsule schema is invalid")
    try:
        _validate_contract("knowledge-capsule-projection.v1.schema.json", dict(capsule))
    except (TypeError, ValueError):
        raise TaskDomainDriverError(f"public {operation} capsule schema is invalid") from None


def _capsule(
    value: Mapping[str, Any], *, operation: str
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    value = _support_envelope(value, operation=operation)
    result = value.get("result")
    if not isinstance(result, Mapping):
        raise TaskDomainDriverError(f"public {operation} result is invalid")
    capsule = result.get("capsule")
    delivery = result.get("delivery")
    if not isinstance(capsule, Mapping) or not isinstance(delivery, Mapping):
        raise TaskDomainDriverError(f"public {operation} provider capsule is invalid")
    _validate_capsule_schema(capsule, operation=operation)
    if (
        capsule.get("schema_version") != "deeplaw.knowledge-capsule-projection/v1"
        or delivery.get("hard_limit_bytes") != 65_536
        or delivery.get("write_performed") is not False
        or result.get("policy_id") != "evidence-first-v1"
        or result.get("purpose") != "quote"
    ):
        raise TaskDomainDriverError(f"public {operation} provider policy is invalid")
    if delivery.get("provider_content_bytes") != len(canonical_json(capsule).encode("utf-8")):
        raise TaskDomainDriverError(f"public {operation} provider byte accounting is invalid")
    return capsule, result


def _capsule_observation(
    capsule: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    operation: str,
) -> dict[str, Any]:
    _validate_capsule_schema(capsule, operation=operation)
    include = expected["include"]
    exclude = expected["exclude"]
    statements = capsule.get("statements")
    evidence = capsule.get("evidence")
    gaps = capsule.get("gaps")
    if (
        not isinstance(statements, list)
        or not isinstance(evidence, list)
        or not isinstance(gaps, list)
        or len(statements) != 1
        or len(evidence) != 1
        or not isinstance(statements[0], Mapping)
        or not isinstance(evidence[0], Mapping)
    ):
        raise TaskDomainDriverError(f"public {operation} source/authority binding is invalid")
    target = statements[0]
    evidence_ref = evidence[0]
    selected_id = target.get("knowledge_id")
    selected_source = evidence_ref.get("source_revision_id")
    if not isinstance(selected_id, str) or not isinstance(selected_source, str):
        raise TaskDomainDriverError(f"public {operation} selected identity is invalid")
    selected_ids = {selected_id}
    selected_sources = {selected_source}
    if selected_ids != {include["knowledge_id"]} or selected_ids & set(
        exclude["knowledge_ids"]
    ):
        raise TaskDomainDriverError(f"public {operation} selected an unexpected knowledge identity")
    if selected_sources != {include["source_revision_id"]} or selected_sources & set(
        exclude["source_revision_ids"]
    ):
        raise TaskDomainDriverError(f"public {operation} selected an unexpected source revision")
    refs = target.get("source_refs")
    expected_ref = {
        "source_revision_id": include["source_revision_id"],
        "fragment_id": include["fragment_id"],
        "locator": include["locator"],
        "quote_sha256": include["quote_sha256"],
    }
    if (
        not isinstance(refs, list)
        or len(refs) != 1
        or not isinstance(refs[0], Mapping)
        or set(refs[0]) != set(expected_ref)
        or any(refs[0].get(key) != expected_ref[key] for key in expected_ref)
        or not isinstance(evidence_ref.get("source_refs"), list)
        or len(evidence_ref["source_refs"]) != 1
        or not isinstance(evidence_ref["source_refs"][0], Mapping)
        or set(evidence_ref["source_refs"][0]) != set(expected_ref)
        or any(
            evidence_ref["source_refs"][0].get(key) != expected_ref[key]
            for key in expected_ref
        )
        or target.get("knowledge_revision_id") != include["knowledge_revision_id"]
        or target.get("authority") != include["authority"]
        or target.get("legal_authority") is not include["legal_authority"]
        or target.get("verification") != include["verification"]
        or evidence_ref.get("verification") != "verified_source"
        or evidence_ref.get("fragment_id") != include["fragment_id"]
        or evidence_ref.get("content_sha256") != include["quote_sha256"]
        or not isinstance(evidence_ref.get("excerpt"), str)
        or sha256_bytes(evidence_ref["excerpt"].encode("utf-8")) != include["quote_sha256"]
    ):
        raise TaskDomainDriverError(f"public {operation} source/authority binding is invalid")
    actual_gap_pairs: list[tuple[str, str]] = []
    for item in gaps:
        if not isinstance(item, Mapping) or not isinstance(item.get("code"), str) or not isinstance(
            item.get("duty"), str
        ):
            raise TaskDomainDriverError(f"public {operation} Gap set is malformed")
        pair = (item["code"], item["duty"])
        if pair in actual_gap_pairs:
            raise TaskDomainDriverError(f"public {operation} Gap set contains duplicates")
        actual_gap_pairs.append(pair)
    actual_gaps = set(actual_gap_pairs)
    expected_gaps = {(item["code"], item["duty"]) for item in expected["gaps"]}
    if actual_gaps != expected_gaps:
        raise TaskDomainDriverError(f"public {operation} Gap set differs from the frozen seed")
    if {"ledger", "query_trace", "audit", "query_plan"} & set(capsule):
        raise TaskDomainDriverError(f"public {operation} capsule leaked local trace state")
    return {
        "selected_knowledge_ids": sorted(selected_ids),
        "selected_source_revision_ids": sorted(selected_sources),
        "knowledge_revision_id": include["knowledge_revision_id"],
        "source_refs": [expected_ref],
        "gap_pairs": [
            {"code": code, "duty": duty} for code, duty in sorted(actual_gap_pairs)
        ],
        "statement_count": len(statements),
        "evidence_count": len(evidence),
        "provider_content_bytes": len(canonical_json(capsule).encode("utf-8")),
        "provider_content_sha256": sha256_bytes(_json_bytes(capsule)),
        "write_performed": False,
    }


async def _public_v7_reads(
    seed: Mapping[str, Any],
    *,
    vault: Path,
    deeplaw_executable: str,
    deeplaw_prefix: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    executable = deeplaw_executable
    if not isinstance(executable, str) or not executable or chr(0) in executable:
        raise TaskDomainDriverError("DeepLaw executable is invalid")
    if shutil.which(executable) is None and not Path(executable).is_file():
        raise TaskDomainDriverError("installed DeepLaw executable is unavailable")
    prefix = tuple(deeplaw_prefix)
    if any(not isinstance(item, str) or not item or chr(0) in item for item in prefix):
        raise TaskDomainDriverError("DeepLaw executable prefix is invalid")
    expected_vault_id = observed_knowledge_vault_id(vault)
    try:
        argv = build_closed_mcp_argv(
            surface="knowledge_support",
            executable=executable,
            expected_vault_id=expected_vault_id,
        )
    except Exception as error:
        raise TaskDomainDriverError("DeepLaw closed MCP command is invalid") from error
    command = [argv[0], *prefix, *argv[1:]]
    include = seed["expected"]["include"]
    request = {
        "purpose": "quote",
        "policy": "evidence-first-v1",
        "query_plan_version": "6",
        "query_target": {"knowledge_id": include["knowledge_id"]},
        "scope": seed["scope"],
        "max_sensitivity": seed["max_sensitivity"],
        "limit": 4,
        "max_chars": 8_000,
        "max_tokens": 2_000,
        "max_sources": 4,
        "applicable_duties": list(seed["public_duties"]),
    }
    with closed_mcp_environment(
        surface="knowledge_support",
        vault_path=vault,
        expected_vault_id=expected_vault_id,
    ) as launch:
        parameters = StdioServerParameters(
            command=command[0],
            args=command[1:],
            cwd=launch.cwd,
            env=launch.environment,
        )
        try:
            async with (
                stdio_client(parameters) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                tools = await session.list_tools()
                if len(tools.tools) != 1 or tools.tools[0].name != "knowledge_support":
                    raise TaskDomainDriverError("public knowledge_support inventory is not closed")
                schema = tools.tools[0].inputSchema
                if schema.get("title") != "DeepLaw Knowledge Support Provider Input v7":
                    raise TaskDomainDriverError("public knowledge_support input is not v7")
                if {
                    branch.get("$ref", "").rsplit("/", maxsplit=1)[-1]
                    for branch in schema.get("oneOf", [])
                } != {"query", "context", "explain"}:
                    raise TaskDomainDriverError(
                        "public knowledge_support operation inventory changed"
                    )
                query_request = {**request, "operation": "query", "query": seed["task"]}
                query_result = await session.call_tool("knowledge_support", query_request)
                query_value = _structured(query_result, operation="query")
                query_capsule, query_outer = _capsule(query_value, operation="query")
                query_provider_content = _provider_content(
                    query_result, query_capsule, operation="query"
                )
                query_observation = _capsule_observation(
                    query_capsule, seed["expected"], operation="query"
                )
                query_receipt = query_outer.get("receipt")
                if not isinstance(query_receipt, Mapping):
                    raise TaskDomainDriverError("public query receipt is invalid")
                query_observation["caller"] = _CALLER
                query_observation["receipt_id"] = query_receipt.get("receipt_id")
                query_observation["calls"] = [
                    _io_record(
                        operation="knowledge_support",
                        action="query",
                        request=query_request,
                        response=query_observation,
                        structured_response=query_value,
                        provider_content=query_provider_content,
                    )
                ]
                context_request = {
                    **request,
                    "operation": "context",
                    "task": seed["task"],
                    "confirm_no_case_data": True,
                }
                context_result = await session.call_tool("knowledge_support", context_request)
                context_value = _structured(context_result, operation="context")
                context_capsule, context_outer = _capsule(
                    context_value, operation="context"
                )
                context_provider_content = _provider_content(
                    context_result, context_capsule, operation="context"
                )
                context_observation = _capsule_observation(
                    context_capsule, seed["expected"], operation="context"
                )
                context_receipt = context_outer.get("receipt")
                if not isinstance(context_receipt, Mapping):
                    raise TaskDomainDriverError("public context receipt is invalid")
                context_observation["caller"] = _CALLER
                context_observation["receipt_id"] = context_receipt.get("receipt_id")
                context_observation["calls"] = [
                    _io_record(
                        operation="knowledge_support",
                        action="context",
                        request=context_request,
                        response=context_observation,
                        structured_response=context_value,
                        provider_content=context_provider_content,
                    )
                ]
                receipt_id = context_receipt.get("receipt_id")
                explain_request = {"operation": "explain", "receipt_id": receipt_id}
                explain_result = await session.call_tool("knowledge_support", explain_request)
                explain_value = _structured(explain_result, operation="explain")
                if explain_value.get("schema_version") != "deeplaw.knowledge-support-output/v6":
                    raise TaskDomainDriverError("public explain did not return Query Plan v6")
                explain_body = explain_value.get("result")
                audit = explain_body.get("audit") if isinstance(explain_body, Mapping) else None
                if (
                    not isinstance(audit, Mapping)
                    or audit.get("receipt_id") != receipt_id
                    or audit.get("write_performed") is not False
                    or audit.get("schema_version") != "deeplaw.query-audit-receipt/v1"
                ):
                    raise TaskDomainDriverError("public QueryTrace is invalid")
                query_trace = {
                    key: audit.get(key)
                    for key in (
                        "receipt_id",
                        "query_plan_sha256",
                        "query_sha256",
                        "receipt_sha256",
                        "input_audit_head",
                        "input_legacy_audit_head",
                        "selected_statement_ids",
                        "residual_gap_ids",
                    )
                }
                query_trace["write_performed"] = False
                context_statement_ids = [
                    item["statement_id"]
                    for item in context_capsule["statements"]
                    if isinstance(item, Mapping) and "statement_id" in item
                ]
                if query_trace["selected_statement_ids"] != context_statement_ids:
                    raise TaskDomainDriverError("QueryTrace statement binding is invalid")
                if not isinstance(query_trace["query_plan_sha256"], str) or not isinstance(
                    query_trace["receipt_sha256"], str
                ):
                    raise TaskDomainDriverError("QueryTrace digest binding is invalid")
                explain_response = {
                    "schema_version": audit["schema_version"],
                    "receipt_id": audit["receipt_id"],
                    "query_plan_sha256": audit.get("query_plan_sha256"),
                    "selected_statement_ids": audit.get("selected_statement_ids"),
                    "residual_gap_ids": audit.get("residual_gap_ids"),
                    "write_performed": False,
                }
                query_trace.update(
                    {
                        "caller": _CALLER,
                        "operation": "knowledge_support",
                        "action": "explain",
                        "calls": [
                            _io_record(
                                operation="knowledge_support",
                                action="explain",
                                request=explain_request,
                                response=explain_response,
                                structured_response=explain_value,
                            )
                        ],
                    }
                )
                return query_observation, context_observation, query_trace
        except TaskDomainDriverError:
            raise
        except Exception as error:
            raise TaskDomainDriverError("public knowledge_support v7 read failed") from error


def collect_task_domain(
    seed: Mapping[str, Any],
    *,
    vault: str | Path,
    deeplaw_executable: str = "deeplaw",
    deeplaw_prefix: Sequence[str] = (),
) -> dict[str, Any]:
    """Collect bounded read facts for one frozen source-backed task seed."""

    frozen = validate_task_seed(seed)
    selected_vault = Path(vault).expanduser().absolute()
    if not selected_vault.is_dir() or selected_vault.is_symlink():
        raise TaskDomainDriverError("selected Knowledge Vault is unavailable")
    before = _read_identity(selected_vault)
    source = _source_observation(frozen, selected_vault)
    wiki = _wiki_observation(frozen, selected_vault)
    query, context, query_trace = asyncio.run(
        _public_v7_reads(
            frozen,
            vault=selected_vault,
            deeplaw_executable=deeplaw_executable,
            deeplaw_prefix=deeplaw_prefix,
        )
    )
    after = _read_identity(selected_vault)
    if before != after:
        raise TaskDomainDriverError("read driver changed the Ledger identity")
    if (
        query_trace.get("input_audit_head") != before["audit_head"]
        or query_trace.get("input_legacy_audit_head") != before["legacy_audit_head"]
    ):
        raise TaskDomainDriverError("QueryTrace input identity differs from the read snapshot")
    executed_operations = {"source_read", "wiki_read", "query_context"}
    if frozen["task_case"] == "professional_evidence":
        executed_operations.add("fragment_read")
        executed_duties = {
            "original_bytes",
            "original_hash",
            "fragment",
            "locator",
            "wiki_exact_source_drill_down",
        }
    else:
        executed_duties = {"wiki_exact_source_drill_down"}
    not_executed = [
        f"duty:{duty}"
        for duty in frozen["expected"]["duties"]
        if duty not in executed_duties
    ]
    not_executed.extend(
        f"operation:{operation}"
        for operation in frozen["expected"]["operations"]
        if operation not in executed_operations
    )
    not_executed.extend(
        [
            "native_host_events",
            "model_usage",
            "process_authority",
            "formal_host_qualification",
        ]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "executed",
        "formal_admission": False,
        "claim_eligible": False,
        "caller": _CALLER,
        "driver_kind": DRIVER_KIND,
        "task_case": frozen["task_case"],
        "task_seed": frozen,
        "observations": {
            "source_read": source,
            "wiki_read": wiki,
            "query": query,
            "context": context,
            "query_trace": query_trace,
            "ledger": {
                "before_audit_head": before["audit_head"],
                "after_audit_head": after["audit_head"],
                "before_legacy_audit_head": before["legacy_audit_head"],
                "after_legacy_audit_head": after["legacy_audit_head"],
                "unchanged": True,
            },
        },
        "executed_operations": sorted(executed_operations),
        "not_executed": not_executed,
        "write_performed": False,
    }


def _load_seed(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_SEED_BYTES:
            raise TaskDomainDriverError("task seed file exceeds its read bound")
        with path.open("rb") as handle:
            raw = handle.read(_MAX_SEED_BYTES + 1)
        if len(raw) > _MAX_SEED_BYTES:
            raise TaskDomainDriverError("task seed file exceeds its read bound")
        value = strict_json_loads(raw)
    except TaskDomainDriverError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise TaskDomainDriverError("task seed file is unavailable") from error
    if not isinstance(value, Mapping):
        raise TaskDomainDriverError("task seed file is not an object")
    return validate_task_seed(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded v0.13 task-domain read driver."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    collect_parser = subcommands.add_parser("collect")
    collect_parser.add_argument("--vault", type=Path, required=True)
    collect_parser.add_argument("--seed", type=Path, required=True)
    collect_parser.add_argument("--deeplaw-executable", default="deeplaw")
    collect_parser.add_argument("--deeplaw-prefix", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "collect":
        raise TaskDomainDriverError("unsupported task-domain driver command")
    result = collect_task_domain(
        _load_seed(args.seed),
        vault=args.vault,
        deeplaw_executable=args.deeplaw_executable,
        deeplaw_prefix=args.deeplaw_prefix,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DRIVER_KIND",
    "SCHEMA_VERSION",
    "SEED_SCHEMA_VERSION",
    "TASK_CASES",
    "TaskDomainDriverError",
    "build_parser",
    "build_task_seed",
    "collect_task_domain",
    "main",
    "validate_task_seed",
]
