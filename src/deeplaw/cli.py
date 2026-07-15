from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .catalog_signing import (
    export_trust_store,
    initialize_signing_key,
    sign_catalog_file,
)
from .evaluate import evaluate_file
from .ingest import build_release
from .markdown_export import export_markdown
from .mcp_server import run_mcp
from .models import SearchRequest
from .official import (
    disable_official,
    enable_official,
    official_status,
    sync_official,
    uninstall_official,
)
from .private_library import (
    add_private_document,
    delete_private_document,
    list_private_documents,
    resolve_private_database,
)
from .search import DeepLaw, response_json
from .store import database_sha256, default_home, resolve_active_database
from .vision import (
    EXTRACTION_EVIDENCE_SCHEMA,
    PIPELINE_NAME,
    extract_pdf_vision_consensus,
)


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deeplaw", description="Read-only Chinese legal research")
    parser.add_argument("--version", action="version", version=f"deeplaw {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Build an immutable release from a verified manifest")
    build.add_argument("--source-root", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--review-overlay", type=Path)
    build.add_argument("--reviewed-pages-root", type=Path)
    build.add_argument("--output-root", type=Path, default=default_home() / "releases")
    build.add_argument("--activate", action="store_true")
    build.add_argument(
        "--pdf-fallback",
        choices=("off", "vision-consensus", "document-engine"),
        default="off",
    )
    build.add_argument("--allow-needs-ocr", action="store_true")

    evidence = commands.add_parser(
        "pdf-evidence",
        help="Extract one PDF with native-first page evidence and fail-closed OCR review",
    )
    evidence.add_argument("--source", type=Path, required=True)
    evidence.add_argument("--reviewed-pages", type=Path)
    evidence.add_argument("--language", default="chi_sim+eng")

    search = commands.add_parser("search", help="Return bounded legal evidence cards")
    search.add_argument("--query", required=True)
    search.add_argument("--purpose", default="auto")
    search.add_argument("--as-of")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--max-chars", type=int, default=3500)
    search.add_argument("--document-type", action="append", default=[])
    search.add_argument("--db", type=Path)

    get = commands.add_parser("get", help="Fetch one exact segment by ID")
    get.add_argument("--segment-id", required=True)
    get.add_argument("--max-chars", type=int, default=6000)
    get.add_argument("--db", type=Path)

    verify = commands.add_parser("verify", help="Verify an evidence receipt or release database")
    verify.add_argument("--segment-id")
    verify.add_argument("--receipt-id")
    verify.add_argument("--db", type=Path)

    evaluate = commands.add_parser("eval", help="Run a source-free retrieval evaluation file")
    evaluate.add_argument("--cases", type=Path, required=True)
    evaluate.add_argument("--db", type=Path)
    evaluate.add_argument("--limit", type=int, default=5)
    evaluate.add_argument("--output", type=Path)

    export_md = commands.add_parser(
        "export-markdown",
        help="Export deterministic human-readable views from verified Document IR",
    )
    export_md.add_argument("--db", type=Path)
    export_md.add_argument("--output", type=Path, required=True)

    mcp = commands.add_parser("mcp", help="Run the read-only MCP server")
    mcp.add_argument("--transport", choices=("stdio",), default="stdio")
    mcp.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport (explicit alias for host plugin manifests)",
    )

    official = commands.add_parser(
        "official",
        help="Install, update, disable, or uninstall the team-maintained official catalog",
    )
    official_commands = official.add_subparsers(dest="official_command", required=True)
    for name, help_text in (
        ("install", "Install the bundled official catalog and activate its release"),
        ("update", "Fetch a newer official catalog and build its release"),
    ):
        sync = official_commands.add_parser(name, help=help_text)
        sync.add_argument("--catalog", help="Local catalog path or HTTPS catalog URL")
        sync.add_argument(
            "--catalog-signature",
            help="Detached signature path or HTTPS URL (defaults to <catalog>.sig)",
        )
        sync.add_argument(
            "--source-root",
            type=Path,
            help="Use an existing verified source package instead of downloading source files",
        )
        sync.add_argument(
            "--pdf-fallback",
            choices=("off", "vision-consensus", "document-engine"),
            default=None,
            help=(
                "PDF fallback for unsigned local development catalogs; a signed catalog's "
                "buildPolicy is authoritative"
            ),
        )
        sync.add_argument(
            "--allow-unsigned-local-catalog",
            action="store_true",
            help="Development only: accept an explicitly selected local unsigned catalog",
        )
    official_commands.add_parser("status", help="Show official catalog installation state")
    official_commands.add_parser("enable", help="Enable the installed official release")
    official_commands.add_parser("disable", help="Disable without modifying the release")
    official_commands.add_parser("uninstall", help="Delete locally installed official data")

    private = commands.add_parser(
        "private",
        help="Manage a per-OS-user legal-reference library outside the official catalog",
    )
    private_commands = private.add_subparsers(dest="private_command", required=True)
    private_add = private_commands.add_parser("add", help="Add one private legal-reference file")
    private_add.add_argument("--source", type=Path, required=True)
    private_add.add_argument("--title")
    private_add.add_argument(
        "--document-type",
        choices=(
            "law",
            "administrative_regulation",
            "judicial_interpretation",
            "prosecution_standard",
            "departmental_rule",
            "normative_document",
            "case_reference",
        ),
        default="normative_document",
    )
    private_add.add_argument("--issuer", default="用户提供（未经 DeepLaw 官方审核）")
    private_add.add_argument("--effective-from")
    private_add.add_argument("--effective-to")
    private_add.add_argument(
        "--confirm-no-case-data",
        action="store_true",
        help="Confirm this is a legal reference, not Analytix case material",
    )
    private_add.add_argument(
        "--pdf-fallback",
        choices=("off", "vision-consensus", "document-engine"),
        default="off",
    )
    private_add.add_argument("--allow-needs-ocr", action="store_true")

    private_delete = private_commands.add_parser("delete", help="Delete one private document")
    private_delete.add_argument("--document-id", required=True)
    private_delete.add_argument(
        "--pdf-fallback",
        choices=("off", "vision-consensus", "document-engine"),
        default="off",
    )
    private_delete.add_argument("--allow-needs-ocr", action="store_true")
    private_commands.add_parser("list", help="List private legal-reference documents")
    private_commands.add_parser("status", help="Show private legal-reference library state")

    private_search = private_commands.add_parser(
        "search", help="Search only the user-private legal-reference library"
    )
    private_search.add_argument("--query", required=True)
    private_search.add_argument("--purpose", default="auto")
    private_search.add_argument("--as-of")
    private_search.add_argument("--limit", type=int, default=5)
    private_search.add_argument("--max-chars", type=int, default=3500)
    private_search.add_argument("--document-type", action="append", default=[])
    private_search.add_argument("--db", type=Path)
    private_get = private_commands.add_parser("get", help="Fetch one private segment by ID")
    private_get.add_argument("--segment-id", required=True)
    private_get.add_argument("--max-chars", type=int, default=6000)
    private_get.add_argument("--db", type=Path)
    private_verify = private_commands.add_parser(
        "verify", help="Verify a private-library receipt"
    )
    private_verify.add_argument("--segment-id", required=True)
    private_verify.add_argument("--receipt-id", required=True)
    private_verify.add_argument("--db", type=Path)

    maintainer = commands.add_parser(
        "maintainer",
        help="Manage the offline official-catalog signing identity",
    )
    maintainer_commands = maintainer.add_subparsers(dest="maintainer_command", required=True)
    init_key = maintainer_commands.add_parser(
        "init-signing-key",
        help="Create or inspect the owner-only Ed25519 signing key",
    )
    init_key.add_argument("--key-file", type=Path)
    init_key.add_argument("--trust-output", type=Path)
    sign_catalog = maintainer_commands.add_parser(
        "sign-catalog",
        help="Sign an exact official catalog with the maintainer key",
    )
    sign_catalog.add_argument("--catalog", type=Path, required=True)
    sign_catalog.add_argument("--signature-output", type=Path)
    sign_catalog.add_argument("--key-file", type=Path)
    sign_catalog.add_argument("--trust-output", type=Path)

    doctor = commands.add_parser("doctor", help="Inspect the active release without changing it")
    doctor.add_argument("--db", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        if args.command == "pdf-evidence":
            result = extract_pdf_vision_consensus(
                args.source.expanduser().resolve(strict=True),
                reviewed_pages_path=(
                    args.reviewed_pages.expanduser().resolve(strict=True)
                    if args.reviewed_pages is not None
                    else None
                ),
                language=args.language,
            )
            quality = asdict(result.quality)
            pages = quality.pop("page_evidence")
            _print_json(
                {
                    "schemaVersion": EXTRACTION_EVIDENCE_SCHEMA,
                    "pipeline": PIPELINE_NAME,
                    "sourceName": args.source.name,
                    "sourceSha256": result.quality.source_sha256,
                    "quality": quality,
                    "pages": pages,
                    "blocks": [asdict(block) for block in result.blocks],
                }
            )
            return
        if args.command == "build":
            release_dir, report = build_release(
                source_root=args.source_root,
                manifest_path=args.manifest,
                output_root=args.output_root,
                activate=args.activate,
                pdf_fallback=args.pdf_fallback,
                allow_needs_ocr=args.allow_needs_ocr,
                review_overlay_path=args.review_overlay,
                reviewed_pages_root=args.reviewed_pages_root,
            )
            _print_json({"release_dir": str(release_dir), "report": report.to_dict()})
            return
        if args.command == "mcp":
            run_mcp(transport="stdio" if args.stdio else args.transport)
            return
        if args.command == "export-markdown":
            database = resolve_active_database(explicit_db=args.db)
            _print_json(export_markdown(database, args.output))
            return
        if args.command == "official":
            if args.official_command in {"install", "update"}:
                _print_json(
                    sync_official(
                        catalog_source=args.catalog,
                        catalog_signature_source=args.catalog_signature,
                        source_root=args.source_root,
                        update=args.official_command == "update",
                        pdf_fallback=args.pdf_fallback,
                        allow_unsigned_local_catalog=args.allow_unsigned_local_catalog,
                    )
                )
            elif args.official_command == "status":
                _print_json(official_status())
            elif args.official_command == "enable":
                _print_json(enable_official())
            elif args.official_command == "disable":
                _print_json(disable_official())
            elif args.official_command == "uninstall":
                _print_json(uninstall_official())
            else:
                raise RuntimeError(f"unhandled official command: {args.official_command}")
            return
        if args.command == "maintainer":
            key_result = initialize_signing_key(args.key_file)
            if args.maintainer_command == "init-signing-key":
                result: dict[str, Any] = dict(key_result)
                if args.trust_output is not None:
                    result["trust"] = export_trust_store(
                        args.trust_output,
                        key_path=args.key_file,
                    )
                _print_json(result)
                return
            if args.maintainer_command == "sign-catalog":
                result = {
                    **key_result,
                    **sign_catalog_file(
                        args.catalog,
                        signature_path=args.signature_output,
                        key_path=args.key_file,
                    ),
                }
                if args.trust_output is not None:
                    result["trust"] = export_trust_store(
                        args.trust_output,
                        key_path=args.key_file,
                    )
                _print_json(result)
                return
            raise RuntimeError(f"unhandled maintainer command: {args.maintainer_command}")
        if args.command == "private":
            if args.private_command == "add":
                _print_json(
                    add_private_document(
                        args.source,
                        title=args.title,
                        document_type=args.document_type,
                        issuer=args.issuer,
                        effective_from=args.effective_from,
                        effective_to=args.effective_to,
                        confirm_no_case_data=args.confirm_no_case_data,
                        pdf_fallback=args.pdf_fallback,
                        allow_needs_ocr=args.allow_needs_ocr,
                    )
                )
                return
            if args.private_command == "delete":
                _print_json(
                    delete_private_document(
                        args.document_id,
                        pdf_fallback=args.pdf_fallback,
                        allow_needs_ocr=args.allow_needs_ocr,
                    )
                )
                return
            if args.private_command in {"list", "status"}:
                _print_json(list_private_documents())
                return
            database = resolve_private_database(explicit_db=getattr(args, "db", None))
            with DeepLaw(database, expected_scope="user_private") as law:
                if args.private_command == "search":
                    response = law.search(
                        SearchRequest(
                            query=args.query,
                            purpose=args.purpose,
                            as_of=args.as_of,
                            limit=args.limit,
                            max_chars=args.max_chars,
                            document_types=tuple(args.document_type),
                        )
                    )
                    _print_json(response.to_dict())
                    return
                if args.private_command == "get":
                    _print_json(law.get(args.segment_id, max_chars=args.max_chars))
                    return
                if args.private_command == "verify":
                    _print_json(law.verify(args.segment_id, args.receipt_id))
                    return
            raise RuntimeError(f"unhandled private command: {args.private_command}")

        database = resolve_active_database(explicit_db=getattr(args, "db", None))
        if args.command == "eval":
            report = evaluate_file(database, args.cases, limit=args.limit)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(response_json(report) + "\n", encoding="utf-8")
            _print_json(report)
            return
        if args.command == "doctor":
            with DeepLaw(database, expected_scope="official") as law:
                info = law.release_info()
            info["database"] = str(database)
            _print_json(info)
            return
        with DeepLaw(database, expected_scope="official") as law:
            if args.command == "search":
                response = law.search(
                    SearchRequest(
                        query=args.query,
                        purpose=args.purpose,
                        as_of=args.as_of,
                        limit=args.limit,
                        max_chars=args.max_chars,
                        document_types=tuple(args.document_type),
                    )
                )
                _print_json(response.to_dict())
                return
            if args.command == "get":
                _print_json(law.get(args.segment_id, max_chars=args.max_chars))
                return
            if args.command == "verify":
                if args.segment_id or args.receipt_id:
                    if not args.segment_id or not args.receipt_id:
                        raise ValueError("--segment-id and --receipt-id must be provided together")
                    _print_json(law.verify(args.segment_id, args.receipt_id))
                else:
                    _print_json(
                        {
                            "valid": True,
                            "release": law.release_info(),
                            "database_sha256": database_sha256(database),
                        }
                    )
                return
        raise RuntimeError(f"unhandled command: {args.command}")
    except (FileNotFoundError, KeyError, OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"deeplaw: {error}", file=sys.stderr)
        raise SystemExit(2) from error
