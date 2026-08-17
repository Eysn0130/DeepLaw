from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from deeplaw import __version__


def test_wheel_packages_the_exact_local_codex_marketplace() -> None:
    repository = Path(__file__).resolve().parents[1]
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    included = project["tool"]["hatch"]["build"]["targets"]["wheel"][
        "force-include"
    ]

    assert included[".agents/plugins/marketplace.json"] == (
        "deeplaw/codex_marketplace/.agents/plugins/marketplace.json"
    )
    for plugin_name in ("deeplaw", "deeplaw-knowledge-os"):
        assert included[f"plugins/{plugin_name}"] == (
            f"deeplaw/codex_marketplace/plugins/{plugin_name}"
        )
    assert included["adapters/opencode"] == "deeplaw/opencode_adapter"


def test_optional_knowledge_plugin_is_explicit_read_only_and_separate() -> None:
    repository = Path(__file__).resolve().parents[1]
    root = repository / "plugins" / "deeplaw-knowledge-os"
    codex = json.loads(
        (root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    claude = json.loads(
        (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    compatibility_mcp = json.loads(
        (root / ".mcp.json").read_text(encoding="utf-8")
    )
    codex_mcp = compatibility_mcp
    claude_mcp = json.loads(
        (root / ".claude-plugin" / "mcp.json").read_text(encoding="utf-8")
    )
    openai = yaml.safe_load(
        (
            root / "skills" / "use-knowledge-assets" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
    )
    compiler_openai = yaml.safe_load(
        (
            root / "skills" / "compile-living-wiki" / "agents" / "openai.yaml"
        ).read_text(encoding="utf-8")
    )

    assert codex["version"] == claude["version"] == __version__
    assert codex["name"] == claude["name"] == "deeplaw-knowledge-os"
    assert openai["policy"]["allow_implicit_invocation"] is False
    assert compiler_openai["policy"]["allow_implicit_invocation"] is False
    assert codex["mcpServers"] == "./.mcp.json"
    assert claude["mcpServers"] == "./.claude-plugin/mcp.json"
    assert codex_mcp == claude_mcp == compatibility_mcp == {
        "mcpServers": {
            "deeplaw-knowledge": {
                "command": "deeplaw",
                "args": ["knowledge", "mcp", "--closed-environment", "--stdio"],
            }
        }
    }
    serialized = json.dumps(
        {
            "codex": codex,
            "claude": claude,
            "codex_mcp": codex_mcp,
            "claude_mcp": claude_mcp,
            "compatibility_mcp": compatibility_mcp,
        },
        sort_keys=True,
    ).lower()
    assert '"law_support"' not in serialized
    assert '"remember"' not in serialized
    assert '"learn"' not in serialized

    knowledge_skill = (
        root / "skills" / "use-knowledge-assets" / "SKILL.md"
    ).read_text(encoding="utf-8")
    legal_skill = (
        repository
        / "plugins"
        / "deeplaw"
        / "skills"
        / "research-chinese-law"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "Never emulate a write with shell or\nfilesystem tools" in knowledge_skill
    assert "independently enabled" in knowledge_skill
    compiler_skill = (
        root / "skills" / "compile-living-wiki" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "knowledge_support" in compiler_skill
    assert "knowledge_sink" in compiler_skill
    assert "Never create a grant" in compiler_skill
    assert "run reaches `succeeded`" in compiler_skill
    assert "Never use shell or\nfilesystem tools to run or bypass" in legal_skill


def test_codex_plugins_use_only_plugin_json_and_root_mcp_configuration() -> None:
    repository = Path(__file__).resolve().parents[1]
    expected = {
        "deeplaw": ("deeplaw", ["mcp", "--closed-environment", "--stdio"]),
        "deeplaw-knowledge-os": (
            "deeplaw-knowledge",
            ["knowledge", "mcp", "--closed-environment", "--stdio"],
        ),
    }
    for plugin_name, (server_name, arguments) in expected.items():
        root = repository / "plugins" / plugin_name
        codex_dir = root / ".codex-plugin"
        assert sorted(path.name for path in codex_dir.iterdir()) == ["plugin.json"]
        manifest = json.loads((codex_dir / "plugin.json").read_text(encoding="utf-8"))
        assert manifest["mcpServers"] == "./.mcp.json"
        mcp = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        assert mcp == {
            "mcpServers": {
                server_name: {
                    "command": "deeplaw",
                    "args": arguments,
                }
            }
        }
def test_marketplace_and_opencode_keep_both_products_isolated() -> None:
    repository = Path(__file__).resolve().parents[1]
    codex_marketplace = json.loads(
        (repository / ".agents" / "plugins" / "marketplace.json").read_text(
            encoding="utf-8"
        )
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
        (repository / ".claude-plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    claude_entries = {
        plugin["name"]: plugin for plugin in claude_marketplace["plugins"]
    }
    assert claude_marketplace["version"] == __version__
    assert set(claude_entries) == set(codex_entries)
    for name in claude_entries:
        assert claude_entries[name]["version"] == __version__
        assert claude_entries[name]["source"] == f"./plugins/{name}"

    config = (
        repository / "adapters" / "opencode" / "knowledge-os.jsonc"
    ).read_text(encoding="utf-8")
    agent = (
        repository / "adapters" / "opencode" / "agents" / "deeplaw-knowledge.md"
    ).read_text(encoding="utf-8")
    assert '"*": "deny"' in config
    assert '"deeplaw_knowledge_knowledge_support": "allow"' in config
    assert "deeplaw_knowledge_knowledge_support: allow" in agent
    assert "deeplaw_law_support" not in agent
    assert "mode: subagent" in agent

    opencode_manifest = json.loads(
        (repository / "adapters" / "opencode" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert opencode_manifest["version"] == __version__
    assert opencode_manifest["model_or_api_call_required_for_lifecycle"] is False
    assert {item["id"] for item in opencode_manifest["products"]} == {
        "deeplaw",
        "deeplaw-knowledge-os",
    }
    knowledge_product = next(
        item
        for item in opencode_manifest["products"]
        if item["id"] == "deeplaw-knowledge-os"
    )
    assert knowledge_product["optional_compile_config"] == (
        "knowledge-compiler.example.jsonc"
    )
    assert knowledge_product["optional_compile_agent"] == (
        "agents/deeplaw-compiler.md"
    )
    assert knowledge_product["optional_compile_skill"].endswith(
        "/skills/deeplaw-compile-source"
    )
    assert knowledge_product["skill"].endswith("/skills/deeplaw-query")
    assert "use-knowledge-assets" not in json.dumps(knowledge_product)
