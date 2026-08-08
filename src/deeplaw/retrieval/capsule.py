"""Shared assembly for purpose-aware Knowledge Capsule responses.

This module deliberately depends on the retrieval service, not on an Agent
adapter.  The local v3 capsule is an owner-local response contract; the
provider v2 projection is the only payload intended to cross an Agent
provider boundary.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..knowledge_models import utc_now
from ..task_context import normalize_task_context_binding
from ..util import canonical_json, sha256_bytes, stable_id

LOCAL_CAPSULE_SCHEMA = "deeplaw.knowledge-capsule/v3"
PROVIDER_CAPSULE_SCHEMA = "deeplaw.provider-knowledge-capsule/v2"
LOCAL_CAPSULE_HARD_LIMIT = 256 * 1024
PROVIDER_CAPSULE_HARD_LIMIT = 65_536


def _validate_contract(name: str, value: dict[str, Any]) -> None:
    # Keep this dependency lazy to avoid the knowledge_autonomy/query_v6 import
    # cycle.  The canonical contract loader remains owned by the domain store.
    from ..knowledge_autonomy import _validate_contract as validate

    validate(name, value)


def provider_capsule_from_v6(result: dict[str, Any]) -> dict[str, Any]:
    """Project one v6 retrieval result onto the bounded provider surface."""

    if result.get("schema_version") != "deeplaw.purpose-aware-retrieval/v3":
        raise RuntimeError("Query Plan v6 result is invalid")
    plan = result.get("query_plan")
    capsule = result.get("capsule")
    receipt_id = result.get("receipt_id")
    if not isinstance(plan, dict) or not isinstance(capsule, dict):
        raise RuntimeError("Query Plan v6 provider projection is invalid")
    if not isinstance(receipt_id, str):
        raise RuntimeError("Query Plan v6 receipt identity is invalid")
    provider_capsule = deepcopy(capsule)
    # Task-line bindings remain owner-local admission input.  The provider
    # surface carries only the bounded Gap/receipt result and never the
    # binding's opaque identity fields.
    provider_capsule.pop("task_binding", None)
    provider_plan = provider_capsule.get("query_plan")
    if isinstance(provider_plan, dict):
        provider_plan.pop("task_binding", None)
    if provider_capsule.get("projection") == "audit":
        # Candidate scores and planner diagnostics remain local-only.  An audit
        # request is represented by a standard provider projection plus its
        # opaque receipt join key.
        provider_capsule.pop("audit", None)
        provider_capsule["projection"] = "standard"
    provider = {
        "schema_version": PROVIDER_CAPSULE_SCHEMA,
        "purpose": result["purpose"],
        "policy_id": result["policy_id"],
        "capsule": provider_capsule,
        "receipt": {"receipt_id": receipt_id},
        "delivery": {
            "hard_limit_bytes": PROVIDER_CAPSULE_HARD_LIMIT,
            "provider_content_bytes": len(canonical_json(provider_capsule).encode("utf-8")),
            "projection": provider_capsule["projection"],
            "write_performed": False,
        },
    }
    if provider["delivery"]["provider_content_bytes"] > PROVIDER_CAPSULE_HARD_LIMIT:
        raise RuntimeError("Query Plan v6 provider projection exceeds its hard limit")
    _validate_contract("provider-knowledge-capsule.v2.schema.json", provider)
    return provider


def _local_audit_summary(audit: dict[str, Any], *, receipt_id: str) -> dict[str, Any]:
    """Create a bounded local audit summary without candidate scores or text."""

    if audit.get("receipt_id") != receipt_id:
        raise RuntimeError("Query Plan v6 audit receipt identity is invalid")

    def _count(name: str) -> int:
        value = audit.get(name, [])
        return len(value) if isinstance(value, list) else 0

    summary = {
        "schema_version": "deeplaw.query-audit-summary/v1",
        "receipt_id": receipt_id,
        "query_plan_sha256": audit.get("query_plan_sha256"),
        "query_sha256": audit.get("query_sha256"),
        "input_audit_head": audit.get("input_audit_head"),
        "input_legacy_audit_head": audit.get("input_legacy_audit_head"),
        "candidate_count": audit.get("candidate_count", 0),
        "admitted_statement_count": audit.get("admitted_statement_count", 0),
        "selected_statement_ids": list(audit.get("selected_statement_ids", []))[:20],
        "fallback_count": _count("fallback"),
        "deduplication_count": _count("deduplications"),
        "suppression_count": _count("suppressions"),
        "rejection_count": _count("rejections"),
        "residual_gap_ids": list(audit.get("residual_gap_ids", []))[:32],
        "ranking_authority_changed": False,
        "write_performed": False,
    }
    if not all(isinstance(summary.get(field), str) for field in (
        "query_plan_sha256",
        "query_sha256",
        "input_audit_head",
        "input_legacy_audit_head",
    )):
        raise RuntimeError("Query Plan v6 audit summary is invalid")
    return summary


def _seal(capsule: dict[str, Any]) -> dict[str, Any]:
    capsule["capsule_digest"] = ""
    capsule["capsule_id"] = ""
    digest_body = {
        key: value
        for key, value in capsule.items()
        if key not in {"capsule_id", "capsule_digest"}
    }
    digest = sha256_bytes(canonical_json(digest_body).encode("utf-8"))
    capsule["capsule_digest"] = digest
    capsule["capsule_id"] = stable_id("capsule", capsule["vault_id"], digest)
    return capsule


def assemble_v6_context(
    store: Any,
    *,
    task: str,
    goal: str | None,
    purpose: str,
    policy: str | None,
    scope: str,
    max_sensitivity: str,
    limit: int,
    max_chars: int,
    max_tokens: int,
    max_sources: int,
    graph_hops: int,
    retrieval_mode: str,
    as_of: str | None,
    kinds: tuple[str, ...],
    force_canonical_lexical: bool,
    query_target: str | dict[str, Any] | None,
    applicable_duties: tuple[str, ...] | list[str] | None,
    projection: str,
    confirm_no_case_data: bool,
    task_binding: dict[str, Any] | None = None,
    runtime_snapshot: Any | None = None,
) -> dict[str, Any]:
    """Return local v3, provider v2, and one local trace for a v6 query."""

    if not confirm_no_case_data:
        raise ValueError("Knowledge Capsule requires confirmation that no case data is present")
    from .purpose import PurposeAwareRetrievalService

    normalized_task_binding = normalize_task_context_binding(
        task_binding,
        allow_none=True,
    )
    selected_goal = goal
    query = f"{task} {selected_goal or ''}".strip()
    retrieval = PurposeAwareRetrievalService(store.root).query(
        query,
        purpose=purpose,
        policy=policy,
        scope=scope,
        max_sensitivity=max_sensitivity,
        limit=limit,
        max_chars=max_chars,
        max_tokens=max_tokens,
        max_sources=max_sources,
        graph_hops=graph_hops,
        retrieval_mode=retrieval_mode,
        as_of=as_of,
        kinds=kinds,
        query_plan_version="6",
        force_canonical_lexical=force_canonical_lexical,
        query_target=query_target,
        applicable_duties=applicable_duties,
        projection=projection,
        task_binding=normalized_task_binding,
        _runtime_snapshot=runtime_snapshot,
    )
    if (
        retrieval.get("audit_head") != store.audit_head
        or retrieval.get("query_plan", {}).get("input_legacy_audit_head")
        != store.legacy_audit_head
    ):
        raise RuntimeError("knowledge read planes changed during Capsule compilation")
    receipt_id = retrieval.get("receipt_id")
    local_audit = retrieval.get("local_audit")
    if not isinstance(receipt_id, str) or not isinstance(local_audit, dict):
        raise RuntimeError("Query Plan v6 context receipt is invalid")
    provider = provider_capsule_from_v6(retrieval)
    capsule = {
        "schema_version": LOCAL_CAPSULE_SCHEMA,
        "vault_id": store.vault_id,
        "task": task,
        "goal": selected_goal,
        "as_of": as_of,
        "purpose": purpose,
        "policy_id": retrieval["policy_id"],
        "task_binding": normalized_task_binding,
        "query_plan": retrieval["query_plan"],
        "query_plan_sha256": retrieval["query_plan_sha256"],
        "statements": retrieval["statements"],
        "evidence": retrieval["evidence"],
        "contradictions": retrieval["contradictions"],
        "gaps": retrieval["gaps"],
        "receipt_id": receipt_id,
        "budget": {
            **retrieval["budget"],
            "provider_payload_bytes": provider["delivery"]["provider_content_bytes"],
            "local_payload_hard_limit_bytes": LOCAL_CAPSULE_HARD_LIMIT,
        },
        "audit": _local_audit_summary(local_audit, receipt_id=receipt_id),
        "audit_head": retrieval["audit_head"],
        "created_at": utc_now(),
        "write_performed": False,
        "provider_capsule": provider,
        "capsule_id": "",
        "capsule_digest": "",
    }
    _seal(capsule)
    if len(canonical_json(capsule).encode("utf-8")) > LOCAL_CAPSULE_HARD_LIMIT:
        raise RuntimeError("local v6 Knowledge Capsule exceeds its hard bound")
    _validate_contract("knowledge-capsule.v3.schema.json", capsule)
    return {
        "capsule": capsule,
        "provider_capsule": provider,
        "local_audit": local_audit,
        "retrieval": retrieval,
    }


def build_v6_capsule(store: Any, **kwargs: Any) -> dict[str, Any]:
    """Build only the owner-local v3 capsule."""

    return assemble_v6_context(store, **kwargs)["capsule"]
