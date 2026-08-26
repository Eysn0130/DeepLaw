"""Thin, machine-only adapter for the v0.13 Gate v9 Host task matrix.

The adapter deliberately does not start Codex/OpenCode.  A real Host run is an
external qualification action with owner-controlled prerequisites.  This file
provides the frozen task plan, public read seams used by an eventual runner,
and validation of already-retained typed evidence.  It never reads ambient
authentication, invokes a model, or opens a private database API.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.host_preflight_receipt import (
    HostIdentityValidationError,
    host_identity_sha256,
    load_host_identity_input,
)
from benchmarks.release.typed_qualification_evidence import parse_typed_evidence
from benchmarks.release.typed_qualification_evidence_v3_host_tasks import (
    TASK_DUTIES,
    TASK_OPERATIONS,
    TASK_WRONG_STATES,
)
from deeplaw.api.knowledge_os import KnowledgeOS
from deeplaw.read_services import SourceReadService, WikiReadService

REPOSITORY = Path(__file__).resolve().parents[2]
CASES_RELATIVE_PATH = Path("benchmarks/hosts/v013-host-task-cases-v1.json")
SCHEMA_RELATIVE_PATH = Path("contracts/v013-host-task-evidence.v1.schema.json")
HOSTS = ("codex", "opencode")
TASK_CASES = ("continuity", "living_wiki", "professional_evidence")
CONTINUITY_LIFECYCLE = (
    "new",
    "resume",
    "fork",
    "compaction",
    "stale",
    "wrong_task_line",
    "forget",
    "resume_after_forget",
)
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+:-]{0,99}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT = re.compile(r"^[0-9a-f]{40}$")


def _validate_catalog_host_constraints(value: Mapping[str, Any]) -> None:
    if set(value) != set(HOSTS):
        raise HostTaskQualificationError("v0.13 Host coordinates are not closed")
    codex = value["codex"]
    if not isinstance(codex, Mapping) or set(codex) != {
        "tool_version", "binary_sha256", "model_id", "expected_response_model_id",
        "reasoning_effort", "argv_prefix",
    }:
        raise HostTaskQualificationError("v0.13 Codex coordinates are not closed")
    if (
        codex["tool_version"] is not None
        or codex["binary_sha256"] is not None
        or codex["model_id"] != "gpt-5.6-luna"
        or codex["expected_response_model_id"] != "gpt-5.6-luna"
        or codex["reasoning_effort"] != "max"
        or codex["argv_prefix"] != ["codex", "app-server", "--stdio"]
    ):
        raise HostTaskQualificationError("v0.13 Codex coordinate shape is invalid")
    opencode = value["opencode"]
    if not isinstance(opencode, Mapping) or set(opencode) != {
        "tool_version", "binary_sha256", "source_commit", "config_selector",
        "model_id", "expected_response_model_id", "reasoning_effort", "argv_prefix",
    }:
        raise HostTaskQualificationError("v0.13 OpenCode coordinates are not closed")
    if (
        opencode["tool_version"] is not None
        or opencode["binary_sha256"] is not None
        or opencode["source_commit"] is not None
        or opencode["config_selector"] != "deepseek/deepseek-v4-flash"
        or opencode["model_id"] != "deepseek-v4-flash"
        or opencode["expected_response_model_id"] != "deepseek-v4-flash"
        or opencode["reasoning_effort"] is not None
        or opencode["argv_prefix"] != ["opencode", "--pure", "run", "--format", "json"]
    ):
        raise HostTaskQualificationError("v0.13 OpenCode coordinates shape is invalid")


class HostTaskQualificationError(ValueError):
    """The frozen v0.13 Host task plan or retained evidence is invalid."""


def _schema() -> dict[str, Any]:
    try:
        value = json.loads((REPOSITORY / SCHEMA_RELATIVE_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError("v0.13 Host task schema is unavailable") from exc
    if not isinstance(value, dict):
        raise HostTaskQualificationError("v0.13 Host task schema is not an object")
    Draft202012Validator.check_schema(value)
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_external_identity(path: Path | str) -> dict[str, Any]:
    try:
        return load_host_identity_input(path, repository=REPOSITORY)
    except (HostIdentityValidationError, OSError, ValueError) as exc:
        raise HostTaskQualificationError(
            "owner-controlled Host identity input was rejected"
        ) from exc


def load_task_cases(path: Path | str | None = None) -> dict[str, Any]:
    """Load and validate the frozen three-task Host case catalog."""

    selected = REPOSITORY / CASES_RELATIVE_PATH if path is None else Path(path)
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise HostTaskQualificationError("v0.13 Host task cases are unavailable") from exc
    if not isinstance(value, Mapping):
        raise HostTaskQualificationError("v0.13 Host task cases are not an object")
    try:
        Draft202012Validator(_schema(), format_checker=FormatChecker()).validate(value)
    except Exception as exc:  # jsonschema has several validation exception classes.
        raise HostTaskQualificationError(
            "v0.13 Host task cases failed strict schema validation"
        ) from exc
    tasks = value["task_cases"]
    if [item["task_case"] for item in tasks] != list(TASK_CASES):
        raise HostTaskQualificationError("v0.13 Host task case order is not frozen")
    for row in tasks:
        task = row["task_case"]
        if (
            row["required_duties"] != list(TASK_DUTIES[task])
            or row["required_wrong_states"] != list(TASK_WRONG_STATES[task])
            or row["required_operations"] != list(TASK_OPERATIONS[task])
        ):
            raise HostTaskQualificationError(
                "v0.13 Host task duties, wrong states, or operations are not frozen"
            )
    _validate_catalog_host_constraints(value["host_constraints"])
    return json.loads(_canonical(value))


def task_case(host: str, task: str, *, cases: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return one host/task plan without executing a Host or changing state."""

    if host not in HOSTS or task not in TASK_CASES:
        raise HostTaskQualificationError("unsupported v0.13 Host task identity")
    catalog = load_task_cases() if cases is None else cases
    row = next((item for item in catalog["task_cases"] if item["task_case"] == task), None)
    if row is None:
        raise HostTaskQualificationError("frozen v0.13 Host task is missing")
    plan = {
        "status": "not_executed",
        "executed": False,
        "host": host,
        "task_case": task,
        "required_duties": list(row["required_duties"]),
        "required_wrong_states": list(row["required_wrong_states"]),
        "required_operations": list(row["required_operations"]),
        "public_seams": [
            "knowledge_support",
            "SourceReadService",
            "WikiReadService",
            "KnowledgeOS.retrieval.query",
            "KnowledgeOS.context.compile",
            "native_host.parse_native_host_event",
            "native_host.derive_native_host_receipt",
        ],
        "native_host_receipts_required": True,
        "claim_eligible": False,
        "release_ready": False,
        "host_identity_source": "owner_external_frozen_input",
    }
    if task == "continuity":
        plan["lifecycle"] = list(CONTINUITY_LIFECYCLE)
    if task == "living_wiki":
        plan["wiki_cases"] = list(row["required_duties"])
    if task == "professional_evidence":
        plan["source_duties"] = list(row["required_duties"])
    return plan


def public_source_read(
    vault: Path | str,
    *,
    source_id: str,
    fragment_id: str | None = None,
    max_chars: int = 12_000,
) -> dict[str, Any]:
    """Use the policy-admitted public Source read seam."""

    service = SourceReadService(vault)
    if fragment_id is None:
        return service.execute(action="get", source_id=source_id, max_chars=max_chars)
    return service.execute(
        action="fragment",
        source_id=source_id,
        fragment_id=fragment_id,
        max_chars=max_chars,
    )


def public_wiki_read(
    vault: Path | str,
    *,
    action: str,
    wiki_path: str | None = None,
    knowledge_id: str | None = None,
) -> dict[str, Any]:
    """Use the policy-admitted public Living Wiki read seam."""

    service = WikiReadService(vault)
    return service.execute(
        action=action,
        wiki_path=wiki_path,
        knowledge_id=knowledge_id,
        limit=20,
    )


def public_context_read(
    vault: Path | str,
    *,
    task: str,
    duties: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Use the bounded public KnowledgeOS context seam."""

    with KnowledgeOS.open(vault) as knowledge:
        return knowledge.context.compile(
            task=task,
            applicable_duties=duties,
            max_chars=8_000,
            max_tokens=6_000,
            max_sources=12,
            confirm_no_case_data=True,
        )


def validate_retained_manifest(
    manifest: Path | str,
    *,
    root: Path | str | None = None,
    expected_candidate: Mapping[str, Any] | None = None,
    expected_run_id: str | None = None,
    expected_workflow_run_id: int | None = None,
    expected_corpus_sha256: str | None = None,
    expected_runner: Mapping[str, Any] | None = None,
    expected_scorer: Mapping[str, Any] | None = None,
    host_identity_input: Path | str | None = None,
) -> dict[str, Any]:
    """Validate a retained v3 ``host_event_sequence`` evidence manifest."""

    try:
        result = parse_typed_evidence(
            manifest,
            root=root,
            expected_candidate=expected_candidate,
            expected_run_id=expected_run_id,
            expected_workflow_run_id=expected_workflow_run_id,
            expected_corpus_sha256=expected_corpus_sha256,
            expected_runner=expected_runner,
            expected_scorer=expected_scorer,
        )
    except Exception as exc:
        raise HostTaskQualificationError("retained v0.13 Host task evidence was rejected") from exc
    if result.get("kind") != "host_event_sequence":
        raise HostTaskQualificationError("retained evidence kind is not host_event_sequence")
    metrics = result.get("metrics")
    if (
        not isinstance(metrics, Mapping)
        or metrics.get("task_case") not in TASK_CASES
        or metrics.get("host") not in HOSTS
    ):
        raise HostTaskQualificationError("retained evidence lacks v0.13 Host/task metrics")
    if host_identity_input is not None:
        identity = _load_external_identity(host_identity_input)
        expected_identity = host_identity_sha256(identity["hosts"][metrics["host"]])
        if metrics.get("host_identity_sha256") != expected_identity:
            raise HostTaskQualificationError(
                "retained Host evidence does not bind the owner-controlled identity"
            )
    return result


def validate_host_task_matrix(
    results: list[Mapping[str, Any]],
    *,
    host_identity_input: Path | str | None = None,
) -> dict[str, Any]:
    """Require exactly one derived result for each Host/task pair.

    This is an aggregate admission check only.  It never turns a local result
    into a release decision and never accepts caller-authored aggregate fields.
    """

    if not isinstance(results, list) or len(results) != len(HOSTS) * len(TASK_CASES):
        raise HostTaskQualificationError("v0.13 Host task matrix requires exactly six results")
    identities: set[tuple[str, str]] = set()
    frozen_identity: Mapping[str, Any] | None = None
    if host_identity_input is not None:
        frozen_identity = _load_external_identity(host_identity_input)
    for index, result in enumerate(results):
        if not isinstance(result, Mapping) or result.get("kind") != "host_event_sequence":
            raise HostTaskQualificationError(f"Host task matrix result {index} has the wrong kind")
        metrics = result.get("metrics")
        if not isinstance(metrics, Mapping):
            raise HostTaskQualificationError(
                f"Host task matrix result {index} has no derived metrics"
            )
        host = metrics.get("host")
        task = metrics.get("task_case")
        if host not in HOSTS or task not in TASK_CASES or (host, task) in identities:
            raise HostTaskQualificationError(
                "Host task matrix contains a duplicate or unsupported pair"
            )
        identities.add((host, task))
        if frozen_identity is not None:
            identity_digest = metrics.get("host_identity_sha256")
            if not isinstance(identity_digest, str) or _SHA256.fullmatch(identity_digest) is None:
                raise HostTaskQualificationError(
                    "Host task matrix lacks a bounded Host identity digest"
                )
            if identity_digest != host_identity_sha256(frozen_identity["hosts"][host]):
                raise HostTaskQualificationError(
                    "Host task matrix does not bind the owner-controlled identity"
                )
    expected = {(host, task) for host in HOSTS for task in TASK_CASES}
    if identities != expected:
        raise HostTaskQualificationError(
            "Host task matrix does not cover both Hosts and all three tasks"
        )
    return {
        "status": "derived",
        "result_count": len(results),
        "host_count": len(HOSTS),
        "task_count_per_host": len(TASK_CASES),
        "claim_eligible": False,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan or validate v0.13 Gate v9 Host task evidence."
    )
    parser.add_argument("--host", choices=HOSTS)
    parser.add_argument("--task-case", choices=TASK_CASES)
    parser.add_argument("--cases", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--host-identity-input", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.manifest is not None:
            if args.root is None:
                raise HostTaskQualificationError("--root is required with --manifest")
            result = validate_retained_manifest(
                args.manifest,
                root=args.root,
                host_identity_input=args.host_identity_input,
            )
        else:
            if args.host is None or args.task_case is None:
                raise HostTaskQualificationError("--host and --task-case are required for a plan")
            catalog = load_task_cases(args.cases)
            result = task_case(args.host, args.task_case, cases=catalog)
        print(_canonical(result))
        return 0
    except HostTaskQualificationError as exc:
        parser.error(str(exc))
        return 2


def main(argv: list[str] | None = None) -> int:
    return _main(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CASES_RELATIVE_PATH",
    "CONTINUITY_LIFECYCLE",
    "HOSTS",
    "TASK_CASES",
    "HostTaskQualificationError",
    "load_task_cases",
    "main",
    "public_context_read",
    "public_source_read",
    "public_wiki_read",
    "task_case",
    "validate_host_task_matrix",
    "validate_retained_manifest",
]
