from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from deeplaw import __version__
from deeplaw.document_engine import extract_pdf_page_range
from deeplaw.document_engine_models import verify_installed_models
from deeplaw.util import sha256_bytes, sha256_file

SCHEMA_VERSION = "deeplaw.document-engine-actual-pdf-diagnostic/v1"
FIXTURE_TEXT = (
    "DeepLaw actual PDF extraction diagnostic.\n"
    "The local pipeline preserves verifiable source text and page order."
)
IMPLEMENTATION_PATHS = (
    "benchmarks/release/run_document_engine_actual_pdf.py",
    "src/deeplaw/document_engine.py",
    "src/deeplaw/document_engine_cli.py",
    "src/deeplaw/document_engine_models.py",
)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _make_fixture(path: Path) -> None:
    document = canvas.Canvas(
        str(path),
        pagesize=A4,
        invariant=1,
        pageCompression=0,
    )
    document.setTitle("DeepLaw actual PDF extraction diagnostic")
    document.setFont("Helvetica", 12)
    for index, line in enumerate(FIXTURE_TEXT.splitlines()):
        document.drawString(72, 760 - index * 24, line)
    document.showPage()
    document.save()


def run(repository: Path, *, timeout_seconds: float) -> dict[str, Any]:
    model = verify_installed_models()
    with tempfile.TemporaryDirectory(prefix="deeplaw-actual-pdf-") as temporary:
        pdf = Path(temporary) / "actual-pdf-diagnostic.pdf"
        _make_fixture(pdf)
        started = time.perf_counter()
        extracted = extract_pdf_page_range(
            pdf,
            start_page=1,
            end_page=1,
            timeout_seconds=timeout_seconds,
            method="txt",
            backend="pipeline",
            language="en",
        )
        elapsed = time.perf_counter() - started
        output_text = "\n".join(block.text for block in extracted.blocks)
        expected_observed = all(
            phrase.casefold() in output_text.casefold()
            for phrase in (
                "DeepLaw actual PDF extraction diagnostic",
                "verifiable source text and page order",
            )
        )
        if not extracted.blocks or len(extracted.pages) != 1 or not expected_observed:
            raise RuntimeError("actual PDF extraction did not preserve the fixture evidence")
        fixture = {
            "generator": "reportlab",
            "generator_version": version("reportlab"),
            "source_text_sha256": sha256_bytes(FIXTURE_TEXT.encode("utf-8")),
            "pdf_sha256": sha256_file(pdf),
            "pdf_bytes": pdf.stat().st_size,
            "page_count": 1,
        }

    implementation_files = {
        relative: sha256_file(repository / relative)
        for relative in IMPLEMENTATION_PATHS
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "claim_ineligibility_reason": (
            "single synthetic PDF in one local environment; not a frozen release, "
            "real-document corpus, or cross-platform gate"
        ),
        "candidate": {
            "candidate_line": "0.7.0-unreleased",
            "package_version": __version__,
            "commit": _git(repository, "rev-parse", "HEAD"),
            "worktree_dirty": bool(_git(repository, "status", "--porcelain")),
            "implementation_files": implementation_files,
        },
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "engine": {
            "name": extracted.engine,
            "version": extracted.engine_version,
            "output_schema": extracted.output_schema,
            "configuration": list(extracted.configuration),
        },
        "models": {
            "verified": True,
            "repository": model["repository"],
            "revision": model["revision"],
            "manifest_sha256": model["manifest_sha256"],
            "file_count": model["file_count"],
            "total_bytes": model["total_bytes"],
            "network_during_ingest": model["network_during_ingest"],
        },
        "fixture": fixture,
        "result": {
            "success": True,
            "elapsed_seconds": elapsed,
            "page_count": len(extracted.pages),
            "block_count": len(extracted.blocks),
            "block_types": sorted({block.type for block in extracted.blocks}),
            "output_characters": len(output_text),
            "output_text_sha256": sha256_bytes(output_text.encode("utf-8")),
            "expected_text_observed": expected_observed,
        },
        "limitations": [
            "The fixture is a generated text-only one-page PDF.",
            "It does not cover OCR, tables, figures, damaged files, or multilingual layout.",
            "A single-environment synthetic diagnostic cannot become "
            "competitive-claim evidence.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the pinned document engine against a real generated PDF byte stream."
    )
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")
    try:
        report = run(
            arguments.repository.resolve(),
            timeout_seconds=arguments.timeout_seconds,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
