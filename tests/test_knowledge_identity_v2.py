from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_identity import (
    KNOWLEDGE_IDENTITY_SCHEMA,
    SOURCE_IR_SCHEMA,
    canonical_origin_commitment,
    identity_snapshot,
    identity_tables_present,
    install_identity_tables,
    make_collection_id,
    make_compilation_id,
    make_source_key,
    make_source_revision_id,
    normalize_logical_path,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval_fabric import retrieve
from deeplaw.util import sha256_bytes


def test_source_identity_is_independent_of_checkout_location() -> None:
    collection_id = make_collection_id(
        vault_id="vault_0123456789abcdef01234567",
        name="project",
    )
    first = make_source_key(
        collection_id=collection_id,
        logical_path="docs/architecture.md",
    )
    second = make_source_key(
        collection_id=collection_id,
        logical_path=normalize_logical_path("docs\\architecture.md"),
    )

    assert first == second


@pytest.mark.parametrize(
    "value",
    (
        "/Users/example/project.md",
        "C:\\Users\\example\\project.md",
        "../project.md",
        "docs/../../project.md",
        "docs//project.md",
    ),
)
def test_source_identity_rejects_absolute_or_escaping_paths(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_logical_path(value)


def test_source_revision_excludes_parser_and_governance_identity() -> None:
    collection_id = make_collection_id(
        vault_id="vault_0123456789abcdef01234567",
        name="project",
    )
    source_key = make_source_key(
        collection_id=collection_id,
        logical_path="policy.md",
    )
    content_sha256 = sha256_bytes(b"same source bytes")
    source_revision_id = make_source_revision_id(
        source_key=source_key,
        content_sha256=content_sha256,
        media_identity="text/markdown",
        origin_commitment="local",
    )
    other = make_source_revision_id(
        source_key=source_key,
        content_sha256=content_sha256,
        media_identity="text/markdown",
        origin_commitment="local",
    )
    first_compilation = make_compilation_id(
        source_revision_id=source_revision_id,
        adapter="markdown",
        adapter_version="1",
        configuration_sha256=sha256_bytes(b"config-a"),
        source_ir_schema=SOURCE_IR_SCHEMA,
        fragment_inventory_sha256=sha256_bytes(b"fragments"),
    )
    second_compilation = make_compilation_id(
        source_revision_id=source_revision_id,
        adapter="markdown",
        adapter_version="2",
        configuration_sha256=sha256_bytes(b"config-b"),
        source_ir_schema=SOURCE_IR_SCHEMA,
        fragment_inventory_sha256=sha256_bytes(b"fragments"),
    )

    assert source_revision_id == other
    assert first_compilation != second_compilation


def test_origin_commitment_removes_default_port_and_fragment() -> None:
    assert canonical_origin_commitment("HTTPS://Example.COM:443/a?b=1#local") == (
        "https://example.com/a?b=1"
    )
    with pytest.raises(ValueError, match="file"):
        canonical_origin_commitment("file:///Users/example/private.md")


def test_identity_schema_installs_as_additive_tables() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        install_identity_tables(
            connection,
            installed_at="2026-07-27T00:00:00Z",
            migration_source="new-vault",
        )
        assert identity_tables_present(connection)
        snapshot = identity_snapshot(connection)
    finally:
        connection.close()

    assert snapshot["schema_version"] == KNOWLEDGE_IDENTITY_SCHEMA
    assert len(snapshot["tables"]) == 21
    assert len(snapshot["identity_root_sha256"]) == 64


def test_source_governance_changes_without_changing_source_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="governance", scope="project")
    source = tmp_path / "policy.md"
    source.write_text("# Policy\nThe Mercury source remains local.\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        asset = vault.approve_asset(
            compiled["asset_ids"][0],
            confirm_reviewed=True,
        )
        before = vault.source_info(compiled["source"]["source_id"])
        changed = vault.update_source_governance(
            before["source_id"],
            trust="user_provided",
            sensitivity="restricted",
            export_allowed=False,
            reviewer_id="local-operator",
            reason="Restrict this source after local policy review.",
            confirm_reviewed=True,
        )
        after = vault.source_info(before["source_id"])
        response = retrieve(vault, "Mercury source", mode="lexical")

    assert changed["source_revision_changed"] is False
    assert before["source_revision_id"] == after["source_revision_id"]
    assert before["governance_revision"] != after["governance_revision"]
    assert after["governance"]["sensitivity"] == "restricted"
    assert asset.asset_id not in {item["asset_id"] for item in response["results"]}
    assert any(
        "source_sensitivity:restricted" in reason
        for item in response["trace"]["excluded_candidates"]
        for reason in item["reasons"]
    )


def test_extractor_change_reuses_source_and_compilation_identity(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="extractor", scope="project")
    source = tmp_path / "decision.md"
    source.write_text("# Decision\nUse immutable local evidence.\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            typed_extraction="off",
        )
        second = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            typed_extraction="deterministic-v2",
        )

    assert first["identity"]["source_revision_id"] == second["identity"][
        "source_revision_id"
    ]
    assert first["identity"]["compilation_id"] == second["identity"]["compilation_id"]
    assert first["identity"]["proposal_set_id"] != second["identity"]["proposal_set_id"]


def test_source_ir_can_be_reviewed_with_zero_proposals(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="zero-proposals", scope="project")
    source = tmp_path / "reference.md"
    source.write_text("# Reference\nEvidence can exist without a claim.\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            typed_extraction="off",
            reference_proposals=False,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        approval = vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )

    assert compiled["asset_ids"] == []
    assert manifest["fragment_count"] == 1
    assert manifest["proposal_count"] == 0
    assert approval["reviewed_asset_count"] == 0
    assert approval["source_activated"] is True


def test_temporal_relation_preserves_current_past_and_as_of_views(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="temporal", scope="project")
    source = tmp_path / "relation.md"
    source.write_text(
        "# Alpha\nAlpha requires Beta.\n\n# Beta\nBeta is the verified target.\n",
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
        )
        assets = [vault.get_asset(asset_id) for asset_id in compiled["asset_ids"]]
        evidence_fragment_id = assets[0].source_refs[0].fragment_id
        relation = vault.add_relation(
            subject_asset_id=assets[0].asset_id,
            predicate="depends_on",
            object_asset_id=assets[1].asset_id,
            evidence_fragment_id=evidence_fragment_id,
            confirm_reviewed=True,
            valid_from="2020-01-01T00:00:00Z",
        )
        initial = vault.temporal_relations(mode="current")
        revised = vault.revise_temporal_relation(
            relation["relation_key"],
            status="revoked",
            evidence_fragment_id=evidence_fragment_id,
            confirm_reviewed=True,
            valid_from="2020-01-01T00:00:00Z",
            valid_to="2025-01-01T00:00:00Z",
        )
        current = vault.temporal_relations(mode="current")
        past = vault.temporal_relations(mode="past")
        historical = vault.temporal_relations(
            mode="as-of",
            as_of=initial["relations"][0]["observed_at"],
        )

    assert initial["relations"][0]["relation_key"] == relation["relation_key"]
    assert current["relations"] == []
    assert {item["relation_revision_id"] for item in past["relations"]} >= {
        relation["relation_revision_id"],
        revised["relation_revision_id"],
    }
    assert historical["relations"][0]["relation_revision_id"] == relation[
        "relation_revision_id"
    ]


@pytest.mark.parametrize(
    ("table", "mutation", "expected_reason"),
    (
        (
            "source_ir_nodes_v2",
            "UPDATE source_ir_nodes_v2 SET title = 'tampered tree'",
            "compiled_fragment_digest_mismatch",
        ),
        (
            "relation_revisions_v2",
            "UPDATE relation_revisions_v2 SET predicate = 'tampered'",
            "identity_v2_snapshot_mismatch",
        ),
    ),
)
def test_identity_snapshot_detects_tree_and_graph_tampering(
    tmp_path: Path,
    table: str,
    mutation: str,
    expected_reason: str,
) -> None:
    root = tmp_path / table
    initialize_knowledge_vault(root, name=table, scope="project")
    source = tmp_path / f"{table}.md"
    source.write_text(
        "# Alpha\nAlpha requires Beta.\n\n# Beta\nBeta is reviewed evidence.\n",
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
        )
        assets = [vault.get_asset(asset_id) for asset_id in compiled["asset_ids"]]
        vault.add_relation(
            subject_asset_id=assets[0].asset_id,
            predicate="depends_on",
            object_asset_id=assets[1].asset_id,
            evidence_fragment_id=assets[0].source_refs[0].fragment_id,
            confirm_reviewed=True,
        )
        assert vault.verify_integrity()["valid"] is True

    connection = sqlite3.connect(root / "vault.sqlite3")
    try:
        connection.execute(mutation)
        connection.commit()
    finally:
        connection.close()

    with KnowledgeVault(root, read_only=True) as vault:
        integrity = vault.verify_integrity()

    assert integrity["valid"] is False
    assert integrity["state"]["reason"] == expected_reason
