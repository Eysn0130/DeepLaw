"""Development-only regression for the Living Wiki Recent Changes read seam.

The projection builder emits a governed ``wiki/recent-changes`` page family from
Ledger events.  The public ``recent_changes`` reads must return that projection,
not a current-object browse that loses event history.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.projection import rebuild_living_wiki
from deeplaw.read_services import WikiReadService

_RECENT_INDEX = "wiki/recent-changes/index.md"
_RECENT_INDEX_LINK = re.compile(r"\[\[(wiki/recent-changes/[^|]+)\|")


def _vault(tmp_path: Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-recent-changes", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-recent-changes-tests",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )["grant_id"]
        first = store.remember(
            grant_id=grant_id,
            idempotency_key="recent-changes-first",
            title="Recent changes fixture",
            body="The first Ledger revision for the Recent Changes fixture.",
            kind="concept",
            operation="upsert_concept",
            semantic_key="v013:recent-changes:fixture",
            confirm_no_case_data=True,
        )
        second = store.remember(
            grant_id=grant_id,
            idempotency_key="recent-changes-second",
            title="Recent changes fixture revised",
            body="The second Ledger revision must remain visible as an event.",
            kind="concept",
            operation="upsert_concept",
            semantic_key="v013:recent-changes:fixture",
            knowledge_id=first["knowledge_id"],
            expected_revision_id=first["revision_id"],
            confirm_no_case_data=True,
        )
        assert second["parent_revision_id"] == first["revision_id"]
        assert second["revision_id"] != first["revision_id"]
        rebuild_living_wiki(store)
    return root, first, second


def _projection_fixture(root: Path) -> tuple[str, str]:
    with KnowledgeOS.open(root) as knowledge_os:
        index = knowledge_os.wiki.page(_RECENT_INDEX)
        shard_match = _RECENT_INDEX_LINK.search(index["content"])
        assert shard_match is not None
        shard_path = shard_match.group(1) + ".md"
        shard = knowledge_os.wiki.page(shard_path)
    assert index["wiki_path"] == _RECENT_INDEX
    assert "# Recent changes" in index["content"]
    assert "knowledge_revision_committed" in shard["content"]
    return index["content"], shard_path


def _assert_recent_projection(
    result: dict[str, Any],
    *,
    index_content: str,
    shard_path: str,
) -> None:
    """Freeze the minimum read contract for generated Recent Changes pages."""

    assert result["schema_version"] == "deeplaw.living-wiki-recent-changes-read/v1"
    assert result["action"] == "recent_changes"
    assert result["index_path"] == _RECENT_INDEX
    assert result["index_content"] == index_content
    assert isinstance(result["index_content_sha256"], str)
    shards = result["shards"]
    assert isinstance(shards, list) and shards
    first_shard = shards[0]
    assert first_shard["path"] == shard_path
    assert isinstance(first_shard["event_count"], int)
    assert first_shard["event_count"] >= 2
    assert isinstance(first_shard["content_sha256"], str)
    assert isinstance(first_shard["byte_size"], int)
    assert result["returned_shard_count"] <= result["total_shard_count"]
    assert result["truncated"] is False
    assert result["truncation_reason"] is None
    assert isinstance(result["history_truncated"], bool)
    assert result["write_performed"] is False


def _run_cli(root: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "deeplaw",
            "knowledge",
            "wiki",
            "recent",
            "--vault",
            str(root),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return value


def test_python_recent_changes_returns_generated_projection(tmp_path: Path) -> None:
    root, _, _ = _vault(tmp_path)
    index_content, shard_path = _projection_fixture(root)

    with KnowledgeOS.open(root) as knowledge_os:
        python_result = knowledge_os.wiki.recent_changes(limit=20)
    _assert_recent_projection(
        python_result,
        index_content=index_content,
        shard_path=shard_path,
    )


def test_cli_recent_changes_returns_generated_projection(tmp_path: Path) -> None:
    root, _, _ = _vault(tmp_path)
    index_content, shard_path = _projection_fixture(root)
    cli_result = _run_cli(root)
    _assert_recent_projection(
        cli_result,
        index_content=index_content,
        shard_path=shard_path,
    )


def test_mcp_recent_changes_returns_generated_projection(tmp_path: Path) -> None:
    root, _, _ = _vault(tmp_path)
    index_content, shard_path = _projection_fixture(root)
    mcp_response = handle_knowledge_support(
        operation="wiki",
        wiki_action="recent_changes",
        plane="autonomous",
        vault_path=root,
    )
    mcp_result = mcp_response["result"]
    assert isinstance(mcp_result, dict)
    _assert_recent_projection(
        mcp_result,
        index_content=index_content,
        shard_path=shard_path,
    )


def test_recent_changes_limit_is_explicitly_bounded(tmp_path: Path) -> None:
    root, _, _ = _vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_ids = [
            store.enable_grant(
                writer_id=f"v013-recent-changes-limit-{grant_index}",
                operations=tuple(sorted(SINK_OPERATIONS)),
                max_mutations_per_minute=120,
            )["grant_id"]
            for grant_index in range(2)
        ]
        for index in range(195):
            store.remember(
                grant_id=grant_ids[index % len(grant_ids)],
                idempotency_key=f"recent-changes-limit-{index}",
                title=f"Recent changes limit fixture {index}",
                body="Bounded Recent Changes shard fixture.",
                kind="concept",
                operation="upsert_concept",
                semantic_key=f"v013:recent-changes:limit:{index}",
                confirm_no_case_data=True,
            )
        rebuild_living_wiki(store)
    with KnowledgeOS.open(root) as knowledge_os:
        result = knowledge_os.wiki.recent_changes(limit=1)
    assert result["returned_shard_count"] == 1
    assert result["total_shard_count"] >= 2
    assert result["truncated"] is True
    assert result["truncation_reason"] == "limit"


def test_recent_changes_admission_rejects_private_projection_for_public_scope(
    tmp_path: Path,
) -> None:
    root, _, _ = _vault(tmp_path)
    with pytest.raises(PermissionError):
        WikiReadService(root).execute(
            action="recent_changes",
            max_sensitivity="public",
        )


def test_recent_changes_v3_never_scans_the_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, _ = _vault(tmp_path)

    def fail_rglob(*_: Any, **__: Any) -> None:
        raise AssertionError("v3 Recent Changes must not scan the filesystem")

    monkeypatch.setattr(Path, "rglob", fail_rglob)
    with KnowledgeOS.open(root) as knowledge_os:
        result = knowledge_os.wiki.recent_changes(limit=20)
    assert result["schema_version"] == "deeplaw.living-wiki-recent-changes-read/v1"
    assert result["returned_shard_count"] == 1
