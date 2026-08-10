from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from deeplaw.editor_bridge import (
    context_for_editor,
    host_bridge_contract,
    host_context_envelope,
    merge_standard_mcp_config,
    tolaria_context_envelope,
    tolaria_mcp_servers,
    tolaria_open_note_request,
)

TOLARIA_VERSION = "alpha-v2026.8.10-alpha.0001"
TOLARIA_COMMIT = "ab01faa6773136a58285d04cb81e2587c11bac85"


def _step(status: str, **details: object) -> dict[str, object]:
    return {"status": status, **details}


def _run_cli(*arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError("DeepLaw domain CLI failed closed")
    if len(completed.stdout.encode("utf-8")) > 65_536:
        raise RuntimeError("DeepLaw domain CLI exceeded the provider byte bound")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("DeepLaw domain CLI returned an invalid receipt")
    return value


def main() -> int:
    # The harness uses a synthetic, temporary Vault and only read-only Domain
    # APIs.  No host binary, model, network, Grant, or canonical mutation is
    # inferred from this deterministic run.
    with tempfile.TemporaryDirectory(prefix="deeplaw-tolaria-harness-") as temporary:
        vault = Path(temporary) / "vault"
        initialized = _run_cli(
            "knowledge",
            "--format",
            "json",
            "init",
            "--vault",
            str(vault),
            "--name",
            "tolaria-harness",
            "--scope",
            "project",
        )
        snapshot = {
            "activeNote": {"path": "notes/active.md", "body": "# Source-free fixture"},
            "openTabs": [{"path": "notes/related.md"}],
            "referencedNotes": [],
        }

        bridge = host_bridge_contract("tolaria")
        envelope = host_context_envelope(
            "tolaria",
            snapshot,
            workspace_identity=initialized["vault_id"],
            repository_identity="tolaria-harness",
            task="Find the governed admission boundary.",
            goal="Return a bounded, verifiable local context.",
            requested_purpose="answer",
            scope="project",
            max_sensitivity="private",
            token_budget=1_000,
        )

        # Keep the legacy editor bridge exercised as a compatibility check.  The
        # provider-facing value above is the host-neutral v1 envelope.
        legacy_envelope = tolaria_context_envelope(
            snapshot,
            vault_identity=initialized["vault_id"],
            user_intent="Find the governed admission boundary.",
            frontend_version=TOLARIA_VERSION,
        )
        context = context_for_editor(vault, legacy_envelope)
        retrieval = _run_cli(
            "knowledge",
            "--format",
            "json",
            "query",
            "--vault",
            str(vault),
            "--query",
            "Find the governed admission boundary.",
            "--purpose",
            "answer",
            "--scope",
            "project",
            "--max-sensitivity",
            "private",
            "--limit",
            "4",
            "--max-chars",
            "4000",
            "--max-tokens",
            "1000",
            "--query-plan-version",
            "6",
            "--capsule-projection",
            "compact",
        )
        merged = merge_standard_mcp_config(
            {"mcpServers": {"tolaria": {"command": "node", "args": ["index.js"]}}},
            tolaria_mcp_servers(deeplaw_executable="deeplaw", vault_path=vault),
        )
        open_request = tolaria_open_note_request("wiki/index.md", vault_path=vault)

        result = {
            "schema_version": "deeplaw.tolaria-integration-harness/v1",
            "integration_status": bridge["integration_status"],
            "source_fixture": "public_synthetic",
            "exact_upstream": {
                "name": "Tolaria",
                "version": TOLARIA_VERSION,
                "commit": TOLARIA_COMMIT,
            },
            "agent_context": {
                "schema_version": envelope["schema_version"],
                "envelope_sha256": envelope["envelope_sha256"],
                "ephemeral": envelope["ephemeral"],
                "persistence_allowed": envelope["persistence_allowed"],
                "persistence_performed": envelope["persistence_performed"],
                "authority": envelope["authority"],
                "legal_authority": envelope["legal_authority"],
            },
            "tolaria_entry_preserved": "tolaria" in merged["mcpServers"],
            "deeplaw_entry_added": "deeplaw_knowledge" in merged["mcpServers"],
            "editor_context_ephemeral": context["ephemeral_context"],
            "persistence_allowed": False,
            "persistence_performed": False,
            "authority": "none",
            "legal_authority": False,
            "open_note_ui_only": open_request["mutation"] == "ui_only",
            "steps": {
                "active_note_snapshot": _step(
                    "executed",
                    path="notes/active.md",
                    source_fixture="public_synthetic",
                ),
                "agent_context_envelope": _step(
                    "executed",
                    schema_version=envelope["schema_version"],
                    envelope_sha256=envelope["envelope_sha256"],
                ),
                "read_only_query_v6": _step(
                    "executed",
                    query_plan_version="6",
                    statement_count=len(retrieval.get("statements", [])),
                ),
                "context_preview": _step(
                    "executed",
                    legacy_editor_context=True,
                    ephemeral_context=context["ephemeral_context"],
                    persistence_performed=context["persistence_performed"],
                ),
                "wiki_resolver_page_intent": _step(
                    "not_executed",
                    reason=(
                        "The synthetic Vault has no built Living Wiki resolver registry, and "
                        "the frozen Tolaria release exposes no stable third-party page-intent "
                        "preview or promotion extension point."
                    ),
                ),
                "draft": _step(
                    "not_executed",
                    reason=(
                        "No non-canonical draft is created automatically by this read-only "
                        "harness; a UI or owner-directed draft action is required."
                    ),
                ),
                "explicit_promotion": _step(
                    "not_executed",
                    reason=(
                        "No owner-created knowledge_sink Grant is supplied; promotion authority "
                        "cannot be inferred from host context."
                    ),
                ),
                "refreshed_revision": _step(
                    "not_executed",
                    reason=(
                        "No explicit promotion receipt exists to trigger a rebuild or "
                        "revision read."
                    ),
                ),
            },
            "valid": True,
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
