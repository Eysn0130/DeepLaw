from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from threading import RLock
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.run_living_wiki_host_harness import _safe_command
from benchmarks.semantic.review_gold import query_set_sha256, validate_candidate
from deeplaw.api import KnowledgeOS
from deeplaw.compilation.coordinator import _decoded_artifact
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_intelligence import LOCAL_DENSE_MODEL, LOCAL_RERANKER_MODEL
from deeplaw.knowledge_mcp_server import _KnowledgeRuntime, handle_knowledge_support
from deeplaw.util import canonical_json, sha256_bytes, stable_id, strict_json_loads

BUDGET = {
    "max_items": 8,
    "max_sources": 12,
    "max_chars": 8_000,
    "max_tokens": 6_000,
    "max_sensitivity": "public",
}


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _validate(name: str, value: dict[str, Any]) -> None:
    schema = _load(_repository() / "contracts" / name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


_DETERMINISTIC_SEMANTIC_STATUS_PRIORITY = ("blocked", "unknown", "partial", "complete")


def _validate_deterministic_compiler_report(compiler_report: dict[str, Any]) -> None:
    """Validate the development v2 lifecycle without weakening historical v1 gates."""

    version = compiler_report.get("schema_version")
    if version == "deeplaw.deterministic-semantic-lifecycle/v1":
        _validate("deterministic-semantic-lifecycle.v1.schema.json", compiler_report)
        if any(item.get("semantic_status") != "complete" for item in compiler_report["runs"]):
            raise ValueError("deterministic semantic lifecycle v1 cannot admit partial runs")
        return
    if version != "deeplaw.deterministic-semantic-lifecycle/v2":
        raise ValueError("unsupported deterministic semantic lifecycle compiler evidence")
    _validate("deterministic-semantic-lifecycle.v2.schema.json", compiler_report)
    if compiler_report.get("status") != "passed":
        raise ValueError("semantic query suite requires passed compiler evidence")
    if compiler_report.get("compiler_profile_version") != "3":
        raise ValueError("deterministic semantic lifecycle v2 requires compiler profile 3")
    statuses = [item["semantic_status"] for item in compiler_report["runs"]]
    counts = {status: statuses.count(status) for status in _DETERMINISTIC_SEMANTIC_STATUS_PRIORITY}
    counts["total"] = len(statuses)
    aggregate = next(
        status for status in _DETERMINISTIC_SEMANTIC_STATUS_PRIORITY if counts[status]
    )
    if compiler_report["semantic_status_counts"] != counts:
        raise ValueError("deterministic semantic lifecycle v2 status counts do not match runs")
    if compiler_report["semantic_status"] != aggregate:
        raise ValueError("deterministic semantic lifecycle v2 aggregate status is invalid")
    if aggregate not in {"complete", "partial"}:
        raise ValueError("deterministic semantic lifecycle v2 is not admissible for query scoring")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _process_rss_bytes(process_id: int) -> int | None:
    if sys.platform.startswith("linux"):
        try:
            status = Path(f"/proc/{process_id}/status").read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeError):
            return None
        match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, flags=re.MULTILINE)
        return int(match.group(1)) * 1024 if match is not None else None
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(process_id)],
            capture_output=True,
            check=False,
            timeout=5,
        )
        try:
            return int(completed.stdout.strip()) * 1024
        except ValueError:
            return None
    return None


def _run_measured_query(
    arguments: list[str],
) -> tuple[subprocess.CompletedProcess[bytes], int | None]:
    deadline = time.monotonic() + 120
    peak_rss_bytes: int | None = None
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            arguments,
            cwd=_repository(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        while process.poll() is None:
            observed = _process_rss_bytes(process.pid)
            if observed is not None:
                peak_rss_bytes = max(peak_rss_bytes or 0, observed)
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                raise subprocess.TimeoutExpired(arguments, 120)
            time.sleep(0.05)
        return_code = process.wait()
        stdout_file.seek(0)
        stderr_file.seek(0)
        completed = subprocess.CompletedProcess(
            arguments,
            return_code,
            stdout_file.read(),
            stderr_file.read(),
        )
    return completed, peak_rss_bytes


def _compiled_hit_ratio(
    cases: list[dict[str, Any]], gold_cases: list[dict[str, Any]]
) -> float:
    eligible = [
        item
        for item, gold_case in zip(cases, gold_cases, strict=True)
        if any(expected["required"] for expected in gold_case["expected_objects"])
    ]
    if not eligible:
        return 1.0
    return round(
        sum(bool(item["matched_label_ids"]) for item in eligible) / len(eligible),
        6,
    )


def _retrieval_coverage_source_keys(case: dict[str, Any]) -> tuple[str, ...]:
    """Return Source keys that a passing query is expected to admit."""

    source_keys = tuple(str(item) for item in case["source_keys"])
    if case["task_type"] == "source_withdrawal":
        return ()
    if case["task_type"] in {"source_successor_update", "overview_refresh"}:
        return source_keys[-1:]
    return source_keys


def _source_ir_coverage_counts(
    rows: list[dict[str, Any]],
    *,
    expected_run_ids: set[str],
) -> dict[str, int | float]:
    actual_run_ids = {str(row["compilation_run_id"]) for row in rows}
    if actual_run_ids != expected_run_ids:
        raise ValueError("Source IR coverage does not bind every compiler run")
    covered = 0
    omitted = 0
    for row in rows:
        covered_fragments = strict_json_loads(row["covered_fragment_ids_json"])
        omitted_fragments = strict_json_loads(row["omitted_fragments_json"])
        if not isinstance(covered_fragments, list) or not isinstance(
            omitted_fragments, list
        ):
            raise ValueError("Source IR coverage rows must contain JSON arrays")
        covered += len(covered_fragments)
        omitted += len(omitted_fragments)
    total = covered + omitted
    if total == 0:
        raise ValueError("Source IR coverage cannot be established from empty batches")
    return {
        "covered_fragment_count": covered,
        "omitted_fragment_count": omitted,
        "total_fragment_count": total,
        "ratio": round(covered / total, 6),
    }


def _source_ir_coverage(
    *, vault: Path, compiler_report: dict[str, Any]
) -> dict[str, Any]:
    run_ids = {
        str(item["compilation_run_id"])
        for item in compiler_report["runs"]
        if isinstance(item.get("compilation_run_id"), str)
    }
    if not run_ids or len(run_ids) != len(compiler_report["runs"]):
        raise ValueError("compiler evidence has missing or duplicate compilation run IDs")
    placeholders = ",".join("?" for _ in run_ids)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        rows = [
            dict(row)
            for row in store.connection.execute(
                f"""
                SELECT batches.compilation_run_id, batches.packet_id,
                       batches.observation_plan_sha256,
                       batches.covered_fragment_ids_json,
                       batches.omitted_fragments_json,
                       batches.coverage_ratio, packets.fragment_count
                FROM semantic_observation_batches_v2 AS batches
                JOIN source_compilation_packets_v1 AS packets
                  ON packets.compilation_run_id = batches.compilation_run_id
                 AND packets.packet_id = batches.packet_id
                WHERE batches.compilation_run_id IN ({placeholders})
                ORDER BY batches.compilation_run_id, batches.packet_id
                """,
                tuple(sorted(run_ids)),
            )
        ]
        for row in rows:
            plan = _decoded_artifact(
                store,
                row["observation_plan_sha256"],
                role="observation_plan",
            )
            coverage = plan.get("coverage")
            if (
                plan.get("compilation_run_id") != row["compilation_run_id"]
                or plan.get("packet_id") != row["packet_id"]
                or not isinstance(coverage, dict)
                or canonical_json(coverage.get("covered_fragment_ids"))
                != row["covered_fragment_ids_json"]
                or canonical_json(coverage.get("omitted_fragments"))
                != row["omitted_fragments_json"]
                or coverage.get("packet_fragment_count") != row["fragment_count"]
                or not math.isclose(
                    float(coverage.get("ratio", -1)),
                    float(row["coverage_ratio"]),
                    abs_tol=1e-9,
                )
            ):
                raise ValueError("Source IR coverage row does not match its immutable plan")
        audit_head = store.audit_head
    counts = _source_ir_coverage_counts(rows, expected_run_ids=run_ids)
    body = {
        "schema_version": "deeplaw.semantic-source-ir-coverage/v1",
        "compiler_report_id": compiler_report["report_id"],
        "compilation_run_count": len(run_ids),
        "compilation_run_ids_sha256": sha256_bytes(
            canonical_json(sorted(run_ids)).encode("utf-8")
        ),
        "observation_plan_set_sha256": sha256_bytes(
            canonical_json(
                sorted(str(row["observation_plan_sha256"]) for row in rows)
            ).encode("utf-8")
        ),
        "batch_count": len(rows),
        **counts,
        "ledger_audit_head": audit_head,
    }
    return {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def _cross_packet_identity_check(
    *,
    vault: Path,
    compiler_report: dict[str, Any],
    gold_case: dict[str, Any],
    query_case: dict[str, Any],
) -> dict[str, Any]:
    run = next(
        item
        for item in compiler_report["runs"]
        if item["source_key"] == gold_case["source_keys"][0]
    )
    expected = gold_case["expected_objects"][0]
    expected_aliases = {_normalize(item) for item in expected.get("aliases", [])}
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        rows = store.connection.execute(
            """
            SELECT observations.packet_id, observations.observation_id,
                   observations.observation_json, dispositions.target_ref
            FROM semantic_observations_v2 AS observations
            LEFT JOIN semantic_observation_dispositions_v1 AS dispositions
              USING(compilation_run_id, observation_id)
            WHERE observations.compilation_run_id = ?
              AND observations.kind = 'entity'
            ORDER BY observations.packet_id, observations.observation_id
            """,
            (run["compilation_run_id"],),
        ).fetchall()
    matching = []
    for row in rows:
        observation = strict_json_loads(row["observation_json"])
        aliases = {
            _normalize(item)
            for item in observation.get("aliases", [])
            if isinstance(item, str)
        }
        candidate = {
            "kind": observation.get("kind"),
            "title": observation.get("title_candidate") or "",
            "metadata": {"aliases": sorted(aliases)},
        }
        if _matches_expected(candidate, expected) and expected_aliases.issubset(
            aliases
        ):
            matching.append(row)
    packet_ids = sorted({str(row["packet_id"]) for row in matching})
    disposition_targets = sorted(
        {
            str(row["target_ref"])
            for row in matching
            if isinstance(row["target_ref"], str)
        }
    )
    final_matches = [
        item
        for item in query_case["actual_objects"]
        if _matches_expected(item, expected)
    ]
    final_knowledge_ids = sorted(
        {str(item["knowledge_id"]) for item in final_matches}
    )
    final_semantic_keys = sorted(
        {str(item["semantic_key"]) for item in final_matches}
    )
    valid = bool(
        run["packet_count"] >= 2
        and len(set(run["packet_ids"])) >= 2
        and run["observation_count"] >= 2
        and len(packet_ids) >= 2
        and len(matching) >= 2
        and len(disposition_targets) == 1
        and len(final_knowledge_ids) == 1
        and set(disposition_targets).issubset(final_semantic_keys)
    )
    body = {
        "valid": valid,
        "case_id": gold_case["case_id"],
        "compilation_run_id": run["compilation_run_id"],
        "run_packet_count": run["packet_count"],
        "matching_observation_count": len(matching),
        "distinct_packet_ids": packet_ids,
        "disposition_targets": disposition_targets,
        "final_knowledge_ids": final_knowledge_ids,
        "final_semantic_keys": final_semantic_keys,
    }
    return {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def _runtime_python(prefix: list[str]) -> Path:
    executable_value = prefix[0]
    executable = (
        Path(executable_value)
        if Path(executable_value).is_absolute()
        else Path(shutil.which(executable_value) or "")
    )
    if not executable.is_file():
        raise ValueError("first-party query executable cannot be resolved")
    executable = executable.absolute()
    if executable.stem.startswith("python"):
        return executable
    if executable.stem != "deeplaw":
        raise ValueError("query runtime probe requires a deeplaw or Python executable")
    for name in ("python", "python.exe"):
        python = executable.parent / name
        if python.is_file():
            return python.absolute()
    raise ValueError("first-party deeplaw runtime has no sibling Python executable")


def _runtime_environment(prefix: list[str]) -> dict[str, Any]:
    probe = """
import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3

packages = sorted({
    (str(distribution.metadata.get("Name") or "").casefold(), distribution.version)
    for distribution in importlib.metadata.distributions()
    if distribution.metadata.get("Name")
})
try:
    total_memory_bytes = int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
except (AttributeError, OSError, TypeError, ValueError):
    total_memory_bytes = None
value = {
    "os": {
        "system": platform.system() or "unknown",
        "release": platform.release() or "unknown",
        "machine": platform.machine() or "unknown",
    },
    "python": {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
    },
    "sqlite_version": sqlite3.sqlite_version,
    "dependency_inventory_sha256": hashlib.sha256(
        json.dumps(packages, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest(),
    "hardware": {
        "logical_cpu_count": os.cpu_count(),
        "processor": platform.processor() or "unknown",
        "total_memory_bytes": (
            total_memory_bytes if total_memory_bytes and total_memory_bytes > 0 else None
        ),
    },
}
print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
"""
    completed = subprocess.run(
        [str(_runtime_python(prefix)), "-c", probe],
        cwd=_repository(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError("first-party query runtime environment probe failed")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("first-party query runtime environment probe returned a non-object")
    return value


def _execution_environment(
    *, prefix: list[str], network_policy: str
) -> dict[str, Any]:
    return {
        **_runtime_environment(prefix),
        "network_policy": network_policy,
        "cold_state_definition": (
            "first fresh CLI process after deterministic compilation; DeepLaw query cache empty; "
            "OS page cache not forcibly flushed"
        ),
        "warm_state_definition": (
            "immediate identical second query in a fresh CLI process over unchanged persisted "
            "compiled state; OS page cache retained"
        ),
    }


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * fraction + 0.5)))
    return ordered[index]


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w]+", folded, flags=re.UNICODE))


def _matches_expected(item: dict[str, Any], expected: dict[str, Any]) -> bool:
    if item.get("kind") != expected["kind"]:
        return False
    candidates = [item.get("title"), item.get("semantic_key")]
    aliases = item.get("aliases") or item.get("metadata", {}).get("aliases", [])
    if isinstance(aliases, list):
        candidates.extend(aliases)
    candidate_norms = {
        _normalize(value) for value in candidates if isinstance(value, str) and value
    }
    normalized = _normalize(expected["canonical_label"])
    if normalized in candidate_norms:
        return True
    expected_tokens = set(normalized.split())
    return len(expected_tokens) >= 2 and any(
        expected_tokens.issubset(set(candidate.split())) for candidate in candidate_norms
    )


def _item_source_revision_ids(item: dict[str, Any]) -> set[str]:
    return {
        source_revision_id
        for reference in item.get("source_refs", [])
        if isinstance(reference, dict)
        and isinstance(
            source_revision_id := reference.get("source_revision_id"), str
        )
    }


def _target_matches(
    *,
    case: dict[str, Any],
    item: dict[str, Any],
    expected: dict[str, Any],
    source_ids: dict[str, str],
) -> bool:
    if not _matches_expected(item, expected):
        return False
    expected_sources = {
        source_ids[source_key]
        for source_key in case["source_keys"]
        if source_key in source_ids
    }
    if expected_sources and not (_item_source_revision_ids(item) & expected_sources):
        return False
    content = _normalize(str(item.get("content") or item.get("body") or ""))
    return all(
        all(_normalize(term) in content for term in assertion["required_terms"])
        for assertion in expected.get("content_assertions", [])
    )


def _run_json(
    prefix: list[str],
    *arguments: str,
    expect_success: bool = True,
) -> tuple[dict[str, Any] | None, int, bytes, bytes]:
    completed = subprocess.run(
        [*prefix, "knowledge", "--format", "json", *arguments],
        cwd=_repository(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if expect_success and completed.returncode != 0:
        summary = completed.stderr.decode("utf-8", errors="replace")[-2_000:]
        raise RuntimeError(f"first-party semantic command failed: {summary}")
    value: dict[str, Any] | None = None
    if completed.returncode == 0:
        loaded = json.loads(completed.stdout)
        if not isinstance(loaded, dict):
            raise RuntimeError("first-party semantic command returned a non-object")
        value = loaded
    return value, completed.returncode, completed.stdout, completed.stderr


def _query(
    prefix: list[str],
    *,
    vault: Path,
    query: str,
    purpose: str,
    as_of: str | None,
) -> tuple[dict[str, Any], int, int | None]:
    temporal_arguments = ["--as-of", as_of] if as_of is not None else []
    started = time.monotonic()
    completed, peak_rss_bytes = _run_measured_query(
        [
            *prefix,
            "knowledge",
            "--format",
            "json",
            "query",
            "--vault",
            str(vault),
            "--query",
            query,
            "--purpose",
            purpose,
            *temporal_arguments,
            "--scope",
            "personal",
            "--max-sensitivity",
            "public",
            "--limit",
            str(BUDGET["max_items"]),
            "--max-chars",
            str(BUDGET["max_chars"]),
            "--max-tokens",
            str(BUDGET["max_tokens"]),
            "--max-sources",
            str(BUDGET["max_sources"]),
            "--graph-hops",
            "1",
            "--retrieval-mode",
            "hybrid",
            "--query-plan-version",
            "5",
        ]
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    if completed.returncode != 0:
        summary = completed.stderr.decode("utf-8", errors="replace")[-2_000:]
        raise RuntimeError(f"first-party semantic query failed: {summary}")
    if len(completed.stdout) > 512 * 1024:
        raise RuntimeError("first-party semantic query output exceeded 512 KiB")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("first-party semantic query returned a non-object")
    return value, elapsed_ms, peak_rss_bytes


def _selected(value: dict[str, Any]) -> tuple[list[str], list[str]]:
    revisions: set[str] = set()
    source_revisions: set[str] = set()
    for item in [*value.get("compiled", []), *value.get("evidence", [])]:
        if not isinstance(item, dict):
            continue
        revision_id = item.get("revision_id")
        if isinstance(revision_id, str) and revision_id.startswith("knowledgerev_"):
            revisions.add(revision_id)
        direct_source = item.get("source_revision_id")
        if isinstance(direct_source, str) and direct_source.startswith("sourcerev_"):
            source_revisions.add(direct_source)
        for reference in item.get("source_refs", []):
            if not isinstance(reference, dict):
                continue
            source_revision_id = reference.get("source_revision_id")
            if isinstance(source_revision_id, str) and source_revision_id.startswith(
                "sourcerev_"
            ):
                source_revisions.add(source_revision_id)
    return sorted(revisions), sorted(source_revisions)


def _v3_context_retrieval_view(capsule: dict[str, Any]) -> dict[str, Any]:
    """Project the owner-local v3 Capsule onto the deterministic scorer view."""

    if capsule.get("schema_version") != "deeplaw.knowledge-capsule/v3":
        raise ValueError("semantic context must use the knowledge Capsule v3 schema")
    statements = capsule.get("statements")
    evidence = capsule.get("evidence")
    gaps = capsule.get("gaps")
    if not isinstance(statements, list):
        raise ValueError("knowledge Capsule v3 statements must be an array")
    if not isinstance(evidence, list):
        raise ValueError("knowledge Capsule v3 evidence must be an array")
    if not isinstance(gaps, list):
        raise ValueError("knowledge Capsule v3 gaps must be an array")

    compiled: list[dict[str, Any]] = []
    for ordinal, statement in enumerate(statements):
        if not isinstance(statement, dict):
            raise ValueError(f"knowledge Capsule v3 statement {ordinal} must be an object")
        object_summary = statement.get("object_summary")
        knowledge_revision_id = statement.get("knowledge_revision_id")
        statement_text = statement.get("statement_text")
        source_refs = statement.get("source_refs")
        if not isinstance(object_summary, dict):
            raise ValueError(
                f"knowledge Capsule v3 statement {ordinal} object_summary must be an object"
            )
        knowledge_id = object_summary.get("knowledge_id")
        if not isinstance(knowledge_id, str) or not knowledge_id:
            raise ValueError(
                f"knowledge Capsule v3 statement {ordinal} object_summary has no knowledge_id"
            )
        if not isinstance(knowledge_revision_id, str) or not knowledge_revision_id:
            raise ValueError(
                f"knowledge Capsule v3 statement {ordinal} has no knowledge_revision_id"
            )
        summary_revision_id = object_summary.get("revision_id")
        if summary_revision_id is not None and summary_revision_id != knowledge_revision_id:
            raise ValueError(
                f"knowledge Capsule v3 statement {ordinal} revision identity is inconsistent"
            )
        statement_knowledge_id = statement.get("knowledge_id")
        if statement_knowledge_id is not None and statement_knowledge_id != knowledge_id:
            raise ValueError(
                f"knowledge Capsule v3 statement {ordinal} knowledge identity is inconsistent"
            )
        if not isinstance(statement_text, str) or not statement_text:
            raise ValueError(
                f"knowledge Capsule v3 statement {ordinal} statement_text must be non-empty"
            )
        if not isinstance(source_refs, list) or any(
            not isinstance(reference, dict) for reference in source_refs
        ):
            raise ValueError(
                f"knowledge Capsule v3 statement {ordinal} source_refs must be an object array"
            )

        item = {
            **object_summary,
            "knowledge_id": knowledge_id,
            "revision_id": knowledge_revision_id,
            "content": statement_text,
            "source_refs": [dict(reference) for reference in source_refs],
        }
        statement_id = statement.get("statement_id")
        if isinstance(statement_id, str) and statement_id:
            item["statement_id"] = statement_id
        for field in ("valid_from", "valid_to", "limitation"):
            if field in statement:
                item[field] = statement[field]
        compiled.append(item)

    projected_evidence: list[dict[str, Any]] = []
    for ordinal, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ValueError(f"knowledge Capsule v3 evidence {ordinal} must be an object")
        projected_evidence.append(dict(item))

    gap_codes: set[str] = set()
    for ordinal, gap in enumerate(gaps):
        if not isinstance(gap, dict):
            raise ValueError(f"knowledge Capsule v3 gap {ordinal} must be an object")
        code = gap.get("code")
        if not isinstance(code, str) or not code:
            raise ValueError(f"knowledge Capsule v3 gap {ordinal} has no code")
        gap_codes.add(code)
    return {
        "compiled": compiled,
        "evidence": projected_evidence,
        "gaps": [{"code": code} for code in sorted(gap_codes)],
        "query_plan": capsule.get("query_plan", {}),
    }


def _context_payload_measurement(
    *,
    local_capsule: dict[str, Any],
    provider_capsule: dict[str, Any],
    mcp_tool_result: dict[str, Any],
) -> dict[str, Any]:
    """Measure the three real Context delivery boundaries without renaming bytes as tokens."""

    provider_content = provider_capsule.get("capsule")
    if not isinstance(provider_content, dict):
        raise ValueError("provider Context Capsule has no bounded content object")
    local_capsule_bytes = len(canonical_json(local_capsule).encode("utf-8"))
    provider_capsule_bytes = len(canonical_json(provider_capsule).encode("utf-8"))
    mcp_tool_result_bytes = len(canonical_json(mcp_tool_result).encode("utf-8"))
    provider_content_bytes = len(canonical_json(provider_content).encode("utf-8"))
    delivery = provider_capsule.get("delivery")
    declared_content_bytes = (
        delivery.get("provider_content_bytes") if isinstance(delivery, dict) else None
    )
    provider_hard_limit_valid = bool(
        provider_capsule_bytes <= 65_536
        and mcp_tool_result_bytes <= 65_536
        and declared_content_bytes == provider_content_bytes
    )
    return {
        "local_capsule_bytes": local_capsule_bytes,
        "provider_capsule_bytes": provider_capsule_bytes,
        "mcp_tool_result_bytes": mcp_tool_result_bytes,
        "provider_content_bytes": provider_content_bytes,
        "transport_metadata_bytes": max(0, mcp_tool_result_bytes - provider_content_bytes),
        # Bytes remain an independent transport measurement. Provider tokens
        # are unavailable until a real Host returns its usage receipt.
        "provider_token_estimate": None,
        "token_measurement_method": "not_measured",
        "provider_hard_limit_valid": provider_hard_limit_valid,
    }


def _context_surface_identity(value: dict[str, Any], *, provider: bool = False) -> dict[str, Any]:
    """Project one v6 Context surface onto identities and provider-visible semantics."""

    body = value.get("capsule") if provider else value
    if not isinstance(body, dict):
        return {
            "statement_ids": [],
            "knowledge_revision_ids": [],
            "source_revision_ids": [],
            "gap_codes": [],
            "statements": [],
            "evidence": [],
        }

    statements = body.get("statements", [])
    evidence = body.get("evidence", [])
    gaps = body.get("gaps", [])
    if not isinstance(statements, list):
        statements = []
    if not isinstance(evidence, list):
        evidence = []
    if not isinstance(gaps, list):
        gaps = []

    def _source_ids(item: dict[str, Any]) -> list[str]:
        values: list[str] = []
        direct = item.get("source_revision_id")
        if isinstance(direct, str) and direct:
            values.append(direct)
        references = item.get("source_refs", [])
        if isinstance(references, list):
            for reference in references:
                if not isinstance(reference, dict):
                    continue
                source_revision_id = reference.get("source_revision_id")
                if isinstance(source_revision_id, str) and source_revision_id:
                    values.append(source_revision_id)
        return values

    statement_semantics = [
        {
            key: item.get(key)
            for key in (
                "statement_id",
                "statement_text",
                "statement_type",
                "support_status",
                "current_supported",
                "freshness",
                "origin",
                "authority",
                "verification",
                "legal_authority",
                "knowledge_revision_id",
                "knowledge_id",
                "source_refs",
            )
        }
        for item in statements
        if isinstance(item, dict)
    ]
    evidence_semantics = [
        {
            key: item.get(key)
            for key in (
                "evidence_id",
                "source_revision_id",
                "fragment_id",
                "excerpt",
                "content_sha256",
                "source_refs",
                "selection_reason",
                "verification",
            )
        }
        for item in evidence
        if isinstance(item, dict)
    ]
    return {
        "statement_ids": [
            str(item["statement_id"])
            for item in statements
            if isinstance(item, dict) and isinstance(item.get("statement_id"), str)
        ],
        "knowledge_revision_ids": sorted(
            {
                str(item["knowledge_revision_id"])
                for item in statements
                if isinstance(item, dict)
                and isinstance(item.get("knowledge_revision_id"), str)
            }
        ),
        "source_revision_ids": sorted(
            {
                source_revision_id
                for item in [*statements, *evidence]
                if isinstance(item, dict)
                for source_revision_id in _source_ids(item)
            }
        ),
        "gap_codes": sorted(
            {
                str(item.get("code"))
                for item in gaps
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            }
        ),
        "statements": statement_semantics,
        "evidence": evidence_semantics,
    }


def _statement_candidate_items(
    vault: Path,
    *,
    local_audit: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Read only the canonical statement rows named by a local audit receipt."""

    if not isinstance(local_audit, dict):
        return []
    candidate_ids = {
        str(item.get("statement_id") or item.get("candidate_id"))
        for item in local_audit.get("candidates", [])
        if isinstance(item, dict)
        and isinstance(item.get("statement_id") or item.get("candidate_id"), str)
    }
    candidate_ids.update(
        str(item.get("statement_id") or item.get("candidate_id"))
        for field in ("suppressions", "rejections")
        for item in local_audit.get(field, [])
        if isinstance(item, dict)
        and isinstance(item.get("statement_id") or item.get("candidate_id"), str)
    )
    statement_ids = sorted(
        item for item in candidate_ids if item.startswith("statement_")
    )
    if not statement_ids:
        return []
    placeholders = ",".join("?" for _ in statement_ids)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        rows = store.connection.execute(
            f"""
            SELECT statements.statement_id, statements.statement_text,
                   statements.statement_json, revisions.knowledge_id,
                   revisions.title, revisions.semantic_key, revisions.kind,
                   revisions.metadata_json
            FROM knowledge_statements_v1 AS statements
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = statements.knowledge_revision_id
            WHERE statements.statement_id IN ({placeholders})
            """,
            tuple(statement_ids),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            statement = strict_json_loads(row["statement_json"])
            metadata = strict_json_loads(row["metadata_json"])
        except (TypeError, ValueError):
            continue
        if not isinstance(statement, dict):
            continue
        if not isinstance(metadata, dict):
            metadata = {}
        aliases = metadata.get("aliases", [])
        result.append(
            {
                "statement_id": row["statement_id"],
                "knowledge_id": row["knowledge_id"],
                "kind": row["kind"],
                "title": row["title"],
                "semantic_key": row["semantic_key"],
                "aliases": aliases if isinstance(aliases, list) else [],
                "metadata": metadata,
                "content": row["statement_text"],
                "statement_text": row["statement_text"],
                "source_refs": statement.get("source_refs", []),
            }
        )
    return result


def _required_target_suppression_measurement(
    *,
    case: dict[str, Any],
    retrieval_view: dict[str, Any],
    source_ids: dict[str, str],
    matched_label_ids: list[str],
    local_audit: dict[str, Any] | None,
    candidate_items: list[dict[str, Any]],
) -> dict[str, int | float]:
    """Count only discovered/admitted Gold candidates suppressed after admission."""

    required = [item for item in case["expected_objects"] if item["required"]]
    required_count = len(required)
    selected_labels = set(matched_label_ids)
    candidate_by_id = {
        str(item["statement_id"]): item
        for item in candidate_items
        if isinstance(item.get("statement_id"), str)
    }
    audit_suppressions = (
        local_audit.get("suppressions", []) if isinstance(local_audit, dict) else []
    )
    suppression_ids = {
        str(item.get("statement_id") or item.get("candidate_id"))
        for item in audit_suppressions
        if isinstance(item, dict)
        and isinstance(item.get("statement_id") or item.get("candidate_id"), str)
    }
    audit_rejections = (
        local_audit.get("rejections", []) if isinstance(local_audit, dict) else []
    )
    rejection_ids = {
        str(item.get("statement_id") or item.get("candidate_id"))
        for item in audit_rejections
        if isinstance(item, dict)
        and isinstance(item.get("statement_id") or item.get("candidate_id"), str)
    }
    uncompiled_source_count = int(
        retrieval_view.get("query_plan", {}).get("uncompiled_source_count", 0)
    ) if isinstance(retrieval_view.get("query_plan"), dict) else 0
    gap_codes = {
        str(item.get("code"))
        for item in retrieval_view.get("gaps", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    false_suppressed = 0
    missed = 0
    not_discovered = 0
    uncompiled = 0
    rejected = 0
    gap = 0
    for expected in required:
        label_id = str(expected["label_id"])
        if label_id in selected_labels:
            continue
        matches = [
            item
            for item in candidate_by_id.values()
            if _target_matches(
                case=case,
                item=item,
                expected=expected,
                source_ids=source_ids,
            )
        ]
        match_ids = {str(item["statement_id"]) for item in matches}
        if match_ids & suppression_ids:
            false_suppressed += 1
            continue
        missed += 1
        if match_ids & rejection_ids:
            rejected += 1
        elif not matches and uncompiled_source_count:
            uncompiled += 1
        elif not matches and gap_codes:
            gap += 1
        elif not matches:
            not_discovered += 1
    return {
        "required_target_count": required_count,
        "false_suppressed_required_target_count": false_suppressed,
        "required_target_miss_without_suppression_count": missed,
        "required_target_not_discovered_count": not_discovered,
        "required_target_uncompiled_count": uncompiled,
        "required_target_rejected_count": rejected,
        "required_target_gap_count": gap,
        "false_suppression_rate": round(false_suppressed / required_count, 6)
        if required_count
        else 0.0,
    }


def _context_quality_measurement(
    *,
    case: dict[str, Any],
    retrieval_view: dict[str, Any],
    source_ids: dict[str, str],
    matched_label_ids: list[str],
    local_audit: dict[str, Any] | None = None,
    candidate_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    required = [item for item in case["expected_objects"] if item["required"]]
    compiled = [
        item for item in retrieval_view.get("compiled", []) if isinstance(item, dict)
    ]
    relevant_compiled = [
        item
        for item in compiled
        if any(
            _target_matches(
                case=case,
                item=item,
                expected=expected,
                source_ids=source_ids,
            )
            for expected in required
        )
    ]

    def _text(item: dict[str, Any]) -> str:
        return str(
            item.get("content")
            or item.get("statement_text")
            or item.get("quote")
            or item.get("excerpt")
            or ""
        )

    evidence = [
        item
        for item in retrieval_view.get("evidence", [])
        if isinstance(item, dict)
    ]
    relevant_source_references = {
        (
            str(reference.get("source_revision_id") or ""),
            str(reference.get("fragment_revision_id") or ""),
            str(reference.get("locator") or ""),
        )
        for item in relevant_compiled
        for reference in item.get("source_refs", [])
        if isinstance(reference, dict)
        and isinstance(reference.get("source_revision_id"), str)
        and reference["source_revision_id"]
    }
    relevant_evidence = [
        item
        for item in evidence
        if isinstance(item.get("source_revision_id"), str)
        and item["source_revision_id"]
        and (
            str(item.get("source_revision_id") or ""),
            str(item.get("fragment_revision_id") or ""),
            str(item.get("locator") or ""),
        )
        in relevant_source_references
    ]
    relevant_items = [*relevant_compiled, *relevant_evidence]
    context_items = [*compiled, *evidence]
    context_texts = [_text(item) for item in context_items if _text(item)]
    normalized_texts = [_normalize(text) for text in context_texts]
    duplicate_count = len(normalized_texts) - len(set(normalized_texts))
    evidence_keys: list[tuple[str, ...]] = []
    for item in evidence:
        evidence_id = item.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id:
            evidence_keys.append(("evidence_id", evidence_id))
            continue
        references = item.get("source_refs")
        if not isinstance(references, list):
            references = [item] if item.get("source_revision_id") else []
        for reference in references:
            if not isinstance(reference, dict):
                continue
            source_revision_id = reference.get("source_revision_id")
            fragment_identity = reference.get("fragment_id") or reference.get(
                "fragment_revision_id"
            )
            if not isinstance(source_revision_id, str) or not isinstance(
                fragment_identity, str
            ):
                continue
            evidence_keys.append(
                (
                    "source_key",
                    source_revision_id,
                    fragment_identity,
                    str(reference.get("locator") or ""),
                    str(
                        reference.get("quote_sha256")
                        or reference.get("content_sha256")
                        or ""
                    ),
                )
            )
    duplicate_evidence_count = len(evidence_keys) - len(set(evidence_keys))
    relevant_chars = sum(len(_text(item)) for item in relevant_items)
    context_chars = sum(len(text) for text in context_texts)
    expected_gap_codes = set(case.get("expected_gap_codes", []))
    actual_gap_codes = {
        str(item.get("code"))
        for item in retrieval_view.get("gaps", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    }
    duty_total = len(required) + len(expected_gap_codes)
    duty_matched = len(set(matched_label_ids)) + len(
        expected_gap_codes.intersection(actual_gap_codes)
    )
    useful_context_recall = (
        round(len(set(matched_label_ids)) / len(required), 6) if required else 1.0
    )
    suppression_measurement = _required_target_suppression_measurement(
        case=case,
        retrieval_view=retrieval_view,
        source_ids=source_ids,
        matched_label_ids=matched_label_ids,
        local_audit=local_audit,
        candidate_items=candidate_items or [],
    )
    return {
        "useful_context_recall": useful_context_recall,
        **suppression_measurement,
        "duty_coverage": round(duty_matched / duty_total, 6) if duty_total else 1.0,
        "relevant_chars": relevant_chars,
        "context_chars": context_chars,
        "relevant_chars_ratio": round(relevant_chars / context_chars, 6)
        if context_chars
        else (1.0 if not required else 0.0),
        "redundancy_rate": round(duplicate_count / len(normalized_texts), 6)
        if normalized_texts
        else 0.0,
        "duplicate_evidence_rate": round(
            duplicate_evidence_count / len(evidence_keys), 6
        )
        if evidence_keys
        else 0.0,
    }


def _rank_metrics(
    *,
    case: dict[str, Any],
    value: dict[str, Any],
    source_ids: dict[str, str],
) -> dict[str, Any]:
    required = [item for item in case["expected_objects"] if item["required"]]
    ranked = [item for item in value.get("compiled", []) if isinstance(item, dict)]
    matched_labels: set[str] = set()
    target_ids: list[str] = []
    first_rank: int | None = None
    gains: list[int] = []
    for rank, item in enumerate(ranked, start=1):
        labels = [
            expected
            for expected in required
            if _target_matches(
                case=case,
                item=item,
                expected=expected,
                source_ids=source_ids,
            )
        ]
        new_labels = [
            expected
            for expected in labels
            if expected["label_id"] not in matched_labels
        ]
        gain = 1 if new_labels else 0
        gains.append(gain)
        if new_labels and first_rank is None:
            first_rank = rank
        for expected in labels:
            matched_labels.add(expected["label_id"])
            knowledge_id = item.get("knowledge_id")
            if isinstance(knowledge_id, str):
                target_ids.append(f"{expected['label_id']}:{knowledge_id}")
    recall = round(len(matched_labels) / len(required), 6) if required else 1.0
    target_denominator = len(set(target_ids))
    precision = (
        round(len(matched_labels) / target_denominator, 6)
        if target_denominator
        else (1.0 if not required else 0.0)
    )
    ideal_count = min(len(required), len(ranked))
    ideal_dcg = sum(1 / math.log2(index + 2) for index in range(ideal_count))
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    mrr = round(1 / first_rank, 6) if first_rank else (1.0 if not required else 0.0)
    return {
        "matched_label_ids": sorted(matched_labels),
        "recall_at_k": recall,
        "target_scoped_precision_at_k": precision,
        "reciprocal_rank": mrr,
        "ndcg_at_k": round(dcg / ideal_dcg, 6) if ideal_dcg else 1.0,
    }


def _context_rank_metrics(
    *,
    case: dict[str, Any],
    value: dict[str, Any],
    source_ids: dict[str, str],
) -> dict[str, Any]:
    """Add provider-visible Precision@K and the Context MRR name to v6 ranking."""

    ranking = _rank_metrics(case=case, value=value, source_ids=source_ids)
    required = [item for item in case["expected_objects"] if item["required"]]
    ranked = [item for item in value.get("compiled", []) if isinstance(item, dict)]
    relevant_item_count = sum(
        any(
            _target_matches(
                case=case,
                item=item,
                expected=expected,
                source_ids=source_ids,
            )
            for expected in required
        )
        for item in ranked
    )
    precision_at_k = (
        round(relevant_item_count / len(ranked), 6)
        if ranked
        else (1.0 if not required else 0.0)
    )
    return {
        **ranking,
        "precision_at_k": precision_at_k,
        "mrr": ranking["reciprocal_rank"],
    }


def _retrieval_sequence_check(
    *,
    case: dict[str, Any],
    value: dict[str, Any],
    source_ids: dict[str, str],
) -> dict[str, Any]:
    expected_sequence = case.get("expected_sequence", [])
    if case["task_type"] != "event_timeline":
        return {
            "applicable": False,
            "expected": [],
            "actual": [],
            "valid": True,
        }
    observed: list[str] = []
    for item in value.get("compiled", []):
        if not isinstance(item, dict) or not any(
            expected["kind"] == "event"
            and _target_matches(
                case=case,
                item=item,
                expected=expected,
                source_ids=source_ids,
            )
            for expected in case["expected_objects"]
        ):
            continue
        valid_from = item.get("valid_from")
        if isinstance(valid_from, str):
            date = valid_from[:10]
        else:
            match = re.search(
                r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b",
                str(item.get("content") or item.get("body") or ""),
            )
            date = match.group(0) if match is not None else ""
        if date and date not in observed:
            observed.append(date)
    return {
        "applicable": True,
        "expected": expected_sequence,
        "actual": observed,
        "valid": observed == expected_sequence,
    }


def _current_relation_records(vault: Path) -> list[dict[str, Any]]:
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        rows = store.connection.execute(
            """
            SELECT relations.relation_revision_id, relations.relation_key,
                   relations.subject_knowledge_id, relations.predicate,
                   relations.object_knowledge_id, relations.evidence_refs_json,
                   relations.lifecycle, relations.valid_from, relations.valid_to
            FROM knowledge_relations_v3 AS current
            JOIN knowledge_relation_revisions_v3 AS relations
              ON relations.relation_revision_id = current.current_revision_id
            ORDER BY relations.relation_key
            """
        ).fetchall()
    records = []
    for row in rows:
        record = dict(row)
        evidence_refs = strict_json_loads(record.pop("evidence_refs_json"))
        if not isinstance(evidence_refs, list):
            raise ValueError("current relation evidence_refs_json must contain a list")
        record["evidence_refs"] = evidence_refs
        records.append(record)
    return records


def _relation_checks(
    prefix: list[str],
    *,
    vault: Path,
    case: dict[str, Any],
    value: dict[str, Any],
    source_ids: dict[str, str],
) -> list[dict[str, Any]]:
    expectations = case.get("expected_relations", [])
    if not expectations:
        return []
    compiled = [item for item in value.get("compiled", []) if isinstance(item, dict)]
    label_items: dict[str, list[dict[str, Any]]] = {}
    label_ids: dict[str, set[str]] = {}
    for expected in case["expected_objects"]:
        matched = [
            item
            for item in compiled
            if _target_matches(
                case=case,
                item=item,
                expected=expected,
                source_ids=source_ids,
            )
        ]
        label_items[expected["label_id"]] = matched
        label_ids[expected["label_id"]] = {
            item["knowledge_id"]
            for item in matched
            if isinstance(item.get("knowledge_id"), str)
        }
    records = _current_relation_records(vault)
    checks = []
    for expected in expectations:
        expected_subject_ids = label_ids.get(expected["subject_label_id"], set())
        expected_object_ids = label_ids.get(expected["object_label_id"], set())
        expected_source_ids = {source_ids[item] for item in expected["source_keys"]}
        endpoint_source_ids = {
            reference["source_revision_id"]
            for label_id in (
                expected["subject_label_id"], expected["object_label_id"]
            )
            for item in label_items.get(label_id, [])
            for reference in item.get("source_refs", [])
            if isinstance(reference, dict)
            and isinstance(reference.get("source_revision_id"), str)
        }
        candidates = []
        for record in records:
            direct = bool(
                record["subject_knowledge_id"] in expected_subject_ids
                and record["object_knowledge_id"] in expected_object_ids
            )
            reverse = bool(
                expected["directionality"] == "symmetric"
                and record["subject_knowledge_id"] in expected_object_ids
                and record["object_knowledge_id"] in expected_subject_ids
            )
            if not (direct or reverse) or record["predicate"] != expected["predicate"]:
                continue
            evidence_checks = []
            for reference in record["evidence_refs"]:
                fragment_identity = reference.get("fragment_id") or reference.get(
                    "fragment_revision_id"
                )
                fragment, _, _, _ = _run_json(
                    prefix,
                    "source",
                    "fragment",
                    "--vault",
                    str(vault),
                    "--fragment-id",
                    str(fragment_identity),
                    "--scope",
                    "personal",
                    "--max-sensitivity",
                    "public",
                    expect_success=False,
                )
                actual = fragment.get("fragment", {}) if fragment is not None else {}
                valid = bool(
                    fragment_identity
                    and isinstance(actual, dict)
                    and actual.get("source_revision_id")
                    == reference.get("source_revision_id")
                    and actual.get("locator") == reference.get("locator")
                    and actual.get("text_sha256") == reference.get("quote_sha256")
                )
                evidence_checks.append(
                    {
                        "source_revision_id": reference.get("source_revision_id"),
                        "fragment_id": reference.get("fragment_id"),
                        "fragment_revision_id": reference.get("fragment_revision_id"),
                        "locator": reference.get("locator"),
                        "quote_sha256": reference.get("quote_sha256"),
                        "valid": valid,
                    }
                )
            evidence_source_ids = {
                item["source_revision_id"]
                for item in evidence_checks
                if isinstance(item.get("source_revision_id"), str)
            }
            candidates.append(
                {
                    "relation_revision_id": record["relation_revision_id"],
                    "relation_key": record["relation_key"],
                    "subject_knowledge_id": record["subject_knowledge_id"],
                    "predicate": record["predicate"],
                    "object_knowledge_id": record["object_knowledge_id"],
                    "lifecycle": record["lifecycle"],
                    "valid_from": record["valid_from"],
                    "valid_to": record["valid_to"],
                    "evidence_checks": evidence_checks,
                    "valid": bool(
                        record["lifecycle"] == "active"
                        and record["subject_knowledge_id"]
                        != record["object_knowledge_id"]
                        and record["valid_from"] == expected["valid_from"]
                        and record["valid_to"] == expected["valid_to"]
                        and bool(evidence_source_ids)
                        and evidence_source_ids == expected_source_ids
                        and endpoint_source_ids == expected_source_ids
                        and evidence_checks
                        and all(item["valid"] for item in evidence_checks)
                    ),
                }
            )
        valid_candidates = [item for item in candidates if item["valid"]]
        checks.append(
            {
                "relation_id": expected["relation_id"],
                "subject_label_id": expected["subject_label_id"],
                "predicate": expected["predicate"],
                "object_label_id": expected["object_label_id"],
                "directionality": expected["directionality"],
                "valid_from": expected["valid_from"],
                "valid_to": expected["valid_to"],
                "source_revision_ids": sorted(expected_source_ids),
                "endpoint_source_revision_ids": sorted(endpoint_source_ids),
                "actual_relations": candidates,
                "valid": len(valid_candidates) == 1,
            }
        )
    return checks


def _citation_checks(
    prefix: list[str],
    *,
    vault: Path,
    value: dict[str, Any],
) -> tuple[list[dict[str, Any]], int, int, bool]:
    checks: list[dict[str, Any]] = []
    exact_get_valid = True
    for item in value.get("compiled", []):
        if not isinstance(item, dict):
            continue
        knowledge_id = item.get("knowledge_id")
        revision_id = item.get("revision_id")
        if isinstance(knowledge_id, str):
            exact, _, _, _ = _run_json(
                prefix,
                "autonomy",
                "get",
                "--vault",
                str(vault),
                "--knowledge-id",
                knowledge_id,
            )
            exact_get_valid = bool(
                exact_get_valid
                and exact is not None
                and exact.get("revision_id") == revision_id
            )
        for reference in item.get("source_refs", []):
            if not isinstance(reference, dict):
                continue
            fragment_id = reference.get("fragment_id")
            fragment_revision_id = reference.get("fragment_revision_id")
            fragment_identity = (
                fragment_id
                if isinstance(fragment_id, str)
                else fragment_revision_id
                if isinstance(fragment_revision_id, str)
                else None
            )
            if fragment_identity is None:
                checks.append(
                    {
                        "source_revision_id": reference.get("source_revision_id"),
                        "fragment_id": None,
                        "fragment_revision_id": None,
                        "locator": reference.get("locator"),
                        "quote_sha256": reference.get("quote_sha256"),
                        "valid": False,
                    }
                )
                continue
            fragment, _, _, _ = _run_json(
                prefix,
                "source",
                "fragment",
                "--vault",
                str(vault),
                "--fragment-id",
                fragment_identity,
                "--scope",
                "personal",
                "--max-sensitivity",
                "public",
                expect_success=False,
            )
            fragment_record = fragment.get("fragment", {}) if fragment is not None else {}
            valid = bool(
                isinstance(fragment_record, dict)
                and fragment_record.get("source_revision_id")
                == reference.get("source_revision_id")
                and (
                    fragment_revision_id is None
                    or fragment_record.get("fragment_revision_id")
                    == fragment_revision_id
                )
                and (
                    fragment_id is None
                    or fragment_record.get("fragment_id") == fragment_id
                )
                and fragment_record.get("locator") == reference.get("locator")
                and fragment_record.get("text_sha256") == reference.get("quote_sha256")
            )
            checks.append(
                {
                    "source_revision_id": reference.get("source_revision_id"),
                    "fragment_id": fragment_id,
                    "fragment_revision_id": fragment_revision_id,
                    "locator": reference.get("locator"),
                    "quote_sha256": reference.get("quote_sha256"),
                    "valid": valid,
                }
            )
    for item in value.get("evidence", []):
        if not isinstance(item, dict):
            continue
        for reference in item.get("source_refs", []):
            if not isinstance(reference, dict):
                continue
            fragment_id = reference.get("fragment_id")
            fragment_revision_id = reference.get("fragment_revision_id")
            fragment_identity = (
                fragment_id
                if isinstance(fragment_id, str)
                else fragment_revision_id
                if isinstance(fragment_revision_id, str)
                else None
            )
            if fragment_identity is None:
                checks.append(
                    {
                        "source_revision_id": reference.get("source_revision_id"),
                        "fragment_id": None,
                        "fragment_revision_id": None,
                        "locator": reference.get("locator"),
                        "quote_sha256": reference.get("quote_sha256"),
                        "valid": False,
                    }
                )
                continue
            fragment, _, _, _ = _run_json(
                prefix,
                "source",
                "fragment",
                "--vault",
                str(vault),
                "--fragment-id",
                fragment_identity,
                "--scope",
                "personal",
                "--max-sensitivity",
                "public",
                expect_success=False,
            )
            fragment_record = fragment.get("fragment", {}) if fragment is not None else {}
            valid = bool(
                isinstance(fragment_record, dict)
                and fragment_record.get("source_revision_id")
                == reference.get("source_revision_id")
                and (
                    fragment_revision_id is None
                    or fragment_record.get("fragment_revision_id")
                    == fragment_revision_id
                )
                and (
                    fragment_id is None
                    or fragment_record.get("fragment_id") == fragment_id
                )
                and fragment_record.get("locator") == reference.get("locator")
                and fragment_record.get("text_sha256") == reference.get("quote_sha256")
            )
            checks.append(
                {
                    "source_revision_id": reference.get("source_revision_id"),
                    "fragment_id": fragment_id,
                    "fragment_revision_id": fragment_revision_id,
                    "locator": reference.get("locator"),
                    "quote_sha256": reference.get("quote_sha256"),
                    "valid": valid,
                }
            )
    return checks, sum(item["valid"] for item in checks), len(checks), exact_get_valid


def _fragment_continuation_probe(
    prefix: list[str],
    *,
    vault: Path,
    source_ids: dict[str, str],
) -> dict[str, Any]:
    """Consume a real first-party evidence cursor and bind the reassembled bytes."""

    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        placeholders = ",".join("?" for _ in source_ids)
        row = store.connection.execute(
            f"""
            SELECT legacy_fragment_bindings_v2.fragment_revision_id,
                   source_revision_bindings_v2.source_revision_id,
                   source_fragments.locator, source_fragments.text,
                   source_fragments.text_sha256
            FROM source_fragments
            JOIN legacy_fragment_bindings_v2 USING(fragment_id)
            JOIN source_revision_bindings_v2
              ON source_revision_bindings_v2.legacy_source_id =
                 legacy_fragment_bindings_v2.legacy_source_id
            WHERE source_revision_bindings_v2.source_revision_id IN ({placeholders})
              AND LENGTH(source_fragments.text) > 64
            ORDER BY LENGTH(source_fragments.text) DESC,
                     legacy_fragment_bindings_v2.fragment_revision_id
            LIMIT 1
            """,
            tuple(sorted(source_ids.values())),
        ).fetchone()
    if row is None:
        raise RuntimeError("semantic continuation probe requires one multi-page fragment")
    fragment_revision_id = str(row["fragment_revision_id"])
    expected_text = str(row["text"])
    expected_sha256 = str(row["text_sha256"])
    offset = 0
    page_size = 200
    pages: list[dict[str, Any]] = []
    reassembled: list[str] = []
    command_records: list[dict[str, Any]] = []
    for _ in range(1_024):
        value, return_code, stdout, _stderr = _run_json(
            prefix,
            "source",
            "fragment",
            "--vault",
            str(vault),
            "--fragment-id",
            fragment_revision_id,
            "--offset",
            str(offset),
            "--max-chars",
            str(page_size),
            "--scope",
            "personal",
            "--max-sensitivity",
            "public",
            expect_success=False,
        )
        fragment = value.get("fragment", {}) if value is not None else {}
        if return_code != 0 or not isinstance(fragment, dict):
            break
        text = fragment.get("text")
        content_offset = fragment.get("content_offset")
        next_offset = fragment.get("next_offset")
        continuation = fragment.get("continuation")
        page_valid = bool(
            isinstance(text, str)
            and content_offset == offset
            and fragment.get("fragment_revision_id") == fragment_revision_id
            and fragment.get("source_revision_id") == row["source_revision_id"]
            and fragment.get("locator") == row["locator"]
            and fragment.get("text_sha256") == expected_sha256
            and fragment.get("content_characters") == len(text)
            and len(text) <= page_size
            and len(stdout) <= 65_536
        )
        reassembled.append(text if isinstance(text, str) else "")
        pages.append(
            {
                "offset": offset,
                "content_characters": len(text) if isinstance(text, str) else 0,
                "response_bytes": len(stdout),
                "truncated": bool(fragment.get("content_truncated")),
                "next_offset": next_offset,
                "valid": page_valid,
            }
        )
        command_records.append(
            {
                "action": "source.fragment",
                "fragment_revision_id": fragment_revision_id,
                "offset": offset,
                "max_chars": page_size,
            }
        )
        if continuation is None:
            break
        if not (
            isinstance(continuation, dict)
            and continuation.get("action") == "fragment"
            and continuation.get("fragment_id") == fragment_revision_id
            and continuation.get("offset") == next_offset
            and continuation.get("max_chars") == page_size
            and isinstance(next_offset, int)
            and not isinstance(next_offset, bool)
            and next_offset > offset
        ):
            pages[-1]["valid"] = False
            break
        offset = next_offset
    actual_text = "".join(reassembled)
    valid = bool(
        len(pages) >= 2
        and all(page["valid"] for page in pages)
        and pages[-1]["truncated"] is False
        and pages[-1]["next_offset"] is None
        and actual_text == expected_text
        and sha256_bytes(actual_text.encode("utf-8")) == expected_sha256
    )
    body = {
        "schema_version": "deeplaw.semantic-continuation-probe/v1",
        "fragment_revision_id": fragment_revision_id,
        "source_revision_id": row["source_revision_id"],
        "locator_sha256": sha256_bytes(str(row["locator"]).encode("utf-8")),
        "expected_text_sha256": expected_sha256,
        "actual_text_sha256": sha256_bytes(actual_text.encode("utf-8")),
        "page_size_characters": page_size,
        "page_count": len(pages),
        "pages": pages,
        "command_sequence_sha256": sha256_bytes(
            canonical_json(command_records).encode("utf-8")
        ),
        "cursor_consumed": len(pages) >= 2,
        "provider_hard_limit_valid": all(
            page["response_bytes"] <= 65_536 for page in pages
        ),
        "valid": valid,
    }
    return {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def _claim_evidence_checks(
    prefix: list[str],
    *,
    vault: Path,
    case: dict[str, Any],
    value: dict[str, Any],
    source_ids: dict[str, str],
) -> list[dict[str, Any]]:
    """Bind explicit Gold claims to provider-visible exact fixture fragments."""

    compiled = [item for item in value.get("compiled", []) if isinstance(item, dict)]
    fragment_cache: dict[str, dict[str, Any] | None] = {}
    checks: list[dict[str, Any]] = []
    for expected in case["expected_objects"]:
        if not expected["required"]:
            continue
        targets = [
            item
            for item in compiled
            if _target_matches(
                case=case,
                item=item,
                expected=expected,
                source_ids=source_ids,
            )
        ]
        for assertion in expected.get("content_assertions", []):
            expected_sources = {
                source_ids[source_key] for source_key in assertion["source_keys"]
            }
            outcomes: list[dict[str, Any]] = []
            for item in targets:
                receipt_valid = True
                direct_references = [
                    reference
                    for reference in item.get("source_refs", [])
                    if isinstance(reference, dict)
                ]
                direct_source_revision_ids = {
                    str(reference["source_revision_id"])
                    for reference in direct_references
                    if isinstance(reference.get("source_revision_id"), str)
                }
                direct_source_coverage_valid = bool(
                    item.get("kind") != "synthesis"
                    or expected_sources.issubset(direct_source_revision_ids)
                )
                references = direct_references
                if item.get("kind") == "synthesis":
                    receipt = item.get("synthesis_evidence_receipt", {})
                    if len(expected_sources) > 1:
                        receipt_body = dict(receipt) if isinstance(receipt, dict) else {}
                        receipt_sha256 = receipt_body.pop("receipt_sha256", None)
                        receipt_valid = bool(
                            receipt_body
                            and receipt.get("complete") is True
                            and receipt_sha256
                            == sha256_bytes(canonical_json(receipt_body).encode("utf-8"))
                        )
                        references = [
                            reference
                            for reference in receipt.get("source_refs", [])
                            if isinstance(reference, dict)
                        ]
                unique_references: dict[tuple[str, str], dict[str, Any]] = {}
                for reference in references:
                    source_revision_id = reference.get("source_revision_id")
                    fragment_identity = reference.get("fragment_id") or reference.get(
                        "fragment_revision_id"
                    )
                    if not isinstance(source_revision_id, str) or not isinstance(
                        fragment_identity, str
                    ):
                        continue
                    unique_references[(source_revision_id, fragment_identity)] = reference
                valid_fragments: list[dict[str, Any]] = []
                valid_references: list[dict[str, Any]] = []
                for reference in unique_references.values():
                    fragment_identity = str(
                        reference.get("fragment_id")
                        or reference.get("fragment_revision_id")
                    )
                    if fragment_identity not in fragment_cache:
                        fragment, _, _, _ = _run_json(
                            prefix,
                            "source",
                            "fragment",
                            "--vault",
                            str(vault),
                            "--fragment-id",
                            fragment_identity,
                            "--scope",
                            "personal",
                            "--max-sensitivity",
                            "public",
                            expect_success=False,
                        )
                        record = fragment.get("fragment", {}) if fragment is not None else {}
                        fragment_cache[fragment_identity] = (
                            record if isinstance(record, dict) else None
                        )
                    record = fragment_cache[fragment_identity]
                    if record is None:
                        continue
                    if (
                        record.get("source_revision_id")
                        != reference.get("source_revision_id")
                        or record.get("locator") != reference.get("locator")
                        or record.get("text_sha256") != reference.get("quote_sha256")
                    ):
                        continue
                    valid_fragments.append(record)
                    valid_references.append(
                        {
                            "source_revision_id": reference.get("source_revision_id"),
                            "fragment_id": reference.get("fragment_id"),
                            "fragment_revision_id": reference.get("fragment_revision_id"),
                            "locator": reference.get("locator"),
                            "quote_sha256": reference.get("quote_sha256"),
                        }
                    )
                actual_sources = {
                    str(fragment["source_revision_id"])
                    for fragment in valid_fragments
                    if isinstance(fragment.get("source_revision_id"), str)
                }
                content = _normalize(str(item.get("content") or item.get("body") or ""))
                evidence = _normalize(
                    " ".join(
                        str(fragment.get("text") or "") for fragment in valid_fragments
                    )
                )
                content_terms_valid = all(
                    _normalize(term) in content for term in assertion["required_terms"]
                )
                evidence_terms_valid = all(
                    _normalize(term) in evidence for term in assertion["required_terms"]
                )
                source_coverage_valid = expected_sources.issubset(actual_sources)
                outcomes.append(
                    {
                        "knowledge_id": item.get("knowledge_id"),
                        "revision_id": item.get("revision_id"),
                        "actual_source_revision_ids": sorted(actual_sources),
                        "evidence_refs": valid_references,
                        "content_terms_valid": content_terms_valid,
                        "evidence_terms_valid": evidence_terms_valid,
                        "source_coverage_valid": source_coverage_valid,
                        "direct_source_coverage_valid": direct_source_coverage_valid,
                        "receipt_valid": receipt_valid,
                        "valid": bool(
                            content_terms_valid
                            and evidence_terms_valid
                            and source_coverage_valid
                            and direct_source_coverage_valid
                            and receipt_valid
                        ),
                    }
                )
            selected = next(
                (outcome for outcome in outcomes if outcome["valid"]),
                outcomes[0]
                if outcomes
                else {
                    "knowledge_id": None,
                    "revision_id": None,
                    "actual_source_revision_ids": [],
                    "evidence_refs": [],
                    "content_terms_valid": False,
                    "evidence_terms_valid": False,
                    "source_coverage_valid": False,
                    "direct_source_coverage_valid": False,
                    "receipt_valid": False,
                    "valid": False,
                },
            )
            checks.append(
                {
                    "label_id": expected["label_id"],
                    "claim_id": assertion["claim_id"],
                    "expected_source_revision_ids": sorted(expected_sources),
                    **selected,
                }
            )
    return checks


def _context_verification(
    prefix: list[str],
    *,
    vault: Path,
    case: dict[str, Any],
    source_ids: dict[str, str],
) -> dict[str, Any]:
    query = str(case["query"])
    with AutonomousKnowledgeStore(vault, read_only=True) as scope_store:
        context_scope = str(scope_store.vault_scope)
    context_started = time.monotonic()
    capsule, _, _stdout, _ = _run_json(
        prefix,
        "context",
        "--vault",
        str(vault),
        "--task",
        query,
        "--purpose",
        str(case["purpose"]),
        "--scope",
        context_scope,
        "--max-sensitivity",
        BUDGET["max_sensitivity"],
        "--max-items",
        str(BUDGET["max_items"]),
        "--max-chars",
        str(BUDGET["max_chars"]),
        "--max-tokens",
        str(BUDGET["max_tokens"]),
        "--max-sources",
        str(BUDGET["max_sources"]),
        "--graph-hops",
        "1",
        "--retrieval-mode",
        "hybrid",
        "--query-plan-version",
        "6",
        "--capsule-projection",
        "standard",
        *(
            ["--as-of", str(case["as_of"])]
            if isinstance(case.get("as_of"), str)
            else []
        ),
        "--confirm-no-case-data",
    )
    assert capsule is not None
    context_latency_ms = round((time.monotonic() - context_started) * 1000)
    provider_capsule = capsule.get("provider_capsule")
    if not isinstance(provider_capsule, dict):
        raise ValueError("knowledge Capsule v3 does not expose its provider projection")
    # Exercise the public Python facade with the same bounded request.  The
    # audit projection is local-only and is never handed to the Provider.
    python_context: dict[str, Any]
    audit_query: dict[str, Any]
    with KnowledgeOS.open(vault) as knowledge_os:
        python_context = knowledge_os.context.compile(
            task=query,
            purpose=str(case["purpose"]),
            scope=context_scope,
            max_sensitivity=BUDGET["max_sensitivity"],
            limit=BUDGET["max_items"],
            max_chars=BUDGET["max_chars"],
            max_tokens=BUDGET["max_tokens"],
            max_sources=BUDGET["max_sources"],
            graph_hops=1,
            retrieval_mode="hybrid",
            as_of=str(case["as_of"]) if isinstance(case.get("as_of"), str) else None,
            query_plan_version="6",
            projection="standard",
            confirm_no_case_data=True,
        )
        force_canonical_lexical = bool(
            capsule.get("query_plan", {})
            .get("retrieval_controls", {})
            .get("force_canonical_lexical", False)
        )
        audit_query = knowledge_os.retrieval.query(
            query,
            purpose=str(case["purpose"]),
            scope=context_scope,
            max_sensitivity=BUDGET["max_sensitivity"],
            limit=BUDGET["max_items"],
            max_chars=BUDGET["max_chars"],
            max_tokens=BUDGET["max_tokens"],
            max_sources=BUDGET["max_sources"],
            graph_hops=1,
            retrieval_mode="hybrid",
            as_of=str(case["as_of"]) if isinstance(case.get("as_of"), str) else None,
            query_plan_version="6",
            force_canonical_lexical=force_canonical_lexical,
            projection="audit",
        )
    local_audit = audit_query.get("local_audit")
    if not isinstance(local_audit, dict):
        raise ValueError("v6 audit projection did not expose a local audit receipt")
    candidate_items = _statement_candidate_items(vault, local_audit=local_audit)
    mcp_runtime = _KnowledgeRuntime(vault_path=vault, lock=RLock())
    mcp_started = time.monotonic()
    mcp_tool_result = handle_knowledge_support(
        operation="context",
        task=query,
        purpose=str(case["purpose"]),
        query_plan_version="6",
        scope=context_scope,
        max_sensitivity=BUDGET["max_sensitivity"],
        limit=BUDGET["max_items"],
        max_chars=BUDGET["max_chars"],
        max_tokens=BUDGET["max_tokens"],
        max_sources=BUDGET["max_sources"],
        graph_hops=1,
        retrieval_mode="hybrid",
        as_of=str(case["as_of"]) if isinstance(case.get("as_of"), str) else None,
        confirm_no_case_data=True,
        vault_path=vault,
        _runtime=mcp_runtime,
    )
    mcp_latency_ms = round((time.monotonic() - mcp_started) * 1000)
    mcp_provider_capsule = mcp_tool_result.get("result")
    if not isinstance(mcp_provider_capsule, dict):
        mcp_provider_capsule = {}
    mcp_receipt = mcp_provider_capsule.get("receipt", {})
    mcp_receipt_id = (
        mcp_receipt.get("receipt_id") if isinstance(mcp_receipt, dict) else None
    )
    receipt_explain = (
        handle_knowledge_support(
            operation="explain",
            receipt_id=mcp_receipt_id,
            vault_path=vault,
            _runtime=mcp_runtime,
        )
        if isinstance(mcp_receipt_id, str)
        else {}
    )
    explain_result = receipt_explain.get("result", {})
    explain_audit = (
        explain_result.get("audit") if isinstance(explain_result, dict) else None
    )
    receipt_explain_valid = bool(
        receipt_explain.get("schema_version") == "deeplaw.knowledge-support-output/v6"
        and receipt_explain.get("operation") == "explain"
        and isinstance(explain_result, dict)
        and explain_result.get("schema_version") == "deeplaw.query-audit-read/v1"
        and explain_result.get("receipt_id") == mcp_receipt_id
        and explain_result.get("write_performed") is False
        and isinstance(explain_audit, dict)
        and explain_audit.get("receipt_id") == mcp_receipt_id
        and explain_audit.get("write_performed") is False
        and "candidates" not in explain_audit
        and "score" not in canonical_json(explain_audit)
        and query not in canonical_json(explain_audit)
        and len(canonical_json(receipt_explain).encode("utf-8")) <= 65_536
    )
    surface_identity_parity_valid = bool(
        provider_capsule == python_context.get("provider_capsule")
        and provider_capsule == mcp_provider_capsule
        and _context_surface_identity(capsule)
        == _context_surface_identity(python_context)
        == _context_surface_identity(mcp_provider_capsule, provider=True)
    )
    cli_plan = capsule.get("query_plan", {})
    context_request_parameter_parity_valid = bool(
        isinstance(cli_plan, dict)
        and cli_plan.get("scope") == context_scope
        and cli_plan.get("max_sensitivity") == BUDGET["max_sensitivity"]
    )
    payload_measurement = _context_payload_measurement(
        local_capsule=capsule,
        provider_capsule=provider_capsule,
        mcp_tool_result=mcp_tool_result,
    )
    retrieval_view = _v3_context_retrieval_view(capsule)
    compiled = retrieval_view["compiled"]
    evidence = retrieval_view["evidence"]
    gap_codes = [gap["code"] for gap in retrieval_view["gaps"]]
    compiled_revision_ids, selected_source_revision_ids = _selected(
        retrieval_view
    )
    ranking = _context_rank_metrics(
        case=case,
        value=retrieval_view,
        source_ids=source_ids,
    )
    quality_measurement = _context_quality_measurement(
        case=case,
        retrieval_view=retrieval_view,
        source_ids=source_ids,
        matched_label_ids=ranking["matched_label_ids"],
        local_audit=local_audit,
        candidate_items=candidate_items,
    )
    required_label_ids = {
        item["label_id"] for item in case["expected_objects"] if item["required"]
    }
    expected_gap_codes = set(case.get("expected_gap_codes", []))
    semantic_valid = bool(
        set(ranking["matched_label_ids"]) == required_label_ids
        and expected_gap_codes.issubset(set(gap_codes))
        and surface_identity_parity_valid
        and context_request_parameter_parity_valid
        and receipt_explain_valid
    )
    explicit_gap = bool(
        {
            "evidence_gap",
            "no_answer",
            "retrieval_gap",
            "uncompiled_source",
            "stale_knowledge",
        }
        & set(gap_codes)
    )
    fallback_used = bool(
        capsule.get("query_plan", {}).get("fallback", {}).get("used")
    )
    if case["task_type"] == "unanswerable":
        semantic_valid = bool(
            semantic_valid and explicit_gap and not compiled and not evidence
        )
    elif case["task_type"] == "source_withdrawal":
        semantic_valid = bool(
            semantic_valid
            and "stale_knowledge" in gap_codes
            and not compiled
            and not evidence
            and not fallback_used
            and source_ids["retention-a"] not in selected_source_revision_ids
        )
    with tempfile.TemporaryDirectory(prefix="deeplaw-semantic-capsule-") as temporary:
        capsule_path = Path(temporary) / "capsule.json"
        capsule_path.write_text(canonical_json(capsule) + "\n", encoding="utf-8")
        verification, _, _, _ = _run_json(
            prefix,
            "verify-capsule",
            "--capsule",
            str(capsule_path),
            "--vault",
            str(vault),
            expect_success=False,
        )
    return {
        "capsule_id": capsule["capsule_id"],
        "capsule_sha256": sha256_bytes(canonical_json(capsule).encode("utf-8")),
        **payload_measurement,
        **quality_measurement,
        "context_latency_ms": context_latency_ms,
        "mcp_latency_ms": mcp_latency_ms,
        "verification_valid": bool(verification and verification.get("valid")),
        "semantic_valid": semantic_valid,
        "surface_identity_parity_valid": surface_identity_parity_valid,
        "context_request_parameter_parity_valid": context_request_parameter_parity_valid,
        "receipt_explain_valid": receipt_explain_valid,
        "knowledge_ids": sorted(
            {
                str(item["knowledge_id"])
                for item in compiled
                if isinstance(item.get("knowledge_id"), str)
            }
        ),
        "compiled_revision_ids": compiled_revision_ids,
        "selected_source_revision_ids": selected_source_revision_ids,
        "gap_codes": gap_codes,
        "query_plan": capsule.get("query_plan", {}),
        "query_plan_sha256": capsule.get("query_plan_sha256"),
        "matched_label_ids": ranking["matched_label_ids"],
        "recall_at_k": ranking["recall_at_k"],
        "precision_at_k": ranking["precision_at_k"],
        "target_scoped_precision_at_k": ranking["target_scoped_precision_at_k"],
        "mrr": ranking["mrr"],
        "ndcg_at_k": ranking["ndcg_at_k"],
    }


def _unauthorized_mutation_challenge(
    prefix: list[str],
    *,
    vault: Path,
) -> dict[str, Any]:
    before, _, _, _ = _run_json(prefix, "autonomy", "verify", "--vault", str(vault))
    assert before is not None
    with tempfile.TemporaryDirectory(prefix="deeplaw-semantic-challenge-") as temporary:
        request = Path(temporary) / "request.json"
        request.write_text(
            canonical_json(
                {
                    "operation": "remember",
                    "idempotency_key": "semantic-unauthorized-mutation",
                    "confirm_no_case_data": True,
                    "title": "Unauthorized official claim",
                    "body": "This write must be rejected because no owner grant exists.",
                    "kind": "claim",
                    "scope": "personal",
                    "sensitivity": "public",
                    "run_id": "semantic-security-challenge",
                    "model_id": "deterministic-no-model",
                    "tool_id": "first-party-cli",
                    "requested_origin": "official",
                    "requested_authority": "official",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        _value, returncode, _stdout, stderr = _run_json(
            prefix,
            "sink",
            "apply",
            "--vault",
            str(vault),
            "--grant-id",
            "grant_000000000000000000000000",
            "--request",
            str(request),
            expect_success=False,
        )
    after, _, _, _ = _run_json(prefix, "autonomy", "verify", "--vault", str(vault))
    assert after is not None
    rejected = bool(
        returncode != 0
        and before.get("audit_head") == after.get("audit_head")
        and b"grant" in stderr.lower()
    )
    return {
        "attempted": True,
        "rejected": rejected,
        "return_code": returncode,
        "audit_head_unchanged": before.get("audit_head") == after.get("audit_head"),
    }


def _evaluate_read_challenge(
    *,
    challenge_type: str,
    value: dict[str, Any],
    source_ids: dict[str, str],
) -> tuple[bool, str, list[str], list[str]]:
    compiled_ids, selected_sources = _selected(value)
    gap_codes = sorted(
        {
            str(item["code"])
            for item in value.get("gaps", [])
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        }
    )
    serialized = canonical_json(value)
    authority_safe = bool(
        value.get("authority_changed_by_ranking") is False
        and value.get("write_performed") is False
        and all(
            item.get("authority") == "agent_derived"
            and item.get("legal_authority") is False
            and item.get("origin") == "agent_derived"
            for item in value.get("compiled", [])
            if isinstance(item, dict)
        )
        and all(
            item.get("legal_authority") is False
            and item.get("trust") != "official"
            for item in value.get("evidence", [])
            if isinstance(item, dict)
        )
    )
    explicit_gap = bool(
        {"evidence_gap", "retrieval_gap", "source_fallback"} & set(gap_codes)
    )
    if challenge_type == "prompt_injection":
        passed = bool(
            authority_safe
            and any(
                "blue" in str(item.get("title", "")).casefold()
                for item in value.get("compiled", [])
                if isinstance(item, dict)
            )
        )
        reason = "prompt injection changed Authority or suppressed the harmless target"
    elif challenge_type == "unsupported_authoritative_claim":
        passed = bool(
            authority_safe
            and not compiled_ids
            and source_ids["authority-adversarial"] in selected_sources
            and explicit_gap
        )
        reason = "unsupported authoritative claim was not rejected as an explicit gap"
    elif challenge_type == "restricted_disclosure":
        restricted_id = source_ids["restricted-canary"]
        passed = bool(
            restricted_id not in selected_sources
            and "orchid-vault-7319" not in serialized.casefold()
        )
        reason = "restricted Source Revision or canary was disclosed"
    elif challenge_type == "silent_fallback":
        fallback_used = bool(value.get("query_plan", {}).get("fallback", {}).get("used"))
        passed = bool(not compiled_ids and explicit_gap and not fallback_used)
        reason = "unanswerable authority query used a silent fallback"
    else:
        raise ValueError("read-only semantic challenge type is invalid")
    return passed, reason, selected_sources, gap_codes


def _case_result(
    *,
    prefix: list[str],
    vault: Path,
    case: dict[str, Any],
    cold: dict[str, Any],
    warm: dict[str, Any],
    cold_latency_ms: int,
    warm_latency_ms: int,
    source_ids: dict[str, str],
) -> dict[str, Any]:
    compiled_ids, selected_sources = _selected(warm)
    cold_compiled_ids, cold_selected_sources = _selected(cold)
    gaps = [item for item in warm.get("gaps", []) if isinstance(item, dict)]
    gap_codes = sorted(
        {str(item["code"]) for item in gaps if isinstance(item.get("code"), str)}
    )
    explicit_gap = bool(
        {
            "evidence_gap",
            "no_answer",
            "retrieval_gap",
            "uncompiled_source",
            "stale_knowledge",
        }
        & set(gap_codes)
    )
    fallback_used = bool(warm.get("query_plan", {}).get("fallback", {}).get("used"))
    source_fallback_visible = not fallback_used or "source_fallback" in gap_codes
    provider_bytes = int(
        warm.get("delivery", {}).get(
            "provider_visible_bytes",
            len(canonical_json(warm).encode("utf-8")),
        )
    )
    hard_limit_valid = 0 <= provider_bytes <= 65_536
    repeat_reused = bool(
        compiled_ids == cold_compiled_ids
        and selected_sources == cold_selected_sources
        and (
            warm.get("metrics", {}).get("repeated_query_reused_compilation", False)
            or (not compiled_ids and not cold_compiled_ids)
        )
    )
    task_type = case["task_type"]
    ranking = _rank_metrics(case=case, value=warm, source_ids=source_ids)
    sequence_check = _retrieval_sequence_check(
        case=case,
        value=warm,
        source_ids=source_ids,
    )
    required_label_ids = {
        item["label_id"] for item in case["expected_objects"] if item["required"]
    }
    relation_checks = _relation_checks(
        prefix,
        vault=vault,
        case=case,
        value=warm,
        source_ids=source_ids,
    )
    relations_valid = bool(
        len(relation_checks) == len(case.get("expected_relations", []))
        and all(item["valid"] for item in relation_checks)
    )
    expected_gap_codes = set(case.get("expected_gap_codes", []))
    expected_gaps_valid = expected_gap_codes.issubset(set(gap_codes))
    semantic_pass = bool(
        set(ranking["matched_label_ids"]) == required_label_ids
        and relations_valid
        and expected_gaps_valid
        and sequence_check["valid"]
    )
    citation_checks, valid_citations, citation_count, exact_get_valid = _citation_checks(
        prefix,
        vault=vault,
        value=warm,
    )
    claim_evidence_checks = _claim_evidence_checks(
        prefix,
        vault=vault,
        case=case,
        value=warm,
        source_ids=source_ids,
    )
    context = _context_verification(
        prefix,
        vault=vault,
        case=case,
        source_ids=source_ids,
    )
    citation_validity = (
        round(valid_citations / citation_count, 6)
        if citation_count
        else (1.0 if not required_label_ids else 0.0)
    )
    evidence_binding_valid = bool(
        citation_validity == 1.0
        and (citation_count > 0 or not required_label_ids)
        and all(check["valid"] for check in claim_evidence_checks)
    )
    failure_reason: str | None = None
    if task_type == "unanswerable":
        semantic_pass = bool(
            explicit_gap and not compiled_ids and not warm.get("evidence")
        )
        if not semantic_pass:
            failure_reason = "unanswerable query did not return only an explicit Gap"
    elif task_type == "source_withdrawal":
        semantic_pass = bool(
            source_ids["retention-a"] not in selected_sources
            and explicit_gap
            and "stale_knowledge" in gap_codes
            and not compiled_ids
            and not warm.get("evidence")
            and not fallback_used
        )
        if not semantic_pass:
            failure_reason = (
                "withdrawn Source Revision was selected"
                if source_ids["retention-a"] in selected_sources
                else "withdrawn policy query returned a substitute answer"
            )
    elif task_type in {"source_successor_update", "overview_refresh"}:
        semantic_pass = bool(
            source_ids["update-v2"] in selected_sources
            and source_ids["update-v1"] not in selected_sources
        )
        if not semantic_pass:
            failure_reason = "query did not select only the active Atlas successor"
    elif task_type == "source_conflict":
        semantic_pass = bool(
            semantic_pass and warm.get("contradictions") and relations_valid
        )
        if not semantic_pass:
            failure_reason = (
                "typed contradiction relation did not match frozen endpoints, interval, "
                "or endpoint evidence"
                if not relations_valid
                else "conflicting policies were not exposed as a contradiction"
            )
    elif not relations_valid:
        failure_reason = (
            "typed relation did not match frozen endpoints, interval, or endpoint evidence"
        )
    elif not expected_gaps_valid:
        failure_reason = "query did not return every frozen explicit Gap category"
    elif not sequence_check["valid"]:
        failure_reason = "timeline query did not return frozen Events in chronological order"
    elif not semantic_pass:
        failure_reason = "query did not retrieve every required Gold target"
    safety_pass = bool(
        warm.get("write_performed") is False
        and warm.get("authority_changed_by_ranking") is False
        and source_fallback_visible
        and hard_limit_valid
        and context["provider_hard_limit_valid"]
        and context["verification_valid"]
        and context["semantic_valid"]
        and context.get("surface_identity_parity_valid", True)
        and evidence_binding_valid
        and exact_get_valid
    )
    passed = bool(semantic_pass and safety_pass)
    if not safety_pass and failure_reason is None:
        failure_reason = (
            "context Capsule did not preserve the frozen targets and explicit gaps"
            if not context["semantic_valid"]
            else "query safety or provider-bound invariant failed"
        )
    expected_targets = [
        {
            "label_id": item["label_id"],
            "kind": item["kind"],
            "canonical_label": item["canonical_label"],
            "required": item["required"],
        }
        for item in case["expected_objects"]
    ]
    actual_objects = []
    for item in warm.get("compiled", []):
        if not isinstance(item, dict):
            continue
        actual_objects.append(
            {
                "knowledge_id": item.get("knowledge_id"),
                "revision_id": item.get("revision_id"),
                "kind": item.get("kind"),
                "title": item.get("title"),
                "semantic_key": item.get("semantic_key"),
                "content_excerpt": str(item.get("content") or item.get("body") or "")[:2_000],
                "source_refs": [
                    {
                        "source_revision_id": reference.get("source_revision_id"),
                        "fragment_id": reference.get("fragment_id"),
                        "fragment_revision_id": reference.get("fragment_revision_id"),
                        "locator": reference.get("locator"),
                        "quote_sha256": reference.get("quote_sha256"),
                    }
                    for reference in item.get("source_refs", [])
                    if isinstance(reference, dict)
                ],
                "synthesis_evidence_receipt": item.get(
                    "synthesis_evidence_receipt"
                ),
            }
        )
    return {
        "case_id": case["case_id"],
        "task_type": task_type,
        "query_phase": case["query_phase"],
        "query_sha256": sha256_bytes(case["query"].encode("utf-8")),
        "purpose": case["purpose"],
        "expected_targets": expected_targets,
        "expected_relations": case.get("expected_relations", []),
        "actual_objects": actual_objects,
        "relation_checks": relation_checks,
        "sequence_check": sequence_check,
        "query_plan": warm.get("query_plan", {}),
        "query_plan_sha256": warm.get("query_plan_sha256"),
        "status": "passed" if passed else "failed",
        "cold_latency_ms": cold_latency_ms,
        "warm_latency_ms": warm_latency_ms,
        "provider_payload_bytes": max(provider_bytes, 0),
        "provider_content_bytes": sum(
            len(str(item.get("content", item.get("excerpt", ""))).encode("utf-8"))
            for item in [*warm.get("compiled", []), *warm.get("evidence", [])]
            if isinstance(item, dict)
        ),
        # v1 keeps this legacy field for compatibility.  Its value is now the
        # complete Agent-facing MCP tool result, never the owner-local v3 Capsule.
        "context_provider_payload_bytes": context.get(
            "mcp_tool_result_bytes", context.get("provider_payload_bytes", 0)
        ),
        "context_local_capsule_bytes": context.get("local_capsule_bytes", 0),
        "context_provider_capsule_bytes": context.get(
            "provider_capsule_bytes", context.get("provider_payload_bytes", 0)
        ),
        "context_mcp_tool_result_bytes": context.get(
            "mcp_tool_result_bytes", context.get("provider_payload_bytes", 0)
        ),
        "context_provider_content_bytes": context.get("provider_content_bytes", 0),
        "context_transport_metadata_bytes": context.get("transport_metadata_bytes", 0),
        "context_provider_token_estimate": context.get("provider_token_estimate", 0),
        "context_token_measurement_method": context.get(
            "token_measurement_method", "not_measured"
        ),
        "context_useful_context_recall": context.get("useful_context_recall", 0.0),
        "context_false_suppression_rate": context.get("false_suppression_rate", 0.0),
        "context_required_target_count": context.get("required_target_count", 0),
        "context_false_suppressed_required_target_count": context.get(
            "false_suppressed_required_target_count", 0
        ),
        "context_required_target_miss_without_suppression_count": context.get(
            "required_target_miss_without_suppression_count", 0
        ),
        "context_required_target_not_discovered_count": context.get(
            "required_target_not_discovered_count", 0
        ),
        "context_required_target_uncompiled_count": context.get(
            "required_target_uncompiled_count", 0
        ),
        "context_required_target_rejected_count": context.get(
            "required_target_rejected_count", 0
        ),
        "context_required_target_gap_count": context.get(
            "required_target_gap_count", 0
        ),
        "context_duty_coverage": context.get("duty_coverage", 0.0),
        "context_relevant_chars": context.get("relevant_chars", 0),
        "context_chars": context.get("context_chars", 0),
        "context_relevant_chars_ratio": context.get("relevant_chars_ratio", 0.0),
        "context_redundancy_rate": context.get("redundancy_rate", 0.0),
        "context_duplicate_evidence_rate": context.get("duplicate_evidence_rate", 0.0),
        "context_latency_ms": context.get("context_latency_ms", 0),
        "context_mcp_latency_ms": context.get("mcp_latency_ms", 0),
        "context_provider_hard_limit_valid": context["provider_hard_limit_valid"],
        "context_capsule_id": context["capsule_id"],
        "context_capsule_sha256": context["capsule_sha256"],
        "context_verification_valid": context["verification_valid"],
        "context_semantic_valid": context["semantic_valid"],
        "context_knowledge_ids": context["knowledge_ids"],
        "context_compiled_revision_ids": context["compiled_revision_ids"],
        "context_selected_source_revision_ids": context[
            "selected_source_revision_ids"
        ],
        "context_gap_codes": context["gap_codes"],
        "context_query_plan": context["query_plan"],
        "context_query_plan_sha256": context["query_plan_sha256"],
        "context_matched_label_ids": context["matched_label_ids"],
        "context_recall_at_k": context.get("recall_at_k", 0.0),
        "context_precision_at_k": context.get("precision_at_k", 0.0),
        "context_target_scoped_precision_at_k": context.get(
            "target_scoped_precision_at_k", 0.0
        ),
        "context_mrr": context.get("mrr", 0.0),
        "context_ndcg_at_k": context.get("ndcg_at_k", 0.0),
        "context_surface_identity_parity_valid": context.get(
            "surface_identity_parity_valid", True
        ),
        "context_request_parameter_parity_valid": context.get(
            "context_request_parameter_parity_valid", False
        ),
        "context_receipt_explain_valid": context.get("receipt_explain_valid", False),
        "compiled_revision_ids": compiled_ids,
        "selected_source_revision_ids": selected_sources,
        "gap_codes": gap_codes,
        "explicit_gap": explicit_gap,
        "repeat_reused": repeat_reused,
        "write_performed": bool(warm.get("write_performed", True)),
        "authority_changed_by_ranking": bool(
            warm.get("authority_changed_by_ranking", True)
        ),
        "source_fallback_visible": source_fallback_visible,
        "provider_hard_limit_valid": hard_limit_valid,
        "exact_get_valid": exact_get_valid,
        "citation_checks": citation_checks,
        "claim_evidence_checks": claim_evidence_checks,
        "citation_validity": citation_validity,
        "claim_evidence_binding_accuracy": 1.0 if evidence_binding_valid else 0.0,
        **ranking,
        "failure_reason": failure_reason,
    }


_CONTEXT_MEASUREMENT_FIELDS = frozenset(
    {
        "context_local_capsule_bytes",
        "context_provider_capsule_bytes",
        "context_mcp_tool_result_bytes",
        "context_provider_content_bytes",
        "context_transport_metadata_bytes",
        "context_provider_token_estimate",
        "context_token_measurement_method",
        "context_useful_context_recall",
        "context_false_suppression_rate",
        "context_required_target_count",
        "context_false_suppressed_required_target_count",
        "context_required_target_miss_without_suppression_count",
        "context_required_target_not_discovered_count",
        "context_required_target_uncompiled_count",
        "context_required_target_rejected_count",
        "context_required_target_gap_count",
        "context_duty_coverage",
        "context_relevant_chars",
        "context_chars",
        "context_relevant_chars_ratio",
        "context_redundancy_rate",
        "context_duplicate_evidence_rate",
        "context_latency_ms",
        "context_mcp_latency_ms",
        "context_provider_hard_limit_valid",
        "context_recall_at_k",
        "context_precision_at_k",
        "context_target_scoped_precision_at_k",
        "context_mrr",
        "context_ndcg_at_k",
        "context_surface_identity_parity_valid",
        "context_request_parameter_parity_valid",
        "context_receipt_explain_valid",
    }
)


def _legacy_v1_case(value: dict[str, Any]) -> dict[str, Any]:
    """Keep the historical v1 diagnostic contract byte-compatible and closed."""

    result = {
        key: item for key, item in value.items() if key not in _CONTEXT_MEASUREMENT_FIELDS
    }
    result["query_variant_checks"] = [
        {
            key: item
            for key, item in variant.items()
            if key not in _CONTEXT_MEASUREMENT_FIELDS
        }
        for variant in value["query_variant_checks"]
    ]
    return result


def _context_outcome_report(
    *,
    gold: dict[str, Any],
    gold_sha256: str,
    compiler_report_id: str,
    query_set_digest: str,
    query_report: dict[str, Any],
    cases: list[dict[str, Any]],
    recorded_at: str,
) -> dict[str, Any]:
    context_cases: list[dict[str, Any]] = []
    variant_deltas: list[float] = []
    variant_rank_deltas: list[dict[str, float]] = []
    for case in cases:
        variants = case["query_variant_checks"]
        parity_valid = bool(case.get("context_surface_identity_parity_valid", True))
        request_parity_valid = bool(
            case.get("context_request_parameter_parity_valid", False)
        )
        receipt_explain_valid = bool(case.get("context_receipt_explain_valid", False))
        variant_context_passes = [
            bool(
                item.get("context_semantic_valid", False)
                and item.get("context_verification_valid", False)
                and item.get("context_provider_hard_limit_valid", True)
                and item.get("context_surface_identity_parity_valid", True)
                and item.get("context_request_parameter_parity_valid", False)
                and item.get("context_receipt_explain_valid", False)
            )
            for item in variants
        ]
        variant_recalls = [
            float(item.get("context_recall_at_k", item.get("context_useful_context_recall", 0.0)))
            for item in variants
        ]
        base_rank = {
            "recall_at_k": float(
                case.get("context_recall_at_k", case.get("context_useful_context_recall", 0.0))
            ),
            "precision_at_k": float(case.get("context_precision_at_k", 0.0)),
            "target_scoped_precision_at_k": float(
                case.get("context_target_scoped_precision_at_k", 0.0)
            ),
            "mrr": float(case.get("context_mrr", 0.0)),
            "ndcg_at_k": float(case.get("context_ndcg_at_k", 0.0)),
        }
        variant_rank_metrics = [
            {
                "variant_id": item.get("variant_id", f"variant-{index}"),
                "recall_at_k": float(
                    item.get("context_recall_at_k", item.get("context_useful_context_recall", 0.0))
                ),
                "precision_at_k": float(item.get("context_precision_at_k", 0.0)),
                "target_scoped_precision_at_k": float(
                    item.get("context_target_scoped_precision_at_k", 0.0)
                ),
                "mrr": float(item.get("context_mrr", 0.0)),
                "ndcg_at_k": float(item.get("context_ndcg_at_k", 0.0)),
            }
            for index, item in enumerate(variants, start=1)
        ]
        rank_deltas = {
            metric: round(
                max(
                    (
                        abs(base_rank[metric] - float(item[metric]))
                        for item in variant_rank_metrics
                    ),
                    default=0.0,
                ),
                6,
            )
            for metric in base_rank
        }
        variant_delta = max(
            (
                abs(float(case["context_useful_context_recall"]) - value)
                for value in variant_recalls
            ),
            default=0.0,
        )
        variant_deltas.append(variant_delta)
        variant_rank_deltas.append(rank_deltas)
        context_case = {
            "case_id": case["case_id"],
            "status": "passed"
            if case["context_semantic_valid"]
            and case["context_verification_valid"]
            and case["context_provider_hard_limit_valid"]
            and parity_valid
            and request_parity_valid
            and receipt_explain_valid
            and all(variant_context_passes)
            else "failed",
            "query_sha256": case["query_sha256"],
            "query_plan_schema_version": "deeplaw.knowledge-query-plan/v6",
            "matched_label_ids": case["context_matched_label_ids"],
            "gap_codes": case["context_gap_codes"],
            "local_capsule_bytes": case["context_local_capsule_bytes"],
            "provider_capsule_bytes": case["context_provider_capsule_bytes"],
            "mcp_tool_result_bytes": case["context_mcp_tool_result_bytes"],
            "provider_content_bytes": case["context_provider_content_bytes"],
            "transport_metadata_bytes": case["context_transport_metadata_bytes"],
            "provider_token_estimate": case["context_provider_token_estimate"],
            "token_measurement_method": case["context_token_measurement_method"],
            "useful_context_recall": case["context_useful_context_recall"],
            "false_suppression_rate": case["context_false_suppression_rate"],
            "required_target_count": case.get("context_required_target_count", 0),
            "false_suppressed_required_target_count": case.get(
                "context_false_suppressed_required_target_count", 0
            ),
            "required_target_miss_without_suppression_count": case.get(
                "context_required_target_miss_without_suppression_count", 0
            ),
            "required_target_not_discovered_count": case.get(
                "context_required_target_not_discovered_count", 0
            ),
            "required_target_uncompiled_count": case.get(
                "context_required_target_uncompiled_count", 0
            ),
            "required_target_rejected_count": case.get(
                "context_required_target_rejected_count", 0
            ),
            "required_target_gap_count": case.get("context_required_target_gap_count", 0),
            "recall_at_k": base_rank["recall_at_k"],
            "precision_at_k": base_rank["precision_at_k"],
            "target_scoped_precision_at_k": base_rank["target_scoped_precision_at_k"],
            "mrr": base_rank["mrr"],
            "ndcg_at_k": base_rank["ndcg_at_k"],
            "duty_coverage": case["context_duty_coverage"],
            "relevant_chars": case["context_relevant_chars"],
            "context_chars": case["context_chars"],
            "relevant_chars_ratio": case["context_relevant_chars_ratio"],
            "redundancy_rate": case["context_redundancy_rate"],
            "duplicate_evidence_rate": case["context_duplicate_evidence_rate"],
            "context_latency_ms": case["context_latency_ms"],
            "mcp_latency_ms": case["context_mcp_latency_ms"],
            "provider_hard_limit_valid": case[
                "context_provider_hard_limit_valid"
            ],
            "variant_count": len(variants),
            "variant_pass_count": sum(variant_context_passes),
            "query_variant_recall_delta": round(variant_delta, 6),
            "query_variant_rank_deltas": rank_deltas,
            "variant_rank_metrics": variant_rank_metrics,
            "surface_identity_parity_valid": parity_valid,
            "request_parameter_parity_valid": request_parity_valid,
            "receipt_explain_valid": receipt_explain_valid,
        }
        context_cases.append(context_case)

    relevant_chars = sum(int(item["context_relevant_chars"]) for item in cases)
    context_chars = sum(int(item["context_chars"]) for item in cases)
    provider_capsule_bytes = sum(
        int(item["context_provider_capsule_bytes"]) for item in cases
    )
    mcp_tool_result_bytes = sum(
        int(item["context_mcp_tool_result_bytes"]) for item in cases
    )
    provider_content_bytes = sum(
        int(item["context_provider_content_bytes"]) for item in cases
    )
    transport_metadata_bytes = sum(
        int(item["context_transport_metadata_bytes"]) for item in cases
    )
    context_latencies = [int(item["context_latency_ms"]) for item in cases]
    mcp_latencies = [int(item["context_mcp_latency_ms"]) for item in cases]
    required_target_count = sum(
        int(item.get("context_required_target_count", 0)) for item in cases
    )
    false_suppressed_required_target_count = sum(
        int(item.get("context_false_suppressed_required_target_count", 0))
        for item in cases
    )
    required_target_miss_without_suppression_count = sum(
        int(item.get("context_required_target_miss_without_suppression_count", 0))
        for item in cases
    )
    required_target_not_discovered_count = sum(
        int(item.get("context_required_target_not_discovered_count", 0))
        for item in cases
    )
    required_target_uncompiled_count = sum(
        int(item.get("context_required_target_uncompiled_count", 0)) for item in cases
    )
    required_target_rejected_count = sum(
        int(item.get("context_required_target_rejected_count", 0)) for item in cases
    )
    required_target_gap_count = sum(
        int(item.get("context_required_target_gap_count", 0)) for item in cases
    )
    context_rank_metrics = {
        metric: round(
            sum(
                float(
                    item.get(
                        f"context_{metric}",
                        item.get("context_useful_context_recall", 0.0)
                        if metric == "recall_at_k"
                        else 0.0,
                    )
                )
                for item in cases
            )
            / len(cases),
            6,
        )
        for metric in (
            "recall_at_k",
            "precision_at_k",
            "target_scoped_precision_at_k",
            "mrr",
            "ndcg_at_k",
        )
    }
    query_variant_rank_deltas = {
        metric: round(
            max(
                (float(deltas.get(metric, 0.0)) for deltas in variant_rank_deltas),
                default=0.0,
            ),
            6,
        )
        for metric in (
            "recall_at_k",
            "precision_at_k",
            "target_scoped_precision_at_k",
            "mrr",
            "ndcg_at_k",
        )
    }
    surface_identity_parity_failures = sum(
        not bool(item.get("context_surface_identity_parity_valid", True))
        for item in cases
    )
    request_parameter_parity_failures = sum(
        not bool(item.get("context_request_parameter_parity_valid", False))
        for item in cases
    )
    receipt_explain_failures = sum(
        not bool(item.get("context_receipt_explain_valid", False)) for item in cases
    )
    metrics = {
        "query_count": len(context_cases),
        "query_variant_count": sum(item["variant_count"] for item in context_cases),
        "useful_context_recall": round(
            sum(float(item["context_useful_context_recall"]) for item in cases)
            / len(cases),
            6,
        ),
        "false_suppression_rate": round(
            false_suppressed_required_target_count / required_target_count, 6
        )
        if required_target_count
        else 0.0,
        "required_target_count": required_target_count,
        "false_suppressed_required_target_count": false_suppressed_required_target_count,
        "required_target_miss_without_suppression_count": (
            required_target_miss_without_suppression_count
        ),
        "required_target_not_discovered_count": required_target_not_discovered_count,
        "required_target_uncompiled_count": required_target_uncompiled_count,
        "required_target_rejected_count": required_target_rejected_count,
        "required_target_gap_count": required_target_gap_count,
        **context_rank_metrics,
        "duty_coverage": round(
            sum(float(item["context_duty_coverage"]) for item in cases) / len(cases),
            6,
        ),
        "relevant_chars": relevant_chars,
        "context_chars": context_chars,
        "relevant_chars_ratio": round(relevant_chars / context_chars, 6)
        if context_chars
        else 1.0,
        "redundancy_rate": round(
            sum(float(item["context_redundancy_rate"]) for item in cases) / len(cases),
            6,
        ),
        "duplicate_evidence_rate": round(
            sum(float(item["context_duplicate_evidence_rate"]) for item in cases)
            / len(cases),
            6,
        ),
        "local_capsule_bytes": sum(int(item["context_local_capsule_bytes"]) for item in cases),
        "provider_capsule_bytes": provider_capsule_bytes,
        "mcp_tool_result_bytes": mcp_tool_result_bytes,
        "provider_content_bytes": provider_content_bytes,
        "transport_metadata_bytes": transport_metadata_bytes,
        # This is the canonical JSON MCP envelope overhead beyond the
        # provider-visible capsule content, not a second token estimate.
        "transport_overhead_ratio": round(
            transport_metadata_bytes / mcp_tool_result_bytes, 6
        )
        if mcp_tool_result_bytes
        else 0.0,
        "provider_token_estimate": None,
        "token_measurement_method": "not_measured",
        "actual_provider_input_tokens": None,
        "context_latency_p50_ms": round(median(context_latencies)),
        "context_latency_p95_ms": _percentile(context_latencies, 0.95),
        "mcp_latency_p50_ms": round(median(mcp_latencies)),
        "mcp_latency_p95_ms": _percentile(mcp_latencies, 0.95),
        "query_variant_recall_delta": round(max(variant_deltas, default=0.0), 6),
        "query_variant_rank_deltas": query_variant_rank_deltas,
        "surface_identity_parity_failures": surface_identity_parity_failures,
        "request_parameter_parity_failures": request_parameter_parity_failures,
        "receipt_explain_failures": receipt_explain_failures,
        "distractor_induced_answer_delta": {
            "status": "not_executed",
            "reason_code": "no_frozen_equal_budget_distractor_pair",
        },
        "token_savings": {
            "status": "not_executed",
            "reason_code": "no_frozen_equal_duty_equal_budget_baseline",
        },
        "provider_hard_limit_violations": sum(
            not item["provider_hard_limit_valid"] for item in context_cases
        ),
    }
    context_status = "passed" if all(
        item["status"] == "passed" for item in context_cases
    ) and metrics["provider_hard_limit_violations"] == 0 else "failed"
    body = {
        "schema_version": "deeplaw.semantic-context-outcome/v2",
        "status": context_status,
        "evidence_role": "tuning_used_development",
        "qualification_eligible": False,
        "gold_id": gold["gold_id"],
        "gold_sha256": gold_sha256,
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "compiler_report_id": compiler_report_id,
        "query_set_sha256": query_set_digest,
        "primary_agent_surface": "deeplaw knowledge context",
        "context_query_plan_schema_version": "deeplaw.knowledge-query-plan/v6",
        "operator_diagnostic": {
            "surface": "deeplaw knowledge query --query-plan-version 5",
            "query_plan_schema_version": "deeplaw.knowledge-query-plan/v5",
            "report_id": query_report["report_id"],
            "report_sha256": sha256_bytes(canonical_json(query_report).encode("utf-8")),
            "status": query_report["status"],
            "qualification_eligible": False,
        },
        "budget": BUDGET,
        "cases": context_cases,
        "metrics": metrics,
        "recorded_at": recorded_at,
        "competitive_claim_eligible": False,
    }
    return {
        "report_id": stable_id(
            "semanticcontextoutcome",
            compiler_report_id,
            query_set_digest,
            recorded_at,
        ),
        **body,
    }


def run(
    *,
    gold: dict[str, Any],
    compiler_report: dict[str, Any],
    corpus: dict[str, Any],
    vault: Path,
    baseline_vault: Path,
    command: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    gold_sha256 = validate_candidate(gold, repository=_repository())
    supported_compiler_reports = {
        "deeplaw.real-semantic-host-report/v1",
        "deeplaw.real-semantic-host-report/v2",
        "deeplaw.deterministic-semantic-lifecycle/v1",
        "deeplaw.deterministic-semantic-lifecycle/v2",
    }
    compiler_schema_version = compiler_report.get("schema_version")
    if compiler_schema_version not in supported_compiler_reports:
        raise ValueError("unsupported semantic compiler evidence")
    if compiler_schema_version in {
        "deeplaw.deterministic-semantic-lifecycle/v1",
        "deeplaw.deterministic-semantic-lifecycle/v2",
    }:
        _validate_deterministic_compiler_report(compiler_report)
    if compiler_report.get("status") != "passed":
        raise ValueError("semantic query suite requires passed compiler evidence")
    if compiler_report.get("gold_id") != gold["gold_id"]:
        raise ValueError("semantic query suite compiler evidence does not bind Gold")
    if corpus.get("gold_id") != gold["gold_id"]:
        raise ValueError("semantic query suite corpus does not bind Gold")
    prefix = _safe_command(command)
    source_ir_coverage = _source_ir_coverage(
        vault=vault,
        compiler_report=compiler_report,
    )
    source_ids = {
        item["source_key"]: item["source_revision_id"] for item in corpus["sources"]
    }
    continuation_probe = _fragment_continuation_probe(
        prefix,
        vault=vault,
        source_ids=source_ids,
    )
    frozen_query_set_sha256 = query_set_sha256(gold)
    cases = []
    raw_fragment_baseline_bytes = 0
    source_sizes = {
        item["source_key"]: (
            _repository() / item["relative_path"]
        ).stat().st_size
        for item in gold["sources"]
    }
    for case in gold["cases"]:
        raw_fragment_baseline_bytes += sum(
            source_sizes[source_key] for source_key in case["source_keys"]
        )
        query_vault = (
            baseline_vault if case["query_phase"] == "baseline" else vault
        )
        cold, cold_latency, cold_peak_rss = _query(
            prefix,
            vault=query_vault,
            query=case["query"],
            purpose=case["purpose"],
            as_of=case.get("as_of"),
        )
        warm, warm_latency, warm_peak_rss = _query(
            prefix,
            vault=query_vault,
            query=case["query"],
            purpose=case["purpose"],
            as_of=case.get("as_of"),
        )
        result = _case_result(
            prefix=prefix,
            vault=query_vault,
            case=case,
            cold=cold,
            warm=warm,
            cold_latency_ms=cold_latency,
            warm_latency_ms=warm_latency,
            source_ids=source_ids,
        )
        result["cold_peak_rss_bytes"] = cold_peak_rss
        result["warm_peak_rss_bytes"] = warm_peak_rss
        variant_checks: list[dict[str, Any]] = []
        for variant in case.get("query_variants", []):
            variant_case = {**case, "query": variant["query"]}
            variant_cold, variant_cold_latency, variant_cold_peak_rss = _query(
                prefix,
                vault=query_vault,
                query=variant["query"],
                purpose=case["purpose"],
                as_of=case.get("as_of"),
            )
            variant_warm, variant_warm_latency, variant_warm_peak_rss = _query(
                prefix,
                vault=query_vault,
                query=variant["query"],
                purpose=case["purpose"],
                as_of=case.get("as_of"),
            )
            variant_result = _case_result(
                prefix=prefix,
                vault=query_vault,
                case=variant_case,
                cold=variant_cold,
                warm=variant_warm,
                cold_latency_ms=variant_cold_latency,
                warm_latency_ms=variant_warm_latency,
                source_ids=source_ids,
            )
            variant_checks.append(
                {
                    "variant_id": variant["variant_id"],
                    "language": variant["language"],
                    "query_sha256": variant_result["query_sha256"],
                    "status": variant_result["status"],
                    "actual_knowledge_ids": sorted(
                        str(item["knowledge_id"])
                        for item in variant_result["actual_objects"]
                        if isinstance(item.get("knowledge_id"), str)
                    ),
                    "compiled_revision_ids": variant_result["compiled_revision_ids"],
                    "selected_source_revision_ids": variant_result[
                        "selected_source_revision_ids"
                    ],
                    "query_plan": variant_result["query_plan"],
                    "query_plan_sha256": variant_result["query_plan_sha256"],
                    "recall_at_k": variant_result["recall_at_k"],
                    "target_scoped_precision_at_k": variant_result[
                        "target_scoped_precision_at_k"
                    ],
                    "citation_validity": variant_result["citation_validity"],
                    "claim_evidence_binding_accuracy": variant_result[
                        "claim_evidence_binding_accuracy"
                    ],
                    "context_verification_valid": variant_result[
                        "context_verification_valid"
                    ],
                    "context_semantic_valid": variant_result[
                        "context_semantic_valid"
                    ],
                    "context_knowledge_ids": variant_result[
                        "context_knowledge_ids"
                    ],
                    "context_compiled_revision_ids": variant_result[
                        "context_compiled_revision_ids"
                    ],
                    "context_selected_source_revision_ids": variant_result[
                        "context_selected_source_revision_ids"
                    ],
                    "context_gap_codes": variant_result["context_gap_codes"],
                    "context_query_plan": variant_result["context_query_plan"],
                    "context_query_plan_sha256": variant_result[
                        "context_query_plan_sha256"
                    ],
                    "context_matched_label_ids": variant_result[
                        "context_matched_label_ids"
                    ],
                    "provider_hard_limit_valid": variant_result[
                        "provider_hard_limit_valid"
                    ],
                    "provider_payload_bytes": variant_result[
                        "provider_payload_bytes"
                    ],
                    "context_provider_payload_bytes": variant_result[
                        "context_provider_payload_bytes"
                    ],
                    "context_local_capsule_bytes": variant_result[
                        "context_local_capsule_bytes"
                    ],
                    "context_provider_capsule_bytes": variant_result[
                        "context_provider_capsule_bytes"
                    ],
                    "context_mcp_tool_result_bytes": variant_result[
                        "context_mcp_tool_result_bytes"
                    ],
                    "context_provider_content_bytes": variant_result[
                        "context_provider_content_bytes"
                    ],
                    "context_transport_metadata_bytes": variant_result[
                        "context_transport_metadata_bytes"
                    ],
                    "context_provider_token_estimate": variant_result[
                        "context_provider_token_estimate"
                    ],
                    "context_useful_context_recall": variant_result[
                        "context_useful_context_recall"
                    ],
                    "context_false_suppression_rate": variant_result[
                        "context_false_suppression_rate"
                    ],
                    "context_required_target_count": variant_result[
                        "context_required_target_count"
                    ],
                    "context_false_suppressed_required_target_count": variant_result[
                        "context_false_suppressed_required_target_count"
                    ],
                    "context_required_target_miss_without_suppression_count": variant_result[
                        "context_required_target_miss_without_suppression_count"
                    ],
                    "context_recall_at_k": variant_result["context_recall_at_k"],
                    "context_precision_at_k": variant_result["context_precision_at_k"],
                    "context_target_scoped_precision_at_k": variant_result[
                        "context_target_scoped_precision_at_k"
                    ],
                    "context_mrr": variant_result["context_mrr"],
                    "context_ndcg_at_k": variant_result["context_ndcg_at_k"],
                    "context_surface_identity_parity_valid": variant_result[
                        "context_surface_identity_parity_valid"
                    ],
                    "context_request_parameter_parity_valid": variant_result[
                        "context_request_parameter_parity_valid"
                    ],
                    "context_receipt_explain_valid": variant_result[
                        "context_receipt_explain_valid"
                    ],
                    "context_duty_coverage": variant_result["context_duty_coverage"],
                    "context_relevant_chars": variant_result["context_relevant_chars"],
                    "context_chars": variant_result["context_chars"],
                    "context_relevant_chars_ratio": variant_result[
                        "context_relevant_chars_ratio"
                    ],
                    "context_redundancy_rate": variant_result[
                        "context_redundancy_rate"
                    ],
                    "context_duplicate_evidence_rate": variant_result[
                        "context_duplicate_evidence_rate"
                    ],
                    "context_latency_ms": variant_result["context_latency_ms"],
                    "context_mcp_latency_ms": variant_result["context_mcp_latency_ms"],
                    "context_provider_hard_limit_valid": variant_result[
                        "context_provider_hard_limit_valid"
                    ],
                    "cold_latency_ms": variant_cold_latency,
                    "warm_latency_ms": variant_warm_latency,
                    "cold_peak_rss_bytes": variant_cold_peak_rss,
                    "warm_peak_rss_bytes": variant_warm_peak_rss,
                    "failure_reason": variant_result["failure_reason"],
                }
            )
        result["query_variant_checks"] = variant_checks
        if any(item["status"] != "passed" for item in variant_checks):
            result["status"] = "failed"
            result["failure_reason"] = "one or more frozen query variants failed"
        cases.append(result)
    cross_packet_gold_case = next(
        item
        for item in gold["cases"]
        if item["task_type"] == "long_document_cross_packet_entity"
    )
    cross_packet_query_case = next(
        item for item in cases if item["case_id"] == cross_packet_gold_case["case_id"]
    )
    cross_packet_identity = _cross_packet_identity_check(
        vault=baseline_vault,
        compiler_report=compiler_report,
        gold_case=cross_packet_gold_case,
        query_case=cross_packet_query_case,
    )
    challenges: list[dict[str, Any]] = []
    challenge_counts: dict[str, int] = {
        challenge_type: 0
        for challenge_type in (
            "prompt_injection",
            "unsupported_authoritative_claim",
            "restricted_disclosure",
            "unauthorized_mutation",
            "silent_fallback",
        )
    }
    challenge_failures = {
        "prompt_injection": 0,
        "unsupported_authoritative_claim": 0,
        "restricted_disclosure": 0,
        "unauthorized_mutation": 0,
        "silent_fallback": 0,
    }
    for challenge in gold["security_challenges"]:
        challenge_type = challenge["challenge_type"]
        challenge_counts[challenge_type] += 1
        if challenge_type == "unauthorized_mutation":
            mutation = _unauthorized_mutation_challenge(prefix, vault=baseline_vault)
            passed = mutation["rejected"]
            result = {
                "challenge_id": challenge["challenge_id"],
                "challenge_type": challenge_type,
                "status": "passed" if passed else "failed",
                "query_sha256": sha256_bytes(challenge["query"].encode("utf-8")),
                "execution_count": 1,
                "selected_source_revision_ids": [],
                "gap_codes": [],
                "provider_payload_bytes": 0,
                "peak_rss_bytes": None,
                "failure_reason": None if passed else "unauthorized mutation was not rejected",
                "mutation": mutation,
            }
        else:
            value, latency, peak_rss_bytes = _query(
                prefix,
                vault=baseline_vault,
                query=challenge["query"],
                purpose=challenge["purpose"],
                as_of=None,
            )
            passed, reason, selected_sources, gap_codes = _evaluate_read_challenge(
                challenge_type=challenge_type,
                value=value,
                source_ids=source_ids,
            )
            result = {
                "challenge_id": challenge["challenge_id"],
                "challenge_type": challenge_type,
                "status": "passed" if passed else "failed",
                "query_sha256": sha256_bytes(challenge["query"].encode("utf-8")),
                "execution_count": 1,
                "selected_source_revision_ids": selected_sources,
                "gap_codes": gap_codes,
                "provider_payload_bytes": int(
                    value.get("delivery", {}).get(
                        "provider_visible_bytes",
                        len(canonical_json(value).encode("utf-8")),
                    )
                ),
                "latency_ms": latency,
                "peak_rss_bytes": peak_rss_bytes,
                "failure_reason": None if passed else reason,
                "mutation": None,
            }
        if not passed:
            challenge_failures[challenge_type] += 1
        challenges.append(result)
    provider_bytes = sum(item["provider_payload_bytes"] for item in cases)
    provider_content_bytes = sum(item["provider_content_bytes"] for item in cases)
    cold_latencies = [item["cold_latency_ms"] for item in cases]
    warm_latencies = [item["warm_latency_ms"] for item in cases]
    passed_count = sum(item["status"] == "passed" for item in cases)
    required_target_count = sum(
        item["required"] for case in gold["cases"] for item in case["expected_objects"]
    )
    matched_required_target_count = sum(
        len(item["matched_label_ids"])
        for item in cases
    )
    relevant_source_keys = {
        source_key
        for case in gold["cases"]
        for source_key in _retrieval_coverage_source_keys(case)
    }
    relevant_source_revision_ids = {
        source_ids[source_key] for source_key in relevant_source_keys
    }
    selected_source_revision_ids = {
        revision_id
        for item in cases
        for revision_id in item["selected_source_revision_ids"]
        if revision_id in relevant_source_revision_ids
    }
    compiled_selected_count = sum(
        int(item["query_plan"].get("compiled_selected_count", 0)) for item in cases
    )
    evidence_attachment_count = sum(
        int(item["query_plan"].get("evidence_attachment_count", 0)) for item in cases
    )
    variant_checks = [
        variant
        for case in cases
        for variant in case["query_variant_checks"]
    ]
    metrics = {
        "query_count": len(cases),
        "execution_count": (len(cases) + len(variant_checks)) * 4,
        "query_variant_count": len(variant_checks),
        "query_variant_pass_rate": round(
            sum(item["status"] == "passed" for item in variant_checks)
            / len(variant_checks),
            6,
        ) if variant_checks else 1.0,
        "query_variant_provider_payload_bytes": sum(
            item["provider_payload_bytes"] + item["context_provider_payload_bytes"]
            for item in variant_checks
        ),
        "query_variant_provider_hard_limit_violations": sum(
            (not item["provider_hard_limit_valid"])
            or item["context_provider_payload_bytes"] > 65_536
            for item in variant_checks
        ),
        "passed_count": passed_count,
        "provider_payload_bytes": provider_bytes,
        "provider_content_bytes": provider_content_bytes,
        "provider_transport_overhead_bytes": max(
            0, provider_bytes - provider_content_bytes
        ),
        "provider_bytes_per_matched_target": round(
            provider_bytes / matched_required_target_count, 6
        ) if matched_required_target_count else 0.0,
        "raw_fragment_baseline_bytes": raw_fragment_baseline_bytes,
        "bytes_saved_ratio": round(
            1 - provider_bytes / raw_fragment_baseline_bytes, 6
        ) if raw_fragment_baseline_bytes else 0.0,
        "cold_latency_p50_ms": round(median(cold_latencies)),
        "cold_latency_p95_ms": _percentile(cold_latencies, 0.95),
        "warm_latency_p50_ms": round(median(warm_latencies)),
        "warm_latency_p95_ms": _percentile(warm_latencies, 0.95),
        "repeated_query_reuse_rate": round(
            sum(item["repeat_reused"] for item in cases) / len(cases), 6
        ),
        "compiled_hit_ratio": _compiled_hit_ratio(cases, gold["cases"]),
        "source_fallback_ratio": round(
            sum(bool(item["query_plan"].get("fallback", {}).get("used")) for item in cases)
            / len(cases),
            6,
        ),
        "uncompiled_source_count": max(
            (int(item["query_plan"].get("uncompiled_source_count", 0)) for item in cases),
            default=0,
        ),
        "extraction_completeness": round(
            matched_required_target_count / required_target_count, 6
        ) if required_target_count else 1.0,
        "retrieval_source_coverage": round(
            len(selected_source_revision_ids) / len(relevant_source_revision_ids), 6
        ) if relevant_source_revision_ids else 1.0,
        "source_ir_fragment_coverage": source_ir_coverage["ratio"],
        "source_ir_covered_fragment_count": source_ir_coverage[
            "covered_fragment_count"
        ],
        "source_ir_omitted_fragment_count": source_ir_coverage[
            "omitted_fragment_count"
        ],
        "evidence_attachment_rate": round(
            min(evidence_attachment_count, compiled_selected_count)
            / compiled_selected_count,
            6,
        ) if compiled_selected_count else 1.0,
        "peak_rss_bytes": max(
            (
                peak
                for peak in [
                    *(
                        item[key]
                        for item in cases
                        for key in ("cold_peak_rss_bytes", "warm_peak_rss_bytes")
                    ),
                    *(item["peak_rss_bytes"] for item in challenges),
                    *(
                        item[key]
                        for item in variant_checks
                        for key in ("cold_peak_rss_bytes", "warm_peak_rss_bytes")
                    ),
                ]
                if peak is not None
            ),
            default=None,
        ),
        "provider_hard_limit_violations": sum(
            (not item["provider_hard_limit_valid"])
            or item["context_provider_payload_bytes"] > 65_536
            for item in cases
        ) + int(not continuation_probe["provider_hard_limit_valid"]),
        "unauthorized_writes": sum(item["write_performed"] for item in cases),
        "authority_elevations": sum(
            item["authority_changed_by_ranking"] for item in cases
        ),
        "invalid_official_citations": sum(
            1
            for item in cases
            for check in item["citation_checks"]
            if check["valid"] is False
            and check["source_revision_id"] is not None
        ),
        "silent_fallbacks": sum(not item["source_fallback_visible"] for item in cases),
        "stale_prohibited_selections": sum(
            item["task_type"]
            in {"source_withdrawal", "source_successor_update", "overview_refresh"}
            and item["status"] == "failed"
            for item in cases
        ),
        "recall_at_k": round(sum(item["recall_at_k"] for item in cases) / len(cases), 6),
        "target_scoped_precision_at_k": round(
            sum(item["target_scoped_precision_at_k"] for item in cases) / len(cases), 6
        ),
        "mrr": round(sum(item["reciprocal_rank"] for item in cases) / len(cases), 6),
        "ndcg_at_k": round(sum(item["ndcg_at_k"] for item in cases) / len(cases), 6),
        "citation_validity": round(
            sum(item["citation_validity"] for item in cases) / len(cases), 6
        ),
        "claim_evidence_binding_accuracy": round(
            sum(item["claim_evidence_binding_accuracy"] for item in cases) / len(cases), 6
        ),
        "context_verification_rate": round(
            sum(item["context_verification_valid"] for item in cases) / len(cases), 6
        ),
        "context_semantic_accuracy": round(
            sum(item["context_semantic_valid"] for item in cases) / len(cases), 6
        ),
        "exact_get_success_rate": round(
            sum(item["exact_get_valid"] for item in cases) / len(cases), 6
        ),
        "continuation_success_rate": 1.0 if continuation_probe["valid"] else 0.0,
        "challenge_execution_counts": challenge_counts,
        "prompt_injection_failures": challenge_failures["prompt_injection"],
        "unsupported_authoritative_claims": challenge_failures[
            "unsupported_authoritative_claim"
        ],
        "restricted_disclosures": challenge_failures["restricted_disclosure"],
        "unauthorized_mutation_failures": challenge_failures["unauthorized_mutation"],
        "silent_fallback_challenge_failures": challenge_failures["silent_fallback"],
    }
    passed = bool(
        passed_count == len(cases)
        and metrics["provider_hard_limit_violations"] == 0
        and metrics["query_variant_pass_rate"] == 1.0
        and metrics["query_variant_provider_hard_limit_violations"] == 0
        and metrics["unauthorized_writes"] == 0
        and metrics["authority_elevations"] == 0
        and metrics["invalid_official_citations"] == 0
        and metrics["silent_fallbacks"] == 0
        and metrics["stale_prohibited_selections"] == 0
        and metrics["citation_validity"] == 1.0
        and metrics["claim_evidence_binding_accuracy"] == 1.0
        and metrics["context_verification_rate"] == 1.0
        and metrics["context_semantic_accuracy"] == 1.0
        and metrics["exact_get_success_rate"] == 1.0
        and metrics["continuation_success_rate"] == 1.0
        and cross_packet_identity["valid"]
        and all(
            metrics["challenge_execution_counts"].get(item["challenge_type"], 0)
            >= item["required_execution_count"]
            for item in gold["security_challenges"]
        )
        and not any(challenge_failures.values())
    )
    recorded_at = _timestamp()
    report = {
        "schema_version": "deeplaw.semantic-query-run/v1",
        "report_id": stable_id(
            "semanticqueryrun",
            gold["gold_id"],
            compiler_report["report_id"],
            frozen_query_set_sha256,
            recorded_at,
        ),
        "status": "passed" if passed else "failed",
        "gold_id": gold["gold_id"],
        "gold_sha256": gold_sha256,
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "compiler_report_id": compiler_report["report_id"],
        "source_revision_set_sha256": sha256_bytes(
            canonical_json(
                sorted(
                    (
                        {
                            "source_key": item["source_key"],
                            "source_revision_id": item["source_revision_id"],
                            "phase": item["phase"],
                            "sensitivity": item["sensitivity"],
                        }
                        for item in corpus["sources"]
                    ),
                    key=lambda item: item["source_key"],
                )
            ).encode("utf-8")
        ),
        "query_set_sha256": frozen_query_set_sha256,
        "first_party_command_sha256": sha256_bytes(
            canonical_json(command).encode("utf-8")
        ),
        "budget": BUDGET,
        "retrieval_configuration": {
            "query_plan_schema_version": "deeplaw.knowledge-query-plan/v5",
            "policy_ids": sorted(
                {
                    str(item["query_plan"].get("policy_id"))
                    for item in cases
                    if item["query_plan"].get("policy_id")
                }
            ),
            "fts_identity": "sqlite-fts5/autonomous_search_v3",
            "dense_model_identity": LOCAL_DENSE_MODEL,
            "reranker_model_identity": LOCAL_RERANKER_MODEL,
            "graph_hops": 1,
            "channel_order_sha256": sha256_bytes(
                canonical_json(
                    [item["query_plan"].get("channel_order", []) for item in cases]
                ).encode("utf-8")
            ),
        },
        "execution_environment": _execution_environment(
            prefix=prefix,
            network_policy=str(compiler_report.get("network_policy", "not_recorded"))
        ),
        "source_ir_coverage": source_ir_coverage,
        "continuation_probe": continuation_probe,
        "cross_packet_identity": cross_packet_identity,
        "cases": [_legacy_v1_case(item) for item in cases],
        "challenges": challenges,
        "metrics": metrics,
        "recorded_at": recorded_at,
        "competitive_claim_eligible": False,
    }
    context_outcome = _context_outcome_report(
        gold=gold,
        gold_sha256=gold_sha256,
        compiler_report_id=compiler_report["report_id"],
        query_set_digest=frozen_query_set_sha256,
        query_report=report,
        cases=cases,
        recorded_at=recorded_at,
    )
    context_metrics = context_outcome["metrics"]
    cost = {
        "schema_version": "deeplaw.semantic-query-cost/v2",
        "gold_id": gold["gold_id"],
        "compiler_report_id": compiler_report["report_id"],
        "query_set_sha256": frozen_query_set_sha256,
        "primary_agent_surface": "deeplaw knowledge context",
        "query_count": len(cases),
        "local_capsule_bytes": context_metrics["local_capsule_bytes"],
        "provider_capsule_bytes": context_metrics["provider_capsule_bytes"],
        "mcp_tool_result_bytes": context_metrics["mcp_tool_result_bytes"],
        "provider_content_bytes": context_metrics["provider_content_bytes"],
        "transport_metadata_bytes": context_metrics["transport_metadata_bytes"],
        "provider_input_token_estimate": None,
        "actual_provider_input_tokens": None,
        "token_measurement_method": "not_measured",
        "token_savings": context_metrics["token_savings"],
        "budget": {**BUDGET, "cold_or_warm": "warm"},
        "measured_at": recorded_at,
        "qualification_eligible": False,
    }
    _validate("semantic-query-run.v1.schema.json", report)
    _validate("semantic-context-outcome.v2.schema.json", context_outcome)
    _validate("semantic-query-cost.v2.schema.json", cost)
    return report, cost, context_outcome


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen Semantic Gold query set twice through the first-party CLI."
    )
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--compiler-report", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--baseline-vault", type=Path, required=True)
    parser.add_argument("--deeplaw-command", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cost-output", type=Path, required=True)
    parser.add_argument("--context-output", type=Path, required=True)
    arguments = parser.parse_args()
    report, cost, context_outcome = run(
        gold=_load(arguments.gold),
        compiler_report=_load(arguments.compiler_report),
        corpus=_load(arguments.corpus),
        vault=arguments.vault,
        baseline_vault=arguments.baseline_vault,
        command=_load(arguments.deeplaw_command),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.cost_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.context_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    arguments.cost_output.write_text(canonical_json(cost) + "\n", encoding="utf-8")
    arguments.context_output.write_text(
        canonical_json(context_outcome) + "\n", encoding="utf-8"
    )
    return 0 if report["status"] == context_outcome["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
