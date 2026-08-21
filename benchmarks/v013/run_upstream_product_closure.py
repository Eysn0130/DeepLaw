"""Run the named upstream product-closure task through public DeepLaw seams.

This is a source-free development diagnostic.  It does not invoke a model, read a
credential, update qualification state, or author a Gate result.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

try:
    import resource
except ImportError:  # pragma: no cover - exercised by native Windows collection
    resource = None  # type: ignore[assignment]

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY = Path(__file__).resolve().parents[2]
MAX_PROVIDER_BYTES = 65_536
MAX_SCALE = 100_000
EXPENSIVE_SCALE_THRESHOLD = 10_000
PACKET_SIZE = 100
DEFAULT_WARM_ITERATIONS = 5
OWNERSHIP_MANIFEST = Path(".deeplaw/derived/tree/living-wiki-manifest.json")
V3_MANIFEST = Path(".deeplaw/derived/wiki/v3/manifest.json")
NOT_EXECUTED = (
    "real crash/kill interruption injection and recovery",
    "positive OCR critical-token consensus and human review task",
    "arbitrary semantic wrong-merge correctness against independent Gold",
    "native Host duplicate/distractor outcome and actual token comparison",
    "effective-date/exception/proviso/cross-reference/wrong-version/false-Authority task",
    "real Codex Host/model task",
    "real OpenCode Host/model task",
    "Obsidian Desktop UI behavior",
    "signed Legal Pack qualification",
    "3 OS and required Python matrix",
    "reproducible wheel/sdist and supply-chain qualification",
    "Human Gold or legal attestation",
    "isolated external or commercial scoring",
)


class DiagnosticFailure(RuntimeError):
    """Closed, path-free failure raised by one public task."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mapping_contains_key(value: object, keys: set[str]) -> bool:
    """Return whether a nested JSON value exposes one forbidden field name."""

    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).replace("-", "_").replace(" ", "_").casefold()
            if normalized in keys or _mapping_contains_key(nested, keys):
                return True
        return False
    if isinstance(value, list):
        return any(_mapping_contains_key(item, keys) for item in value)
    return False


def _provider_boundary_receipt(provider: dict[str, Any]) -> dict[str, Any]:
    """Prove local trace/audit fields did not cross the Provider projection."""

    forbidden_keys = {
        "query_trace",
        "querytrace",
        "canonical_ledger",
        "canonicalledger",
        "audit_head",
        "ledger_head",
    }
    query_trace_in_provider = _mapping_contains_key(provider, {"query_trace", "querytrace"})
    canonical_ledger_in_provider = _mapping_contains_key(
        provider,
        {"canonical_ledger", "canonicalledger", "audit_head", "ledger_head"},
    )
    if query_trace_in_provider or canonical_ledger_in_provider:
        raise DiagnosticFailure("Provider projection exposed local Query Trace or Canonical Ledger")
    return {
        "query_trace_in_provider": query_trace_in_provider,
        "canonical_ledger_in_provider": canonical_ledger_in_provider,
        "provider_internal_surface_leak": False,
        "forbidden_provider_keys": sorted(forbidden_keys),
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_adjacent_checksums(output: Path) -> Path:
    """Write a conventional sibling SHA256SUMS inventory without self-reference."""

    checksum_path = output.parent / "SHA256SUMS"
    if output.name == checksum_path.name:
        raise DiagnosticFailure("Report output cannot also be the SHA256SUMS inventory")
    if checksum_path.exists() or checksum_path.is_symlink():
        raise DiagnosticFailure("Adjacent SHA256SUMS inventory already exists")
    if output.is_symlink() or not output.is_file():
        raise DiagnosticFailure("Report output is missing or unsafe for checksum inventory")
    lines = [f"{_sha256_file(output)}  {output.name}"]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_path


def _open_file_count() -> int:
    """Return the current process file-descriptor estimate when supported."""

    try:
        return len(os.listdir("/dev/fd"))
    except OSError:
        return -1


def _percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(ratio * len(ordered)) - 1)))
    return round(ordered[index] * 1000, 3)


def _child_environment(root: Path) -> dict[str, str]:
    temporary = root / "tmp"
    owner_home = root / "owner-home"
    temporary.mkdir(parents=True, exist_ok=True)
    owner_home.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUTF8": "1",
        "TMPDIR": str(temporary),
        "DEEPLAW_HOME": str(owner_home),
    }
    for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def _run_process(
    arguments: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    input_text: str | None = None,
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    started = time.perf_counter()
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    completed.elapsed_seconds = time.perf_counter() - started  # type: ignore[attr-defined]
    return completed


def _run_cli(
    environment: dict[str, str],
    *arguments: str,
    input_text: str | None = None,
) -> tuple[dict[str, Any], float]:
    completed = _run_process(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=REPOSITORY,
        environment=environment,
        input_text=input_text,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else ""
        for candidate in (str(REPOSITORY), *arguments):
            if "/" in candidate or "\\" in candidate:
                detail = detail.replace(candidate, "<path>")
        step = " ".join(arguments[:3])
        raise DiagnosticFailure(
            f"public CLI step {step} failed with exit code {completed.returncode}: {detail[:500]}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise DiagnosticFailure("public CLI step returned invalid JSON") from error
    if not isinstance(value, dict):
        raise DiagnosticFailure("public CLI step did not return a JSON object")
    return value, float(completed.elapsed_seconds)  # type: ignore[attr-defined]


def _expect_cli_permission_denied(
    environment: dict[str, str],
    *arguments: str,
) -> None:
    completed = _run_process(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=REPOSITORY,
        environment=environment,
    )
    if completed.returncode == 0 or (
        "The Knowledge OS operation is outside its granted boundary."
        not in completed.stderr
    ):
        raise DiagnosticFailure("Pending Source content did not fail closed")


def _git(
    environment: dict[str, str],
    repository: Path,
    *arguments: str,
) -> str:
    completed = _run_process(
        ["git", *arguments],
        cwd=repository,
        environment=environment,
    )
    if completed.returncode != 0:
        raise DiagnosticFailure(f"Git fixture step failed with exit code {completed.returncode}")
    return completed.stdout.strip()


def _repository_fixture(root: Path, environment: dict[str, str]) -> tuple[Path, Path]:
    repository = root / "repository"
    repository.mkdir()
    _git(environment, repository, "init", "-q")
    tracked = repository / "tracked.txt"
    tracked.write_text("stable\n", encoding="utf-8")
    _git(environment, repository, "add", "tracked.txt")
    _git(
        environment,
        repository,
        "-c",
        "user.name=DeepLaw Diagnostic",
        "-c",
        "user.email=deeplaw@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    child = root / "child-worktree"
    _git(environment, repository, "worktree", "add", "-q", "--detach", str(child), "HEAD")
    return repository, child


def _enable_grant(
    environment: dict[str, str],
    vault: Path,
    *,
    writer: str,
    operations: tuple[str, ...] = (),
    profile: str | None = None,
    max_objects: int = 100_000,
) -> str:
    arguments = [
        "knowledge",
        "sink",
        "enable",
        "--vault",
        str(vault),
        "--writer-id",
        writer,
        "--max-request-bytes",
        str(320 * 1024),
        "--max-mutations-per-minute",
        "120",
        "--max-objects",
        str(max_objects),
    ]
    if profile is not None:
        arguments.extend(("--profile", profile))
    for operation in operations:
        arguments.extend(("--operation", operation))
    result, _ = _run_cli(environment, *arguments)
    grant_id = result.get("grant_id")
    if not isinstance(grant_id, str):
        raise DiagnosticFailure("Sink grant did not return an opaque grant identity")
    return grant_id


def _apply_sink_request(
    environment: dict[str, str],
    vault: Path,
    *,
    grant_id: str,
    request: dict[str, Any],
    request_path: Path,
) -> tuple[dict[str, Any], float]:
    request_path.write_text(_canonical(request), encoding="utf-8")
    response, elapsed = _run_cli(
        environment,
        "knowledge",
        "sink",
        "apply",
        "--vault",
        str(vault),
        "--grant-id",
        grant_id,
        "--request",
        str(request_path),
    )
    result = response.get("result")
    boundary = response.get("boundary")
    if (
        not isinstance(result, dict)
        or not isinstance(boundary, dict)
        or boundary.get("case_data_allowed") is not False
    ):
        raise DiagnosticFailure("Public Knowledge Sink response lost its closed boundary")
    return result, elapsed


def _compilation_plan(packet: dict[str, Any]) -> dict[str, Any]:
    fragments = packet.get("fragments")
    if not isinstance(fragments, list) or not fragments:
        raise DiagnosticFailure("Compilation Packet did not contain fragments")
    actions: list[dict[str, Any]] = []
    covered: list[str] = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            raise DiagnosticFailure("Compilation Packet fragment is invalid")
        ordinal = int(fragment["ordinal"])
        fragment_id = str(fragment["fragment_id"])
        covered.append(fragment_id)
        actions.append(
            {
                "action": "create",
                "kind": "claim",
                "semantic_key": (f"upstream-closure:{packet['source_revision_id']}:{ordinal:06d}"),
                "knowledge_id": None,
                "expected_revision_id": None,
                "title": f"Scale claim {ordinal:06d}",
                "body": str(fragment["text"]),
                "aliases": [],
                "epistemic_state": "supported",
                "source_refs": [
                    {
                        "source_revision_id": packet["source_revision_id"],
                        "fragment_id": fragment_id,
                        "locator": fragment["locator"],
                        "quote_sha256": fragment["text_sha256"],
                    }
                ],
                "assertion": None,
                "tags": ["upstream-closure-development"],
                "valid_from": None,
                "valid_to": None,
                "applicability": {
                    "description": "Synthetic development scale fixture.",
                    "scopes": [],
                    "conditions": [],
                    "exclusions": [],
                },
                "synthesis_inputs": None,
                "reason": "Create one source-bound development claim.",
            }
        )
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": actions,
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [],
        "coverage": {
            "packet_fragment_count": len(covered),
            "covered_fragment_ids": covered,
            "omitted_fragment_ids": [],
            "ratio": 1.0,
            "completeness": "complete",
        },
        "skipped_fragments": [],
        "warnings": [],
    }


def _compile_source_revision(
    environment: dict[str, str],
    vault: Path,
    *,
    source_revision_id: str,
    grant_id: str,
    plan_root: Path,
) -> dict[str, Any]:
    begun, begin_seconds = _run_cli(
        environment,
        "knowledge",
        "compile",
        "begin",
        "--vault",
        str(vault),
        "--grant-id",
        grant_id,
        "--source-revision-id",
        source_revision_id,
        "--host-identity",
        "deeplaw-upstream-product-closure-development",
        "--packet-max-fragments",
        str(PACKET_SIZE),
        "--confirm-no-case-data",
    )
    run_id = str(begun["compilation_run_id"])
    staged = 0
    packet_count = 0
    packet_seconds = 0.0
    stage_seconds = 0.0
    first_fragment: dict[str, str] | None = None
    while True:
        packet, packet_elapsed = _run_cli(
            environment,
            "knowledge",
            "compile",
            "packet",
            "--vault",
            str(vault),
            "--grant-id",
            grant_id,
            "--run-id",
            run_id,
        )
        packet_seconds += packet_elapsed
        if packet.get("complete") is True:
            break
        packet_fragments = packet.get("fragments")
        if not isinstance(packet_fragments, list) or not packet_fragments:
            raise DiagnosticFailure("Compilation Packet omitted its source fragments")
        if first_fragment is None:
            fragment = packet_fragments[0]
            if not isinstance(fragment, dict):
                raise DiagnosticFailure("Compilation Packet source fragment is invalid")
            first_fragment = {
                "fragment_id": str(fragment["fragment_id"]),
                "locator": str(fragment["locator"]),
                "text_sha256": str(fragment["text_sha256"]),
            }
        plan = _compilation_plan(packet)
        plan_path = plan_root / f"plan-{packet_count:06d}.json"
        plan_path.write_text(_canonical(plan), encoding="utf-8")
        _, elapsed = _run_cli(
            environment,
            "knowledge",
            "compile",
            "stage",
            "--vault",
            str(vault),
            "--grant-id",
            grant_id,
            "--run-id",
            run_id,
            "--plan",
            str(plan_path),
            "--confirm-no-case-data",
        )
        stage_seconds += elapsed
        staged += len(plan["object_actions"])
        packet_count += 1
    validation, validate_seconds = _run_cli(
        environment,
        "knowledge",
        "compile",
        "validate",
        "--vault",
        str(vault),
        "--grant-id",
        grant_id,
        "--run-id",
        run_id,
        "--confirm-no-case-data",
    )
    if validation.get("valid") is not True:
        raise DiagnosticFailure("Compilation validation did not pass")
    commit, commit_seconds = _run_cli(
        environment,
        "knowledge",
        "compile",
        "commit",
        "--vault",
        str(vault),
        "--grant-id",
        grant_id,
        "--run-id",
        run_id,
        "--confirm-no-case-data",
    )
    projected, project_seconds = _run_cli(
        environment,
        "knowledge",
        "compile",
        "resume",
        "--vault",
        str(vault),
        "--grant-id",
        grant_id,
        "--run-id",
        run_id,
        "--project",
        "--confirm-no-case-data",
    )
    if first_fragment is None:
        raise DiagnosticFailure("Compilation Run did not expose an exact source fragment")
    return {
        "run_id": run_id,
        "first_correct_action": (
            "Begin one resumable Compilation Run for the exact Source Revision."
        ),
        "public_cli_steps": (2 * packet_count) + 5,
        "public_operation_count": (2 * packet_count) + 5,
        "owner_operation_steps": 5,
        "host_internal_packet_count": packet_count,
        "host_internal_packet_steps": 2 * packet_count,
        "objects_staged": staged,
        "packet_count": packet_count,
        "validation_valid": True,
        "commit_status": commit.get("status"),
        "projection_status": projected.get("status"),
        "process_boundary_recovery": {
            "status": "executed",
            "kind": "persisted_compilation_run_resume",
            "commit_status_before_resume": commit.get("status"),
            "resume_status": projected.get("status"),
            "real_crash_or_kill_injection": {
                "status": "not_executed",
                "reason": "A diagnostic child was not forcibly terminated.",
            },
        },
        "first_fragment": first_fragment,
        "phase_elapsed_seconds": {
            "begin": round(begin_seconds, 6),
            "packet": round(packet_seconds, 6),
            "stage": round(stage_seconds, 6),
            "validate": round(validate_seconds, 6),
            "commit": round(commit_seconds, 6),
            "project": round(project_seconds, 6),
        },
        "elapsed_seconds": round(
            begin_seconds
            + packet_seconds
            + stage_seconds
            + validate_seconds
            + commit_seconds
            + project_seconds,
            6,
        ),
    }


def _write_docx_fixture(path: Path, text: str) -> None:
    """Create the smallest public-test-compatible DOCX fixture with exact caller bytes."""

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p><w:sectPr/></w:body>"
        "</w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)


def _write_native_pdf_fixture(path: Path) -> bool:
    """Use the already-installed public test PDF generator when available."""

    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path))
    lines = [
        "Native PDF fixture exact bytes and source identity are retained.",
        "This source-free development paragraph is intentionally long enough for the",
        "deterministic native-text PDF quality gate and locator extraction.",
    ]
    for index in range(5):
        for line in lines:
            pdf.drawString(48, 780 - ((index * len(lines)) + lines.index(line)) * 18, line)
    pdf.showPage()
    pdf.save()
    return True


def _expect_public_cli_failure(
    environment: dict[str, str],
    *arguments: str,
    stderr_fragment: str,
) -> None:
    completed = _run_process(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=REPOSITORY,
        environment=environment,
    )
    if completed.returncode == 0 or stderr_fragment not in completed.stderr:
        raise DiagnosticFailure("Public fail-closed probe did not return the expected rejection")


def _review_source(
    environment: dict[str, str],
    vault: Path,
    source_id: str,
) -> tuple[dict[str, Any], float]:
    manifest, manifest_seconds = _run_cli(
        environment,
        "knowledge",
        "review",
        "manifest",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    manifest_sha256 = manifest.get("review_manifest_sha256")
    if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
        raise DiagnosticFailure("Source review manifest did not return an exact SHA-256")
    approved, approve_seconds = _run_cli(
        environment,
        "knowledge",
        "review",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        "--review-manifest-sha256",
        manifest_sha256,
        "--reviewer-id",
        "upstream-closure-development-review",
        "--reason",
        "Synthetic source-free fixture review for exact read seam.",
        "--confirm-reviewed",
    )
    if not isinstance(approved, dict):
        raise DiagnosticFailure("Source review approval did not return a receipt")
    return manifest, manifest_seconds + approve_seconds


def _evidence_format_lane(root: Path, environment: dict[str, str]) -> dict[str, Any]:
    """Register exact bytes for public text/document formats, then read after review."""

    root.mkdir(parents=True)
    vault = root / "vault"
    _run_cli(
        environment,
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "upstream-closure-evidence-formats",
        "--scope",
        "project",
    )
    fixtures: dict[str, tuple[str, bytes | None]] = {
        "markdown": (
            "evidence.md",
            b"# Evidence Markdown\nThe exact Markdown bytes are source-free.\n",
        ),
        "html": (
            "evidence.html",
            b"<html><body><h1>Evidence HTML</h1>"
            b"<p>The exact HTML bytes are source-free.</p></body></html>",
        ),
        "docx": ("evidence.docx", None),
        "native_text_pdf": ("evidence.pdf", None),
    }
    docx_path = root / "evidence.docx"
    _write_docx_fixture(
        docx_path,
        "Evidence DOCX exact bytes are source-free and long enough for deterministic extraction.",
    )
    fixtures["docx"] = ("evidence.docx", docx_path.read_bytes())
    pdf_path = root / "evidence.pdf"
    pdf_available = _write_native_pdf_fixture(pdf_path)
    fixtures["native_text_pdf"] = (
        "evidence.pdf",
        pdf_path.read_bytes() if pdf_available else None,
    )

    format_results: dict[str, Any] = {}
    public_operation_count = 1
    for name, (filename, fixture_bytes) in fixtures.items():
        if fixture_bytes is None:
            format_results[name] = {
                "status": "not_executed",
                "reason": "The installed PDF generation dependency was unavailable.",
            }
            continue
        source_path = root / filename
        if name in {"markdown", "html"}:
            source_path.write_bytes(fixture_bytes)
        caller_sha256 = _sha256_bytes(fixture_bytes)
        added, add_seconds = _run_cli(
            environment,
            "knowledge",
            "source",
            "add",
            "--vault",
            str(vault),
            "--source",
            str(source_path),
            "--typed-extraction",
            "off",
            "--pdf-fallback",
            "off",
            "--confirm-no-case-data",
        )
        source_card = added.get("source")
        compiler = added.get("compiler")
        if not isinstance(source_card, dict) or not isinstance(compiler, dict):
            raise DiagnosticFailure(f"{name} source add omitted its source/compiler receipt")
        source_id = str(source_card["source_id"])
        if compiler.get("source_sha256") != caller_sha256:
            raise DiagnosticFailure(f"{name} source add did not bind caller bytes")
        verified, verify_seconds = _run_cli(
            environment,
            "knowledge",
            "source",
            "verify",
            "--vault",
            str(vault),
            "--source-id",
            source_id,
        )
        if verified.get("valid") is not True:
            raise DiagnosticFailure(f"{name} public source verification failed")
        manifest, review_seconds = _review_source(environment, vault, source_id)
        read, read_seconds = _run_cli(
            environment,
            "knowledge",
            "source",
            "get",
            "--vault",
            str(vault),
            "--source-id",
            source_id,
        )
        read_card = read.get("source")
        if (
            not isinstance(read_card, dict)
            or read_card.get("content_sha256") != caller_sha256
            or read.get("write_performed") is not False
        ):
            raise DiagnosticFailure(f"{name} source get did not preserve exact caller bytes")
        compiler_grant = _enable_grant(
            environment,
            vault,
            writer=f"upstream-closure-evidence-{name}",
            profile="compiler",
            max_objects=10,
        )
        compilation = _compile_source_revision(
            environment,
            vault,
            source_revision_id=str(added["identity"]["source_revision_id"]),
            grant_id=compiler_grant,
            plan_root=root,
        )
        fragment_id = str(compilation["first_fragment"]["fragment_id"])
        fragment_read, fragment_seconds = _run_cli(
            environment,
            "knowledge",
            "source",
            "fragment",
            "--vault",
            str(vault),
            "--fragment-id",
            fragment_id,
            "--max-chars",
            "12000",
        )
        fragment_card = fragment_read.get("fragment")
        if (
            not isinstance(fragment_card, dict)
            or fragment_read.get("write_performed") is not False
            or fragment_card.get("source_revision_id")
            != added["identity"]["source_revision_id"]
            or not isinstance(fragment_card.get("locator"), str)
            or not isinstance(fragment_card.get("text_sha256"), str)
        ):
            raise DiagnosticFailure(f"{name} fragment read omitted its locator identity")
        format_results[name] = {
            "status": "executed",
            "source_id": source_id,
            "source_revision_id": added["identity"]["source_revision_id"],
            "source_sha256": caller_sha256,
            "verify_valid": verified["valid"],
            "read_content_sha256": read_card["content_sha256"],
            "read_write_performed": read["write_performed"],
            "review_manifest_sha256": manifest["review_manifest_sha256"],
            "fragment_id": fragment_id,
            "fragment_locator": fragment_card["locator"],
            "fragment_text_sha256": fragment_card["text_sha256"],
            "document_version_fragment_identity": True,
            "fragment_read_write_performed": fragment_read["write_performed"],
            "host_internal_packet_count": compilation["host_internal_packet_count"],
            "elapsed_seconds": round(
                add_seconds
                + verify_seconds
                + review_seconds
                + read_seconds
                + compilation["elapsed_seconds"]
                + fragment_seconds,
                6,
            ),
        }
        public_operation_count += 7 + int(compilation["public_operation_count"])

    ocr_probe: dict[str, Any]
    if pdf_available:
        scanned = root / "scanned.pdf"
        # A blank page has no native text.  The public seam must stop at the OCR-needed gate.
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            ocr_probe = {
                "status": "not_executed",
                "reason": "The installed PDF generation dependency was unavailable.",
            }
        else:
            pdf = canvas.Canvas(str(scanned))
            pdf.showPage()
            pdf.save()
            _expect_public_cli_failure(
                environment,
                "knowledge",
                "source",
                "add",
                "--vault",
                str(vault),
                "--source",
                str(scanned),
                "--typed-extraction",
                "off",
                "--pdf-fallback",
                "off",
                "--confirm-no-case-data",
                stderr_fragment="PDF text quality gate failed",
            )
            ocr_probe = {
                "status": "executed",
                "kind": "blank_or_scanned_pdf",
                "expected": "fail_closed_ocr_needed",
                "positive_ocr": "not_executed",
            }
            public_operation_count += 1
    else:
        ocr_probe = {
            "status": "not_executed",
            "reason": "The installed PDF generation dependency was unavailable.",
        }
    return {
        "status": "executed",
        "format_results": format_results,
        "ocr_needed_fail_closed": ocr_probe,
        "fragment_identity_status": "executed_per_format_after_review",
        "public_operation_count": public_operation_count,
        "host_internal_packet_count": sum(
            int(result.get("host_internal_packet_count", 0))
            for result in format_results.values()
            if isinstance(result, dict)
        ),
    }


def _source_successor_lane(root: Path, environment: dict[str, str]) -> dict[str, Any]:
    """Exercise stable aliases, an exact successor, and ambiguous parallel successors."""

    root.mkdir(parents=True)
    vault = root / "vault"
    _run_cli(
        environment,
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "upstream-closure-source-successor",
        "--scope",
        "project",
    )
    original = root / "policy.md"
    original.write_text(
        "# Policy\nUse the source-free Atlas review path.\n",
        encoding="utf-8",
    )
    first, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(original),
        "--typed-extraction",
        "off",
        "--pdf-fallback",
        "off",
        "--confirm-no-case-data",
    )
    first_source = first.get("source")
    if not isinstance(first_source, dict):
        raise DiagnosticFailure("Initial Source successor fixture omitted its identity")
    _review_source(environment, vault, str(first_source["source_id"]))

    renamed = root / "renamed-policy.md"
    renamed.write_bytes(original.read_bytes())
    second, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "update",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--source",
        str(renamed),
        "--typed-extraction",
        "off",
        "--pdf-fallback",
        "off",
        "--confirm-no-case-data",
    )
    second_source = second.get("source")
    if not isinstance(second_source, dict):
        raise DiagnosticFailure("Source successor update omitted its identity")
    if second_source.get("previous_source_id") != first_source["source_id"]:
        raise DiagnosticFailure("Source successor did not bind its previous revision")
    active_before, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--active",
    )
    latest, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--latest",
    )
    diff, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "diff",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--latest",
    )
    if (
        active_before.get("source_id") != first_source["source_id"]
        or latest.get("source_id") != second_source["source_id"]
        or latest.get("status") != "pending"
        or diff.get("unchanged_count") != 1
    ):
        raise DiagnosticFailure("Source successor selectors lost their active/latest boundary")

    _review_source(environment, vault, str(second_source["source_id"]))
    active_from_old_alias, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--active",
    )
    verified_from_new_alias, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "verify",
        "--vault",
        str(vault),
        "--alias",
        "renamed-policy.md",
        "--active",
    )
    if (
        active_from_old_alias.get("source_id") != second_source["source_id"]
        or active_from_old_alias.get("logical_path") != "renamed-policy.md"
        or verified_from_new_alias.get("valid") is not True
    ):
        raise DiagnosticFailure("Historical/current aliases did not resolve the exact successor")

    for index in (1, 2):
        renamed.write_text(
            f"# Policy\nCandidate {index} remains pending for explicit review.\n",
            encoding="utf-8",
        )
        _run_cli(
            environment,
            "knowledge",
            "source",
            "update",
            "--vault",
            str(vault),
            "--alias",
            "renamed-policy.md",
            "--source",
            str(renamed),
            "--typed-extraction",
            "off",
            "--pdf-fallback",
            "off",
            "--confirm-no-case-data",
        )
    _expect_public_cli_failure(
        environment,
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "renamed-policy.md",
        "--latest",
        stderr_fragment="multiple pending successors",
    )
    return {
        "status": "executed",
        "stable_logical_source_identity": True,
        "historical_alias_resolved": True,
        "current_alias_resolved": True,
        "successor_previous_source_id": second_source["previous_source_id"],
        "active_source_id_before_review": active_before["source_id"],
        "active_source_id_after_review": active_from_old_alias["source_id"],
        "latest_pending_source_id": latest["source_id"],
        "unchanged_fragment_count": diff["unchanged_count"],
        "parallel_pending_successor_rejection": {
            "status": "executed",
            "kind": "ambiguous_successor_not_arbitrary_semantic_merge_judgment",
            "wrong_state_admission_count": 0,
        },
        "public_operation_count": 15,
        "host_internal_packet_count": 0,
    }


def _file_inventory(root: Path) -> dict[str, Any]:
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    directories = [path for path in root.rglob("*") if path.is_dir() and path != root]
    symlinks = [path for path in root.rglob("*") if path.is_symlink()]
    markdown_files = [path for path in files if path.suffix.lower() == ".md"]
    return {
        "file_count": len(files),
        "directory_count": len(directories),
        "scanned_entries": len(directories) + len(files) + len(symlinks),
        "symlink_count": len(symlinks),
        "storage_bytes": sum(path.stat().st_size for path in files),
        "markdown_file_count": len(markdown_files),
        "open_file_descriptors": _open_file_count(),
    }


def _manifest_inventory(root: Path, manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files")
    wiki_files = (
        [
            item["path"]
            for item in files
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        ]
        if isinstance(files, list)
        else []
    )
    path_set = set(wiki_files)
    object_directories = {
        "claims",
        "comparisons",
        "concepts",
        "decisions",
        "entities",
        "events",
        "experiences",
        "memory",
        "preferences",
        "procedures",
        "skills",
        "syntheses",
    }
    managed_paths = [root / str(path) for path in path_set]
    managed_regular = [path for path in managed_paths if path.is_file() and not path.is_symlink()]
    return {
        "managed_file_count": len(path_set),
        "managed_regular_file_count": len(managed_regular),
        "managed_missing_or_unsafe_count": len(path_set) - len(managed_regular),
        "managed_storage_bytes": sum(path.stat().st_size for path in managed_regular),
        "markdown_managed_count": len([path for path in path_set if str(path).endswith(".md")]),
        "canvas_file_count": len([path for path in path_set if str(path).endswith(".canvas")]),
        "wiki_object_markdown_count": len(
            [
                path
                for path in path_set
                if str(path).startswith("wiki/")
                and str(path).count("/") == 2
                and not str(path).endswith("/index.md")
                and str(path).split("/", 2)[1] in object_directories
                and str(path).endswith(".md")
            ]
        ),
        "wiki_source_markdown_count": len(
            [path for path in path_set if str(path).startswith("wiki/sources/")]
        ),
        "wiki_index_markdown_count": len(
            [
                path
                for path in path_set
                if str(path).startswith("wiki/indexes/") and str(path).endswith(".md")
            ]
        ),
        "wiki_community_markdown_count": len(
            [
                path
                for path in path_set
                if str(path).startswith("wiki/communities/") and str(path).endswith(".md")
            ]
        ),
        "manifest_sha256": _sha256_file(manifest_path),
        "manifest_bytes": manifest_path.stat().st_size,
    }


def _manifest_descriptor(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        return {"status": "missing_or_unsafe"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    return {
        "status": "present",
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "record_count": len(records) if isinstance(records, list) else None,
    }


def _v3_inventory(root: Path) -> dict[str, Any]:
    path = root / V3_MANIFEST
    top = _manifest_descriptor(path)
    if top.get("status") != "present":
        return {"manifest": top, "components": {}}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    components: dict[str, Any] = {}
    for item in manifest.get("components", []):
        if not isinstance(item, dict):
            continue
        name = item.get("component")
        relative = item.get("manifest_path")
        if not isinstance(name, str) or not isinstance(relative, str):
            continue
        components[name] = {
            **_manifest_descriptor(root / relative),
            "declared_bytes": item.get("byte_size"),
            "declared_sha256": item.get("sha256"),
        }
    return {"manifest": top, "components": components}


def _owned_projection_state(root: Path) -> dict[str, tuple[str, int]]:
    relative_paths = {OWNERSHIP_MANIFEST.as_posix(), V3_MANIFEST.as_posix()}
    ownership_path = root / OWNERSHIP_MANIFEST
    if ownership_path.is_file() and not ownership_path.is_symlink():
        manifest = json.loads(ownership_path.read_text(encoding="utf-8"))
        relative_paths.update(
            str(item["path"])
            for item in manifest.get("files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        )
    v3_path = root / V3_MANIFEST
    if v3_path.is_file() and not v3_path.is_symlink():
        v3 = json.loads(v3_path.read_text(encoding="utf-8"))
        relative_paths.update(
            str(item["manifest_path"])
            for item in v3.get("components", [])
            if isinstance(item, dict) and isinstance(item.get("manifest_path"), str)
        )
    for directory in ("knowledge", "memory", "skills"):
        relative_paths.update(
            path.relative_to(root).as_posix()
            for path in (root / directory).rglob("*.md")
            if path.is_file() and not path.is_symlink()
        )
    result: dict[str, tuple[str, int]] = {}
    for relative in sorted(relative_paths):
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise DiagnosticFailure("DeepLaw-owned projection file is missing or unsafe")
        result[relative] = (_sha256_file(path), path.stat().st_mtime_ns)
    return result


def _owned_projection_hashes(root: Path) -> dict[str, str]:
    return {path: value[0] for path, value in _owned_projection_state(root).items()}


def _artifact_inventory(root: Path, *, owner_file: Path) -> dict[str, Any]:
    ownership_path = root / OWNERSHIP_MANIFEST
    ownership = _manifest_inventory(root, ownership_path)
    canonical_markdown = [
        path
        for directory in ("knowledge", "memory", "skills")
        for path in (root / directory).rglob("*.md")
        if path.is_file() and not path.is_symlink() and path != owner_file
    ]
    owner_files = [
        path
        for directory in ("knowledge", "memory", "skills")
        for path in (root / directory).rglob("*")
        if path.is_file() and not path.is_symlink() and path == owner_file
    ]
    workspace_directories = [
        path
        for directory in ("knowledge", "memory", "skills")
        for path in (root / directory).rglob("*")
        if path.is_dir() and not path.is_symlink()
    ]
    workspace_symlinks = [
        path
        for directory in ("knowledge", "memory", "skills")
        for path in (root / directory).rglob("*")
        if path.is_symlink()
    ]
    cas_root = root / ".deeplaw" / "objects" / "sha256"
    cas_files = (
        [path for path in cas_root.rglob("*") if path.is_file() and not path.is_symlink()]
        if cas_root.is_dir() and not cas_root.is_symlink()
        else []
    )
    wiki_files = [
        path for path in (root / "wiki").rglob("*") if path.is_file() and not path.is_symlink()
    ]
    canvas_files = [
        path
        for path in (root / "canvas").rglob("*.canvas")
        if path.is_file() and not path.is_symlink()
    ]
    v3 = _v3_inventory(root)
    manifest_paths = (
        Path(".deeplaw/manifest.json"),
        Path(".deeplaw/derived/manifest.json"),
        OWNERSHIP_MANIFEST,
        V3_MANIFEST,
    )
    return {
        "canonical_knowledge_markdown_file_count": len(canonical_markdown),
        "canonical_knowledge_markdown_bytes": sum(
            path.stat().st_size for path in canonical_markdown
        ),
        "registered_revision_markdown_file_count": len(canonical_markdown),
        "cas_file_count": len(cas_files),
        "cas_revision_file_count": len(cas_files),
        "cas_revision_bytes": sum(path.stat().st_size for path in cas_files),
        "wiki_file_count": len(wiki_files),
        "wiki_storage_bytes": sum(path.stat().st_size for path in wiki_files),
        "canvas_file_count": len(canvas_files),
        "canvas_storage_bytes": sum(path.stat().st_size for path in canvas_files),
        "workspace_managed_markdown_file_count": len(canonical_markdown),
        "workspace_unmanaged_owner_file_count": len(owner_files),
        "workspace_directory_count": len(workspace_directories),
        "workspace_unsafe_symlink_entry_count": len(workspace_symlinks),
        "ownership_manifest": ownership,
        "v3_page_registry": v3["components"].get("page_registry", {}),
        "v3_link_index": v3["components"].get("link_index", {}),
        "v3_resolver": v3["components"].get("resolver", {}),
        "v3_manifest": v3["manifest"],
        "manifest_bytes_by_path": {
            relative.as_posix(): _manifest_descriptor(root / relative)
            for relative in manifest_paths
        },
    }


def _cold_cli_startup(environment: dict[str, str]) -> float:
    completed = _run_process(
        [sys.executable, "-m", "deeplaw", "--help"],
        cwd=REPOSITORY,
        environment=environment,
    )
    if completed.returncode != 0:
        raise DiagnosticFailure("Cold CLI startup failed")
    return float(completed.elapsed_seconds)  # type: ignore[attr-defined]


def _markdown_hashes(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root).as_posix(): (_sha256_file(path), path.stat().st_mtime_ns)
        for path in root.rglob("*.md")
        if path.is_file() and not path.is_symlink()
    }


def _rss_bytes() -> int | None:
    if resource is None:
        return None
    observed = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(observed if sys.platform == "darwin" else observed * 1024)


async def _run_mcp_task(
    *,
    environment: dict[str, str],
    vault: Path,
    warm_iterations: int = 0,
    warm_query_text: str = "synthetic retention decision",
) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "deeplaw",
            "knowledge",
            "mcp",
            "--stdio",
            "--vault",
            str(vault),
        ],
        cwd=REPOSITORY,
        env=environment,
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        if [tool.name for tool in listed.tools] != ["knowledge_support"]:
            raise DiagnosticFailure("MCP advertisement was not the single read-only tool")
        tool = listed.tools[0]
        schema = tool.inputSchema
        definitions = schema.get("$defs", {})
        operations = sorted(
            str(definitions[name]["properties"]["operation"]["const"])
            for name in ("query", "context", "explain")
        )
        warm_query_timings: list[float] = []
        warm_context_timings: list[float] = []
        warm_provider_bytes: list[int] = []

        query_started = time.perf_counter()
        query = await session.call_tool(
            "knowledge_support",
            {
                "operation": "query",
                "query": "synthetic retention decision",
                "query_plan_version": "6",
            },
        )
        cold_query_seconds = time.perf_counter() - query_started
        if query.isError or not isinstance(query.structuredContent, dict):
            raise DiagnosticFailure("MCP query failed")
        provider = query.structuredContent.get("result")
        if not isinstance(provider, dict):
            raise DiagnosticFailure("MCP query omitted its bounded Provider result")
        receipt = provider.get("receipt")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("receipt_id"), str):
            raise DiagnosticFailure("MCP query omitted its local explain receipt")
        context_started = time.perf_counter()
        context = await session.call_tool(
            "knowledge_support",
            {
                "operation": "context",
                "task": "Resume the synthetic retention decision.",
                "confirm_no_case_data": True,
                "query_plan_version": "6",
            },
        )
        cold_context_seconds = time.perf_counter() - context_started
        explained = await session.call_tool(
            "knowledge_support",
            {"operation": "explain", "receipt_id": receipt["receipt_id"]},
        )
        if context.isError or explained.isError:
            raise DiagnosticFailure("MCP context or explain failed")
        provider_boundary = _provider_boundary_receipt(provider)

        for _ in range(max(0, warm_iterations)):
            warm_query_started = time.perf_counter()
            warm_query = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "query",
                    "query": warm_query_text,
                    "query_plan_version": "6",
                },
            )
            warm_query_timings.append(time.perf_counter() - warm_query_started)
            if warm_query.isError or not isinstance(warm_query.structuredContent, dict):
                raise DiagnosticFailure("Warm MCP query failed")
            warm_query_provider = warm_query.structuredContent.get("result")
            if not isinstance(warm_query_provider, dict):
                raise DiagnosticFailure("Warm MCP query omitted its Provider result")
            _provider_boundary_receipt(warm_query_provider)
            warm_context_started = time.perf_counter()
            warm_context = await session.call_tool(
                "knowledge_support",
                {
                    "operation": "context",
                    "task": warm_query_text,
                    "confirm_no_case_data": True,
                    "query_plan_version": "6",
                },
            )
            warm_context_timings.append(time.perf_counter() - warm_context_started)
            if warm_context.isError:
                raise DiagnosticFailure("Warm MCP context failed")
            warm_query_receipt = warm_query.structuredContent.get("result", {}).get("receipt", {})
            if (
                not isinstance(warm_query_receipt, dict)
                or not isinstance(warm_query_receipt.get("receipt_id"), str)
            ):
                raise DiagnosticFailure("Warm MCP query omitted its local explain receipt")
            for response in (warm_query, warm_context):
                payload = sum(
                    len(item.text.encode("utf-8"))
                    for item in response.content
                    if getattr(item, "type", None) == "text"
                )
                if not 0 < payload <= MAX_PROVIDER_BYTES:
                    raise DiagnosticFailure("Warm MCP Provider payload violated its hard bound")
                warm_provider_bytes.append(payload)
        provider_bytes = [
            len(item.text.encode("utf-8"))
            for response in (query, context)
            for item in response.content
            if getattr(item, "type", None) == "text"
        ]
        if len(provider_bytes) != 2 or max(provider_bytes) > MAX_PROVIDER_BYTES:
            raise DiagnosticFailure("MCP Provider byte accounting exceeded the hard bound")
        return {
            "advertised_operations": operations,
            "first_correct_action": "List the current Provider advertisement before calling it.",
            "provider_operation_steps": 3,
            "public_operation_count": 3,
            "query_status": "executed",
            "context_status": "executed",
            "explain_status": "executed",
            "provider_content_bytes": provider_bytes,
            "max_provider_content_bytes": max(provider_bytes),
            "provider_bytes": {
                "status": "executed",
                "values": provider_bytes,
                "maximum": max(provider_bytes),
                "hard_limit": MAX_PROVIDER_BYTES,
            },
            "cold_query_timing_ms": round(cold_query_seconds * 1000, 3),
            "cold_context_timing_ms": round(cold_context_seconds * 1000, 3),
            "warm_query_timing_ms_p50": _percentile(warm_query_timings, 0.5),
            "warm_query_timing_ms_p95": _percentile(warm_query_timings, 0.95),
            "warm_query_timing_samples": len(warm_query_timings),
            "warm_context_timing_ms_p50": _percentile(warm_context_timings, 0.5),
            "warm_context_timing_ms_p95": _percentile(warm_context_timings, 0.95),
            "warm_context_timing_samples": len(warm_context_timings),
            "provider_content_bytes_warm_max": (
                max(warm_provider_bytes) if warm_provider_bytes else None
            ),
            "native_provider_tokens": "unavailable",
            "actual_provider_tokens": {
                "status": "not_executed",
                "value": None,
                "reason": "No real Host/model usage receipt was available.",
            },
            **provider_boundary,
        }


def _source_and_task_task(root: Path, environment: dict[str, str]) -> dict[str, Any]:
    root.mkdir(parents=True)
    vault = root / "vault"
    repository, child_worktree = _repository_fixture(root, environment)
    initialized, init_seconds = _run_cli(
        environment,
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "upstream-product-closure-development",
        "--scope",
        "project",
    )
    doctor, doctor_seconds = _run_cli(environment, "knowledge", "doctor", "--vault", str(vault))
    if doctor.get("ready") is not True:
        raise DiagnosticFailure("Doctor did not report the synthetic Vault ready")

    source = root / "source.md"
    source_bytes = (
        b"# Synthetic retention decision A\n"
        b"The synthetic retention decision is exactly thirty days.\n\n"
        b"# Synthetic retention decision B\n"
        b"The synthetic retention decision is exactly thirty days.\n\n"
        b"# Unrelated distractor\n"
        b"Tropical rainfall measurements concern monsoon regions.\n"
    )
    source.write_bytes(source_bytes)
    added, add_seconds = _run_cli(
        environment,
        "knowledge",
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(source),
        "--typed-extraction",
        "off",
        "--pdf-fallback",
        "off",
        "--confirm-no-case-data",
    )
    caller_sha256 = _sha256_bytes(source_bytes)
    source_sha256 = added.get("compiler", {}).get("source_sha256")
    if source_sha256 != caller_sha256:
        raise DiagnosticFailure("Source registration did not bind the exact caller bytes")
    source_id = str(added["source"]["source_id"])
    source_revision_id = str(added["identity"]["source_revision_id"])
    verified, _ = _run_cli(
        environment,
        "knowledge",
        "source",
        "verify",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    if verified.get("valid") is not True:
        raise DiagnosticFailure("Public Source verification did not pass")
    source_only, _ = _run_cli(
        environment,
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "Verify the synthetic retention decision.",
        "--purpose",
        "verify",
        "--confirm-no-case-data",
    )
    gap_codes = {
        str(item.get("code")) for item in source_only.get("gaps", []) if isinstance(item, dict)
    }
    if "uncompiled_source" not in gap_codes or source_only.get("statements") != []:
        raise DiagnosticFailure("Source-only Context did not preserve its required Gap")

    handoff, handoff_seconds = _run_cli(
        environment,
        "knowledge",
        "compile",
        "handoff",
        "--vault",
        str(vault),
        "--source-revision-id",
        source_revision_id,
    )
    expected_boundaries = {
        "read_leaf": "knowledge_support",
        "write_leaf": "knowledge_sink",
        "grant_required": True,
        "grant_included": False,
        "model_invoked": False,
        "write_performed": False,
    }
    if (
        handoff.get("boundaries") != expected_boundaries
        or handoff.get("write_performed") is not False
    ):
        raise DiagnosticFailure("Compilation handoff did not preserve its no-write boundary")
    handoff_steps = handoff.get("steps")
    if not isinstance(handoff_steps, list) or not handoff_steps:
        raise DiagnosticFailure("Compilation handoff omitted its public leaf steps")
    for step in handoff_steps:
        if not isinstance(step, dict):
            raise DiagnosticFailure("Compilation handoff step is invalid")
        leaf = step.get("leaf")
        if leaf == "knowledge_support" and step.get("write") is not False:
            raise DiagnosticFailure("Compilation handoff read leaf was marked writable")
        if leaf == "knowledge_sink" and step.get("write") is not True:
            raise DiagnosticFailure("Compilation handoff write leaf was not explicit")
        if leaf not in {"knowledge_support", "knowledge_sink"}:
            raise DiagnosticFailure("Compilation handoff exposed an unknown leaf")

    compiler_grant = _enable_grant(
        environment,
        vault,
        writer="upstream-closure-compiler",
        profile="compiler",
        max_objects=10,
    )
    compilation = _compile_source_revision(
        environment,
        vault,
        source_revision_id=source_revision_id,
        grant_id=compiler_grant,
        plan_root=root,
    )
    first_fragment = compilation["first_fragment"]
    source_page = vault / "wiki" / "sources" / f"{source_revision_id}.md"
    fragment_page = (
        vault
        / "wiki"
        / "indexes"
        / f"source-{source_revision_id}-fragments-0001.md"
    )
    source_projection = source_page.read_text(encoding="utf-8")
    fragment_projection = fragment_page.read_text(encoding="utf-8")
    exact_coordinates = all(
        value in source_projection
        for value in (source_revision_id, caller_sha256, "## Exact evidence drill-down")
    ) and all(
        value in fragment_projection
        for value in (
            str(first_fragment["fragment_id"]),
            str(first_fragment["locator"]),
            str(first_fragment["text_sha256"]),
        )
    )
    if not exact_coordinates:
        raise DiagnosticFailure("Living Wiki lost an exact Source evidence coordinate")

    _expect_cli_permission_denied(
        environment,
        "knowledge",
        "source",
        "get",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    _expect_cli_permission_denied(
        environment,
        "knowledge",
        "source",
        "fragment",
        "--vault",
        str(vault),
        "--fragment-id",
        str(first_fragment["fragment_id"]),
    )

    review_manifest, review_manifest_seconds = _run_cli(
        environment,
        "knowledge",
        "review",
        "manifest",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    review_manifest_sha256 = review_manifest.get("review_manifest_sha256")
    if not isinstance(review_manifest_sha256, str) or len(review_manifest_sha256) != 64:
        raise DiagnosticFailure("Source review manifest did not return an exact SHA-256")
    approved, approve_seconds = _run_cli(
        environment,
        "knowledge",
        "review",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        "--review-manifest-sha256",
        review_manifest_sha256,
        "--reviewer-id",
        "upstream-closure-development-review",
        "--reason",
        "Synthetic source-free fixture review for exact read seam.",
        "--confirm-reviewed",
    )
    if not isinstance(approved, dict):
        raise DiagnosticFailure("Source review approval did not return a receipt")
    source_read, source_read_seconds = _run_cli(
        environment,
        "knowledge",
        "source",
        "get",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    fragment_read, fragment_read_seconds = _run_cli(
        environment,
        "knowledge",
        "source",
        "fragment",
        "--vault",
        str(vault),
        "--fragment-id",
        str(first_fragment["fragment_id"]),
        "--max-chars",
        "12000",
    )
    source_card = source_read.get("source")
    fragment_card = fragment_read.get("fragment")
    fragment_text = fragment_card.get("text") if isinstance(fragment_card, dict) else None
    if (
        not isinstance(source_card, dict)
        or source_card.get("content_sha256") != caller_sha256
        or source_read.get("write_performed") is not False
        or not isinstance(fragment_card, dict)
        or fragment_read.get("write_performed") is not False
        or fragment_card.get("text_sha256") != first_fragment["text_sha256"]
        or not isinstance(fragment_text, str)
        or _sha256_bytes(fragment_text.encode("utf-8")) != first_fragment["text_sha256"]
    ):
        raise DiagnosticFailure("Reviewed Source/Fragment read did not preserve exact evidence")

    applicable_duties = ("primary_answer", "source_evidence", "unresolved_gap")
    duty_arguments = [
        argument
        for duty in applicable_duties
        for argument in ("--applicable-duty", duty)
    ]
    duty_query, duty_query_seconds = _run_cli(
        environment,
        "knowledge",
        "query",
        "--vault",
        str(vault),
        "--query",
        "synthetic retention decision",
        "--purpose",
        "verify",
        "--query-plan-version",
        "6",
        *duty_arguments,
    )
    duty_context, duty_context_seconds = _run_cli(
        environment,
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "Verify the synthetic retention decision.",
        "--purpose",
        "verify",
        "--query-plan-version",
        "6",
        *duty_arguments,
        "--confirm-no-case-data",
    )
    expected_include = "The synthetic retention decision is exactly thirty days."
    expected_exclude = "Tropical rainfall measurements concern monsoon regions."
    selected_evidence = duty_query.get("evidence")
    query_plan = duty_query.get("query_plan")
    if not isinstance(selected_evidence, list) or not isinstance(query_plan, dict):
        raise DiagnosticFailure("Duty Query omitted its evidence or Query Plan")
    selected_text = _canonical(selected_evidence)
    duty_statuses = {
        str(item.get("duty")): item.get("status")
        for item in query_plan.get("duties", [])
        if isinstance(item, dict)
    }
    deduplication_reasons = {
        str(item.get("reason"))
        for item in duty_query.get("local_audit", {}).get("deduplications", [])
        if isinstance(item, dict)
    }
    provider_capsule = duty_context.get("provider_capsule")
    if (
        selected_text.count(expected_include) != 1
        or expected_exclude in selected_text
        or deduplication_reasons != {"duplicate_source_reference"}
        or any(duty_statuses.get(duty) != "satisfied" for duty in applicable_duties)
        or not isinstance(provider_capsule, dict)
        or provider_capsule.get("delivery", {}).get("write_performed") is not False
    ):
        raise DiagnosticFailure("Context selection admitted a duplicate/distractor or lost a duty")

    task_grant = _enable_grant(
        environment,
        vault,
        writer="upstream-closure-continuity",
        operations=("record_run", "remember", "forget"),
        max_objects=100,
    )
    project = "DeepLaw"
    task_text = "Complete the named upstream public-seam diagnostic."
    started, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "start",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )
    task_handle = str(started["task_handle"])
    located_before, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "locate",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )
    connect, _ = _run_cli(
        environment,
        "knowledge",
        "host",
        "connect",
        "--host",
        "codex",
        "--vault",
        str(vault),
    )
    raw_session = "synthetic-official-session-upstream-closure"
    session_sha256 = _sha256_bytes(raw_session.encode("utf-8"))
    unbound, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "resolve-host-continuity",
        "--vault",
        str(vault),
        "--host",
        "codex",
        "--session-sha256",
        session_sha256,
        "--workspace",
        str(repository),
    )
    enrolled, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "enroll-host-session",
        "--vault",
        str(vault),
        "--host",
        "codex",
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--grant-id",
        task_grant,
        "--idempotency-key",
        "upstream-closure-enroll",
        "--confirm-no-case-data",
        input_text=raw_session + "\n",
    )
    if raw_session in _canonical(enrolled):
        raise DiagnosticFailure("Raw Host session identifier escaped enrollment")
    checkpoint, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "checkpoint",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--grant-id",
        task_grant,
        "--idempotency-key",
        "upstream-closure-checkpoint",
        "--summary",
        "The synthetic public-seam diagnostic is enrolled.",
        "--next-action",
        "Review the retained development report.",
        "--expires-at",
        "2099-01-01T00:00:00Z",
        "--decision",
        "Keep static Host Connect task-neutral.",
        "--gap",
        "Real Host qualification is not executed.",
        "--artifact-ref",
        "upstream-product-closure-development",
        "--confirm-no-case-data",
    )
    resumed, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "resume",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
    )
    provider = resumed.get("provider_capsule", {})
    provider_bytes = provider.get("delivery", {}).get("provider_content_bytes")
    provider_text = _canonical(provider)
    if not isinstance(provider_bytes, int) or not 0 < provider_bytes <= MAX_PROVIDER_BYTES:
        raise DiagnosticFailure("Task resume Provider Capsule violated its byte bound")
    for forbidden in (raw_session, str(root), "transcript", "reasoning"):
        if forbidden in provider_text:
            raise DiagnosticFailure("Task resume Provider Capsule exposed forbidden data")

    forked, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "fork",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--child-workspace",
        str(child_worktree),
        "--mode",
        "child-task",
        "--child-task",
        "Run child-only verification.",
    )
    compacted, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "compaction",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
    )
    wrong_task, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "resume",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        "A different task line.",
        "--workspace",
        str(repository),
    )
    wrong_worktree, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "resolve-host-continuity",
        "--vault",
        str(vault),
        "--host",
        "codex",
        "--session-sha256",
        session_sha256,
        "--workspace",
        str(child_worktree),
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("diverged\n", encoding="utf-8")
    stale, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "resume",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
    )
    tracked.write_text("stable\n", encoding="utf-8")
    forgotten, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "forget",
        "--vault",
        str(vault),
        "--task-handle",
        task_handle,
        "--workspace",
        str(repository),
        "--grant-id",
        task_grant,
        "--idempotency-key",
        "upstream-closure-forget",
        "--reason",
        "Owner requested synthetic checkpoint removal.",
        "--confirm-no-case-data",
    )
    after_forget, _ = _run_cli(
        environment,
        "knowledge",
        "task",
        "resume",
        "--vault",
        str(vault),
        "--project",
        project,
        "--task",
        task_text,
        "--workspace",
        str(repository),
    )

    same_name_title = "Shared qualification identity"
    stable_alias = "First qualification identity alias"
    identity_grant = _enable_grant(
        environment,
        vault,
        writer="upstream-closure-identity",
        operations=("upsert_concept",),
        max_objects=10,
    )
    identity_results: list[dict[str, Any]] = []
    identity_sink_seconds = 0.0
    identity_aliases = (stable_alias, "Second qualification identity alias")
    for index, alias in enumerate(identity_aliases, start=1):
        result, elapsed = _apply_sink_request(
            environment,
            vault,
            grant_id=identity_grant,
            request={
                "operation": "upsert_concept",
                "idempotency_key": f"upstream-closure-same-name-{index}",
                "title": same_name_title,
                "body": f"Distinct source-free qualification identity {index}.",
                "aliases": [alias],
                "semantic_key": f"upstream-closure:same-name:{index}",
                "scope": "project",
                "sensitivity": "private",
                "confirm_no_case_data": True,
            },
            request_path=root / f"identity-{index}.json",
        )
        identity_results.append(result)
        identity_sink_seconds += elapsed
    identity_ids = {str(result.get("knowledge_id")) for result in identity_results}
    if (
        len(identity_ids) != 2
        or any(result.get("authority") != "agent_derived" for result in identity_results)
        or any(result.get("legal_authority") is not False for result in identity_results)
    ):
        raise DiagnosticFailure("Same-name public writes lost distinct governed identities")
    _, identity_rebuild_seconds = _run_cli(
        environment,
        "knowledge",
        "autonomy",
        "rebuild",
        "--vault",
        str(vault),
    )
    identity_browse, identity_browse_seconds = _run_cli(
        environment,
        "knowledge",
        "wiki",
        "browse-kind",
        "--vault",
        str(vault),
        "--kind",
        "concept",
        "--limit",
        "8",
    )
    browsed_by_id = {
        str(item.get("knowledge_id")): item
        for item in identity_browse.get("items", [])
        if isinstance(item, dict)
    }
    if not identity_ids <= set(browsed_by_id):
        raise DiagnosticFailure("Living Wiki browse lost a same-name governed identity")

    before_reads, _ = _run_cli(
        environment, "knowledge", "autonomy", "status", "--vault", str(vault)
    )
    identity_page_seconds = 0.0
    identity_page_paths: set[str] = set()
    for result, alias in zip(identity_results, identity_aliases, strict=True):
        knowledge_id = str(result["knowledge_id"])
        wiki_path = browsed_by_id[knowledge_id].get("workspace_path")
        if not isinstance(wiki_path, str):
            raise DiagnosticFailure("Living Wiki browse omitted a governed identity path")
        page, page_seconds = _run_cli(
            environment,
            "knowledge",
            "wiki",
            "page",
            "--vault",
            str(vault),
            "--wiki-path",
            wiki_path,
        )
        content = page.get("content")
        if (
            not isinstance(content, str)
            or knowledge_id not in content
            or same_name_title not in content
            or alias not in content
            or page.get("write_performed") is not False
        ):
            raise DiagnosticFailure("Living Wiki exact page lost identity, title, or alias")
        identity_page_paths.add(wiki_path)
        identity_page_seconds += page_seconds
    if len(identity_page_paths) != 2:
        raise DiagnosticFailure("Same-name identities resolved to one Living Wiki page")

    mcp = asyncio.run(_run_mcp_task(environment=environment, vault=vault))
    after_reads, _ = _run_cli(environment, "knowledge", "autonomy", "status", "--vault", str(vault))
    read_no_write = before_reads.get("sequence") == after_reads.get(
        "sequence"
    ) and before_reads.get("audit_head") == after_reads.get("audit_head")
    if not read_no_write:
        raise DiagnosticFailure("Read-only CLI/MCP journey changed the canonical Ledger")

    expected_states = (
        located_before.get("status") == "not_found",
        connect.get("task_handle_configured") is False,
        unbound.get("gaps") == [{"code": "route_unbound"}],
        enrolled.get("status") == "bound",
        checkpoint.get("status") == "checkpointed",
        resumed.get("status") == "admitted",
        forked.get("workspace_independent") is True,
        compacted.get("transcript_copied") is False,
        wrong_task.get("status") == "not_found",
        wrong_worktree.get("gaps", [{}])[0].get("code") == "route_wrong_worktree",
        not set(stale.get("gap_codes", ())).isdisjoint({"workspace_diverged", "stale_checkpoint"}),
        forgotten.get("status") == "forgotten",
        after_forget.get("status") == "not_found",
    )
    if not all(expected_states):
        raise DiagnosticFailure("Task Continuity public journey admitted a wrong state")

    return {
        "init_doctor": {
            "status": "executed",
            "first_correct_action": "Initialize the Vault, then run doctor.",
            "public_cli_steps": 2,
            "ready": True,
            "autonomous_vault_ready": doctor.get("product_readiness", {}).get(
                "autonomous_vault_ready"
            ),
            "elapsed_seconds": round(init_seconds + doctor_seconds, 6),
        },
        "source_evidence": {
            "status": "executed",
            "first_correct_action": "Register the exact source bytes before compiling knowledge.",
            "public_cli_steps": 5,
            "public_operation_count": 5,
            "living_wiki_file_read_steps": 2,
            "exact_source_bytes_bound": True,
            "source_verify_valid": True,
            "source_only_gap_codes": sorted(gap_codes),
            "source_only_statements": 0,
            "wiki_exact_source_coordinate_drill_down": True,
            "locator_and_quote_sha256_preserved": True,
            "source_review_status": "executed_source_free_fixture_review",
            "source_content_read_status": "executed_after_review",
            "source_content_exact_sha256": caller_sha256,
            "source_read_write_performed": source_read["write_performed"],
            "fragment_read_write_performed": fragment_read["write_performed"],
            "source_review_manifest_sha256": review_manifest_sha256,
            "source_content_wrong_state_admission_count": 0,
            "public_review_operation_count": 2,
            "public_read_operation_count": 2,
            "elapsed_seconds": round(
                add_seconds
                + handoff_seconds
                + review_manifest_seconds
                + approve_seconds
                + source_read_seconds
                + fragment_read_seconds,
                6,
            ),
        },
        "compilation": {
            key: value
            for key, value in compilation.items()
            if key not in {"run_id", "first_fragment"}
        },
        "compilation_handoff": {
            "status": "executed",
            "first_correct_action": "Inspect the read-only handoff before opening the sink saga.",
            "public_operation_count": 1,
            "host_internal_packet_count": 0,
            "read_leaf": handoff["boundaries"]["read_leaf"],
            "write_leaf": handoff["boundaries"]["write_leaf"],
            "grant_required": handoff["boundaries"]["grant_required"],
            "grant_included": handoff["boundaries"]["grant_included"],
            "model_invoked": handoff["boundaries"]["model_invoked"],
            "write_performed": handoff["write_performed"],
            "read_step_count": len(
                [step for step in handoff["steps"] if step["leaf"] == "knowledge_support"]
            ),
            "write_step_count": len(
                [step for step in handoff["steps"] if step["leaf"] == "knowledge_sink"]
            ),
            "elapsed_seconds": round(handoff_seconds, 6),
        },
        "context_selection": {
            "status": "executed",
            "public_cli_steps": 2,
            "public_operation_count": 2,
            "expected_include": "executed_and_selected_once",
            "expected_exclude": "executed_and_excluded",
            "required_duties": {
                duty: duty_statuses[duty] for duty in applicable_duties
            },
            "acceptable_gap": "uncompiled_source",
            "duplicate_suppression_reasons": sorted(deduplication_reasons),
            "distractor_suppressed": True,
            "provider_write_performed": False,
            "elapsed_seconds": round(duty_query_seconds + duty_context_seconds, 6),
        },
        "identity_ambiguity": {
            "status": "executed",
            "public_cli_steps": 7,
            "public_mcp_steps": 0,
            "public_operation_count": 7,
            "host_internal_packet_count": 0,
            "same_name_distinct_identity_count": 2,
            "same_name_lookup_status": "wiki_browse_distinct",
            "automatic_title_merge_rejected": True,
            "alias_page_read_status": "exact_page_read",
            "alias_resolved_exact_identity": True,
            "wiki_distinct_identity_count": 2,
            "legal_authority": False,
            "elapsed_seconds": round(
                identity_sink_seconds
                + identity_rebuild_seconds
                + identity_browse_seconds
                + identity_page_seconds,
                6,
            ),
        },
        "task_continuity": {
            "status": "executed",
            "first_correct_action": "Review the retained development report.",
            "public_cli_steps": 15,
            "decision_preserved": "Keep static Host Connect task-neutral." in provider_text,
            "wrong_state_admission_count": 0,
            "fork_admitted": True,
            "compaction_transcript_copied": False,
            "stale_checkpoint_withheld": True,
            "wrong_task_withheld": True,
            "wrong_worktree_withheld": True,
            "selective_forget_withheld": True,
            "provider_content_bytes": provider_bytes,
        },
        "provider": mcp,
        "read_no_write": {
            "status": "executed",
            "public_operation_count": 2,
            "host_internal_packet_count": 0,
            "canonical_sequence_unchanged": True,
            "canonical_audit_head_unchanged": True,
        },
        "safety": {
            "session_identifier_in_report": False,
            "session_identifier_in_provider": False,
            "transcript_or_reasoning_read": False,
            "credential_or_env_file_read": False,
            "child_environment_allowlisted": True,
        },
        "initialization_schema": initialized.get("schema_version"),
    }


def _scale_source(scale: int) -> bytes:
    return "".join(
        f"# Scale claim {index:06d}\nScale fact {index:06d} remains source-bound.\n"
        for index in range(1, scale + 1)
    ).encode("utf-8")


def _run_scale(
    scale: int,
    root: Path,
    environment: dict[str, str],
    *,
    warm_iterations: int,
) -> dict[str, Any]:
    lane_started = time.perf_counter()
    rss_before = _rss_bytes()
    fd_before = _open_file_count()
    cold_startup_seconds = _cold_cli_startup(environment)
    root.mkdir(parents=True)
    vault = root / f"scale-{scale}"
    _run_cli(
        environment,
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        f"upstream-closure-scale-{scale}",
    )
    user_file = vault / "knowledge" / "owner-notes.md"
    user_bytes = b"# Owner notes\nThis unmanaged file must remain exact.\n"
    user_file.write_bytes(user_bytes)
    source = root / f"scale-{scale}.md"
    source.write_bytes(_scale_source(scale))
    added, add_seconds = _run_cli(
        environment,
        "knowledge",
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(source),
        "--typed-extraction",
        "off",
        "--pdf-fallback",
        "off",
        "--no-reference-proposals",
        "--confirm-no-case-data",
    )
    grant_id = _enable_grant(
        environment,
        vault,
        writer=f"upstream-closure-scale-{scale}",
        profile="compiler",
        max_objects=max(scale + 100, 1_000),
    )
    compilation = _compile_source_revision(
        environment,
        vault,
        source_revision_id=str(added["identity"]["source_revision_id"]),
        grant_id=grant_id,
        plan_root=root,
    )
    if compilation["objects_staged"] != scale:
        raise DiagnosticFailure("Scale compilation did not stage the requested object count")
    if user_file.read_bytes() != user_bytes:
        raise DiagnosticFailure("Projection changed an unmanaged owner file")

    before_no_op = _owned_projection_state(vault)
    canonical_before_no_op, _ = _run_cli(
        environment,
        "knowledge",
        "autonomy",
        "status",
        "--vault",
        str(vault),
    )
    _, no_op_seconds = _run_cli(
        environment,
        "knowledge",
        "compile",
        "resume",
        "--vault",
        str(vault),
        "--grant-id",
        grant_id,
        "--run-id",
        str(compilation["run_id"]),
        "--project",
        "--confirm-no-case-data",
    )
    after_no_op = _owned_projection_state(vault)
    canonical_after_no_op, _ = _run_cli(
        environment,
        "knowledge",
        "autonomy",
        "status",
        "--vault",
        str(vault),
    )
    no_op_equivalent = before_no_op == after_no_op
    no_op_canonical_unchanged = (
        canonical_before_no_op.get("sequence") == canonical_after_no_op.get("sequence")
        and canonical_before_no_op.get("audit_head") == canonical_after_no_op.get("audit_head")
    )
    if not no_op_equivalent or not no_op_canonical_unchanged:
        raise DiagnosticFailure("No-op projection rewrote an owned file or canonical Ledger")

    query, query_seconds = _run_cli(
        environment,
        "knowledge",
        "query",
        "--vault",
        str(vault),
        "--query",
        f"Scale fact {scale:06d}",
        "--purpose",
        "verify",
        "--query-plan-version",
        "6",
        "--max-chars",
        "4000",
        "--max-tokens",
        "1000",
    )
    browse, browse_seconds = _run_cli(
        environment,
        "knowledge",
        "wiki",
        "browse-kind",
        "--vault",
        str(vault),
        "--kind",
        "claim",
        "--limit",
        "20",
    )
    capsule = query.get("capsule")
    if not isinstance(capsule, dict):
        raise DiagnosticFailure("Scale query omitted its Provider Capsule projection")
    provider_bytes = len(_canonical(capsule).encode("utf-8"))
    if provider_bytes > MAX_PROVIDER_BYTES:
        raise DiagnosticFailure("Scale query exceeded the Provider byte bound")
    mcp = asyncio.run(
        _run_mcp_task(
            environment=environment,
            vault=vault,
            warm_iterations=warm_iterations,
            warm_query_text=f"Scale fact {scale:06d}",
        )
    )

    managed = sorted(
        path
        for path in (vault / "knowledge").rglob("*.md")
        if path.is_file() and path.name != user_file.name
    )
    if not managed:
        raise DiagnosticFailure("Scale projection did not materialize governed Markdown")
    original = managed[0]
    renamed = original.with_name("renamed-" + original.name)
    shutil.move(original, renamed)
    renamed.write_text(
        renamed.read_text(encoding="utf-8") + "\nExternal owner edit.\n",
        encoding="utf-8",
    )
    reconcile_grant = _enable_grant(
        environment,
        vault,
        writer="upstream-closure-reconcile",
        operations=("remember",),
        max_objects=100,
    )
    reconciled, reconcile_seconds = _run_cli(
        environment,
        "knowledge",
        "reconcile",
        "--vault",
        str(vault),
        "--grant-id",
        reconcile_grant,
        "--confirm-no-case-data",
    )
    edit_move_preserved = bool(reconciled.get("committed")) and renamed.exists()
    derived_maintenance = reconciled.get("derived_maintenance", {})
    if (
        edit_move_preserved is not True
        or user_file.read_bytes() != user_bytes
        or derived_maintenance.get("status") != "rebuilt"
    ):
        raise DiagnosticFailure("Rename/edit reconcile did not preserve identity or owner file")

    incremental_hashes = _owned_projection_hashes(vault)
    rebuilt, full_rebuild_seconds = _run_cli(
        environment,
        "knowledge",
        "autonomy",
        "rebuild",
        "--vault",
        str(vault),
    )
    full_hashes = _owned_projection_hashes(vault)
    full_incremental_equivalent = incremental_hashes == full_hashes
    if not full_incremental_equivalent:
        raise DiagnosticFailure("Full and changed incremental projections are not equivalent")
    if user_file.read_bytes() != user_bytes:
        raise DiagnosticFailure("Full rebuild changed an unmanaged owner file")

    renamed_wiki_path = renamed.relative_to(vault).as_posix()
    exact_source_wiki_path = (
        f"wiki/sources/{added['identity']['source_revision_id']}.md"
    )
    outlinks, outlinks_seconds = _run_cli(
        environment,
        "knowledge",
        "wiki",
        "outlinks",
        "--vault",
        str(vault),
        "--wiki-path",
        renamed_wiki_path,
        "--limit",
        "20",
    )
    backlinks, backlinks_seconds = _run_cli(
        environment,
        "knowledge",
        "wiki",
        "backlinks",
        "--vault",
        str(vault),
        "--wiki-path",
        exact_source_wiki_path,
        "--limit",
        "20",
    )
    if (
        outlinks.get("index_used") is not True
        or backlinks.get("index_used") is not True
        or outlinks.get("write_performed") is not False
        or backlinks.get("write_performed") is not False
        or exact_source_wiki_path not in outlinks.get("links", [])
        or not isinstance(backlinks.get("total_count"), int)
        or backlinks["total_count"] < 1
    ):
        raise DiagnosticFailure("Living Wiki link index lost its exact Source edge")

    inventory = _file_inventory(vault)
    artifacts = _artifact_inventory(vault, owner_file=user_file)
    browse_items = browse.get("items", [])
    return {
        "scale": scale,
        "status": "executed",
        "first_correct_action": "Register the exact scale source before compilation.",
        "public_cli_steps": int(compilation["public_operation_count"]) + 13,
        "public_operation_count": int(compilation["public_operation_count"]) + 13,
        "compilation_public_operation_count": int(compilation["public_operation_count"]),
        "owner_operation_steps": 18,
        "host_internal_packet_count": compilation["packet_count"],
        "host_internal_packet_steps": 2 * compilation["packet_count"],
        "workspace_edit_steps": 1,
        "source_add_elapsed_seconds": round(add_seconds, 6),
        "compilation_elapsed_seconds": compilation["elapsed_seconds"],
        "compilation_phase_elapsed_seconds": compilation["phase_elapsed_seconds"],
        "cold_cli_startup_elapsed_seconds": round(cold_startup_seconds, 6),
        "no_op_projection_elapsed_seconds": round(no_op_seconds, 6),
        "changed_incremental_elapsed_seconds": round(reconcile_seconds, 6),
        "full_rebuild_elapsed_seconds": round(full_rebuild_seconds, 6),
        "total_elapsed_seconds": round(time.perf_counter() - lane_started, 6),
        "packet_count": compilation["packet_count"],
        "objects_staged": compilation["objects_staged"],
        "validation_valid": compilation["validation_valid"],
        "no_op_projection_equivalent": no_op_equivalent,
        "no_op_canonical_ledger_unchanged": no_op_canonical_unchanged,
        "no_op_owned_file_count": len(before_no_op),
        "full_incremental_changed_input_equivalent": full_incremental_equivalent,
        "full_rebuild_status": rebuilt.get("living_wiki", {}).get("projection_profile_name"),
        "user_file_exact_bytes_preserved": True,
        "rename_edit_reconcile": edit_move_preserved,
        "reconcile_status": "executed",
        "reconcile_derived_maintenance": derived_maintenance,
        "query_status": "executed",
        "query_gap_codes": sorted(
            {str(item.get("code")) for item in capsule.get("gaps", []) if isinstance(item, dict)}
        ),
        "provider_content_bytes": provider_bytes,
        "provider_bytes": {
            "status": "executed",
            "value": provider_bytes,
            "hard_limit": MAX_PROVIDER_BYTES,
        },
        "actual_provider_tokens": {
            "status": "not_executed",
            "value": None,
            "reason": "No real Host/model usage receipt was available.",
        },
        "persistent_mcp": mcp,
        "wiki_browse_status": "executed",
        "wiki_browse_returned": len(browse_items) if isinstance(browse_items, list) else 0,
        "wiki_link_index": {
            "status": "executed",
            "claim_wiki_path": renamed_wiki_path,
            "exact_source_wiki_path": exact_source_wiki_path,
            "outlink_resolved": True,
            "backlink_resolved": True,
            "backlink_total_count": backlinks["total_count"],
            "index_used": True,
            "write_performed": False,
            "elapsed_seconds": round(outlinks_seconds + backlinks_seconds, 6),
        },
        "query_elapsed_seconds": round(query_seconds, 6),
        "wiki_browse_elapsed_seconds": round(browse_seconds, 6),
        "peak_child_rss_bytes": _rss_bytes(),
        "peak_child_rss_baseline_bytes": rss_before,
        "rss_measurement_scope": "runner_process_lifetime_child_peak",
        "open_file_descriptors_before": fd_before,
        "open_file_descriptors_after": _open_file_count(),
        "reconcile_count_fields": {
            key: value
            for key, value in reconciled.items()
            if key.endswith("_count") and isinstance(value, int)
        },
        "artifacts": artifacts,
        **inventory,
    }


def _git_identity(environment: dict[str, str]) -> dict[str, Any]:
    commit = _git(environment, REPOSITORY, "rev-parse", "HEAD")
    tree = _git(environment, REPOSITORY, "rev-parse", "HEAD^{tree}")
    return {
        "commit": commit,
        "tree": tree,
        "package_version": importlib.metadata.version("deeplaw"),
        "uv_lock_sha256": _sha256_file(REPOSITORY / "uv.lock"),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "worktree_clean": not bool(
            _git(environment, REPOSITORY, "status", "--porcelain", "--untracked-files=all")
        ),
    }


def run_diagnostic(
    scales: list[int],
    *,
    allow_expensive_scale: bool = False,
    warm_iterations: int = DEFAULT_WARM_ITERATIONS,
) -> dict[str, Any]:
    if not scales or any(scale < 1 or scale > MAX_SCALE for scale in scales):
        raise ValueError("scale must be between 1 and 100000")
    if any(scale > EXPENSIVE_SCALE_THRESHOLD for scale in scales) and not allow_expensive_scale:
        raise ValueError("scale above 10000 requires --allow-expensive-scale")
    if not 1 <= warm_iterations <= 100:
        raise ValueError("warm iterations must be between 1 and 100")
    if len(scales) != len(set(scales)):
        raise ValueError("scale values must be unique")
    started = time.perf_counter()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with tempfile.TemporaryDirectory(prefix="deeplaw-upstream-closure-") as temporary:
        root = Path(temporary).resolve()
        environment = _child_environment(root)
        base = _source_and_task_task(root / "base", environment)
        evidence_formats = _evidence_format_lane(root / "evidence-formats", environment)
        source_identity = _source_successor_lane(root / "source-identity", environment)
        scale_results = [
            _run_scale(
                scale,
                root / f"lane-{scale}",
                environment,
                warm_iterations=warm_iterations,
            )
            for scale in scales
        ]
        exact = _git_identity(environment)
    executed = [
        "public init and doctor",
        "exact Markdown Source registration and verification",
        "source-only Context Gap",
        "read-only Compilation Handoff",
        "public Compilation Run and Living Wiki projection",
        "synthetic source review and exact Source/Fragment reads",
        "Markdown/HTML/DOCX/native-text PDF exact-byte and locator lanes",
        "blank PDF OCR-needed fail-closed probe",
        "Source successor, historical/current alias, and ambiguous successor rejection",
        "same-name distinct identity, exact alias page read, and automatic merge rejection",
        "Living Wiki backlink/outlink exact Source resolution",
        "Context expected include/exclude, duty, Gap, duplicate, and distractor lane",
        "Task Continuity enrollment/resume/fork/compaction/wrong-state/forget",
        "stdio MCP query/context/explain and read no-write audit",
        *[f"public {scale}-object scale lane" for scale in scales],
    ]
    failed: list[str] = []
    not_executed = list(NOT_EXECUTED)
    not_executed.extend(
        f"{name} exact-byte lane: {result.get('reason', 'unavailable')}"
        for name, result in evidence_formats["format_results"].items()
        if isinstance(result, dict) and result.get("status") == "not_executed"
    )
    public_operation_counts = {
        "base": {
            "cli_steps": sum(
                int(base[section].get("public_cli_steps", 0))
                for section in (
                    "init_doctor",
                    "source_evidence",
                    "compilation",
                    "context_selection",
                    "identity_ambiguity",
                    "task_continuity",
                )
            ),
            "handoff_steps": int(base["compilation_handoff"]["public_operation_count"]),
            "compilation_grant_steps": 1,
            "source_review_and_read_steps": int(
                base["source_evidence"]["public_review_operation_count"]
                + base["source_evidence"]["public_read_operation_count"]
            ),
            "mcp_operations": int(base["provider"]["public_operation_count"]),
            "read_audit_steps": int(base["read_no_write"]["public_operation_count"]),
        },
        "scale_lanes": {
            str(lane["scale"]): {
                "cli_steps": int(lane["public_operation_count"]),
                "mcp_operations": int(lane["persistent_mcp"]["public_operation_count"]),
            }
            for lane in scale_results
        },
        "evidence_formats": int(evidence_formats["public_operation_count"]),
        "source_identity": int(source_identity["public_operation_count"]),
    }
    public_operation_counts["total_cli_steps"] = int(
        public_operation_counts["base"]["cli_steps"]
        + public_operation_counts["base"]["handoff_steps"]
        + public_operation_counts["base"]["compilation_grant_steps"]
        + public_operation_counts["base"]["source_review_and_read_steps"]
        + public_operation_counts["base"]["read_audit_steps"]
        + public_operation_counts["evidence_formats"]
        + public_operation_counts["source_identity"]
        + sum(item["cli_steps"] for item in public_operation_counts["scale_lanes"].values())
    )
    public_operation_counts["total_mcp_operations"] = int(
        public_operation_counts["base"]["mcp_operations"]
        + sum(item["mcp_operations"] for item in public_operation_counts["scale_lanes"].values())
    )
    public_operation_counts["total_public_operations"] = int(
        public_operation_counts["total_cli_steps"]
        + public_operation_counts["total_mcp_operations"]
    )
    host_internal_packet_counts = {
        "base": int(base["compilation"]["host_internal_packet_count"]),
        "evidence_formats": int(evidence_formats["host_internal_packet_count"]),
        "source_identity": int(source_identity["host_internal_packet_count"]),
        "scale_lanes": {
            str(lane["scale"]): int(lane["host_internal_packet_count"])
            for lane in scale_results
        },
    }
    host_internal_packet_counts["total"] = int(
        host_internal_packet_counts["base"]
        + host_internal_packet_counts["evidence_formats"]
        + host_internal_packet_counts["source_identity"]
        + sum(host_internal_packet_counts["scale_lanes"].values())
    )
    receipt = {
        "schema_version": "deeplaw.upstream-product-closure-receipt/v1",
        "status": "executed",
        "executed": executed,
        "failed": failed,
        "not_executed": not_executed,
        "counts": {
            "executed": len(executed),
            "failed": len(failed),
            "not_executed": len(not_executed),
        },
        "public_operation_counts": public_operation_counts,
        "host_internal_packet_counts": host_internal_packet_counts,
        "provider": {
            "bytes": base["provider"]["provider_bytes"],
            "actual_tokens": base["provider"]["actual_provider_tokens"],
            "query_trace_in_provider": base["provider"]["query_trace_in_provider"],
            "canonical_ledger_in_provider": base["provider"]["canonical_ledger_in_provider"],
        },
    }
    report = {
        "schema_version": "deeplaw.upstream-product-closure-development/v1",
        "evidence_class": "development_diagnostic",
        "generated_at": generated_at,
        "exact": exact,
        "upstream_research_anchors": {
            "openwiki": "21746ce996f3a69898883da58b122770f7dbd668",
            "tolaria": "40cc9f9479fef7bfe8a51a6df7e02fe11971f95e",
            "obsidian_api": "cc1744324150c632416857c98964f87b1574a5fc",
            "ekgardt_llm_wiki": "350eec8a284e159b2e4cfd068d808cbf203a6cc5",
        },
        "upstream_current_observation": {
            "observation_date": "2026-08-20",
            "openwiki": {
                "released_comparator": "v0.3.3@355f4f68e71bd024631cdcff7aa871c3e72435da",
                "moving_head": "46c0a3d53011a1f4916052187288dc5b4651c292",
                "execution_status": "not_executed",
            },
            "tolaria": {
                "released_comparator": "v2026-08-19@cf9b0c8b9fca7cd9556da4b0401e207626a70384",
                "moving_head": "367a91416477c90bbfae766dc06add3de6ae75a7",
                "execution_status": "not_executed",
            },
            "obsidian_api": {
                "moving_head": "cc1744324150c632416857c98964f87b1574a5fc",
                "execution_status": "not_executed",
            },
            "ekgardt_llm_wiki": {
                "moving_head": "350eec8a284e159b2e4cfd068d808cbf203a6cc5",
                "execution_status": "not_executed",
            },
        },
        "formal_claims": {
            "qualification_evidence": False,
            "release_ready": False,
            "claim_eligible": False,
            "human_gold": False,
            "legal_attestation": False,
            "competitive_claim": False,
        },
        "receipt": receipt,
        "base_journey": base,
        "evidence_formats": evidence_formats,
        "source_identity": source_identity,
        "scale_lanes": scale_results,
        "executed": executed,
        "failed": failed,
        "not_executed": not_executed,
        "limitations": [
            "Synthetic local inputs only; no client or case material.",
            "Provider bytes are measured from actual stdio MCP content.",
            "Native Provider token usage is unavailable because no real Host/model ran.",
            "Latency, RSS, and storage are one-machine development observations.",
            "RSS is the runner process-lifetime child peak; file descriptors are parent estimates.",
            "Upstream repositories were researched at exact commits but not executed here.",
        ],
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }
    rendered = _canonical(report)
    forbidden = ("/Users/", "/tmp/", "\\Users\\", "token_path", "raw_session")
    if any(value in rendered for value in forbidden):
        raise DiagnosticFailure("Sanitized report contained a forbidden path or capability field")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scale",
        type=int,
        action="append",
        dest="scales",
        help="Public compilation scale; repeat for more than one lane.",
    )
    parser.add_argument(
        "--allow-expensive-scale",
        action="store_true",
        help="Explicitly permit a scale above 10000; required for the final 100k candidate only.",
    )
    parser.add_argument(
        "--warm-iterations",
        type=int,
        default=DEFAULT_WARM_ITERATIONS,
        help="Persistent MCP warm query/context samples per scale lane (default: 5).",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    scales = arguments.scales or [3]
    report = run_diagnostic(
        scales,
        allow_expensive_scale=arguments.allow_expensive_scale,
        warm_iterations=arguments.warm_iterations,
    )
    output = arguments.output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise DiagnosticFailure("Report output is a symlink")
    if output.name == "SHA256SUMS" or (output.parent / "SHA256SUMS").exists() or (
        output.parent / "SHA256SUMS"
    ).is_symlink():
        raise DiagnosticFailure("Adjacent SHA256SUMS inventory already exists")
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum_path = _write_adjacent_checksums(output)
    print(
        _canonical(
            {
                "schema_version": report["schema_version"],
                "evidence_class": report["evidence_class"],
                "scales": scales,
                "failed": report["failed"],
                "output_sha256": _sha256_file(output),
                "sha256sums": checksum_path.name,
                "sha256sums_sha256": _sha256_file(checksum_path),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
