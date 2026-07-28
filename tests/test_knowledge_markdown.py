from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_markdown import export_knowledge_markdown
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.projection_workflow import projection_diff, propose_projection_edits
from deeplaw.util import sha256_file


def _ready_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="markdown", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        constraint = vault.propose_asset(
            kind="constraint",
            memory_tier="project",
            title="Stable contract",
            statement="Preserve the stable public contract.",
            semantic_key="contract.stability",
            sensitivity="internal",
        )
        decision = vault.propose_asset(
            kind="decision",
            memory_tier="project",
            title="Storage choice",
            statement="Use SQLite as the canonical local store.",
            semantic_key="storage.choice",
            sensitivity="internal",
        )
        first = vault.approve_asset(constraint.asset_id, confirm_reviewed=True)
        second = vault.approve_asset(decision.asset_id, confirm_reviewed=True)
        vault.add_relation(
            subject_asset_id=second.asset_id,
            predicate="implements",
            object_asset_id=first.asset_id,
            confirm_reviewed=True,
        )
    return root


def test_markdown_is_a_deterministic_projection_with_backlinks(tmp_path: Path) -> None:
    root = _ready_vault(tmp_path)
    first_output = tmp_path / "markdown-one"
    second_output = tmp_path / "markdown-two"
    with KnowledgeVault(root, read_only=True) as vault:
        first = export_knowledge_markdown(
            vault,
            first_output,
            max_sensitivity="internal",
        )
        second = export_knowledge_markdown(
            vault,
            second_output,
            max_sensitivity="internal",
        )

    assert first["files"] == second["files"]
    assert first["asset_count"] == 2
    assert (first_output / "INDEX.md").is_file()
    decision_file = next((first_output / "project" / "decision").glob("*.md"))
    content = decision_file.read_text()
    assert "directive_mode" in content
    assert "legal_authority: false" in content
    assert "`implements`" in content
    assert "The SQLite vault remains canonical" in (first_output / "INDEX.md").read_text()
    manifest = json.loads((first_output / "manifest.json").read_text())
    for item in manifest["files"]:
        assert sha256_file(first_output / item["path"]) == item["sha256"]


def test_markdown_export_refuses_to_overwrite_without_explicit_replace(
    tmp_path: Path,
) -> None:
    root = _ready_vault(tmp_path)
    output = tmp_path / "markdown"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_markdown(vault, output, max_sensitivity="internal")
        with pytest.raises(FileExistsError, match="--replace"):
            export_knowledge_markdown(vault, output, max_sensitivity="internal")
        replaced = export_knowledge_markdown(
            vault,
            output,
            max_sensitivity="internal",
            replace=True,
        )

    assert replaced["asset_count"] == 2


def test_markdown_replace_never_deletes_an_unowned_directory(tmp_path: Path) -> None:
    root = _ready_vault(tmp_path)
    output = tmp_path / "unowned"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("user data", encoding="utf-8")

    with (
        KnowledgeVault(root, read_only=True) as vault,
        pytest.raises(RuntimeError, match="not owned by a DeepLaw export"),
    ):
        export_knowledge_markdown(
            vault,
            output,
            max_sensitivity="internal",
            replace=True,
        )

    assert marker.read_text(encoding="utf-8") == "user data"


def test_markdown_replace_refuses_to_delete_untracked_user_notes(
    tmp_path: Path,
) -> None:
    root = _ready_vault(tmp_path)
    output = tmp_path / "markdown"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_markdown(vault, output, max_sensitivity="internal")
    marker = output / "my-notes.md"
    marker.write_text("Keep this human note.", encoding="utf-8")

    with (
        KnowledgeVault(root, read_only=True) as vault,
        pytest.raises(RuntimeError, match="untracked or missing"),
    ):
        export_knowledge_markdown(
            vault,
            output,
            max_sensitivity="internal",
            replace=True,
        )

    assert marker.read_text(encoding="utf-8") == "Keep this human note."


def test_markdown_renders_asset_content_as_literal_untrusted_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="literal-markdown", scope="project")
    with KnowledgeVault(root, read_only=False) as vault:
        proposal = vault.propose_asset(
            kind="reference",
            memory_tier="project",
            title="Unsafe [link](https://example.invalid) <img src=x>",
            statement=(
                "Reference text must remain literal.\n"
                "![external](https://example.invalid/track.png)\n"
                "```\n"
                "<script>not executable</script>"
            ),
            sensitivity="internal",
        )
        vault.approve_asset(
            proposal.asset_id,
            confirm_reviewed=True,
            confirm_quarantined=True,
        )
    output = tmp_path / "markdown"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_markdown(vault, output, max_sensitivity="internal")

    asset_file = next((output / "project" / "reference").glob("*.md"))
    content = asset_file.read_text(encoding="utf-8")
    assert "# Unsafe \\[link\\]\\(https://example\\.invalid\\)" in content
    assert "&lt;img src=x&gt;" in content
    assert "````text\nReference text must remain literal." in content
    assert "\n````\n\n## Provenance" in content


def test_projection_edit_becomes_quarantined_source_bound_revision(
    tmp_path: Path,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="projection edit", scope="project")
    source = tmp_path / "rule.md"
    source.write_text(
        "# Release rule\nVerify the artifact before deployment.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            typed_extraction="deterministic-v2",
            confirm_no_case_data=True,
        )
        source_id = compiled["source"]["source_id"]
        manifest = vault.source_review_manifest(source_id)
        vault.approve_source_assets(
            source_id,
            confirm_reviewed=True,
            confirm_quarantined=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
    projection = tmp_path / "projection"
    with KnowledgeVault(root, read_only=True) as vault:
        export_knowledge_markdown(vault, projection, max_sensitivity="private")
    page = next((projection / "constraints").glob("knowledge_*.md"), None)
    if page is None:
        page = next(
            path
            for directory in ("knowledge", "procedures")
            for path in (projection / directory).glob("knowledge_*.md")
        )
    content = page.read_text(encoding="utf-8")
    asset_id = next(
        line.split('"')[1]
        for line in content.splitlines()
        if line.startswith("asset_id: ")
    )
    with KnowledgeVault(root, read_only=True) as vault:
        active = vault.get_asset(asset_id)
        before_lineage = vault.knowledge_lineage(asset_id=active.asset_id)
    page.write_text(
        content.replace(
            "Verify the artifact before deployment.",
            "Verify the signed artifact twice before deployment.",
        ),
        encoding="utf-8",
    )

    with KnowledgeVault(root, read_only=True) as vault:
        diff = projection_diff(vault, projection)
    assert diff["proposal_eligible_count"] == 1
    assert diff["canonical_write_performed"] is False

    with KnowledgeVault(root, read_only=False) as vault:
        result = propose_projection_edits(
            vault,
            projection,
            confirm_no_case_data=True,
        )
        proposal = vault.get_asset(
            result["proposals"][0]["asset_id"], include_inactive=True
        )
        predecessor = vault.get_asset(active.asset_id)
        after_lineage = vault.knowledge_lineage(asset_id=proposal.asset_id)
        assert vault.verify_integrity()["valid"] is True

    assert result["review_required"] is True
    assert result["approval_inherited"] is False
    assert proposal.status == "quarantined"
    assert proposal.verification == "source_bound"
    assert proposal.source_refs == predecessor.source_refs
    assert after_lineage["knowledge_key"] == before_lineage["knowledge_key"]
    assert any(
        item["status"] == "modified" for item in after_lineage["transitions"]
    )
