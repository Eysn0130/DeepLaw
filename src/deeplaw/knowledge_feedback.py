from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_compiler import compile_context, verify_capsule
from .knowledge_models import Sensitivity, utc_now
from .knowledge_store import KnowledgeVault
from .util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads

_MAX_CAPSULE_FILE_BYTES = 256 * 1024


def _read_verified_capsule(
    path: str | Path,
    *,
    vault: KnowledgeVault,
    allow_historical: bool = False,
) -> dict[str, Any]:
    candidate = Path(path).expanduser().absolute()
    if candidate.is_symlink() or not candidate.is_file():
        raise ValueError("knowledge Capsule must be a regular non-symlink file")
    if not 1 <= candidate.stat().st_size <= _MAX_CAPSULE_FILE_BYTES:
        raise ValueError("knowledge Capsule file is empty or exceeds its bound")
    capsule = strict_json_loads(candidate.read_bytes())
    verification = verify_capsule(capsule, vault=None if allow_historical else vault)
    if not verification["valid"]:
        raise ValueError("knowledge Capsule is not valid for the selected vault")
    if allow_historical and (
        capsule["vault_id"] != vault.vault_id
        or vault.audit_hash_at(capsule["vault_revision"]) != capsule["audit_head"]
    ):
        raise ValueError("historical Capsule audit anchor does not match the selected vault")
    return capsule


def _selected_items(capsule: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[group]
    ]


def create_run_receipt(
    vault: KnowledgeVault,
    *,
    capsule_path: str | Path,
    status: str,
    host_name: str,
    host_version: str,
    model_name: str | None = None,
    model_version: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    outcome_artifact: str | Path | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: float | None = None,
    cost: float | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    capsule = _read_verified_capsule(capsule_path, vault=vault)
    items = _selected_items(capsule)
    selected_asset_ids = [item["asset_id"] for item in items]
    source_ids = sorted(
        {reference["source_id"] for item in items for reference in item["source_refs"]}
    )
    if (model_name is None) != (model_version is None):
        raise ValueError("run receipt model name and version must be supplied together")
    outcome_sha256 = None
    if outcome_artifact is not None:
        artifact = Path(outcome_artifact).expanduser().absolute()
        if artifact.is_symlink() or not artifact.is_file():
            raise ValueError("run outcome artifact must be a regular non-symlink file")
        outcome_sha256 = sha256_file(artifact)
    now = utc_now()
    payload = {
        "schema_version": "deeplaw.knowledge-run-receipt/v1",
        "vault_id": vault.vault_id,
        "vault_revision": capsule["vault_revision"],
        "audit_head": capsule["audit_head"],
        "capsule_id": capsule["capsule_id"],
        "capsule_digest": capsule["capsule_digest"],
        "task_sha256": sha256_bytes(capsule["task"].encode("utf-8")),
        "goal_sha256": (
            sha256_bytes(capsule["goal"].encode("utf-8")) if capsule["goal"] is not None else None
        ),
        "selected_asset_ids": selected_asset_ids,
        "source_ids": source_ids,
        "host": {"name": host_name, "version": host_version},
        "model": (
            {"name": model_name, "version": model_version}
            if model_name is not None and model_version is not None
            else None
        ),
        "started_at": started_at or now,
        "finished_at": finished_at or now,
        "status": status,
        "outcome_artifact_sha256": outcome_sha256,
        "metrics": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "cost": cost,
            "currency": currency,
        },
    }
    return vault.record_run_receipt(payload, capsule=capsule)


def record_structured_feedback(
    vault: KnowledgeVault,
    *,
    run_id: str,
    outcome: str,
    helpful_asset_ids: tuple[str, ...] = (),
    irrelevant_asset_ids: tuple[str, ...] = (),
    harmful_asset_ids: tuple[str, ...] = (),
    stale_asset_ids: tuple[str, ...] = (),
    missing_knowledge: tuple[str, ...] = (),
    missing_sources: tuple[str, ...] = (),
    incorrect_relations: tuple[str, ...] = (),
    budget_failures: tuple[str, ...] = (),
    observation: str,
    recommended_action: str,
    sensitivity: Sensitivity = "private",
) -> dict[str, Any]:
    run = vault.get_run_receipt(run_id)
    if not run["valid"]:
        raise ValueError("structured feedback requires a valid run receipt")
    case_id = stable_id(
        "case",
        vault.vault_id,
        run_id,
        run["capsule_digest"],
        canonical_json(list(helpful_asset_ids)),
        canonical_json(list(missing_knowledge)),
    )
    payload = {
        "schema_version": "deeplaw.knowledge-feedback-ledger/v1",
        "vault_id": vault.vault_id,
        "run_id": run_id,
        "capsule_id": run["capsule_id"],
        "capsule_digest": run["capsule_digest"],
        "vault_revision": run["vault_revision"],
        "outcome": outcome,
        "helpful_asset_ids": list(helpful_asset_ids),
        "irrelevant_asset_ids": list(irrelevant_asset_ids),
        "harmful_asset_ids": list(harmful_asset_ids),
        "stale_asset_ids": list(stale_asset_ids),
        "missing_knowledge": list(missing_knowledge),
        "missing_sources": list(missing_sources),
        "incorrect_relations": list(incorrect_relations),
        "budget_failures": list(budget_failures),
        "observation": observation,
        "recommended_action": recommended_action,
        "review_status": "proposed",
        "created_at": utc_now(),
        "regression_case": {
            "case_id": case_id,
            "run_id": run_id,
            "capsule_id": run["capsule_id"],
            "capsule_digest": run["capsule_digest"],
            "vault_revision": run["vault_revision"],
            "task_sha256": run["task_sha256"],
            "selected_asset_ids": list(run["selected_asset_ids"]),
            "source_ids": list(run["source_ids"]),
            "expected_helpful_asset_ids": list(helpful_asset_ids),
        },
        "sensitivity": sensitivity,
    }
    return vault.record_feedback(payload)


def replay_feedback(
    vault: KnowledgeVault,
    *,
    feedback_id: str,
    capsule_path: str | Path,
) -> dict[str, Any]:
    feedback = vault.get_feedback(feedback_id)
    if not feedback["valid"]:
        raise ValueError("feedback replay requires a valid feedback record")
    original = _read_verified_capsule(
        capsule_path,
        vault=vault,
        allow_historical=True,
    )
    if (
        original["capsule_id"] != feedback["capsule_id"]
        or original["capsule_digest"] != feedback["capsule_digest"]
    ):
        raise ValueError("feedback replay Capsule does not match the feedback record")
    current = compile_context(
        vault,
        task=original["task"],
        goal=original["goal"],
        confirm_no_case_data=True,
        max_items=original["budget"]["max_items"],
        max_chars=original["budget"]["max_chars"],
    )
    original_ids = [item["asset_id"] for item in _selected_items(original)]
    current_ids = [item["asset_id"] for item in _selected_items(current)]
    original_source_ids = sorted(
        {
            reference["source_id"]
            for item in _selected_items(original)
            for reference in item["source_refs"]
        }
    )
    current_source_ids = sorted(
        {
            reference["source_id"]
            for item in _selected_items(current)
            for reference in item["source_refs"]
        }
    )
    helpful = set(feedback["helpful_asset_ids"])
    return {
        "schema_version": "deeplaw.knowledge-feedback-replay/v1",
        "feedback_id": feedback_id,
        "run_id": feedback["run_id"],
        "original_capsule_id": original["capsule_id"],
        "current_capsule_id": current["capsule_id"],
        "original_asset_ids": original_ids,
        "current_asset_ids": current_ids,
        "original_source_ids": original_source_ids,
        "current_source_ids": current_source_ids,
        "retained_helpful_asset_ids": sorted(helpful & set(current_ids)),
        "added_asset_ids": sorted(set(current_ids) - set(original_ids)),
        "removed_asset_ids": sorted(set(original_ids) - set(current_ids)),
        "original_gap_count": len(original["gaps"]),
        "current_gap_count": len(current["gaps"]),
        "retrieval_changed": original_ids != current_ids,
        "source_versions_changed": original_source_ids != current_source_ids,
        "gaps_changed": original["gaps"] != current["gaps"],
        "context_changed": original["capsule_digest"] != current["capsule_digest"],
        "replay_configuration": {
            "compiler_schema": original["compiler_schema"],
            "max_items": original["budget"]["max_items"],
            "max_chars": original["budget"]["max_chars"],
        },
        "task_success_inferred": False,
        "claim_eligible": False,
    }
