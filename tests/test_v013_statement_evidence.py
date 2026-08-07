from __future__ import annotations

from pathlib import Path

import pytest

from deeplaw.compilation.applicability import applicability_digest, policy_digest
from deeplaw.compilation.coordinator import CompilationCoordinator, _artifact
from deeplaw.compilation.models import COMPILER_GRANT_OPERATIONS
from deeplaw.compilation.profiles import SEMANTIC_DUTIES, compiler_profile
from deeplaw.evidence import (
    StatementEvidenceStore,
    build_input_set_sha256,
    statement_sha256,
    validate_statement,
    validate_statement_plans,
)
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes, stable_id


def _statement(*, text: str, source_ref: dict, start: int = 0) -> dict:
    source_refs = [source_ref]
    return {
        "ordinal": 1,
        "char_start": start,
        "char_end": start + len(text),
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


def test_statement_validation_uses_explicit_codepoint_span() -> None:
    source_ref = {
        "source_revision_id": "sourcerev_" + "a" * 24,
        "fragment_id": "fragment_" + "b" * 24,
        "locator": "section:1",
        "quote_sha256": "c" * 64,
    }
    value = _statement(text="same", source_ref=source_ref, start=5)
    assert validate_statement(value, body="same same same")["statement_text"] == "same"
    with pytest.raises(ValueError, match="spans overlap"):
        validate_statement_plans(
            [
                {
                    "packet_id": "packet_" + "d" * 24,
                    "object_action_ordinal": 1,
                    "statements": [
                        value,
                        {
                            **_statement(text="same same", source_ref=source_ref, start=0),
                            "ordinal": 2,
                        },
                    ],
                }
            ],
            action_bodies={("packet_" + "d" * 24, 1): "same same same"},
            action_kinds={("packet_" + "d" * 24, 1): "claim"},
        )


def test_statement_plan_forced_kind_and_evidence_rules() -> None:
    source_ref = {
        "source_revision_id": "sourcerev_" + "a" * 24,
        "fragment_id": "fragment_" + "b" * 24,
        "locator": "section:1",
        "quote_sha256": "c" * 64,
    }
    target = ("packet_" + "d" * 24, 1)
    with pytest.raises(ValueError, match="forced Knowledge kind"):
        validate_statement_plans(
            [], action_bodies={target: "summary"}, action_kinds={target: "synthesis"}
        )
    unsupported = _statement(text="summary", source_ref=source_ref)
    unsupported["support_status"] = "contested"
    unsupported["source_refs"] = []
    unsupported["gaps"] = [{"gap_id": "gap-1", "reason": "Opposing evidence is absent."}]
    unsupported["input_set_sha256"] = build_input_set_sha256(
        source_refs=[],
        knowledge_revision_refs=[],
        relation_revision_refs=[],
        valid_from=None,
        valid_to=None,
        statement_type="factual",
        support_status="contested",
        limitation=None,
        gaps=unsupported["gaps"],
    )
    assert validate_statement(unsupported, body="summary")["support_status"] == "contested"


def _prepared_v3_run(
    tmp_path: Path,
    *,
    root: Path | None = None,
    grant_id: str | None = None,
    source_name: str = "source.md",
    source_text: str = "# Source\nA durable source statement.",
    semantic_key: str = "statement:test",
) -> tuple[Path, str, str, dict, dict]:
    root = root or (tmp_path / "vault")
    if not (root / ".deeplaw" / "ledger.sqlite3").exists():
        initialize_knowledge_vault(root, name="statement-evidence", scope="project")
        initialize_autonomous_core(root)
    source = tmp_path / source_name
    source.write_text(source_text, encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault, source, source_kind="document", confirm_no_case_data=True
        )
    if grant_id is None:
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            grant_id = store.enable_grant(
                writer_id="statement-agent", operations=COMPILER_GRANT_OPERATIONS
            )["grant_id"]
    profile = compiler_profile(version="3")
    coordinator = CompilationCoordinator(root)
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version="3",
        host_identity="statement-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        confirm_no_case_data=True,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    assert packet is not None
    fragment = packet["fragments"][0]
    source_ref = {
        "source_revision_id": packet["source_revision_id"],
        "fragment_id": fragment["fragment_id"],
        "locator": fragment["locator"],
        "quote_sha256": fragment["text_sha256"],
    }
    action = {
        "action": "create",
        "kind": "claim",
        "semantic_key": semantic_key,
        "knowledge_id": None,
        "expected_revision_id": None,
        "title": "Source",
        "body": fragment["text"],
        "aliases": [],
        "epistemic_state": "supported",
        "source_refs": [source_ref],
        "assertion": None,
        "tags": [],
        "valid_from": None,
        "valid_to": None,
        "applicability": {
            "description": "This source.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": None,
        "reason": "Persist an evidence-bound statement.",
    }
    plan = {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": [action],
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [],
        "coverage": {
            "packet_fragment_count": 1,
            "covered_fragment_ids": [fragment["fragment_id"]],
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
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    statement = _statement(text=fragment["text"], source_ref=source_ref)
    facts = {
        "source_present": True,
        "source_admitted": True,
        "source_nonempty": True,
        "media_type": "text/markdown",
        "byte_size": 1,
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
    facts_sha256 = sha256_bytes(canonical_json(facts).encode("utf-8"))
    reports = []
    for duty_type in SEMANTIC_DUTIES:
        reports.append(
            {
                "duty_id": stable_id("duty", begun["compilation_run_id"], duty_type),
                "duty_type": duty_type,
                "required": False,
                "applicability": "not_applicable",
                "status": "omitted_with_reason",
                "output_refs": [],
                "evidence_refs": [],
                "reason": "Synthetic commit-boundary fixture.",
                "unresolved_items": [],
                "omission_reason": "Not exercised by this unit fixture.",
                "deterministic_basis": {
                    "rule_id": "statement-test-v1",
                    "facts": facts,
                    "stable_refs": [],
                    "facts_sha256": facts_sha256,
                    "reason": "Synthetic commit-boundary fixture.",
                },
            }
        )
    policy_sha256 = policy_digest()
    applicability_sha256 = applicability_digest(
        {
            report["duty_type"]: {
                "applicability": report["applicability"],
                "deterministic_basis": report["deterministic_basis"],
            }
            for report in reports
        }
    )
    inventory_value = {
        "coverage": {
            "applicability_policy_sha256": policy_sha256,
            "applicability_digest": applicability_sha256,
            "compilation_run_id": begun["compilation_run_id"],
        },
    }
    inventory_value["inventory_sha256"] = sha256_bytes(
        canonical_json(inventory_value).encode("utf-8")
    )
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        inventory_digest, _ = _artifact(
            store,
            value=inventory_value,
            role="semantic_inventory",
            created_at=store._next_transaction_time(),
        )
        store.connection.execute(
            """
            UPDATE semantic_compilation_runs_v2
            SET inventory_sha256 = ?
            WHERE compilation_run_id = ?
            """,
            (inventory_value["inventory_sha256"], begun["compilation_run_id"]),
        )
        store.connection.execute(
            """
            INSERT INTO semantic_inventories_v1(
                artifact_sha256, inventory_sha256, inventory_id,
                compilation_run_id, observation_count, packet_count,
                truncated, recorded_at
            ) VALUES (?, ?, ?, ?, 0, 1, 0, ?)
            """,
            (
                inventory_digest,
                inventory_value["inventory_sha256"],
                "semanticinventory_" + begun["compilation_run_id"][-24:],
                begun["compilation_run_id"],
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
        "source_revision_id": packet["source_revision_id"],
        "expected_audit_head": begun["input_audit_head"],
        "inventory_sha256": inventory_value["inventory_sha256"],
        "finalization_packet_id": "finalization_" + "e" * 24,
        "applicability_policy_sha256": policy_sha256,
        "applicability_digest": applicability_sha256,
        "packet_plans": [{"packet_id": packet["packet_id"]}],
        "statement_plans": [
            {
                "packet_id": packet["packet_id"],
                "object_action_ordinal": 1,
                "statements": [statement],
            }
        ]
        ,
        "observation_dispositions": [],
        "duty_reports": reports,
        "semantic_status": "partial",
        "warnings": [],
    }
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        digest, _ = _artifact(
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
            (digest, begun["compilation_run_id"]),
        )
        store.connection.commit()
    return root, grant_id, begun["compilation_run_id"], publication, statement


def test_statement_commit_maps_receipt_and_replay(tmp_path: Path) -> None:
    root, grant_id, run_id, _publication, _statement_value = _prepared_v3_run(tmp_path)
    CompilationCoordinator(root).commit(
        grant_id=grant_id, compilation_run_id=run_id, confirm_no_case_data=True
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        revision_id = store.connection.execute(
            "SELECT revision_id FROM knowledge_revisions_v3 ORDER BY revision_id DESC LIMIT 1"
        ).fetchone()["revision_id"]
        statement_id_value = store.connection.execute(
            "SELECT statement_id FROM knowledge_statements_v1"
        ).fetchone()["statement_id"]
        verification = store.verify()
        assert verification["valid"] is True, verification["failures"]
    evidence = StatementEvidenceStore(root)
    statement_result = evidence.statement(statement_id_value)
    assert statement_result["status"] == "present"
    assert statement_result["is_current"] is True
    assert statement_result["current_supported"] is True
    assert evidence.receipt(statement_id_value)["status"] == "present"
    map_value = evidence.map_for_revision(revision_id)
    assert map_value["status"] == "current"
    assert map_value["is_current"] is True
    assert map_value["maps"][0]["char_start"] == 0


def test_statement_map_marks_human_revision_as_historical(tmp_path: Path) -> None:
    root, _grant_id, run_id, _publication, _statement_value = _prepared_v3_run(tmp_path)
    coordinator = CompilationCoordinator(root)
    coordinator.commit(
        grant_id=_grant_id, compilation_run_id=run_id, confirm_no_case_data=True
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        revision = store.connection.execute(
            """
            SELECT knowledge_id, revision_id
            FROM knowledge_revisions_v3
            WHERE revision_id IN (
                SELECT knowledge_revision_id FROM knowledge_statements_v1
            )
            LIMIT 1
            """
        ).fetchone()
        assert revision is not None
        old_revision_id = revision["revision_id"]
        knowledge_id = revision["knowledge_id"]
        statement_id_value = store.connection.execute(
            "SELECT statement_id FROM knowledge_statements_v1 LIMIT 1"
        ).fetchone()["statement_id"]
        current = store.get_current(knowledge_id)
        workspace_path = root / current["workspace_path"]
        original = workspace_path.read_text(encoding="utf-8")

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        owner_grant = store.enable_grant(
            writer_id="human-owner",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )["grant_id"]
        workspace_path.write_text(
            original.replace("A durable source statement.", "A human-edited body."),
            encoding="utf-8",
        )
        reconciled = store.reconcile_workspace(
            grant_id=owner_grant,
            confirm_no_case_data=True,
        )
        assert reconciled["committed"]
        new_revision_id = reconciled["committed"][0]["revision_id"]
        assert new_revision_id != old_revision_id

    evidence = StatementEvidenceStore(root)
    old_map = evidence.map_for_revision(old_revision_id)
    assert old_map["status"] == "historical"
    assert old_map["is_current"] is False
    assert old_map["current_revision_id"] == new_revision_id
    new_map = evidence.map_for_revision(new_revision_id)
    assert new_map["status"] == "missing"
    assert new_map["knowledge_revision_id"] == new_revision_id
    assert new_map["current_revision_id"] == new_revision_id
    assert new_map["is_current"] is True
    assert new_map["current_supported"] is False
    old_statement = evidence.statement(statement_id_value)
    assert old_statement["status"] == "historical"
    assert old_statement["is_current"] is False
    assert old_statement["current_revision_id"] == new_revision_id
    assert old_statement["current_supported"] is False


def test_statement_reader_marks_only_changed_source_dependencies_stale(
    tmp_path: Path,
) -> None:
    root, grant_id, run_a, _publication_a, statement_a = _prepared_v3_run(
        tmp_path,
        source_name="source-a.md",
        source_text="# Source A\nA statement before the successor.",
        semantic_key="statement:test-a",
    )
    coordinator = CompilationCoordinator(root)
    old_source_revision_id = statement_a["source_refs"][0]["source_revision_id"]
    coordinator.commit(
        grant_id=grant_id, compilation_run_id=run_a, confirm_no_case_data=True
    )
    _root, grant_b, run_b, _publication_b, _statement_b = _prepared_v3_run(
        tmp_path,
        root=root,
        grant_id=grant_id,
        source_name="source-b.md",
        source_text="# Source B\nB statement remains unchanged.",
        semantic_key="statement:test-b",
    )
    assert grant_b == grant_id
    coordinator.commit(
        grant_id=grant_b, compilation_run_id=run_b, confirm_no_case_data=True
    )
    source_a = tmp_path / "source-a.md"
    source_a.write_text("# Source A\nA statement after the successor.", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        successor = compile_source(
            vault,
            source_a,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(successor["source"]["source_id"])
        vault.approve_source_assets(
            successor["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="statement-freshness-test",
            review_reason="Activate the changed Source Revision.",
        )
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=old_source_revision_id,
        replacement_source_revision_id=successor["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert report["changed_fragment_ids"]

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        rows = store.connection.execute(
            """
            SELECT statement_id, statement_text
            FROM knowledge_statements_v1
            ORDER BY statement_text
            """
        ).fetchall()
    assert [row["statement_text"] for row in rows] == [
        "A statement before the successor.",
        "B statement remains unchanged.",
    ]
    evidence = StatementEvidenceStore(root)
    stale = evidence.statement(rows[0]["statement_id"])
    unaffected = evidence.statement(rows[1]["statement_id"])
    assert stale["status"] == "stale"
    assert stale["freshness"] == "stale"
    assert stale["current_supported"] is False
    assert unaffected["status"] == "present"
    assert unaffected["freshness"] == "fresh"
    assert unaffected["current_supported"] is True


def test_statement_commit_rolls_back_on_tampered_input_set(tmp_path: Path) -> None:
    root, grant_id, run_id, publication, statement_value = _prepared_v3_run(tmp_path)
    statement_value["input_set_sha256"] = "0" * 64
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        digest, _ = _artifact(
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
            (digest, run_id),
        )
        store.connection.commit()
    with pytest.raises(ValueError, match="input-set digest"):
        CompilationCoordinator(root).commit(
            grant_id=grant_id, compilation_run_id=run_id, confirm_no_case_data=True
        )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.connection.execute(
            "SELECT COUNT(*) FROM knowledge_revisions_v3"
        ).fetchone()[0] == 0
        assert store.connection.execute(
            "SELECT COUNT(*) FROM knowledge_statements_v1"
        ).fetchone()[0] == 0
