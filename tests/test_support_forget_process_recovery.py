"""Process exit on either side of forget's canonical commit cannot mix support state."""
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from deeplaw import knowledge_autonomy
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.subprocess_environment import _build_subprocess_environment

from .test_support_maintenance_boundaries import _commit_fixture


@pytest.mark.parametrize("boundary", ("before_commit", "after_commit"))
def test_forget_process_exit_preserves_atomic_support_state(tmp_path, boundary, monkeypatch):
    root, _, _ = _commit_fixture(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        row = store.connection.execute(
            "SELECT knowledge_id, revision_id FROM knowledge_revisions_v3 LIMIT 1"
        ).fetchone()
        target_id, target_revision = row["knowledge_id"], row["revision_id"]
        grant = store.enable_grant(
            writer_id="synthetic-forget-process", operations=("remember", "forget"),
        )["grant_id"]
        dependent = store.remember(
            grant_id=grant, idempotency_key="dependent",
            title="Orchid dependent note", body="Use the bound Orchid archive requirement.",
            source_refs=[{"revision_id": target_revision}], confirm_no_case_data=True,
        )
        before_head = store.audit_head
        assert store.revision_provenance_admitted(store.get_current(dependent["knowledge_id"]))

    script = r'''
import os
import sys
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

root, grant, target, revision, boundary = sys.argv[1:]
with AutonomousKnowledgeStore(root, read_only=False) as store:
    original = store._append_event
    def interrupted_append(**kwargs):
        if (kwargs.get("event_type") == "knowledge_revision_committed"
                and kwargs.get("payload", {}).get("operation") == "forget"):
            os._exit(91)
        return original(**kwargs)
    def interrupted_materialization(revision_id):
        os._exit(91)
    if boundary == "before_commit":
        store._append_event = interrupted_append
    else:
        store._materialize_pending = interrupted_materialization
    store.forget(
        grant_id=grant, idempotency_key="process-forget", knowledge_id=target,
        expected_revision_id=revision, reason="Synthetic process exit boundary.",
        confirm_no_case_data=True,
    )
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), grant, target_id, target_revision, boundary],
        cwd=Path(__file__).resolve().parents[1],
        env={**_build_subprocess_environment(), "PYTHONUTF8": "1"},
        capture_output=True, timeout=120, check=False,
    )
    assert result.returncode == 91
    with pytest.raises(RuntimeError, match="file lease is already held"):
        AutonomousKnowledgeStore(root, read_only=False)
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        expiry = store.connection.execute(
            "SELECT expires_at FROM workspace_file_leases_v4 WHERE lease_key = ?",
            ("canonical-mutation",),
        ).fetchone()[0]
    # Exercise ordinary lease expiry without sleeping for the five-minute TTL.
    after_expiry = (
        datetime.fromisoformat(expiry.replace("Z", "+00:00")) + timedelta(seconds=1)
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    monkeypatch.setattr(knowledge_autonomy, "utc_now", lambda: after_expiry)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        store.recover()
        target = store.get_current(target_id, include_inactive=True)
        current_dependent = store.get_current(dependent["knowledge_id"])
        assert current_dependent["revision_id"] == dependent["revision_id"]
        if boundary == "before_commit":
            assert store.audit_head == before_head
            assert target["revision_id"] == target_revision
            assert target["lifecycle"] == "active"
            assert store.revision_provenance_admitted(current_dependent)
        else:
            assert target["revision_id"] != target_revision
            assert target["lifecycle"] == "forgotten"
            assert not store.revision_provenance_admitted(current_dependent)
            replay = store.forget(
                grant_id=grant, idempotency_key="process-forget", knowledge_id=target_id,
                expected_revision_id=target_revision, reason="Synthetic process exit boundary.",
                confirm_no_case_data=True,
            )
            assert replay["revision_id"] == target["revision_id"]
        assert store.verify()["valid"]
