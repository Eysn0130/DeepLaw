"""Claim-ineligible Query v6 and relation/graph scale diagnostic.

This runner is intentionally a diagnostic, not a quality or release gate.  It builds a
temporary synthetic Vault, records bounded retrieval and graph evidence, and refuses to
silently substitute a smaller fixture for an explicitly requested expensive lane.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import subprocess
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

try:  # ``resource`` is unavailable on native Windows.
    import resource
except ImportError:  # pragma: no cover - exercised on native Windows
    resource = None  # type: ignore[assignment]

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.compilation.applicability import applicability_digest, policy_digest
from deeplaw.compilation.coordinator import CompilationCoordinator, _artifact
from deeplaw.compilation.models import COMPILER_GRANT_OPERATIONS
from deeplaw.compilation.profiles import SEMANTIC_DUTIES, compiler_profile
from deeplaw.evidence import build_input_set_sha256, statement_sha256
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.persistent_read_runtime import PersistentReadRuntime
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.util import canonical_json, sha256_bytes, stable_id

SCHEMA_VERSION = "deeplaw.v013-query-graph-scale-report/v1"
RUNNER_RELATIVE_PATH = "benchmarks/v013/query_graph_scale.py"
SCHEMA_RELATIVE_PATH = "contracts/v013-query-graph-scale-report.v1.schema.json"
SCALE_CHOICES = (101, 5_001, 10_000, 100_000)
DEFAULT_SCALES = (101,)
EXPENSIVE_SCALES = (10_000, 100_000)
SEED = 0xD33F013
PROVIDER_HARD_LIMIT_BYTES = 65_536
STATEMENT_CANDIDATE_BOUND = 512
GRAPH_ADMITTED_BOUND = 500
GRAPH_SCANNED_BOUND = 5_000
MAX_PROVIDER_STATEMENTS = 8
QUERY_RETRIEVAL_MODES = ("exact", "lexical", "dense", "graph", "hybrid")
STATEMENTS_PER_REVISION = 1_000
PACKET_MAX_FRAGMENTS = 4

_LOCAL_PATH = re.compile(
    r"(?:/Users/|/home/|/private/var/|/tmp/|/var/folders/|[A-Za-z]:[\\/]|\\\\)"
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_reason(value: str) -> str:
    value = _LOCAL_PATH.sub("<local-path>", value)
    value = re.sub(r"(?:[A-Za-z0-9._-]+/){2,}[A-Za-z0-9._-]+", "<local-path>", value)
    return value[:500]


def _runner_path() -> Path:
    return Path(__file__).resolve()


def _repo_root() -> Path:
    return _runner_path().parents[2]


def _sha256_path(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except OSError:
        return "0" * 64


def _git_metadata() -> dict[str, Any]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return {"commit": None, "working_tree_dirty": False, "reason": type(error).__name__}
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        return {"commit": None, "working_tree_dirty": dirty, "reason": "invalid git commit"}
    return {"commit": commit, "working_tree_dirty": dirty, "reason": None}


def _environment() -> dict[str, Any]:
    ram: int | None
    try:
        ram = int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        ram = None
    git = _git_metadata()
    return {
        "cpu": {
            "logical_count": max(1, int(os.cpu_count() or 1)),
            "model": platform.processor() or "unknown",
        },
        "ram_total_bytes": ram,
        "os": {
            "name": platform.system() or "unknown",
            "release": platform.release() or "unknown",
            "machine": platform.machine() or "unknown",
        },
        "python": platform.python_version(),
        "sqlite": _sqlite_version(),
        "git_commit": git["commit"],
        "working_tree_dirty": git["working_tree_dirty"],
        "git_reason": git["reason"],
    }


def _sqlite_version() -> str:
    try:
        import sqlite3

        return str(sqlite3.sqlite_version)
    except Exception:  # pragma: no cover - Python always ships sqlite3 in supported runs
        return "unknown"


@contextmanager
def _temporary_workspace(workspace: Path | None) -> Iterator[Path]:
    if workspace is None:
        with tempfile.TemporaryDirectory(prefix="deeplaw-v013-query-graph-") as value:
            root = Path(value)
            root.chmod(0o700)
            yield root
        return
    root = workspace.expanduser().absolute()
    if root.exists() and any(root.iterdir()):
        raise ValueError(
            "workspace must be absent or empty; the runner never reads an existing Vault"
        )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    yield root


def _statement_value(
    *,
    ordinal: int,
    text: str,
    source_ref: dict[str, str],
    char_start: int,
    char_end: int,
) -> dict[str, Any]:
    source_refs = [source_ref]
    return {
        "ordinal": ordinal,
        "char_start": char_start,
        "char_end": char_end,
        "statement_text": text,
        "statement_sha256": statement_sha256(text),
        "statement_type": "factual",
        "support_status": "supported",
        "source_refs": source_refs,
        "knowledge_revision_refs": [],
        "relation_revision_refs": [],
        "valid_from": None,
        "valid_to": None,
        "limitation": None,
        "gaps": [],
        "input_set_sha256": build_input_set_sha256(
            source_refs=source_refs,
            knowledge_revision_refs=[],
            relation_revision_refs=[],
            valid_from=None,
            valid_to=None,
            statement_type="factual",
            support_status="supported",
            limitation=None,
            gaps=[],
        ),
    }


def _source_text(statement_count: int, *, seed: int) -> str:
    rng = random.Random(seed + statement_count)
    permutation = list(range(statement_count))
    rng.shuffle(permutation)
    lines: list[str] = []
    for group_start in range(0, statement_count, STATEMENTS_PER_REVISION):
        lines.append(
            f"# Query graph scale group {group_start // STATEMENTS_PER_REVISION:04d}"
        )
        for index in permutation[group_start : group_start + STATEMENTS_PER_REVISION]:
            # Source order is seeded and randomized; the short unique text keeps
            # each 1,000-Statement section below the compiler's 12,000-character
            # extraction chunk ceiling, so no Statement is split at a chunk edge.
            lines.append(f"q{index:06d}")
    return "\n".join(lines) + "\n"


def _scale_facts(source: Path) -> tuple[dict[str, Any], str]:
    facts: dict[str, Any] = {
        "source_present": True,
        "source_admitted": True,
        "source_nonempty": True,
        "media_type": "text/markdown",
        "byte_size": source.stat().st_size,
        "lifecycle": "active",
        "node_types": {},
        "signals": {
            key: False for key in ("code", "table", "list", "timeline", "question", "procedure")
        },
        "observation_kinds": {},
        "observation_count": 0,
        "existing_kinds": {},
        "existing_count": 0,
        "relation_count": 0,
        "previous_output_count": 0,
        "affected_synthesis_count": 0,
        "truncated": False,
    }
    return facts, sha256_bytes(canonical_json(facts).encode("utf-8"))


def _duty_reports(*, run_id: str, source: Path) -> list[dict[str, Any]]:
    facts, facts_sha256 = _scale_facts(source)
    return [
        {
            "duty_id": stable_id("duty", run_id, duty_type),
            "duty_type": duty_type,
            "required": False,
            "applicability": "not_applicable",
            "status": "omitted_with_reason",
            "output_refs": [],
            "evidence_refs": [],
            "reason": "Deterministic Query/Graph scale fixture.",
            "unresolved_items": [],
            "omission_reason": "Synthetic fixture does not exercise semantic duties.",
            "deterministic_basis": {
                "rule_id": "query-graph-scale-v1",
                "facts": facts,
                "stable_refs": [],
                "facts_sha256": facts_sha256,
                "reason": "Deterministic Query/Graph scale fixture.",
            },
        }
        for duty_type in SEMANTIC_DUTIES
    ]


def _commit_statement_fixture(root: Path, source: Path, *, statement_count: int) -> dict[str, Any]:
    """Create source-bound Statements through the public v3 compilation commit path."""

    initialize_knowledge_vault(root, name="DeepLaw Query/Graph scale diagnostic", scope="project")
    initialize_autonomous_core(root)
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(vault, source, source_kind="document", confirm_no_case_data=True)
    source_revision_id = str(compiled["identity"]["source_revision_id"])
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-query-graph-scale-compiler",
            operations=COMPILER_GRANT_OPERATIONS,
            max_request_bytes=320 * 1024,
            max_mutations_per_minute=120,
            max_objects=100_000,
        )["grant_id"]
    profile = compiler_profile(version="3")
    coordinator = CompilationCoordinator(root)
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=source_revision_id,
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version="3",
        host_identity="v013-query-graph-scale",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        confirm_no_case_data=True,
        # Four <12 KiB sections keep each staged public compilation request
        # under its closed grant byte budget. Grouping 1,000 Statements per
        # revision also keeps the exact 100k fixture at 100 governed mutations,
        # below the production 120/min grant ceiling without weakening it.
        packet_max_fragments=PACKET_MAX_FRAGMENTS,
    )
    packet_plans: list[dict[str, str]] = []
    statement_plans: list[dict[str, Any]] = []
    packet_count = 0
    action_count = 0
    while (packet := coordinator.next_packet(begun["compilation_run_id"])) is not None:
        packet_count += 1
        packet_plans.append({"packet_id": packet["packet_id"]})
        object_actions: list[dict[str, Any]] = []
        for local_ordinal, fragment in enumerate(packet["fragments"], start=1):
            source_ref = {
                "source_revision_id": packet["source_revision_id"],
                "fragment_id": fragment["fragment_id"],
                "locator": fragment["locator"],
                "quote_sha256": fragment["text_sha256"],
            }
            statements: list[dict[str, Any]] = []
            cursor = 0
            for ordinal, line in enumerate(fragment["text"].splitlines(), start=1):
                if not line.strip() or line.lstrip().startswith("#"):
                    cursor += len(line) + 1
                    continue
                text = line.strip()
                start = fragment["text"].find(text, cursor)
                if start < 0:
                    start = cursor
                end = start + len(text)
                cursor = end + 1
                statements.append(
                    _statement_value(
                        ordinal=ordinal,
                        text=text,
                        source_ref=source_ref,
                        char_start=start,
                        char_end=end,
                    )
                )
            object_actions.append(
                {
                    "action": "create",
                    "kind": "claim",
                    "semantic_key": f"v013-query-graph-scale:{action_count}",
                    "knowledge_id": None,
                    "expected_revision_id": None,
                    "title": f"Query graph scale group {action_count}",
                    "body": fragment["text"],
                    "aliases": [],
                    "epistemic_state": "supported",
                    "source_refs": [source_ref],
                    "assertion": None,
                    "tags": [],
                    "valid_from": None,
                    "valid_to": None,
                    "applicability": {
                        "description": "Deterministic Query/Graph scale fixture.",
                        "scopes": [],
                        "conditions": [],
                        "exclusions": [],
                    },
                    "synthesis_inputs": None,
                    "reason": "Deterministic Query/Graph scale fixture.",
                }
            )
            statement_plans.append(
                {
                    "packet_id": packet["packet_id"],
                    "object_action_ordinal": local_ordinal,
                    "statements": statements,
                }
            )
            action_count += 1
        plan = {
            "schema_version": "deeplaw.source-compilation-plan/v1",
            "source_revision_id": packet["source_revision_id"],
            "packet_id": packet["packet_id"],
            "expected_audit_head": packet["input_audit_head"],
            "object_actions": object_actions,
            "relation_actions": [],
            "identity_actions": [],
            "unresolved_identities": [],
            "contradictions": [],
            "coverage": {
                "packet_fragment_count": len(packet["fragments"]),
                "covered_fragment_ids": [
                    fragment["fragment_id"] for fragment in packet["fragments"]
                ],
                "omitted_fragment_ids": [],
                "ratio": 1.0,
                "completeness": "complete",
            },
            "skipped_fragments": [],
            "warnings": [],
        }
        coordinator.stage(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            plan=plan,
            confirm_no_case_data=True,
        )
    if packet_count == 0:
        raise RuntimeError("source compilation produced no packets")
    if action_count == 0:
        raise RuntimeError("source compilation produced no object actions")
    if (
        coordinator.validate(
            grant_id=grant_id,
            compilation_run_id=begun["compilation_run_id"],
            confirm_no_case_data=True,
        )["valid"]
        is not True
    ):
        raise RuntimeError("source compilation validation failed")
    reports = _duty_reports(run_id=begun["compilation_run_id"], source=source)
    applicability_policy_sha256 = policy_digest()
    applicability_sha256 = applicability_digest(
        {
            report["duty_type"]: {
                "applicability": report["applicability"],
                "deterministic_basis": report["deterministic_basis"],
            }
            for report in reports
        }
    )
    inventory = {
        "coverage": {
            "applicability_policy_sha256": applicability_policy_sha256,
            "applicability_digest": applicability_sha256,
            "compilation_run_id": begun["compilation_run_id"],
        },
    }
    inventory["inventory_sha256"] = sha256_bytes(canonical_json(inventory).encode("utf-8"))
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        inventory_digest, _ = _artifact(
            store,
            value=inventory,
            role="semantic_inventory",
            created_at=store._next_transaction_time(),
        )
        store.connection.execute(
            "UPDATE semantic_compilation_runs_v2 SET inventory_sha256 = ? "
            "WHERE compilation_run_id = ?",
            (inventory["inventory_sha256"], begun["compilation_run_id"]),
        )
        store.connection.execute(
            """
            INSERT INTO semantic_inventories_v1(
                artifact_sha256, inventory_sha256, inventory_id,
                compilation_run_id, observation_count, packet_count,
                truncated, recorded_at
            ) VALUES (?, ?, ?, ?, 0, ?, 0, ?)
            """,
            (
                inventory_digest,
                inventory["inventory_sha256"],
                "semanticinventory_" + begun["compilation_run_id"][-24:],
                begun["compilation_run_id"],
                packet_count,
                store._next_transaction_time(),
            ),
        )
        for report in reports:
            store.connection.execute(
                """
                INSERT INTO semantic_duty_reports_v1(
                    compilation_run_id, duty_id, duty_type, required,
                    status, report_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    begun["compilation_run_id"],
                    report["duty_id"],
                    report["duty_type"],
                    int(report["required"]),
                    report["status"],
                    canonical_json(report),
                ),
            )
        store.connection.commit()
    publication = {
        "schema_version": "deeplaw.semantic-publication-plan/v3",
        "compiler_profile_version": "3",
        "compilation_run_id": begun["compilation_run_id"],
        "source_revision_id": source_revision_id,
        "expected_audit_head": begun["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "finalization_packet_id": "finalization_" + "e" * 24,
        "applicability_policy_sha256": applicability_policy_sha256,
        "applicability_digest": applicability_sha256,
        "packet_plans": packet_plans,
        "statement_plans": statement_plans,
        "observation_dispositions": [],
        "duty_reports": reports,
        "semantic_status": "partial",
        "warnings": [],
    }
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        publication_digest, _ = _artifact(
            store,
            value=publication,
            role="publication_plan",
            created_at=store._next_transaction_time(),
        )
        store.connection.execute(
            "UPDATE semantic_compilation_runs_v2 SET publication_plan_sha256 = ? "
            "WHERE compilation_run_id = ?",
            (publication_digest, begun["compilation_run_id"]),
        )
        store.connection.commit()
    committed = CompilationCoordinator(root).commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    return {
        "source_revision_id": source_revision_id,
        "compilation_run_id": begun["compilation_run_id"],
        "statement_count": action_count and _statement_count(root),
        "packet_count": packet_count,
        "audit_head": committed.get("audit_head"),
    }


def _statement_count(root: Path) -> int:
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        return int(
            store.connection.execute("SELECT COUNT(*) FROM knowledge_statements_v1").fetchone()[0]
        )


def _source_ref(root: Path) -> str:
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        row = store.connection.execute(
            "SELECT knowledge_revision_id FROM knowledge_statements_v1 "
            "ORDER BY statement_id LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("compiled Statement evidence is unavailable")
        return str(row["knowledge_revision_id"])


def _create_graph_fixture(
    root: Path,
    *,
    scale: int,
    source_revision_id: str,
    execute_expensive: bool,
) -> dict[str, Any]:
    """Create a small governed graph for smoke; expensive relation lanes are explicit."""

    if scale != DEFAULT_SCALES[0]:
        return {
            "requested_relation_count": scale,
            "executed_relation_count": 0,
            "status": "not_executed",
            "reason": (
                "not_executed: no safe equivalent audited bulk relation constructor is "
                "available; public add_relation is rate-bounded at 120 mutations/min"
                + ("; execution was explicitly requested" if execute_expensive else "")
            ),
            "nodes": [],
            "seed": None,
            "checks": {},
            "graph_hops": {},
            "truncation": {
                "admitted_bound": GRAPH_ADMITTED_BOUND,
                "scanned_bound": GRAPH_SCANNED_BOUND,
                "status": "not_executed",
                "gap_or_receipt_evidence": False,
            },
        }
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="v013-query-graph-scale-relations",
            operations=("save_claim", "add_relation"),
            max_mutations_per_minute=120,
            max_objects=200,
        )
        nodes: list[str] = []
        for index in range(12):
            title = "zzzzuniqueseedtoken" if index == 0 else f"Query graph node {index}"
            response = store.remember(
                grant_id=grant["grant_id"],
                idempotency_key=f"graph-node-{index}",
                title=title,
                body=f"Deterministic {title}.",
                kind="claim",
                operation="save_claim",
                semantic_key=f"v013-query-graph-node:{index}",
                source_refs=[{"revision_id": source_revision_id}],
                confirm_no_case_data=True,
            )
            nodes.append(str(response["knowledge_id"]))

        def edge(key: str, subject: int, predicate: str, object_id: int, **kwargs: Any) -> None:
            store.add_relation(
                grant_id=grant["grant_id"],
                idempotency_key=key,
                subject_knowledge_id=nodes[subject],
                predicate=predicate,
                object_knowledge_id=nodes[object_id],
                evidence_refs=[{"revision_id": source_revision_id}],
                confirm_no_case_data=True,
                **kwargs,
            )

        edge("edge-hub-2", 0, "supports", 2)
        edge("edge-hub-3", 0, "supports", 3)
        edge("edge-deep-34", 3, "depends_on", 4)
        edge("edge-deep-45", 4, "depends_on", 5)
        edge("edge-cycle-56", 5, "related_to", 6)
        edge("edge-cycle-65", 6, "related_to", 5)
        edge("edge-contradiction", 1, "contradicts", 2)
        edge(
            "edge-temporal",
            2,
            "applies_to",
            3,
            valid_from="2099-01-01T00:00:00Z",
            valid_to="2100-01-01T00:00:00Z",
        )
        # Insert the directly reachable dependency last so the topology check also
        # exercises a tail-position relation in this bounded development fixture.
        edge("edge-tail", 0, "depends_on", 1)
        try:
            edge("edge-self-loop-invalid", 0, "related_to", 0)
        except ValueError:
            self_loop_rejected = True
        else:
            self_loop_rejected = False
        try:
            store.add_relation(
                grant_id=grant["grant_id"],
                idempotency_key="edge-dangling-invalid",
                subject_knowledge_id=nodes[0],
                predicate="related_to",
                object_knowledge_id="knowledge_000000000000000000000000",
                evidence_refs=[{"revision_id": source_revision_id}],
                confirm_no_case_data=True,
            )
        except (KeyError, ValueError):
            dangling_relation_rejected = True
        else:
            dangling_relation_rejected = False
        raw_count = int(
            store.connection.execute(
                "SELECT COUNT(*) FROM knowledge_relation_revisions_v3"
            ).fetchone()[0]
        )
        verification = store.verify()
        graph_by_seed = store.graph(knowledge_id=nodes[0], limit=100)
        graph_global = store.graph(limit=GRAPH_ADMITTED_BOUND)
        graph_hops: dict[str, Any] = {}
        for hops in (0, 1, 2):
            recall = store.recall(
                "zzzzuniqueseedtoken",
                graph_hops=hops,
                retrieval_mode="graph",
                limit=8,
                max_chars=4_000,
                max_tokens=2_000,
                max_sources=8,
            )
            graph_hops[str(hops)] = {
                "requested": hops,
                "effective": recall.get("query_plan", {}).get("budget", {}).get("graph_hops"),
                "selected_ids": [item.get("knowledge_id") for item in recall.get("results", [])],
                "gaps": recall.get("gaps", [])[:16],
                "query_plan_sha256": recall.get("query_plan_sha256"),
            }
        hop_zero_ids = set(graph_hops["0"]["selected_ids"])
        hop_one_ids = set(graph_hops["1"]["selected_ids"])
        hop_two_ids = set(graph_hops["2"]["selected_ids"])
        dangling = store.connection.execute(
            """
            SELECT COUNT(*)
            FROM knowledge_relation_revisions_v3 AS relation
            LEFT JOIN knowledge_objects_v3 AS subject
              ON subject.knowledge_id = relation.subject_knowledge_id
            LEFT JOIN knowledge_objects_v3 AS object
              ON object.knowledge_id = relation.object_knowledge_id
            WHERE subject.knowledge_id IS NULL OR object.knowledge_id IS NULL
            """
        ).fetchone()[0]
        before = store.graph(as_of="2098-12-31T23:59:59Z", limit=100)
        after = store.graph(as_of="2099-01-02T00:00:00Z", limit=100)
    temporal_before = not any(item.get("predicate") == "applies_to" for item in before["relations"])
    temporal_after = any(item.get("predicate") == "applies_to" for item in after["relations"])
    global_predicates = {str(item.get("predicate")) for item in graph_global["relations"]}
    global_objects = {item.get("object_knowledge_id") for item in graph_global["relations"]}
    checks = {
        "tail_edge_retrieved": nodes[1]
        in {item.get("object_knowledge_id") for item in graph_by_seed["relations"]},
        "hub_edges": len(graph_by_seed["relations"]) >= 3,
        "deep_chain": nodes[4] in global_objects,
        "cycle": "related_to" in global_predicates,
        "contradiction": "contradicts" in global_predicates,
        "temporal_before_excluded": temporal_before,
        "temporal_after_included": temporal_after,
        "dangling_relations_absent": int(dangling) == 0,
        "dangling_relation_rejected": dangling_relation_rejected,
        "self_loop_rejected": self_loop_rejected,
        "graph_hops_zero_seed_only": hop_zero_ids == {nodes[0]},
        "graph_hops_one_direct": {nodes[0], nodes[1], nodes[2], nodes[3]}.issubset(
            hop_one_ids
        )
        and nodes[4] not in hop_one_ids,
        "graph_hops_two_deep": nodes[4] in hop_two_ids,
        "verify_valid": bool(verification.get("valid")),
    }
    truncation = {
        "admitted_bound": GRAPH_ADMITTED_BOUND,
        "scanned_bound": GRAPH_SCANNED_BOUND,
        "candidate_relations_scanned": graph_global["budget"].get("candidate_relations_scanned"),
        "candidate_scan_truncated": bool(graph_global["budget"].get("candidate_scan_truncated")),
        "status": "not_executed",
        "gap_or_receipt_evidence": False,
        "reason": (
            "smoke graph has fewer than 500 relations; 500/5000 truncation requires "
            "an expensive lane"
        ),
    }
    return {
        "requested_relation_count": raw_count,
        "executed_relation_count": raw_count,
        "status": "executed",
        "reason": None,
        "nodes": nodes,
        "seed": nodes[0],
        "checks": checks,
        "graph_hops": graph_hops,
        "truncation": truncation,
    }


def _peak_rss_bytes() -> int | None:
    if resource is None:
        return None
    try:
        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    # macOS reports bytes, Linux reports KiB.
    return value if platform.system() == "Darwin" else value * 1024


def _storage(root: Path) -> dict[str, int]:
    total = 0
    files = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                files += 1
                total += path.stat().st_size
        except OSError:
            continue
    return {"bytes": total, "file_count": files}


def _query_scale(root: Path, *, statement_count: int) -> dict[str, Any]:
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        rows = store.connection.execute(
            "SELECT statement_id, statement_text FROM knowledge_statements_v1 "
            "ORDER BY knowledge_revision_id, ordinal, statement_id"
        ).fetchall()
    if len(rows) != statement_count:
        raise RuntimeError(
            f"Statement fixture count mismatch: expected {statement_count}, got {len(rows)}"
        )
    positions = {0, statement_count // 2, statement_count - 1}
    if statement_count > 5_001:
        positions.add(5_001)
    target_positions = sorted(positions)
    targets: list[dict[str, Any]] = []
    service = PurposeAwareRetrievalService(root)
    full_verify_count = 0
    original_verify = AutonomousKnowledgeStore.verify

    def counted_verify(
        store: AutonomousKnowledgeStore,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        nonlocal full_verify_count
        full_verify_count += 1
        return original_verify(store, *args, **kwargs)

    runtime_start = time.perf_counter()
    with patch.object(AutonomousKnowledgeStore, "verify", counted_verify):
        runtime = PersistentReadRuntime(root)
        startup_full_verify_count = full_verify_count
        runtime_startup_ms = round((time.perf_counter() - runtime_start) * 1_000, 3)
        query_start = time.perf_counter()
        try:
            for position in target_positions:
                target = str(rows[position]["statement_text"])
                snapshot = runtime.get_snapshot(operation="query")
                result = service.query(
                    target,
                    query_plan_version="6",
                    applicable_duties=("primary_answer",),
                    projection="audit",
                    limit=MAX_PROVIDER_STATEMENTS,
                    max_chars=8_000,
                    max_tokens=4_000,
                    max_sources=8,
                    _runtime_snapshot=snapshot,
                )
                selected = [
                    str(item.get("statement_id"))
                    for item in result.get("statements", [])
                ]
                targets.append(
                    {
                        "position": position,
                        "statement_id": str(rows[position]["statement_id"]),
                        "statement_text_sha256": statement_sha256(target),
                        "selected_ids": selected,
                        "selected": str(rows[position]["statement_id"]) in selected,
                        "candidate_count": result.get("query_plan", {}).get(
                            "compiled_candidate_count"
                        ),
                        "candidate_bound": STATEMENT_CANDIDATE_BOUND,
                        "provider_bytes": len(
                            canonical_json(result["capsule"]).encode("utf-8")
                        ),
                        "provider_hard_limit_bytes": PROVIDER_HARD_LIMIT_BYTES,
                        "receipt_id": result.get("receipt_id"),
                        "query_plan_sha256": result.get("query_plan_sha256"),
                        "discovery_limitations": result.get("query_plan", {})
                        .get("discovery", {})
                        .get("limitations", [])[:16],
                        "gaps": result.get("gaps", [])[:16],
                    }
                )
        finally:
            runtime.close()
    elapsed_ms = round((time.perf_counter() - query_start) * 1000, 3)
    return {
        "statement_count": statement_count,
        "target_positions": target_positions,
        "targets": targets,
        "tail_recall": all(item["selected"] for item in targets),
        "position_independent": len({item["selected"] for item in targets}) == 1,
        "query_elapsed_ms": elapsed_ms,
        "runtime_mode": "persistent_verified_snapshot",
        "runtime_startup_ms": runtime_startup_ms,
        "runtime_full_verify_count": full_verify_count,
        "per_request_full_verify": full_verify_count > startup_full_verify_count,
        "candidate_bound": STATEMENT_CANDIDATE_BOUND,
        "legacy_global_prefix_scan_removed": True,
        "provider_hard_limit_bytes": PROVIDER_HARD_LIMIT_BYTES,
        "max_provider_bytes": max((item["provider_bytes"] for item in targets), default=0),
        "scan_strategy_bounded": all(
            item["candidate_count"] is None or item["candidate_count"] <= STATEMENT_CANDIDATE_BOUND
            for item in targets
        ),
    }


def _run_scale(root: Path, *, scale: int, execute_expensive: bool) -> dict[str, Any]:
    if scale in EXPENSIVE_SCALES and not execute_expensive:
        return {
            "scale": scale,
            "status": "not_executed",
            "statement_status": "not_executed",
            "graph_status": "not_executed",
            "reason": "not_executed: 10,000/100,000 lanes require --execute-expensive",
            "construction": "not_executed",
            "derived_rebuild": {
                "status": "not_executed",
                "reason": "the scale lane was not executed",
            },
            "statement": {"statement_count": 0, "tail_recall": None},
            "graph": {
                "requested_relation_count": scale,
                "executed_relation_count": 0,
                "status": "not_executed",
                "reason": (
                    "not_executed: no safe equivalent audited bulk relation constructor "
                    "is available; public add_relation is rate-bounded at 120 mutations/min"
                ),
            },
            "resource": {
                "process_peak_rss_before_bytes": _peak_rss_bytes(),
                "process_peak_rss_after_bytes": _peak_rss_bytes(),
                "storage": {"bytes": 0, "file_count": 0},
            },
            "limitations": ["No smaller substitute is used for this requested lane."],
        }
    scale_root = root / f"scale-{scale}"
    source = root / f"scale-{scale}.md"
    source.write_text(_source_text(scale, seed=SEED), encoding="utf-8", newline="\n")
    rss_before = _peak_rss_bytes()
    build_start = time.perf_counter()
    try:
        fixture = _commit_statement_fixture(scale_root, source, statement_count=scale)
        relation = _create_graph_fixture(
            scale_root,
            scale=scale,
            source_revision_id=_source_ref(scale_root),
            execute_expensive=execute_expensive,
        )
        derived_rebuild: dict[str, Any]
        with AutonomousKnowledgeStore(scale_root, read_only=False) as store:
            try:
                store.rebuild_derived(projection_profile="standard")
            except Exception as error:
                derived_rebuild = {
                    "status": "fail",
                    "reason": _safe_reason(
                        f"{type(error).__name__}: {error}"
                    ),
                }
            else:
                derived_rebuild = {"status": "executed", "reason": None}
        query = _query_scale(scale_root, statement_count=scale)
        build_ms = round((time.perf_counter() - build_start) * 1000, 3)
        rss_after = _peak_rss_bytes()
        derived_failed = derived_rebuild["status"] == "fail"
        return {
            "scale": scale,
            "status": (
                "fail"
                if not query["tail_recall"] or derived_failed or relation["status"] == "fail"
                else "executed"
                if relation["status"] == "executed"
                else "not_executed"
            ),
            "statement_status": "executed" if query["tail_recall"] else "fail",
            "graph_status": relation["status"],
            "reason": (
                "tail Statement was not selected"
                if not query["tail_recall"]
                else derived_rebuild["reason"]
                if derived_failed
                else relation.get("reason")
                if relation["status"] == "not_executed"
                else None
            ),
            "construction": "public_profile_v3_compilation",
            "derived_rebuild": derived_rebuild,
            "source_sha256": _sha256_path(source),
            "fixture": fixture,
            "statement": query,
            "graph": relation,
            "resource": {
                "build_elapsed_ms": build_ms,
                "process_peak_rss_before_bytes": rss_before,
                "process_peak_rss_after_bytes": rss_after,
                "storage": _storage(scale_root),
            },
            "limitations": [
                "Synthetic, source-free quality diagnostic; not eligible for a release "
                "or competitive claim.",
                "500/5000 relation truncation is not passable unless the bound and an "
                "explicit Gap/Receipt are observed.",
            ],
        }
    except Exception as error:
        return {
            "scale": scale,
            "status": "fail",
            "statement_status": "fail",
            "graph_status": "fail",
            "reason": _safe_reason(f"{type(error).__name__}: {error}"),
            "construction": "public_profile_v3_compilation",
            "derived_rebuild": {
                "status": "fail",
                "reason": _safe_reason(f"{type(error).__name__}: {error}"),
            },
            "source_sha256": _sha256_path(source),
            "fixture": {},
            "statement": {"statement_count": 0, "tail_recall": False},
            "graph": {
                "requested_relation_count": scale,
                "executed_relation_count": 0,
                "status": "fail",
                "reason": "fixture construction failed",
            },
            "resource": {
                "process_peak_rss_before_bytes": rss_before,
                "process_peak_rss_after_bytes": _peak_rss_bytes(),
                "storage": _storage(scale_root),
            },
            "limitations": ["Failure is retained; no smaller substitute was used."],
        }


def _digest_body(report: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(report)
    body.pop("report_sha256", None)
    return body


def _package_version() -> str:
    try:
        from deeplaw import __version__
    except ImportError:  # pragma: no cover
        return "unknown"
    return str(__version__)


def build_report(
    *,
    scales: Sequence[int] = DEFAULT_SCALES,
    execute_expensive: bool = False,
    workspace: Path | None = None,
) -> dict[str, Any]:
    selected = tuple(dict.fromkeys(int(value) for value in scales))
    if not selected or any(value not in SCALE_CHOICES for value in selected):
        raise ValueError(f"scales must be selected from {SCALE_CHOICES}")
    with _temporary_workspace(workspace) as root:
        reports = [
            _run_scale(root, scale=value, execute_expensive=execute_expensive) for value in selected
        ]
    runner_hash = _sha256_path(_runner_path())
    source_hashes = {
        RUNNER_RELATIVE_PATH: runner_hash,
        SCHEMA_RELATIVE_PATH: _sha256_path(_repo_root() / SCHEMA_RELATIVE_PATH),
        "src/deeplaw/retrieval/query_v6.py": _sha256_path(
            _repo_root() / "src/deeplaw/retrieval/query_v6.py"
        ),
        "src/deeplaw/knowledge_autonomy.py": _sha256_path(
            _repo_root() / "src/deeplaw/knowledge_autonomy.py"
        ),
        "src/deeplaw/projection/builder.py": _sha256_path(
            _repo_root() / "src/deeplaw/projection/builder.py"
        ),
    }
    executed = sum(item["status"] == "executed" for item in reports)
    failed = sum(item["status"] == "fail" for item in reports)
    not_executed = [
        {"scale": item["scale"], "reason": item.get("reason") or "not_executed"}
        for item in reports
        if item["status"] == "not_executed"
    ]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "competitive_claim_eligible": False,
        "claim_ineligibility_reason": (
            "Synthetic construction diagnostic; no Gold, legal Pack, real host, or "
            "user corpus is read."
        ),
        "profile": "construction_diagnostic",
        "generated_at_utc": _utc_now(),
        "release_gate_passed": False,
        "candidate": {
            "package_version": _package_version(),
            "runner": RUNNER_RELATIVE_PATH,
            "runner_sha256": runner_hash,
            "source_hashes": source_hashes,
        },
        "environment": _environment(),
        "configuration": {
            "scales": list(selected),
            "seed": SEED,
            "execute_expensive": execute_expensive,
            "statement_candidate_bound": STATEMENT_CANDIDATE_BOUND,
            "legacy_global_prefix_scan_removed": True,
            "provider_hard_limit_bytes": PROVIDER_HARD_LIMIT_BYTES,
            "graph_admitted_bound": GRAPH_ADMITTED_BOUND,
            "graph_scanned_bound": GRAPH_SCANNED_BOUND,
            "graph_hops": [0, 1, 2],
            "max_provider_statements": MAX_PROVIDER_STATEMENTS,
            "statements_per_revision": STATEMENTS_PER_REVISION,
            "packet_max_fragments": PACKET_MAX_FRAGMENTS,
        },
        "scale_reports": reports,
        "overall": {
            "status": "not_released",
            "release_gate_passed": False,
            "executed_count": executed,
            "fail_count": failed,
            "not_executed_count": len(not_executed),
            "not_executed": not_executed,
        },
        "limitations": [
            "Synthetic, temporary, claim-ineligible diagnostic; never a competitive, "
            "RC, GA, or SOTA claim.",
            "Expensive lanes are never replaced by the 101-statement smoke lane.",
            "The runner does not read credentials, network data, Gold/scorer material, "
            "or private Vaults.",
            "No truncation check is passable without explicit bound evidence and an "
            "emitted Gap/Receipt.",
        ],
        "rerun_commands": [
            "uv run --frozen python -m benchmarks.v013.query_graph_scale "
            "--output REPORT.json --scale 101",
            "uv run --frozen python -m benchmarks.v013.query_graph_scale "
            "--output REPORT.json --scale 10000 --execute-expensive",
            "uv run --frozen python -m benchmarks.v013.query_graph_scale "
            "--output REPORT.json --scale 100000 --execute-expensive",
        ],
    }
    report["report_sha256"] = sha256_bytes(canonical_json(_digest_body(report)).encode("utf-8"))
    return report


def run_diagnostic(
    *,
    scales: Sequence[int] = DEFAULT_SCALES,
    scale: int | None = None,
    execute_expensive: bool = False,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Compatibility entry point for benchmark callers."""

    selected = (scale,) if scale is not None else scales
    return build_report(
        scales=selected,
        execute_expensive=execute_expensive,
        workspace=workspace,
    )


def verify_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["report must be an object"]}
    report = dict(value)
    errors: list[str] = []
    try:
        schema = json.loads((_repo_root() / SCHEMA_RELATIVE_PATH).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors.extend(
            error.message
            for error in Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
                report
            )
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        errors.append(f"schema validation unavailable: {type(error).__name__}")
    if report.get("report_sha256") != sha256_bytes(
        canonical_json(_digest_body(report)).encode("utf-8")
    ):
        errors.append("report digest mismatch")
    if (
        report.get("claim_eligible") is not False
        or report.get("competitive_claim_eligible") is not False
    ):
        errors.append("claim eligibility is not fail-closed")
    if report.get("release_gate_passed") is not False:
        errors.append("release gate is not fail-closed")
    if _LOCAL_PATH.search(json.dumps(report, ensure_ascii=False, sort_keys=True)):
        errors.append("report contains a local absolute path")
    for item in report.get("scale_reports", []):
        if (
            isinstance(item, Mapping)
            and item.get("status") == "not_executed"
            and not item.get("reason")
        ):
            errors.append("not_executed scale lacks a reason")
    return {"valid": not errors, "errors": errors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the claim-ineligible v0.13 Query/Graph scale diagnostic."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scale", type=int, choices=SCALE_CHOICES, action="append")
    parser.add_argument("--execute-expensive", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(
        scales=tuple(args.scale) if args.scale else DEFAULT_SCALES,
        execute_expensive=args.execute_expensive,
    )
    output = args.output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0 if verify_report(report)["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
