"""Deterministic black-box reproductions for the v0.13 Query/Graph P0 tail.

These tests intentionally do not patch the retrieval implementation.  The scale
fixture is built through the public Source Revision -> Compilation -> commit path,
then queried through :class:`PurposeAwareRetrievalService`.  A few deliberately
skipped cases document the exact setup that is not run in this narrow regression
file; they are not ``xfail`` cases and must not be read as passed coverage.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from deeplaw.compilation.applicability import applicability_digest, policy_digest
from deeplaw.compilation.coordinator import CompilationCoordinator, _artifact
from deeplaw.compilation.models import COMPILER_GRANT_OPERATIONS
from deeplaw.compilation.profiles import SEMANTIC_DUTIES, compiler_profile
from deeplaw.evidence import build_input_set_sha256, statement_sha256
from deeplaw.knowledge_autonomy import (
    RELATION_PREDICATES as AUTONOMOUS_RELATION_PREDICATES,
)
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.util import canonical_json, sha256_bytes, stable_id
from tests.test_v013_query_v6 import _committed_vault

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RELATION_SCHEMA = json.loads(
    (_REPO_ROOT / "contracts" / "knowledge-relation.v3.schema.json").read_text(
        encoding="utf-8"
    )
)
_SCALE_SEED = 0xD33F013


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


def _scale_source_text(statement_count: int) -> str:
    """Return a seeded permutation split into bounded, whole Source sections."""

    rng = random.Random(_SCALE_SEED + statement_count)
    permutation = list(range(statement_count))
    rng.shuffle(permutation)
    lines: list[str] = []
    # A heading starts a new Source Fragment.  Keeping each section below the
    # 12k section splitter bound makes the requested count exact (no split-line
    # phantom statements) while retaining randomized ordinals and IDs.
    for group_start in range(0, statement_count, 250):
        lines.append(f"# Scale group {group_start // 250:03d}")
        for index in permutation[group_start : group_start + 250]:
            token = rng.randrange(1_000_000_000)
            lines.append(f"seeded-{token:09d}-statement-{index:06d}.")
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
            "code": False,
            "table": False,
            "list": False,
            "timeline": False,
            "question": False,
            "procedure": False,
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


def _scale_reports(
    *,
    compilation_run_id: str,
    source: Path,
) -> list[dict[str, Any]]:
    facts, facts_sha256 = _scale_facts(source)
    return [
        {
            "duty_id": stable_id("duty", compilation_run_id, duty_type),
            "duty_type": duty_type,
            "required": False,
            "applicability": "not_applicable",
            "status": "omitted_with_reason",
            "output_refs": [],
            "evidence_refs": [],
            "reason": "Deterministic P0 scale fixture.",
            "unresolved_items": [],
            "omission_reason": "Synthetic fixture does not exercise semantic duties.",
            "deterministic_basis": {
                "rule_id": "query-graph-p0-v1",
                "facts": facts,
                "stable_refs": [],
                "facts_sha256": facts_sha256,
                "reason": "Deterministic P0 scale fixture.",
            },
        }
        for duty_type in SEMANTIC_DUTIES
    ]


def _commit_scale_vault(tmp_path: Path, statement_count: int) -> Path:
    """Commit ``statement_count`` source-bound Statements without a model call."""

    root = tmp_path / f"scale-{statement_count}"
    source = tmp_path / f"scale-{statement_count}.md"
    source.write_text(_scale_source_text(statement_count), encoding="utf-8")
    initialize_knowledge_vault(root, name="query graph p0", scope="project")
    initialize_autonomous_core(root)
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="query-graph-p0-scale",
            operations=COMPILER_GRANT_OPERATIONS,
            max_request_bytes=320 * 1024,
            max_mutations_per_minute=120,
            max_objects=100_000,
        )["grant_id"]

    profile = compiler_profile(version="3")
    coordinator = CompilationCoordinator(root)
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version="3",
        host_identity="query-graph-p0-scale",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        confirm_no_case_data=True,
        packet_max_fragments=128,
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
                if not line.strip():
                    cursor += len(line) + 1
                    continue
                text = line.strip()
                start = fragment["text"].find(text, cursor)
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
                    "semantic_key": f"query-graph-p0:scale:{action_count}",
                    "knowledge_id": None,
                    "expected_revision_id": None,
                    "title": f"P0 scale group {action_count}",
                    "body": fragment["text"],
                    "aliases": [],
                    "epistemic_state": "supported",
                    "source_refs": [source_ref],
                    "assertion": None,
                    "tags": [],
                    "valid_from": None,
                    "valid_to": None,
                    "applicability": {
                        "description": "Deterministic P0 scale fixture.",
                        "scopes": [],
                        "conditions": [],
                        "exclusions": [],
                    },
                    "synthesis_inputs": None,
                    "reason": "Deterministic P0 scale fixture.",
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
    assert packet_count >= 1
    assert coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )["valid"] is True

    reports = _scale_reports(
        compilation_run_id=begun["compilation_run_id"],
        source=source,
    )
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
    inventory["inventory_sha256"] = sha256_bytes(
        canonical_json(inventory).encode("utf-8")
    )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        inventory_digest, _ = _artifact(
            store,
            value=inventory,
            role="semantic_inventory",
            created_at=store._next_transaction_time(),
        )
        store.connection.execute(
            """
            UPDATE semantic_compilation_runs_v2
            SET inventory_sha256 = ?
            WHERE compilation_run_id = ?
            """,
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
        "source_revision_id": compiled["identity"]["source_revision_id"],
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
            """
            UPDATE semantic_compilation_runs_v2
            SET publication_plan_sha256 = ?
            WHERE compilation_run_id = ?
            """,
            (publication_digest, begun["compilation_run_id"]),
        )
        store.connection.commit()
    CompilationCoordinator(root).commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    return root


def test_v6_statement_tail_scan_is_bounded_but_position_independent(
    tmp_path: Path,
) -> None:
    root = _commit_scale_vault(tmp_path, 5_001)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        rows = store.connection.execute(
            """
            SELECT statement_text
            FROM knowledge_statements_v1
            ORDER BY knowledge_revision_id, ordinal, statement_id
            """
        ).fetchall()
    assert len(rows) == 5_001
    target_positions = (0, 2_500, 5_000)
    target_rows = [rows[index]["statement_text"] for index in target_positions]
    service = PurposeAwareRetrievalService(root)
    results = [
        service.query(
            target,
            query_plan_version="6",
            applicable_duties=("primary_answer",),
            projection="audit",
        )
        for target in target_rows
    ]
    for position, target, result in zip(
        target_positions, target_rows, results, strict=True
    ):
        assert len(result["capsule"]["statements"]) <= 8
        assert len(canonical_json(result["capsule"]).encode("utf-8")) <= 65_536
        assert result["query_plan"]["compiled_candidate_count"] <= 512
        assert result["query_plan"]["discovery"]["statement_candidate_limit"] == 512
        assert result["query_plan"]["discovery"]["selected_revision_count"] <= 20
        assert len(result["local_audit"]["candidates"]) <= 512
        # The source/ordinal/ID ordering is intentionally randomized by the
        # seeded fixture.  A tail position must not decide whether an exact
        # Statement is admitted to the provider-visible capsule.
        assert any(
            item["statement_text"] == target for item in result["statements"]
        ), f"exact statement at scan position {position} was not selected"


@pytest.mark.qualification
@pytest.mark.skip(
    reason="not_executed: 10,000-Statement commit is reserved for the full stress lane",
)
def test_v6_statement_scan_10000_not_executed(tmp_path: Path) -> None:
    _commit_scale_vault(tmp_path, 10_000)


@pytest.mark.qualification
@pytest.mark.skip(
    reason="not_executed: 100,000-Statement commit exceeds this narrow timeout-safe lane",
)
def test_v6_statement_scan_100000_not_executed(tmp_path: Path) -> None:
    _commit_scale_vault(tmp_path, 100_000)


def test_v6_tail_controls_are_not_silently_discarded(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    service = PurposeAwareRetrievalService(root)
    baseline = service.query(
        "A durable source statement.",
        query_plan_version="6",
        graph_hops=0,
        retrieval_mode="exact",
        force_canonical_lexical=False,
    )
    varied = service.query(
        "A durable source statement.",
        query_plan_version="6",
        graph_hops=2,
        retrieval_mode="hybrid",
        force_canonical_lexical=True,
    )
    # The v4/v5 public query plans carry graph_hops in the budget.  v6 accepts
    # all three controls but must leave an observable plan/receipt witness when
    # they change; equality is the minimal black-box reproduction of a dropped
    # tail-control set.
    assert baseline["query_plan"] != varied["query_plan"]
    assert baseline["query_plan"]["retrieval_controls"] == {
        "graph_hops": 0,
        "retrieval_mode": "exact",
        "force_canonical_lexical": False,
    }
    assert varied["query_plan"]["retrieval_controls"] == {
        "graph_hops": 2,
        "retrieval_mode": "hybrid",
        "force_canonical_lexical": True,
    }
    assert baseline["local_audit"]["query_plan_sha256"] != (
        varied["local_audit"]["query_plan_sha256"]
    )


def test_v6_retrieval_mode_is_validated_and_propagated(tmp_path: Path) -> None:
    root = _committed_vault(tmp_path)
    service = PurposeAwareRetrievalService(root)
    with pytest.raises(ValueError, match="retrieval mode"):
        service.query(
            "A durable source statement.",
            query_plan_version="6",
            retrieval_mode="not-a-retrieval-mode",
        )
    with pytest.raises(ValueError, match="canonical lexical"):
        service.query(
            "A durable source statement.",
            query_plan_version="6",
            force_canonical_lexical="false",  # type: ignore[arg-type]
        )


def test_underlying_recall_controls_are_observable_before_v6_adapter(
    tmp_path: Path,
) -> None:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        lexical = store.recall(
            "A durable source statement.",
            graph_hops=0,
            retrieval_mode="lexical",
            force_canonical_lexical=True,
        )
        graph = store.recall(
            "A durable source statement.",
            graph_hops=2,
            retrieval_mode="graph",
            force_canonical_lexical=False,
        )
        one_hop = store.recall(
            "A durable source statement.",
            graph_hops=1,
            retrieval_mode="hybrid",
            force_canonical_lexical=False,
        )
    assert lexical["query_plan"]["budget"]["graph_hops"] == 0
    assert one_hop["query_plan"]["budget"]["graph_hops"] == 1
    assert graph["query_plan"]["budget"]["graph_hops"] == 2
    assert lexical["query_plan"]["retrieval_mode"] == "lexical"
    assert graph["query_plan"]["retrieval_mode"] == "graph"


def test_relation_predicate_schema_and_runtime_enum_are_in_parity() -> None:
    predicate_schema = _RELATION_SCHEMA["properties"]["predicate"]
    assert predicate_schema.get("enum") == sorted(AUTONOMOUS_RELATION_PREDICATES)


def _graph_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], str]:
    root = _committed_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        source_revision_id = store.connection.execute(
            "SELECT revision_id FROM knowledge_revisions_v3 ORDER BY revision_id LIMIT 1"
        ).fetchone()["revision_id"]
        seed = store.connection.execute(
            "SELECT knowledge_id FROM knowledge_objects_v3 ORDER BY knowledge_id LIMIT 1"
        ).fetchone()["knowledge_id"]
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="query-graph-p0-relations",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
            max_objects=100,
        )["grant_id"]
        nodes = {"seed": seed}
        for index in range(1, 8):
            response = store.remember(
                grant_id=grant_id,
                idempotency_key=f"graph-node-{index}",
                title=f"Graph node {index}",
                body=f"Deterministic graph node {index}.",
                kind="claim",
                semantic_key=f"query-graph-p0:node:{index}",
                source_refs=[{"revision_id": source_revision_id}],
                confirm_no_case_data=True,
            )
            nodes[str(index)] = response["knowledge_id"]

        def edge(key: str, subject: str, predicate: str, object_id: str, **kwargs: Any) -> None:
            store.add_relation(
                grant_id=grant_id,
                idempotency_key=key,
                subject_knowledge_id=nodes[subject],
                predicate=predicate,
                object_knowledge_id=nodes[object_id],
                evidence_refs=[{"revision_id": source_revision_id}],
                confirm_no_case_data=True,
                **kwargs,
            )

        # Tail edge + hub, a depth-2 chain, a ring, a contradiction, and a
        # future temporal edge all coexist in one governed relation view.
        edge("edge-tail", "seed", "depends_on", "1")
        edge("edge-hub-2", "seed", "supports", "2")
        edge("edge-hub-3", "seed", "supports", "3")
        edge("edge-chain-34", "3", "depends_on", "4")
        edge("edge-chain-45", "4", "depends_on", "5")
        edge("edge-ring-56", "5", "related_to", "6")
        edge("edge-ring-65", "6", "related_to", "5")
        edge("edge-contradiction", "1", "contradicts", "2")
        edge(
            "edge-temporal",
            "2",
            "applies_to",
            "3",
            valid_from="2099-01-01T00:00:00Z",
            valid_to="2100-01-01T00:00:00Z",
        )
        current = store.get_current(nodes["7"])
        edge("edge-broken", "6", "depends_on", "7")
        store.forget(
            grant_id=grant_id,
            idempotency_key="forget-broken-endpoint",
            knowledge_id=nodes["7"],
            expected_revision_id=current["revision_id"],
            reason="Create a deterministic broken endpoint for admission coverage.",
            confirm_no_case_data=True,
        )
    return root, nodes, source_revision_id


def test_relation_graph_edges_have_no_position_bias_and_temporal_gates(
    tmp_path: Path,
) -> None:
    root, nodes, _source_revision_id = _graph_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        current = store.graph(limit=100)
        assert len(current["relations"]) >= 7
        predicates = {relation["predicate"] for relation in current["relations"]}
        assert {"depends_on", "supports", "related_to", "contradicts"} <= predicates
        assert current["budget"]["selected_relations"] == len(current["relations"])
        tail = store.graph(knowledge_id=nodes["seed"], limit=100)
        assert {relation["object_knowledge_id"] for relation in tail["relations"]} == {
            nodes["1"],
            nodes["2"],
            nodes["3"],
        }
        before_temporal = store.graph(as_of="2098-12-31T23:59:59Z", limit=100)
        after_temporal = store.graph(as_of="2099-01-02T00:00:00Z", limit=100)
        assert not any(
            relation["predicate"] == "applies_to"
            for relation in before_temporal["relations"]
        )
        assert any(
            relation["predicate"] == "applies_to"
            for relation in after_temporal["relations"]
        )
        assert not any(
            relation["object_knowledge_id"] == nodes["7"]
            for relation in current["relations"]
        )


@pytest.mark.qualification
@pytest.mark.skip(
    reason="not_executed: 500/5,000 relation-edge truncation needs a dedicated bulk fixture",
)
def test_relation_graph_scan_500_and_5000_truncation_not_executed(tmp_path: Path) -> None:
    _graph_fixture(tmp_path)
