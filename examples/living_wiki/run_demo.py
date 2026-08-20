from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from benchmarks.hosts.deterministic_fake_agent import compile_with_fake_mcp_agent
from deeplaw.api import KnowledgeOS
from deeplaw.compilation.models import SEMANTIC_COMPILER_GRANT_OPERATIONS
from deeplaw.compilation_handoff import build_compilation_handoff
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
        review_manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        source_review = vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=review_manifest["review_manifest_sha256"],
            reviewer_id="living-wiki-demo-owner",
            review_reason="Approve the public source-free demo fixture.",
        )
    source_revision_id = compiled["identity"]["source_revision_id"]
    handoff = build_compilation_handoff(
        vault_path,
        source_revision_id=source_revision_id,
    )
    with AutonomousKnowledgeStore(vault_path, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deeplaw-deterministic-fake-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]
    report = compile_with_fake_mcp_agent(
        vault=vault_path,
        grant_id=grant_id,
        source_revision_id=source_revision_id,
        packet_max_fragments=1,
    )
    with KnowledgeOS.open(vault_path) as knowledge_os:
        verification = knowledge_os.verify()
        query = knowledge_os.retrieval.query(
            "What enforces governance?",
            purpose="answer",
            query_plan_version="6",
        )
        context = knowledge_os.context.compile(
            task="Verify what enforces governance and retain exact source evidence.",
            purpose="verify",
            query_plan_version="6",
            applicable_duties=("primary_answer", "source_evidence"),
            confirm_no_case_data=True,
        )
        source_page = knowledge_os.wiki.page(
            f"wiki/sources/{source_revision_id}.md"
        )
    provider_bytes = context["provider_capsule"]["delivery"][
        "provider_content_bytes"
    ]
    source_page_content = source_page["content"]
    return {
        "schema_version": "deeplaw.living-wiki-demo/v1",
        "source_revision_id": source_revision_id,
        "compilation": report,
        "journey": {
            "init_doctor": {
                "status": "executed",
                "canonical_verification_valid": verification["valid"],
            },
            "source_add": {
                "status": "executed",
                "exact_source_sha256": compiled["compiler"]["source_sha256"],
            },
            "owner_source_review": {
                "status": "executed",
                "approved_asset_count": len(source_review["approved_asset_ids"]),
            },
            "compilation_handoff": {
                "status": "executed",
                "write_performed": handoff["write_performed"],
                "grant_included": handoff["boundaries"]["grant_included"],
                "model_invoked": handoff["boundaries"]["model_invoked"],
                "read_leaf": handoff["boundaries"]["read_leaf"],
                "write_leaf": handoff["boundaries"]["write_leaf"],
            },
            "owner_compiler_grant": {
                "status": "executed",
                "operation_count": len(SEMANTIC_COMPILER_GRANT_OPERATIONS),
            },
            "query": {
                "status": "executed",
                "selected_statement_count": query["capsule"][
                    "selected_statement_count"
                ],
            },
            "context": {
                "status": "executed",
                "provider_content_bytes": provider_bytes,
            },
            "wiki_exact_source_drill_down": {
                "status": "executed",
                "wiki_path": source_page["wiki_path"],
                "source_revision_present": source_revision_id in source_page_content,
                "page_sha256": hashlib.sha256(
                    source_page_content.encode("utf-8")
                ).hexdigest(),
            },
        },
        "commands": {
            "query": (
                "deeplaw knowledge query --vault <workspace>/vault "
                '--query "What enforces governance?" --purpose answer'
            ),
            "context": (
                "deeplaw knowledge context --vault <workspace>/vault "
                "--task 'Verify what enforces governance.' --purpose verify "
                "--confirm-no-case-data"
            ),
            "wiki_source": (
                "deeplaw knowledge wiki page --vault <workspace>/vault "
                f"--wiki-path wiki/sources/{source_revision_id}.md"
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
