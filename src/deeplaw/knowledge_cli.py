from __future__ import annotations

import argparse
import json
import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from .api import KnowledgeOS
from .compilation.models import (
    COMPILER_GRANT_OPERATIONS,
    SEMANTIC_COMPILER_GRANT_OPERATIONS,
)
from .context_compiler import compile_context, verify_capsule_file
from .knowledge_autonomy import (
    FEEDBACK_EVALUATOR_TYPES,
    KNOWLEDGE_KINDS,
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    _validate_contract,
    autonomous_core_installed,
    initialize_autonomous_core,
    migrate_autonomous_core,
    rollback_autonomous_core,
)
from .knowledge_autonomy import (
    SCOPES as AUTONOMOUS_SCOPES,
)
from .knowledge_autonomy import (
    SENSITIVITIES as AUTONOMOUS_SENSITIVITIES,
)
from .knowledge_compiler import (
    TYPED_EXTRACTION_MODES,
    compile_directory,
    compile_source,
    record_capsule_feedback,
    record_debug_experience,
)
from .knowledge_discovery import (
    DISCOVERY_MODEL_PROFILES,
    DiscoveryIndex,
    build_discovery_index,
    setup_discovery_model,
    verify_discovery_index,
    verify_discovery_model,
)
from .knowledge_feedback import (
    create_run_receipt,
    record_structured_feedback,
    replay_feedback,
)
from .knowledge_identity import normalize_logical_path
from .knowledge_inbox import (
    list_inbox_artifacts,
    promote_inbox_proposal,
    reject_inbox_artifact,
    submit_inbox_artifact,
    verify_inbox_artifact,
)
from .knowledge_jobs import (
    cancel_ingest_job,
    list_ingest_jobs,
    load_ingest_job,
    run_ingest_job,
)
from .knowledge_maintenance import (
    create_knowledge_snapshot,
    detect_knowledge_orphans,
    garbage_collect_derived,
    knowledge_doctor,
    restore_knowledge_snapshot,
    verify_knowledge_snapshot,
)
from .knowledge_markdown import export_knowledge_markdown
from .knowledge_models import (
    ASSET_KINDS,
    MEMORY_TIERS,
    SENSITIVITY_LEVELS,
    SOURCE_KINDS,
    USER_SETTABLE_TRUST_LEVELS,
    AssetKind,
    MemoryTier,
    Sensitivity,
    SourceKind,
    TrustLevel,
)
from .knowledge_package import (
    export_knowledge_package,
    import_knowledge_package,
    verify_knowledge_package,
)
from .knowledge_store import (
    RELATION_PREDICATES,
    VAULT_SCOPES,
    KnowledgeVault,
    VaultScope,
    default_knowledge_vault,
    initialize_knowledge_vault,
    knowledge_vault_permission_report,
    restore_knowledge_migration_backup,
    verify_knowledge_migration_backup,
)
from .lineage_workflow import review_lineage_mapping
from .projection_workflow import projection_diff, propose_projection_edits
from .relation_workflow import (
    pending_relation_carry_forward,
    plan_relation_carry_forward,
    propose_relation_carry_forward,
    review_relation_carry_forward,
)
from .retrieval_fabric import (
    RETRIEVAL_MODES,
    RetrievalMode,
    compare_retrieval,
    recall,
    retrieve,
    verify_retrieval_trace,
)
from .retrieval_profiles import (
    activate_retrieval_profile,
    evaluate_retrieval_profile,
    load_active_retrieval_profile,
    rollback_retrieval_profile,
    train_retrieval_profile,
)
from .skill_factory import (
    SKILL_TARGETS,
    SkillTarget,
    build_skill_bundle,
    install_skill_bundle,
    verify_skill_bundle,
)
from .util import excerpt, strict_json_loads


@contextmanager
def _command_vault(
    path: Path,
    *,
    read_only: bool,
) -> Iterator[KnowledgeVault]:
    """Close a successful legacy write before importing its immutable evidence."""
    with KnowledgeVault(path, read_only=read_only) as vault:
        yield vault
    if not read_only and autonomous_core_installed(path):
        with AutonomousKnowledgeStore(path, read_only=False):
            pass


def _add_typed_extraction_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--typed-extraction",
        choices=sorted(TYPED_EXTRACTION_MODES),
        default="off",
    )
    parser.add_argument(
        "--typed-extractor-manifest",
        type=Path,
        help="Closed local sidecar manifest for local or explicit external extraction",
    )
    parser.add_argument(
        "--confirm-external-disclosure",
        action="store_true",
        help="Confirm section text and locators may be sent by the explicit external sidecar",
    )
    parser.add_argument(
        "--no-reference-proposals",
        dest="reference_proposals",
        action="store_false",
        default=True,
        help="Compile Source IR without creating Reference proposals",
    )


def add_knowledge_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    knowledge = commands.add_parser(
        "knowledge",
        help="Operate the local Markdown-native Agent Knowledge OS",
    )
    knowledge.add_argument(
        "--format",
        dest="knowledge_format",
        choices=("json", "jsonl", "human"),
        default="json",
        help="Render the final result as pretty JSON, one JSONL event, or human-readable text",
    )
    subcommands = knowledge.add_subparsers(dest="knowledge_command", required=True)

    init = subcommands.add_parser("init", help="Initialize an owner-only knowledge vault")
    init.add_argument("--vault", type=Path, default=default_knowledge_vault())
    init.add_argument("--name", required=True)
    init.add_argument("--scope", choices=sorted(VAULT_SCOPES), default="personal")
    init.add_argument(
        "--legacy-review-core",
        action="store_true",
        help="Initialize only the v0.7 migration baseline without the autonomous core",
    )

    ingest = subcommands.add_parser(
        "ingest",
        help="Compile a source into review candidates; never activates them automatically",
    )
    ingest.add_argument("--vault", type=Path, default=default_knowledge_vault())
    ingest.add_argument("--source", type=Path, required=True)
    ingest.add_argument("--source-kind", choices=sorted(SOURCE_KINDS), default="document")
    ingest.add_argument("--title")
    ingest.add_argument("--origin-uri")
    ingest.add_argument(
        "--trust",
        choices=sorted(USER_SETTABLE_TRUST_LEVELS),
        default="user_provided",
    )
    ingest.add_argument(
        "--sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    ingest.add_argument(
        "--pdf-fallback",
        choices=("off", "vision-consensus", "document-engine"),
        default="off",
    )
    _add_typed_extraction_arguments(ingest)
    ingest.add_argument("--confirm-no-case-data", action="store_true")

    source = subcommands.add_parser(
        "source",
        help="Manage logical sources and immutable source versions",
    )
    source_commands = source.add_subparsers(dest="source_command", required=True)
    source_add = source_commands.add_parser("add", help="Add one immutable source version")
    source_add.add_argument("--vault", type=Path, default=default_knowledge_vault())
    source_add.add_argument("--source", type=Path, required=True)
    source_add.add_argument("--source-kind", choices=sorted(SOURCE_KINDS), default="document")
    source_add.add_argument("--title")
    source_add.add_argument("--origin-uri")
    source_add.add_argument(
        "--trust", choices=sorted(USER_SETTABLE_TRUST_LEVELS), default="user_provided"
    )
    source_add.add_argument("--sensitivity", choices=sorted(SENSITIVITY_LEVELS), default="private")
    source_add.add_argument(
        "--pdf-fallback", choices=("off", "vision-consensus", "document-engine"), default="off"
    )
    _add_typed_extraction_arguments(source_add)
    source_add.add_argument("--confirm-no-case-data", action="store_true")

    source_add_dir = source_commands.add_parser(
        "add-dir",
        help="Compile a bounded directory manifest with per-file atomicity",
    )
    source_add_dir.add_argument("--vault", type=Path, default=default_knowledge_vault())
    source_add_dir.add_argument("--directory", type=Path, required=True)
    source_add_dir.add_argument("--recursive", action="store_true")
    source_add_dir.add_argument("--include", action="append", default=[])
    source_add_dir.add_argument("--exclude", action="append", default=[])
    source_add_dir.add_argument("--source-kind", choices=sorted(SOURCE_KINDS), default="document")
    source_add_dir.add_argument(
        "--trust", choices=sorted(USER_SETTABLE_TRUST_LEVELS), default="user_provided"
    )
    source_add_dir.add_argument(
        "--sensitivity", choices=sorted(SENSITIVITY_LEVELS), default="private"
    )
    source_add_dir.add_argument(
        "--pdf-fallback", choices=("off", "vision-consensus", "document-engine"), default="off"
    )
    _add_typed_extraction_arguments(source_add_dir)
    source_add_dir.add_argument("--dry-run", action="store_true")
    source_add_dir.add_argument("--confirm-no-case-data", action="store_true")

    for name, help_text in (
        ("list", "List logical source versions"),
        ("show", "Show one exact source version"),
        ("verify", "Verify one source version and stored bytes"),
        ("diff", "Compare two versions of one logical source"),
        ("update", "Compile a successor for an existing logical source"),
        ("remove", "Remove one source version through the audited lifecycle"),
    ):
        source_command = source_commands.add_parser(name, help=help_text)
        source_command.add_argument("--vault", type=Path, default=default_knowledge_vault())
        if name in {"show", "verify", "remove"}:
            source_target = source_command.add_mutually_exclusive_group(required=True)
            source_target.add_argument("--source-id")
            source_target.add_argument(
                "--alias",
                help="Stable logical-path alias; historical paths resolve across rename/move",
            )
            source_selector = source_command.add_mutually_exclusive_group()
            source_selector.add_argument("--active", action="store_true")
            source_selector.add_argument("--latest", action="store_true")
        if name == "list":
            source_filter = source_command.add_mutually_exclusive_group()
            source_filter.add_argument("--source-key")
            source_filter.add_argument("--alias")
            source_selector = source_command.add_mutually_exclusive_group()
            source_selector.add_argument("--active", action="store_true")
            source_selector.add_argument("--latest", action="store_true")
        if name == "diff":
            source_command.add_argument("--old-source-id")
            source_command.add_argument("--new-source-id")
            source_command.add_argument(
                "--alias",
                help="Compare the latest two versions of this stable logical-path alias",
            )
            source_command.add_argument("--latest", action="store_true")
        if name == "update":
            source_target = source_command.add_mutually_exclusive_group(required=True)
            source_target.add_argument("--source-key")
            source_target.add_argument("--alias")
            source_command.add_argument("--source", type=Path, required=True)
            source_command.add_argument(
                "--source-kind", choices=sorted(SOURCE_KINDS), default="document"
            )
            source_command.add_argument("--title")
            source_command.add_argument("--origin-uri")
            source_command.add_argument(
                "--trust", choices=sorted(USER_SETTABLE_TRUST_LEVELS), default="user_provided"
            )
            source_command.add_argument(
                "--sensitivity", choices=sorted(SENSITIVITY_LEVELS), default="private"
            )
            source_command.add_argument(
                "--pdf-fallback",
                choices=("off", "vision-consensus", "document-engine"),
                default="off",
            )
            _add_typed_extraction_arguments(source_command)
            source_command.add_argument("--confirm-no-case-data", action="store_true")
        if name == "remove":
            source_command.add_argument("--reason", required=True)
            source_command.add_argument("--confirm", action="store_true")

    source_governance = source_commands.add_parser(
        "governance",
        help="Change source policy without changing immutable source identity",
    )
    source_governance.add_argument("--vault", type=Path, default=default_knowledge_vault())
    source_governance_target = source_governance.add_mutually_exclusive_group(required=True)
    source_governance_target.add_argument("--source-id")
    source_governance_target.add_argument(
        "--alias",
        help="Stable logical-path alias; defaults to its active version",
    )
    source_governance_selector = source_governance.add_mutually_exclusive_group()
    source_governance_selector.add_argument("--active", action="store_true")
    source_governance_selector.add_argument("--latest", action="store_true")
    source_governance.add_argument(
        "--trust", choices=sorted(USER_SETTABLE_TRUST_LEVELS), required=True
    )
    source_governance.add_argument(
        "--sensitivity", choices=sorted(SENSITIVITY_LEVELS), required=True
    )
    source_governance.add_argument("--allow-export", action="store_true")
    source_governance.add_argument("--reviewer-id", required=True)
    source_governance.add_argument("--reason", required=True)
    source_governance.add_argument("--confirm-reviewed", action="store_true")
    source_get = source_commands.add_parser(
        "get", help="Read one exact active Source Revision through Agent admission"
    )
    source_get.add_argument("--vault", type=Path, default=default_knowledge_vault())
    source_get.add_argument("--source-id", required=True)
    source_get.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    source_get.add_argument(
        "--max-sensitivity",
        choices=("public", "internal", "private"),
        default="private",
    )
    source_fragment = source_commands.add_parser(
        "fragment", help="Read one exact admitted evidence fragment"
    )
    source_fragment.add_argument("--vault", type=Path, default=default_knowledge_vault())
    source_fragment.add_argument("--fragment-id", required=True)
    source_fragment.add_argument("--offset", type=int, default=0)
    source_fragment.add_argument("--max-chars", type=int, default=12_000)
    source_fragment.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    source_fragment.add_argument(
        "--max-sensitivity",
        choices=("public", "internal", "private"),
        default="private",
    )

    structure = subcommands.add_parser(
        "structure",
        help="Inspect and search the verified Source IR hierarchy",
    )
    structure_commands = structure.add_subparsers(
        dest="structure_command",
        required=True,
    )
    structure_get = structure_commands.add_parser("get", help="Read one Source IR node")
    structure_get.add_argument("--vault", type=Path, default=default_knowledge_vault())
    structure_get.add_argument("--node-id", required=True)
    structure_get.add_argument("--max-chars", type=int, default=20_000)
    structure_list = structure_commands.add_parser(
        "list", help="List Source IR roots or direct children"
    )
    structure_list.add_argument("--vault", type=Path, default=default_knowledge_vault())
    structure_list.add_argument("--source-id")
    structure_list.add_argument("--compilation-id")
    structure_list.add_argument("--parent-node-id")
    structure_list.add_argument("--limit", type=int, default=100)
    structure_search = structure_commands.add_parser(
        "search", help="Search bounded Source IR candidates"
    )
    structure_search.add_argument("--vault", type=Path, default=default_knowledge_vault())
    structure_search.add_argument("--query", required=True)
    structure_search.add_argument("--source-id")
    structure_search.add_argument("--limit", type=int, default=20)
    structure_trace = structure_commands.add_parser(
        "trace", help="Trace one Source IR node to its hierarchy root"
    )
    structure_trace.add_argument("--vault", type=Path, default=default_knowledge_vault())
    structure_trace.add_argument("--node-id", required=True)

    propose = subcommands.add_parser(
        "propose",
        help="Create one manual knowledge proposal without activating it",
    )
    propose.add_argument("--vault", type=Path, default=default_knowledge_vault())
    propose.add_argument("--kind", choices=sorted(ASSET_KINDS), required=True)
    propose.add_argument("--memory-tier", choices=sorted(MEMORY_TIERS), required=True)
    propose.add_argument("--title", required=True)
    propose.add_argument("--statement", required=True)
    propose.add_argument("--semantic-key")
    propose.add_argument(
        "--trust",
        choices=sorted(USER_SETTABLE_TRUST_LEVELS),
        default="user_provided",
    )
    propose.add_argument(
        "--sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    propose.add_argument("--tag", action="append", default=[])
    propose.add_argument("--expires-at")
    propose.add_argument("--supersedes-asset-id")
    propose.add_argument("--origin-uri")
    propose.add_argument("--quarantine", action="store_true")
    propose.add_argument("--confirm-no-case-data", action="store_true")

    approve = subcommands.add_parser(
        "approve",
        help="Promote one reviewed proposal into Agent-visible active knowledge",
    )
    approve.add_argument("--vault", type=Path, default=default_knowledge_vault())
    approve.add_argument("--asset-id", required=True)
    approve.add_argument("--confirm-reviewed", action="store_true")
    approve.add_argument("--confirm-quarantine", action="store_true")

    approve_source = subcommands.add_parser(
        "approve-source",
        help=("Atomically activate every reviewed candidate from one exact compiled source"),
    )
    approve_source.add_argument(
        "--vault",
        type=Path,
        default=default_knowledge_vault(),
    )
    approve_source.add_argument("--source-id", required=True)
    approve_source.add_argument("--confirm-reviewed", action="store_true")
    approve_source.add_argument("--confirm-quarantine", action="store_true")
    approve_source.add_argument("--review-manifest-sha256", required=True)
    approve_source.add_argument("--reviewer-id", default="local-operator")
    approve_source.add_argument(
        "--review-reason",
        default="The exact compiled source manifest was reviewed.",
    )

    review = subcommands.add_parser(
        "review",
        help="Inspect proposals and record immutable local review receipts",
    )
    review_commands = review.add_subparsers(dest="review_command", required=True)
    review_queue = review_commands.add_parser("queue", help="List pending review candidates")
    review_queue.add_argument("--vault", type=Path, default=default_knowledge_vault())
    review_queue.add_argument("--source-id")
    review_queue.add_argument("--kind", choices=sorted(ASSET_KINDS))
    review_queue.add_argument("--status", choices=("proposed", "quarantined"))
    review_queue.add_argument("--limit", type=int, default=100)
    review_show = review_commands.add_parser("show", help="Show one exact proposal")
    review_show.add_argument("--vault", type=Path, default=default_knowledge_vault())
    review_show.add_argument("--asset-id", required=True)
    review_manifest = review_commands.add_parser(
        "manifest",
        help="Freeze the exact bounded membership commitment for a source review",
    )
    review_manifest.add_argument("--vault", type=Path, default=default_knowledge_vault())
    review_manifest.add_argument("--source-id", required=True)
    review_approve = review_commands.add_parser("approve", help="Approve one exact proposal")
    review_approve.add_argument("--vault", type=Path, default=default_knowledge_vault())
    review_approve.add_argument("--asset-id", required=True)
    review_approve.add_argument("--reviewer-id", required=True)
    review_approve.add_argument("--reason", required=True)
    review_approve.add_argument("--confirm-reviewed", action="store_true")
    review_approve.add_argument("--confirm-quarantine", action="store_true")
    review_reject = review_commands.add_parser("reject", help="Reject one exact proposal")
    review_reject.add_argument("--vault", type=Path, default=default_knowledge_vault())
    review_reject.add_argument("--asset-id", required=True)
    review_reject.add_argument("--reviewer-id", required=True)
    review_reject.add_argument("--reason", required=True)
    review_reject.add_argument("--confirm-reviewed", action="store_true")
    review_source = review_commands.add_parser(
        "approve-source",
        help="Approve an exact source only when its reviewed manifest still matches",
    )
    review_source.add_argument("--vault", type=Path, default=default_knowledge_vault())
    review_source.add_argument("--source-id", required=True)
    review_source.add_argument("--review-manifest-sha256", required=True)
    review_source.add_argument("--reviewer-id", required=True)
    review_source.add_argument("--reason", required=True)
    review_source.add_argument("--confirm-reviewed", action="store_true")
    review_source.add_argument("--confirm-quarantine", action="store_true")
    review_verify = review_commands.add_parser(
        "verify-receipt",
        help="Verify one immutable review receipt",
    )
    review_verify.add_argument("--vault", type=Path, default=default_knowledge_vault())
    review_verify.add_argument("--review-receipt-id", required=True)

    migrate = subcommands.add_parser(
        "migrate",
        help="Plan, apply, verify, or roll back the additive knowledge control schema",
    )
    migrate.add_argument("--vault", type=Path, default=default_knowledge_vault())
    migrate_action = migrate.add_mutually_exclusive_group()
    migrate_action.add_argument("--apply", action="store_true")
    migrate_action.add_argument("--verify", action="store_true")
    migrate_action.add_argument("--rollback", action="store_true")
    migrate.add_argument(
        "--backup",
        type=Path,
        help="Explicit backup output for --apply or verified backup input for --verify/--rollback",
    )
    migrate.add_argument("--confirm-rollback", action="store_true")

    revoke = subcommands.add_parser("revoke", help="Revoke one knowledge asset")
    revoke.add_argument("--vault", type=Path, default=default_knowledge_vault())
    revoke.add_argument("--asset-id", required=True)
    revoke.add_argument("--reason", required=True)
    revoke.add_argument("--confirm", action="store_true")

    forget = subcommands.add_parser(
        "forget",
        help="Selectively revoke current knowledge while retaining verifiable history",
    )
    forget.add_argument("--vault", type=Path, default=default_knowledge_vault())
    forget_target = forget.add_mutually_exclusive_group(required=True)
    forget_target.add_argument("--knowledge-key")
    forget_target.add_argument("--asset-id")
    forget.add_argument("--reason", required=True)
    forget.add_argument("--confirm", action="store_true")

    relate = subcommands.add_parser(
        "relate",
        help="Add one explicit human-reviewed relation between active assets",
    )
    relate.add_argument("--vault", type=Path, default=default_knowledge_vault())
    relate.add_argument("--subject-asset-id", required=True)
    relate.add_argument("--predicate", choices=sorted(RELATION_PREDICATES), required=True)
    relate.add_argument("--object-asset-id", required=True)
    relate.add_argument("--evidence-fragment-id")
    relate.add_argument("--event-time")
    relate.add_argument("--valid-from")
    relate.add_argument("--valid-to")
    relate.add_argument("--confirm-reviewed", action="store_true")

    relation = subcommands.add_parser(
        "relation",
        help="Inspect or revise the embedded reviewed temporal graph",
    )
    relation_commands = relation.add_subparsers(dest="relation_command", required=True)
    relation_list = relation_commands.add_parser("list")
    relation_list.add_argument("--vault", type=Path, default=default_knowledge_vault())
    relation_list.add_argument("--mode", choices=("current", "past", "as-of"), default="current")
    relation_list.add_argument("--as-of")
    relation_list.add_argument("--limit", type=int, default=100)
    relation_revise = relation_commands.add_parser("revise")
    relation_revise.add_argument("--vault", type=Path, default=default_knowledge_vault())
    relation_revise.add_argument("--relation-key", required=True)
    relation_revise.add_argument(
        "--status",
        choices=("active", "superseded", "revoked", "ambiguous"),
        required=True,
    )
    relation_revise.add_argument("--evidence-fragment-id", required=True)
    relation_revise.add_argument("--event-time")
    relation_revise.add_argument("--valid-from")
    relation_revise.add_argument("--valid-to")
    relation_revise.add_argument("--confirm-reviewed", action="store_true")
    relation_carry_forward = relation_commands.add_parser(
        "carry-forward",
        help="Plan or create review-gated relation successor candidates",
    )
    relation_carry_forward.add_argument("--vault", type=Path, default=default_knowledge_vault())
    relation_carry_forward.add_argument("--apply", action="store_true")
    relation_carry_forward.add_argument("--limit", type=int, default=100)
    relation_candidates = relation_commands.add_parser(
        "candidates",
        help="List pending relation carry-forward candidates",
    )
    relation_candidates.add_argument("--vault", type=Path, default=default_knowledge_vault())
    relation_candidates.add_argument("--limit", type=int, default=100)
    relation_review = relation_commands.add_parser(
        "review-candidate",
        help="Approve or reject one review-gated relation successor",
    )
    relation_review.add_argument("--vault", type=Path, default=default_knowledge_vault())
    relation_review.add_argument("--relation-revision-id", required=True)
    relation_review.add_argument("--decision", choices=("approve", "reject"), required=True)
    relation_review.add_argument("--confirm-reviewed", action="store_true")
    relation_review.add_argument("--reviewer-id", default="local-operator")
    relation_review.add_argument(
        "--reason", default="Reviewed the relation carry-forward candidate."
    )

    lineage = subcommands.add_parser(
        "lineage",
        help="Inspect or explicitly review stable Knowledge Lineage mappings",
    )
    lineage.add_argument("--vault", type=Path, default=default_knowledge_vault())
    lineage_target = lineage.add_mutually_exclusive_group()
    lineage_target.add_argument("--knowledge-key")
    lineage_target.add_argument("--asset-id")
    lineage.add_argument("--map-status", choices=("split", "merged", "ambiguous"))
    lineage.add_argument("--from-asset-id", action="append", default=[])
    lineage.add_argument("--to-asset-id", action="append", default=[])
    lineage.add_argument("--reviewer-id", default="local-operator")
    lineage.add_argument("--reason")
    lineage.add_argument("--confirm-reviewed", action="store_true")

    search = subcommands.add_parser("search", help="Search reviewed active Knowledge Assets")
    search.add_argument("--vault", type=Path, default=default_knowledge_vault())
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--max-chars", type=int, default=5_000)
    search.add_argument("--kind", action="append", default=[])
    search.add_argument("--memory-tier", action="append", default=[])
    search.add_argument("--include-restricted", action="store_true")
    search.add_argument("--include-inactive", action="store_true")

    recall_command = subcommands.add_parser(
        "recall",
        help="Plan multi-channel retrieval and compile one verified Knowledge Capsule",
    )
    recall_command.add_argument("--vault", type=Path, default=default_knowledge_vault())
    recall_command.add_argument("--query", required=True)
    recall_command.add_argument("--goal")
    recall_command.add_argument("--mode", choices=sorted(RETRIEVAL_MODES), default="auto")
    recall_command.add_argument("--max-items", type=int, default=8)
    recall_command.add_argument("--max-chars", type=int, default=6_000)
    recall_command.add_argument("--max-tokens", type=int, default=4_096)
    recall_command.add_argument("--kind", action="append", default=[])
    recall_command.add_argument("--memory-tier", action="append", default=[])
    recall_command.add_argument("--as-of")
    recall_command.add_argument("--discovery-index", type=Path)
    recall_command.add_argument("--model-root", type=Path)
    recall_command.add_argument("--reranker-manifest", type=Path)
    recall_command.add_argument("--threads", type=int)
    recall_command.add_argument("--include-restricted", action="store_true")
    recall_command.add_argument("--confirm-no-case-data", action="store_true")
    recall_command.add_argument("--output", type=Path)

    diagnose_retrieval = subcommands.add_parser(
        "diagnose-retrieval",
        help="Show Query Plan, every candidate channel, exclusions, gaps, and Capsule",
    )
    diagnose_retrieval.add_argument("--vault", type=Path, default=default_knowledge_vault())
    diagnose_retrieval.add_argument("--query", required=True)
    diagnose_retrieval.add_argument("--goal")
    diagnose_retrieval.add_argument("--mode", choices=sorted(RETRIEVAL_MODES), default="auto")
    diagnose_retrieval.add_argument("--max-items", type=int, default=8)
    diagnose_retrieval.add_argument("--max-chars", type=int, default=6_000)
    diagnose_retrieval.add_argument("--max-tokens", type=int, default=4_096)
    diagnose_retrieval.add_argument("--kind", action="append", default=[])
    diagnose_retrieval.add_argument("--memory-tier", action="append", default=[])
    diagnose_retrieval.add_argument("--as-of")
    diagnose_retrieval.add_argument("--discovery-index", type=Path)
    diagnose_retrieval.add_argument("--model-root", type=Path)
    diagnose_retrieval.add_argument("--reranker-manifest", type=Path)
    diagnose_retrieval.add_argument("--threads", type=int)
    diagnose_retrieval.add_argument("--include-restricted", action="store_true")
    diagnose_retrieval.add_argument("--confirm-no-case-data", action="store_true")

    explain_retrieval = subcommands.add_parser(
        "explain",
        help="Explain a retrieval query or the last local retrieval trace",
    )
    explain_retrieval.add_argument("--vault", type=Path, default=default_knowledge_vault())
    explain_target = explain_retrieval.add_mutually_exclusive_group(required=True)
    explain_target.add_argument("--query")
    explain_target.add_argument("--last", action="store_true")
    explain_retrieval.add_argument("--mode", choices=sorted(RETRIEVAL_MODES), default="auto")
    explain_retrieval.add_argument("--limit", type=int, default=5)
    explain_retrieval.add_argument("--max-chars", type=int, default=5_000)
    explain_retrieval.add_argument("--as-of")
    explain_retrieval.add_argument("--discovery-index", type=Path)
    explain_retrieval.add_argument("--model-root", type=Path)
    explain_retrieval.add_argument("--reranker-manifest", type=Path)
    explain_retrieval.add_argument("--threads", type=int)
    explain_retrieval.add_argument("--confirm-no-case-data", action="store_true")

    compare = subcommands.add_parser(
        "compare-retrieval",
        help="Replay one query through bounded retrieval modes without making a benchmark claim",
    )
    compare.add_argument("--vault", type=Path, default=default_knowledge_vault())
    compare.add_argument("--query", required=True)
    compare.add_argument(
        "--mode",
        action="append",
        choices=sorted(RETRIEVAL_MODES),
        default=[],
    )
    compare.add_argument("--limit", type=int, default=5)
    compare.add_argument("--max-chars", type=int, default=5_000)
    compare.add_argument("--as-of")
    compare.add_argument("--discovery-index", type=Path)
    compare.add_argument("--reranker-manifest", type=Path)
    compare.add_argument("--confirm-no-case-data", action="store_true")

    retrieval_profile = subcommands.add_parser(
        "retrieval-profile",
        help="Train, gate, activate, and roll back local ranking-only profiles",
    )
    profile_commands = retrieval_profile.add_subparsers(dest="profile_command", required=True)
    profile_train = profile_commands.add_parser("train")
    profile_train.add_argument("--vault", type=Path, default=default_knowledge_vault())
    profile_train.add_argument("--feedback-id", action="append", required=True)
    profile_evaluate = profile_commands.add_parser("evaluate")
    profile_evaluate.add_argument("--vault", type=Path, default=default_knowledge_vault())
    profile_evaluate.add_argument("--profile-id", required=True)
    profile_evaluate.add_argument("--suite", type=Path, required=True)
    profile_activate = profile_commands.add_parser("activate")
    profile_activate.add_argument("--vault", type=Path, default=default_knowledge_vault())
    profile_activate.add_argument("--profile-id", required=True)
    profile_activate.add_argument("--evaluation", type=Path, required=True)
    for name in ("rollback", "status"):
        profile_command = profile_commands.add_parser(name)
        profile_command.add_argument("--vault", type=Path, default=default_knowledge_vault())

    get = subcommands.add_parser("get", help="Read one exact Knowledge Asset")
    get.add_argument("--vault", type=Path, default=default_knowledge_vault())
    get.add_argument("--asset-id", required=True)
    get.add_argument("--include-inactive", action="store_true")

    context = subcommands.add_parser(
        "context",
        help="Compile a bounded, verifiable Knowledge Capsule for one task",
    )
    context.add_argument("--vault", type=Path, default=default_knowledge_vault())
    context.add_argument("--task", required=True)
    context.add_argument("--goal")
    context.add_argument("--max-items", type=int, default=8)
    context.add_argument("--max-chars", type=int, default=6_000)
    context.add_argument("--kind", action="append", default=[])
    context.add_argument("--memory-tier", action="append", default=[])
    context.add_argument("--include-restricted", action="store_true")
    context.add_argument("--confirm-no-case-data", action="store_true")
    context.add_argument("--output", type=Path)

    verify = subcommands.add_parser("verify", help="Verify an asset and its audit/source chain")
    verify.add_argument("--vault", type=Path, default=default_knowledge_vault())
    verify.add_argument("--asset-id", required=True)

    verify_capsule = subcommands.add_parser(
        "verify-capsule",
        help="Verify a portable Knowledge Capsule and optional current-vault bindings",
    )
    verify_capsule.add_argument("--capsule", type=Path, required=True)
    verify_capsule.add_argument("--vault", type=Path)

    inspect = subcommands.add_parser(
        "inspect",
        help="Inspect readiness, review backlog, expiry, and audit integrity",
    )
    inspect.add_argument("--vault", type=Path, default=default_knowledge_vault())

    doctor = subcommands.add_parser(
        "doctor",
        help="Diagnose canonical integrity, jobs, inbox, Source IR, and native permissions",
    )
    doctor.add_argument("--vault", type=Path, default=default_knowledge_vault())
    doctor.add_argument("--permissions", action="store_true")
    doctor.add_argument("--repair-derived", action="store_true")

    rebuild_indexes = subcommands.add_parser(
        "rebuild-indexes",
        help="Rebuild removable local retrieval indexes from canonical Assets",
    )
    rebuild_indexes.add_argument("--vault", type=Path, default=default_knowledge_vault())
    rebuild_indexes.add_argument("--confirm", action="store_true")

    discovery_model = subcommands.add_parser(
        "discovery-model",
        help="Explicitly provision or verify a pinned local discovery model",
    )
    discovery_model_commands = discovery_model.add_subparsers(
        dest="discovery_model_command",
        required=True,
    )
    for name, help_text in (
        ("setup", "Download and verify one fixed local discovery model"),
        ("status", "Verify one already installed discovery model"),
    ):
        model_command = discovery_model_commands.add_parser(name, help=help_text)
        model_command.add_argument(
            "--profile",
            choices=sorted(DISCOVERY_MODEL_PROFILES),
            required=True,
        )
        model_command.add_argument("--model-root", type=Path)
        if name == "setup":
            model_command.add_argument("--local-files-only", action="store_true")

    build_discovery = subcommands.add_parser(
        "build-discovery",
        help=("Build a source-bound derived discovery index without changing the canonical vault"),
    )
    build_discovery.add_argument(
        "--vault",
        type=Path,
        default=default_knowledge_vault(),
    )
    build_discovery.add_argument("--output", type=Path, required=True)
    build_discovery.add_argument(
        "--profile",
        choices=sorted(DISCOVERY_MODEL_PROFILES),
        required=True,
    )
    build_discovery.add_argument("--model-root", type=Path)
    build_discovery.add_argument("--threads", type=int)
    build_discovery.add_argument("--confirm-no-case-data", action="store_true")

    verify_discovery = subcommands.add_parser(
        "verify-discovery",
        help="Verify a derived discovery index and its exact current-vault binding",
    )
    verify_discovery.add_argument(
        "--vault",
        type=Path,
        default=default_knowledge_vault(),
    )
    verify_discovery.add_argument("--index", type=Path, required=True)

    search_discovery = subcommands.add_parser(
        "search-discovery",
        help=("Run explicit derived candidate discovery; results remain non-authoritative"),
    )
    search_discovery.add_argument(
        "--vault",
        type=Path,
        default=default_knowledge_vault(),
    )
    search_discovery.add_argument("--index", type=Path, required=True)
    search_discovery.add_argument("--query", required=True)
    search_discovery.add_argument("--limit", type=int, default=5)
    search_discovery.add_argument("--max-chars", type=int, default=1_500)
    search_discovery.add_argument("--model-root", type=Path)
    search_discovery.add_argument("--threads", type=int)

    export = subcommands.add_parser(
        "export",
        help="Export active assets as a content-verifiable, unsigned portable package",
    )
    export.add_argument("--vault", type=Path, default=default_knowledge_vault())
    export.add_argument("--output", type=Path, required=True)
    export.add_argument(
        "--max-sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="public",
    )
    export.add_argument("--include-evidence-text", action="store_true")
    export.add_argument("--include-source-files", action="store_true")
    export.add_argument("--confirm-export-source-files", action="store_true")

    verify_package = subcommands.add_parser(
        "verify-package",
        help="Verify portable package structure and payload hashes",
    )
    verify_package.add_argument("--package", type=Path, required=True)

    export_markdown = subcommands.add_parser(
        "export-markdown",
        help="Export deterministic human-readable views for Obsidian or review",
    )
    export_markdown.add_argument("--vault", type=Path, default=default_knowledge_vault())
    export_markdown.add_argument("--output", type=Path, required=True)
    export_markdown.add_argument(
        "--max-sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    export_markdown.add_argument("--replace", action="store_true")

    import_package = subcommands.add_parser(
        "import-package",
        help="Import portable assets into quarantine without laundering source trust",
    )
    import_package.add_argument("--vault", type=Path, default=default_knowledge_vault())
    import_package.add_argument("--package", type=Path, required=True)
    import_package.add_argument("--confirm-untrusted", action="store_true")

    debug = subcommands.add_parser(
        "debug",
        help="Record a failure lesson as a review-gated Experience Memory proposal",
    )
    debug.add_argument("--vault", type=Path, default=default_knowledge_vault())
    debug.add_argument("--question", required=True)
    debug.add_argument("--cause", required=True)
    debug.add_argument("--fix", required=True)
    debug.add_argument("--prevention", required=True)
    debug.add_argument(
        "--sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    debug.add_argument("--confirm-no-case-data", action="store_true")

    feedback = subcommands.add_parser(
        "feedback",
        help="Record a Capsule outcome as a review-gated learning proposal",
    )
    feedback.add_argument(
        "feedback_action",
        nargs="?",
        choices=("record", "list", "show", "verify", "replay", "promote-eval"),
    )
    feedback.add_argument("--vault", type=Path, default=default_knowledge_vault())
    feedback.add_argument("--capsule", type=Path)
    feedback.add_argument(
        "--outcome",
        choices=("success", "partial", "failure"),
    )
    feedback.add_argument("--observation")
    feedback.add_argument("--lesson")
    feedback.add_argument("--next-action")
    feedback.add_argument("--run-id")
    feedback.add_argument("--feedback-id")
    feedback.add_argument("--helpful-asset-id", action="append", default=[])
    feedback.add_argument("--irrelevant-asset-id", action="append", default=[])
    feedback.add_argument("--harmful-asset-id", action="append", default=[])
    feedback.add_argument("--stale-asset-id", action="append", default=[])
    feedback.add_argument("--missing-knowledge", action="append", default=[])
    feedback.add_argument("--missing-source", action="append", default=[])
    feedback.add_argument("--incorrect-relation", action="append", default=[])
    feedback.add_argument("--budget-failure", action="append", default=[])
    feedback.add_argument("--recommended-action")
    feedback.add_argument("--limit", type=int, default=100)
    feedback.add_argument("--output", type=Path)
    feedback.add_argument(
        "--sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    feedback.add_argument("--confirm-no-case-data", action="store_true")

    run_receipt = subcommands.add_parser(
        "run-receipt",
        help="Create and verify immutable Agent task run receipts",
    )
    run_commands = run_receipt.add_subparsers(dest="run_command", required=True)
    run_create = run_commands.add_parser("create", help="Create a receipt for a verified Capsule")
    run_create.add_argument("--vault", type=Path, default=default_knowledge_vault())
    run_create.add_argument("--capsule", type=Path, required=True)
    run_create.add_argument(
        "--status",
        choices=("success", "partial", "failure", "refusal", "timeout"),
        required=True,
    )
    run_create.add_argument("--host-name", required=True)
    run_create.add_argument("--host-version", required=True)
    run_create.add_argument("--model-name")
    run_create.add_argument("--model-version")
    run_create.add_argument("--started-at")
    run_create.add_argument("--finished-at")
    run_create.add_argument("--outcome-artifact", type=Path)
    run_create.add_argument("--input-tokens", type=int)
    run_create.add_argument("--output-tokens", type=int)
    run_create.add_argument("--latency-ms", type=float)
    run_create.add_argument("--cost", type=float)
    run_create.add_argument("--currency")
    for name in ("list", "show", "verify"):
        run_command = run_commands.add_parser(name)
        run_command.add_argument("--vault", type=Path, default=default_knowledge_vault())
        if name != "list":
            run_command.add_argument("--run-id", required=True)
        else:
            run_command.add_argument("--limit", type=int, default=100)

    inbox = subcommands.add_parser(
        "inbox",
        help="Manage isolated Agent proposal, feedback, run, and evaluation artifacts",
    )
    inbox_commands = inbox.add_subparsers(dest="inbox_command", required=True)
    inbox_submit = inbox_commands.add_parser(
        "submit",
        help="Write one hash-bound artifact to the isolated local inbox",
    )
    inbox_submit.add_argument("--vault", type=Path, default=default_knowledge_vault())
    inbox_submit.add_argument(
        "--type",
        dest="artifact_type",
        choices=("proposal", "feedback", "run", "eval"),
        required=True,
    )
    inbox_submit.add_argument("--payload", type=Path, required=True)
    inbox_submit.add_argument("--producer-name", required=True)
    inbox_submit.add_argument("--producer-version", required=True)
    inbox_submit.add_argument("--priority-signal", action="append", default=[])
    inbox_submit.add_argument(
        "--sensitivity", choices=sorted(SENSITIVITY_LEVELS), default="private"
    )
    inbox_submit.add_argument("--confirm-no-case-data", action="store_true")
    inbox_list = inbox_commands.add_parser("list", help="List bounded inbox metadata")
    inbox_list.add_argument("--vault", type=Path, default=default_knowledge_vault())
    inbox_list.add_argument(
        "--state", choices=("pending", "processed", "rejected"), default="pending"
    )
    inbox_list.add_argument(
        "--type", dest="artifact_type", choices=("proposal", "feedback", "run", "eval")
    )
    inbox_list.add_argument("--limit", type=int, default=100)
    for name in ("show", "verify", "promote", "reject"):
        inbox_command = inbox_commands.add_parser(name)
        inbox_command.add_argument("--vault", type=Path, default=default_knowledge_vault())
        inbox_command.add_argument("--artifact-id", required=True)
        if name in {"promote", "reject"}:
            inbox_command.add_argument("--confirm-reviewed", action="store_true")

    jobs = subcommands.add_parser(
        "job",
        help="Inspect, resume, retry, or cancel resumable ingest jobs",
    )
    job_commands = jobs.add_subparsers(dest="job_command", required=True)
    job_list = job_commands.add_parser("list")
    job_list.add_argument("--vault", type=Path, default=default_knowledge_vault())
    job_list.add_argument("--limit", type=int, default=100)
    for name in ("show", "resume", "retry", "cancel"):
        job_command = job_commands.add_parser(name)
        job_command.add_argument("--vault", type=Path, default=default_knowledge_vault())
        job_command.add_argument("--job-id", required=True)

    snapshot = subcommands.add_parser(
        "snapshot",
        help="Create, verify, or atomically restore a local vault snapshot",
    )
    snapshot_commands = snapshot.add_subparsers(
        dest="snapshot_command",
        required=True,
    )
    snapshot_create = snapshot_commands.add_parser("create")
    snapshot_create.add_argument("--vault", type=Path, default=default_knowledge_vault())
    snapshot_create.add_argument("--output", type=Path, required=True)
    snapshot_create.add_argument(
        "--include-operator-state",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    snapshot_verify = snapshot_commands.add_parser("verify")
    snapshot_verify.add_argument("--snapshot", type=Path, required=True)
    snapshot_verify.add_argument("--expected-vault-id")
    snapshot_restore = snapshot_commands.add_parser("restore")
    snapshot_restore.add_argument("--vault", type=Path, required=True)
    snapshot_restore.add_argument("--snapshot", type=Path, required=True)
    snapshot_restore.add_argument("--confirm", action="store_true")

    gc = subcommands.add_parser(
        "gc",
        help="Detect or remove bounded derived temporary orphan files",
    )
    gc.add_argument("--vault", type=Path, default=default_knowledge_vault())
    gc.add_argument("--orphans", action="store_true")
    gc.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    gc.add_argument("--confirm", action="store_true")

    projection = subcommands.add_parser(
        "projection",
        help="Export, diff, or turn Human Projection edits into review proposals",
    )
    projection_commands = projection.add_subparsers(
        dest="projection_command",
        required=True,
    )
    projection_export = projection_commands.add_parser("export")
    projection_export.add_argument("--vault", type=Path, default=default_knowledge_vault())
    projection_export.add_argument("--output", type=Path, required=True)
    projection_export.add_argument(
        "--max-sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    projection_export.add_argument("--replace", action="store_true")
    for name in ("diff", "propose"):
        projection_command = projection_commands.add_parser(name)
        projection_command.add_argument("--vault", type=Path, default=default_knowledge_vault())
        projection_command.add_argument("--projection", type=Path, required=True)
        if name == "propose":
            projection_command.add_argument("--confirm-no-case-data", action="store_true")

    skill = subcommands.add_parser(
        "skill",
        help="Build, verify, install, or update revision-bound read-only Agent Skills",
    )
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_build = skill_commands.add_parser("build")
    skill_build.add_argument("--vault", type=Path, default=default_knowledge_vault())
    skill_build.add_argument("--output", type=Path, required=True)
    skill_build.add_argument("--name", required=True)
    skill_build.add_argument("--description", required=True)
    skill_build.add_argument("--knowledge-key", action="append", default=[])
    skill_build.add_argument("--asset-id", action="append", default=[])
    skill_build.add_argument("--target", choices=sorted(SKILL_TARGETS), action="append", default=[])
    skill_build.add_argument(
        "--max-sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    skill_build.add_argument("--max-items", type=int, default=20)
    skill_build.add_argument("--max-chars", type=int, default=40_000)
    skill_build.add_argument("--max-tokens", type=int, default=10_000)
    skill_build.add_argument("--replace", action="store_true")
    skill_verify = skill_commands.add_parser("verify")
    skill_verify.add_argument("--bundle", type=Path, required=True)
    skill_verify.add_argument("--vault", type=Path)
    for name in ("install", "update"):
        skill_command = skill_commands.add_parser(name)
        skill_command.add_argument("--bundle", type=Path, required=True)
        skill_command.add_argument("--install-root", type=Path, required=True)
        skill_command.add_argument("--target", choices=sorted(SKILL_TARGETS), required=True)
        skill_command.add_argument("--vault", type=Path)
        skill_command.add_argument("--expected-vault-id")
        skill_command.add_argument("--trust-external", action="store_true")
        skill_command.add_argument("--confirm", action="store_true")

    workbench = subcommands.add_parser(
        "workbench",
        help="Open the local Operator Workbench or print its bounded snapshot",
    )
    workbench.add_argument("--vault", type=Path, default=default_knowledge_vault())

    autonomy = subcommands.add_parser(
        "autonomy",
        help="Migrate, verify, reconcile, retrieve, and rebuild the autonomous Markdown core",
    )
    autonomy_commands = autonomy.add_subparsers(dest="autonomy_command", required=True)
    for name in ("status", "inspect", "verify", "recover", "lint", "rebuild"):
        autonomy_command = autonomy_commands.add_parser(name)
        autonomy_command.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_migrate = autonomy_commands.add_parser(
        "migrate",
        help="Create a verified rollback point and install the additive autonomous core",
    )
    autonomy_migrate.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_migrate.add_argument("--backup", type=Path)
    autonomy_rollback = autonomy_commands.add_parser(
        "rollback",
        help="Atomically restore the verified pre-autonomy Vault backup",
    )
    autonomy_rollback.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_rollback.add_argument("--backup", type=Path, required=True)
    autonomy_rollback.add_argument("--confirm", action="store_true")
    autonomy_reconcile = autonomy_commands.add_parser(
        "reconcile",
        help="Commit safe external Markdown edits and preserve conflicts explicitly",
    )
    autonomy_reconcile.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_reconcile.add_argument("--grant-id", required=True)
    autonomy_reconcile.add_argument("--confirm-no-case-data", action="store_true")
    autonomy_watch = autonomy_commands.add_parser(
        "watch",
        help="Poll the bounded Markdown workspace and reconcile changes through the coordinator",
    )
    autonomy_watch.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_watch.add_argument("--grant-id", required=True)
    autonomy_watch.add_argument("--confirm-no-case-data", action="store_true")
    autonomy_watch.add_argument("--interval", type=float, default=2.0)
    autonomy_watch.add_argument("--max-cycles", type=int)
    autonomy_recall = autonomy_commands.add_parser("recall")
    autonomy_recall.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_recall.add_argument("--query", required=True)
    autonomy_recall.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    autonomy_recall.add_argument(
        "--max-sensitivity",
        choices=sorted(AUTONOMOUS_SENSITIVITIES),
        default="private",
    )
    autonomy_recall.add_argument(
        "--kind",
        choices=sorted(KNOWLEDGE_KINDS),
        action="append",
        default=[],
    )
    autonomy_recall.add_argument("--limit", type=int, default=5)
    autonomy_recall.add_argument("--max-chars", type=int, default=5_000)
    autonomy_recall.add_argument("--max-tokens", type=int, default=4_000)
    autonomy_recall.add_argument("--max-sources", type=int, default=8)
    autonomy_recall.add_argument("--graph-hops", type=int, choices=(0, 1, 2), default=1)
    autonomy_recall.add_argument(
        "--retrieval-mode",
        choices=("exact", "lexical", "dense", "graph", "hybrid"),
        default="hybrid",
    )
    autonomy_recall.add_argument("--as-of")
    autonomy_explain = autonomy_commands.add_parser(
        "explain",
        help="Explain recall discovery, admission, selection, budgets, and receipts",
    )
    autonomy_explain.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_explain.add_argument("--query", required=True)
    autonomy_explain.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    autonomy_explain.add_argument(
        "--max-sensitivity",
        choices=sorted(AUTONOMOUS_SENSITIVITIES),
        default="private",
    )
    autonomy_explain.add_argument(
        "--kind",
        choices=sorted(KNOWLEDGE_KINDS),
        action="append",
        default=[],
    )
    autonomy_explain.add_argument("--limit", type=int, default=5)
    autonomy_explain.add_argument("--max-chars", type=int, default=5_000)
    autonomy_explain.add_argument("--max-tokens", type=int, default=4_000)
    autonomy_explain.add_argument("--max-sources", type=int, default=8)
    autonomy_explain.add_argument("--graph-hops", type=int, choices=(0, 1, 2), default=1)
    autonomy_explain.add_argument(
        "--retrieval-mode",
        choices=("exact", "lexical", "dense", "graph", "hybrid"),
        default="hybrid",
    )
    autonomy_explain.add_argument("--as-of")
    autonomy_graph = autonomy_commands.add_parser(
        "graph",
        help="Read admitted temporal relation revisions and bounded endpoint metadata",
    )
    autonomy_graph.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_graph.add_argument("--knowledge-id")
    autonomy_graph.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    autonomy_graph.add_argument(
        "--max-sensitivity",
        choices=sorted(AUTONOMOUS_SENSITIVITIES),
        default="private",
    )
    autonomy_graph.add_argument("--limit", type=int, default=100)
    autonomy_graph.add_argument("--as-of")
    autonomy_conflicts = autonomy_commands.add_parser(
        "conflicts",
        help="List unresolved externally edited Markdown conflicts",
    )
    autonomy_conflicts.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_conflicts.add_argument("--limit", type=int, default=100)
    autonomy_context = autonomy_commands.add_parser("context")
    autonomy_context.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_context.add_argument("--task", required=True)
    autonomy_context.add_argument("--goal")
    autonomy_context.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    autonomy_context.add_argument(
        "--max-sensitivity",
        choices=sorted(AUTONOMOUS_SENSITIVITIES),
        default="private",
    )
    autonomy_context.add_argument("--limit", type=int, default=8)
    autonomy_context.add_argument("--max-chars", type=int, default=8_000)
    autonomy_context.add_argument("--max-tokens", type=int, default=6_000)
    autonomy_context.add_argument("--max-sources", type=int, default=12)
    autonomy_context.add_argument("--graph-hops", type=int, choices=(0, 1, 2), default=1)
    autonomy_context.add_argument(
        "--retrieval-mode",
        choices=("exact", "lexical", "dense", "graph", "hybrid"),
        default="hybrid",
    )
    autonomy_context.add_argument("--as-of")
    autonomy_context.add_argument("--confirm-no-case-data", action="store_true")
    autonomy_identity = autonomy_commands.add_parser(
        "identity",
        help="Resolve an exact semantic key or bounded Concept/Entity alias candidates",
    )
    autonomy_identity.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_identity.add_argument("--query", required=True)
    autonomy_identity.add_argument("--kind", choices=("concept", "entity"))
    autonomy_identity.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    autonomy_identity.add_argument(
        "--max-sensitivity",
        choices=sorted(AUTONOMOUS_SENSITIVITIES),
        default="private",
    )
    autonomy_identity.add_argument("--limit", type=int, default=10)
    autonomy_gaps = autonomy_commands.add_parser(
        "gaps",
        help="Report bounded missing evidence, orphan, conflict, and unresolved-link gaps",
    )
    autonomy_gaps.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_gaps.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    autonomy_gaps.add_argument(
        "--max-sensitivity",
        choices=sorted(AUTONOMOUS_SENSITIVITIES),
        default="private",
    )
    autonomy_gc = autonomy_commands.add_parser(
        "gc",
        help=(
            "Owner-only purge of forgotten Knowledge bytes and unreferenced CAS objects; "
            "governance and audit remain"
        ),
    )
    autonomy_gc.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_gc.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True)
    autonomy_gc.add_argument("--confirm", action="store_true")
    autonomy_gc.add_argument("--include-expired", action="store_true")
    autonomy_gc.add_argument("--max-objects", type=int, default=1_000)
    autonomy_gc.add_argument("--reason", default="owner-requested Knowledge Object forgetting")
    autonomy_skill_draft = autonomy_commands.add_parser(
        "skill-draft",
        help=("Compile explicitly checkable Procedure lines into a governed draft Skill revision"),
    )
    autonomy_skill_draft.add_argument("--vault", type=Path, default=default_knowledge_vault())
    autonomy_skill_draft.add_argument("--grant-id", required=True)
    autonomy_skill_draft.add_argument("--request", type=Path, required=True)
    for name in ("get", "history"):
        autonomy_read = autonomy_commands.add_parser(name)
        autonomy_read.add_argument("--vault", type=Path, default=default_knowledge_vault())
        autonomy_read.add_argument("--knowledge-id", required=True)
        if name == "get":
            autonomy_read.add_argument("--include-inactive", action="store_true")
            autonomy_read.add_argument(
                "--as-of",
                help="Read the canonical revision known at this transaction time",
            )

    compilation = subcommands.add_parser(
        "compile",
        help="Operate resumable Source-to-Knowledge Compilation Runs",
    )
    compilation_commands = compilation.add_subparsers(
        dest="compilation_command",
        required=True,
    )
    compilation_profile = compilation_commands.add_parser("profile")
    compilation_profile.add_argument("--vault", type=Path, default=default_knowledge_vault())
    compilation_profile.add_argument("--compiler-profile", default="living-wiki-agent")
    compilation_profile.add_argument("--compiler-profile-version", default="1")
    compilation_begin = compilation_commands.add_parser("begin")
    compilation_begin.add_argument("--vault", type=Path, default=default_knowledge_vault())
    compilation_begin.add_argument("--grant-id", required=True)
    compilation_begin.add_argument("--source-revision-id", required=True)
    compilation_begin.add_argument("--compiler-profile", default="living-wiki-agent")
    compilation_begin.add_argument("--compiler-profile-version", default="1")
    compilation_begin.add_argument("--host-identity", required=True)
    compilation_begin.add_argument("--model-identity")
    compilation_begin.add_argument("--prompt-template-id")
    compilation_begin.add_argument("--prompt-config-sha256")
    compilation_begin.add_argument("--plan-configuration-sha256")
    compilation_begin.add_argument("--packet-max-fragments", type=int, default=32)
    compilation_begin.add_argument("--confirm-no-case-data", action="store_true")
    for name in ("packet", "status", "explain"):
        compilation_read = compilation_commands.add_parser(name)
        compilation_read.add_argument("--vault", type=Path, default=default_knowledge_vault())
        compilation_read.add_argument("--run-id", required=True)
        if name == "packet":
            compilation_read.add_argument("--grant-id", required=True)
    compilation_stage = compilation_commands.add_parser("stage")
    compilation_stage.add_argument("--vault", type=Path, default=default_knowledge_vault())
    compilation_stage.add_argument("--grant-id", required=True)
    compilation_stage.add_argument("--run-id", required=True)
    compilation_stage.add_argument("--plan", type=Path, required=True)
    compilation_stage.add_argument("--confirm-no-case-data", action="store_true")
    for name in ("validate", "commit"):
        compilation_write = compilation_commands.add_parser(name)
        compilation_write.add_argument("--vault", type=Path, default=default_knowledge_vault())
        compilation_write.add_argument("--grant-id", required=True)
        compilation_write.add_argument("--run-id", required=True)
        compilation_write.add_argument("--confirm-no-case-data", action="store_true")
    compilation_resume = compilation_commands.add_parser("resume")
    compilation_resume.add_argument("--vault", type=Path, default=default_knowledge_vault())
    compilation_resume.add_argument("--grant-id", required=True)
    compilation_resume.add_argument("--run-id", required=True)
    compilation_resume.add_argument("--project", action="store_true")
    compilation_resume.add_argument("--confirm-no-case-data", action="store_true")
    compilation_abort = compilation_commands.add_parser("abort")
    compilation_abort.add_argument("--vault", type=Path, default=default_knowledge_vault())
    compilation_abort.add_argument("--grant-id", required=True)
    compilation_abort.add_argument("--run-id", required=True)
    compilation_abort.add_argument("--reason", required=True)
    compilation_abort.add_argument("--confirm-no-case-data", action="store_true")
    compilation_refresh = compilation_commands.add_parser("refresh")
    compilation_refresh.add_argument("--vault", type=Path, default=default_knowledge_vault())
    compilation_refresh.add_argument("--grant-id", required=True)
    compilation_refresh.add_argument("--source-revision-id", required=True)
    compilation_refresh.add_argument("--replacement-source-revision-id")
    compilation_refresh.add_argument("--confirm-no-case-data", action="store_true")

    semantic = subcommands.add_parser(
        "semantic",
        help="Operate the semantic observation and finalization protocol",
    )
    semantic_commands = semantic.add_subparsers(dest="semantic_command", required=True)
    for name in ("profile", "duties"):
        semantic_profile = semantic_commands.add_parser(name)
        semantic_profile.add_argument("--vault", type=Path, default=default_knowledge_vault())
        semantic_profile.add_argument("--compiler-profile", default="living-wiki-agent")
        semantic_profile.add_argument("--compiler-profile-version", default="2")
    for name in ("packet", "finalization", "status", "explain"):
        semantic_read = semantic_commands.add_parser(name)
        semantic_read.add_argument("--vault", type=Path, default=default_knowledge_vault())
        semantic_read.add_argument("--run-id", required=True)
        if name in {"packet", "finalization"}:
            semantic_read.add_argument("--grant-id", required=True)
    semantic_observe = semantic_commands.add_parser("observe")
    semantic_observe.add_argument("--vault", type=Path, default=default_knowledge_vault())
    semantic_observe.add_argument("--grant-id", required=True)
    semantic_observe.add_argument("--run-id", required=True)
    semantic_observe.add_argument("--plan", type=Path, required=True)
    semantic_observe.add_argument("--confirm-no-case-data", action="store_true")
    semantic_inventory = semantic_commands.add_parser("inventory")
    semantic_inventory.add_argument("--vault", type=Path, default=default_knowledge_vault())
    semantic_inventory.add_argument("--grant-id", required=True)
    semantic_inventory.add_argument("--run-id", required=True)
    semantic_inventory.add_argument("--confirm-no-case-data", action="store_true")
    semantic_finalize = semantic_commands.add_parser("finalize")
    semantic_finalize.add_argument("--vault", type=Path, default=default_knowledge_vault())
    semantic_finalize.add_argument("--grant-id", required=True)
    semantic_finalize.add_argument("--run-id", required=True)
    semantic_finalize.add_argument("--plan", type=Path, required=True)
    semantic_finalize.add_argument("--confirm-no-case-data", action="store_true")

    synthesis = subcommands.add_parser(
        "synthesis",
        help="Operate explicit revision-bound Synthesis refresh sagas",
    )
    synthesis_commands = synthesis.add_subparsers(
        dest="synthesis_command",
        required=True,
    )
    synthesis_list = synthesis_commands.add_parser("list-stale")
    synthesis_list.add_argument("--vault", type=Path, default=default_knowledge_vault())
    synthesis_list.add_argument("--limit", type=int, default=100)
    synthesis_coverage = synthesis_commands.add_parser("coverage")
    synthesis_coverage.add_argument("--vault", type=Path, default=default_knowledge_vault())
    synthesis_begin = synthesis_commands.add_parser("begin")
    synthesis_begin.add_argument("--vault", type=Path, default=default_knowledge_vault())
    synthesis_begin.add_argument("--grant-id", required=True)
    synthesis_begin.add_argument("--request", type=Path, required=True)
    synthesis_begin.add_argument("--confirm-no-case-data", action="store_true")
    for name in ("packet", "status", "explain"):
        synthesis_read = synthesis_commands.add_parser(name)
        synthesis_read.add_argument("--vault", type=Path, default=default_knowledge_vault())
        synthesis_read.add_argument("--refresh-run-id", required=True)
    synthesis_stage = synthesis_commands.add_parser("stage")
    synthesis_stage.add_argument("--vault", type=Path, default=default_knowledge_vault())
    synthesis_stage.add_argument("--grant-id", required=True)
    synthesis_stage.add_argument("--refresh-run-id", required=True)
    synthesis_stage.add_argument("--plan", type=Path, required=True)
    synthesis_stage.add_argument("--confirm-no-case-data", action="store_true")
    for name in ("validate", "commit"):
        synthesis_write = synthesis_commands.add_parser(name)
        synthesis_write.add_argument("--vault", type=Path, default=default_knowledge_vault())
        synthesis_write.add_argument("--grant-id", required=True)
        synthesis_write.add_argument("--refresh-run-id", required=True)
        synthesis_write.add_argument("--confirm-no-case-data", action="store_true")
    synthesis_resume = synthesis_commands.add_parser("resume")
    synthesis_resume.add_argument("--vault", type=Path, default=default_knowledge_vault())
    synthesis_resume.add_argument("--grant-id", required=True)
    synthesis_resume.add_argument("--refresh-run-id", required=True)
    synthesis_resume.add_argument("--project", action="store_true")
    synthesis_resume.add_argument("--confirm-no-case-data", action="store_true")
    synthesis_abort = synthesis_commands.add_parser("abort")
    synthesis_abort.add_argument("--vault", type=Path, default=default_knowledge_vault())
    synthesis_abort.add_argument("--grant-id", required=True)
    synthesis_abort.add_argument("--refresh-run-id", required=True)
    synthesis_abort.add_argument("--reason", required=True)
    synthesis_abort.add_argument("--confirm-no-case-data", action="store_true")

    wiki = subcommands.add_parser("wiki", help="Read governed Living Wiki projections")
    wiki_commands = wiki.add_subparsers(dest="wiki_command", required=True)
    for name in ("page", "backlinks", "outlinks"):
        wiki_page = wiki_commands.add_parser(name)
        wiki_page.add_argument("--vault", type=Path, default=default_knowledge_vault())
        wiki_page.add_argument("--wiki-path", required=True)
        wiki_page.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
        wiki_page.add_argument(
            "--max-sensitivity",
            choices=("public", "internal", "private"),
            default="private",
        )
        wiki_page.add_argument("--limit", type=int, default=20)
    wiki_graph = wiki_commands.add_parser("local-graph")
    wiki_graph.add_argument("--vault", type=Path, default=default_knowledge_vault())
    wiki_graph.add_argument("--knowledge-id", required=True)
    wiki_graph.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    wiki_graph.add_argument(
        "--max-sensitivity",
        choices=("public", "internal", "private"),
        default="private",
    )
    wiki_graph.add_argument("--limit", type=int, default=20)
    wiki_browse = wiki_commands.add_parser("browse-kind")
    wiki_browse.add_argument("--vault", type=Path, default=default_knowledge_vault())
    wiki_browse.add_argument("--kind", choices=sorted(KNOWLEDGE_KINDS), required=True)
    wiki_browse.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    wiki_browse.add_argument(
        "--max-sensitivity",
        choices=("public", "internal", "private"),
        default="private",
    )
    wiki_browse.add_argument("--limit", type=int, default=20)
    wiki_recent = wiki_commands.add_parser("recent")
    wiki_recent.add_argument("--vault", type=Path, default=default_knowledge_vault())
    wiki_recent.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    wiki_recent.add_argument(
        "--max-sensitivity",
        choices=("public", "internal", "private"),
        default="private",
    )
    wiki_recent.add_argument("--limit", type=int, default=20)

    editor = subcommands.add_parser("editor", help="Compile an ephemeral Editor Context Envelope")
    editor_commands = editor.add_subparsers(dest="editor_command", required=True)
    editor_context = editor_commands.add_parser("context")
    editor_context.add_argument("--vault", type=Path, default=default_knowledge_vault())
    editor_context.add_argument("--envelope", type=Path, required=True)

    purpose_query = subcommands.add_parser(
        "query",
        help="Run purpose-aware compiled-first or evidence-first retrieval",
    )
    purpose_query.add_argument("--vault", type=Path, default=default_knowledge_vault())
    purpose_query.add_argument("--query", required=True)
    purpose_query.add_argument(
        "--purpose",
        choices=(
            "answer",
            "verify",
            "quote",
            "historical",
            "legal",
            "debug",
            "freshness_check",
        ),
        default="answer",
    )
    purpose_query.add_argument(
        "--policy",
        choices=("compiled-first-v1", "evidence-first-v1", "balanced-v1"),
    )
    purpose_query.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    purpose_query.add_argument(
        "--max-sensitivity",
        choices=("public", "internal", "private"),
        default="private",
    )
    purpose_query.add_argument(
        "--kind", choices=sorted(KNOWLEDGE_KINDS), action="append", default=[]
    )
    purpose_query.add_argument("--limit", type=int, default=8)
    purpose_query.add_argument("--max-chars", type=int, default=8_000)
    purpose_query.add_argument("--max-tokens", type=int, default=6_000)
    purpose_query.add_argument("--max-sources", type=int, default=12)
    purpose_query.add_argument("--graph-hops", type=int, choices=(0, 1, 2), default=1)
    purpose_query.add_argument(
        "--retrieval-mode",
        choices=("exact", "lexical", "dense", "graph", "hybrid"),
        default="hybrid",
    )
    purpose_query.add_argument("--as-of")
    purpose_query.add_argument(
        "--query-plan-version",
        choices=("4", "5"),
        default="4",
    )

    backfill = subcommands.add_parser(
        "backfill",
        help="Propose, validate, and explicitly promote query-synthesis drafts",
    )
    backfill_commands = backfill.add_subparsers(
        dest="backfill_command",
        required=True,
    )
    backfill_propose = backfill_commands.add_parser("propose")
    backfill_propose.add_argument("--vault", type=Path, default=default_knowledge_vault())
    backfill_propose.add_argument("--grant-id", required=True)
    backfill_propose.add_argument("--request", type=Path, required=True)
    backfill_propose.add_argument("--confirm-no-case-data", action="store_true")
    backfill_validate = backfill_commands.add_parser("validate")
    backfill_validate.add_argument("--vault", type=Path, default=default_knowledge_vault())
    backfill_validate.add_argument("--grant-id", required=True)
    backfill_validate.add_argument("--draft-id", required=True)
    backfill_validate.add_argument("--confirm-no-case-data", action="store_true")
    backfill_promote = backfill_commands.add_parser("promote")
    backfill_promote.add_argument("--vault", type=Path, default=default_knowledge_vault())
    backfill_promote.add_argument("--grant-id", required=True)
    backfill_promote.add_argument("--draft-id", required=True)
    backfill_promote.add_argument("--idempotency-key", required=True)
    backfill_promote.add_argument(
        "--evaluator-type",
        choices=("user", "external_check", "owner_policy"),
        required=True,
    )
    backfill_promote.add_argument("--evaluator-id", required=True)
    backfill_promote.add_argument("--evaluation-reason", required=True)
    backfill_promote.add_argument("--confirm-no-case-data", action="store_true")
    backfill_status = backfill_commands.add_parser("status")
    backfill_status.add_argument("--vault", type=Path, default=default_knowledge_vault())
    backfill_status.add_argument("--draft-id", required=True)

    sink = subcommands.add_parser(
        "sink",
        help="Explicitly enable and use the separate scope-bound Agent mutation capability",
    )
    sink_commands = sink.add_subparsers(dest="sink_command", required=True)
    sink_enable = sink_commands.add_parser("enable")
    sink_enable.add_argument("--vault", type=Path, default=default_knowledge_vault())
    sink_enable.add_argument("--writer-id", required=True)
    sink_enable.add_argument("--scope", choices=sorted(AUTONOMOUS_SCOPES))
    sink_enable.add_argument(
        "--max-sensitivity",
        choices=sorted(AUTONOMOUS_SENSITIVITIES),
        default="private",
    )
    sink_enable.add_argument("--operation", choices=sorted(SINK_OPERATIONS), action="append")
    sink_enable.add_argument(
        "--feedback-evaluator-type",
        choices=sorted(FEEDBACK_EVALUATOR_TYPES),
        action="append",
        help=(
            "Grant evaluator identities allowed for record_feedback; defaults to "
            "agent_self_report only"
        ),
    )
    sink_enable.add_argument(
        "--profile",
        choices=("compiler", "semantic-compiler"),
        help=(
            "Use the least-privilege built-in operation set for Source Compilation Runs; "
            "cannot be combined with --operation"
        ),
    )
    sink_enable.add_argument("--max-request-bytes", type=int, default=65_536)
    sink_enable.add_argument("--max-mutations-per-minute", type=int, default=60)
    sink_enable.add_argument("--max-objects", type=int, default=100_000)
    sink_disable = sink_commands.add_parser("disable")
    sink_disable.add_argument("--vault", type=Path, default=default_knowledge_vault())
    sink_disable.add_argument("--grant-id", required=True)
    sink_status = sink_commands.add_parser(
        "status",
        help="Verify one enabled capability and show only non-secret grant metadata",
    )
    sink_status.add_argument("--vault", type=Path, default=default_knowledge_vault())
    sink_status.add_argument("--grant-id", required=True)
    sink_apply = sink_commands.add_parser(
        "apply",
        help="Apply one closed knowledge-sink.input/v2 JSON request",
    )
    sink_apply.add_argument("--vault", type=Path, default=default_knowledge_vault())
    sink_apply.add_argument("--grant-id", required=True)
    sink_apply.add_argument("--request", type=Path, required=True)
    sink_expire = sink_commands.add_parser("expire-due")
    sink_expire.add_argument("--vault", type=Path, default=default_knowledge_vault())
    sink_expire.add_argument("--grant-id", required=True)
    sink_expire.add_argument("--as-of")
    sink_expire.add_argument("--confirm-no-case-data", action="store_true")
    sink_mcp = sink_commands.add_parser(
        "mcp",
        help="Run the independently enabled write-capable Knowledge Sink MCP server",
    )
    sink_mcp.add_argument("--vault", type=Path, default=default_knowledge_vault())
    sink_mcp.add_argument("--grant-id", required=True)
    sink_mcp.add_argument("--transport", choices=("stdio",), default="stdio")
    sink_mcp.add_argument("--stdio", action="store_true")

    mcp = subcommands.add_parser(
        "mcp",
        help="Run the optional read-only Knowledge Asset MCP server",
    )
    mcp.add_argument("--vault", type=Path)
    mcp.add_argument("--transport", choices=("stdio",), default="stdio")
    mcp.add_argument("--stdio", action="store_true")


def _write_capsule(path: Path, capsule: dict[str, Any]) -> None:
    output = path.expanduser().absolute()
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise RuntimeError("capsule output must not be a symbolic link")
    if output.exists():
        raise FileExistsError("capsule output already exists; choose a new output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(capsule, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary = output.with_name(f".{output.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


_MAX_RETRIEVAL_TRACE_BYTES = 4 * 1024 * 1024


def _retrieval_trace_path(vault_root: Path) -> Path:
    derived = vault_root / "derived"
    retrieval = derived / "retrieval"
    for directory in (derived, retrieval):
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise RuntimeError("retrieval trace directory is unsafe")
        directory.mkdir(mode=0o700, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o700)
    return retrieval / "last-trace.json"


def _write_last_retrieval_trace(vault_root: Path, trace: dict[str, Any]) -> None:
    output = _retrieval_trace_path(vault_root)
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise RuntimeError("last retrieval trace path is unsafe")
    payload = (json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if len(payload) > _MAX_RETRIEVAL_TRACE_BYTES:
        raise ValueError("retrieval trace exceeds its local sidecar bound")
    temporary = output.with_name(f".{output.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
        if os.name != "nt":
            os.chmod(output, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_last_retrieval_trace(vault_root: Path) -> dict[str, Any]:
    path = vault_root / "derived" / "retrieval" / "last-trace.json"
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > _MAX_RETRIEVAL_TRACE_BYTES
        or (os.name != "nt" and path.stat().st_mode & 0o077)
    ):
        raise RuntimeError("last retrieval trace is unavailable or unsafe")
    try:
        payload = strict_json_loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("last retrieval trace is invalid") from error
    if not isinstance(payload, dict):
        raise RuntimeError("last retrieval trace is invalid")
    return payload


def _bounded_ingest_receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Keep CLI/tool output bounded while preserving the full compiler API."""
    asset_ids = result.get("asset_ids")
    if not isinstance(asset_ids, list):
        return result
    visible_ids = asset_ids[:100]
    return {
        **result,
        "asset_count": len(asset_ids),
        "asset_ids": visible_ids,
        "asset_ids_truncated": len(visible_ids) < len(asset_ids),
    }


def _source_key_for_alias(vault: KnowledgeVault, alias: str) -> str:
    logical_path = normalize_logical_path(alias)
    rows = vault.connection.execute(
        """
        SELECT DISTINCT source_revisions_v2.source_key
        FROM source_locations_v2
        JOIN source_revisions_v2 USING(source_revision_id)
        WHERE source_locations_v2.logical_path_folded = ?
        ORDER BY source_revisions_v2.source_key
        """,
        (logical_path.casefold(),),
    ).fetchall()
    if not rows:
        raise KeyError(f"knowledge source alias is unavailable: {logical_path}")
    if len(rows) != 1:
        raise RuntimeError(
            "knowledge source alias was reused by multiple identities; use an exact source key"
        )
    return str(rows[0]["source_key"])


def _source_versions_for_cli(
    vault: KnowledgeVault,
    *,
    source_key: str | None = None,
    alias: str | None = None,
) -> list[dict[str, Any]]:
    selected_key = _source_key_for_alias(vault, alias) if alias is not None else source_key
    sources = (
        list(vault.source_versions(selected_key))
        if selected_key is not None
        else list(vault.all_sources())
    )
    return sorted(
        sources,
        key=lambda item: (item["source_key"], item["imported_at"], item["source_id"]),
    )


def _latest_source_version(sources: list[dict[str, Any]]) -> dict[str, Any]:
    if not sources:
        raise KeyError("source identity has no versions")
    pending = [source for source in sources if source["status"] == "pending"]
    if len(pending) == 1:
        return pending[0]
    if len(pending) > 1:
        raise RuntimeError(
            "source identity has multiple pending successors; use an exact source ID"
        )
    active = [source for source in sources if source["status"] == "active"]
    if len(active) == 1:
        return active[0]
    if len(active) > 1:
        raise RuntimeError("source identity has multiple active versions")
    predecessor_ids = {
        source["previous_source_id"]
        for source in sources
        if source["previous_source_id"] is not None
    }
    heads = [source for source in sources if source["source_id"] not in predecessor_ids]
    if len(heads) != 1:
        raise RuntimeError("source identity history is ambiguous; use an exact source ID")
    return heads[0]


def _filter_source_selector(
    sources: list[dict[str, Any]],
    *,
    active: bool,
    latest: bool,
) -> list[dict[str, Any]]:
    if active:
        return [source for source in sources if source["status"] == "active"]
    if not latest:
        return sources
    versions_by_key: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        versions_by_key.setdefault(source["source_key"], []).append(source)
    return sorted(
        (_latest_source_version(versions) for versions in versions_by_key.values()),
        key=lambda item: (item["source_key"], item["source_id"]),
    )


def _resolve_source_id(
    vault: KnowledgeVault,
    *,
    source_id: str | None,
    alias: str | None,
    active: bool,
    latest: bool,
) -> str:
    if source_id is not None:
        if active or latest:
            raise ValueError("--active/--latest can only be used with --alias")
        return source_id
    if alias is None:
        raise ValueError("source selection requires --source-id or --alias")
    versions = _source_versions_for_cli(vault, alias=alias)
    if latest:
        return str(_latest_source_version(versions)["source_id"])
    active_versions = [source for source in versions if source["status"] == "active"]
    if len(active_versions) != 1:
        if not active_versions:
            raise KeyError("source alias has no active version; use --latest to inspect it")
        raise RuntimeError("source alias has multiple active versions")
    return str(active_versions[0]["source_id"])


def render_knowledge_result(value: Any, *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if output_format == "jsonl":
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if output_format != "human":
        raise ValueError(f"unsupported knowledge output format: {output_format}")

    def human_lines(candidate: Any, *, indent: int = 0) -> list[str]:
        prefix = " " * indent
        if isinstance(candidate, dict):
            lines: list[str] = []
            for key in sorted(candidate):
                nested = candidate[key]
                if isinstance(nested, (dict, list)):
                    lines.append(f"{prefix}{key}:")
                    lines.extend(human_lines(nested, indent=indent + 2))
                else:
                    rendered = json.dumps(nested, ensure_ascii=False)
                    lines.append(f"{prefix}{key}: {rendered}")
            return lines
        if isinstance(candidate, list):
            if not candidate:
                return [f"{prefix}[]"]
            lines = []
            for item in candidate:
                if isinstance(item, (dict, list)):
                    lines.append(f"{prefix}-")
                    lines.extend(human_lines(item, indent=indent + 2))
                else:
                    lines.append(f"{prefix}- {json.dumps(item, ensure_ascii=False)}")
            return lines
        return [f"{prefix}{json.dumps(candidate, ensure_ascii=False)}"]

    return "\n".join(human_lines(value))


def _read_bounded_json_object(path: Path, *, label: str, max_bytes: int) -> dict[str, Any]:
    selected = path.expanduser().absolute()
    if (
        selected.is_symlink()
        or not selected.is_file()
        or not 1 <= selected.stat().st_size <= max_bytes
    ):
        raise ValueError(f"{label} must be a regular bounded JSON file")
    value = strict_json_loads(selected.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return value


def handle_knowledge_command(args: argparse.Namespace) -> dict[str, Any] | None:
    command = args.knowledge_command
    if command == "init":
        legacy = initialize_knowledge_vault(
            args.vault,
            name=args.name,
            scope=cast(VaultScope, args.scope),
        )
        if args.legacy_review_core:
            return legacy
        autonomous = initialize_autonomous_core(
            args.vault,
            migration_source="new-vault",
        )
        return {
            "schema_version": "deeplaw.knowledge-vault-initialization/v2",
            "vault_id": legacy["vault_id"],
            "legacy_compatibility": legacy,
            "autonomous_core": autonomous,
            "active_write_policy": "agent_derived_autonomous",
        }
    if command == "compile":
        action = args.compilation_command
        knowledge_os = KnowledgeOS.open(args.vault)
        if action == "profile":
            return knowledge_os.compilations.profile(
                args.compiler_profile,
                args.compiler_profile_version,
            )
        if action == "begin":
            profile = knowledge_os.compilations.profile(
                args.compiler_profile,
                args.compiler_profile_version,
            )
            provided = {
                "prompt_template_id": args.prompt_template_id,
                "prompt_config_sha256": args.prompt_config_sha256,
                "plan_configuration_sha256": args.plan_configuration_sha256,
            }
            if any(
                value is not None and value != profile[field] for field, value in provided.items()
            ):
                raise ValueError(
                    "Compilation provenance differs from the registered compiler profile"
                )
            run = knowledge_os.compilations.begin(
                grant_id=args.grant_id,
                source_revision_id=args.source_revision_id,
                compiler_profile=args.compiler_profile,
                compiler_profile_version=args.compiler_profile_version,
                host_identity=args.host_identity,
                model_identity=args.model_identity,
                prompt_template_id=profile["prompt_template_id"],
                prompt_config_sha256=profile["prompt_config_sha256"],
                plan_configuration_sha256=profile["plan_configuration_sha256"],
                packet_max_fragments=args.packet_max_fragments,
                confirm_no_case_data=args.confirm_no_case_data,
            )
            return run.begin_receipt()
        if action == "status":
            return knowledge_os.compilations.status(args.run_id)
        if action == "explain":
            return knowledge_os.compilations.explain(args.run_id)
        if action == "refresh":
            return knowledge_os.compilations.refresh(
                grant_id=args.grant_id,
                source_revision_id=args.source_revision_id,
                replacement_source_revision_id=args.replacement_source_revision_id,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        run = knowledge_os.compilations.open(
            compilation_run_id=args.run_id,
            grant_id=args.grant_id,
        )
        if action == "packet":
            packet = run.next_packet()
            if packet is not None:
                return packet
            response = {
                "schema_version": "deeplaw.source-compilation-packet-end/v1",
                "compilation_run_id": args.run_id,
                "complete": True,
            }
            _validate_contract(
                "source-compilation-packet-end.v1.schema.json",
                response,
            )
            return response
        if action == "stage":
            plan = _read_bounded_json_object(
                args.plan,
                label="Compilation Plan",
                max_bytes=320 * 1024,
            )
            return run.stage(
                plan,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "validate":
            return run.validate(
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "commit":
            return run.commit(
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "resume":
            return run.resume(
                project=args.project,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "abort":
            return run.abort(
                reason=args.reason,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        raise ValueError(f"unsupported Compilation Run action: {action}")
    if command == "semantic":
        action = args.semantic_command
        knowledge_os = KnowledgeOS.open(args.vault)
        if action in {"profile", "duties"}:
            profile = knowledge_os.compilations.profile(
                args.compiler_profile,
                args.compiler_profile_version,
            )
            if action == "profile":
                return profile
            return {
                "schema_version": "deeplaw.semantic-compilation-duties/v1",
                "compiler_profile": profile["compiler_profile"],
                "compiler_profile_version": profile["compiler_profile_version"],
                "semantic_duties": profile["semantic_duties"],
                "write_performed": False,
            }
        if action == "status":
            return knowledge_os.compilations.status(args.run_id)
        if action == "explain":
            return knowledge_os.compilations.explain(args.run_id)
        run = knowledge_os.compilations.open(
            compilation_run_id=args.run_id,
            grant_id=args.grant_id,
        )
        if run.compiler_profile_version != "2":
            raise ValueError("semantic command requires compiler profile version 2")
        if action == "packet":
            packet = run.next_packet()
            return packet or {
                "schema_version": "deeplaw.semantic-observation-packet-end/v1",
                "compilation_run_id": args.run_id,
                "complete": True,
            }
        if action == "finalization":
            return run.finalization_packet()
        if action == "observe":
            plan = _read_bounded_json_object(
                args.plan,
                label="Semantic Observation Plan",
                max_bytes=320 * 1024,
            )
            return run.stage_observations(
                plan,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "inventory":
            return run.semantic_inventory(
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "finalize":
            plan = _read_bounded_json_object(
                args.plan,
                label="Semantic Publication Plan",
                max_bytes=320 * 1024,
            )
            return run.stage_publication(
                plan,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        raise ValueError(f"unsupported semantic compilation action: {action}")
    if command == "synthesis":
        action = args.synthesis_command
        syntheses = KnowledgeOS.open(args.vault).syntheses
        if action == "list-stale":
            if not 1 <= args.limit <= 1000:
                raise ValueError("Synthesis refresh list limit must be between 1 and 1000")
            return {
                "schema_version": "deeplaw.synthesis-refresh-task-list/v1",
                "tasks": syntheses.refresh_tasks(status="planned")[: args.limit],
                "write_performed": False,
            }
        if action == "coverage":
            return syntheses.refresh_coverage()
        if action == "begin":
            request = _read_bounded_json_object(
                args.request,
                label="Synthesis Refresh Begin Request",
                max_bytes=320 * 1024,
            )
            if {"grant_id", "confirm_no_case_data"}.intersection(request):
                raise ValueError(
                    "Synthesis refresh request cannot override CLI capability parameters"
                )
            return syntheses.begin_refresh(
                **request,
                grant_id=args.grant_id,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "packet":
            packet = syntheses.refresh_packet(args.refresh_run_id)
            return packet or {
                "schema_version": "deeplaw.synthesis-refresh-packet-end/v1",
                "synthesis_refresh_run_id": args.refresh_run_id,
                "complete": True,
            }
        if action == "status":
            return syntheses.refresh_status(args.refresh_run_id)
        if action == "explain":
            return syntheses.refresh_explain(args.refresh_run_id)
        common = {
            "grant_id": args.grant_id,
            "synthesis_refresh_run_id": args.refresh_run_id,
            "confirm_no_case_data": args.confirm_no_case_data,
        }
        if action == "stage":
            plan = _read_bounded_json_object(
                args.plan,
                label="Synthesis Refresh Plan",
                max_bytes=320 * 1024,
            )
            return syntheses.stage_refresh(**common, plan=plan)
        if action == "validate":
            return syntheses.validate_refresh(**common)
        if action == "commit":
            return syntheses.commit_refresh(**common)
        if action == "resume":
            return syntheses.resume_refresh(**common, project=args.project)
        if action == "abort":
            return syntheses.abort_refresh(**common, reason=args.reason)
        raise ValueError(f"unsupported Synthesis refresh action: {action}")
    if command == "query":
        return KnowledgeOS.open(args.vault).retrieval.query(
            args.query,
            purpose=args.purpose,
            policy=args.policy,
            scope=args.scope,
            max_sensitivity=args.max_sensitivity,
            limit=args.limit,
            max_chars=args.max_chars,
            max_tokens=args.max_tokens,
            max_sources=args.max_sources,
            graph_hops=args.graph_hops,
            retrieval_mode=args.retrieval_mode,
            as_of=args.as_of,
            kinds=tuple(args.kind),
            query_plan_version=args.query_plan_version,
        )
    if command == "backfill":
        action = args.backfill_command
        backfill_api = KnowledgeOS.open(args.vault).backfill
        if action == "propose":
            request = _read_bounded_json_object(
                args.request,
                label="query backfill request",
                max_bytes=320 * 1024,
            )
            if {"grant_id", "confirm_no_case_data"}.intersection(request):
                raise ValueError("query backfill request cannot override CLI capability parameters")
            return backfill_api.propose(
                **request,
                grant_id=args.grant_id,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "validate":
            return backfill_api.validate(
                grant_id=args.grant_id,
                draft_id=args.draft_id,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "promote":
            return backfill_api.promote(
                grant_id=args.grant_id,
                draft_id=args.draft_id,
                idempotency_key=args.idempotency_key,
                evaluator_type=args.evaluator_type,
                evaluator_id=args.evaluator_id,
                evaluation_reason=args.evaluation_reason,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if action == "status":
            return backfill_api.status(args.draft_id)
        raise ValueError(f"unsupported query backfill action: {action}")
    if command == "autonomy":
        action = args.autonomy_command
        if action == "status":
            installed = autonomous_core_installed(args.vault)
            result: dict[str, Any] = {
                "schema_version": "deeplaw.autonomous-status/v1",
                "installed": installed,
            }
            if installed:
                with AutonomousKnowledgeStore(args.vault, read_only=True) as store:
                    result.update(
                        {
                            "vault_id": store.vault_id,
                            "sequence": store.sequence,
                            "audit_head": store.audit_head,
                            "verification": store.verify(),
                        }
                    )
            return result
        if action == "migrate":
            return migrate_autonomous_core(args.vault, backup_output=args.backup)
        if action == "rollback":
            return rollback_autonomous_core(
                args.vault,
                backup=args.backup,
                confirm=args.confirm,
            )
        read_only = action in {
            "inspect",
            "verify",
            "lint",
            "recall",
            "explain",
            "graph",
            "conflicts",
            "context",
            "identity",
            "gaps",
            "get",
            "history",
        }
        with AutonomousKnowledgeStore(args.vault, read_only=read_only) as store:
            selected_scope = (
                args.scope if hasattr(args, "scope") and args.scope else store.vault_scope
            )
            agent_read_integrity = None
            if action in {
                "recall",
                "explain",
                "graph",
                "context",
                "identity",
                "gaps",
                "get",
                "history",
            }:
                agent_read_integrity = store.verify()
                if not agent_read_integrity["valid"]:
                    raise RuntimeError(
                        "autonomous knowledge canonical integrity is invalid; read stopped"
                    )
            if action == "inspect":
                return store.inspect()
            if action == "verify":
                return store.verify()
            if action == "recover":
                return store.recover()
            if action == "reconcile":
                return store.reconcile_workspace(
                    grant_id=args.grant_id,
                    confirm_no_case_data=args.confirm_no_case_data,
                )
            if action == "watch":
                if not 0.25 <= args.interval <= 3_600:
                    raise ValueError("watch interval must be between 0.25 and 3600 seconds")
                if args.max_cycles is not None and not 1 <= args.max_cycles <= 1_000_000:
                    raise ValueError("watch max cycles must be between 1 and 1000000")
                cycle_count = 0
                interrupted = False
                last: dict[str, Any] | None = None
                try:
                    while True:
                        last = store.reconcile_workspace(
                            grant_id=args.grant_id,
                            confirm_no_case_data=args.confirm_no_case_data,
                        )
                        pending_rebuilds = store.connection.execute(
                            "SELECT COUNT(*) FROM derived_rebuild_queue_v3 "
                            "WHERE completed_at IS NULL"
                        ).fetchone()[0]
                        if pending_rebuilds:
                            try:
                                rebuilt = store.rebuild_derived()
                                last["derived_maintenance"] = {
                                    "status": "rebuilt",
                                    "queued_before": pending_rebuilds,
                                    "knowledge_count": rebuilt["knowledge_count"],
                                    "relation_count": rebuilt["relation_count"],
                                    "input_audit_head": rebuilt["input_audit_head"],
                                }
                            except Exception as error:
                                last["derived_maintenance"] = {
                                    "status": "retry_pending",
                                    "queued_before": pending_rebuilds,
                                    "error_type": type(error).__name__,
                                }
                        else:
                            last["derived_maintenance"] = {
                                "status": "current",
                                "queued_before": 0,
                            }
                        cycle_count += 1
                        if args.max_cycles is not None and cycle_count >= args.max_cycles:
                            break
                        time.sleep(args.interval)
                except KeyboardInterrupt:
                    interrupted = True
                return {
                    "schema_version": "deeplaw.workspace-watch/v1",
                    "cycle_count": cycle_count,
                    "interrupted": interrupted,
                    "last": last,
                    "audit_head": store.audit_head,
                }
            if action == "lint":
                return store.semantic_lint()
            if action == "rebuild":
                return store.rebuild_derived()
            if action == "gc":
                return store.garbage_collect_content(
                    dry_run=args.dry_run,
                    confirm=args.confirm,
                    include_expired=args.include_expired,
                    max_objects=args.max_objects,
                    reason=args.reason,
                )
            if action == "skill-draft":
                request_path = args.request.expanduser().absolute()
                if (
                    request_path.is_symlink()
                    or not request_path.is_file()
                    or request_path.stat().st_size > 128 * 1024
                ):
                    raise ValueError("Skill Factory request file is missing, unsafe, or oversized")
                request = strict_json_loads(request_path.read_bytes())
                if not isinstance(request, dict):
                    raise ValueError("Skill Factory request must contain a JSON object")
                return store.create_skill_draft(
                    grant_id=args.grant_id,
                    request=request,
                )
            if action == "recall":
                return store.recall(
                    args.query,
                    scope=selected_scope,
                    max_sensitivity=args.max_sensitivity,
                    limit=args.limit,
                    max_chars=args.max_chars,
                    max_tokens=args.max_tokens,
                    max_sources=args.max_sources,
                    graph_hops=args.graph_hops,
                    retrieval_mode=args.retrieval_mode,
                    as_of=args.as_of,
                    kinds=tuple(args.kind),
                    force_canonical_lexical=bool(
                        agent_read_integrity and not agent_read_integrity["derived_ready"]
                    ),
                )
            if action == "explain":
                return store.explain_recall(
                    args.query,
                    scope=selected_scope,
                    max_sensitivity=args.max_sensitivity,
                    limit=args.limit,
                    max_chars=args.max_chars,
                    max_tokens=args.max_tokens,
                    max_sources=args.max_sources,
                    graph_hops=args.graph_hops,
                    retrieval_mode=args.retrieval_mode,
                    as_of=args.as_of,
                    kinds=tuple(args.kind),
                    force_canonical_lexical=bool(
                        agent_read_integrity and not agent_read_integrity["derived_ready"]
                    ),
                )
            if action == "graph":
                return store.graph(
                    knowledge_id=args.knowledge_id,
                    scope=selected_scope,
                    max_sensitivity=args.max_sensitivity,
                    limit=args.limit,
                    as_of=args.as_of,
                )
            if action == "conflicts":
                return store.list_conflicts(limit=args.limit)
            if action == "context":
                return store.build_capsule(
                    task=args.task,
                    goal=args.goal,
                    scope=selected_scope,
                    max_sensitivity=args.max_sensitivity,
                    limit=args.limit,
                    max_chars=args.max_chars,
                    max_tokens=args.max_tokens,
                    max_sources=args.max_sources,
                    graph_hops=args.graph_hops,
                    retrieval_mode=args.retrieval_mode,
                    as_of=args.as_of,
                    confirm_no_case_data=args.confirm_no_case_data,
                    force_canonical_lexical=bool(
                        agent_read_integrity and not agent_read_integrity["derived_ready"]
                    ),
                )
            if action == "identity":
                return store.lookup_identity(
                    args.query,
                    kind=args.kind,
                    scope=selected_scope,
                    max_sensitivity=args.max_sensitivity,
                    limit=args.limit,
                )
            if action == "gaps":
                return store.discover_gaps(
                    scope=selected_scope,
                    max_sensitivity=args.max_sensitivity,
                )
            if action == "get":
                if args.as_of is not None:
                    if args.include_inactive:
                        raise ValueError("--include-inactive cannot be combined with --as-of")
                    return store.get_at(args.knowledge_id, recorded_at=args.as_of)
                return store.get_current(
                    args.knowledge_id,
                    include_inactive=args.include_inactive,
                )
            if action == "history":
                return store.history(args.knowledge_id)
        raise ValueError(f"unsupported autonomous knowledge action: {action}")
    if command == "source" and args.source_command in {"get", "fragment"}:
        sources = KnowledgeOS.open(args.vault).sources
        options = {
            "scope": args.scope,
            "max_sensitivity": args.max_sensitivity,
        }
        if args.source_command == "get":
            return sources.get(args.source_id, **options)
        return sources.fragment(
            args.fragment_id,
            offset=args.offset,
            max_chars=args.max_chars,
            **options,
        )
    if command == "wiki":
        wiki_api = KnowledgeOS.open(args.vault).wiki
        action = args.wiki_command
        options = {
            "scope": args.scope,
            "max_sensitivity": args.max_sensitivity,
            "limit": args.limit,
        }
        if action == "page":
            return wiki_api.page(args.wiki_path, **options)
        if action == "backlinks":
            return wiki_api.backlinks(args.wiki_path, **options)
        if action == "outlinks":
            return wiki_api.outlinks(args.wiki_path, **options)
        if action == "local-graph":
            return wiki_api.local_graph(args.knowledge_id, **options)
        if action == "browse-kind":
            return wiki_api.browse_kind(args.kind, **options)
        if action == "recent":
            return wiki_api.recent_changes(**options)
        raise ValueError(f"unsupported Living Wiki action: {action}")
    if command == "editor":
        envelope = _read_bounded_json_object(
            args.envelope,
            label="Editor Context Envelope",
            max_bytes=128 * 1024,
        )
        return KnowledgeOS.open(args.vault).editor_context.compile(envelope)
    if command == "sink":
        action = args.sink_command
        if action == "mcp":
            from .knowledge_sink_mcp_server import run_knowledge_sink_mcp

            run_knowledge_sink_mcp(
                grant_id=args.grant_id,
                transport="stdio" if args.stdio else args.transport,
                vault_path=args.vault,
            )
            return None
        if action == "apply":
            request_path = args.request.expanduser().absolute()
            if (
                request_path.is_symlink()
                or not request_path.is_file()
                or request_path.stat().st_size > 320 * 1024
            ):
                raise ValueError("Knowledge Sink request file is missing, unsafe, or oversized")
            request = strict_json_loads(request_path.read_bytes())
            if not isinstance(request, dict):
                raise ValueError("Knowledge Sink request file must contain a JSON object")
            from .knowledge_sink_mcp_server import handle_knowledge_sink

            return handle_knowledge_sink(
                request,
                grant_id=args.grant_id,
                vault_path=args.vault,
            )
        with AutonomousKnowledgeStore(
            args.vault,
            read_only=action == "status",
        ) as store:
            if action == "enable":
                if args.profile is not None and args.operation:
                    raise ValueError("Knowledge Sink --profile cannot be combined with --operation")
                operations = (
                    SEMANTIC_COMPILER_GRANT_OPERATIONS
                    if args.profile == "semantic-compiler"
                    else (
                        COMPILER_GRANT_OPERATIONS
                        if args.profile == "compiler"
                        else tuple(args.operation or ("remember",))
                    )
                )
                return store.enable_grant(
                    writer_id=args.writer_id,
                    allowed_scope=args.scope or store.vault_scope,
                    max_sensitivity=args.max_sensitivity,
                    operations=operations,
                    evaluator_types=tuple(args.feedback_evaluator_type or ("agent_self_report",)),
                    max_request_bytes=args.max_request_bytes,
                    max_mutations_per_minute=args.max_mutations_per_minute,
                    max_objects=args.max_objects,
                )
            if action == "disable":
                return store.disable_grant(args.grant_id)
            if action == "status":
                return store.grant_status(args.grant_id)
            if action == "expire-due":
                return store.expire_due(
                    grant_id=args.grant_id,
                    as_of=args.as_of,
                    confirm_no_case_data=args.confirm_no_case_data,
                )
        raise ValueError(f"unsupported Knowledge Sink action: {action}")
    if command == "doctor":
        permission_report = knowledge_vault_permission_report(args.vault)
        if args.permissions:
            return permission_report
        return knowledge_doctor(args.vault, repair_derived=args.repair_derived)
    if command == "verify-package":
        return verify_knowledge_package(args.package)
    if command == "verify-capsule":
        if args.vault is None:
            return verify_capsule_file(args.capsule)
        with KnowledgeVault(args.vault, read_only=True) as vault:
            return verify_capsule_file(args.capsule, vault=vault)
    if command == "snapshot":
        if args.snapshot_command == "verify":
            return verify_knowledge_snapshot(
                args.snapshot,
                expected_vault_id=args.expected_vault_id,
            )
        if args.snapshot_command == "restore":
            return restore_knowledge_snapshot(
                args.vault,
                snapshot=args.snapshot,
                confirm=args.confirm,
            )
    if command == "skill" and args.skill_command in {"verify", "install", "update"}:
        if args.skill_command == "verify":
            if args.vault is None:
                return verify_skill_bundle(args.bundle)
            with KnowledgeVault(args.vault, read_only=True) as vault:
                return verify_skill_bundle(args.bundle, vault=vault)
        expected_vault_id = args.expected_vault_id
        if args.vault is not None:
            with KnowledgeVault(args.vault, read_only=True) as vault:
                bound = verify_skill_bundle(args.bundle, vault=vault)
                if not bound["valid"] and bound.get("source_vault_id") == vault.vault_id:
                    raise RuntimeError(
                        "same-vault skill bundle no longer matches current active knowledge"
                    )
                expected_vault_id = vault.vault_id
        return install_skill_bundle(
            args.bundle,
            args.install_root,
            target=cast(SkillTarget, args.target),
            expected_vault_id=expected_vault_id,
            trust_external=args.trust_external,
            confirm=args.confirm,
            update=args.skill_command == "update",
        )
    if command == "workbench":
        from .operator_workbench import run_operator_workbench

        return run_operator_workbench(args.vault)
    if command == "discovery-model":
        if args.discovery_model_command == "setup":
            return setup_discovery_model(
                args.profile,
                model_root=args.model_root,
                local_files_only=args.local_files_only,
            )
        return verify_discovery_model(
            args.profile,
            model_root=args.model_root,
        )
    if command == "mcp":
        from .knowledge_mcp_server import run_knowledge_mcp

        run_knowledge_mcp(
            transport="stdio" if args.stdio else args.transport,
            vault_path=args.vault,
        )
        return None
    if command == "migrate" and args.rollback:
        if args.backup is None:
            raise ValueError("knowledge migration rollback requires --backup")
        return restore_knowledge_migration_backup(
            args.vault,
            backup=args.backup,
            confirm=args.confirm_rollback,
        )

    write_commands = {
        "ingest",
        "propose",
        "approve",
        "approve-source",
        "revoke",
        "forget",
        "relate",
        "import-package",
        "debug",
        "rebuild-indexes",
    }
    nested_write = (
        (
            command == "source"
            and (
                args.source_command in {"add", "update", "remove", "governance"}
                or (args.source_command == "add-dir" and not args.dry_run)
            )
        )
        or (command == "review" and args.review_command in {"approve", "reject", "approve-source"})
        or (command == "migrate" and args.apply)
        or (command == "run-receipt" and args.run_command == "create")
        or (command == "feedback" and args.feedback_action in {None, "record"})
        or (command == "inbox" and args.inbox_command == "promote")
        or (
            command == "relation"
            and (
                args.relation_command == "revise"
                or (args.relation_command == "carry-forward" and args.apply)
                or args.relation_command == "review-candidate"
            )
        )
        or (command == "lineage" and args.map_status is not None)
        or (command == "job" and args.job_command in {"resume", "retry", "cancel"})
        or (command == "projection" and args.projection_command == "propose")
        or (command == "gc" and not args.dry_run and not args.orphans)
    )
    command_read_only = command not in write_commands and not nested_write
    with _command_vault(
        args.vault,
        read_only=command_read_only,
    ) as vault:
        if command == "job":
            if args.job_command == "list":
                return list_ingest_jobs(vault, limit=args.limit)
            if args.job_command == "show":
                return load_ingest_job(vault, args.job_id)
            if args.job_command == "cancel":
                return cancel_ingest_job(vault, args.job_id)
            return run_ingest_job(
                vault,
                args.job_id,
                retry_failed=args.job_command == "retry",
            )
        if command == "snapshot":
            return create_knowledge_snapshot(
                vault,
                args.output,
                include_operator_state=args.include_operator_state,
            )
        if command == "gc":
            if args.orphans:
                return detect_knowledge_orphans(vault)
            return garbage_collect_derived(
                vault,
                confirm=args.confirm,
                dry_run=args.dry_run,
            )
        if command == "projection":
            if args.projection_command == "export":
                return export_knowledge_markdown(
                    vault,
                    args.output,
                    max_sensitivity=cast(Sensitivity, args.max_sensitivity),
                    replace=args.replace,
                )
            if args.projection_command == "diff":
                return projection_diff(vault, args.projection)
            return propose_projection_edits(
                vault,
                args.projection,
                confirm_no_case_data=args.confirm_no_case_data,
            )
        if command == "skill":
            targets = tuple(args.target) or ("generic",)
            return build_skill_bundle(
                vault,
                args.output,
                skill_name=args.name,
                description=args.description,
                knowledge_keys=tuple(args.knowledge_key),
                asset_ids=tuple(args.asset_id),
                targets=cast(tuple[SkillTarget, ...], targets),
                max_sensitivity=cast(Sensitivity, args.max_sensitivity),
                max_items=args.max_items,
                max_chars=args.max_chars,
                max_tokens=args.max_tokens,
                replace=args.replace,
            )
        if command == "retrieval-profile":
            if args.profile_command == "train":
                return train_retrieval_profile(
                    vault,
                    feedback_ids=tuple(args.feedback_id),
                )
            if args.profile_command == "evaluate":
                return evaluate_retrieval_profile(
                    vault,
                    profile_id=args.profile_id,
                    suite_path=args.suite,
                )
            if args.profile_command == "activate":
                return activate_retrieval_profile(
                    vault,
                    profile_id=args.profile_id,
                    evaluation_path=args.evaluation,
                )
            if args.profile_command == "rollback":
                return rollback_retrieval_profile(vault)
            active_profile = load_active_retrieval_profile(vault)
            return {
                "schema_version": "deeplaw.retrieval-profile-status/v1",
                "vault_id": vault.vault_id,
                "active": active_profile is not None,
                "profile": active_profile,
                "authority_effect": "ranking-only",
            }
        if command == "lineage":
            if args.map_status is not None:
                if args.knowledge_key is not None or args.asset_id is not None:
                    raise ValueError(
                        "lineage mapping cannot be combined with a lineage lookup selector"
                    )
                if args.reason is None:
                    raise ValueError("lineage mapping requires --reason")
                return review_lineage_mapping(
                    vault,
                    status=args.map_status,
                    from_asset_ids=tuple(args.from_asset_id),
                    to_asset_ids=tuple(args.to_asset_id),
                    confirm_reviewed=args.confirm_reviewed,
                    reviewer_id=args.reviewer_id,
                    reason=args.reason,
                )
            if (args.knowledge_key is None) == (args.asset_id is None):
                raise ValueError(
                    "lineage lookup requires exactly one --knowledge-key or --asset-id"
                )
            return vault.knowledge_lineage(
                knowledge_key=args.knowledge_key,
                asset_id=args.asset_id,
            )
        if command == "relation":
            if args.relation_command == "list":
                return vault.temporal_relations(
                    mode=args.mode,
                    as_of=args.as_of,
                    limit=args.limit,
                )
            if args.relation_command == "carry-forward":
                if args.apply:
                    return propose_relation_carry_forward(vault, limit=args.limit)
                return plan_relation_carry_forward(vault, limit=args.limit)
            if args.relation_command == "candidates":
                return pending_relation_carry_forward(vault, limit=args.limit)
            if args.relation_command == "review-candidate":
                return review_relation_carry_forward(
                    vault,
                    relation_revision_id=args.relation_revision_id,
                    decision=args.decision,
                    confirm_reviewed=args.confirm_reviewed,
                    reviewer_id=args.reviewer_id,
                    reason=args.reason,
                )
            return vault.revise_temporal_relation(
                args.relation_key,
                status=args.status,
                evidence_fragment_id=args.evidence_fragment_id,
                confirm_reviewed=args.confirm_reviewed,
                event_time=args.event_time,
                valid_from=args.valid_from,
                valid_to=args.valid_to,
            )
        if command == "inbox":
            if args.inbox_command == "submit":
                payload_path = args.payload.expanduser().absolute()
                if (
                    payload_path.is_symlink()
                    or not payload_path.is_file()
                    or not 1 <= payload_path.stat().st_size <= 1024 * 1024
                ):
                    raise ValueError("inbox payload must be a regular JSON file under 1 MiB")
                payload = strict_json_loads(payload_path.read_bytes())
                if not isinstance(payload, dict):
                    raise ValueError("inbox payload must be a JSON object")
                return submit_inbox_artifact(
                    vault,
                    artifact_type=args.artifact_type,
                    payload=payload,
                    producer_name=args.producer_name,
                    producer_version=args.producer_version,
                    priority_signals=tuple(args.priority_signal),
                    sensitivity=cast(Sensitivity, args.sensitivity),
                    confirm_no_case_data=args.confirm_no_case_data,
                )
            if args.inbox_command == "list":
                return list_inbox_artifacts(
                    vault,
                    state=args.state,
                    artifact_type=args.artifact_type,
                    limit=args.limit,
                )
            if args.inbox_command in {"show", "verify"}:
                result = verify_inbox_artifact(vault, args.artifact_id)
                if args.inbox_command == "show" and result["valid"]:
                    return result["artifact"]
                return result
            if args.inbox_command == "promote":
                return promote_inbox_proposal(
                    vault,
                    artifact_id=args.artifact_id,
                    confirm_reviewed=args.confirm_reviewed,
                )
            if args.inbox_command == "reject":
                return reject_inbox_artifact(
                    vault,
                    artifact_id=args.artifact_id,
                    confirm_reviewed=args.confirm_reviewed,
                )
        if command == "rebuild-indexes":
            if not args.confirm:
                raise ValueError("derived index rebuild requires --confirm")
            return vault.rebuild_derived_indexes()
        if command in {"recall", "diagnose-retrieval"}:
            result = recall(
                vault,
                args.query,
                goal=args.goal,
                confirm_no_case_data=args.confirm_no_case_data,
                mode=cast(RetrievalMode, args.mode),
                max_items=args.max_items,
                max_chars=args.max_chars,
                max_tokens=args.max_tokens,
                kinds=tuple(args.kind),
                memory_tiers=tuple(args.memory_tier),
                include_restricted=args.include_restricted,
                as_of=args.as_of,
                discovery_index_path=args.discovery_index,
                model_root=args.model_root,
                threads=args.threads,
                reranker_manifest=args.reranker_manifest,
            )
            _write_last_retrieval_trace(vault.root, result["retrieval"]["trace"])
            if command == "recall" and args.output is not None:
                _write_capsule(args.output, result["capsule"])
            return result
        if command == "explain":
            if args.last:
                trace = _read_last_retrieval_trace(vault.root)
                verification = verify_retrieval_trace(trace, vault=vault)
                if not verification["valid"]:
                    raise RuntimeError("last retrieval trace failed verification")
                return {
                    "schema_version": "deeplaw.knowledge-retrieval-explanation/v1",
                    "trace": trace,
                    "verification": verification,
                }
            if not args.confirm_no_case_data:
                raise ValueError("retrieval explanation requires --confirm-no-case-data")
            result = retrieve(
                vault,
                args.query,
                mode=cast(RetrievalMode, args.mode),
                limit=args.limit,
                max_chars=args.max_chars,
                as_of=args.as_of,
                discovery_index_path=args.discovery_index,
                model_root=args.model_root,
                threads=args.threads,
                reranker_manifest=args.reranker_manifest,
                explain=True,
            )
            _write_last_retrieval_trace(vault.root, result["trace"])
            return {
                "schema_version": "deeplaw.knowledge-retrieval-explanation/v1",
                "trace": result["trace"],
                "verification": verify_retrieval_trace(result["trace"], vault=vault),
            }
        if command == "compare-retrieval":
            if not args.confirm_no_case_data:
                raise ValueError("retrieval comparison requires --confirm-no-case-data")
            modes = tuple(args.mode) or ("lexical", "hybrid", "global")
            result = compare_retrieval(
                vault,
                args.query,
                modes=cast(tuple[RetrievalMode, ...], modes),
                limit=args.limit,
                max_chars=args.max_chars,
                discovery_index_path=args.discovery_index,
                as_of=args.as_of,
                reranker_manifest=args.reranker_manifest,
            )
            _write_last_retrieval_trace(
                vault.root,
                result["runs"][-1]["trace"],
            )
            return result
        if command == "migrate":
            if args.verify:
                control = vault.verify_knowledge_control_migration()
                identity = vault.verify_identity_v2_migration()
                result = {
                    "schema_version": "deeplaw.knowledge-migration-verification/v2",
                    "vault_id": vault.vault_id,
                    "control": control,
                    "identity_v2": identity,
                    "valid": bool(control["valid"] and identity["valid"]),
                }
                if args.backup is not None:
                    result = {
                        **result,
                        "backup": verify_knowledge_migration_backup(
                            args.backup,
                            expected_vault_id=vault.vault_id,
                        ),
                    }
                    result["valid"] = bool(result["valid"] and result["backup"]["valid"])
                return result
            control = vault.migrate_knowledge_control(
                apply=args.apply,
                backup_path=args.backup,
            )
            if not args.apply and not vault.control_enabled:
                return {
                    "schema_version": "deeplaw.knowledge-migration-plan/v2",
                    "vault_id": vault.vault_id,
                    "control": control,
                    "identity_v2": {
                        "required": True,
                        "blocked_by": "knowledge-control/v1",
                        "to_identity_schema": "deeplaw.knowledge-identity/v2",
                    },
                    "required": True,
                    "applied": False,
                }
            identity = vault.migrate_identity_v2(
                apply=args.apply,
                backup_path=(None if control.get("applied") else args.backup),
            )
            result = {
                "schema_version": "deeplaw.knowledge-migration-result/v2",
                "vault_id": vault.vault_id,
                "control": control,
                "identity_v2": identity,
                "required": bool(control["required"] or identity["required"]),
                "applied": bool(control["applied"] or identity["applied"]),
            }
            if "backup" in control:
                result["backup"] = control["backup"]
            elif "backup" in identity:
                result["backup"] = identity["backup"]
            if "verification" in control:
                result["verification"] = control["verification"]
            elif "verification" in identity:
                result["verification"] = identity["verification"]
            return result
        if command == "structure":
            if args.structure_command == "get":
                return vault.structure_get(args.node_id, max_chars=args.max_chars)
            if args.structure_command == "list":
                return vault.structure_list(
                    source_id=args.source_id,
                    compilation_id=args.compilation_id,
                    parent_node_id=args.parent_node_id,
                    limit=args.limit,
                )
            if args.structure_command == "search":
                return vault.structure_search(
                    args.query,
                    source_id=args.source_id,
                    limit=args.limit,
                )
            if args.structure_command == "trace":
                return vault.structure_trace(args.node_id)
        if command == "source":
            if args.source_command == "add":
                return _bounded_ingest_receipt(
                    compile_source(
                        vault,
                        args.source,
                        source_kind=cast(SourceKind, args.source_kind),
                        title=args.title,
                        origin_uri=args.origin_uri,
                        trust=cast(TrustLevel, args.trust),
                        sensitivity=cast(Sensitivity, args.sensitivity),
                        confirm_no_case_data=args.confirm_no_case_data,
                        pdf_fallback=args.pdf_fallback,
                        typed_extraction=args.typed_extraction,
                        typed_extractor_manifest=args.typed_extractor_manifest,
                        confirm_external_disclosure=args.confirm_external_disclosure,
                        reference_proposals=args.reference_proposals,
                    )
                )
            if args.source_command == "add-dir":
                return compile_directory(
                    vault,
                    args.directory,
                    recursive=args.recursive,
                    include=tuple(args.include),
                    exclude=tuple(args.exclude),
                    source_kind=cast(SourceKind, args.source_kind),
                    trust=cast(TrustLevel, args.trust),
                    sensitivity=cast(Sensitivity, args.sensitivity),
                    confirm_no_case_data=args.confirm_no_case_data,
                    pdf_fallback=args.pdf_fallback,
                    typed_extraction=args.typed_extraction,
                    typed_extractor_manifest=args.typed_extractor_manifest,
                    confirm_external_disclosure=args.confirm_external_disclosure,
                    reference_proposals=args.reference_proposals,
                    dry_run=args.dry_run,
                )
            if args.source_command == "list":
                sources = _filter_source_selector(
                    _source_versions_for_cli(
                        vault,
                        source_key=args.source_key,
                        alias=args.alias,
                    ),
                    active=args.active,
                    latest=args.latest,
                )
                return {
                    "schema_version": "deeplaw.knowledge-source-list/v1",
                    "vault_id": vault.vault_id,
                    "source_count": len(sources),
                    "sources": list(sources[:500]),
                    "truncated": len(sources) > 500,
                }
            if args.source_command == "show":
                source_id = _resolve_source_id(
                    vault,
                    source_id=args.source_id,
                    alias=args.alias,
                    active=args.active,
                    latest=args.latest,
                )
                return vault.source_info(source_id)
            if args.source_command == "verify":
                source_id = _resolve_source_id(
                    vault,
                    source_id=args.source_id,
                    alias=args.alias,
                    active=args.active,
                    latest=args.latest,
                )
                return vault.verify_source(source_id)
            if args.source_command == "diff":
                if args.alias is not None:
                    if args.old_source_id is not None or args.new_source_id is not None:
                        raise ValueError(
                            "source diff alias cannot be combined with exact source IDs"
                        )
                    versions = _source_versions_for_cli(vault, alias=args.alias)
                    latest = _latest_source_version(versions)
                    if latest["previous_source_id"] is None:
                        raise ValueError("source diff alias requires at least two versions")
                    old_source_id = latest["previous_source_id"]
                    new_source_id = latest["source_id"]
                else:
                    if args.latest:
                        raise ValueError("source diff --latest requires --alias")
                    if args.old_source_id is None or args.new_source_id is None:
                        raise ValueError("source diff requires --alias or both exact source IDs")
                    old_source_id = args.old_source_id
                    new_source_id = args.new_source_id
                return vault.source_diff(old_source_id, new_source_id)
            if args.source_command == "update":
                source_key = (
                    _source_key_for_alias(vault, args.alias)
                    if args.alias is not None
                    else args.source_key
                )
                if source_key is None:
                    raise ValueError("source update requires --source-key or --alias")
                if vault.active_source_for_key(source_key) is None:
                    raise RuntimeError("source update requires an active predecessor")
                return _bounded_ingest_receipt(
                    compile_source(
                        vault,
                        args.source,
                        source_kind=cast(SourceKind, args.source_kind),
                        title=args.title,
                        origin_uri=args.origin_uri,
                        trust=cast(TrustLevel, args.trust),
                        sensitivity=cast(Sensitivity, args.sensitivity),
                        confirm_no_case_data=args.confirm_no_case_data,
                        pdf_fallback=args.pdf_fallback,
                        source_key=source_key,
                        typed_extraction=args.typed_extraction,
                        typed_extractor_manifest=args.typed_extractor_manifest,
                        confirm_external_disclosure=args.confirm_external_disclosure,
                        reference_proposals=args.reference_proposals,
                    )
                )
            if args.source_command == "remove":
                source_id = _resolve_source_id(
                    vault,
                    source_id=args.source_id,
                    alias=args.alias,
                    active=args.active,
                    latest=args.latest,
                )
                return vault.remove_source(
                    source_id,
                    reason=args.reason,
                    confirm=args.confirm,
                )
            if args.source_command == "governance":
                source_id = _resolve_source_id(
                    vault,
                    source_id=args.source_id,
                    alias=args.alias,
                    active=args.active,
                    latest=args.latest,
                )
                return vault.update_source_governance(
                    source_id,
                    trust=cast(TrustLevel, args.trust),
                    sensitivity=cast(Sensitivity, args.sensitivity),
                    export_allowed=args.allow_export,
                    reviewer_id=args.reviewer_id,
                    reason=args.reason,
                    confirm_reviewed=args.confirm_reviewed,
                )
        if command == "review":
            if args.review_command == "queue":
                return vault.review_queue(
                    source_id=args.source_id,
                    kind=args.kind,
                    status=args.status,
                    limit=args.limit,
                )
            if args.review_command == "show":
                return vault.get_asset(
                    args.asset_id,
                    include_inactive=True,
                ).to_dict()
            if args.review_command == "manifest":
                return vault.source_review_manifest(args.source_id)
            if args.review_command == "approve":
                asset = vault.approve_asset(
                    args.asset_id,
                    confirm_reviewed=args.confirm_reviewed,
                    confirm_quarantined=args.confirm_quarantine,
                    reviewer_id=args.reviewer_id,
                    review_reason=args.reason,
                )
                return {
                    "schema_version": "deeplaw.knowledge-review-decision/v1",
                    "decision": "approve",
                    "asset": asset.to_dict(),
                    "review_receipt": vault.latest_review_receipt_for_asset(asset.asset_id),
                }
            if args.review_command == "reject":
                return vault.reject_asset(
                    args.asset_id,
                    reason=args.reason,
                    reviewer_id=args.reviewer_id,
                    confirm_reviewed=args.confirm_reviewed,
                )
            if args.review_command == "approve-source":
                return vault.approve_source_assets(
                    args.source_id,
                    confirm_reviewed=args.confirm_reviewed,
                    confirm_quarantined=args.confirm_quarantine,
                    review_manifest_sha256=args.review_manifest_sha256,
                    reviewer_id=args.reviewer_id,
                    review_reason=args.reason,
                )
            if args.review_command == "verify-receipt":
                return vault.get_review_receipt(args.review_receipt_id)
        if command == "run-receipt":
            if args.run_command == "create":
                return create_run_receipt(
                    vault,
                    capsule_path=args.capsule,
                    status=args.status,
                    host_name=args.host_name,
                    host_version=args.host_version,
                    model_name=args.model_name,
                    model_version=args.model_version,
                    started_at=args.started_at,
                    finished_at=args.finished_at,
                    outcome_artifact=args.outcome_artifact,
                    input_tokens=args.input_tokens,
                    output_tokens=args.output_tokens,
                    latency_ms=args.latency_ms,
                    cost=args.cost,
                    currency=args.currency,
                )
            if args.run_command == "list":
                return vault.list_run_receipts(limit=args.limit)
            return vault.get_run_receipt(args.run_id)
        if command == "build-discovery":
            return build_discovery_index(
                vault,
                args.output,
                profile_name=args.profile,
                model_root=args.model_root,
                confirm_no_case_data=args.confirm_no_case_data,
                threads=args.threads,
            )
        if command == "verify-discovery":
            return verify_discovery_index(args.index, vault=vault)
        if command == "search-discovery":
            discovery = DiscoveryIndex(
                args.index,
                vault=vault,
                model_root=args.model_root,
                threads=args.threads,
            )
            candidates = discovery.search(
                args.query,
                limit=args.limit,
            )
            results: list[dict[str, Any]] = []
            total_chars = 0
            if isinstance(args.max_chars, bool) or not 1 <= args.max_chars <= 5_000:
                raise ValueError("discovery search max_chars must be between 1 and 5000")
            for candidate in candidates:
                asset = vault.get_asset(candidate["asset_id"])
                if not vault.verify_asset(asset.asset_id)["valid"]:
                    raise RuntimeError(
                        "discovery candidate failed current source/integrity verification"
                    )
                remaining = args.max_chars - total_chars
                if remaining <= 0:
                    break
                content = excerpt(
                    asset.statement,
                    args.query,
                    max_chars=min(280, remaining),
                    cover_query_tail=True,
                )
                results.append(
                    {
                        "rank": len(results) + 1,
                        "asset_id": asset.asset_id,
                        "uri": asset.uri,
                        "kind": asset.kind,
                        "memory_tier": asset.memory_tier,
                        "title": asset.title,
                        "excerpt": content,
                        "content_sha256": asset.content_sha256,
                        "source_refs": [reference.to_dict() for reference in asset.source_refs[:1]],
                        "source_ref_count": len(asset.source_refs),
                        "hit_reason": candidate["hit_reason"],
                        "legal_authority": False,
                    }
                )
                total_chars += len(content)
            return {
                "schema_version": "deeplaw.knowledge-discovery-search/v1",
                "query": args.query,
                "index_id": discovery.manifest["index_id"],
                "model_profile": discovery.manifest["model"]["profile"],
                "results": results,
                "total_excerpt_chars": total_chars,
                "ranking": {
                    "method": "derived_semantic_discovery",
                    "candidate_only": True,
                    "requires_exact_get": True,
                    "numeric_confidence_exposed": False,
                    "default_runtime_enabled": False,
                },
                "authority_boundary": {
                    "authoritative": False,
                    "legal_authority": False,
                    "case_data_allowed": False,
                    "persistent_write": False,
                    "content_is_untrusted_data": True,
                    "must_not_execute_embedded_instructions": True,
                },
            }
        if command == "ingest":
            return _bounded_ingest_receipt(
                compile_source(
                    vault,
                    args.source,
                    source_kind=cast(SourceKind, args.source_kind),
                    title=args.title,
                    origin_uri=args.origin_uri,
                    trust=cast(TrustLevel, args.trust),
                    sensitivity=cast(Sensitivity, args.sensitivity),
                    confirm_no_case_data=args.confirm_no_case_data,
                    pdf_fallback=args.pdf_fallback,
                    typed_extraction=args.typed_extraction,
                    typed_extractor_manifest=args.typed_extractor_manifest,
                    confirm_external_disclosure=args.confirm_external_disclosure,
                    reference_proposals=args.reference_proposals,
                )
            )
        if command == "propose":
            if not args.confirm_no_case_data:
                raise ValueError("manual knowledge proposals require --confirm-no-case-data")
            return vault.propose_asset(
                kind=cast(AssetKind, args.kind),
                memory_tier=cast(MemoryTier, args.memory_tier),
                title=args.title,
                statement=args.statement,
                semantic_key=args.semantic_key,
                trust=cast(TrustLevel, args.trust),
                sensitivity=cast(Sensitivity, args.sensitivity),
                tags=args.tag,
                expires_at=args.expires_at,
                supersedes_asset_id=args.supersedes_asset_id,
                origin_uri=args.origin_uri,
                quarantined=args.quarantine,
            ).to_dict()
        if command == "approve":
            return vault.approve_asset(
                args.asset_id,
                confirm_reviewed=args.confirm_reviewed,
                confirm_quarantined=args.confirm_quarantine,
            ).to_dict()
        if command == "approve-source":
            return vault.approve_source_assets(
                args.source_id,
                confirm_reviewed=args.confirm_reviewed,
                confirm_quarantined=args.confirm_quarantine,
                review_manifest_sha256=args.review_manifest_sha256,
                reviewer_id=args.reviewer_id,
                review_reason=args.review_reason,
            )
        if command == "revoke":
            return vault.revoke_asset(
                args.asset_id,
                reason=args.reason,
                confirm=args.confirm,
            ).to_dict()
        if command == "forget":
            return vault.selectively_forget(
                knowledge_key=args.knowledge_key,
                asset_id=args.asset_id,
                reason=args.reason,
                confirm=args.confirm,
            )
        if command == "relate":
            return vault.add_relation(
                subject_asset_id=args.subject_asset_id,
                predicate=args.predicate,
                object_asset_id=args.object_asset_id,
                evidence_fragment_id=args.evidence_fragment_id,
                confirm_reviewed=args.confirm_reviewed,
                event_time=args.event_time,
                valid_from=args.valid_from,
                valid_to=args.valid_to,
            )
        if command == "search":
            return vault.search(
                args.query,
                limit=args.limit,
                max_chars=args.max_chars,
                kinds=args.kind,
                memory_tiers=args.memory_tier,
                include_restricted=args.include_restricted,
                include_inactive=args.include_inactive,
            ).to_dict()
        if command == "get":
            asset = vault.get_asset(
                args.asset_id,
                include_inactive=args.include_inactive,
            )
            if not args.include_inactive and not vault.verify_asset(asset.asset_id)["valid"]:
                raise RuntimeError(
                    "active Knowledge Asset failed current source/integrity verification"
                )
            return asset.to_dict()
        if command == "context":
            capsule = compile_context(
                vault,
                task=args.task,
                confirm_no_case_data=args.confirm_no_case_data,
                goal=args.goal,
                max_items=args.max_items,
                max_chars=args.max_chars,
                kinds=tuple(args.kind),
                memory_tiers=tuple(args.memory_tier),
                include_restricted=args.include_restricted,
            )
            if args.output is not None:
                _write_capsule(args.output, capsule)
            return capsule
        if command == "verify":
            return vault.verify_asset(args.asset_id)
        if command == "inspect":
            return vault.inspect()
        if command == "export":
            if args.include_source_files and not args.confirm_export_source_files:
                raise ValueError("source-file export requires --confirm-export-source-files")
            return export_knowledge_package(
                vault,
                args.output,
                max_sensitivity=cast(Sensitivity, args.max_sensitivity),
                include_evidence_text=args.include_evidence_text,
                include_source_files=args.include_source_files,
            )
        if command == "export-markdown":
            return export_knowledge_markdown(
                vault,
                args.output,
                max_sensitivity=cast(Sensitivity, args.max_sensitivity),
                replace=args.replace,
            )
        if command == "import-package":
            return import_knowledge_package(
                vault,
                args.package,
                confirm_untrusted=args.confirm_untrusted,
            )
        if command == "debug":
            return record_debug_experience(
                vault,
                question=args.question,
                cause=args.cause,
                fix=args.fix,
                prevention=args.prevention,
                confirm_no_case_data=args.confirm_no_case_data,
                sensitivity=cast(Sensitivity, args.sensitivity),
            )
        if command == "feedback":
            if args.feedback_action == "record":
                if not args.confirm_no_case_data:
                    raise ValueError("structured feedback requires --confirm-no-case-data")
                if not all((args.run_id, args.outcome, args.observation, args.recommended_action)):
                    raise ValueError(
                        "structured feedback record requires run, outcome, "
                        "observation, and recommended action"
                    )
                return record_structured_feedback(
                    vault,
                    run_id=args.run_id,
                    outcome=args.outcome,
                    helpful_asset_ids=tuple(args.helpful_asset_id),
                    irrelevant_asset_ids=tuple(args.irrelevant_asset_id),
                    harmful_asset_ids=tuple(args.harmful_asset_id),
                    stale_asset_ids=tuple(args.stale_asset_id),
                    missing_knowledge=tuple(args.missing_knowledge),
                    missing_sources=tuple(args.missing_source),
                    incorrect_relations=tuple(args.incorrect_relation),
                    budget_failures=tuple(args.budget_failure),
                    observation=args.observation,
                    recommended_action=args.recommended_action,
                    sensitivity=cast(Sensitivity, args.sensitivity),
                )
            if args.feedback_action == "list":
                return vault.list_feedback(limit=args.limit)
            if args.feedback_action in {"show", "verify", "promote-eval"}:
                if not args.feedback_id:
                    raise ValueError(f"feedback {args.feedback_action} requires --feedback-id")
                feedback_record = vault.get_feedback(args.feedback_id)
                if args.feedback_action == "promote-eval":
                    result = {
                        "schema_version": "deeplaw.knowledge-regression-case/v1",
                        **feedback_record["regression_case"],
                        "claim_eligible": False,
                    }
                    if args.output is not None:
                        _write_capsule(args.output, result)
                    return result
                return feedback_record
            if args.feedback_action == "replay":
                if not args.feedback_id or args.capsule is None:
                    raise ValueError("feedback replay requires --feedback-id and --capsule")
                return replay_feedback(
                    vault,
                    feedback_id=args.feedback_id,
                    capsule_path=args.capsule,
                )
            if not all((args.capsule, args.outcome, args.observation, args.lesson)):
                raise ValueError(
                    "legacy feedback requires capsule, outcome, observation, and lesson"
                )
            return record_capsule_feedback(
                vault,
                capsule_path=args.capsule,
                outcome=args.outcome,
                observation=args.observation,
                lesson=args.lesson,
                next_action=args.next_action,
                confirm_no_case_data=args.confirm_no_case_data,
                sensitivity=cast(Sensitivity, args.sensitivity),
            )
    raise RuntimeError(f"unhandled knowledge command: {command}")
