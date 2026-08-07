from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.projection import rebuild_living_wiki
from deeplaw.projection.incremental import (
    _legacy_change_set,
    _write_journal,
    begin_transaction,
    read_previous_manifest,
    recover_projection,
)
from deeplaw.util import canonical_json, sha256_bytes


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-recovery", scope="project")
    initialize_autonomous_core(root)
    return root


def _grant(store: AutonomousKnowledgeStore) -> str:
    return store.enable_grant(
        writer_id="v013-recovery-tests",
        operations=tuple(sorted(SINK_OPERATIONS)),
        max_mutations_per_minute=120,
    )["grant_id"]


def _seed(store: AutonomousKnowledgeStore, key: str = "recovery-seed") -> None:
    grant_id = _grant(store)
    store.remember(
        grant_id=grant_id,
        idempotency_key=key,
        title="Recovery seed",
        body="A deterministic recovery fixture.",
        kind="concept",
        operation="upsert_concept",
        confirm_no_case_data=True,
    )


@pytest.mark.parametrize(
    "phase",
    ["after_prepare", "partial_activate", "after_v3_manifest_switch", "after_manifest_switch"],
)
def test_recovery_is_idempotent_at_each_crash_boundary(tmp_path: Path, phase: str) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        store.rebuild_derived()
        _seed(store, "recovery-successor")

        def fault_hook(observed: str) -> None:
            if observed == phase:
                raise RuntimeError(observed)

        with pytest.raises(RuntimeError, match=phase):
            rebuild_living_wiki(store, _fault_hook=fault_hook)
        journal = root / ".deeplaw/derived/tree/living-wiki-projection.journal.json"
        assert journal.is_file()
        rebuilt = rebuild_living_wiki(store)
        assert not journal.exists()
        assert rebuilt["change_set"]["new_manifest_sha256"] == rebuilt["manifest_sha256"]
        # A second recovery/rebuild has no work and does not create another journal.
        rebuild_living_wiki(store)
        assert not journal.exists()


def test_symlinked_projection_ancestor_fails_closed(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "wiki").rename(root / "wiki-original")
        (root / "wiki").symlink_to(outside, target_is_directory=True)
        with pytest.raises(RuntimeError, match=r"unsafe|symbolic"):
            rebuild_living_wiki(store)
        assert not list(outside.iterdir())


def test_tampered_prepare_journal_refuses_recovery(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        store.rebuild_derived()
        _seed(store, "recovery-tamper")

        def fault_hook(observed: str) -> None:
            if observed == "after_prepare":
                raise RuntimeError(observed)

        with pytest.raises(RuntimeError, match="after_prepare"):
            rebuild_living_wiki(store, _fault_hook=fault_hook)
        journal = root / ".deeplaw/derived/tree/living-wiki-projection.journal.json"
        journal.write_text(
            journal.read_text(encoding="utf-8").replace(
                '"phase": "prepare"', '"phase": "activate"'
            ),
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="journal hash"):
            rebuild_living_wiki(store)


@pytest.mark.parametrize("artifact", ["backup", "staging"])
def test_tampered_transaction_artifact_refuses_recovery(
    tmp_path: Path,
    artifact: str,
) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        store.rebuild_derived()
        _seed(store, "recovery-artifact-tamper")

        def fault_hook(observed: str) -> None:
            if observed == "after_prepare":
                raise RuntimeError(observed)

        with pytest.raises(RuntimeError, match="after_prepare"):
            rebuild_living_wiki(store, _fault_hook=fault_hook)
        journal = root / ".deeplaw/derived/tree/living-wiki-projection.journal.json"
        payload = json.loads(journal.read_text(encoding="utf-8"))
        directory = root / payload[f"{artifact}_path"]
        candidates = [path for path in directory.rglob("*") if path.is_file()]
        assert candidates
        candidates[0].write_bytes(candidates[0].read_bytes() + b"tampered")
        with pytest.raises(RuntimeError):
            rebuild_living_wiki(store)
        assert journal.is_file()


@pytest.mark.parametrize("field", ["transaction_id", "backup_path"])
def test_recomputed_journal_identity_binding_still_fails_closed(
    tmp_path: Path,
    field: str,
) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        store.rebuild_derived()
        _seed(store, "recovery-identity-tamper")

        def fault_hook(observed: str) -> None:
            if observed == "after_prepare":
                raise RuntimeError(observed)

        with pytest.raises(RuntimeError, match="after_prepare"):
            rebuild_living_wiki(store, _fault_hook=fault_hook)
        journal = root / ".deeplaw/derived/tree/living-wiki-projection.journal.json"
        payload = json.loads(journal.read_text(encoding="utf-8"))
        if field == "transaction_id":
            payload[field] = "0" * 32
        else:
            payload[field] = payload[field].replace(
                payload["transaction_id"], "0" * 32
            )
        body = {key: value for key, value in payload.items() if key != "journal_sha256"}
        payload["journal_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
        journal.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(RuntimeError, match="identity"):
            rebuild_living_wiki(store)


def test_legacy_v1_prepare_journal_is_still_recoverable(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        store.rebuild_derived()
        previous = read_previous_manifest(root)
        assert previous is not None
        txn_id, staging, backup = begin_transaction(root)
        for item in previous["files"]:
            target = staging / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / item["path"]).read_bytes())
        staged_manifest = staging / "living-wiki-manifest.json"
        staged_manifest.parent.mkdir(parents=True, exist_ok=True)
        staged_manifest.write_bytes(
            (root / ".deeplaw/derived/tree/living-wiki-manifest.json").read_bytes()
        )
        legacy_change_set = _legacy_change_set(previous, previous)
        journal = _write_journal(
            root,
            {
                "schema_version": "deeplaw.living-wiki-projection-journal/v1",
                "transaction_id": txn_id,
                "phase": "prepare",
                "old_manifest_sha256": previous["manifest_sha256"],
                "new_manifest_sha256": previous["manifest_sha256"],
                "input_audit_head": previous["input_audit_head"],
                "legacy_audit_head": previous["legacy_audit_head"],
                "projection_profile_sha256": previous["configuration"][
                    "projection_profile_sha256"
                ],
                "staging_path": staging.relative_to(root).as_posix(),
                "backup_path": backup.relative_to(root).as_posix(),
                "manifest_path": ".deeplaw/derived/tree/living-wiki-manifest.json",
                "created": legacy_change_set["created"],
                "updated": legacy_change_set["updated"],
                "deleted": legacy_change_set["deleted"],
                "change_set_sha256": legacy_change_set["change_set_sha256"],
            },
        )
        assert journal["schema_version"] == "deeplaw.living-wiki-projection-journal/v1"
        recover_projection(root)
        assert not (root / ".deeplaw/derived/tree/living-wiki-projection.journal.json").exists()


def test_legacy_v2_without_v3_fields_is_recoverable(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        _seed(store)
        store.rebuild_derived()
        previous = read_previous_manifest(root)
        assert previous is not None
        txn_id, staging, backup = begin_transaction(root)
        for item in previous["files"]:
            target = staging / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / item["path"]).read_bytes())
        (staging / "living-wiki-manifest.json").write_bytes(
            (root / ".deeplaw/derived/tree/living-wiki-manifest.json").read_bytes()
        )
        change_set = _legacy_change_set(previous, previous)
        journal = {
            "schema_version": "deeplaw.living-wiki-projection-journal/v2",
            "transaction_id": txn_id,
            "phase": "prepare",
            "old_manifest_sha256": previous["manifest_sha256"],
            "new_manifest_sha256": previous["manifest_sha256"],
            "input_audit_head": previous["input_audit_head"],
            "legacy_audit_head": previous["legacy_audit_head"],
            "projection_profile_sha256": previous["configuration"][
                "projection_profile_sha256"
            ],
            "staging_path": staging.relative_to(root).as_posix(),
            "backup_path": backup.relative_to(root).as_posix(),
            "manifest_path": ".deeplaw/derived/tree/living-wiki-manifest.json",
            "created": change_set["created"],
            "updated": change_set["updated"],
            "deleted": change_set["deleted"],
            "change_set_sha256": change_set["change_set_sha256"],
        }
        journal_body = {
            key: value for key, value in journal.items() if key != "journal_sha256"
        }
        journal["journal_sha256"] = sha256_bytes(
            canonical_json(journal_body).encode("utf-8")
        )
        journal_path = root / ".deeplaw/derived/tree/living-wiki-projection.journal.json"
        journal_path.write_text(
            json.dumps(journal, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        recover_projection(root)
        assert not journal_path.exists()
