from __future__ import annotations

import argparse
import json
import os
import secrets
from pathlib import Path
from typing import Any, cast

from .context_compiler import compile_context, verify_capsule_file
from .knowledge_compiler import (
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
)
from .util import excerpt


def add_knowledge_parser(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    knowledge = commands.add_parser(
        "knowledge",
        help="Compile and read isolated, review-gated Agent Knowledge Assets",
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
    ingest.add_argument("--confirm-no-case-data", action="store_true")

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
        help=(
            "Atomically activate every reviewed candidate from one exact compiled "
            "source"
        ),
    )
    approve_source.add_argument(
        "--vault",
        type=Path,
        default=default_knowledge_vault(),
    )
    approve_source.add_argument("--source-id", required=True)
    approve_source.add_argument("--confirm-reviewed", action="store_true")
    approve_source.add_argument("--confirm-quarantine", action="store_true")

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
        help=(
            "Build a source-bound derived discovery index without changing the "
            "canonical vault"
        ),
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
        help=(
            "Run explicit derived candidate discovery; results remain non-authoritative"
        ),
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
    feedback.add_argument("--vault", type=Path, default=default_knowledge_vault())
    feedback.add_argument("--capsule", type=Path, required=True)
    feedback.add_argument(
        "--outcome",
        choices=("success", "partial", "failure"),
        required=True,
    )
    feedback.add_argument("--observation", required=True)
    feedback.add_argument("--lesson", required=True)
    feedback.add_argument("--next-action")
    feedback.add_argument(
        "--sensitivity",
        choices=sorted(SENSITIVITY_LEVELS),
        default="private",
    )
    feedback.add_argument("--confirm-no-case-data", action="store_true")

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
        raise FileExistsError(
            "capsule output already exists; choose a new output path"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(capsule, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
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


def handle_knowledge_command(args: argparse.Namespace) -> dict[str, Any] | None:
    command = args.knowledge_command
    if command == "init":
        return initialize_knowledge_vault(
            args.vault,
            name=args.name,
            scope=cast(VaultScope, args.scope),
        )
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

    write_commands = {
        "ingest",
        "propose",
        "approve",
        "approve-source",
        "revoke",
        "relate",
        "import-package",
        "debug",
        "feedback",
    }
    with KnowledgeVault(args.vault, read_only=command not in write_commands) as vault:
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
                raise ValueError(
                    "discovery search max_chars must be between 1 and 5000"
                )
            for candidate in candidates:
                asset = vault.get_asset(candidate["asset_id"])
                if not vault.verify_asset(asset.asset_id)["valid"]:
                    raise RuntimeError(
                        "discovery candidate failed current source/integrity "
                        "verification"
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
                        "source_refs": [
                            reference.to_dict()
                            for reference in asset.source_refs[:1]
                        ],
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
                )
            )
        if command == "propose":
            if not args.confirm_no_case_data:
                raise ValueError(
                    "manual knowledge proposals require --confirm-no-case-data"
                )
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
                raise ValueError(
                    "source-file export requires --confirm-export-source-files"
                )
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
