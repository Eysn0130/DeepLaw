from __future__ import annotations

import json
import sys
from pathlib import Path

from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.local_reranker import load_local_reranker_manifest
from deeplaw.retrieval_fabric import retrieve
from deeplaw.util import sha256_file


def _manifest(tmp_path: Path) -> Path:
    wrapper = tmp_path / "reranker.py"
    wrapper.write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "ordered = [item['asset_id'] for item in reversed(request['candidates'])]\n"
        "json.dump({'schema_version': 'deeplaw.local-reranker-output/v1', "
        "'ordered_asset_ids': ordered}, sys.stdout)\n",
        encoding="utf-8",
    )
    model = tmp_path / "model.bin"
    model.write_bytes(b"fixed-test-model")
    manifest = {
        "schema_version": "deeplaw.local-reranker-manifest/v1",
        "implementation_revision": "test-wrapper/1",
        "model_identity": "example/fixed-test-reranker",
        "model_revision": "0123456789abcdef",
        "model_files": [
            {"path": str(wrapper), "sha256": sha256_file(wrapper)},
            {"path": str(model), "sha256": sha256_file(model)},
        ],
        "command": [str(Path(sys.executable).resolve()), str(wrapper), str(model)],
        "network_policy": "offline",
        "max_candidates": 10,
        "max_input_chars": 100_000,
        "max_output_bytes": 10_000,
        "timeout_seconds": 10,
    }
    path = tmp_path / "reranker.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _active(vault: KnowledgeVault, title: str, statement: str) -> str:
    proposal = vault.propose_asset(
        kind="fact",
        memory_tier="project",
        title=title,
        statement=statement,
    )
    return vault.approve_asset(proposal.asset_id, confirm_reviewed=True).asset_id


def test_local_reranker_is_pinned_offline_bounded_and_candidate_only(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="reranker", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        first = _active(vault, "Shared reranker A", "Shared reranker phrase alpha.")
        second = _active(vault, "Shared reranker B", "Shared reranker phrase beta.")
    manifest_path = _manifest(tmp_path)
    loaded = load_local_reranker_manifest(manifest_path)

    with KnowledgeVault(root, read_only=True) as vault:
        baseline = retrieve(vault, "shared reranker phrase", mode="lexical", limit=2)
        reranked = retrieve(
            vault,
            "shared reranker phrase",
            mode="lexical",
            limit=2,
            reranker_manifest=manifest_path,
        )

    baseline_ids = [item["asset_id"] for item in baseline["results"]]
    reranked_ids = [item["asset_id"] for item in reranked["results"]]
    assert set(baseline_ids) == set(reranked_ids) == {first, second}
    assert reranked_ids == list(reversed(baseline_ids))
    assert reranked["trace"]["query_plan"]["reranker_profile"] == loaded["profile_id"]
    assert [item["asset_id"] for item in reranked["trace"]["reranker_ranks"]] == (
        list(reversed(baseline_ids))
    )
    assert reranked["trace"]["numeric_confidence_exposed"] is False
    assert reranked["trace"]["authority_changed_by_ranking"] is False
