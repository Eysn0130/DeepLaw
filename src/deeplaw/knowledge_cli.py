from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path
from typing import Any, cast

from .context_compiler import compile_context, verify_capsule_file
from .knowledge_compiler import (
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
from .util import excerpt


def add_knowledge_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    knowledge = commands.add_parser(
        "knowledge",
        help="Compile and read isolated, review-gated Agent Knowledge Assets",
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
    ingest.add_argument(
        "--typed-extraction",
        choices=("off", "deterministic-v1"),
        default="off",
    )
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
    source_add.add_argument(
        "--typed-extraction", choices=("off", "deterministic-v1"), default="off"
    )
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
    source_add_dir.add_argument(
        "--typed-extraction", choices=("off", "deterministic-v1"), default="off"
    )
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
            source_command.add_argument("--source-id", required=True)
        if name == "list":
            source_command.add_argument("--source-key")
        if name == "diff":
            source_command.add_argument("--old-source-id", required=True)
            source_command.add_argument("--new-source-id", required=True)
        if name == "update":
            source_command.add_argument("--source-key", required=True)
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
            source_command.add_argument(
                "--typed-extraction", choices=("off", "deterministic-v1"), default="off"
            )
            source_command.add_argument("--confirm-no-case-data", action="store_true")
        if name == "remove":
            source_command.add_argument("--reason", required=True)
            source_command.add_argument("--confirm", action="store_true")

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

    relate = subcommands.add_parser(
        "relate",
        help="Add one explicit human-reviewed relation between active assets",
    )
    relate.add_argument("--vault", type=Path, default=default_knowledge_vault())
    relate.add_argument("--subject-asset-id", required=True)
    relate.add_argument("--predicate", choices=sorted(RELATION_PREDICATES), required=True)
    relate.add_argument("--object-asset-id", required=True)
    relate.add_argument("--evidence-fragment-id")
    relate.add_argument("--confirm-reviewed", action="store_true")

    search = subcommands.add_parser("search", help="Search reviewed active Knowledge Assets")
    search.add_argument("--vault", type=Path, default=default_knowledge_vault())
    search.add_argument("--query", required=True)
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--max-chars", type=int, default=5_000)
    search.add_argument("--kind", action="append", default=[])
    search.add_argument("--memory-tier", action="append", default=[])
    search.add_argument("--include-restricted", action="store_true")
    search.add_argument("--include-inactive", action="store_true")

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
        help="Diagnose filesystem isolation without overstating Windows ACL support",
    )
    doctor.add_argument("--vault", type=Path, default=default_knowledge_vault())
    doctor.add_argument("--permissions", action="store_true")

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


def handle_knowledge_command(args: argparse.Namespace) -> dict[str, Any] | None:
    command = args.knowledge_command
    if command == "init":
        return initialize_knowledge_vault(
            args.vault,
            name=args.name,
            scope=cast(VaultScope, args.scope),
        )
    if command == "doctor":
        permission_report = knowledge_vault_permission_report(args.vault)
        if args.permissions:
            return permission_report
        try:
            with KnowledgeVault(args.vault, read_only=True) as vault:
                inspection = vault.inspect()
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            inspection = {
                "valid": False,
                "error": str(error),
            }
        return {
            "schema_version": "deeplaw.knowledge-doctor/v1",
            "permissions": permission_report,
            "vault": inspection,
            "ready": bool(permission_report["permissions_verified"])
            and bool(inspection.get("integrity", {}).get("valid")),
        }
    if command == "verify-package":
        return verify_knowledge_package(args.package)
    if command == "verify-capsule":
        if args.vault is None:
            return verify_capsule_file(args.capsule)
        with KnowledgeVault(args.vault, read_only=True) as vault:
            return verify_capsule_file(args.capsule, vault=vault)
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
        "relate",
        "import-package",
        "debug",
    }
    nested_write = (
        (
            command == "source"
            and (
                args.source_command in {"add", "update", "remove"}
                or (args.source_command == "add-dir" and not args.dry_run)
            )
        )
        or (command == "review" and args.review_command in {"approve", "reject", "approve-source"})
        or (command == "migrate" and args.apply)
        or (command == "run-receipt" and args.run_command == "create")
        or (
            command == "feedback"
            and args.feedback_action in {None, "record"}
        )
    )
    with KnowledgeVault(
        args.vault,
        read_only=command not in write_commands and not nested_write,
    ) as vault:
        if command == "migrate":
            if args.verify:
                result = vault.verify_knowledge_control_migration()
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
            return vault.migrate_knowledge_control(
                apply=args.apply,
                backup_path=args.backup,
            )
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
                    dry_run=args.dry_run,
                )
            if args.source_command == "list":
                sources = (
                    vault.source_versions(args.source_key)
                    if args.source_key
                    else vault.all_sources()
                )
                return {
                    "schema_version": "deeplaw.knowledge-source-list/v1",
                    "vault_id": vault.vault_id,
                    "source_count": len(sources),
                    "sources": list(sources[:500]),
                    "truncated": len(sources) > 500,
                }
            if args.source_command == "show":
                return vault.source_info(args.source_id)
            if args.source_command == "verify":
                return vault.verify_source(args.source_id)
            if args.source_command == "diff":
                return vault.source_diff(args.old_source_id, args.new_source_id)
            if args.source_command == "update":
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
                        source_key=args.source_key,
                        typed_extraction=args.typed_extraction,
                    )
                )
            if args.source_command == "remove":
                return vault.remove_source(
                    args.source_id,
                    reason=args.reason,
                    confirm=args.confirm,
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
        if command == "relate":
            return vault.add_relation(
                subject_asset_id=args.subject_asset_id,
                predicate=args.predicate,
                object_asset_id=args.object_asset_id,
                evidence_fragment_id=args.evidence_fragment_id,
                confirm_reviewed=args.confirm_reviewed,
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
