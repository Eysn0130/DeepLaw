from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.compilation.finalization import SemanticFinalizer
from deeplaw.compilation.models import SEMANTIC_COMPILER_GRANT_OPERATIONS
from deeplaw.compilation.profiles import compiler_profile
from deeplaw.compilation.semantic import SemanticCompilationService
from deeplaw.context_compiler import verify_capsule
from deeplaw.evidence import (
    StatementEvidenceStore,
    build_input_set_sha256,
    statement_sha256,
    validate_statement,
)
from deeplaw.evidence.statements import canonical_support_sets
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    create_autonomous_snapshot,
    initialize_autonomous_core,
    restore_autonomous_snapshot,
    verify_autonomous_snapshot,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.util import canonical_json, sha256_bytes

_SUPPORT_CLAIM = "Orchid archive policy requires amber labels."
_DEPENDENT_CLAIM = "Orchid archive policy requires amber labels in delegated records."
_FIRST_SOURCE_TEXT = (
    f"{_SUPPORT_CLAIM} The first independent archive record states this requirement."
)
_SECOND_SOURCE_TEXT = (
    f"{_SUPPORT_CLAIM} The second independent archive record corroborates this requirement."
)


def _source_ref(index: int) -> dict[str, str]:
    return {
        "source_revision_id": f"sourcerev_{index:024x}",
        "fragment_id": f"fragment_{index + 0x100:024x}",
        "locator": f"section:{index}",
        "quote_sha256": f"{index:064x}",
    }


def _support_group(
    *,
    source_refs: list[dict[str, str]] | None = None,
    knowledge_revision_refs: list[str] | None = None,
    relation_revision_refs: list[str] | None = None,
) -> dict[str, object]:
    return {
        "source_refs": [] if source_refs is None else source_refs,
        "knowledge_revision_refs": (
            [] if knowledge_revision_refs is None else knowledge_revision_refs
        ),
        "relation_revision_refs": (
            [] if relation_revision_refs is None else relation_revision_refs
        ),
    }


def _statement_v2(
    *,
    source_refs: list[dict[str, str]],
    support_sets: list[dict[str, object]],
    text: str = "A complete support-set statement.",
    digest: bool = True,
) -> dict[str, object]:
    knowledge_revision_refs = sorted(
        {
            reference
            for group in support_sets
            for reference in group["knowledge_revision_refs"]
        }
    )
    relation_revision_refs = sorted(
        {
            reference
            for group in support_sets
            for reference in group["relation_revision_refs"]
        }
    )
    input_set_sha256 = "0" * 64
    if digest:
        input_set_sha256 = build_input_set_sha256(
            source_refs=source_refs,
            knowledge_revision_refs=knowledge_revision_refs,
            relation_revision_refs=relation_revision_refs,
            valid_from=None,
            valid_to=None,
            statement_type="factual",
            support_status="supported",
            limitation=None,
            gaps=[],
            support_sets=support_sets,
        )
    return {
        "schema_version": "deeplaw.knowledge-statement/v2",
        "ordinal": 1,
        "statement_text": text,
        "statement_sha256": statement_sha256(text),
        "statement_type": "factual",
        "support_status": "supported",
        "source_refs": source_refs,
        "knowledge_revision_refs": knowledge_revision_refs,
        "relation_revision_refs": relation_revision_refs,
        "valid_from": None,
        "valid_to": None,
        "limitation": None,
        "gaps": [],
        "input_set_sha256": input_set_sha256,
        "support_sets": support_sets,
    }


def test_v1_statement_bytes_and_digest_remain_unchanged() -> None:
    source_ref = {
        "source_revision_id": "sourcerev_" + "a" * 24,
        "fragment_id": "fragment_" + "b" * 24,
        "locator": "section:1",
        "quote_sha256": "c" * 64,
    }
    statement_text = "A stable v1 statement."
    input_set_sha256 = build_input_set_sha256(
        source_refs=[source_ref],
        knowledge_revision_refs=[],
        relation_revision_refs=[],
        valid_from=None,
        valid_to=None,
        statement_type="factual",
        support_status="supported",
        limitation=None,
        gaps=[],
    )
    assert input_set_sha256 == "a49f551526c23b6b7d18ebc69617fa26667d6e6c47b8562e670bec517dc568bb"
    value = {
        "schema_version": "deeplaw.knowledge-statement/v1",
        "ordinal": 1,
        "statement_text": statement_text,
        "statement_sha256": "f228ff0ce1bbb3a98391b8f405ac962ab5d255e2309cabaa9ea8f6984c9026f8",
        "statement_type": "factual",
        "support_status": "supported",
        "source_refs": [source_ref],
        "knowledge_revision_refs": [],
        "relation_revision_refs": [],
        "valid_from": None,
        "valid_to": None,
        "limitation": None,
        "gaps": [],
        "input_set_sha256": input_set_sha256,
    }
    normalized = validate_statement(value, body=statement_text)
    expected_bytes = (
        b'{"gaps":[],"input_set_sha256":"a49f551526c23b6b7d18ebc69617fa26667d6e6c47b8562e670bec517dc568bb",'
        b'"knowledge_revision_id":null,"knowledge_revision_refs":[],"limitation":null,"ordinal":1,'
        b'"relation_revision_refs":[],"schema_version":"deeplaw.knowledge-statement/v1",'
        b'"source_refs":[{"fragment_id":"fragment_bbbbbbbbbbbbbbbbbbbbbbbb","locator":"section:1",'
        b'"quote_sha256":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",'
        b'"source_revision_id":"sourcerev_aaaaaaaaaaaaaaaaaaaaaaaa"}],"statement_id":null,'
        b'"statement_sha256":"f228ff0ce1bbb3a98391b8f405ac962ab5d255e2309cabaa9ea8f6984c9026f8",'
        b'"statement_text":"A stable v1 statement.","statement_type":"factual",'
        b'"support_status":"supported","valid_from":null,"valid_to":null}'
    )
    assert canonical_json(normalized).encode("utf-8") == expected_bytes


def test_and_vs_or_support_grouping_changes_input_digest() -> None:
    first = _source_ref(1)
    second = _source_ref(2)
    flat_refs = [first, second]
    conjunction = [_support_group(source_refs=flat_refs)]
    alternatives = [
        _support_group(source_refs=[first]),
        _support_group(source_refs=[second]),
    ]

    normalized_and = validate_statement(
        _statement_v2(source_refs=flat_refs, support_sets=conjunction),
        body="A complete support-set statement.",
    )
    normalized_or = validate_statement(
        _statement_v2(source_refs=flat_refs, support_sets=alternatives),
        body="A complete support-set statement.",
    )
    assert normalized_and["source_refs"] == normalized_or["source_refs"]
    assert normalized_and["input_set_sha256"] != normalized_or["input_set_sha256"]
    assert canonical_support_sets(list(reversed(alternatives))) == canonical_support_sets(
        alternatives
    )


@pytest.mark.parametrize(
    ("case", "source_refs", "support_sets", "message"),
    [
        (
            "missing union",
            [_source_ref(1), _source_ref(2)],
            [_support_group(source_refs=[_source_ref(1)])],
            "support set union",
        ),
        (
            "empty group",
            [_source_ref(1)],
            [_support_group()],
            "empty support set",
        ),
        (
            "duplicate group",
            [_source_ref(1)],
            [
                _support_group(source_refs=[_source_ref(1)]),
                _support_group(source_refs=[_source_ref(1)]),
            ],
            "duplicate alternatives",
        ),
        (
            "over 256 members",
            [_source_ref(index) for index in range(1, 258)],
            [_support_group(source_refs=[_source_ref(index) for index in range(1, 258)])],
            "support (?:source refs|set members) exceed",
        ),
    ],
)
def test_invalid_support_set_groupings_are_rejected(
    case: str,
    source_refs: list[dict[str, str]],
    support_sets: list[dict[str, object]],
    message: str,
) -> None:
    del case
    value = _statement_v2(
        source_refs=source_refs,
        support_sets=support_sets,
        digest=False,
    )
    with pytest.raises(ValueError, match=message):
        validate_statement(value, body="A complete support-set statement.")


def test_tampered_support_grouping_is_rejected() -> None:
    first = _source_ref(1)
    second = _source_ref(2)
    value = _statement_v2(
        source_refs=[first, second],
        support_sets=[
            _support_group(source_refs=[first]),
            _support_group(source_refs=[second]),
        ],
    )
    tampered = deepcopy(value)
    tampered["support_sets"] = [_support_group(source_refs=[first, second])]
    with pytest.raises(ValueError, match="input-set digest is invalid"):
        validate_statement(tampered, body="A complete support-set statement.")


def _observation_plan(packet: dict[str, object]) -> dict[str, object]:
    fragment_ids = [fragment["fragment_id"] for fragment in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-observation-plan/v2",
        "compilation_run_id": packet["compilation_run_id"],
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "observations": [],
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragments": [],
            "ratio": 1.0,
        },
        "warnings": [],
    }


def _packet_plan(
    packet: dict[str, object],
    support_sets: list[dict[str, object]],
    *,
    statement_text: str,
    semantic_key: str = "claim:complete-support-sets",
) -> dict[str, object]:
    fragments = packet["fragments"]
    body = statement_text
    source_refs = [
        {
            "source_revision_id": packet["source_revision_id"],
            "fragment_id": fragment["fragment_id"],
            "locator": fragment["locator"],
            "quote_sha256": fragment["text_sha256"],
        }
        for fragment in fragments
    ]
    action = {
        "action": "create",
        "kind": "claim",
        "semantic_key": semantic_key,
        "knowledge_id": None,
        "expected_revision_id": None,
        "title": "Complete support-set claim",
        "body": body,
        "aliases": [],
        "epistemic_state": "supported",
        "source_refs": source_refs,
        "assertion": None,
        "tags": ["support-set-regression"],
        "valid_from": None,
        "valid_to": None,
        "applicability": {
            "description": "A synthetic source-bound claim.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": None,
        "reason": "Exercise complete support-set persistence.",
    }
    statement = _statement_v2(
        source_refs=source_refs,
        support_sets=support_sets,
        text=body,
    )
    statement["char_start"] = 0
    statement["char_end"] = len(body)
    return {
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
            "packet_fragment_count": len(fragments),
            "covered_fragment_ids": [fragment["fragment_id"] for fragment in fragments],
            "omitted_fragment_ids": [],
            "ratio": 1.0,
            "completeness": "complete",
        },
        "skipped_fragments": [],
        "warnings": [],
    }, statement


def _v4_fixture(
    tmp_path: Path,
    *,
    root: Path | None = None,
    grant_id: str | None = None,
    support_mode: str = "or",
    source_name: str = "source.md",
    semantic_key: str = "claim:complete-support-sets",
    statement_text: str = _SUPPORT_CLAIM,
    knowledge_revision_refs: list[str] | tuple[str, ...] = (),
) -> tuple[Path, str, str, dict, dict, list[dict[str, object]]]:
    root = root or (tmp_path / "vault")
    if not (root / ".deeplaw" / "ledger.sqlite3").exists():
        initialize_knowledge_vault(root, name="complete-support-sets", scope="project")
        initialize_autonomous_core(root)
    source = tmp_path / source_name
    source.write_text(
        "# First source section\n"
        f"{statement_text} The first independent archive record states this requirement.\n\n"
        "# Second source section\n"
        f"{statement_text} The second independent archive record corroborates this requirement.",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="complete-support-sets-fixture",
            review_reason="Activate the synthetic source for support evaluation.",
        )
    if grant_id is None:
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            grant_id = store.enable_grant(
                writer_id="complete-support-sets-agent",
                operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
            )["grant_id"]
    profile = compiler_profile(version="3")
    coordinator = CompilationCoordinator(root)
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version=profile["compiler_profile_version"],
        host_identity="complete-support-sets-agent",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        packet_max_fragments=128,
        confirm_no_case_data=True,
    )
    run_id = begun["compilation_run_id"]
    service = SemanticCompilationService(root)
    packets = []
    while packet := service.next_observation_packet(run_id):
        packets.append(packet)
        service.stage_observations(
            grant_id=grant_id,
            compilation_run_id=run_id,
            plan=_observation_plan(packet),
            confirm_no_case_data=True,
        )
    assert len(packets) == 1
    inventory = service.inventory(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    finalization_packet = service.finalization_packet(run_id)
    packet = packets[0]
    source_refs = [
        {
            "source_revision_id": packet["source_revision_id"],
            "fragment_id": fragment["fragment_id"],
            "locator": fragment["locator"],
            "quote_sha256": fragment["text_sha256"],
        }
        for fragment in packet["fragments"]
    ]
    selected_knowledge_revision_refs = list(knowledge_revision_refs)
    if support_mode == "and":
        raw_support_sets = [
            _support_group(
                source_refs=source_refs,
                knowledge_revision_refs=selected_knowledge_revision_refs,
            )
        ]
    elif support_mode == "dependency_or":
        raw_support_sets = [
            _support_group(
                source_refs=source_refs,
                knowledge_revision_refs=selected_knowledge_revision_refs,
            ),
            _support_group(source_refs=source_refs),
        ]
    else:
        raw_support_sets = [
            _support_group(source_refs=[source_refs[0]]),
            _support_group(source_refs=[source_refs[1]]),
        ]
    support_sets = canonical_support_sets(raw_support_sets)
    packet_plan, statement = _packet_plan(
        packet,
        support_sets,
        statement_text=statement_text,
        semantic_key=semantic_key,
    )
    publication_plan = {
        "schema_version": "deeplaw.semantic-publication-plan/v4",
        "compiler_profile_version": "3",
        "compilation_run_id": run_id,
        "source_revision_id": packet["source_revision_id"],
        "expected_audit_head": packet["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "finalization_packet_id": finalization_packet["finalization_packet_id"],
        "applicability_policy_sha256": finalization_packet[
            "applicability_policy_sha256"
        ],
        "applicability_digest": finalization_packet["applicability_digest"],
        "packet_plans": [packet_plan],
        "statement_plans": [
            {
                "packet_id": packet["packet_id"],
                "object_action_ordinal": 1,
                "statements": [statement],
            }
        ],
        "observation_dispositions": [],
        "duty_reports": deepcopy(finalization_packet["duties"]),
        "semantic_status": "partial",
        "warnings": [],
    }
    return root, grant_id, run_id, publication_plan, statement, support_sets


def test_v4_stage_commit_and_snapshot_restore_preserve_exact_support_sets(
    tmp_path: Path,
) -> None:
    root, grant_id, run_id, publication_plan, statement, support_sets = _v4_fixture(tmp_path)
    finalizer = SemanticFinalizer(root)
    staged = finalizer.stage_publication(
        grant_id=grant_id,
        compilation_run_id=run_id,
        plan=publication_plan,
        confirm_no_case_data=True,
    )
    assert staged["compilation_run_id"] == run_id
    assert staged["idempotent_replay"] is False
    CompilationCoordinator(root).validate(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    committed = finalizer.commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    assert committed["compilation_run_id"] == run_id

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        row = store.connection.execute(
            "SELECT statement_id, knowledge_revision_id FROM knowledge_statements_v1"
        ).fetchone()
        assert row is not None
        statement_id = row["statement_id"]
        revision_id = row["knowledge_revision_id"]
        verification = store.verify()
    assert verification["valid"] is True, verification["failures"]

    evidence = StatementEvidenceStore(root)
    statement_result = evidence.statement(statement_id)
    map_result = evidence.map_for_revision(revision_id)
    receipt_result = evidence.receipt(statement_id)
    assert statement_result["support_sets"] == support_sets
    assert map_result["maps"][0]["support_sets"] == support_sets
    assert receipt_result["support_sets"] == support_sets
    assert statement_result["input_set_sha256"] == statement["input_set_sha256"]
    assert map_result["maps"][0]["input_set_sha256"] == statement["input_set_sha256"]
    assert receipt_result["input_set_sha256"] == statement["input_set_sha256"]

    snapshot = tmp_path / "support-set-snapshot"
    create_autonomous_snapshot(root, snapshot)
    assert verify_autonomous_snapshot(snapshot)["valid"] is True
    restored = tmp_path / "support-set-restored"
    restore_autonomous_snapshot(restored, snapshot=snapshot, confirm=True)
    with AutonomousKnowledgeStore(restored, read_only=True) as store:
        restored_verification = store.verify()
    assert restored_verification["valid"] is True, restored_verification["failures"]
    restored_evidence = StatementEvidenceStore(restored)
    assert restored_evidence.statement(statement_id)["support_sets"] == support_sets
    restored_map = restored_evidence.map_for_revision(revision_id)["maps"][0]
    assert restored_map["support_sets"] == support_sets
    assert restored_evidence.receipt(statement_id)["support_sets"] == support_sets


def test_v4_publication_rejects_tampered_support_grouping(tmp_path: Path) -> None:
    _root, _grant_id, _run_id, publication_plan, _statement, _support_sets = _v4_fixture(
        tmp_path
    )
    tampered = deepcopy(publication_plan)
    statement = tampered["statement_plans"][0]["statements"][0]
    first, second = statement["source_refs"]
    statement["support_sets"] = [_support_group(source_refs=[first, second])]
    with pytest.raises(ValueError, match="input-set digest is invalid"):
        SemanticFinalizer(tmp_path / "vault").stage_publication(
            grant_id=_grant_id,
            compilation_run_id=_run_id,
            plan=tampered,
            confirm_no_case_data=True,
        )


def _compile_approved_successor(root: Path, source: Path, first_text: str) -> dict:
    source.write_text(
        "# First source section\n"
        f"{first_text}\n\n"
        "# Second source section\n"
        + _SECOND_SOURCE_TEXT,
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        successor = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        source_id = successor["source"]["source_id"]
        manifest = vault.source_review_manifest(source_id)
        vault.approve_source_assets(
            source_id,
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
            reviewer_id="complete-support-sets-successor",
            review_reason="Activate the synthetic unchanged-fragment successor.",
        )
    return successor


def _committed_statement_identity(root: Path) -> tuple[str, str]:
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        row = store.connection.execute(
            "SELECT statement_id, knowledge_revision_id FROM knowledge_statements_v1"
        ).fetchone()
    assert row is not None
    return row["statement_id"], row["knowledge_revision_id"]


def _committed_statement_details(
    root: Path,
    *,
    semantic_key: str,
) -> tuple[str, str, str]:
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        row = store.connection.execute(
            """
            SELECT statements.statement_id,
                   statements.knowledge_revision_id,
                   revisions.knowledge_id
            FROM knowledge_statements_v1 AS statements
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = statements.knowledge_revision_id
            WHERE revisions.semantic_key = ?
            """,
            (semantic_key,),
        ).fetchone()
    assert row is not None
    return row["statement_id"], row["knowledge_revision_id"], row["knowledge_id"]


def _commit_publication(
    root: Path,
    grant_id: str,
    run_id: str,
    publication_plan: dict[str, object],
) -> None:
    finalizer = SemanticFinalizer(root)
    finalizer.stage_publication(
        grant_id=grant_id,
        compilation_run_id=run_id,
        plan=publication_plan,
        confirm_no_case_data=True,
    )
    coordinator = CompilationCoordinator(root)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    finalizer.commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )


def _refs_in_source_order(value: dict[str, object]) -> tuple[dict[str, str], dict[str, str]]:
    refs = value["source_refs"]
    assert isinstance(refs, list)
    assert len(refs) == 2
    return refs[0], refs[1]


def test_or_support_survives_partial_successor_and_projects_valid_witness(
    tmp_path: Path,
) -> None:
    root, grant_id, run_id, publication_plan, _statement, support_sets = _v4_fixture(
        tmp_path
    )
    finalizer = SemanticFinalizer(root)
    finalizer.stage_publication(
        grant_id=grant_id,
        compilation_run_id=run_id,
        plan=publication_plan,
        confirm_no_case_data=True,
    )
    coordinator = CompilationCoordinator(root)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    finalizer.commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    statement_id, original_revision_id = _committed_statement_identity(root)
    evidence = StatementEvidenceStore(root)
    before = evidence.statement(statement_id)
    assert before["knowledge_revision_id"] == original_revision_id
    assert before["freshness"] == "fresh"
    assert before["current_supported"] is True

    first_ref, second_ref = _refs_in_source_order(before)
    successor = _compile_approved_successor(
        root,
        tmp_path / "source.md",
        f"{_SUPPORT_CLAIM} The first independent archive record is revised in the successor.",
    )
    successor_revision_id = successor["identity"]["source_revision_id"]
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=first_ref["source_revision_id"],
        replacement_source_revision_id=successor_revision_id,
        confirm_no_case_data=True,
    )
    assert len(report["changed_fragment_ids"]) == 1
    assert len(report["unchanged_fragment_ids"]) == 1
    assert report["missing_fragment_ids"] == []
    changed_fragment_id = report["changed_fragment_ids"][0]
    unchanged_fragment_id = report["unchanged_fragment_ids"][0]
    assert {changed_fragment_id, unchanged_fragment_id} == {
        first_ref["fragment_id"],
        second_ref["fragment_id"],
    }
    unchanged_ref = next(
        ref for ref in (first_ref, second_ref) if ref["fragment_id"] == unchanged_fragment_id
    )
    second_group = next(
        group for group in support_sets if group["source_refs"] == [unchanged_ref]
    )

    after = evidence.statement(statement_id)
    assert after["knowledge_revision_id"] == original_revision_id
    assert after["source_refs"] == before["source_refs"]
    assert after["freshness"] == "fresh"
    assert after["current_supported"] is True
    assert after["support_sets"] == support_sets

    result = PurposeAwareRetrievalService(root).query(
        _SUPPORT_CLAIM
    )
    selected = [
        item for item in result["statements"] if item["statement_id"] == statement_id
    ]
    assert len(selected) == 1
    projected = selected[0]
    assert projected["freshness"] == "fresh"
    assert projected["current_supported"] is True
    assert projected["source_refs"] == [unchanged_ref]
    assert projected["source_refs"] != before["source_refs"]
    expected_support_digest = sha256_bytes(
        canonical_json(second_group).encode("utf-8")
    )
    assert projected["support_set_sha256"] == expected_support_digest


def test_and_support_rejects_partial_successor_with_same_flat_refs(tmp_path: Path) -> None:
    root, grant_id, run_id, publication_plan, _statement, support_sets = _v4_fixture(
        tmp_path,
        support_mode="and",
    )
    finalizer = SemanticFinalizer(root)
    finalizer.stage_publication(
        grant_id=grant_id,
        compilation_run_id=run_id,
        plan=publication_plan,
        confirm_no_case_data=True,
    )
    coordinator = CompilationCoordinator(root)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    finalizer.commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    statement_id, original_revision_id = _committed_statement_identity(root)
    evidence = StatementEvidenceStore(root)
    before = evidence.statement(statement_id)
    first_ref, second_ref = _refs_in_source_order(before)
    assert before["support_sets"] == support_sets
    assert before["source_refs"] == [first_ref, second_ref]
    assert before["freshness"] == "fresh"

    or_support_sets = canonical_support_sets(
        [
            _support_group(source_refs=[first_ref]),
            _support_group(source_refs=[second_ref]),
        ]
    )
    or_digest = build_input_set_sha256(
        source_refs=[first_ref, second_ref],
        knowledge_revision_refs=[],
        relation_revision_refs=[],
        valid_from=None,
        valid_to=None,
        statement_type="factual",
        support_status="supported",
        limitation=None,
        gaps=[],
        support_sets=or_support_sets,
    )
    assert before["input_set_sha256"] == build_input_set_sha256(
        source_refs=[first_ref, second_ref],
        knowledge_revision_refs=[],
        relation_revision_refs=[],
        valid_from=None,
        valid_to=None,
        statement_type="factual",
        support_status="supported",
        limitation=None,
        gaps=[],
        support_sets=support_sets,
    )
    assert before["input_set_sha256"] != or_digest

    successor = _compile_approved_successor(
        root,
        tmp_path / "source.md",
        f"{_SUPPORT_CLAIM} The first independent archive record is revised in the successor.",
    )
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=first_ref["source_revision_id"],
        replacement_source_revision_id=successor["identity"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert len(report["changed_fragment_ids"]) == 1
    assert len(report["unchanged_fragment_ids"]) == 1
    assert set(report["changed_fragment_ids"] + report["unchanged_fragment_ids"]) == {
        first_ref["fragment_id"],
        second_ref["fragment_id"],
    }

    stale = evidence.statement(statement_id)
    assert stale["knowledge_revision_id"] == original_revision_id
    assert stale["support_sets"] == support_sets
    assert stale["freshness"] == "stale"
    assert stale["current_supported"] is False
    query_result = PurposeAwareRetrievalService(root).query(
        _SUPPORT_CLAIM
    )
    assert not any(
        item["statement_id"] == statement_id
        for item in query_result["statements"]
    )


def test_invalidated_support_stays_invalid_after_snapshot_restore_and_rebuild(
    tmp_path: Path,
) -> None:
    root, grant_id, run_id, publication_plan, statement, support_sets = _v4_fixture(
        tmp_path
    )
    finalizer = SemanticFinalizer(root)
    finalizer.stage_publication(
        grant_id=grant_id,
        compilation_run_id=run_id,
        plan=publication_plan,
        confirm_no_case_data=True,
    )
    coordinator = CompilationCoordinator(root)
    coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    finalizer.commit(
        grant_id=grant_id,
        compilation_run_id=run_id,
        confirm_no_case_data=True,
    )
    statement_id, original_revision_id = _committed_statement_identity(root)
    before = StatementEvidenceStore(root).statement(statement_id)
    first_ref, _second_ref = _refs_in_source_order(before)
    successor = _compile_approved_successor(
        root,
        tmp_path / "source.md",
        f"{_SUPPORT_CLAIM} The first independent archive record changes before withdrawal.",
    )
    successor_source_id = successor["source"]["source_id"]
    with KnowledgeVault(root, read_only=False) as vault:
        vault.remove_source(
            successor_source_id,
            reason="Withdraw the successor for complete invalidation regression.",
            confirm=True,
        )
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=first_ref["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert report["replacement_source_revision_id"] is None
    assert report["missing_fragment_ids"]
    invalidated = StatementEvidenceStore(root).statement(statement_id)
    assert invalidated["knowledge_revision_id"] == original_revision_id
    assert invalidated["support_sets"] == support_sets
    assert invalidated["input_set_sha256"] == statement["input_set_sha256"]
    assert invalidated["freshness"] == "invalidated"
    assert invalidated["current_supported"] is False
    query = PurposeAwareRetrievalService(root).query(
        _SUPPORT_CLAIM
    )
    assert not any(item["statement_id"] == statement_id for item in query["statements"])

    snapshot = tmp_path / "invalidated-support-snapshot"
    create_autonomous_snapshot(root, snapshot)
    assert verify_autonomous_snapshot(snapshot)["valid"] is True
    restored = tmp_path / "invalidated-support-restored"
    restore_autonomous_snapshot(restored, snapshot=snapshot, confirm=True)
    with AutonomousKnowledgeStore(restored, read_only=False) as store:
        rebuilt = store.rebuild_derived()
        verification = store.verify()
    assert rebuilt["schema_version"] == "deeplaw.derived-manifest/v2"
    assert verification["valid"] is True, verification["failures"]
    restored_statement = StatementEvidenceStore(restored).statement(statement_id)
    assert restored_statement["knowledge_revision_id"] == original_revision_id
    assert restored_statement["support_sets"] == support_sets
    assert restored_statement["freshness"] == "invalidated"
    assert restored_statement["current_supported"] is False
    restored_query = PurposeAwareRetrievalService(restored).query(
        _SUPPORT_CLAIM
    )
    assert not any(
        item["statement_id"] == statement_id
        for item in restored_query["statements"]
    )


def test_forgetting_knowledge_support_invalidates_dependent_but_keeps_or_branch(
    tmp_path: Path,
) -> None:
    root, grant_id, parent_run_id, parent_plan, _parent_statement, _parent_support = (
        _v4_fixture(
            tmp_path,
            source_name="c5-parent.md",
            semantic_key="claim:c5-support-parent",
            statement_text=_SUPPORT_CLAIM,
        )
    )
    _commit_publication(root, grant_id, parent_run_id, parent_plan)
    parent_statement_id, parent_revision_id, parent_knowledge_id = (
        _committed_statement_details(
            root,
            semantic_key="claim:c5-support-parent",
        )
    )
    parent_before = StatementEvidenceStore(root).statement(parent_statement_id)
    assert parent_before["knowledge_revision_id"] == parent_revision_id
    assert parent_before["current_supported"] is True

    dependent = _v4_fixture(
        tmp_path,
        root=root,
        grant_id=grant_id,
        source_name="c5-dependent.md",
        semantic_key="claim:c5-dependent",
        statement_text=_DEPENDENT_CLAIM,
        support_mode="and",
        knowledge_revision_refs=[parent_revision_id],
    )
    _commit_publication(root, grant_id, dependent[2], dependent[3])
    dependent_or = _v4_fixture(
        tmp_path,
        root=root,
        grant_id=grant_id,
        source_name="c5-dependent-or.md",
        semantic_key="claim:c5-dependent-or",
        statement_text=_DEPENDENT_CLAIM,
        support_mode="dependency_or",
        knowledge_revision_refs=[parent_revision_id],
    )
    dependent_or_support_sets = dependent_or[5]
    _commit_publication(root, grant_id, dependent_or[2], dependent_or[3])
    dependent_statement_id, dependent_revision_id, dependent_knowledge_id = (
        _committed_statement_details(root, semantic_key="claim:c5-dependent")
    )
    dependent_or_statement_id, dependent_or_revision_id, dependent_or_knowledge_id = (
        _committed_statement_details(root, semantic_key="claim:c5-dependent-or")
    )
    evidence = StatementEvidenceStore(root)
    dependent_before = evidence.statement(dependent_statement_id)
    dependent_or_before = evidence.statement(dependent_or_statement_id)
    assert dependent_before["knowledge_revision_refs"] == [parent_revision_id]
    assert dependent_before["freshness"] == "fresh"
    assert dependent_before["current_supported"] is True
    assert dependent_or_before["knowledge_revision_refs"] == [parent_revision_id]
    assert dependent_or_before["freshness"] == "fresh"
    assert dependent_or_before["current_supported"] is True

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        before_recall = store.recall(_DEPENDENT_CLAIM)
        before_recall_ids = {
            item["knowledge_id"] for item in before_recall["results"]
        }
        assert {dependent_knowledge_id, dependent_or_knowledge_id} <= before_recall_ids
        evidence.map_for_revision(dependent_revision_id)
        evidence.receipt(dependent_statement_id)
        evidence.map_for_revision(dependent_or_revision_id)
        evidence.receipt(dependent_or_statement_id)
        integrity = store.verify()
        assert integrity["valid"] is True, integrity["failures"]
        capsule = store.build_capsule(
            task=_DEPENDENT_CLAIM,
            query_target={"knowledge_id": dependent_knowledge_id},
            query_plan_version="7",
            scope="project",
            max_sensitivity="private",
            confirm_no_case_data=True,
        )
    assert [item["statement_id"] for item in capsule["statements"]] == [
        dependent_statement_id
    ]
    with KnowledgeVault(root, read_only=True) as vault:
        assert verify_capsule(capsule, vault=vault)["valid"] is True

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        owner_grant_id = store.enable_grant(
            writer_id="c5-owner",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )["grant_id"]
        forgotten = store.forget(
            grant_id=owner_grant_id,
            idempotency_key="c5-forget-parent",
            knowledge_id=parent_knowledge_id,
            expected_revision_id=parent_revision_id,
            reason="Owner withdrew the synthetic parent Knowledge evidence.",
            confirm_no_case_data=True,
        )
        assert forgotten["lifecycle"] == "forgotten"
        assert store.get_current(parent_knowledge_id, include_inactive=True)[
            "lifecycle"
        ] == "forgotten"

    dependent_after = evidence.statement(dependent_statement_id)
    dependent_or_after = evidence.statement(dependent_or_statement_id)
    assert dependent_after["knowledge_revision_id"] == dependent_revision_id
    assert dependent_after["knowledge_revision_refs"] == [parent_revision_id]
    assert dependent_after["freshness"] == "invalidated"
    assert dependent_after["current_supported"] is False
    assert dependent_or_after["knowledge_revision_id"] == dependent_or_revision_id
    assert dependent_or_after["knowledge_revision_refs"] == [parent_revision_id]
    assert dependent_or_after["freshness"] == "fresh"
    assert dependent_or_after["current_supported"] is True

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        after_recall = store.recall(_DEPENDENT_CLAIM)
    assert {
        item["knowledge_id"] for item in after_recall["results"]
    } == {dependent_or_knowledge_id}
    assert any(
        item["candidate_sha256"] == sha256_bytes(dependent_knowledge_id.encode("utf-8"))
        for item in after_recall["rejected"]
    )
    query = PurposeAwareRetrievalService(root).query(_DEPENDENT_CLAIM)
    assert {
        item["statement_id"] for item in query["statements"]
    } == {dependent_or_statement_id}
    source_only_group = next(
        group
        for group in dependent_or_support_sets
        if not group["knowledge_revision_refs"]
    )
    assert query["statements"][0]["support_set_sha256"] == sha256_bytes(
        canonical_json(source_only_group).encode("utf-8")
    )
    with KnowledgeVault(root, read_only=True) as vault:
        assert verify_capsule(capsule, vault=vault)["valid"] is False

    with AutonomousKnowledgeStore(root, read_only=False) as store:
        rebuilt = store.rebuild_derived()
        rebuilt_verification = store.verify()
    assert rebuilt["schema_version"] == "deeplaw.derived-manifest/v2"
    assert rebuilt_verification["valid"] is True, rebuilt_verification["failures"]
    assert StatementEvidenceStore(root).statement(dependent_statement_id)[
        "knowledge_revision_id"
    ] == dependent_revision_id
    assert StatementEvidenceStore(root).statement(dependent_statement_id)[
        "freshness"
    ] == "invalidated"
    assert StatementEvidenceStore(root).statement(dependent_or_statement_id)[
        "freshness"
    ] == "fresh"

    snapshot = tmp_path / "c5-forgotten-snapshot"
    create_autonomous_snapshot(root, snapshot)
    assert verify_autonomous_snapshot(snapshot)["valid"] is True
    restored = tmp_path / "c5-forgotten-restored"
    restore_autonomous_snapshot(restored, snapshot=snapshot, confirm=True)
    with AutonomousKnowledgeStore(restored, read_only=False) as store:
        restored_rebuild = store.rebuild_derived()
        restored_verification = store.verify()
    assert restored_rebuild["schema_version"] == "deeplaw.derived-manifest/v2"
    assert restored_verification["valid"] is True, restored_verification["failures"]
    restored_dependent = StatementEvidenceStore(restored).statement(dependent_statement_id)
    restored_dependent_or = StatementEvidenceStore(restored).statement(
        dependent_or_statement_id
    )
    assert restored_dependent["knowledge_revision_id"] == dependent_revision_id
    assert restored_dependent["freshness"] == "invalidated"
    assert restored_dependent["current_supported"] is False
    assert restored_dependent_or["knowledge_revision_id"] == dependent_or_revision_id
    assert restored_dependent_or["freshness"] == "fresh"
    assert restored_dependent_or["current_supported"] is True
    restored_query = PurposeAwareRetrievalService(restored).query(_DEPENDENT_CLAIM)
    assert {
        item["statement_id"] for item in restored_query["statements"]
    } == {dependent_or_statement_id}
    with AutonomousKnowledgeStore(restored, read_only=True) as store:
        restored_recall = store.recall(_DEPENDENT_CLAIM)
    assert {
        item["knowledge_id"] for item in restored_recall["results"]
    } == {dependent_or_knowledge_id}
    with KnowledgeVault(restored, read_only=True) as vault:
        assert verify_capsule(capsule, vault=vault)["valid"] is False
