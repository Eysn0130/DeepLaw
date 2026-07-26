from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplaw.knowledge_markdown import export_knowledge_markdown
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
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
