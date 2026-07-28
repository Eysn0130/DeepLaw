from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplaw.knowledge_inbox import (
    list_inbox_artifacts,
    promote_inbox_proposal,
    reject_inbox_artifact,
    submit_inbox_artifact,
    verify_inbox_artifact,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="inbox", scope="project")
    return root


@pytest.mark.parametrize(
    ("artifact_type", "payload", "extension"),
    (
        (
            "proposal",
            {
                "kind": "lesson",
                "memory_tier": "experience",
                "title": "Retry lesson",
                "statement": "Retry a transient read once after verification.",
            },
            ".dlproposal",
        ),
        (
            "feedback",
            {"run_id": "run_example", "labels": ["helpful"], "observation": "Useful"},
            ".dlfeedback",
        ),
        (
            "run",
            {
                "capsule_id": "capsule_example",
                "capsule_digest": "a" * 64,
                "status": "success",
                "host": {"name": "codex", "version": "test"},
            },
            ".dlrun",
        ),
        (
            "eval",
            {"case_id": "case_example", "query": "retry", "expected": ["lesson"]},
            ".dleval",
        ),
    ),
)
def test_inbox_artifact_types_are_hash_bound_and_isolated(
    tmp_path: Path,
    artifact_type: str,
    payload: dict[str, object],
    extension: str,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        artifact = submit_inbox_artifact(
            vault,
            artifact_type=artifact_type,  # type: ignore[arg-type]
            payload=payload,
            producer_name="codex",
            producer_version="test",
            priority_signals=("user_confirmed",),
            confirm_no_case_data=True,
        )
        verification = verify_inbox_artifact(vault, artifact["artifact_id"])
        listing = list_inbox_artifacts(vault)

    assert verification["valid"] is True
    assert listing["artifacts"][0]["artifact_id"] == artifact["artifact_id"]
    path = root / "inbox" / "pending" / f"{artifact['artifact_id']}{extension}"
    assert path.is_file()
    assert artifact["canonical_write_performed"] is False


def test_inbox_proposal_requires_review_and_remains_quarantined(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        artifact = submit_inbox_artifact(
            vault,
            artifact_type="proposal",
            payload={
                "kind": "decision",
                "memory_tier": "project",
                "title": "Local storage",
                "statement": "Use the verified local store.",
                "semantic_key": "decision.local-storage",
            },
            producer_name="opencode",
            producer_version="test",
            priority_signals=("decision_landed",),
            confirm_no_case_data=True,
        )

    with KnowledgeVault(root, read_only=False) as vault:
        with pytest.raises(ValueError, match="explicit operator review"):
            promote_inbox_proposal(
                vault,
                artifact_id=artifact["artifact_id"],
                confirm_reviewed=False,
            )
        promoted = promote_inbox_proposal(
            vault,
            artifact_id=artifact["artifact_id"],
            confirm_reviewed=True,
        )
        asset = vault.get_asset(promoted["asset_id"], include_inactive=True)
        identity = vault.connection.execute(
            """
            SELECT knowledge_revisions_v2.knowledge_key,
                   asset_revision_bindings_v2.asset_revision_id
            FROM asset_revision_bindings_v2
            JOIN knowledge_revisions_v2 USING(asset_revision_id)
            WHERE asset_revision_bindings_v2.legacy_asset_id = ?
            """,
            (asset.asset_id,),
        ).fetchone()

    assert asset.status == "quarantined"
    assert asset.trust == "untrusted"
    assert asset.verification == "source_bound"
    assert len(asset.source_refs) == 1
    assert identity is not None
    assert identity["knowledge_key"] == promoted["knowledge_key"]
    assert promoted["source_revision_id"].startswith("sourcerev_")
    assert not (root / "inbox" / "pending" / f"{artifact['artifact_id']}.dlproposal").exists()
    assert (root / "inbox" / "processed" / f"{artifact['artifact_id']}.dlproposal").is_file()


def test_inbox_tamper_is_detected_and_rejection_never_writes_canonical(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=True) as vault:
        artifact = submit_inbox_artifact(
            vault,
            artifact_type="eval",
            payload={"case_id": "case_1", "query": "missing", "expected": []},
            producer_name="claude-code",
            producer_version="test",
            confirm_no_case_data=True,
        )
        path = root / "inbox" / "pending" / f"{artifact['artifact_id']}.dleval"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["payload"]["query"] = "tampered"
        path.write_text(json.dumps(value), encoding="utf-8")
        assert verify_inbox_artifact(vault, artifact["artifact_id"])["valid"] is False

    path.unlink()
    with KnowledgeVault(root, read_only=True) as vault:
        clean = submit_inbox_artifact(
            vault,
            artifact_type="eval",
            payload={"case_id": "case_2", "query": "safe", "expected": []},
            producer_name="claude-code",
            producer_version="test",
            confirm_no_case_data=True,
        )
        before = vault.revision
        rejected = reject_inbox_artifact(
            vault,
            artifact_id=clean["artifact_id"],
            confirm_reviewed=True,
        )
        after = vault.revision

    assert rejected["canonical_write_performed"] is False
    assert before == after


def test_empty_inbox_listing_is_a_non_mutating_read(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    assert not (root / "inbox").exists()

    with KnowledgeVault(root, read_only=True) as vault:
        listing = list_inbox_artifacts(vault)

    assert listing["artifacts"] == []
    assert not (root / "inbox").exists()
