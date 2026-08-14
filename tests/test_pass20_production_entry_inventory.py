from __future__ import annotations

import json
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def test_readme_default_mcp_examples_use_closed_launcher() -> None:
    for name in ("README.md", "README_EN.md"):
        text = (REPOSITORY / name).read_text(encoding="utf-8")
        assert "deeplaw knowledge mcp --closed-environment --stdio" in text
        assert "deeplaw mcp --closed-environment --stdio" in text


def test_current_qualification_runners_use_production_launcher() -> None:
    for name in (
        "run_codex_continuity_qualification.py",
        "run_opencode_continuity_qualification.py",
        "run_codex_token_attribution.py",
    ):
        text = (REPOSITORY / "benchmarks" / "hosts" / name).read_text(
            encoding="utf-8"
        )
        assert "--closed-environment" in text
        assert '"--vault"' not in text
        assert "_build_subprocess_environment" not in text

    for name in (
        "run_pass13_codex_continuity_qualification.py",
        "run_pass13_opencode_continuity_qualification.py",
    ):
        text = (REPOSITORY / "benchmarks" / "hosts" / name).read_text(
            encoding="utf-8"
        )
        assert "--closed-environment" in text
        assert '"mcp", "--stdio", "--vault"' not in text
        assert '"--vault", "vault"' not in text

    legal_gate = (
        REPOSITORY / "benchmarks/quality/run_authoritative_28_source_gate.py"
    ).read_text(encoding="utf-8")
    assert 'args=["mcp", "--closed-environment", "--stdio"]' in legal_gate
    assert 'args=["mcp", "--stdio"]' not in legal_gate


def test_shipped_static_and_generated_configs_are_path_free() -> None:
    paths = (
        "plugins/deeplaw/.mcp.json",
        "plugins/deeplaw/.claude-plugin/mcp.json",
        "plugins/deeplaw-knowledge-os/.mcp.json",
        "plugins/deeplaw-knowledge-os/.claude-plugin/mcp.json",
        "adapters/opencode/opencode.jsonc",
        "adapters/opencode/knowledge-os.jsonc",
        "adapters/opencode/knowledge-compiler.example.jsonc",
    )
    for relative in paths:
        payload = json.loads((REPOSITORY / relative).read_text(encoding="utf-8"))
        rendered = json.dumps(payload, sort_keys=True)
        assert "--closed-environment" in rendered
        assert "--vault" not in rendered

    for relative in (
        "adapters/obsidian/plugin/settings.ts",
        "adapters/obsidian/plugin/main.js",
    ):
        rendered = (REPOSITORY / relative).read_text(encoding="utf-8")
        assert "deeplaw knowledge mcp --closed-environment --stdio" in rendered
        assert "deeplaw knowledge mcp --autonomous" not in rendered
