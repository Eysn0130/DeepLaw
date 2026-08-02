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
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.run_living_wiki_host_harness import _safe_command
from benchmarks.semantic.review_gold import query_set_sha256, validate_candidate
from deeplaw.compilation.coordinator import _decoded_artifact
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_intelligence import LOCAL_DENSE_MODEL, LOCAL_RERANKER_MODEL
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
    return {
        "matched_label_ids": sorted(matched_labels),
        "recall_at_k": recall,
        "target_scoped_precision_at_k": precision,
        "reciprocal_rank": (
            round(1 / first_rank, 6) if first_rank else (1.0 if not required else 0.0)
        ),
        "ndcg_at_k": round(dcg / ideal_dcg, 6) if ideal_dcg else 1.0,
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
    query: str,
) -> dict[str, Any]:
    capsule, _, _stdout, _ = _run_json(
        prefix,
        "autonomy",
        "context",
        "--vault",
        str(vault),
        "--task",
        query,
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
        "--confirm-no-case-data",
    )
    assert capsule is not None
    provider_payload_bytes = len(canonical_json(capsule).encode("utf-8"))
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
        "provider_payload_bytes": provider_payload_bytes,
        "provider_hard_limit_valid": provider_payload_bytes <= 65_536,
        "verification_valid": bool(verification and verification.get("valid")),
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
        {"evidence_gap", "retrieval_gap", "uncompiled_source", "stale_knowledge"}
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
    context = _context_verification(prefix, vault=vault, query=case["query"])
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
        and evidence_binding_valid
        and exact_get_valid
    )
    passed = bool(semantic_pass and safety_pass)
    if not safety_pass and failure_reason is None:
        failure_reason = "query safety or provider-bound invariant failed"
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
        "context_provider_payload_bytes": context["provider_payload_bytes"],
        "context_capsule_id": context["capsule_id"],
        "context_capsule_sha256": context["capsule_sha256"],
        "context_verification_valid": context["verification_valid"],
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


def run(
    *,
    gold: dict[str, Any],
    compiler_report: dict[str, Any],
    corpus: dict[str, Any],
    vault: Path,
    baseline_vault: Path,
    command: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    gold_sha256 = validate_candidate(gold, repository=_repository())
    supported_compiler_reports = {
        "deeplaw.real-semantic-host-report/v1",
        "deeplaw.real-semantic-host-report/v2",
        "deeplaw.deterministic-semantic-lifecycle/v1",
    }
    if (
        compiler_report.get("schema_version") not in supported_compiler_reports
        or compiler_report.get("status") != "passed"
    ):
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
    metrics = {
        "query_count": len(cases),
        "execution_count": len(cases) * 2,
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
            1 - provider_content_bytes / raw_fragment_baseline_bytes, 6
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
        and metrics["unauthorized_writes"] == 0
        and metrics["authority_elevations"] == 0
        and metrics["invalid_official_citations"] == 0
        and metrics["silent_fallbacks"] == 0
        and metrics["stale_prohibited_selections"] == 0
        and metrics["citation_validity"] == 1.0
        and metrics["claim_evidence_binding_accuracy"] == 1.0
        and metrics["context_verification_rate"] == 1.0
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
        "cases": cases,
        "challenges": challenges,
        "metrics": metrics,
        "recorded_at": recorded_at,
        "competitive_claim_eligible": False,
    }
    cost = {
        "schema_version": "deeplaw.semantic-query-cost/v1",
        "gold_id": gold["gold_id"],
        "compiler_report_id": compiler_report["report_id"],
        "query_set_sha256": frozen_query_set_sha256,
        "first_party_command": "deeplaw knowledge query",
        "query_count": len(cases),
        "total_query_tokens": provider_bytes,
        "total_context_bytes": provider_bytes,
        "raw_fragment_baseline_bytes": raw_fragment_baseline_bytes,
        "measurement_method": "utf8_bytes_proxy",
        "budget": {**BUDGET, "cold_or_warm": "warm"},
        "measured_at": recorded_at,
    }
    _validate("semantic-query-run.v1.schema.json", report)
    _validate("semantic-query-cost.v1.schema.json", cost)
    return report, cost


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
    arguments = parser.parse_args()
    report, cost = run(
        gold=_load(arguments.gold),
        compiler_report=_load(arguments.compiler_report),
        corpus=_load(arguments.corpus),
        vault=arguments.vault,
        baseline_vault=arguments.baseline_vault,
        command=_load(arguments.deeplaw_command),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.cost_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    arguments.cost_output.write_text(canonical_json(cost) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
