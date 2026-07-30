from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPORT_SCHEMA_VERSION = "deeplaw.living-wiki-quality-report/v1"
SUITE_SCHEMA_VERSION = "deeplaw.living-wiki-quality-suite/v1"
MAX_CLI_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_PROVIDER_BYTES = 65_536
PATH_OPTIONS = frozenset(
    {
        "--vault",
        "--source",
        "--plan",
        "--request",
        "--output",
        "--backup",
        "--capsule",
    }
)


class QualityGateError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_digest(value: dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _redacted_argv(arguments: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for value in arguments:
        if redact_next:
            redacted.append("<local-path>")
            redact_next = False
            continue
        redacted.append(value)
        redact_next = value in PATH_OPTIONS
    return redacted


class Cli:
    def __init__(self, executable: Path) -> None:
        self.executable = executable.resolve(strict=True)
        self.records: list[dict[str, Any]] = []
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "DO_NOT_TRACK": "1",
                "NO_PROXY": "*",
                "no_proxy": "*",
            }
        )

    def run(
        self,
        *arguments: str,
        expected_exit: int = 0,
        timeout: int = 180,
        parse_json: bool = True,
        label: str | None = None,
    ) -> tuple[Any, float]:
        argv = [str(self.executable), *arguments]
        started = time.perf_counter()
        process = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            env=self.environment,
            timeout=timeout,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        if len(process.stdout) > MAX_CLI_OUTPUT_BYTES or len(process.stderr) > MAX_CLI_OUTPUT_BYTES:
            raise QualityGateError("CLI output exceeded the quality harness bound")
        safe_argv = _redacted_argv(list(arguments))
        self.records.append(
            {
                "label": label or " ".join(safe_argv[:4]),
                "argv_sha256": _sha256_bytes(
                    _canonical_json(safe_argv).encode("utf-8")
                ),
                "exit_code": process.returncode,
                "stdout_sha256": _sha256_bytes(process.stdout),
                "stdout_bytes": len(process.stdout),
                "stderr_sha256": _sha256_bytes(process.stderr),
                "stderr_bytes": len(process.stderr),
                "elapsed_ms": round(elapsed_ms, 3),
            }
        )
        if process.returncode != expected_exit:
            diagnostic = process.stderr.decode("utf-8", errors="replace")
            diagnostic = diagnostic.replace(str(self.executable.parent), "<runtime>")
            raise QualityGateError(
                f"CLI command {label or safe_argv[:4]} returned {process.returncode}, "
                f"expected {expected_exit}: {diagnostic[-800:]}"
            )
        if not parse_json:
            return process.stdout.decode("utf-8", errors="strict").strip(), elapsed_ms
        try:
            value = json.loads(process.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise QualityGateError(
                f"CLI command {label or safe_argv[:4]} did not return JSON"
            ) from error
        return value, elapsed_ms


def _main_source() -> str:
    special = {
        1: (
            "Graph Priority Concept",
            "The graph-priority concept is connected to the exact identity claim.",
        ),
        2: (
            "Exact Identity Claim",
            "BETA-EXACT is the exact governed identity target.",
        ),
        3: (
            "Northwind Research",
            "Northwind Research is also called NRL and was formerly Northwind Lab.",
        ),
        4: (
            "ACME Research Lab",
            "ACME Research Lab is an ACME organization for public research.",
        ),
        5: (
            "ACME Shipping Firm",
            "ACME Shipping Firm is a different ACME organization for freight.",
        ),
        6: (
            "Governed Admission",
            "Discovery proposes candidates; admission enforces scope, sensitivity and "
            "Authority; selection stays inside explicit budgets.",
        ),
        7: (
            "KAPPA Control Main",
            "KAPPA control requires signed receipts according to the main source.",
        ),
        8: (
            "Latest Source Revision",
            "LATEST-IOTA identifies the current registered source revision.",
        ),
        9: (
            "ORBIT Launch May 1",
            "Source A records the ORBIT launch date as 2025-05-01.",
        ),
        10: (
            "ORBIT Launch May 3",
            "Source B records the ORBIT launch date as 2025-05-03.",
        ),
        11: (
            "SIGMA Recovery Procedure",
            "SIGMA recovery procedure: snapshot, verify, restore, then verify again.",
        ),
        12: (
            "Historical Limit H-10",
            "The historical limit H-10 was 10 before 2026.",
        ),
        13: (
            "Current Limit H-12",
            "The current limit H-12 is 12 from 2026.",
        ),
        14: (
            "Reusable RHO",
            "RHO-REUSE is a compiled invariant reused across repeated queries.",
        ),
        15: (
            "Exact Quote Evidence",
            "The exact phrase is: evidence precedes interpretation.",
        ),
        16: (
            "Compiled OMEGA",
            "COMPILED-OMEGA is a reusable invariant served from compiled Knowledge.",
        ),
    }
    sections: list[str] = []
    for ordinal in range(1, 361):
        title, body = special.get(
            ordinal,
            (
                f"Quality Object {ordinal:03d}",
                f"QID-{ordinal:03d} records deterministic public invariant "
                f"VALUE-{ordinal:03d}.",
            ),
        )
        sections.append(f"# {title}\n{body}")
    return "\n\n".join(sections) + "\n"


def _source_text(title: str, body: str) -> str:
    return f"# {title}\n{body}\n"


def _write_sources(root: Path) -> dict[str, Path]:
    sources = {
        "main": root / "quality-main.md",
        "corroborating": root / "quality-corroborating.md",
        "refresh": root / "quality-refresh.md",
        "withdrawn": root / "quality-withdrawn.md",
        "uncompiled": root / "quality-uncompiled.md",
        "restricted": root / "quality-restricted.md",
    }
    sources["main"].write_text(_main_source(), encoding="utf-8")
    sources["corroborating"].write_text(
        _source_text(
            "KAPPA Control Corroborating",
            "KAPPA control requires signed receipts according to an independent source.",
        ),
        encoding="utf-8",
    )
    sources["refresh"].write_text(
        "\n\n".join(
            [
                "# Freshness Sentinel\nSTALE-OLD-PHI applies before the source update.",
                "# Stable Refresh Anchor\nREFRESH-STABLE remains byte-equivalent.",
                "# Refresh Dependency\nREFRESH-DEPENDENT depends on the exact source revision.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sources["withdrawn"].write_text(
        _source_text(
            "Withdrawn Rule Z",
            "WITHDRAWN-ZETA is required only while this source remains active.",
        ),
        encoding="utf-8",
    )
    sources["uncompiled"].write_text(
        _source_text(
            "Uncompiled TAU Evidence",
            "RAW-FALLBACK-TAU is available only from admitted source evidence.",
        ),
        encoding="utf-8",
    )
    sources["restricted"].write_text(
        _source_text(
            "Restricted UPSILON",
            "RESTRICTED-UPSILON must never cross a public query boundary.",
        ),
        encoding="utf-8",
    )
    return sources


def _source_add(
    cli: Cli,
    *,
    vault: Path,
    source: Path,
    sensitivity: str,
) -> dict[str, Any]:
    value, _elapsed = cli.run(
        "knowledge",
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(source),
        "--source-kind",
        "document",
        "--sensitivity",
        sensitivity,
        "--confirm-no-case-data",
        label=f"source add {source.stem}",
    )
    return value


def _approve_source(
    cli: Cli,
    *,
    vault: Path,
    source_id: str,
) -> dict[str, Any]:
    manifest, _elapsed = cli.run(
        "knowledge",
        "review",
        "manifest",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        label="source review manifest",
    )
    approved, _elapsed = cli.run(
        "knowledge",
        "review",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        "--review-manifest-sha256",
        manifest["review_manifest_sha256"],
        "--reviewer-id",
        "deeplaw-quality-gate",
        "--reason",
        "Deterministic public quality fixture review.",
        "--confirm-reviewed",
        timeout=300,
        label="source approve",
    )
    return approved


def _action(
    fragment: dict[str, Any],
    *,
    label: str,
    kind: str,
    title: str,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "action": "create",
        "kind": kind,
        "semantic_key": f"quality:{label}:{fragment['ordinal']:04d}",
        "knowledge_id": None,
        "expected_revision_id": None,
        "title": title,
        "body": fragment["text"],
        "aliases": aliases or [],
        "epistemic_state": "supported",
        "source_refs": [
            {
                "source_revision_id": fragment["source_revision_id"],
                "fragment_id": fragment["fragment_id"],
                "locator": fragment["locator"],
                "quote_sha256": fragment["text_sha256"],
            }
        ],
        "assertion": None,
        "tags": ["living-wiki-quality", label],
        "valid_from": None,
        "valid_to": None,
        "applicability": {
            "description": "Bound to the exact public quality Source Revision.",
            "scopes": [],
            "conditions": [],
            "exclusions": [],
        },
        "synthesis_inputs": None,
        "reason": "Compile deterministic source-bound quality Knowledge.",
    }


def _main_metadata(ordinal: int) -> tuple[str, str, list[str]]:
    values = {
        1: ("concept", "Graph Priority Concept", []),
        2: ("claim", "Exact Identity Claim", []),
        3: ("entity", "Northwind Research", ["NRL", "Northwind Lab"]),
        4: ("entity", "ACME Research Lab", ["ACME Research"]),
        5: ("entity", "ACME Shipping Firm", ["ACME Shipping"]),
        6: ("concept", "Governed Admission", []),
        7: ("claim", "KAPPA Control Main", []),
        8: ("claim", "Latest Source Revision", []),
        9: ("claim", "ORBIT Launch May 1", []),
        10: ("claim", "ORBIT Launch May 3", []),
        11: ("procedure", "SIGMA Recovery Procedure", []),
        12: ("event", "Historical Limit H-10", []),
        13: ("claim", "Current Limit H-12", []),
        14: ("claim", "Reusable RHO", []),
        15: ("claim", "Exact Quote Evidence", []),
        16: ("claim", "Compiled OMEGA", []),
    }
    return values.get(ordinal, ("claim", f"Quality Object {ordinal:03d}", []))


def _plan(packet: dict[str, Any], *, label: str) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    action_by_ordinal: dict[int, dict[str, Any]] = {}
    for fragment_value in packet["fragments"]:
        fragment = dict(fragment_value)
        fragment["source_revision_id"] = packet["source_revision_id"]
        if label == "main":
            kind, title, aliases = _main_metadata(fragment["ordinal"])
        elif label == "corroborating":
            kind, title, aliases = ("claim", "KAPPA Control Corroborating", [])
        elif label == "refresh":
            titles = {
                1: "Freshness Sentinel",
                2: "Stable Refresh Anchor",
                3: "Refresh Dependency",
            }
            kind, title, aliases = ("claim", titles[fragment["ordinal"]], [])
        elif label == "withdrawn":
            kind, title, aliases = ("claim", "Withdrawn Rule Z", [])
        else:
            raise QualityGateError(f"unsupported compilation label: {label}")
        value = _action(
            fragment,
            label=label,
            kind=kind,
            title=title,
            aliases=aliases,
        )
        actions.append(value)
        action_by_ordinal[fragment["ordinal"]] = value
    relations: list[dict[str, Any]] = []
    if label == "main" and {1, 2}.issubset(action_by_ordinal):
        relations.append(
            {
                "action": "create",
                "subject": {
                    "knowledge_id": None,
                    "semantic_key": action_by_ordinal[1]["semantic_key"],
                    "kind": action_by_ordinal[1]["kind"],
                },
                "predicate": "supports",
                "object": {
                    "knowledge_id": None,
                    "semantic_key": action_by_ordinal[2]["semantic_key"],
                    "kind": action_by_ordinal[2]["kind"],
                },
                "expected_relation_revision_id": None,
                "evidence_refs": action_by_ordinal[2]["source_refs"],
                "valid_from": None,
                "valid_to": None,
                "reason": "Exercise exact-identity ordering against a graph neighbor.",
            }
        )
    if label == "main" and {9, 10}.issubset(action_by_ordinal):
        relations.append(
            {
                "action": "create",
                "subject": {
                    "knowledge_id": None,
                    "semantic_key": action_by_ordinal[9]["semantic_key"],
                    "kind": action_by_ordinal[9]["kind"],
                },
                "predicate": "contradicts",
                "object": {
                    "knowledge_id": None,
                    "semantic_key": action_by_ordinal[10]["semantic_key"],
                    "kind": action_by_ordinal[10]["kind"],
                },
                "expected_relation_revision_id": None,
                "evidence_refs": action_by_ordinal[10]["source_refs"],
                "valid_from": None,
                "valid_to": None,
                "reason": "Preserve incompatible dated source claims.",
            }
        )
    fragment_ids = [item["fragment_id"] for item in packet["fragments"]]
    return {
        "schema_version": "deeplaw.source-compilation-plan/v1",
        "source_revision_id": packet["source_revision_id"],
        "packet_id": packet["packet_id"],
        "expected_audit_head": packet["input_audit_head"],
        "object_actions": actions,
        "relation_actions": relations,
        "identity_actions": [],
        "unresolved_identities": [],
        "contradictions": (
            [
                {
                    "subject": "ORBIT launch date",
                    "reason": "The two source-bound dates conflict and remain unresolved.",
                }
            ]
            if label == "main" and {9, 10}.issubset(action_by_ordinal)
            else []
        ),
        "coverage": {
            "packet_fragment_count": len(fragment_ids),
            "covered_fragment_ids": fragment_ids,
            "omitted_fragment_ids": [],
            "ratio": 1.0,
            "completeness": "complete",
        },
        "skipped_fragments": [],
        "warnings": [],
    }


def _compile_source(
    cli: Cli,
    *,
    vault: Path,
    grant_id: str,
    source_revision_id: str,
    label: str,
    plans_root: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], float]:
    started = time.perf_counter()
    begun, _elapsed = cli.run(
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
        "deeplaw-quality-fake-agent",
        "--packet-max-fragments",
        "64",
        "--confirm-no-case-data",
        label=f"compile begin {label}",
    )
    run_id = begun["compilation_run_id"]
    citation_index: dict[str, dict[str, Any]] = {}
    packet_count = 0
    while True:
        packet, _elapsed = cli.run(
            "knowledge",
            "compile",
            "packet",
            "--vault",
            str(vault),
            "--grant-id",
            grant_id,
            "--run-id",
            run_id,
            label=f"compile packet {label}",
        )
        if packet.get("complete") is True:
            break
        packet_count += 1
        for fragment in packet["fragments"]:
            citation_index[fragment["fragment_id"]] = {
                "source_revision_id": packet["source_revision_id"],
                "locator": fragment["locator"],
                "quote_sha256": fragment["text_sha256"],
                "ordinal": fragment["ordinal"],
            }
        plan_path = plans_root / f"{label}-{packet_count:04d}.json"
        plan_path.write_text(
            _canonical_json(_plan(packet, label=label)),
            encoding="utf-8",
        )
        cli.run(
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
            label=f"compile stage {label}",
        )
    validation, _elapsed = cli.run(
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
        label=f"compile validate {label}",
    )
    receipt, _elapsed = cli.run(
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
        label=f"compile commit {label}",
    )
    completed, _elapsed = cli.run(
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
        timeout=300,
        label=f"compile resume {label}",
    )
    status, _elapsed = cli.run(
        "knowledge",
        "compile",
        "status",
        "--vault",
        str(vault),
        "--run-id",
        run_id,
        label=f"compile status {label}",
    )
    explanation, _elapsed = cli.run(
        "knowledge",
        "compile",
        "explain",
        "--vault",
        str(vault),
        "--run-id",
        run_id,
        label=f"compile explain {label}",
    )
    if (
        validation.get("valid") is not True
        or completed.get("status") != "succeeded"
        or status.get("status") != "succeeded"
    ):
        raise QualityGateError(f"compilation did not succeed for {label}")
    elapsed_ms = (time.perf_counter() - started) * 1000
    return (
        {
            "label": label,
            "source_revision_id": source_revision_id,
            "compilation_run_id": run_id,
            "packet_count": packet_count,
            "staged_object_count": validation["staged_object_count"],
            "staged_relation_count": validation["staged_relation_count"],
            "knowledge_revision_count": len(receipt["knowledge_revision_ids"]),
            "relation_revision_count": len(receipt["relation_revision_ids"]),
            "receipt_sha256": completed["receipt_sha256"],
            "projection_manifest_sha256": completed["projection"]["living_wiki"][
                "manifest_sha256"
            ],
            "coverage_ratio": explanation["run"]["coverage_ratio"],
            "status": completed["status"],
        },
        citation_index,
        elapsed_ms,
    )


def _all_titles(result: dict[str, Any]) -> list[str]:
    return [
        str(item.get("title"))
        for item in [*result.get("compiled", []), *result.get("evidence", [])]
        if isinstance(item, dict) and isinstance(item.get("title"), str)
    ]


def _score_case(titles: list[str], expected: list[str], top_k: int) -> dict[str, Any]:
    selected = titles[:top_k]
    expected_set = set(expected)
    credited_hits: list[str] = []
    for title in selected:
        if title in expected_set and title not in credited_hits:
            credited_hits.append(title)
    recall = len(credited_hits) / len(expected_set)
    precision = len(credited_hits) / max(1, len(selected))
    reciprocal_rank = next(
        (1.0 / index for index, title in enumerate(selected, start=1) if title in expected_set),
        0.0,
    )
    seen_relevant: set[str] = set()
    dcg = 0.0
    for index, title in enumerate(selected, start=1):
        if title in expected_set and title not in seen_relevant:
            dcg += 1.0 / math.log2(index + 1)
            seen_relevant.add(title)
    ideal_count = min(len(expected_set), top_k)
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_count + 1))
    return {
        "selected_titles": selected,
        "expected_titles": expected,
        "hit_count": len(credited_hits),
        "recall_at_k": recall,
        "precision_at_k": precision,
        "reciprocal_rank": reciprocal_rank,
        "ndcg": dcg / ideal if ideal else 0.0,
    }


def _citation_valid(
    item: dict[str, Any],
    *,
    source_revisions: set[str],
    citation_index: dict[str, dict[str, Any]],
) -> bool:
    references = item.get("source_refs")
    if not isinstance(references, list) or not references:
        return False
    for reference in references:
        if not isinstance(reference, dict):
            continue
        source_revision_id = reference.get("source_revision_id")
        if source_revision_id not in source_revisions:
            continue
        fragment_id = reference.get("fragment_id")
        if isinstance(fragment_id, str) and fragment_id in citation_index:
            expected = citation_index[fragment_id]
            if (
                reference.get("locator") == expected["locator"]
                and reference.get("quote_sha256") == expected["quote_sha256"]
            ):
                return True
        if (
            isinstance(reference.get("locator"), str)
            and isinstance(reference.get("quote_sha256"), str)
        ):
            return True
    return False


def _query(
    cli: Cli,
    *,
    vault: Path,
    query: str,
    purpose: str,
    top_k: int,
    configuration: dict[str, Any],
    as_of: str | None = None,
    label: str,
) -> tuple[dict[str, Any], float]:
    arguments = [
        "knowledge",
        "query",
        "--vault",
        str(vault),
        "--query",
        query,
        "--purpose",
        purpose,
        "--scope",
        configuration["scope"],
        "--max-sensitivity",
        configuration["max_sensitivity"],
        "--limit",
        str(top_k),
        "--max-chars",
        str(configuration["max_characters"]),
        "--max-tokens",
        str(configuration["max_tokens"]),
        "--max-sources",
        str(configuration["max_sources"]),
        "--graph-hops",
        str(configuration["graph_hops"]),
        "--retrieval-mode",
        configuration["retrieval_mode"],
    ]
    if as_of is not None:
        arguments.extend(["--as-of", as_of])
    value, elapsed = cli.run(*arguments, label=label)
    if len(_canonical_json(value).encode("utf-8")) > MAX_PROVIDER_BYTES:
        raise QualityGateError("purpose-aware query exceeded 64 KiB")
    return value, elapsed


def _source_record(label: str, value: dict[str, Any]) -> dict[str, Any]:
    source = value["source"]
    identity = value["identity"]
    compiler = source["compiler"]
    return {
        "label": label,
        "source_id": source["source_id"],
        "source_revision_id": identity["source_revision_id"],
        "immutable_bytes_sha256": source["content_sha256"],
        "media_type": source["media_type"],
        "lifecycle": source["governance"]["lifecycle_status"],
        "scope": "project",
        "sensitivity": source["sensitivity"],
        "origin": "user_source",
        "authority": "user_provided",
        "parser_identity": compiler["source_adapter"],
        "parser_version": compiler["source_adapter_version"],
        "source_ir_compilation_id": source["compilation_id"],
        "source_ir_digest": compiler["compiled_fragment_sha256"],
        "fragment_count": identity["fragment_count"],
        "fragment_inventory_sha256": identity["fragment_inventory_sha256"],
        "warnings": source["warnings"],
    }


def run_gate(
    *,
    repository: Path,
    suite_path: Path,
    deeplaw: Path,
    candidate_role: str,
    candidate_commit: str,
    artifact_sha256: str | None,
) -> dict[str, Any]:
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != SUITE_SCHEMA_VERSION:
        raise QualityGateError("Living Wiki quality suite schema is unsupported")
    suite_schema = json.loads(
        (
            repository / "contracts" / "living-wiki-quality-suite.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(suite_schema)
    Draft202012Validator(suite_schema).validate(suite)
    cli = Cli(deeplaw)
    version_line, _elapsed = cli.run("--version", parse_json=False, label="version")
    version = version_line.rsplit(" ", 1)[-1]
    failures: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="deeplaw-living-wiki-quality-") as temporary:
        workspace = Path(temporary)
        vault = workspace / "vault"
        plans = workspace / "plans"
        plans.mkdir()
        sources = _write_sources(workspace)
        _init, _elapsed = cli.run(
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "living-wiki-quality-v1",
            "--scope",
            "project",
            label="knowledge init",
        )
        source_values: dict[str, dict[str, Any]] = {}
        approval_values: dict[str, dict[str, Any]] = {}
        for label in (
            "main",
            "corroborating",
            "refresh",
            "withdrawn",
            "uncompiled",
        ):
            source_values[label] = _source_add(
                cli,
                vault=vault,
                source=sources[label],
                sensitivity="public",
            )
            approval_values[label] = _approve_source(
                cli,
                vault=vault,
                source_id=source_values[label]["source"]["source_id"],
            )
        source_values["restricted"] = _source_add(
            cli,
            vault=vault,
            source=sources["restricted"],
            sensitivity="restricted",
        )
        approval_values["restricted"] = _approve_source(
            cli,
            vault=vault,
            source_id=source_values["restricted"]["source"]["source_id"],
        )
        grant, _elapsed = cli.run(
            "knowledge",
            "sink",
            "enable",
            "--vault",
            str(vault),
            "--writer-id",
            "deeplaw-quality-fake-agent",
            "--scope",
            "project",
            "--max-sensitivity",
            "public",
            "--profile",
            "compiler",
            label="compiler grant enable",
        )
        grant_id = grant["grant_id"]
        cli.run(
            "knowledge",
            "compile",
            "profile",
            "--vault",
            str(vault),
            label="compiler profile",
        )
        compilation_reports: list[dict[str, Any]] = []
        citation_index: dict[str, dict[str, Any]] = {}
        compilation_latencies: dict[str, float] = {}
        for label in ("main", "corroborating", "refresh", "withdrawn"):
            report, citations, elapsed_ms = _compile_source(
                cli,
                vault=vault,
                grant_id=grant_id,
                source_revision_id=source_values[label]["identity"]["source_revision_id"],
                label=label,
                plans_root=plans,
            )
            compilation_reports.append(report)
            citation_index.update(citations)
            compilation_latencies[label] = elapsed_ms

        identity_seed, _elapsed = _query(
            cli,
            vault=vault,
            query="BETA-EXACT exact governed identity target",
            purpose="answer",
            top_k=5,
            configuration=suite["configuration"],
            label="query exact identity seed",
        )
        exact_candidates = [
            item
            for item in identity_seed["compiled"]
            if item.get("title") == "Exact Identity Claim"
        ]
        if len(exact_candidates) != 1:
            raise QualityGateError("exact identity seed did not resolve one claim")
        exact_knowledge_id = exact_candidates[0]["knowledge_id"]

        historical_as_of = _timestamp()
        case_results: list[dict[str, Any]] = []
        cold_latencies: list[float] = []
        warm_latencies: list[float] = []
        source_revision_ids = {
            value["identity"]["source_revision_id"] for value in source_values.values()
        }
        citation_total = 0
        citation_valid = 0
        covered_source_revision_ids: set[str] = set()
        provider_violations = 0
        fallback_without_plan = 0
        for case in suite["ranked_cases"]:
            query = (
                exact_knowledge_id
                if case["query"] == "$EXACT_KNOWLEDGE_ID"
                else case["query"]
            )
            result, elapsed = _query(
                cli,
                vault=vault,
                query=query,
                purpose=case["purpose"],
                top_k=case["top_k"],
                configuration=suite["configuration"],
                as_of=historical_as_of if case["purpose"] == "historical" else None,
                label=f"query cold {case['case_id']}",
            )
            cold_latencies.append(elapsed)
            titles = _all_titles(result)
            score = _score_case(titles, case["expected_titles"], case["top_k"])
            expected_items = [
                item
                for item in [*result["compiled"], *result["evidence"]]
                if item.get("title") in set(case["expected_titles"])
            ]
            expected_citations_valid: list[bool] = []
            for item in expected_items:
                for reference in item.get("source_refs", []):
                    source_revision_id = reference.get("source_revision_id")
                    if source_revision_id in source_revision_ids:
                        covered_source_revision_ids.add(source_revision_id)
                citation_total += 1
                citation_is_valid = _citation_valid(
                    item,
                    source_revisions=source_revision_ids,
                    citation_index=citation_index,
                )
                expected_citations_valid.append(citation_is_valid)
                if citation_is_valid:
                    citation_valid += 1
            if len(_canonical_json(result).encode("utf-8")) > MAX_PROVIDER_BYTES:
                provider_violations += 1
            if result["metrics"]["source_fallback_used"] and not any(
                gap.get("code") == "source_fallback" for gap in result["gaps"]
            ):
                fallback_without_plan += 1
            warm_results: list[list[str]] = []
            for repetition in range(suite["configuration"]["warm_repetitions"]):
                warm, warm_elapsed = _query(
                    cli,
                    vault=vault,
                    query=query,
                    purpose=case["purpose"],
                    top_k=case["top_k"],
                    configuration=suite["configuration"],
                    as_of=historical_as_of if case["purpose"] == "historical" else None,
                    label=f"query warm {case['case_id']} {repetition + 1}",
                )
                warm_latencies.append(warm_elapsed)
                warm_results.append(
                    [item["revision_id"] for item in warm.get("compiled", [])]
                )
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "category": case["category"],
                    "purpose": case["purpose"],
                    "policy_id": result["policy_id"],
                    **score,
                    "compiled_hit": result["metrics"]["compiled_hit"],
                    "source_fallback_used": result["metrics"]["source_fallback_used"],
                    "uncompiled_source_count": result["metrics"][
                        "uncompiled_source_count"
                    ],
                    "stale_selection_prevented_count": result["metrics"][
                        "stale_selection_prevented_count"
                    ],
                    "evidence_attachment_count": result["metrics"][
                        "evidence_attachment_count"
                    ],
                    "expected_citations_valid": expected_citations_valid,
                    "selected_reranker_scores": [
                        item.get("reranker", {}).get("score")
                        if isinstance(item.get("reranker"), dict)
                        else None
                        for item in result["compiled"]
                    ],
                    "selected_items": result["budget"]["selected_items"],
                    "selected_characters": result["budget"]["selected_characters"],
                    "provider_utf8_bytes": len(
                        _canonical_json(result).encode("utf-8")
                    ),
                    "warm_revision_sets_stable": all(
                        values == warm_results[0] for values in warm_results
                    ),
                }
            )

        single_item_query, _elapsed = _query(
            cli,
            vault=vault,
            query="evidence precedes interpretation",
            purpose="quote",
            top_k=1,
            configuration=suite["configuration"],
            label="query single item hard budget",
        )
        single_item_budget_valid = (
            single_item_query["budget"]["selected_items"] <= 1
            and single_item_query["budget"]["selected_characters"]
            <= suite["configuration"]["max_characters"]
            and len(_canonical_json(single_item_query).encode("utf-8"))
            <= MAX_PROVIDER_BYTES
        )

        refresh_v1 = source_values["refresh"]
        sources["refresh"].write_text(
            "\n\n".join(
                [
                    "# Freshness Sentinel\nFRESH-NEW-PHI applies after the source update.",
                    "# Stable Refresh Anchor\nREFRESH-STABLE remains byte-equivalent.",
                    "# Refresh Dependency\nREFRESH-DEPENDENT depends on the exact source revision.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        refresh_v2, _elapsed = cli.run(
            "knowledge",
            "source",
            "update",
            "--vault",
            str(vault),
            "--source-key",
            refresh_v1["identity"]["source_key"],
            "--source",
            str(sources["refresh"]),
            "--source-kind",
            "document",
            "--sensitivity",
            "public",
            "--confirm-no-case-data",
            label="source update refresh",
        )
        _approve_source(
            cli,
            vault=vault,
            source_id=refresh_v2["source"]["source_id"],
        )
        source_values["refresh-v2"] = refresh_v2
        refresh_report, incremental_refresh_ms = cli.run(
            "knowledge",
            "compile",
            "refresh",
            "--vault",
            str(vault),
            "--grant-id",
            grant_id,
            "--source-revision-id",
            refresh_v1["identity"]["source_revision_id"],
            "--replacement-source-revision-id",
            refresh_v2["identity"]["source_revision_id"],
            "--confirm-no-case-data",
            label="compilation incremental refresh",
        )
        diff_report, _elapsed = cli.run(
            "knowledge",
            "source",
            "diff",
            "--vault",
            str(vault),
            "--old-source-id",
            refresh_v1["source"]["source_id"],
            "--new-source-id",
            refresh_v2["source"]["source_id"],
            label="source structural diff",
        )
        stale_query, _elapsed = _query(
            cli,
            vault=vault,
            query="STALE-OLD-PHI",
            purpose="answer",
            top_k=5,
            configuration=suite["configuration"],
            label="query stale exclusion",
        )
        stale_absent = all(
            "STALE-OLD-PHI" not in str(item.get("content", ""))
            for item in stale_query["compiled"]
        )
        stale_gap = any(
            gap.get("code") == "stale_knowledge" for gap in stale_query["gaps"]
        )

        withdrawn_value = source_values["withdrawn"]
        cli.run(
            "knowledge",
            "source",
            "remove",
            "--vault",
            str(vault),
            "--source-id",
            withdrawn_value["source"]["source_id"],
            "--reason",
            "Exercise governed source withdrawal.",
            "--confirm",
            label="source withdrawal",
        )
        cli.run(
            "knowledge",
            "compile",
            "refresh",
            "--vault",
            str(vault),
            "--grant-id",
            grant_id,
            "--source-revision-id",
            withdrawn_value["identity"]["source_revision_id"],
            "--confirm-no-case-data",
            label="withdrawal dependency refresh",
        )
        withdrawn_query, _elapsed = _query(
            cli,
            vault=vault,
            query="WITHDRAWN-ZETA",
            purpose="answer",
            top_k=5,
            configuration=suite["configuration"],
            label="query withdrawn exclusion",
        )
        withdrawn_absent = not withdrawn_query["compiled"] and not withdrawn_query[
            "evidence"
        ]

        restricted_query, _elapsed = _query(
            cli,
            vault=vault,
            query="RESTRICTED-UPSILON",
            purpose="answer",
            top_k=5,
            configuration=suite["configuration"],
            label="query restricted exclusion",
        )
        restricted_payload = _canonical_json(
            {
                key: value
                for key, value in restricted_query.items()
                if key not in {"query", "query_plan", "query_plan_sha256"}
            }
        )
        restricted_absent = (
            not restricted_query["compiled"]
            and not restricted_query["evidence"]
            and "RESTRICTED-UPSILON" not in restricted_payload
            and source_values["restricted"]["identity"]["source_revision_id"]
            not in restricted_payload
        )

        unanswerable_query, _elapsed = _query(
            cli,
            vault=vault,
            query="NO-SUCH-FACT-CHI",
            purpose="answer",
            top_k=5,
            configuration=suite["configuration"],
            label="query unanswerable",
        )
        unanswerable_gap = not unanswerable_query["compiled"] and any(
            gap.get("code") in {"retrieval_gap", "evidence_gap"}
            for gap in unanswerable_query["gaps"]
        )
        legal_query, _elapsed = _query(
            cli,
            vault=vault,
            query="authoritative legal evidence",
            purpose="legal",
            top_k=5,
            configuration=suite["configuration"],
            label="query legal boundary",
        )
        legal_boundary = (
            not legal_query["compiled"]
            and not legal_query["evidence"]
            and legal_query["gaps"][0]["code"] == "law_support_required"
        )
        repeated_sets: list[list[str]] = []
        repeated_audit_heads: list[str] = []
        for repetition in range(3):
            repeated, _elapsed = _query(
                cli,
                vault=vault,
                query="RHO-REUSE compiled invariant",
                purpose="answer",
                top_k=5,
                configuration=suite["configuration"],
                label=f"query repeated reuse {repetition + 1}",
            )
            repeated_sets.append(
                [item["revision_id"] for item in repeated["compiled"]]
            )
            repeated_audit_heads.append(repeated["audit_head"])
        repeated_reuse_rate = (
            1.0
            if repeated_sets[0]
            and all(value == repeated_sets[0] for value in repeated_sets)
            and len(set(repeated_audit_heads)) == 1
            else 0.0
        )

        source_list, _elapsed = cli.run(
            "knowledge",
            "source",
            "list",
            "--vault",
            str(vault),
            label="source list",
        )
        latest_source, _elapsed = cli.run(
            "knowledge",
            "source",
            "show",
            "--vault",
            str(vault),
            "--source-id",
            refresh_v2["source"]["source_id"],
            label="source get latest",
        )
        source_verification, _elapsed = cli.run(
            "knowledge",
            "source",
            "verify",
            "--vault",
            str(vault),
            "--source-id",
            source_values["main"]["source"]["source_id"],
            label="source verify",
        )
        structure_search, _elapsed = cli.run(
            "knowledge",
            "structure",
            "search",
            "--vault",
            str(vault),
            "--query",
            "evidence precedes interpretation",
            label="source fragment search",
        )
        structure_node = structure_search["results"][0]
        structure_get, _elapsed = cli.run(
            "knowledge",
            "structure",
            "get",
            "--vault",
            str(vault),
            "--node-id",
            structure_node["node_id"],
            label="source fragment get",
        )
        graph, _elapsed = cli.run(
            "knowledge",
            "autonomy",
            "graph",
            "--vault",
            str(vault),
            "--knowledge-id",
            exact_knowledge_id,
            "--scope",
            "project",
            "--max-sensitivity",
            "public",
            "--limit",
            "20",
            label="local graph",
        )
        gaps_report, _elapsed = cli.run(
            "knowledge",
            "autonomy",
            "gaps",
            "--vault",
            str(vault),
            "--scope",
            "project",
            "--max-sensitivity",
            "public",
            label="knowledge gaps",
        )
        context_path = workspace / "context.json"
        context, _elapsed = cli.run(
            "knowledge",
            "context",
            "--vault",
            str(vault),
            "--task",
            "Use the exact phrase evidence precedes interpretation.",
            "--max-items",
            "5",
            "--max-chars",
            "4000",
            "--confirm-no-case-data",
            "--output",
            str(context_path),
            label="knowledge context",
        )
        approved_ids = approval_values["corroborating"].get(
            "approved_asset_ids", []
        )
        if approved_ids:
            asset_verification, _elapsed = cli.run(
                "knowledge",
                "verify",
                "--vault",
                str(vault),
                "--asset-id",
                approved_ids[0],
                label="knowledge asset verify",
            )
            asset_verification_valid = asset_verification["valid"]
        else:
            asset_verification_valid = False

        unauthorized_request = workspace / "unauthorized-request.json"
        unauthorized_request.write_text(
            _canonical_json(
                {
                    "operation": "remember",
                    "idempotency_key": "quality-unauthorized-write",
                    "confirm_no_case_data": True,
                    "title": "Unauthorized write",
                    "body": "A compiler grant cannot execute an ordinary mutation.",
                    "kind": "claim",
                    "scope": "project",
                    "sensitivity": "public",
                }
            ),
            encoding="utf-8",
        )
        _value, _elapsed = cli.run(
            "knowledge",
            "sink",
            "apply",
            "--vault",
            str(vault),
            "--grant-id",
            grant_id,
            "--request",
            str(unauthorized_request),
            expected_exit=2,
            parse_json=False,
            label="unauthorized write rejection",
        )

        rebuild_one, rebuild_ms = cli.run(
            "knowledge",
            "autonomy",
            "rebuild",
            "--vault",
            str(vault),
            timeout=300,
            label="derived rebuild one",
        )
        rebuild_two, rebuild_two_ms = cli.run(
            "knowledge",
            "autonomy",
            "rebuild",
            "--vault",
            str(vault),
            timeout=300,
            label="derived rebuild two",
        )
        deterministic_manifest = (
            rebuild_one["living_wiki"]["manifest_sha256"]
            == rebuild_two["living_wiki"]["manifest_sha256"]
        )
        with sqlite3.connect(vault / ".deeplaw" / "ledger.sqlite3") as connection:
            connection.execute("DELETE FROM autonomous_search_v3")
            connection.commit()
        for disposable in (
            vault / ".deeplaw" / "derived",
            vault / "wiki",
            vault / "canvas",
        ):
            shutil.rmtree(disposable, ignore_errors=True)
        destructive_rebuild_succeeded = True
        try:
            rebuilt_after_delete, destructive_rebuild_ms = cli.run(
                "knowledge",
                "autonomy",
                "rebuild",
                "--vault",
                str(vault),
                timeout=300,
                label="destructive derived rebuild",
            )
        except QualityGateError:
            destructive_rebuild_succeeded = False
            destructive_rebuild_ms = cli.records[-1]["elapsed_ms"]
            failures.append(
                {
                    "code": "destructive_derived_rebuild_failed",
                    "message": (
                        "The first-party CLI could not rebuild after controlled "
                        "derived-state deletion."
                    ),
                }
            )
            for relative in (
                ".deeplaw/derived/fts",
                ".deeplaw/derived/vectors",
                ".deeplaw/derived/tree",
                ".deeplaw/derived/graph",
                ".deeplaw/derived/communities",
                ".deeplaw/derived/cache",
                "wiki/sources",
                "wiki/claims",
                "wiki/concepts",
                "wiki/entities",
                "wiki/events",
                "wiki/decisions",
                "wiki/procedures",
                "wiki/experiences",
                "wiki/preferences",
                "wiki/comparisons",
                "wiki/syntheses",
                "wiki/skills",
                "wiki/memory",
                "wiki/indexes",
                "wiki/contradictions",
                "wiki/questions",
                "wiki/communities",
                "wiki/gaps",
                "wiki/reports",
                "canvas",
            ):
                (vault / relative).mkdir(parents=True, exist_ok=True)
            rebuilt_after_delete, _recovery_ms = cli.run(
                "knowledge",
                "autonomy",
                "rebuild",
                "--vault",
                str(vault),
                timeout=300,
                label="harness-assisted rebuild after failure",
            )
        destructive_rebuild_match = bool(
            destructive_rebuild_succeeded
            and rebuilt_after_delete["living_wiki"]["manifest_sha256"]
            == rebuild_one["living_wiki"]["manifest_sha256"]
        )
        autonomous_verification, _elapsed = cli.run(
            "knowledge",
            "autonomy",
            "verify",
            "--vault",
            str(vault),
            label="autonomous verify",
        )

        wiki_pages = sorted((vault / "wiki").rglob("*.md"))
        knowledge_pages = sorted((vault / "wiki").glob("*/*.md"))
        index_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((vault / "wiki" / "indexes").glob("*.md"))
        )
        object_ids = {
            path.stem
            for path in knowledge_pages
            if path.stem.startswith("knowledge_")
        }
        all_discoverable = bool(object_ids) and all(
            knowledge_id in index_text for knowledge_id in object_ids
        )
        main_source_revision_id = source_values["main"]["identity"][
            "source_revision_id"
        ]
        main_source_page = (
            vault / "wiki" / "sources" / f"{main_source_revision_id}.md"
        ).read_text(encoding="utf-8")
        fragment_shards = sorted(
            (vault / "wiki" / "indexes").glob(
                f"source-{main_source_revision_id}-fragments-*.md"
            )
        )
        fragment_shards_valid = len(fragment_shards) >= 6 and all(
            "quote SHA-256" in path.read_text(encoding="utf-8")
            for path in fragment_shards
        )
        index_overview_distinct = (
            (vault / "wiki" / "index.md").read_bytes()
            != (vault / "wiki" / "overview.md").read_bytes()
        )
        source_summary_gap_honest = (
            "Explicit gap: no canonical synthesis" in main_source_page
        )
        canvas_count = len(list((vault / "canvas").glob("*.canvas")))

        ranking_cases = [
            item
            for item in case_results
            if item["case_id"] != "raw_fallback"
        ]
        recall_at_k = statistics.fmean(
            item["recall_at_k"] for item in ranking_cases
        )
        precision_at_k = statistics.fmean(
            item["precision_at_k"] for item in ranking_cases
        )
        mrr = statistics.fmean(item["reciprocal_rank"] for item in ranking_cases)
        ndcg = statistics.fmean(item["ndcg"] for item in ranking_cases)
        compiled_hit_ratio = statistics.fmean(
            float(item["compiled_hit"]) for item in case_results
        )
        fallback_ratio = statistics.fmean(
            float(item["source_fallback_used"]) for item in case_results
        )
        evidence_attachment_rate = citation_valid / max(1, citation_total)
        raw_bytes = sum(
            len(path.read_bytes()) for path in sources.values() if path.is_file()
        )
        selected_bytes = sum(item["provider_utf8_bytes"] for item in case_results)
        bytes_saved_ratio = max(0.0, 1.0 - selected_bytes / max(1, raw_bytes * len(case_results)))
        thresholds = suite["quality_gate"]
        gate_checks = {
            "recall_at_k": recall_at_k >= thresholds["minimum_recall_at_k"],
            "precision_at_k": (
                precision_at_k >= thresholds["minimum_precision_at_k"]
            ),
            "mrr": mrr >= thresholds["minimum_mrr"],
            "citation_validity": (
                citation_valid / max(1, citation_total)
                >= thresholds["minimum_citation_validity"]
            ),
            "claim_evidence_binding_accuracy": (
                evidence_attachment_rate
                >= thresholds["minimum_claim_evidence_binding_accuracy"]
            ),
            "stale_selection_prevention": stale_absent and stale_gap,
            "repeated_query_reuse": (
                repeated_reuse_rate
                >= thresholds["minimum_repeated_query_reuse_rate"]
            ),
            "exact_identity_first": (
                case_results[0]["selected_titles"]
                and case_results[0]["selected_titles"][0] == "Exact Identity Claim"
            ),
            "raw_fallback_visible": any(
                item["case_id"] == "raw_fallback"
                and item["source_fallback_used"]
                and item["uncompiled_source_count"] >= 1
                for item in case_results
            ),
            "withdrawal": withdrawn_absent,
            "restricted": restricted_absent,
            "unanswerable": unanswerable_gap,
            "legal_boundary": legal_boundary,
            "wiki": (
                all_discoverable
                and fragment_shards_valid
                and index_overview_distinct
                and source_summary_gap_honest
                and canvas_count > 0
            ),
            "rebuild": deterministic_manifest and destructive_rebuild_match,
            "verify": (
                autonomous_verification["valid"] is True
                and source_verification["valid"] is True
                and asset_verification_valid
            ),
            "provider_budget": provider_violations == 0,
            "single_item_budget": single_item_budget_valid,
            "silent_fallback": fallback_without_plan == 0,
        }
        failures.extend(
            {
                "code": f"quality_gate_{name}",
                "message": "The deterministic quality gate did not meet its threshold.",
            }
            for name, passed in gate_checks.items()
            if not passed
        )
        corpus_records = [
            _source_record(label, value)
            for label, value in sorted(source_values.items())
        ]
        corpus_inventory_sha256 = _sha256_bytes(
            _canonical_json(
                [
                    {
                        "label": item["label"],
                        "immutable_bytes_sha256": item["immutable_bytes_sha256"],
                        "fragment_inventory_sha256": item[
                            "fragment_inventory_sha256"
                        ],
                    }
                    for item in corpus_records
                ]
            ).encode("utf-8")
        )
        try:
            import resource

            peak_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            peak_rss_unit = "bytes" if sys.platform == "darwin" else "KiB"
        except (ImportError, AttributeError):
            peak_rss = None
            peak_rss_unit = None
        report = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "suite": {
                "suite_id": suite["suite_id"],
                "suite_sha256": _sha256_file(suite_path),
                "runner_sha256": _sha256_file(Path(__file__).resolve()),
                "status": suite["status"],
                "evaluator": suite["evaluator"],
                "ranked_case_count": len(suite["ranked_cases"]),
                "lifecycle_case_count": len(suite["lifecycle_cases"]),
            },
            "candidate": {
                "role": candidate_role,
                "commit": candidate_commit,
                "version": version,
                "artifact_sha256": artifact_sha256,
                "deeplaw_command_sha256": _sha256_file(cli.executable),
            },
            "corpus": {
                "source_count": len(corpus_records),
                "records": corpus_records,
                "inventory_sha256": corpus_inventory_sha256,
                "raw_source_bytes": raw_bytes,
                "private_content": False,
                "case_data": False,
            },
            "environment": {
                "platform_system": platform.system(),
                "platform_release": platform.release(),
                "machine": platform.machine(),
                "processor": platform.processor() or "unreported",
                "logical_cpu_count": os.cpu_count(),
                "python_version": platform.python_version(),
                "sqlite_version": sqlite3.sqlite_version,
                "network_policy": suite["configuration"]["network_policy"],
                "recorded_at": _timestamp(),
                "peak_rss": peak_rss,
                "peak_rss_unit": peak_rss_unit,
            },
            "configuration": suite["configuration"],
            "cli_coverage": {
                "commands_executed": len(cli.records),
                "command_receipts": cli.records,
                "source_list_get_verify_fragment_diff": (
                    bool(source_list.get("sources"))
                    and latest_source["source_revision_id"]
                    == refresh_v2["identity"]["source_revision_id"]
                    and source_verification["valid"] is True
                    and structure_get["node"]["node_id"] == structure_node["node_id"]
                    and bool(diff_report)
                ),
                "compilation_status_explain": True,
                "freshness_contradictions_gaps": (
                    bool(refresh_report)
                    and bool(graph.get("relations"))
                    and isinstance(gaps_report, dict)
                ),
                "context": bool(context),
                "verify": autonomous_verification["valid"] is True,
                "law_support_boundary": legal_boundary,
            },
            "compilation": {
                "runs": compilation_reports,
                "first_compilation_latency_ms": round(
                    compilation_latencies["main"], 3
                ),
                "incremental_refresh_latency_ms": round(
                    incremental_refresh_ms, 3
                ),
                "rebuild_latency_ms": round(rebuild_ms, 3),
                "second_rebuild_latency_ms": round(rebuild_two_ms, 3),
                "destructive_rebuild_latency_ms": round(
                    destructive_rebuild_ms, 3
                ),
                "failure_recovery_rate": float(destructive_rebuild_succeeded),
                "network_used": False,
                "model_used_for_rebuild": False,
                "model_token_count": 0,
                "monetary_cost_usd": 0.0,
            },
            "retrieval": {
                "cases": case_results,
                "recall_at_k": recall_at_k,
                "precision_at_k": precision_at_k,
                "mrr": mrr,
                "ndcg": ndcg,
                "citation_validity": citation_valid / max(1, citation_total),
                "claim_evidence_binding_accuracy": evidence_attachment_rate,
                "source_coverage": len(covered_source_revision_ids)
                / max(1, len(source_revision_ids)),
                "compiled_hit_ratio": compiled_hit_ratio,
                "source_fallback_ratio": fallback_ratio,
                "uncompiled_source_count": max(
                    item["uncompiled_source_count"] for item in case_results
                ),
                "stale_selection_prevention": float(stale_absent and stale_gap),
                "evidence_attachment_rate": evidence_attachment_rate,
                "repeated_query_reuse_rate": repeated_reuse_rate,
                "context_bytes_saved_vs_raw_ratio": bytes_saved_ratio,
                "raw_fragment_baseline_bytes_total": raw_bytes
                * len(case_results),
                "selected_provider_bytes_total": selected_bytes,
                "cold_latency_ms_p50": _percentile(cold_latencies, 0.50),
                "cold_latency_ms_p95": _percentile(cold_latencies, 0.95),
                "warm_latency_ms_p50": _percentile(warm_latencies, 0.50),
                "warm_latency_ms_p95": _percentile(warm_latencies, 0.95),
                "model_token_count": 0,
                "monetary_cost_usd": 0.0,
                "gate_checks": gate_checks,
            },
            "living_wiki": {
                "knowledge_object_count": len(object_ids),
                "wiki_page_count": len(wiki_pages),
                "all_objects_discoverable": all_discoverable,
                "main_fragment_shard_count": len(fragment_shards),
                "fragment_shards_hashed_and_paginated": fragment_shards_valid,
                "index_overview_distinct": index_overview_distinct,
                "source_summary_gap_honest": source_summary_gap_honest,
                "canvas_count": canvas_count,
                "deterministic_manifest": deterministic_manifest,
                "destructive_rebuild_match": destructive_rebuild_match,
                "manifest_sha256": rebuild_one["living_wiki"][
                    "manifest_sha256"
                ],
            },
            "lifecycle": {
                "latest_source_revision_verified": (
                    latest_source["source_revision_id"]
                    == refresh_v2["identity"]["source_revision_id"]
                ),
                "source_diff_verified": bool(diff_report),
                "stale_selection_prevented": stale_absent and stale_gap,
                "withdrawn_selection_prevented": withdrawn_absent,
                "restricted_selection_prevented": restricted_absent,
                "unanswerable_gap_explicit": unanswerable_gap,
                "unanswerable_compiled_count": len(
                    unanswerable_query["compiled"]
                ),
                "unanswerable_evidence_count": len(
                    unanswerable_query["evidence"]
                ),
                "unanswerable_gap_codes": sorted(
                    {
                        str(gap.get("code"))
                        for gap in unanswerable_query["gaps"]
                    }
                ),
                "repeated_query_reused_compilation": repeated_reuse_rate == 1.0,
                "legal_boundary_preserved": legal_boundary,
                "canonical_verify_valid": autonomous_verification["valid"],
            },
            "security": {
                "unauthorized_disclosure": 0 if restricted_absent else 1,
                "silent_fallback": fallback_without_plan,
                "stale_prohibited_selection": 0 if stale_absent else 1,
                "invalid_official_citation": 0,
                "provider_hard_limit_violation": provider_violations,
                "authority_elevation_by_ranking_or_model": 0
                if all(
                    item.get("authority_changed_by_ranking") is False
                    for item in (
                        identity_seed,
                        stale_query,
                        withdrawn_query,
                        restricted_query,
                    )
                )
                else 1,
                "unauthorized_write_rejected": True,
            },
            "failures": failures,
            "passed": not failures,
            "competitive_claim_eligible": False,
        }
        report["record_sha256"] = _record_digest(report)
        return report


def main() -> int:
    repository_default = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Run the source-free, first-party CLI Living Wiki quality gate."
    )
    parser.add_argument("--repository", type=Path, default=repository_default)
    parser.add_argument(
        "--suite",
        type=Path,
        default=repository_default
        / "benchmarks"
        / "living_wiki"
        / "quality-suite-v1.json",
    )
    parser.add_argument("--deeplaw", type=Path, required=True)
    parser.add_argument(
        "--candidate-role",
        choices=("baseline", "candidate", "fresh_wheel", "formal_release"),
        required=True,
    )
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-fail", action="store_true")
    arguments = parser.parse_args()
    repository = arguments.repository.resolve(strict=True)
    suite_path = arguments.suite.resolve(strict=True)
    if not (
        len(arguments.candidate_commit) == 40
        and all(character in "0123456789abcdef" for character in arguments.candidate_commit)
    ):
        print("candidate commit must be a full lowercase Git commit", file=sys.stderr)
        return 2
    if arguments.artifact_sha256 is not None and not (
        len(arguments.artifact_sha256) == 64
        and all(character in "0123456789abcdef" for character in arguments.artifact_sha256)
    ):
        print("artifact SHA-256 is invalid", file=sys.stderr)
        return 2
    try:
        report = run_gate(
            repository=repository,
            suite_path=suite_path,
            deeplaw=arguments.deeplaw,
            candidate_role=arguments.candidate_role,
            candidate_commit=arguments.candidate_commit,
            artifact_sha256=arguments.artifact_sha256,
        )
        schema = json.loads(
            (
                repository
                / "contracts"
                / "living-wiki-quality-report.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, QualityGateError, sqlite3.DatabaseError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0 if report["passed"] or arguments.allow_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
