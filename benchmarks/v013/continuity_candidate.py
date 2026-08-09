"""Offline development candidate for bounded cross-thread continuity.

The candidate deliberately accepts only the two development thread inputs.  It never
loads external labels, evaluators, providers, or host credentials.  The output is a
small fact record rather than a transcript or a serialized DeepLaw capsule.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

knowledge_autonomy = importlib.import_module("deeplaw.knowledge_autonomy")

SCHEMA_VERSION = "deeplaw.continuity-candidate/v1"
MAX_SOURCE_BYTES = 64 * 1024
MAX_TEXT_CHARS = 2_000
MAX_TASK_CHARS = 4_000
MAX_TAGS = 16
PROVIDER_LIMIT_BYTES = 65_536
LOCAL_LIMIT_BYTES = 262_144

_ABSOLUTE_PATH = re.compile(r"(?:^|[\s=:\"])/(?:Users|home|tmp|private|var)(?:[\s/\"]|$)")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/]")
_SECRET_LIKE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|secret)\s*[\"']?\s*[:=]"
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def _safe_text(value: Any, *, field: str, maximum: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    value = value.strip()
    if not value or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field} is outside its bound")
    if _ABSOLUTE_PATH.search(value) or _WINDOWS_PATH.search(value) or _SECRET_LIKE.search(value):
        raise ValueError(f"{field} contains disallowed material")
    return value


def _safe_id(value: Any, *, field: str) -> str:
    text = _safe_text(value, field=field, maximum=200)
    if _ID.fullmatch(text) is None:
        raise ValueError(f"{field} is invalid")
    return text


def _bounded_id(value: Any) -> str | None:
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _source_value(
    source: str | Path | Mapping[str, Any], *, role: str
) -> tuple[dict[str, Any], str]:
    if isinstance(source, Mapping):
        value = dict(source)
        encoded = canonical_json(value).encode("utf-8")
    else:
        source_path = Path(source)
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError(f"{role} source must be a regular file")
        encoded = source_path.read_bytes()
        if len(encoded) > MAX_SOURCE_BYTES:
            raise ValueError(f"{role} source exceeds its bound")
        try:
            parsed = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{role} source is not UTF-8 JSON") from error
        value = parsed
    if not isinstance(value, dict):
        raise ValueError(f"{role} source must contain one JSON object")
    if len(encoded) > MAX_SOURCE_BYTES:
        raise ValueError(f"{role} source exceeds its bound")
    return value, sha256_bytes(encoded)


def _task(value: dict[str, Any]) -> tuple[str, str]:
    case_id = _safe_id(value.get("case_id", "continuity-development-case"), field="case_id")
    task = _safe_text(value.get("task"), field="thread task", maximum=MAX_TASK_CHARS)
    return case_id, task


def _development_task_binding(*, case_id: str) -> dict[str, Any]:
    """Derive one opaque, deterministic binding for this bounded dev case."""

    project_sha256 = sha256_bytes(
        canonical_json(
            {
                "purpose": "continuity-development",
                "case_id": case_id,
            }
        ).encode("utf-8")
    )
    task_lineage_sha256 = sha256_bytes(
        canonical_json(
            {
                "project_sha256": project_sha256,
                "purpose": "continuity-development-task-line",
                "case_id": case_id,
            }
        ).encode("utf-8")
    )
    return build_task_context_binding(
        project_sha256=project_sha256,
        task_lineage_sha256=task_lineage_sha256,
    )


def _tags(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a list")
    if len(value) > MAX_TAGS:
        raise ValueError(f"{field} exceeds its bound")
    return [_safe_text(item, field=f"{field} item", maximum=100) for item in value]


def _checkpoint(value: dict[str, Any]) -> dict[str, Any]:
    raw = value.get("checkpoint")
    if not isinstance(raw, Mapping):
        raise ValueError("Thread A checkpoint is required")
    title = _safe_text(raw.get("title", "Task checkpoint"), field="checkpoint title")
    semantic_key = _safe_id(raw.get("semantic_key"), field="checkpoint semantic_key")
    initial_body = _safe_text(raw.get("initial_body"), field="checkpoint initial_body")
    current_body = _safe_text(raw.get("current_body"), field="checkpoint current_body")
    expires_at = _safe_text(raw.get("expires_at"), field="checkpoint expires_at", maximum=64)
    tags = _tags(raw.get("tags"), field="checkpoint tags")
    distractor = value.get("distractor")
    if not isinstance(distractor, Mapping):
        raise ValueError("Thread A distractor is required")
    distractor_value = {
        "title": _safe_text(distractor.get("title", "Unrelated state"), field="distractor title"),
        "body": _safe_text(distractor.get("body"), field="distractor body"),
        "semantic_key": _safe_id(
            distractor.get("semantic_key"), field="distractor semantic_key"
        ),
        "tags": _tags(distractor.get("tags"), field="distractor tags"),
    }
    return {
        "title": title,
        "semantic_key": semantic_key,
        "initial_body": initial_body,
        "current_body": current_body,
        "expires_at": expires_at,
        "tags": tags,
        "distractor": distractor_value,
    }


def _sink(
    request: dict[str, Any],
    *,
    grant_id: str,
    vault_path: Path,
) -> dict[str, Any]:
    response = handle_knowledge_sink(request, grant_id=grant_id, vault_path=vault_path)
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Knowledge Sink returned an invalid result")
    return result


def _statement_fact(item: Mapping[str, Any]) -> dict[str, Any]:
    text = item.get("statement_text")
    if isinstance(text, str) and len(text) <= MAX_TEXT_CHARS:
        try:
            safe_text = _safe_text(text, field="selected statement")
        except ValueError:
            safe_text = ""
    else:
        safe_text = ""
    summary = item.get("object_summary")
    summary = summary if isinstance(summary, Mapping) else {}
    source_refs = []
    raw_refs = item.get("source_refs")
    if isinstance(raw_refs, list):
        for reference in raw_refs[:2]:
            if not isinstance(reference, Mapping):
                continue
            projected = {
                field: _bounded_id(reference.get(field))
                for field in (
                    "source_id",
                    "source_revision_id",
                    "revision_id",
                    "fragment_id",
                    "fragment_revision_id",
                )
                if _bounded_id(reference.get(field)) is not None
            }
            if projected:
                source_refs.append(projected)
    return {
        "statement_id": _bounded_id(item.get("statement_id")),
        "knowledge_id": _bounded_id(item.get("knowledge_id")),
        "knowledge_revision_id": _bounded_id(item.get("knowledge_revision_id")),
        "statement_text": safe_text,
        "statement_text_sha256": sha256_bytes(str(text or "").encode("utf-8")),
        "semantic_key": _bounded_id(summary.get("semantic_key")),
        "origin": _bounded_id(item.get("origin")),
        "authority": _bounded_id(item.get("authority")),
        "legal_authority": item.get("legal_authority")
        if isinstance(item.get("legal_authority"), bool)
        else False,
        "source_refs": source_refs,
        "partition": _bounded_id(item.get("partition")),
    }


def _capsule_facts(
    capsule: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    statements = capsule.get("statements")
    selected = [
        _statement_fact(item)
        for item in statements
        if isinstance(item, Mapping)
    ] if isinstance(statements, list) else []
    gaps = capsule.get("gaps")
    gap_codes = (
        sorted(
            {
                item.get("code")
                for item in gaps
                if isinstance(item, Mapping) and _bounded_id(item.get("code")) is not None
            }
        )[:32]
        if isinstance(gaps, list)
        else []
    )
    contradictions = capsule.get("contradictions")
    contradiction_facts = [
        {
            "relation_revision_id": _bounded_id(item.get("relation_revision_id")),
            "statement_id": _bounded_id(item.get("statement_id")),
            "predicate": _bounded_id(item.get("predicate")),
        }
        for item in contradictions
        if isinstance(item, Mapping)
    ][:16] if isinstance(contradictions, list) else []
    return selected, gap_codes, contradiction_facts


def _first_action(statements: Sequence[Mapping[str, Any]]) -> str | None:
    for item in statements:
        text = item.get("statement_text")
        if not isinstance(text, str):
            continue
        for line in text.splitlines():
            label, separator, value = line.partition(":")
            if separator and label.strip() == "NEXT_ACTION":
                return _safe_text(value, field="checkpoint next action", maximum=500)
    return None


def _base_result(
    *,
    mode: str,
    case_id: str,
    source_hashes: dict[str, str],
    started: float,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "mode": mode,
        "status": "executed",
        "claim_eligible": False,
        "competitive_claim_eligible": False,
        "source_hashes": source_hashes,
        "selected_statements": [],
        "provider_selected_statements": [],
        "gap_codes": [],
        "contradictions": [],
        "context_recovered": False,
        "stale_revision_selected": False,
        "distractor_selected": False,
        "first_action": None,
        "decision": None,
        "decision_basis": "no_current_checkpoint_selected",
        "provider_bytes": 0,
        "local_bytes": 0,
        "provider_limit_bytes": PROVIDER_LIMIT_BYTES,
        "local_limit_bytes": LOCAL_LIMIT_BYTES,
        "latency_ms": 0.0,
        "write_performed": False,
        "audit_head_before": None,
        "audit_head_after": None,
        "audit_head_unchanged": None,
        "current_knowledge_id": None,
        "current_revision_id": None,
        "stale_revision_id": None,
        "distractor_knowledge_id": None,
        "current_body_sha256": None,
        "initial_body_sha256": None,
        "distractor_body_sha256": None,
        "error_code": None,
        "input_roles": ["thread_b"] if mode == "host-only" else ["thread_a", "thread_b"],
        "generated_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def build_host_only(
    thread_b_source: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    thread_b, thread_b_hash = _source_value(thread_b_source, role="Thread B")
    case_id, _task_text = _task(thread_b)
    result = _base_result(
        mode="host-only",
        case_id=case_id,
        source_hashes={"thread_b": thread_b_hash},
        started=started,
    )
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def build_host_plus_deeplaw(
    thread_a_source: str | Path | Mapping[str, Any],
    thread_b_source: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    thread_a, thread_a_hash = _source_value(thread_a_source, role="Thread A")
    thread_b, thread_b_hash = _source_value(thread_b_source, role="Thread B")
    case_a, _ = _task(thread_a)
    case_b, task = _task(thread_b)
    if case_a != case_b:
        raise ValueError("Thread A and Thread B case_id values do not match")
    checkpoint = _checkpoint(thread_a)
    task_binding = _development_task_binding(case_id=case_b)
    result = _base_result(
        mode="host-plus-deeplaw",
        case_id=case_b,
        source_hashes={"thread_a": thread_a_hash, "thread_b": thread_b_hash},
        started=started,
    )
    with tempfile.TemporaryDirectory(prefix="deeplaw-continuity-") as temporary:
        vault_path = Path(temporary) / "vault"
        initialize_knowledge_vault(vault_path, name="continuity-development", scope="project")
        knowledge_autonomy.initialize_autonomous_core(vault_path)
        with knowledge_autonomy.AutonomousKnowledgeStore(vault_path, read_only=False) as store:
            grant_id = store.enable_grant(
                writer_id="continuity-development-host",
                operations=tuple(sorted(knowledge_autonomy.SINK_OPERATIONS)),
                max_mutations_per_minute=120,
            )["grant_id"]
        _sink(
            {
                "operation": "record_run",
                "idempotency_key": "continuity-run-record",
                "confirm_no_case_data": True,
                "run_id": "run-continuity-development",
                "task": "Execute deterministic cross-thread continuity.",
                "host_id": "continuity-development-host",
                "model_id": "deterministic-development-model",
                "status": "succeeded",
                "scope": "project",
                "sensitivity": "private",
                "run_metadata": {"task_binding": task_binding},
            },
            grant_id=grant_id,
            vault_path=vault_path,
        )
        first = _sink(
            {
                "operation": "remember",
                "idempotency_key": "continuity-checkpoint-initial",
                "confirm_no_case_data": True,
                "title": checkpoint["title"],
                "body": checkpoint["initial_body"],
                "kind": "memory",
                "memory_type": "working",
                "semantic_key": checkpoint["semantic_key"],
                "expires_at": checkpoint["expires_at"],
                "scope": "project",
                "sensitivity": "private",
                "run_id": "run-continuity-development",
                "model_id": "deterministic-development-model",
                "tool_id": "continuity-candidate",
                "tags": checkpoint["tags"],
            },
            grant_id=grant_id,
            vault_path=vault_path,
        )
        current = _sink(
            {
                "operation": "remember",
                "idempotency_key": "continuity-checkpoint-current",
                "confirm_no_case_data": True,
                "title": checkpoint["title"],
                "body": checkpoint["current_body"],
                "kind": "memory",
                "memory_type": "working",
                "knowledge_id": first["knowledge_id"],
                "expected_revision_id": first["revision_id"],
                "semantic_key": checkpoint["semantic_key"],
                "expires_at": checkpoint["expires_at"],
                "scope": "project",
                "sensitivity": "private",
                "run_id": "run-continuity-development",
                "model_id": "deterministic-development-model",
                "tool_id": "continuity-candidate",
                "tags": checkpoint["tags"],
            },
            grant_id=grant_id,
            vault_path=vault_path,
        )
        distractor = _sink(
            {
                "operation": "remember",
                "idempotency_key": "continuity-distractor",
                "confirm_no_case_data": True,
                "title": checkpoint["distractor"]["title"],
                "body": checkpoint["distractor"]["body"],
                "kind": "memory",
                "memory_type": "episodic",
                "semantic_key": checkpoint["distractor"]["semantic_key"],
                "expires_at": checkpoint["expires_at"],
                "scope": "project",
                "sensitivity": "private",
                "run_id": "run-continuity-development",
                "model_id": "deterministic-development-model",
                "tool_id": "continuity-candidate",
                "tags": checkpoint["distractor"]["tags"],
            },
            grant_id=grant_id,
            vault_path=vault_path,
        )
        with knowledge_autonomy.AutonomousKnowledgeStore(vault_path, read_only=True) as store:
            audit_head_before = store.audit_head
        context: dict[str, Any] | None = None
        context_error: str | None = None
        try:
            with KnowledgeOS.open(vault_path) as knowledge_os:
                context = knowledge_os.context.compile(
                    task=task,
                    purpose="answer",
                    task_binding=task_binding,
                    confirm_no_case_data=True,
                )
        except (KeyError, RuntimeError, ValueError) as error:
            context_error = type(error).__name__
        with knowledge_autonomy.AutonomousKnowledgeStore(vault_path, read_only=True) as store:
            audit_head_after = store.audit_head
        if context is not None:
            local_statements, gap_codes, contradictions = _capsule_facts(context)
            provider = context.get("provider_capsule")
            provider = provider if isinstance(provider, Mapping) else {}
            provider_statements, provider_gap_codes, _provider_contradictions = _capsule_facts(
                provider.get("capsule") if isinstance(provider.get("capsule"), Mapping) else {}
            )
            result["selected_statements"] = local_statements
            result["provider_selected_statements"] = provider_statements
            result["gap_codes"] = sorted(set(gap_codes) | set(provider_gap_codes))
            result["contradictions"] = contradictions[:16]
            result["provider_bytes"] = len(canonical_json(provider).encode("utf-8"))
            result["local_bytes"] = len(canonical_json(context).encode("utf-8"))
            result["write_performed"] = bool(
                context.get("write_performed")
                or provider.get("delivery", {}).get("write_performed")
            )
        result["current_knowledge_id"] = current["knowledge_id"]
        result["current_revision_id"] = current["revision_id"]
        result["stale_revision_id"] = first["revision_id"]
        result["distractor_knowledge_id"] = distractor["knowledge_id"]
        result["current_body_sha256"] = sha256_bytes(
            checkpoint["current_body"].encode("utf-8")
        )
        result["initial_body_sha256"] = sha256_bytes(
            checkpoint["initial_body"].encode("utf-8")
        )
        result["distractor_body_sha256"] = sha256_bytes(
            checkpoint["distractor"]["body"].encode("utf-8")
        )
        selected_ids = {
            str(item.get("knowledge_revision_id"))
            for item in result["selected_statements"] + result["provider_selected_statements"]
            if item.get("knowledge_revision_id")
        }
        selected_knowledge_ids = {
            str(item.get("knowledge_id"))
            for item in result["selected_statements"] + result["provider_selected_statements"]
            if item.get("knowledge_id")
        }
        result["context_recovered"] = current["revision_id"] in selected_ids
        result["stale_revision_selected"] = first["revision_id"] in selected_ids
        result["distractor_selected"] = distractor["knowledge_id"] in selected_knowledge_ids
        if result["context_recovered"]:
            result["first_action"] = _first_action(result["selected_statements"])
            result["decision"] = result["first_action"]
            result["decision_basis"] = "current_working_revision_selected"
        result["audit_head_before"] = audit_head_before
        result["audit_head_after"] = audit_head_after
        result["audit_head_unchanged"] = audit_head_before == audit_head_after
        result["error_code"] = context_error
        result["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result


def build_candidate(
    mode: str,
    *,
    thread_b_source: str | Path | Mapping[str, Any],
    thread_a_source: str | Path | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if mode == "host-only":
        if thread_a_source is not None:
            raise ValueError("host-only accepts only Thread B source")
        return build_host_only(thread_b_source)
    if mode == "host-plus-deeplaw":
        if thread_a_source is None:
            raise ValueError("host-plus-deeplaw requires Thread A source")
        return build_host_plus_deeplaw(thread_a_source, thread_b_source)
    raise ValueError("unsupported continuity candidate mode")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a bounded continuity development candidate")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    host_only = subparsers.add_parser("host-only")
    host_only.add_argument("--thread-b-source", required=True)
    host_only.add_argument("--output")
    host_plus = subparsers.add_parser("host-plus-deeplaw")
    host_plus.add_argument("--thread-a-source", required=True)
    host_plus.add_argument("--thread-b-source", required=True)
    host_plus.add_argument("--output")
    return parser


def _write_json(value: Mapping[str, Any], output: str | None) -> None:
    encoded = canonical_json(value)
    if output is None:
        print(encoded)
        return
    output_path = Path(output)
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("candidate output already exists")
    output_path.write_text(encoded + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.mode == "host-only":
        result = build_candidate(
            "host-only",
            thread_b_source=arguments.thread_b_source,
        )
    else:
        result = build_candidate(
            "host-plus-deeplaw",
            thread_a_source=arguments.thread_a_source,
            thread_b_source=arguments.thread_b_source,
        )
    _write_json(result, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
