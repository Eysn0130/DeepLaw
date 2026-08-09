"""Claim-ineligible v0.13 local read-runtime stability diagnostic.

The runner creates one source-free synthetic Vault through the public v3 compilation
boundary, then exercises only read paths.  The RSS lane runs in an isolated child process
and uses one real knowledge-support MCP lifespan for the whole workload.  The concurrency
lane opens eight independent read-only runtimes and synchronises their first Query v6 read
with a barrier.  Neither lane can write the canonical Ledger.

This module is deliberately a diagnostic, not a release, quality, or competitive benchmark.
The default workload is a small smoke run; the frozen 10,000-request workload requires the
explicit ``--execute-10k`` flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from mcp import types
from mcp.server.lowlevel.server import RequestContext, request_ctx

from deeplaw.compilation.applicability import applicability_digest, policy_digest
from deeplaw.compilation.coordinator import CompilationCoordinator, _artifact
from deeplaw.compilation.models import COMPILER_GRANT_OPERATIONS
from deeplaw.compilation.profiles import SEMANTIC_DUTIES, compiler_profile
from deeplaw.evidence import build_input_set_sha256, statement_sha256
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_mcp_server import create_knowledge_mcp_server
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.persistent_read_runtime import PersistentReadRuntime
from deeplaw.subprocess_environment import _build_subprocess_environment
from deeplaw.util import canonical_json, sha256_bytes

SCHEMA_VERSION = "deeplaw.v013-runtime-stability-report/v1"
RUNNER_RELATIVE_PATH = "benchmarks/v013/runtime_stability.py"
SCHEMA_RELATIVE_PATH = "contracts/v013-runtime-stability-report.v1.schema.json"
QUERY_V6_RELATIVE_PATH = "src/deeplaw/retrieval/query_v6.py"
PERSISTENT_RUNTIME_RELATIVE_PATH = "src/deeplaw/persistent_read_runtime.py"
MCP_SERVER_RELATIVE_PATH = "src/deeplaw/knowledge_mcp_server.py"
FROZEN_REQUEST_COUNT = 10_000
RSS_GROWTH_LIMIT_PERCENT = 10.0
DEFAULT_REQUEST_COUNT = 16
DEFAULT_WARMUP_REQUESTS = 2
READER_COUNT = 8
QUERY_PLAN_VERSION = "6"
PROJECTION = "standard"
MAX_CHILD_TIMEOUT_SECONDS = 900

_LOCAL_PATH = re.compile(
    r"(?:/Users/|/home/|/private/var/|/tmp/|/var/folders/|[A-Za-z]:[\\/]|\\\\)"
)
_SENSITIVE_MARKER = re.compile(
    r"(?i)(?:auth\.json|api[_ -]?key\s*[:=]|password\s*[:=]|"
    r"credential\s*[:=]|secret\s*[:=]|private[_ -]?key)"
)

_FIXTURE_FAILURE_STAGES = frozenset(
    {
        "workspace",
        "source_write",
        "vault_initialize",
        "source_compile",
        "compilation_begin",
        "compilation_packet",
        "compilation_stage",
        "compilation_validate",
        "compilation_inventory",
        "compilation_commit",
        "statement_verify",
    }
)
_FIXTURE_FAILURE_TYPES = frozenset(
    {
        "attribute_error",
        "file_not_found",
        "permission_error",
        "os_error",
        "sqlite_error",
        "timeout",
        "json_error",
        "value_error",
        "type_error",
        "lookup_error",
        "runtime_error",
        "assertion_error",
        "other",
    }
)
_TRACE_STAGE_BY_FUNCTION = {
    "initialize_knowledge_vault": "vault_initialize",
    "initialize_autonomous_core": "vault_initialize",
    "compile_source": "source_compile",
    "begin": "compilation_begin",
    "next_packet": "compilation_packet",
    "stage": "compilation_stage",
    "validate": "compilation_validate",
    "_duty_reports": "compilation_inventory",
    "_artifact": "compilation_inventory",
    "commit": "compilation_commit",
    "_statement_value": "statement_verify",
    "_ledger_counts": "statement_verify",
}


class _RuntimeDiagnosticFailure(RuntimeError):
    """Carry only a closed stage and exception category into the raw report."""

    def __init__(self, stage: str, error_type: str) -> None:
        self.stage = stage if stage in _FIXTURE_FAILURE_STAGES else "workspace"
        self.error_type = error_type if error_type in _FIXTURE_FAILURE_TYPES else "other"
        super().__init__(self.reason)

    @property
    def reason(self) -> str:
        return f"fixture_failure:{self.stage}:{self.error_type}"


def _failure_type(error: BaseException) -> str:
    if isinstance(error, PermissionError):
        return "permission_error"
    if isinstance(error, FileNotFoundError):
        return "file_not_found"
    if isinstance(error, AttributeError):
        return "attribute_error"
    if isinstance(error, sqlite3.Error):
        return "sqlite_error"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, json.JSONDecodeError):
        return "json_error"
    if isinstance(error, OSError):
        return "os_error"
    if isinstance(error, ValueError):
        return "value_error"
    if isinstance(error, TypeError):
        return "type_error"
    if isinstance(error, LookupError):
        return "lookup_error"
    if isinstance(error, AssertionError):
        return "assertion_error"
    if isinstance(error, RuntimeError):
        return "runtime_error"
    return "other"


def _fixture_failure(error: BaseException) -> _RuntimeDiagnosticFailure:
    if isinstance(error, _RuntimeDiagnosticFailure):
        return error
    stage = "compilation_inventory"
    traceback = error.__traceback__
    while traceback is not None:
        mapped = _TRACE_STAGE_BY_FUNCTION.get(traceback.tb_frame.f_code.co_name)
        if mapped is not None:
            stage = mapped
            break
        traceback = traceback.tb_next
    return _RuntimeDiagnosticFailure(stage, _failure_type(error))


def _fixture_failure_reason_is_closed(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split(":")
    return (
        len(parts) == 3
        and parts[0] == "fixture_failure"
        and parts[1] in _FIXTURE_FAILURE_STAGES
        and parts[2] in _FIXTURE_FAILURE_TYPES
    )

QUERY_TEXT = "Synthetic runtime stability Statement is queryable through Query v6."


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runner_path() -> Path:
    return Path(__file__).resolve()


def _schema_path() -> Path:
    return _repo_root() / SCHEMA_RELATIVE_PATH


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_reason(value: Any) -> str:
    """Redact local paths and secret-like assignments before they can enter a report."""

    text = str(value)
    text = _LOCAL_PATH.sub("<local-path>", text)
    text = re.sub(
        r"(?i)(api[_ -]?key|password|credential|secret)\s*[:=]\s*\S+",
        r"\1=<redacted>",
        text,
    )
    text = re.sub(r"(?:[A-Za-z0-9._-]+/){2,}[A-Za-z0-9._-]+", "<local-path>", text)
    return text[:500]


def _sha256_path(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes())
    except (OSError, ValueError):
        return "0" * 64


def _sqlite_version() -> str:
    try:
        return str(sqlite3.sqlite_version)
    except Exception:  # pragma: no cover - sqlite3 is part of supported Python builds
        return "unknown"


def _candidate_metadata() -> dict[str, Any]:
    try:
        from deeplaw import __version__

        package_version = str(__version__)
    except ImportError:  # pragma: no cover - package import is required in supported runs
        package_version = "unknown"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_repo_root(),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=_repo_root(),
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        commit = None
        dirty = False
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        commit = None
    return {
        "package_version": package_version,
        "git_commit": commit,
        "working_tree_dirty": dirty,
        "runner": RUNNER_RELATIVE_PATH,
        "runner_sha256": _sha256_path(_runner_path()),
        "schema": SCHEMA_RELATIVE_PATH,
        "schema_sha256": _sha256_path(_schema_path()),
        "source_hashes": {
            QUERY_V6_RELATIVE_PATH: _sha256_path(_repo_root() / QUERY_V6_RELATIVE_PATH),
            PERSISTENT_RUNTIME_RELATIVE_PATH: _sha256_path(
                _repo_root() / PERSISTENT_RUNTIME_RELATIVE_PATH
            ),
            MCP_SERVER_RELATIVE_PATH: _sha256_path(
                _repo_root() / MCP_SERVER_RELATIVE_PATH
            ),
        },
    }


def _environment() -> dict[str, Any]:
    try:
        total_ram = int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        total_ram = None
    return {
        "os": {
            "name": platform.system() or "unknown",
            "release": platform.release() or "unknown",
            "machine": platform.machine() or "unknown",
        },
        "python": platform.python_version(),
        "sqlite": _sqlite_version(),
        "cpu": {
            "logical_count": max(1, int(os.cpu_count() or 1)),
            "model": platform.processor() or "unknown",
        },
        "ram_total_bytes": total_ram,
    }


@contextmanager
def _temporary_workspace(workspace: Path | None) -> Iterator[Path]:
    """Yield an empty private workspace without inspecting an existing Vault."""

    if workspace is None:
        with tempfile.TemporaryDirectory(prefix="deeplaw-v013-runtime-") as value:
            root = Path(value)
            root.chmod(0o700)
            yield root
        return
    root = workspace.expanduser().absolute()
    if root.exists() and any(root.iterdir()):
        raise ValueError("workspace must be absent or empty; existing Vaults are never read")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    yield root


def _statement_value(*, text: str, source_ref: dict[str, str]) -> dict[str, Any]:
    source_refs = [source_ref]
    return {
        "ordinal": 1,
        "char_start": 0,
        "char_end": len(text),
        "statement_text": text,
        "statement_sha256": statement_sha256(text),
        "statement_type": "factual",
        "support_status": "supported",
        "source_refs": source_refs,
        "knowledge_revision_refs": [],
        "relation_revision_refs": [],
        "valid_from": None,
        "valid_to": None,
        "limitation": None,
        "gaps": [],
        "input_set_sha256": build_input_set_sha256(
            source_refs=source_refs,
            knowledge_revision_refs=[],
            relation_revision_refs=[],
            valid_from=None,
            valid_to=None,
            statement_type="factual",
            support_status="supported",
            limitation=None,
            gaps=[],
        ),
    }


def _duty_reports(run_id: str, source: Path) -> list[dict[str, Any]]:
    facts = {
        "source_present": True,
        "source_admitted": True,
        "source_nonempty": True,
        "media_type": "text/markdown",
        "byte_size": source.stat().st_size,
        "lifecycle": "active",
        "node_types": {},
        "signals": {
            key: False for key in ("code", "table", "list", "timeline", "question", "procedure")
        },
        "observation_kinds": {},
        "observation_count": 0,
        "existing_kinds": {},
        "existing_count": 0,
        "relation_count": 0,
        "previous_output_count": 0,
        "affected_synthesis_count": 0,
        "truncated": False,
    }
    facts_sha256 = sha256_bytes(canonical_json(facts).encode("utf-8"))
    return [
        {
            "duty_id": _stable_id("duty", run_id, duty_type),
            "duty_type": duty_type,
            "required": False,
            "applicability": "not_applicable",
            "status": "omitted_with_reason",
            "output_refs": [],
            "evidence_refs": [],
            "reason": "Deterministic runtime stability fixture.",
            "unresolved_items": [],
            "omission_reason": "Synthetic source exercises only Query v6 retrieval.",
            "deterministic_basis": {
                "rule_id": "runtime-stability-v1",
                "facts": facts,
                "stable_refs": [],
                "facts_sha256": facts_sha256,
                "reason": "Deterministic runtime stability fixture.",
            },
        }
        for duty_type in SEMANTIC_DUTIES
    ]


def _stable_id(prefix: str, *parts: str) -> str:
    """Use the project identity helper without making it part of the public report."""

    from deeplaw.util import stable_id

    return stable_id(prefix, *parts)


def _commit_statement_fixture(root: Path, source: Path) -> dict[str, Any]:
    """Build one governed Statement through the public v3 compilation commit path."""

    initialize_knowledge_vault(root, name="DeepLaw v0.13 runtime stability", scope="project")
    initialize_autonomous_core(root)
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
            logical_path="synthetic-runtime-stability.md",
        )
    source_revision_id = str(compiled["identity"]["source_revision_id"])
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="v013-runtime-stability-compiler",
            operations=COMPILER_GRANT_OPERATIONS,
            max_request_bytes=320 * 1024,
            max_mutations_per_minute=120,
            max_objects=100,
        )["grant_id"]
    profile = compiler_profile(version="3")
    coordinator = CompilationCoordinator(root)
    begun = coordinator.begin(
        grant_id=grant_id,
        source_revision_id=source_revision_id,
        compiler_profile=profile["compiler_profile"],
        compiler_profile_version="3",
        host_identity="v013-runtime-stability",
        model_identity=None,
        prompt_template_id=profile["prompt_template_id"],
        prompt_config_sha256=profile["prompt_config_sha256"],
        plan_configuration_sha256=profile["plan_configuration_sha256"],
        confirm_no_case_data=True,
        packet_max_fragments=16,
    )
    packet = coordinator.next_packet(begun["compilation_run_id"])
    if packet is None or len(packet["fragments"]) != 1:
        raise RuntimeError("synthetic source compilation did not produce one fragment")
    fragment = packet["fragments"][0]
    source_ref = {
        "source_revision_id": packet["source_revision_id"],
        "fragment_id": fragment["fragment_id"],
        "locator": fragment["locator"],
        "quote_sha256": fragment["text_sha256"],
    }
    statement_text = QUERY_TEXT
    fragment_text = str(fragment["text"])
    start = fragment_text.find(statement_text)
    if start < 0:
        raise RuntimeError("synthetic source fragment does not contain the Query v6 Statement")
    statement = _statement_value(text=statement_text, source_ref=source_ref)
    action = {
        "action": "create",
        "kind": "claim",
        "semantic_key": "v013:runtime-stability:statement",
        "knowledge_id": None,
        "expected_revision_id": None,
        "title": "Synthetic runtime stability Statement",
        "body": fragment_text,
        "aliases": [],
        "epistemic_state": "supported",
        "source_refs": [source_ref],
        "assertion": None,
        "tags": [],
        "valid_from": None,
        "valid_to": None,
        "applicability": {
            "description": "Synthetic runtime stability diagnostic.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": None,
        "reason": "Persist one evidence-bound synthetic Statement.",
    }
    plan = {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": [action],
        "relation_actions": [],
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": [],
        "coverage": {
            "packet_fragment_count": 1,
            "covered_fragment_ids": [fragment["fragment_id"]],
            "omitted_fragment_ids": [],
            "ratio": 1.0,
            "completeness": "complete",
        },
        "skipped_fragments": [],
        "warnings": [],
    }
    coordinator.stage(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        plan=plan,
        confirm_no_case_data=True,
    )
    validation = coordinator.validate(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    if validation.get("valid") is not True:
        raise RuntimeError("synthetic source compilation validation failed")
    reports = _duty_reports(begun["compilation_run_id"], source)
    applicability_policy_sha256 = policy_digest()
    applicability_sha256 = applicability_digest(
        {
            report["duty_type"]: {
                "applicability": report["applicability"],
                "deterministic_basis": report["deterministic_basis"],
            }
            for report in reports
        }
    )
    inventory = {
        "coverage": {
            "applicability_policy_sha256": applicability_policy_sha256,
            "applicability_digest": applicability_sha256,
            "compilation_run_id": begun["compilation_run_id"],
        },
    }
    inventory["inventory_sha256"] = sha256_bytes(canonical_json(inventory).encode("utf-8"))
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        inventory_digest, _ = _artifact(
            store,
            value=inventory,
            role="semantic_inventory",
            created_at=store._next_transaction_time(),
        )
        store.connection.execute(
            "UPDATE semantic_compilation_runs_v2 SET inventory_sha256 = ? "
            "WHERE compilation_run_id = ?",
            (inventory["inventory_sha256"], begun["compilation_run_id"]),
        )
        store.connection.execute(
            """
            INSERT INTO semantic_inventories_v1(
                artifact_sha256, inventory_sha256, inventory_id,
                compilation_run_id, observation_count, packet_count,
                truncated, recorded_at
            ) VALUES (?, ?, ?, ?, 0, 1, 0, ?)
            """,
            (
                inventory_digest,
                inventory["inventory_sha256"],
                "semanticinventory_" + begun["compilation_run_id"][-24:],
                begun["compilation_run_id"],
                store._next_transaction_time(),
            ),
        )
        for report in reports:
            store.connection.execute(
                """
                INSERT INTO semantic_duty_reports_v1(
                    compilation_run_id, duty_id, duty_type, required,
                    status, report_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    begun["compilation_run_id"],
                    report["duty_id"],
                    report["duty_type"],
                    int(report["required"]),
                    report["status"],
                    canonical_json(report),
                ),
            )
        store.connection.commit()
    publication = {
        "schema_version": "deeplaw.semantic-publication-plan/v3",
        "compiler_profile_version": "3",
        "compilation_run_id": begun["compilation_run_id"],
        "source_revision_id": packet["source_revision_id"],
        "expected_audit_head": begun["input_audit_head"],
        "inventory_sha256": inventory["inventory_sha256"],
        "finalization_packet_id": "finalization_" + "e" * 24,
        "applicability_policy_sha256": applicability_policy_sha256,
        "applicability_digest": applicability_sha256,
        "packet_plans": [{"packet_id": packet["packet_id"]}],
        "statement_plans": [
            {
                "packet_id": packet["packet_id"],
                "object_action_ordinal": 1,
                "statements": [statement],
            }
        ],
        "observation_dispositions": [],
        "duty_reports": reports,
        "semantic_status": "partial",
        "warnings": [],
    }
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        publication_digest, _ = _artifact(
            store,
            value=publication,
            role="publication_plan",
            created_at=store._next_transaction_time(),
        )
        store.connection.execute(
            "UPDATE semantic_compilation_runs_v2 SET publication_plan_sha256 = ? "
            "WHERE compilation_run_id = ?",
            (publication_digest, begun["compilation_run_id"]),
        )
        store.connection.commit()
    committed = coordinator.commit(
        grant_id=grant_id,
        compilation_run_id=begun["compilation_run_id"],
        confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        row = store.connection.execute(
            "SELECT statement_id FROM knowledge_statements_v1 ORDER BY statement_id LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("synthetic compilation did not commit a Statement")
        statement_id = str(row["statement_id"])
    return {
        "statement_id": statement_id,
        "statement_text_sha256": statement["statement_sha256"],
        "source_revision_id": source_revision_id,
        "audit_head": str(committed.get("audit_head") or ""),
    }


@dataclass(frozen=True)
class _LedgerCounts:
    legacy_events: int
    autonomous_events: int

    @property
    def total(self) -> int:
        return self.legacy_events + self.autonomous_events

    def as_dict(self) -> dict[str, int]:
        return {
            "legacy_events": self.legacy_events,
            "autonomous_events": self.autonomous_events,
            "total": self.total,
        }


def _ledger_counts(root: Path) -> _LedgerCounts:
    with (
        KnowledgeVault(root, read_only=True) as legacy,
        AutonomousKnowledgeStore(root, read_only=True, legacy_snapshot=legacy) as store,
    ):
        legacy_events = int(
            legacy.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        )
        autonomous_events = int(
            store.connection.execute("SELECT COUNT(*) FROM autonomous_events_v3").fetchone()[0]
        )
    return _LedgerCounts(legacy_events=legacy_events, autonomous_events=autonomous_events)


def _ledger_measurement(before: _LedgerCounts, after: _LedgerCounts) -> dict[str, Any]:
    return {
        "canonical_ledger_event_count_before": before.total,
        "canonical_ledger_event_count_after": after.total,
        "canonical_ledger_unchanged": before.total == after.total,
        "ledger_counts_before": before.as_dict(),
        "ledger_counts_after": after.as_dict(),
    }


def _query_identity(result: Mapping[str, Any]) -> str:
    plan = result.get("query_plan")
    if not isinstance(plan, Mapping):
        raise RuntimeError("Query v6 result has no query plan")
    selection = plan.get("selection")
    selection_ids = selection.get("statement_ids", []) if isinstance(selection, Mapping) else []
    identity = {
        "vault_id": result.get("vault_id"),
        "input_audit_head": plan.get("input_audit_head"),
        "input_legacy_audit_head": plan.get("input_legacy_audit_head"),
        "statement_ids": selection_ids,
    }
    return sha256_bytes(canonical_json(identity).encode("utf-8"))


class _McpLifespan:
    """Synchronous bridge for one actual knowledge-support MCP lifespan."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: Any = None
        self.runtime: Any = None
        self._lifespan: Any = None
        self._request_id = 0

    def __enter__(self) -> _McpLifespan:
        self.loop = asyncio.new_event_loop()
        try:
            self.server = create_knowledge_mcp_server(vault_path=self.vault_path)
            self._lifespan = self.server.lifespan(self.server)
            self.runtime = self.loop.run_until_complete(self._lifespan.__aenter__())
        except BaseException:
            self._close_after_failed_enter()
            raise
        return self

    def _close_after_failed_enter(self) -> None:
        loop = self.loop
        self._lifespan = None
        self.runtime = None
        self.server = None
        self.loop = None
        if loop is not None and not loop.is_closed():
            loop.close()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> bool:
        loop = self.loop
        lifespan = self._lifespan
        try:
            if loop is not None and lifespan is not None:
                return bool(
                    loop.run_until_complete(
                        lifespan.__aexit__(exc_type, exc_value, traceback)
                    )
                )
            return False
        finally:
            self._lifespan = None
            self.runtime = None
            self.server = None
            self.loop = None
            if loop is not None and not loop.is_closed():
                loop.close()

    async def _call_async(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.server is None or self.runtime is None:
            raise RuntimeError("MCP lifespan is not open")
        handler = self.server.request_handlers[types.CallToolRequest]
        token = request_ctx.set(
            RequestContext(
                request_id=self._request_id,
                meta=None,
                session=None,
                lifespan_context=self.runtime,
            )
        )
        try:
            response = await handler(
                types.CallToolRequest(
                    params=types.CallToolRequestParams(
                        name="knowledge_support",
                        arguments=arguments,
                    )
                )
            )
        finally:
            request_ctx.reset(token)
        if response.root.isError:
            raise RuntimeError("knowledge_support returned an error response")
        structured = response.root.structuredContent
        if not isinstance(structured, dict):
            raise RuntimeError("knowledge_support returned no structured response")
        return structured

    def call(self, *, ordinal: int = 0) -> dict[str, Any]:
        if self.loop is None or self.loop.is_closed():
            raise RuntimeError("MCP lifespan is not open")
        self._request_id += 1
        query = QUERY_TEXT if ordinal == 0 else f"{QUERY_TEXT} probe-{ordinal:05d}"
        return self.loop.run_until_complete(
            self._call_async(
                {
                    "operation": "query",
                    "query": query,
                    "query_target": {"text": QUERY_TEXT},
                    "query_plan_version": QUERY_PLAN_VERSION,
                    "capsule_projection": PROJECTION,
                    "applicable_duties": ["primary_answer"],
                }
            )
        )


def _rss_method() -> str | None:
    system = platform.system()
    if system == "Linux":
        return "linux_procfs_statm_current_rss"
    if system == "Darwin":
        return "macos_ps_current_rss"
    return None


def _current_rss_bytes() -> int | None:
    """Read current RSS; never use ru_maxrss, which is a monotonic peak metric."""

    system = platform.system()
    if system == "Linux":
        try:
            fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
            if len(fields) < 2:
                return None
            resident_pages = int(fields[1])
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            if resident_pages < 0 or page_size <= 0:
                return None
            return resident_pages * page_size
        except (OSError, TypeError, ValueError):
            return None
    if system == "Darwin":
        try:
            ps = next(
                (
                    candidate
                    for candidate in ("/bin/ps", "/usr/bin/ps")
                    if Path(candidate).is_file()
                ),
                None,
            )
            if ps is None:
                return None
            completed = subprocess.run(
                [ps, "-o", "rss=", "-p", str(os.getpid())],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
                env={"PATH": os.defpath},
            )
            value = int(completed.stdout.strip())
            return value * 1024 if value >= 0 else None
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return None
    return None


def _rss_result(
    *,
    status: str,
    request_count: int,
    warmup_requests: int,
    attempted: int,
    successful: int,
    failed: int,
    start: int | None,
    end: int | None,
    errors: Sequence[str],
    ledger_before: _LedgerCounts,
    ledger_after: _LedgerCounts,
    reason: str | None = None,
) -> dict[str, Any]:
    growth: float | None = None
    if start is not None and end is not None and start > 0:
        growth = round((end - start) * 100 / start, 6)
    target_10k_executed = (
        request_count == FROZEN_REQUEST_COUNT and attempted == FROZEN_REQUEST_COUNT
    )
    threshold_passed = (
        growth <= RSS_GROWTH_LIMIT_PERCENT
        if target_10k_executed and status == "executed" and growth is not None
        else None
    )
    return {
        "status": status,
        "request_count": request_count,
        "target_request_count": FROZEN_REQUEST_COUNT,
        "warmup_requests": warmup_requests,
        "attempted_requests": attempted,
        "successful_requests": successful,
        "failed_requests": failed,
        "measurement_method": _rss_method() or "unsupported_platform_current_rss_unavailable",
        "start_rss_bytes": start,
        "end_rss_bytes": end,
        "peak_rss_bytes": None,
        "relative_growth_percent": growth,
        "growth_limit_percent": RSS_GROWTH_LIMIT_PERCENT,
        "growth_limit_passed": threshold_passed,
        "errors": [_safe_reason(item) for item in errors[:8]],
        "reason": _safe_reason(reason) if reason else None,
        "target_10k_executed": target_10k_executed,
        **_ledger_measurement(ledger_before, ledger_after),
        "limitations": [
            "Current RSS is sampled only before and after the read loop; peak is unavailable.",
            "The workload is synthetic and claim-ineligible; it is not a release gate.",
            *(["The frozen 10,000-request target was not run; no smaller run is substituted."]
              if request_count != FROZEN_REQUEST_COUNT
              else []),
        ],
    }


def _child_rss(vault_path: Path, *, request_count: int, warmup_requests: int) -> dict[str, Any]:
    method = _rss_method()
    before = _ledger_counts(vault_path)
    if method is None:
        return _rss_result(
            status="not_executed",
            request_count=request_count,
            warmup_requests=warmup_requests,
            attempted=0,
            successful=0,
            failed=0,
            start=None,
            end=None,
            errors=[],
            ledger_before=before,
            ledger_after=before,
            reason="current RSS measurement is supported only on macOS and Linux",
        )
    attempted = 0
    successful = 0
    errors: list[str] = []
    start: int | None = None
    end: int | None = None
    try:
        with _McpLifespan(vault_path) as lifespan:
            for _ in range(warmup_requests):
                try:
                    lifespan.call(ordinal=0)
                except Exception as error:
                    errors.append(f"warmup {type(error).__name__}")
            start = _current_rss_bytes()
            for ordinal in range(1, request_count + 1):
                attempted += 1
                try:
                    response = lifespan.call(ordinal=ordinal)
                    result = response.get("result")
                    if not isinstance(result, Mapping):
                        raise RuntimeError("Query v6 provider result is invalid")
                    receipt = result.get("receipt")
                    if not isinstance(receipt, Mapping) or not isinstance(
                        receipt.get("receipt_id"), str
                    ):
                        raise RuntimeError("Query v6 receipt is invalid")
                    if result.get("delivery", {}).get("write_performed") is not False:
                        raise RuntimeError("Query v6 read unexpectedly reported a write")
                    successful += 1
                except Exception as error:
                    errors.append(f"request {ordinal}: {type(error).__name__}")
            end = _current_rss_bytes()
    except Exception as error:
        errors.append(f"lifespan {type(error).__name__}")
    after = _ledger_counts(vault_path)
    status = "executed" if attempted == request_count and successful == request_count else "fail"
    if start is None or end is None:
        status = "degraded" if successful == request_count else "fail"
    return _rss_result(
        status=status,
        request_count=request_count,
        warmup_requests=warmup_requests,
        attempted=attempted,
        successful=successful,
        failed=request_count - successful,
        start=start,
        end=end,
        errors=errors,
        ledger_before=before,
        ledger_after=after,
        reason=(
            "one or more child-process Query v6 requests failed"
            if successful != request_count
            else "current RSS could not be sampled reliably"
            if start is None or end is None
            else None
        ),
    )


def _run_rss_child(vault_path: Path, *, request_count: int, warmup_requests: int) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "benchmarks.v013.runtime_stability",
        "--child-rss",
        "--vault",
        str(vault_path),
        "--request-count",
        str(request_count),
        "--warmup-requests",
        str(warmup_requests),
    ]
    isolated_home = vault_path.parent / ".runtime-child-home"
    isolated_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    child_environment = _build_subprocess_environment(
        overrides={"HOME": str(isolated_home)}
    )
    child_environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=MAX_CHILD_TIMEOUT_SECONDS,
            cwd=_repo_root(),
            env=child_environment,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise RuntimeError(f"RSS child process failed: {type(error).__name__}") from error
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"RSS child process returned no report ({completed.returncode})")
    try:
        value = json.loads(lines[-1])
    except (TypeError, ValueError) as error:
        raise RuntimeError("RSS child process returned invalid JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("RSS child process report is not an object")
    if completed.returncode != 0:
        value.setdefault("status", "fail")
        value.setdefault("reason", "RSS child process exited non-zero")
    return value


def _reader_once(vault_path: Path, barrier: Barrier, index: int) -> dict[str, Any]:
    runtime: PersistentReadRuntime | None = None
    try:
        runtime = PersistentReadRuntime(vault_path)
        snapshot = runtime.snapshot
        read_only = bool(snapshot.legacy.read_only and snapshot.store.read_only)
        barrier.wait(timeout=60)
        from deeplaw.retrieval import PurposeAwareRetrievalService

        result = PurposeAwareRetrievalService(vault_path).query(
            QUERY_TEXT,
            query_plan_version=QUERY_PLAN_VERSION,
            projection=PROJECTION,
            applicable_duties=("primary_answer",),
            _runtime_snapshot=snapshot,
        )
        receipt_id = result.get("receipt_id")
        if not isinstance(receipt_id, str):
            raise RuntimeError("reader Query v6 receipt is invalid")
        if result.get("write_performed") is not False:
            raise RuntimeError("reader Query v6 result is not read-only")
        return {
            "index": index,
            "success": True,
            "receipt_id": receipt_id,
            "identity_digest": _query_identity(result),
            "read_only": read_only,
        }
    except Exception as error:
        return {
            "index": index,
            "success": False,
            "receipt_id": None,
            "identity_digest": None,
            "read_only": False,
            "error": _safe_reason(f"{type(error).__name__}"),
        }
    finally:
        if runtime is not None:
            runtime.close()


def _run_concurrent_readers(vault_path: Path) -> dict[str, Any]:
    before = _ledger_counts(vault_path)
    barrier = Barrier(READER_COUNT)
    with ThreadPoolExecutor(max_workers=READER_COUNT) as pool:
        futures = [
            pool.submit(_reader_once, vault_path, barrier, index)
            for index in range(READER_COUNT)
        ]
        readers = [future.result() for future in futures]
    after = _ledger_counts(vault_path)
    successes = [item for item in readers if item.get("success") is True]
    receipt_ids = [
        item["receipt_id"]
        for item in successes
        if isinstance(item.get("receipt_id"), str)
    ]
    identities = [
        item["identity_digest"]
        for item in successes
        if isinstance(item.get("identity_digest"), str)
    ]
    read_only_flags = [item.get("read_only") is True for item in successes]
    receipt_consistent = len(receipt_ids) == READER_COUNT and len(set(receipt_ids)) == 1
    identity_consistent = len(identities) == READER_COUNT and len(set(identities)) == 1
    read_only_consistent = len(read_only_flags) == READER_COUNT and all(read_only_flags)
    errors = [str(item["error"]) for item in readers if item.get("error")]
    ok = (
        len(successes) == READER_COUNT
        and receipt_consistent
        and identity_consistent
        and read_only_consistent
    )
    return {
        "status": "executed" if ok else "fail",
        "reader_count": READER_COUNT,
        "successful_readers": len(successes),
        "failed_readers": READER_COUNT - len(successes),
        "barrier_synchronized": len(readers) == READER_COUNT,
        "receipt_consistent": receipt_consistent,
        "identity_consistent": identity_consistent,
        "read_only_consistent": read_only_consistent,
        "receipt_ids": receipt_ids,
        "identity_digests": identities,
        "read_only_flags": read_only_flags,
        "errors": [_safe_reason(item) for item in errors[:8]],
        "reason": None if ok else "8-reader read-only consistency check failed",
        **_ledger_measurement(before, after),
    }


def _empty_rss(*, request_count: int, warmup_requests: int, reason: str) -> dict[str, Any]:
    zero = _LedgerCounts(legacy_events=0, autonomous_events=0)
    return _rss_result(
        status="not_executed",
        request_count=request_count,
        warmup_requests=warmup_requests,
        attempted=0,
        successful=0,
        failed=0,
        start=None,
        end=None,
        errors=[],
        ledger_before=zero,
        ledger_after=zero,
        reason=reason,
    )


def _digest_body(report: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(report)
    body.pop("report_sha256", None)
    return body


def _build_fixture_report(root: Path) -> dict[str, Any]:
    vault = root / "vault"
    source = root / "synthetic-runtime-stability.md"
    try:
        source.write_bytes(
            ("# Synthetic runtime stability\n" + QUERY_TEXT + "\n").encode("utf-8")
        )
    except Exception as error:
        raise _RuntimeDiagnosticFailure("source_write", _failure_type(error)) from error
    try:
        fixture = _commit_statement_fixture(vault, source)
        counts = _ledger_counts(vault)
    except Exception as error:
        raise _fixture_failure(error) from error
    return {
        "status": "executed",
        "construction": "public_profile_v3_compilation",
        "statement_count": 1,
        "statement_id": fixture["statement_id"],
        "statement_text_sha256": fixture["statement_text_sha256"],
        "source_revision_id": fixture["source_revision_id"],
        "canonical_ledger_event_count": counts.total,
    }


def build_report(
    *,
    request_count: int = DEFAULT_REQUEST_COUNT,
    warmup_requests: int = DEFAULT_WARMUP_REQUESTS,
    execute_10k: bool = False,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """Build a claim-ineligible smoke or explicit frozen 10k runtime report."""

    if isinstance(request_count, bool) or not 1 <= request_count <= FROZEN_REQUEST_COUNT:
        raise ValueError("request_count must be between 1 and 10000")
    if isinstance(warmup_requests, bool) or not 0 <= warmup_requests <= 1_000:
        raise ValueError("warmup_requests must be between 0 and 1000")
    if not isinstance(execute_10k, bool):
        raise ValueError("execute_10k must be boolean")
    selected_count = FROZEN_REQUEST_COUNT if execute_10k else request_count
    if request_count == FROZEN_REQUEST_COUNT and not execute_10k:
        requested_10k_reason = "the frozen 10,000-request lane requires explicit --execute-10k"
    else:
        requested_10k_reason = None
    with _temporary_workspace(workspace) as root:
        try:
            fixture = _build_fixture_report(root)
            vault = root / "vault"
            ledger_before = _ledger_counts(vault)
            if requested_10k_reason:
                rss = _empty_rss(
                    request_count=selected_count,
                    warmup_requests=warmup_requests,
                    reason=requested_10k_reason,
                )
            else:
                rss = _run_rss_child(
                    vault,
                    request_count=selected_count,
                    warmup_requests=warmup_requests,
                )
            concurrent = _run_concurrent_readers(vault)
            ledger_after = _ledger_counts(vault)
            fixture["canonical_ledger_event_count"] = ledger_before.total
        except Exception as error:
            failure = _fixture_failure(error)
            reason = failure.reason
            fixture = {
                "status": "fail",
                "construction": "public_profile_v3_compilation",
                "statement_count": 0,
                "statement_id": None,
                "statement_text_sha256": None,
                "source_revision_id": None,
                "canonical_ledger_event_count": 0,
            }
            zero = _LedgerCounts(legacy_events=0, autonomous_events=0)
            rss = _empty_rss(
                request_count=selected_count,
                warmup_requests=warmup_requests,
                reason=reason,
            )
            concurrent = {
                "status": "not_executed",
                "reader_count": READER_COUNT,
                "successful_readers": 0,
                "failed_readers": READER_COUNT,
                "barrier_synchronized": False,
                "receipt_consistent": False,
                "identity_consistent": False,
                "read_only_consistent": False,
                "receipt_ids": [],
                "identity_digests": [],
                "read_only_flags": [],
                "errors": [],
                "reason": reason,
                **_ledger_measurement(zero, zero),
            }
            ledger_before = zero
            ledger_after = zero
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "claim_eligible": False,
        "claim_ineligibility_reason": (
            "Synthetic source-free local diagnostic; it is not quality evidence, a competitive "
            "claim, RC/GA evidence, or a release gate."
        ),
        "profile": "runtime_stability_diagnostic",
        "generated_at_utc": _utc_now(),
        "release_gate_passed": False,
        "candidate": _candidate_metadata(),
        "environment": _environment(),
        "configuration": {
            "request_count": selected_count,
            "target_request_count": FROZEN_REQUEST_COUNT,
            "rss_growth_limit_percent": RSS_GROWTH_LIMIT_PERCENT,
            "warmup_requests": warmup_requests,
            "execute_10k": execute_10k,
            "reader_count": READER_COUNT,
            "query_plan_version": QUERY_PLAN_VERSION,
            "projection": PROJECTION,
            "query_text_sha256": sha256_bytes(QUERY_TEXT.encode("utf-8")),
        },
        "fixture": fixture,
        "rss_stability": rss,
        "concurrent_readers": concurrent,
        "canonical_ledger": {
            "event_count_before": ledger_before.total,
            "event_count_after": ledger_after.total,
            "unchanged": ledger_before.total == ledger_after.total,
            "counts_before": ledger_before.as_dict(),
            "counts_after": ledger_after.as_dict(),
        },
        "limitations": [
            "No network, model, credential, environment Secret, private Vault, or case "
            "data is read.",
            "All reads use a temporary source-free synthetic Vault and are claim-ineligible.",
            "Query v6 reads must not mutate canonical Ledger events; before/after counts "
            "are retained.",
            "Current RSS is not ru_maxrss: only macOS ps or Linux procfs current samples "
            "are accepted.",
            "The 10,000-request lane executes only with explicit --execute-10k; smoke "
            "counts are not 10k.",
        ],
        "rerun_commands": [
            "uv run --frozen python -m benchmarks.v013.runtime_stability --output REPORT.json",
            "uv run --frozen python -m benchmarks.v013.runtime_stability "
            "--output REPORT.json --execute-10k",
        ],
    }
    report["report_sha256"] = sha256_bytes(
        canonical_json(_digest_body(report)).encode("utf-8")
    )
    return report


def verify_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": ["report must be an object"]}
    report = dict(value)
    errors: list[str] = []
    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors.extend(
            error.message
            for error in Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).iter_errors(report)
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        errors.append(f"schema validation unavailable: {type(error).__name__}")
    if report.get("report_sha256") != sha256_bytes(
        canonical_json(_digest_body(report)).encode("utf-8")
    ):
        errors.append("report digest mismatch")
    if report.get("claim_eligible") is not False:
        errors.append("claim eligibility is not fail-closed")
    if report.get("release_gate_passed") is not False:
        errors.append("release gate is not fail-closed")
    fixture = report.get("fixture")
    if isinstance(fixture, Mapping) and fixture.get("status") == "fail":
        rss_reason = report.get("rss_stability", {}).get("reason")
        concurrent_reason = report.get("concurrent_readers", {}).get("reason")
        if not _fixture_failure_reason_is_closed(rss_reason):
            errors.append("fixture failure lacks a closed RSS reason")
        if concurrent_reason != rss_reason:
            errors.append("fixture failure reason differs across unexecuted lanes")
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    if _LOCAL_PATH.search(serialized):
        errors.append("report contains a local absolute path")
    if _SENSITIVE_MARKER.search(serialized):
        errors.append("report contains a credential or secret marker")
    return {"valid": not errors, "errors": errors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the claim-ineligible v0.13 read-runtime stability diagnostic."
    )
    parser.add_argument("--output", type=Path, required=False)
    parser.add_argument("--request-count", type=int, default=DEFAULT_REQUEST_COUNT)
    parser.add_argument("--warmup-requests", type=int, default=DEFAULT_WARMUP_REQUESTS)
    parser.add_argument("--execute-10k", action="store_true")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--child-rss", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--vault", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.child_rss:
        if args.vault is None:
            raise SystemExit("--vault is required for --child-rss")
        result = _child_rss(
            args.vault.expanduser().absolute(),
            request_count=args.request_count,
            warmup_requests=args.warmup_requests,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") in {"executed", "degraded", "not_executed"} else 2
    if args.output is None:
        raise SystemExit("--output is required")
    report = build_report(
        request_count=args.request_count,
        warmup_requests=args.warmup_requests,
        execute_10k=args.execute_10k,
        workspace=args.workspace,
    )
    output = args.output.expanduser().absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if verify_report(report)["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
