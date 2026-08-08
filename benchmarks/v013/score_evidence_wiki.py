"""Deterministic, Gold-only scoring for the Evidence Wiki development lane.

The scorer consumes only a serialized candidate and an independently frozen Gold
manifest.  It never opens the development source, a Vault, or a provider.  The
report is deliberately claim-ineligible: a passing development fixture is
evidence of exercised seams, not release or comparative evidence.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from deeplaw.util import canonical_json, sha256_bytes

SCHEMA_VERSION = "deeplaw.v013.evidence-wiki-score/v1"
GOLD_SCHEMA_VERSION = "deeplaw.evidence-wiki-owner-task-gold/v1"
MAX_CANDIDATE_BYTES = 256 * 1024
MAX_GOLD_BYTES = 64 * 1024
PROVIDER_HARD_LIMIT = 65_536
LOCAL_HARD_LIMIT = 256 * 1024

_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_PATH = re.compile(
    r'(?:^|[\s=:"])\/(?:Users|home|tmp|private|var)(?:[\s/\"]|$)|[A-Za-z]:[\\/]'
)
_SECRET = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|auth(?:orization)?|bearer|secret)\s*[\"']?\s*[:=]"
)

_CHAIN_NODES = frozenset(
    {
        "source_bytes",
        "source_revision",
        "fragment",
        "locator",
        "knowledge_revision",
        "statement",
        "relation_revision",
        "ledger_current",
        "page_registry",
        "link_index",
        "resolver",
        "wiki_page",
        "backlink_or_outlink",
        "exact_evidence_read",
    }
)


def _load_json(path: str | Path, *, maximum: int) -> dict[str, Any]:
    candidate_path = Path(path)
    if candidate_path.is_symlink() or not candidate_path.is_file():
        raise ValueError("score input must be a regular non-symlink file")
    if candidate_path.stat().st_size > maximum:
        raise ValueError("score input exceeds its byte bound")
    try:
        value = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("score input must be UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("score input must contain one JSON object")
    return value


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"candidate {field} must be an object")
    return value


def _hash(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ValueError(f"candidate {field} must be a SHA-256")
    return value


def _bounded_id(value: Any, *, field: str, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f"candidate {field} is invalid")
    return value


def _bounded_locator(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2_000
        or "\x00" in value
        or _PATH.search(value)
        or _SECRET.search(value)
    ):
        raise ValueError(f"candidate {field} is invalid")
    return value


def _validate_source_refs(value: Any, *, field: str) -> None:
    if not isinstance(value, list) or not value or len(value) > 8:
        raise ValueError(f"candidate {field} is invalid")
    for index, reference in enumerate(value):
        item = _mapping(reference, field=f"{field}[{index}]")
        required = {"source_revision_id", "fragment_id", "locator", "quote_sha256"}
        allowed = {*required, "reference_sha256", "metadata_omitted"}
        if not required <= set(item) or not set(item) <= allowed:
            raise ValueError(f"candidate {field}[{index}] has an invalid shape")
        for key in ("source_revision_id", "fragment_id"):
            _bounded_id(item.get(key), field=f"{field}[{index}].{key}")
        _bounded_locator(item.get("locator"), field=f"{field}[{index}].locator")
        _hash(item.get("quote_sha256"), field=f"{field}[{index}].quote_sha256")
        if "reference_sha256" in item:
            _hash(item.get("reference_sha256"), field=f"{field}[{index}].reference_sha256")
        if "metadata_omitted" in item and not isinstance(item["metadata_omitted"], bool):
            raise ValueError(f"candidate {field}[{index}].metadata_omitted is invalid")


def _validate_candidate(candidate: Mapping[str, Any]) -> None:
    if candidate.get("schema_version") != "deeplaw.v013.evidence-wiki-candidate/v1":
        raise ValueError("candidate schema version is invalid")
    if candidate.get("status") != "executed":
        raise ValueError("candidate status is not executed")
    _bounded_id(candidate.get("case_id"), field="case_id")
    if candidate.get("claim_eligible") is not False or candidate.get(
        "competitive_claim_eligible"
    ) is not False:
        raise ValueError("candidate eligibility flags must remain false")
    if candidate.get("write_performed") is not False:
        raise ValueError("candidate reports a canonical write")
    _hash(candidate.get("input_source_sha256"), field="input_source_sha256")
    source = _mapping(candidate.get("source"), field="source")
    for key in ("source_revision_id", "fragment_id"):
        _bounded_id(source.get(key), field=f"source.{key}")
    _bounded_locator(source.get("locator"), field="source.locator")
    _hash(source.get("content_sha256"), field="source.content_sha256")
    _hash(source.get("fragment_text_sha256"), field="source.fragment_text_sha256")

    statement = _mapping(candidate.get("statement"), field="statement")
    for key in ("statement_id", "knowledge_id", "knowledge_revision_id"):
        _bounded_id(statement.get(key), field=f"statement.{key}")
    _hash(statement.get("statement_sha256"), field="statement.statement_sha256")
    _hash(statement.get("receipt_sha256"), field="statement.receipt_sha256")
    if statement.get("receipt_status") not in {"present", "missing"}:
        raise ValueError("candidate statement receipt status is invalid")
    _validate_source_refs(statement.get("source_refs"), field="statement.source_refs")

    interpretation = _mapping(candidate.get("interpretation"), field="interpretation")
    for key in ("knowledge_id", "revision_id", "origin", "authority"):
        _bounded_id(interpretation.get(key), field=f"interpretation.{key}")
    if not isinstance(interpretation.get("legal_authority"), bool):
        raise ValueError("candidate interpretation legal authority is invalid")
    if not isinstance(interpretation.get("source_free"), bool):
        raise ValueError("candidate interpretation source-free flag is invalid")

    relation = _mapping(candidate.get("relation"), field="relation")
    _bounded_id(relation.get("relation_revision_id"), field="relation.relation_revision_id")
    _bounded_id(relation.get("subject_knowledge_id"), field="relation.subject_knowledge_id")
    _bounded_id(relation.get("object_knowledge_id"), field="relation.object_knowledge_id")
    _bounded_id(relation.get("predicate"), field="relation.predicate")
    if not isinstance(relation.get("evidence_refs"), list):
        raise ValueError("candidate relation evidence is invalid")
    if relation.get("evidence_refs"):
        _validate_source_refs(relation.get("evidence_refs"), field="relation.evidence_refs")
    for key in ("origin", "authority"):
        _bounded_id(relation.get(key), field=f"relation.{key}")
    if relation.get("legal_authority") is not False:
        raise ValueError("candidate relation legal authority must be false")

    ledger = _mapping(candidate.get("ledger"), field="ledger")
    for key in (
        "claim_current_revision_id",
        "interpretation_current_revision_id",
        "relation_current_revision_id",
        "audit_head_before_read",
        "audit_head_after_read",
    ):
        _bounded_id(ledger.get(key), field=f"ledger.{key}")
    if ledger.get("audit_head_unchanged") is not True:
        raise ValueError("candidate ledger changed during read qualification")

    source_read = _mapping(candidate.get("source_read"), field="source_read")
    for key in (
        "source_fragment_exact_match",
        "source_bytes_exact_match",
        "source_read_write_performed",
    ):
        if not isinstance(source_read.get(key), bool):
            raise ValueError(f"candidate source_read.{key} is invalid")
    wiki = _mapping(candidate.get("wiki"), field="wiki")
    registry = _mapping(wiki.get("registry"), field="wiki.registry")
    link_index = _mapping(wiki.get("link_index"), field="wiki.link_index")
    resolver = _mapping(wiki.get("resolver"), field="wiki.resolver")
    for key in ("valid", "source_page_registered", "claim_page_registered"):
        if not isinstance(registry.get(key), bool):
            raise ValueError(f"candidate wiki.registry.{key} is invalid")
    if not isinstance(link_index.get("valid"), bool):
        raise ValueError("candidate wiki.link_index.valid is invalid")
    source_resolver = _mapping(
        resolver.get("source_fragment"), field="wiki.resolver.source_fragment"
    )
    claim_resolver = _mapping(resolver.get("claim"), field="wiki.resolver.claim")
    statement_resolver = _mapping(
        resolver.get("statement_target"), field="wiki.resolver.statement_target"
    )
    for value, field in (
        (source_resolver.get("status"), "source_fragment.status"),
        (claim_resolver.get("status"), "claim.status"),
        (statement_resolver.get("status"), "statement_target.status"),
    ):
        _bounded_id(value, field=f"wiki.resolver.{field}")
    for value, field in (
        (statement_resolver.get("reason"), "statement_target.reason"),
        (statement_resolver.get("gap"), "statement_target.gap"),
    ):
        if value is not None:
            _bounded_id(value, field=f"wiki.resolver.{field}")
    for key in (
        "source_page_read_only",
        "claim_page_read_only",
        "link_read_only",
        "link_index_used",
    ):
        if not isinstance(wiki.get(key), bool):
            raise ValueError(f"candidate wiki.{key} is invalid")

    for lane in ("query", "context"):
        section = _mapping(candidate.get(lane), field=lane)
        if section.get("query_plan_version") != "6":
            raise ValueError(f"candidate {lane} is not Query Plan v6")
        if not isinstance(section.get("provider_bytes"), int) or not 0 <= section[
            "provider_bytes"
        ] <= PROVIDER_HARD_LIMIT:
            raise ValueError(f"candidate {lane} provider bytes exceed the hard limit")
        if section.get("write_performed") is not False:
            raise ValueError(f"candidate {lane} reports a write")
        _bounded_id(section.get("receipt_id"), field=f"{lane}.receipt_id")

    limits = _mapping(candidate.get("limits"), field="limits")
    if limits.get("provider_hard_limit_bytes") != PROVIDER_HARD_LIMIT:
        raise ValueError("candidate provider hard limit is not 64 KiB")
    if limits.get("local_capsule_hard_limit_bytes") != LOCAL_HARD_LIMIT:
        raise ValueError("candidate local hard limit is invalid")
    for lane in ("query_provider_bytes", "context_provider_bytes"):
        if not isinstance(limits.get(lane), int) or not 0 <= limits[lane] <= PROVIDER_HARD_LIMIT:
            raise ValueError(f"candidate limits.{lane} is invalid")

    serialized = canonical_json(candidate)
    if len(serialized.encode("utf-8")) > LOCAL_HARD_LIMIT:
        raise ValueError("candidate exceeds the local output bound")
    if _PATH.search(serialized) or _SECRET.search(serialized):
        raise ValueError("candidate contains a path or secret-like material")


def _validate_gold(gold: Mapping[str, Any], *, case_id: str) -> Mapping[str, Any]:
    if gold.get("schema_version") != GOLD_SCHEMA_VERSION:
        raise ValueError("Gold schema version is invalid")
    if gold.get("candidate_visible_when_frozen") is not False:
        raise ValueError("Gold must be frozen before candidate visibility")
    if gold.get("claim_eligible") is not False:
        raise ValueError("development Gold must remain claim-ineligible")
    if gold.get("case_id") != case_id:
        raise ValueError("candidate case is absent from Gold")
    if not isinstance(gold.get("status"), str) or not gold["status"]:
        raise ValueError("Gold status is required")
    quote = gold.get("required_quote")
    if (
        not isinstance(quote, str)
        or not quote
        or len(quote) > 8_000
        or _PATH.search(quote)
        or _SECRET.search(quote)
    ):
        raise ValueError("Gold required_quote is invalid")
    predicate = gold.get("required_relation_predicate")
    _bounded_id(predicate, field="Gold.required_relation_predicate")
    chain_nodes = gold.get("required_chain_nodes")
    if (
        not isinstance(chain_nodes, list)
        or set(chain_nodes) != _CHAIN_NODES
        or len(chain_nodes) != len(_CHAIN_NODES)
    ):
        raise ValueError("Gold required_chain_nodes are invalid")
    requirements = _mapping(gold.get("hard_requirements"), field="Gold.hard_requirements")
    required_fields = {
        "source_bytes_sha256_valid",
        "statement_quote_sha256_valid",
        "locator_present",
        "statement_receipt_valid",
        "registry_and_link_index_verified",
        "source_fragment_resolved",
        "exact_source_read_matches_ingested_bytes",
        "agent_origin",
        "agent_legal_authority",
        "read_write_performed",
        "read_audit_head_changed",
    }
    if set(requirements) != required_fields:
        raise ValueError("Gold hard_requirements are invalid")
    limitation = gold.get("known_expected_limitation")
    if not isinstance(limitation, str) or "statement_map_deferred" not in limitation:
        raise ValueError("Gold known_expected_limitation is invalid")
    return requirements


def _check(
    checks: dict[str, dict[str, Any]],
    failures: list[str],
    name: str,
    actual: Any,
    expected: Any,
) -> None:
    passed = actual == expected
    checks[name] = {"passed": passed, "actual": actual, "expected": expected}
    if not passed:
        failures.append(name)


def score_evidence_wiki(
    *, candidate: Mapping[str, Any], gold: Mapping[str, Any]
) -> dict[str, Any]:
    """Score one candidate against a frozen Gold manifest without reading source bytes."""

    _validate_candidate(candidate)
    requirements = _validate_gold(gold, case_id=str(candidate["case_id"]))
    source = _mapping(candidate["source"], field="source")
    statement = _mapping(candidate["statement"], field="statement")
    interpretation = _mapping(candidate["interpretation"], field="interpretation")
    relation = _mapping(candidate["relation"], field="relation")
    wiki = _mapping(candidate["wiki"], field="wiki")
    registry = _mapping(wiki["registry"], field="wiki.registry")
    link_index = _mapping(wiki["link_index"], field="wiki.link_index")
    resolver = _mapping(wiki["resolver"], field="wiki.resolver")
    source_resolver = _mapping(resolver["source_fragment"], field="wiki.resolver.source_fragment")
    claim_resolver = _mapping(resolver["claim"], field="wiki.resolver.claim")
    statement_resolver = _mapping(
        resolver["statement_target"], field="wiki.resolver.statement_target"
    )
    query = _mapping(candidate["query"], field="query")
    context = _mapping(candidate["context"], field="context")
    limits = _mapping(candidate["limits"], field="limits")

    checks: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    _check(
        checks,
        failures,
        "source_exact_read",
        candidate["source_read"]["source_fragment_exact_match"],
        requirements["source_fragment_resolved"],
    )
    _check(
        checks,
        failures,
        "statement_receipt_status",
        statement["receipt_status"],
        "present" if requirements["statement_receipt_valid"] else "missing",
    )
    _check(
        checks,
        failures,
        "relation_predicate",
        relation["predicate"],
        gold["required_relation_predicate"],
    )
    _check(
        checks,
        failures,
        "interpretation_origin",
        interpretation["origin"],
        requirements["agent_origin"],
    )
    _check(
        checks,
        failures,
        "interpretation_authority",
        interpretation["authority"],
        "agent_derived",
    )
    _check(
        checks,
        failures,
        "legal_authority",
        interpretation["legal_authority"],
        requirements["agent_legal_authority"],
    )
    _check(
        checks,
        failures,
        "statement_resolver_status",
        statement_resolver["status"],
        "index_unavailable",
    )
    _check(
        checks,
        failures,
        "statement_resolver_reason",
        statement_resolver.get("reason"),
        "statement_map_deferred",
    )
    _check(
        checks,
        failures,
        "statement_resolver_gap",
        statement_resolver.get("gap"),
        "statement_semantic_target_not_indexed",
    )
    _check(
        checks,
        failures,
        "query_plan_version",
        query["query_plan_version"],
        "6",
    )
    _check(
        checks,
        failures,
        "context_plan_version",
        context["query_plan_version"],
        "6",
    )
    aggregate_write = any(
        bool(value)
        for value in (
            candidate["write_performed"],
            candidate["source_read"]["source_read_write_performed"],
            wiki["source_page_read_only"] is False,
            wiki["claim_page_read_only"] is False,
            wiki["link_read_only"] is False,
            query["write_performed"],
            context["write_performed"],
        )
    )
    _check(
        checks,
        failures,
        "write_performed",
        aggregate_write,
        requirements["read_write_performed"],
    )
    _check(
        checks,
        failures,
        "provider_hard_limit_bytes",
        limits["provider_hard_limit_bytes"],
        PROVIDER_HARD_LIMIT,
    )

    # These are hard evidence-chain checks, independent of Gold wording.
    _check(checks, failures, "wiki_registry_valid", registry["valid"], True)
    _check(checks, failures, "wiki_link_index_valid", link_index["valid"], True)
    _check(checks, failures, "source_resolver_admitted", source_resolver.get("admitted"), True)
    _check(checks, failures, "claim_resolver_admitted", claim_resolver.get("admitted"), True)
    _check(checks, failures, "relation_evidence_present", bool(relation["evidence_refs"]), True)
    _check(checks, failures, "source_page_read_only", wiki["source_page_read_only"], True)
    _check(checks, failures, "claim_page_read_only", wiki["claim_page_read_only"], True)
    _check(checks, failures, "link_read_only", wiki["link_read_only"], True)
    _check(checks, failures, "link_index_used", wiki["link_index_used"], True)
    _check(
        checks,
        failures,
        "backlink_or_outlink_present",
        isinstance(wiki.get("link_count"), int) and wiki["link_count"] > 0,
        True,
    )
    _check(checks, failures, "statement_source_binding", bool(statement["source_refs"]), True)
    _check(checks, failures, "agent_source_not_free", interpretation["source_free"], False)
    _check(checks, failures, "relation_legal_authority", relation["legal_authority"], False)
    _check(
        checks,
        failures,
        "source_bytes_sha256_valid",
        bool(_HASH.fullmatch(str(source["content_sha256"]))),
        requirements["source_bytes_sha256_valid"],
    )
    _check(
        checks,
        failures,
        "exact_source_read_matches_ingested_bytes",
        candidate["source_read"]["source_bytes_exact_match"],
        requirements["exact_source_read_matches_ingested_bytes"],
    )
    _check(
        checks,
        failures,
        "statement_quote_sha256_valid",
        statement["statement_sha256"]
        == sha256_bytes(str(gold["required_quote"]).encode("utf-8")),
        requirements["statement_quote_sha256_valid"],
    )
    _check(
        checks,
        failures,
        "locator_present",
        bool(source["locator"]),
        requirements["locator_present"],
    )
    _check(
        checks,
        failures,
        "read_audit_head_changed",
        not candidate["ledger"]["audit_head_unchanged"],
        requirements["read_audit_head_changed"],
    )

    max_provider_bytes = max(query["provider_bytes"], context["provider_bytes"])
    max_local_bytes = len(canonical_json(candidate).encode("utf-8"))
    _check(checks, failures, "provider_budget", max_provider_bytes <= PROVIDER_HARD_LIMIT, True)
    _check(checks, failures, "local_budget", max_local_bytes <= LOCAL_HARD_LIMIT, True)

    expected_hashes = gold.get("exact_hashes")
    if expected_hashes is not None:
        exact = _mapping(expected_hashes, field="Gold.exact_hashes")
        for key, candidate_value in (
            ("source_content_sha256", source["content_sha256"]),
            ("source_fragment_text_sha256", source["fragment_text_sha256"]),
            ("statement_sha256", statement["statement_sha256"]),
            ("receipt_sha256", statement["receipt_sha256"]),
        ):
            if key in exact:
                _hash(exact[key], field=f"Gold.exact_hashes.{key}")
                _check(checks, failures, key, candidate_value, exact[key])

    statement_deferred = (
        statement_resolver.get("status") == "index_unavailable"
        and statement_resolver.get("reason") == "statement_map_deferred"
    )
    metrics = {
        "source_exact_match": 1.0 if checks["source_exact_read"]["passed"] else 0.0,
        "statement_receipt_recall": 1.0 if checks["statement_receipt_status"]["passed"] else 0.0,
        "relation_evidence_recall": 1.0 if checks["relation_evidence_present"]["passed"] else 0.0,
        "wiki_registry_integrity": 1.0 if checks["wiki_registry_valid"]["passed"] else 0.0,
        "resolver_source_admission": 1.0
        if checks["source_resolver_admitted"]["passed"]
        else 0.0,
        "resolver_claim_admission": 1.0
        if checks["claim_resolver_admitted"]["passed"]
        else 0.0,
        "provider_bytes": max_provider_bytes,
        "local_bytes": max_local_bytes,
        "redundancy_rate": None,
        "useful_context_recall": None,
        "relevant_chars_context_chars": None,
    }
    known_limitations = [
        "development fixture only; no human Gold review has been completed",
        "model/provider output and external hosts were not used as labels",
    ]
    if statement_deferred:
        known_limitations.append("statement_target_resolver_deferred")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "scored",
        "candidate_sha256": sha256_bytes(canonical_json(candidate).encode("utf-8")),
        "gold_sha256": sha256_bytes(canonical_json(gold).encode("utf-8")),
        "checks": checks,
        "hard_failures": sorted(set(failures)),
        "metrics": metrics,
        "frozen_budgets": {
            "provider_bytes": PROVIDER_HARD_LIMIT,
            "local_bytes": LOCAL_HARD_LIMIT,
        },
        "thresholds_sha256": sha256_bytes(canonical_json(gold).encode("utf-8")),
        "development_thresholds_passed": not failures,
        "known_limitations": known_limitations,
        "not_executed": ["human_gold_review", "real_provider_host", "relation_path_query"],
        "release_gate_passed": False,
        "claim_eligible": False,
        "competitive_claim_eligible": False,
    }


def score_candidate(
    candidate: Mapping[str, Any], gold: Mapping[str, Any]
) -> dict[str, Any]:
    """Positional compatibility alias for benchmark callers."""

    return score_evidence_wiki(candidate=candidate, gold=gold)


def score_files(candidate_path: str | Path, gold_path: str | Path) -> dict[str, Any]:
    return score_evidence_wiki(
        candidate=_load_json(candidate_path, maximum=MAX_CANDIDATE_BYTES),
        gold=_load_json(gold_path, maximum=MAX_GOLD_BYTES),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score one Evidence Wiki development candidate")
    parser.add_argument("candidate")
    parser.add_argument("gold")
    parser.add_argument("--output")
    return parser


def _write_json(value: Mapping[str, Any], output: str | None) -> None:
    encoded = canonical_json(value)
    if output is None:
        print(encoded)
        return
    output_path = Path(output)
    if output_path.exists() or output_path.is_symlink():
        raise ValueError("score output already exists")
    output_path.write_text(encoded + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    _write_json(score_files(arguments.candidate, arguments.gold), arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
