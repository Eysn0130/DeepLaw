from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from deeplaw.editor_bridge import (
    context_for_editor,
    merge_standard_mcp_config,
    tolaria_context_envelope,
    tolaria_mcp_servers,
    tolaria_open_note_request,
)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="deeplaw-tolaria-harness-") as temporary:
        vault = Path(temporary) / "vault"
        initialization = subprocess.run(
            [
                sys.executable,
                "-m",
                "deeplaw",
                "knowledge",
                "init",
                "--vault",
                str(vault),
                "--name",
                "tolaria-harness",
                "--scope",
                "project",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        initialized = json.loads(initialization.stdout)
        snapshot = {
            "activeNote": {"path": "notes/active.md", "body": "# Source-free fixture"},
            "openTabs": [{"path": "notes/related.md"}],
            "referencedNotes": [],
        }
        envelope = tolaria_context_envelope(
            snapshot,
            vault_identity=initialized["vault_id"],
            user_intent="Find the governed admission boundary.",
            frontend_version="v2026-06-23",
        )
        context = context_for_editor(vault, envelope)
        merged = merge_standard_mcp_config(
            {"mcpServers": {"tolaria": {"command": "node", "args": ["index.js"]}}},
            tolaria_mcp_servers(deeplaw_executable="deeplaw", vault_path=vault),
        )
        open_request = tolaria_open_note_request("wiki/index.md", vault_path=vault)
        result = {
            "schema_version": "deeplaw.tolaria-integration-harness/v1",
            "source_fixture": "public_synthetic",
            "tolaria_entry_preserved": "tolaria" in merged["mcpServers"],
            "deeplaw_entry_added": "deeplaw_knowledge" in merged["mcpServers"],
            "editor_context_ephemeral": context["ephemeral_context"],
            "persistence_performed": context["persistence_performed"],
            "open_note_ui_only": open_request["mutation"] == "ui_only",
            "valid": True,
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
