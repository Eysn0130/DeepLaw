from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import __version__
from .evaluate import evaluate_file
from .ingest import build_release
from .mcp_server import run_mcp
from .models import SearchRequest
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
        choices=("off", "vision-consensus"),
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

    mcp = commands.add_parser("mcp", help="Run the read-only MCP server")
    mcp.add_argument("--transport", choices=("stdio",), default="stdio")
    mcp.add_argument(
        "--stdio",
        action="store_true",
        help="Use stdio transport (explicit alias for host plugin manifests)",
    )

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

        database = resolve_active_database(explicit_db=getattr(args, "db", None))
        if args.command == "eval":
            report = evaluate_file(database, args.cases, limit=args.limit)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(response_json(report) + "\n", encoding="utf-8")
            _print_json(report)
            return
        if args.command == "doctor":
            with DeepLaw(database) as law:
                info = law.release_info()
            info["database"] = str(database)
            _print_json(info)
            return
        with DeepLaw(database) as law:
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
