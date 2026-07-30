from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.hosts.deterministic_fake_agent import compile_with_fake_agent
from deeplaw.compilation.models import COMPILER_GRANT_OPERATIONS
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json


def run_demo(workspace: Path) -> dict:
    if workspace.exists() and any(workspace.iterdir()):
        raise RuntimeError("demo workspace must be absent or empty")
    workspace.mkdir(parents=True, exist_ok=True)
    vault_path = workspace / "vault"
    source_path = workspace / "source.md"
    source_path.write_text(
        "# Admission\n"
        "Discovery proposes candidates; admission enforces governance.\n\n"
        "# Selection\n"
        "Selection must remain inside explicit item, source, character, and token budgets.\n",
        encoding="utf-8",
    )
    initialize_knowledge_vault(vault_path, name="living-wiki-demo", scope="project")
    initialize_autonomous_core(vault_path)
    with KnowledgeVault(vault_path, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source_path,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(vault_path, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deeplaw-deterministic-fake-agent",
            operations=COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    report = compile_with_fake_agent(
        vault=vault_path,
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
        packet_max_fragments=1,
    )
    return {
        "schema_version": "deeplaw.living-wiki-demo/v1",
        "source_revision_id": compiled["identity"]["source_revision_id"],
        "compilation": report,
        "commands": {
            "query": (
                "deeplaw knowledge query --vault <workspace>/vault "
                '--query "What enforces governance?" --purpose answer'
            ),
            "verify": "deeplaw knowledge autonomy verify --vault <workspace>/vault",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the offline deterministic DeepLaw Living Wiki compilation demo."
    )
    parser.add_argument("--workspace", required=True, type=Path)
    arguments = parser.parse_args()
    print(canonical_json(run_demo(arguments.workspace)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
