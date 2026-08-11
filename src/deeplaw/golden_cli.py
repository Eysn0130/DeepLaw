from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, cast

from . import __version__
from .context_compiler import verify_capsule_file
from .knowledge_feedback import create_run_receipt, record_structured_feedback
from .knowledge_jobs import (
    cancel_ingest_job,
    create_ingest_job,
    create_snapshot_ingest_job,
    list_ingest_jobs,
    plan_registered_sync,
    run_ingest_job,
    run_registered_sync,
)
from .knowledge_maintenance import knowledge_doctor
from .knowledge_markdown import export_knowledge_markdown
from .knowledge_models import Sensitivity
from .knowledge_service import (
    auto_aware_knowledge_vault,
    initialize_default_knowledge_vault,
    source_knowledge_status_for_result,
)
from .knowledge_store import (
    VAULT_SCOPES,
    KnowledgeVault,
    VaultScope,
    default_knowledge_vault,
)
from .relation_workflow import (
    pending_relation_carry_forward,
    plan_relation_carry_forward,
    propose_relation_carry_forward,
    review_relation_carry_forward,
)
from .retrieval_fabric import RETRIEVAL_MODES, RetrievalMode, recall, retrieve
from .source_connectors import (
    MAX_SOURCE_SNAPSHOT_BYTES,
    capture_git_sources,
    capture_https_source,
    plan_git_source,
    plan_https_source,
)
from .util import canonical_json, sha256_bytes, strict_json_loads

PROJECT_CONFIG_SCHEMA = "deeplaw.local-project/v1"
GOLDEN_COMMANDS = frozenset(
    {"init", "add", "sync", "review", "recall", "explain", "feedback", "status", "open"}
)

_PROJECT_CONFIG_NAME = ".deeplaw.json"
_MAX_PROJECT_CONFIG_BYTES = 64 * 1024
_MAX_LAST_CAPSULE_BYTES = 512 * 1024
_MAX_LAST_TRACE_BYTES = 8 * 1024 * 1024


def _add_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("human", "json", "jsonl"), default="human")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-color", action="store_true")


def _add_vault(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--project-root", type=Path)


def add_golden_parsers(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    init = commands.add_parser("init", help="Initialize a local Agent Knowledge vault")
    init.add_argument("vault", type=Path, nargs="?", default=default_knowledge_vault())
    init.add_argument("--name")
    init.add_argument("--scope", choices=sorted(VAULT_SCOPES), default="project")
    init.add_argument("--project-root", type=Path, default=Path.cwd())
    _add_output_options(init)

    add = commands.add_parser("add", help="Plan and run a resumable source ingest job")
    add.add_argument("source", type=Path, nargs="?")
    _add_vault(add)
    add.add_argument(
        "--url",
        help="Capture one explicit public HTTPS source as an immutable owner-only snapshot",
    )
    add.add_argument(
        "--expected-sha256",
        help="Require downloaded HTTPS bytes to match this SHA-256",
    )
    add.add_argument("--max-download-bytes", type=int, help="HTTPS byte cap (maximum 64 MiB)")
    add.add_argument(
        "--network-timeout",
        type=float,
        help="HTTPS timeout in seconds (1 through 120)",
    )
    add.add_argument(
        "--confirm-network",
        action="store_true",
        help="Explicitly authorize this one HTTPS capture",
    )
    add.add_argument(
        "--git-repository",
        type=Path,
        help="Read one existing local Git repository without clone or checkout",
    )
    add.add_argument("--git-revision", help="Exact full 40- or 64-hex commit object ID")
    add.add_argument(
        "--git-repository-id",
        help="Stable non-secret repository name used in canonical source identity",
    )
    add.add_argument(
        "--confirm-local-repository",
        action="store_true",
        help="Explicitly authorize snapshotting the selected local repository",
    )
    add.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=None)
    add.add_argument("--include", action="append", default=[])
    add.add_argument("--exclude", action="append", default=[])
    add.add_argument(
        "--source-kind",
        choices=("document", "conversation", "tool_result", "code", "web", "database"),
        default=None,
    )
    add.add_argument("--trust", choices=("untrusted", "user_provided"), default=None)
    add.add_argument(
        "--sensitivity",
        choices=("public", "internal", "private", "restricted"),
        default="private",
    )
    add.add_argument(
        "--typed-extraction",
        choices=(
            "off",
            "deterministic-v1",
            "deterministic-v2",
            "local-model-v1",
            "external-model-explicit",
        ),
        default="deterministic-v2",
    )
    add.add_argument("--typed-extractor-manifest", type=Path)
    add.add_argument("--confirm-external-disclosure", action="store_true")
    add.add_argument("--no-reference-proposals", action="store_true")
    add.add_argument(
        "--pdf-fallback",
        choices=("off", "vision-consensus", "document-engine"),
        default="off",
    )
    add.add_argument("--dry-run", action="store_true")
    add.add_argument("--confirm-no-case-data", action="store_true")
    job_action = add.add_mutually_exclusive_group()
    job_action.add_argument("--resume", metavar="JOB_ID")
    job_action.add_argument("--retry", metavar="JOB_ID")
    job_action.add_argument("--cancel", metavar="JOB_ID")
    _add_output_options(add)

    sync = commands.add_parser("sync", help="Synchronize registered local source roots")
    _add_vault(sync)
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--watch", action="store_true")
    sync.add_argument("--interval", type=float, default=2.0)
    sync.add_argument("--max-cycles", type=int)
    _add_output_options(sync)

    review = commands.add_parser("review", help="Review the current proposal queue")
    _add_vault(review)
    review.add_argument("--interactive", action="store_true")
    decision = review.add_mutually_exclusive_group()
    decision.add_argument("--approve-all", action="store_true")
    decision.add_argument("--reject-all", action="store_true")
    review.add_argument("--confirm-reviewed", action="store_true")
    review.add_argument("--confirm-quarantine", action="store_true")
    review.add_argument("--reviewer-id", default="local-operator")
    review.add_argument("--reason", default="Reviewed through the DeepLaw Golden Path.")
    review.add_argument("--limit", type=int, default=100)
    _add_output_options(review)

    recall_parser = commands.add_parser(
        "recall", help="Create a query plan and verified Knowledge Capsule"
    )
    recall_parser.add_argument("query")
    _add_vault(recall_parser)
    recall_parser.add_argument("--goal")
    recall_parser.add_argument("--mode", choices=sorted(RETRIEVAL_MODES), default="auto")
    recall_parser.add_argument("--max-items", type=int, default=8)
    recall_parser.add_argument("--max-chars", type=int, default=6_000)
    recall_parser.add_argument("--max-tokens", type=int, default=4_096)
    recall_parser.add_argument("--as-of")
    recall_parser.add_argument("--discovery-index", type=Path)
    recall_parser.add_argument("--model-root", type=Path)
    recall_parser.add_argument("--threads", type=int)
    recall_parser.add_argument("--reranker-manifest", type=Path)
    recall_parser.add_argument("--output", type=Path)
    recall_parser.add_argument("--include-restricted", action="store_true")
    recall_parser.add_argument("--confirm-no-case-data", action="store_true")
    _add_output_options(recall_parser)

    explain = commands.add_parser("explain", help="Explain the last recall or one new query")
    explain.add_argument("query", nargs="?")
    _add_vault(explain)
    explain.add_argument("--last", action="store_true")
    explain.add_argument("--mode", choices=sorted(RETRIEVAL_MODES), default="auto")
    explain.add_argument("--as-of")
    explain.add_argument("--discovery-index", type=Path)
    explain.add_argument("--model-root", type=Path)
    explain.add_argument("--threads", type=int)
    explain.add_argument("--reranker-manifest", type=Path)
    explain.add_argument("--confirm-no-case-data", action="store_true")
    _add_output_options(explain)

    feedback = commands.add_parser(
        "feedback", help="Bind structured feedback to the last verified Capsule"
    )
    _add_vault(feedback)
    feedback.add_argument("--outcome", choices=("success", "partial", "failure"), required=True)
    feedback.add_argument("--observation", required=True)
    feedback.add_argument("--recommended-action", required=True)
    feedback.add_argument("--mark-selected-helpful", action="store_true")
    feedback.add_argument("--missing-knowledge", action="append", default=[])
    feedback.add_argument("--missing-source", action="append", default=[])
    feedback.add_argument("--incorrect-relation", action="append", default=[])
    feedback.add_argument("--budget-failure", action="append", default=[])
    feedback.add_argument(
        "--sensitivity",
        choices=("public", "internal", "private", "restricted"),
        default="private",
    )
    feedback.add_argument("--confirm-no-case-data", action="store_true")
    _add_output_options(feedback)

    status = commands.add_parser("status", help="Show vault readiness and current local work")
    _add_vault(status)
    status.add_argument("--jobs", action="store_true")
    _add_output_options(status)

    open_parser = commands.add_parser(
        "open", help="Open the local operator workbench or projection"
    )
    _add_vault(open_parser)
    open_parser.add_argument("--projection", type=Path)
    open_parser.add_argument("--replace", action="store_true")
    open_parser.add_argument("--obsidian", action="store_true")
    open_parser.add_argument("--print-uri", action="store_true")
    _add_output_options(open_parser)


def _project_config_digest(value: dict[str, Any]) -> str:
    return sha256_bytes(
        canonical_json(
            {key: item for key, item in value.items() if key != "record_sha256"}
        ).encode()
    )


def _write_project_config(project_root: Path, *, vault: Path, vault_id: str) -> Path:
    root = project_root.expanduser().absolute()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise RuntimeError("project root is unsafe")
    root.mkdir(parents=True, exist_ok=True)
    path = root / _PROJECT_CONFIG_NAME
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("project vault locator is unsafe")
    body = {
        "schema_version": PROJECT_CONFIG_SCHEMA,
        "vault_id": vault_id,
        "vault_path_hint": str(vault.expanduser().absolute()),
        "local_only": True,
    }
    value = {**body, "record_sha256": sha256_bytes(canonical_json(body).encode())}
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


def _read_project_config(path: Path) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= _MAX_PROJECT_CONFIG_BYTES
    ):
        raise RuntimeError("project vault locator is missing or unsafe")
    value = strict_json_loads(path.read_bytes())
    if (
        not isinstance(value, dict)
        or set(value)
        != {"schema_version", "vault_id", "vault_path_hint", "local_only", "record_sha256"}
        or value.get("schema_version") != PROJECT_CONFIG_SCHEMA
        or value.get("local_only") is not True
        or value.get("record_sha256") != _project_config_digest(value)
    ):
        raise RuntimeError("project vault locator does not match its closed contract")
    return value


def resolve_golden_vault(
    explicit: Path | None,
    *,
    project_root: Path | None = None,
) -> Path:
    if explicit is not None:
        return explicit.expanduser().absolute()
    configured = os.environ.get("DEEPLAW_KNOWLEDGE_VAULT")
    if configured:
        return Path(configured).expanduser().absolute()
    start = (project_root or Path.cwd()).expanduser().absolute()
    for directory in (start, *start.parents):
        locator = directory / _PROJECT_CONFIG_NAME
        if locator.exists() or locator.is_symlink():
            value = _read_project_config(locator)
            vault = Path(value["vault_path_hint"]).expanduser().absolute()
            with KnowledgeVault(vault, read_only=True) as selected:
                if selected.vault_id != value["vault_id"]:
                    raise RuntimeError("project vault locator identity no longer matches")
            return vault
    return default_knowledge_vault()


def _retrieval_root(vault: KnowledgeVault) -> Path:
    root = vault.root / "derived" / "retrieval"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise RuntimeError("local retrieval state directory is unsafe")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(root.parent, 0o700)
        os.chmod(root, 0o700)
    return root


def _atomic_sidecar(path: Path, value: dict[str, Any], *, maximum: int) -> None:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if not 1 <= len(payload) <= maximum:
        raise ValueError("local Golden Path state exceeds its size bound")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("local Golden Path state path is unsafe")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_sidecar(path: Path, *, maximum: int) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= maximum
        or (os.name != "nt" and path.stat().st_mode & 0o077)
    ):
        raise RuntimeError("last Golden Path state is unavailable or unsafe")
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError("last Golden Path state is invalid")
    return value


def _last_capsule_path(vault: KnowledgeVault) -> Path:
    return _retrieval_root(vault) / "last-capsule.json"


def _last_trace_path(vault: KnowledgeVault) -> Path:
    return _retrieval_root(vault) / "last-trace.json"


def _selected_asset_ids(capsule: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        item["asset_id"]
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        )
        for item in capsule[group]
    )


def _review_queue(vault: KnowledgeVault, *, limit: int) -> dict[str, Any]:
    queue = vault.review_queue(limit=limit)
    relation_queue = pending_relation_carry_forward(vault, limit=limit)
    relation_plan = plan_relation_carry_forward(vault, limit=limit)
    pending_sources = [
        source
        for source in vault.all_sources()
        if source.get("status") == "pending"
    ]
    return {
        "schema_version": "deeplaw.golden-review-queue/v1",
        "vault_id": vault.vault_id,
        "pending_source_count": len(pending_sources),
        "pending_sources": [
            {
                "source_id": source["source_id"],
                "title": source["title"],
                "logical_path": source.get("logical_path"),
                "proposal_count": vault.source_review_manifest(source["source_id"])[
                    "proposal_count"
                ],
                "quarantine_count": vault.source_review_manifest(source["source_id"])[
                    "quarantine_count"
                ],
            }
            for source in pending_sources[:limit]
        ],
        "proposal_count": queue["total"],
        "proposals": queue["items"],
        "relation_proposal_count": relation_queue["total"],
        "relation_proposals": relation_queue["items"],
        "relation_plan": {
            "candidate_count": relation_plan["candidate_count"],
            "carry_forward_candidate_count": relation_plan[
                "carry_forward_candidate_count"
            ],
            "full_review_candidate_count": relation_plan[
                "full_review_candidate_count"
            ],
            "blocked_count": relation_plan["blocked_count"],
            "blocked": relation_plan["blocked"],
            "automatic_activation": False,
        },
        "truncated": (
            queue["truncated"]
            or relation_queue["truncated"]
            or relation_plan["truncated"]
            or len(pending_sources) > limit
        ),
    }


def _interactive_source_decision(
    vault: KnowledgeVault,
    source: dict[str, Any],
    *,
    reviewer_id: str,
    reason: str,
    confirm_quarantine: bool,
) -> dict[str, Any]:
    manifest = vault.source_review_manifest(source["source_id"])
    print(f"\nSource: {source['title']} ({source.get('logical_path') or source['source_id']})")
    print(
        f"Proposals: {manifest['proposal_count']} · quarantined: "
        f"{manifest['quarantine_count']}"
    )
    rows = vault.connection.execute(
        """
        SELECT assets.kind, assets.title, assets.statement, assets.status
        FROM asset_revision_bindings_v2
        JOIN assets ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
        WHERE asset_revision_bindings_v2.legacy_source_id = ?
          AND asset_revision_bindings_v2.proposal_set_id = ?
        ORDER BY asset_revision_bindings_v2.proposal_ordinal
        """,
        (source["source_id"], source.get("proposal_set_id")),
    ).fetchall()
    for index, row in enumerate(rows[:100], start=1):
        statement = " ".join(row["statement"].splitlines())
        print(f"  {index}. [{row['kind']}/{row['status']}] {row['title']}")
        print(f"     {statement[:500]}")
    if len(rows) > 100:
        print(f"  … {len(rows) - 100} more proposals")
    answer = input("Approve source, reject proposals, skip, or quit? [a/r/s/q] ").strip().lower()
    if answer == "q":
        return {"decision": "quit", "source_id": source["source_id"]}
    if answer == "s":
        return {"decision": "skip", "source_id": source["source_id"]}
    if answer == "r":
        decisions = []
        for row in vault.connection.execute(
            """
            SELECT assets.asset_id
            FROM asset_revision_bindings_v2
            JOIN assets ON assets.asset_id = asset_revision_bindings_v2.legacy_asset_id
            WHERE asset_revision_bindings_v2.legacy_source_id = ?
              AND assets.status IN ('proposed', 'quarantined')
            ORDER BY asset_revision_bindings_v2.proposal_ordinal
            """,
            (source["source_id"],),
        ):
            decisions.append(
                vault.reject_asset(
                    row["asset_id"],
                    reason=reason,
                    reviewer_id=reviewer_id,
                    confirm_reviewed=True,
                )
            )
        return {"decision": "reject", "source_id": source["source_id"], "items": decisions}
    if answer != "a":
        return {"decision": "skip", "source_id": source["source_id"]}
    quarantine_confirmed = confirm_quarantine
    if manifest["quarantine_count"] and not quarantine_confirmed:
        quarantine_confirmed = (
            input("This source contains quarantined proposals. Approve them? [y/N] ")
            .strip()
            .lower()
            == "y"
        )
        if not quarantine_confirmed:
            return {"decision": "skip", "source_id": source["source_id"]}
    return vault.approve_source_assets(
        source["source_id"],
        confirm_reviewed=True,
        confirm_quarantined=quarantine_confirmed,
        review_manifest_sha256=manifest["review_manifest_sha256"],
        reviewer_id=reviewer_id,
        review_reason=reason,
    )


def _interactive_relation_decision(
    vault: KnowledgeVault,
    candidate: dict[str, Any],
    *,
    reviewer_id: str,
    reason: str,
) -> dict[str, Any]:
    print(
        "\nRelation: "
        f"{candidate['subject_knowledge_key']} {candidate['predicate']} "
        f"{candidate['object_knowledge_key']}"
    )
    print(
        f"Review mode: {candidate['review_mode']} · evidence: "
        f"{len(candidate['evidence_refs'])} source span(s)"
    )
    answer = input("Approve relation, reject, skip, or quit? [a/r/s/q] ").strip().lower()
    if answer == "q":
        return {
            "decision": "quit",
            "relation_revision_id": candidate["relation_revision_id"],
        }
    if answer not in {"a", "r"}:
        return {
            "decision": "skip",
            "relation_revision_id": candidate["relation_revision_id"],
        }
    return review_relation_carry_forward(
        vault,
        relation_revision_id=candidate["relation_revision_id"],
        decision="approve" if answer == "a" else "reject",
        confirm_reviewed=True,
        reviewer_id=reviewer_id,
        reason=reason,
    )


def _handle_review(args: argparse.Namespace, vault_path: Path) -> dict[str, Any]:
    write = bool(args.approve_all or args.reject_all or args.interactive)
    with KnowledgeVault(vault_path, read_only=not write) as vault:
        queue = _review_queue(vault, limit=args.limit)
        if not (args.approve_all or args.reject_all or args.interactive):
            return queue
        if (args.approve_all or args.reject_all) and not args.confirm_reviewed:
            raise ValueError("non-interactive review decisions require --confirm-reviewed")
        decisions: list[dict[str, Any]] = []
        preexisting_relation_ids = [
            item["relation_revision_id"] for item in queue["relation_proposals"]
        ]
        pending_sources = [
            source
            for source in vault.all_sources()
            if source.get("status") == "pending"
        ][: args.limit]
        interactive_quit = False
        if args.interactive:
            if not sys.stdin.isatty():
                raise RuntimeError("interactive review requires a TTY")
            for source in pending_sources:
                decision = _interactive_source_decision(
                    vault,
                    source,
                    reviewer_id=args.reviewer_id,
                    reason=args.reason,
                    confirm_quarantine=args.confirm_quarantine,
                )
                decisions.append(decision)
                if decision.get("decision") == "quit":
                    interactive_quit = True
                    break
        elif args.approve_all:
            for source in pending_sources:
                manifest = vault.source_review_manifest(source["source_id"])
                if manifest["quarantine_count"] and not args.confirm_quarantine:
                    decisions.append(
                        {
                            "source_id": source["source_id"],
                            "decision": "skipped-quarantine",
                        }
                    )
                    continue
                decisions.append(
                    vault.approve_source_assets(
                        source["source_id"],
                        confirm_reviewed=True,
                        confirm_quarantined=args.confirm_quarantine,
                        review_manifest_sha256=manifest["review_manifest_sha256"],
                        reviewer_id=args.reviewer_id,
                        review_reason=args.reason,
                    )
                )
            bound_asset_ids = {
                row["legacy_asset_id"]
                for row in vault.connection.execute(
                    "SELECT legacy_asset_id FROM asset_revision_bindings_v2"
                )
            }
            for item in vault.review_queue(limit=args.limit)["items"]:
                if item["asset_id"] in bound_asset_ids:
                    continue
                if item["status"] == "quarantined" and not args.confirm_quarantine:
                    decisions.append(
                        {
                            "asset_id": item["asset_id"],
                            "decision": "skipped-quarantine",
                        }
                    )
                    continue
                decisions.append(
                    {
                        "decision": "approve",
                        "asset": vault.approve_asset(
                            item["asset_id"],
                            confirm_reviewed=True,
                            confirm_quarantined=args.confirm_quarantine,
                            reviewer_id=args.reviewer_id,
                            review_reason=args.reason,
                        ).to_dict(),
                    }
                )
        else:
            for item in queue["proposals"]:
                decisions.append(
                    vault.reject_asset(
                        item["asset_id"],
                        reason=args.reason,
                        reviewer_id=args.reviewer_id,
                        confirm_reviewed=True,
                    )
                )
        proposed_relations = propose_relation_carry_forward(vault, limit=args.limit)
        if args.interactive and not interactive_quit:
            for candidate in pending_relation_carry_forward(
                vault, limit=args.limit
            )["items"]:
                decision = _interactive_relation_decision(
                    vault,
                    candidate,
                    reviewer_id=args.reviewer_id,
                    reason=args.reason,
                )
                decisions.append(decision)
                if decision.get("decision") == "quit":
                    break
        elif args.approve_all or args.reject_all:
            pending_ids = {
                item["relation_revision_id"]
                for item in pending_relation_carry_forward(vault, limit=args.limit)["items"]
            }
            for relation_revision_id in preexisting_relation_ids:
                if relation_revision_id not in pending_ids:
                    continue
                decisions.append(
                    review_relation_carry_forward(
                        vault,
                        relation_revision_id=relation_revision_id,
                        decision="approve" if args.approve_all else "reject",
                        confirm_reviewed=True,
                        reviewer_id=args.reviewer_id,
                        reason=args.reason,
                    )
                )
        remaining_assets = vault.review_queue(limit=1)["total"]
        remaining_relations = pending_relation_carry_forward(vault, limit=1)["total"]
        return {
            "schema_version": "deeplaw.golden-review-result/v1",
            "vault_id": vault.vault_id,
            "decision_count": len(decisions),
            "decisions": decisions,
            "relation_proposal_generation": proposed_relations,
            "remaining_asset_proposals": remaining_assets,
            "remaining_relation_proposals": remaining_relations,
            "remaining": remaining_assets + remaining_relations,
        }


def handle_golden_command(args: argparse.Namespace) -> dict[str, Any]:
    command = args.command
    if command == "init":
        vault_path = args.vault.expanduser().absolute()
        initialized = initialize_default_knowledge_vault(
            vault_path,
            name=args.name or vault_path.name or "DeepLaw Knowledge",
            scope=cast(VaultScope, args.scope),
        )
        locator = _write_project_config(
            args.project_root,
            vault=vault_path,
            vault_id=initialized["vault_id"],
        )
        return {
            **initialized,
            "project_locator": str(locator),
            "next_command": "deeplaw add <file-or-directory>",
        }
    vault_path = resolve_golden_vault(
        getattr(args, "vault", None),
        project_root=getattr(args, "project_root", None),
    )
    if command == "add":
        job_action = args.cancel or args.resume or args.retry
        connector_options = (
            args.url,
            args.expected_sha256,
            args.max_download_bytes,
            args.network_timeout,
            args.git_repository,
            args.git_revision,
            args.git_repository_id,
        )
        if job_action:
            if (
                args.source is not None
                or any(value is not None for value in connector_options)
                or args.confirm_network
                or args.confirm_local_repository
                or args.include
                or args.exclude
                or args.recursive is not None
                or args.source_kind is not None
                or args.trust is not None
                or args.typed_extractor_manifest is not None
                or args.confirm_external_disclosure
                or args.no_reference_proposals
                or args.dry_run
            ):
                raise ValueError("job resume, retry, or cancel cannot select a new source")
            with auto_aware_knowledge_vault(vault_path, read_only=False) as vault:
                if args.cancel:
                    return cancel_ingest_job(vault, args.cancel)
                return source_knowledge_status_for_result(
                    vault,
                    run_ingest_job(
                        vault,
                        args.resume or args.retry,
                        retry_failed=bool(args.retry),
                    ),
                )
        selectors = (
            args.source is not None,
            args.url is not None,
            args.git_repository is not None,
        )
        if sum(selectors) != 1:
            raise ValueError(
                "add requires exactly one local path, HTTPS URL, or local Git repository"
            )
        if not args.confirm_no_case_data:
            raise ValueError("new source ingestion requires --confirm-no-case-data")
        if args.url is not None:
            if (
                args.git_revision is not None
                or args.git_repository_id is not None
                or args.confirm_local_repository
                or args.include
                or args.exclude
                or args.recursive is not None
            ):
                raise ValueError("HTTPS source capture received incompatible local/Git options")
            if args.source_kind not in {None, "web"}:
                raise ValueError("HTTPS source capture requires --source-kind web")
            if args.trust not in {None, "untrusted"}:
                raise ValueError("HTTPS source snapshots must remain untrusted before review")
            maximum_bytes = (
                MAX_SOURCE_SNAPSHOT_BYTES
                if args.max_download_bytes is None
                else args.max_download_bytes
            )
            timeout_seconds = 30.0 if args.network_timeout is None else args.network_timeout
            if args.dry_run:
                return plan_https_source(
                    args.url,
                    expected_sha256=args.expected_sha256,
                    maximum_bytes=maximum_bytes,
                    timeout_seconds=timeout_seconds,
                )
            with auto_aware_knowledge_vault(vault_path, read_only=False) as vault:
                snapshot = capture_https_source(
                    vault,
                    args.url,
                    confirm_network=args.confirm_network,
                    expected_sha256=args.expected_sha256,
                    maximum_bytes=maximum_bytes,
                    timeout_seconds=timeout_seconds,
                )
                job = create_snapshot_ingest_job(
                    vault,
                    (snapshot,),
                    source_kind="web",
                    trust="untrusted",
                    sensitivity=args.sensitivity,
                    pdf_fallback=args.pdf_fallback,
                    typed_extraction=args.typed_extraction,
                    typed_extractor_manifest=args.typed_extractor_manifest,
                    confirm_external_disclosure=args.confirm_external_disclosure,
                    reference_proposals=not args.no_reference_proposals,
                )
                return source_knowledge_status_for_result(
                    vault,
                    run_ingest_job(vault, job["job_id"]),
                )
        if args.git_repository is not None:
            if (
                args.expected_sha256 is not None
                or args.max_download_bytes is not None
                or args.network_timeout is not None
                or args.confirm_network
                or args.recursive is not None
            ):
                raise ValueError("local Git source capture received incompatible HTTPS options")
            if args.git_revision is None or args.git_repository_id is None:
                raise ValueError(
                    "local Git source capture requires --git-revision and --git-repository-id"
                )
            include = tuple(args.include)
            exclude = tuple(args.exclude)
            if args.dry_run:
                return plan_git_source(
                    args.git_repository,
                    args.git_revision,
                    args.git_repository_id,
                    include=include,
                    exclude=exclude,
                )
            with auto_aware_knowledge_vault(vault_path, read_only=False) as vault:
                snapshots = capture_git_sources(
                    vault,
                    args.git_repository,
                    args.git_revision,
                    args.git_repository_id,
                    include=include,
                    exclude=exclude,
                    confirm_local_repository=args.confirm_local_repository,
                )
                job = create_snapshot_ingest_job(
                    vault,
                    snapshots,
                    source_kind=args.source_kind or "code",
                    trust=args.trust or "user_provided",
                    sensitivity=args.sensitivity,
                    pdf_fallback=args.pdf_fallback,
                    typed_extraction=args.typed_extraction,
                    typed_extractor_manifest=args.typed_extractor_manifest,
                    confirm_external_disclosure=args.confirm_external_disclosure,
                    reference_proposals=not args.no_reference_proposals,
                )
                return source_knowledge_status_for_result(
                    vault,
                    run_ingest_job(vault, job["job_id"]),
                )
        if (
            any(value is not None for value in connector_options)
            or args.confirm_network
            or args.confirm_local_repository
        ):
            raise ValueError("local path ingestion received connector-only options")
        with auto_aware_knowledge_vault(vault_path, read_only=False) as vault:
            job = create_ingest_job(
                vault,
                args.source,
                recursive=True if args.recursive is None else args.recursive,
                include=tuple(args.include),
                exclude=tuple(args.exclude),
                source_kind=args.source_kind or "document",
                trust=args.trust or "user_provided",
                sensitivity=args.sensitivity,
                pdf_fallback=args.pdf_fallback,
                typed_extraction=args.typed_extraction,
                typed_extractor_manifest=args.typed_extractor_manifest,
                confirm_external_disclosure=args.confirm_external_disclosure,
                reference_proposals=not args.no_reference_proposals,
            )
            if args.dry_run:
                return job
            return source_knowledge_status_for_result(
                vault,
                run_ingest_job(vault, job["job_id"]),
            )
    if command == "sync":
        if args.interval < 0.25 or args.interval > 3600:
            raise ValueError("watch interval must be between 0.25 and 3600 seconds")
        if args.max_cycles is not None and not 1 <= args.max_cycles <= 1_000_000:
            raise ValueError("watch max cycles must be between 1 and 1000000")
        cycles: list[dict[str, Any]] = []
        cycle = 0
        while True:
            with KnowledgeVault(vault_path, read_only=args.dry_run) as vault:
                result = (
                    plan_registered_sync(vault)
                    if args.dry_run
                    else run_registered_sync(vault)
                )
            cycles.append(result)
            cycle += 1
            if not args.watch or (args.max_cycles is not None and cycle >= args.max_cycles):
                break
            time.sleep(args.interval)
        return {
            "schema_version": "deeplaw.golden-sync/v1",
            "vault": str(vault_path),
            "watch": args.watch,
            "cycle_count": len(cycles),
            "last": cycles[-1],
        }
    if command == "review":
        return _handle_review(args, vault_path)
    if command == "recall":
        if not args.confirm_no_case_data:
            raise ValueError("recall requires --confirm-no-case-data")
        with KnowledgeVault(vault_path, read_only=True) as vault:
            result = recall(
                vault,
                args.query,
                goal=args.goal,
                confirm_no_case_data=True,
                mode=cast(RetrievalMode, args.mode),
                max_items=args.max_items,
                max_chars=args.max_chars,
                max_tokens=args.max_tokens,
                include_restricted=args.include_restricted,
                as_of=args.as_of,
                discovery_index_path=args.discovery_index,
                model_root=args.model_root,
                threads=args.threads,
                reranker_manifest=args.reranker_manifest,
            )
            _atomic_sidecar(
                _last_capsule_path(vault),
                result["capsule"],
                maximum=_MAX_LAST_CAPSULE_BYTES,
            )
            _atomic_sidecar(
                _last_trace_path(vault),
                result["retrieval"]["trace"],
                maximum=_MAX_LAST_TRACE_BYTES,
            )
            if args.output is not None:
                output = args.output.expanduser().absolute()
                if output.exists() or output.is_symlink():
                    raise FileExistsError("recall output must be a new regular file")
                output.parent.mkdir(parents=True, exist_ok=True)
                _atomic_sidecar(output, result["capsule"], maximum=_MAX_LAST_CAPSULE_BYTES)
            result["capsule_verification"] = verify_capsule_file(
                _last_capsule_path(vault), vault=vault
            )
            return result
    if command == "explain":
        with KnowledgeVault(vault_path, read_only=True) as vault:
            if args.last or args.query is None:
                trace = _read_sidecar(_last_trace_path(vault), maximum=_MAX_LAST_TRACE_BYTES)
                return {
                    "schema_version": "deeplaw.golden-explain/v1",
                    "vault_id": vault.vault_id,
                    "trace": trace,
                }
            if not args.confirm_no_case_data:
                raise ValueError("new explain queries require --confirm-no-case-data")
            result = retrieve(
                vault,
                args.query,
                mode=cast(RetrievalMode, args.mode),
                as_of=args.as_of,
                discovery_index_path=args.discovery_index,
                model_root=args.model_root,
                threads=args.threads,
                explain=True,
                reranker_manifest=args.reranker_manifest,
            )
            _atomic_sidecar(
                _last_trace_path(vault), result["trace"], maximum=_MAX_LAST_TRACE_BYTES
            )
            return {
                "schema_version": "deeplaw.golden-explain/v1",
                "vault_id": vault.vault_id,
                "trace": result["trace"],
            }
    if command == "feedback":
        if not args.confirm_no_case_data:
            raise ValueError("feedback requires --confirm-no-case-data")
        with auto_aware_knowledge_vault(vault_path, read_only=False) as vault:
            capsule_path = _last_capsule_path(vault)
            capsule = _read_sidecar(capsule_path, maximum=_MAX_LAST_CAPSULE_BYTES)
            run = create_run_receipt(
                vault,
                capsule_path=capsule_path,
                status=args.outcome,
                host_name="deeplaw-cli",
                host_version=__version__,
            )
            feedback = record_structured_feedback(
                vault,
                run_id=run["run_id"],
                outcome=args.outcome,
                helpful_asset_ids=(
                    _selected_asset_ids(capsule) if args.mark_selected_helpful else ()
                ),
                missing_knowledge=tuple(args.missing_knowledge),
                missing_sources=tuple(args.missing_source),
                incorrect_relations=tuple(args.incorrect_relation),
                budget_failures=tuple(args.budget_failure),
                observation=args.observation,
                recommended_action=args.recommended_action,
                sensitivity=cast(Sensitivity, args.sensitivity),
            )
            return {
                "schema_version": "deeplaw.golden-feedback/v1",
                "run": run,
                "feedback": feedback,
                "task_success_inferred": False,
            }
    if command == "status":
        with KnowledgeVault(vault_path, read_only=True) as vault:
            status = vault.inspect()
            if args.jobs and (vault.root / "operations").exists():
                status["jobs"] = list_ingest_jobs(vault)
            return status
    if command == "open":
        projection = (
            args.projection.expanduser().absolute()
            if args.projection is not None
            else vault_path.parent / f"{vault_path.name}-projection"
        )
        with KnowledgeVault(vault_path, read_only=True) as vault:
            exported = export_knowledge_markdown(
                vault,
                projection,
                max_sensitivity="private",
                replace=args.replace,
            )
            uri = f"obsidian://open?path={projection.as_posix()}"
        opened = False
        if args.obsidian and not args.print_uri:
            opened = bool(webbrowser.open(uri))
        if not args.obsidian:
            from .operator_workbench import run_operator_workbench

            run_operator_workbench(vault_path)
            opened = True
        return {
            "schema_version": "deeplaw.golden-open/v1",
            "projection": exported,
            "obsidian_uri": uri,
            "opened": opened,
        }
    raise RuntimeError(f"unhandled Golden Path command: {command}")


def handle_golden_doctor(args: argparse.Namespace) -> dict[str, Any]:
    vault = resolve_golden_vault(args.vault, project_root=args.project_root)
    return knowledge_doctor(vault, repair_derived=args.repair_derived)


def render_golden_result(value: dict[str, Any], *, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if output_format == "jsonl":
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    schema = value.get("schema_version", "")
    if schema in {
        "deeplaw.knowledge-ingest-job/v1",
        "deeplaw.knowledge-ingest-job/v2",
    }:
        summary = value["summary"]
        return (
            f"Ingest job {value['job_id']}: {value['state']}\n"
            f"  succeeded {summary['succeeded']} · failed {summary['failed']} · "
            f"pending {summary['pending']}"
        )
    if schema == "deeplaw.golden-review-queue/v1":
        lines = [
            f"Review queue: {value['proposal_count']} proposals in "
            f"{value['pending_source_count']} sources"
        ]
        lines.extend(
            f"  - {source['logical_path'] or source['title']}: "
            f"{source['proposal_count']} proposals"
            for source in value["pending_sources"]
        )
        return "\n".join(lines)
    if schema == "deeplaw.knowledge-recall/v1":
        capsule = value["capsule"]
        item_count = sum(
            len(capsule[group])
            for group in (
                "constraints",
                "decisions",
                "knowledge_assets",
                "experiences",
                "open_questions",
            )
        )
        lines = [
            f"Capsule {capsule['capsule_id']} · {item_count} items · "
            f"{capsule['budget'].get('selected_tokens', 0)} tokens",
            f"Query plan: {value['query_plan']['intent']} via "
            f"{', '.join(value['query_plan']['channels'])}",
        ]
        for group in ("constraints", "decisions", "knowledge_assets", "experiences"):
            for item in capsule[group]:
                lines.append(f"  - [{item['kind']}] {item['title']}")
        if capsule["gaps"]:
            lines.append(f"Gaps: {len(capsule['gaps'])}")
        lines.append("Capsule verification: valid")
        return "\n".join(lines)
    if schema == "deeplaw.knowledge-vault/v1":
        return (
            f"Vault {value['name']} · revision {value['revision']}\n"
            f"  active usable assets {value.get('usable_active_count', 0)} · "
            f"agent ready {str(value.get('agent_ready', False)).lower()}"
        )
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
