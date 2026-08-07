from __future__ import annotations

import re
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPOSITORY / "plugins/deeplaw-knowledge-os/skills"
NEW_SKILLS = {
    "deeplaw-query",
    "deeplaw-compile-source",
    "deeplaw-verify-evidence",
    "deeplaw-refresh-synthesis",
    "deeplaw-navigate-wiki",
    "deeplaw-promote-draft",
}
READ_SKILLS = {"deeplaw-query", "deeplaw-verify-evidence", "deeplaw-navigate-wiki"}
WRITE_SKILLS = {
    "deeplaw-compile-source",
    "deeplaw-refresh-synthesis",
    "deeplaw-promote-draft",
}


def _skill_text(name: str) -> str:
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter_and_body(text: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    assert match is not None
    frontmatter = yaml.safe_load(match.group(1))
    assert isinstance(frontmatter, dict)
    return frontmatter, match.group(2)


def test_six_split_skills_have_closed_frontmatter_and_short_bodies() -> None:
    assert {
        path.name
        for path in SKILLS_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("deeplaw-")
    } >= NEW_SKILLS

    for name in sorted(NEW_SKILLS):
        frontmatter, body = _frontmatter_and_body(_skill_text(name))
        assert set(frontmatter) == {"name", "description"}
        assert frontmatter["name"] == name
        assert isinstance(frontmatter["description"], str)
        assert len(frontmatter["description"]) >= 40
        assert "TODO" not in body
        assert len(body.splitlines()) <= 40


def test_split_skill_agents_metadata_is_exact_and_triggerable() -> None:
    for name in sorted(NEW_SKILLS):
        metadata = yaml.safe_load(
            (SKILLS_ROOT / name / "agents/openai.yaml").read_text(encoding="utf-8")
        )
        assert set(metadata) == {"interface"}
        interface = metadata["interface"]
        assert set(interface) == {"display_name", "short_description", "default_prompt"}
        assert 25 <= len(interface["short_description"]) <= 64
        assert f"${name}" in interface["default_prompt"]


def test_read_workflows_use_only_bounded_support_operations() -> None:
    for name in READ_SKILLS:
        text = _skill_text(name)
        assert "knowledge_support" in text
        assert "knowledge_sink" not in text
        assert "wiki_lookup" not in text
        assert "plane=all" not in text
        assert "TODO" not in text
    assert "operation=query" in _skill_text("deeplaw-query")
    assert "operation=context" in _skill_text("deeplaw-query")
    assert "operation=source" in _skill_text("deeplaw-verify-evidence")
    assert "operation=verify" in _skill_text("deeplaw-verify-evidence")
    assert "operation=wiki" in _skill_text("deeplaw-navigate-wiki")


def test_write_workflows_require_owner_grants_and_no_automatic_mutation() -> None:
    for name in WRITE_SKILLS:
        text = _skill_text(name)
        assert "knowledge_sink" in text
        assert "owner-created" in text or "owner CLI" in text
        assert "Never create" in text
        assert "idempotency" in text or "draft_id" in text
        assert "automatically" in text
        assert "plane=all" not in text
        assert "wiki_lookup" not in text
    assert "promote_knowledge_draft" in _skill_text("deeplaw-promote-draft")
    assert "begin_synthesis_refresh" in _skill_text("deeplaw-refresh-synthesis")
    assert "begin_compilation" in _skill_text("deeplaw-compile-source")


def test_legacy_skill_is_explicitly_deprecated_and_routes_replacements() -> None:
    text = _skill_text("use-knowledge-assets")
    assert "Deprecated" in text
    assert "0.15.0" in text
    for name in sorted(NEW_SKILLS):
        assert f"${name}" in text
    assert "wiki_lookup" not in text
    metadata = yaml.safe_load(
        (SKILLS_ROOT / "use-knowledge-assets/agents/openai.yaml").read_text(encoding="utf-8")
    )
    assert metadata["policy"]["allow_implicit_invocation"] is False
    assert "$use-knowledge-assets" in metadata["interface"]["default_prompt"]
