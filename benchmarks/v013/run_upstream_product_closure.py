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
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPOSITORY = Path(__file__).resolve().parents[2]
MAX_PROVIDER_BYTES = 65_536
PACKET_SIZE = 100
NOT_EXECUTED = (
    "interruption injection and recovery",
    "full-versus-incremental changed-input equivalence",
    "PDF/DOCX/HTML exact-byte and locator task",
    "exact Source/Fragment content read pending owner review",
    "alias/same-name collision and Source successor task",
    "backlink/outlink and wrong-merge task",
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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _child_environment(root: Path) -> dict[str, str]:
    temporary = root / "tmp"
    owner_home = root / "owner-home"
    temporary.mkdir(parents=True, exist_ok=True)
    owner_home.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUTF8": "1",
        "TMPDIR": str(temporary),
        "DEEPLAW_HOME": str(owner_home),
    }


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
    stage_seconds = 0.0
    first_fragment: dict[str, str] | None = None
    while True:
        packet, packet_seconds = _run_cli(
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
        stage_seconds += packet_seconds
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
        "objects_staged": staged,
        "packet_count": packet_count,
        "validation_valid": True,
        "commit_status": commit.get("status"),
        "projection_status": projected.get("status"),
        "first_fragment": first_fragment,
        "elapsed_seconds": round(
            begin_seconds + stage_seconds + validate_seconds + commit_seconds + project_seconds,
            6,
        ),
    }


def _file_inventory(root: Path) -> dict[str, Any]:
    files = [path for path in root.rglob("*") if path.is_file() and not path.is_symlink()]
    return {
        "file_count": len(files),
        "storage_bytes": sum(path.stat().st_size for path in files),
    }


def _markdown_hashes(root: Path) -> dict[str, tuple[str, int]]:
    return {
        path.relative_to(root).as_posix(): (_sha256_file(path), path.stat().st_mtime_ns)
        for path in root.rglob("*.md")
        if path.is_file() and not path.is_symlink()
    }


def _rss_bytes() -> int:
    observed = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(observed if sys.platform == "darwin" else observed * 1024)


async def _run_mcp_task(
    *,
    environment: dict[str, str],
    vault: Path,
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
        query = await session.call_tool(
            "knowledge_support",
            {
                "operation": "query",
                "query": "synthetic retention decision",
                "query_plan_version": "6",
            },
        )
        if query.isError or not isinstance(query.structuredContent, dict):
            raise DiagnosticFailure("MCP query failed")
        provider = query.structuredContent.get("result")
        if not isinstance(provider, dict):
            raise DiagnosticFailure("MCP query omitted its bounded Provider result")
        receipt = provider.get("receipt")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("receipt_id"), str):
            raise DiagnosticFailure("MCP query omitted its local explain receipt")
        context = await session.call_tool(
            "knowledge_support",
            {
                "operation": "context",
                "task": "Resume the synthetic retention decision.",
                "confirm_no_case_data": True,
                "query_plan_version": "6",
            },
        )
        explained = await session.call_tool(
            "knowledge_support",
            {"operation": "explain", "receipt_id": receipt["receipt_id"]},
        )
        if context.isError or explained.isError:
            raise DiagnosticFailure("MCP context or explain failed")
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
            "query_status": "executed",
            "context_status": "executed",
            "explain_status": "executed",
            "provider_content_bytes": provider_bytes,
            "max_provider_content_bytes": max(provider_bytes),
            "native_provider_tokens": "unavailable",
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
        b"# Synthetic retention decision\n"
        b"The synthetic retention decision is exactly thirty days.\n"
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

    before_reads, _ = _run_cli(
        environment, "knowledge", "autonomy", "status", "--vault", str(vault)
    )
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
            "living_wiki_file_read_steps": 2,
            "exact_source_bytes_bound": True,
            "source_verify_valid": True,
            "source_only_gap_codes": sorted(gap_codes),
            "source_only_statements": 0,
            "wiki_exact_source_coordinate_drill_down": True,
            "locator_and_quote_sha256_preserved": True,
            "source_content_read_status": "withheld_pending_owner_review",
            "source_content_wrong_state_admission_count": 0,
            "elapsed_seconds": round(add_seconds, 6),
        },
        "compilation": {
            key: value
            for key, value in compilation.items()
            if key not in {"run_id", "first_fragment"}
        },
        "task_continuity": {
            "status": "executed",
            "first_correct_action": "Review the retained development report.",
            "public_cli_steps": 14,
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


def _run_scale(scale: int, root: Path, environment: dict[str, str]) -> dict[str, Any]:
    lane_started = time.perf_counter()
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

    before_no_op = _markdown_hashes(vault)
    _run_cli(
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
    after_no_op = _markdown_hashes(vault)
    no_op_equivalent = before_no_op == after_no_op
    if not no_op_equivalent:
        raise DiagnosticFailure("No-op projection rewrote or changed Markdown")

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
    reconciled, _ = _run_cli(
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
    if edit_move_preserved is not True or user_file.read_bytes() != user_bytes:
        raise DiagnosticFailure("Rename/edit reconcile did not preserve identity or owner file")

    inventory = _file_inventory(vault)
    browse_items = browse.get("items", [])
    return {
        "scale": scale,
        "status": "executed",
        "first_correct_action": "Register the exact scale source before compilation.",
        "public_cli_steps": (2 * compilation["packet_count"]) + 13,
        "workspace_edit_steps": 1,
        "source_add_elapsed_seconds": round(add_seconds, 6),
        "compilation_elapsed_seconds": compilation["elapsed_seconds"],
        "total_elapsed_seconds": round(time.perf_counter() - lane_started, 6),
        "packet_count": compilation["packet_count"],
        "objects_staged": compilation["objects_staged"],
        "validation_valid": compilation["validation_valid"],
        "no_op_projection_equivalent": no_op_equivalent,
        "user_file_exact_bytes_preserved": True,
        "rename_edit_reconcile": edit_move_preserved,
        "reconcile_status": "executed",
        "query_status": "executed",
        "query_gap_codes": sorted(
            {str(item.get("code")) for item in capsule.get("gaps", []) if isinstance(item, dict)}
        ),
        "provider_content_bytes": provider_bytes,
        "wiki_browse_status": "executed",
        "wiki_browse_returned": len(browse_items) if isinstance(browse_items, list) else 0,
        "query_elapsed_seconds": round(query_seconds, 6),
        "wiki_browse_elapsed_seconds": round(browse_seconds, 6),
        "peak_child_rss_bytes": _rss_bytes(),
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
        "worktree_clean": not bool(
            _git(environment, REPOSITORY, "status", "--porcelain", "--untracked-files=all")
        ),
    }


def run_diagnostic(scales: list[int]) -> dict[str, Any]:
    if not scales or any(scale < 1 or scale > 10_000 for scale in scales):
        raise ValueError("scale must be between 1 and 10000")
    if len(scales) != len(set(scales)):
        raise ValueError("scale values must be unique")
    started = time.perf_counter()
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with tempfile.TemporaryDirectory(prefix="deeplaw-upstream-closure-") as temporary:
        root = Path(temporary).resolve()
        environment = _child_environment(root)
        base = _source_and_task_task(root / "base", environment)
        scale_results = [_run_scale(scale, root / f"lane-{scale}", environment) for scale in scales]
        exact = _git_identity(environment)
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
        "formal_claims": {
            "qualification_evidence": False,
            "release_ready": False,
            "claim_eligible": False,
            "human_gold": False,
            "legal_attestation": False,
            "competitive_claim": False,
        },
        "base_journey": base,
        "scale_lanes": scale_results,
        "executed": [
            "public init and doctor",
            "exact Markdown Source registration and verification",
            "source-only Context Gap",
            "public Compilation Run and Living Wiki projection",
            "Task Continuity enrollment/resume/fork/compaction/wrong-state/forget",
            "stdio MCP query/context/explain and read no-write audit",
            *[f"public {scale}-object scale lane" for scale in scales],
        ],
        "failed": [],
        "not_executed": list(NOT_EXECUTED),
        "limitations": [
            "Synthetic local inputs only; no client or case material.",
            "Provider bytes are measured from actual stdio MCP content.",
            "Native Provider token usage is unavailable because no real Host/model ran.",
            "Latency, RSS, and storage are one-machine development observations.",
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
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    scales = arguments.scales or [3]
    report = run_diagnostic(scales)
    output = arguments.output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        _canonical(
            {
                "schema_version": report["schema_version"],
                "evidence_class": report["evidence_class"],
                "scales": scales,
                "failed": report["failed"],
                "output_sha256": _sha256_file(output),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
