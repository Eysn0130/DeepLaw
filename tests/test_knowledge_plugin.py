from __future__ import annotations

import json
from pathlib import Path

import yaml

from deeplaw import __version__


def test_optional_knowledge_plugin_is_explicit_read_only_and_separate() -> None:
    repository = Path(__file__).resolve().parents[1]
    root = repository / "plugins" / "deeplaw-knowledge-os"
    codex = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
    claude = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    mcp = json.loads((root / ".mcp.json").read_text())
    openai = yaml.safe_load(
        (root / "skills" / "use-knowledge-assets" / "agents" / "openai.yaml").read_text()
    )

    codex_base, marker, cachebuster = codex["version"].partition("+codex.")
    assert codex_base == claude["version"] == __version__
    assert marker == "+codex."
    assert cachebuster
    assert codex["name"] == claude["name"] == "deeplaw-knowledge-os"
    assert openai["policy"]["allow_implicit_invocation"] is False
    assert mcp == {
        "mcpServers": {
            "deeplaw-knowledge": {
                "command": "deeplaw",
                "args": ["knowledge", "mcp", "--stdio"],
            }
        }
    }
    serialized = json.dumps(
        {"codex": codex, "claude": claude, "mcp": mcp},
        sort_keys=True,
    ).lower()
    assert '"law_support"' not in serialized
    assert '"remember"' not in serialized
    assert '"learn"' not in serialized

    knowledge_skill = (
        root / "skills" / "use-knowledge-assets" / "SKILL.md"
    ).read_text()
    legal_skill = (
        repository
        / "plugins"
        / "deeplaw"
        / "skills"
        / "research-chinese-law"
        / "SKILL.md"
    ).read_text()
    assert "Never invent one or use shell/filesystem tools to bypass" in knowledge_skill
    assert "Never use shell or\nfilesystem tools to run or bypass" in legal_skill


def test_marketplace_and_opencode_keep_both_products_isolated() -> None:
    repository = Path(__file__).resolve().parents[1]
    codex_marketplace = json.loads(
        (repository / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    codex_entries = {
        plugin["name"]: plugin for plugin in codex_marketplace["plugins"]
    }
    assert set(codex_entries) == {"deeplaw", "deeplaw-knowledge-os"}
    assert codex_entries["deeplaw"]["source"]["path"] == "./plugins/deeplaw"
    assert (
        codex_entries["deeplaw-knowledge-os"]["source"]["path"]
        == "./plugins/deeplaw-knowledge-os"
    )

    claude_marketplace = json.loads(
        (repository / ".claude-plugin" / "marketplace.json").read_text()
    )
    claude_entries = {
        plugin["name"]: plugin for plugin in claude_marketplace["plugins"]
    }
    assert claude_marketplace["version"] == __version__
    assert set(claude_entries) == set(codex_entries)
    for name in claude_entries:
        assert claude_entries[name]["version"] == __version__
        assert claude_entries[name]["source"] == f"./plugins/{name}"

    config = (repository / "adapters" / "opencode" / "knowledge-os.jsonc").read_text()
    agent = (
        repository / "adapters" / "opencode" / "agents" / "deeplaw-knowledge.md"
    ).read_text()
    assert '"deeplaw_knowledge_*": "deny"' in config
    assert "deeplaw_knowledge_knowledge_support: allow" in agent
    assert "deeplaw_law_support" not in agent
    assert "mode: subagent" in agent
