"""Held-out query ablation evidence for DeepLaw v0.13.

This harness owns tiny synthetic KnowledgeVault fixtures and calls public
retrieval seams.  The headline corpus remains source-free and held out.  A
separate synthetic Source Revision/autonomous-knowledge fixture is used only
for the purpose-aware mechanism calibration; it never reads Gold, scorer,
benchmark expected relations, user sources, models, or the network.
"""

from __future__ import annotations

import argparse
import platform
import statistics
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

import deeplaw.retrieval_fabric as retrieval_fabric
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval.purpose import PurposeAwareRetrievalService
from deeplaw.util import (
    QUERY_EXPANSION_PROFILE_V2_METADATA,
    QUERY_EXPANSION_PROFILE_V2_SHA256,
    canonical_json,
    normalize_query_text,
    query_expansion_terms,
    search_terms,
    sha256_bytes,
    strict_json_loads,
)

SCHEMA_VERSION = "deeplaw.query-ablation-report/v1"
CORPUS_SCHEMA_VERSION = "deeplaw.query-ablation-corpus/v1"
CORPUS_FILENAME = "query-ablation-corpus-v1.json"
EXPECTED_CORPUS_SHA256 = "e2c8ef7a13f87b6c3aff92c1abfb31cfbb5d852e25c766f62b7a46953f9a65c7"
K = 3
MAX_CHARS = 2_000
MAX_TOKENS = 512
REPEATS = 1
_PURPOSE_CALIBRATION_FIXTURE_ID = "fixture.aurora.recovery"
_PURPOSE_CALIBRATION_UNEXPECTED_ID = "fixture.calibration.unexpected"
_PURPOSE_SOURCE_TEXT = (
    "# Aurora\n"
    "The checksum guard verifies Aurora sealed snapshot bytes before reopening.\n"
)
_PURPOSE_COMPILED_BODY = (
    "Aurora sealed snapshot reopening verifies the sealed snapshot before work resumes."
)
_PURPOSE_CALIBRATION_QUERIES: tuple[dict[str, Any], ...] = (
    {
        "query_id": "purpose-compiled-hit",
        "query": "Aurora sealed snapshot reopening verifies",
        "purpose": "answer",
        "policy": "compiled-first-v1",
        "expected_ids": [_PURPOSE_CALIBRATION_FIXTURE_ID],
    },
    {
        "query_id": "purpose-evidence-fallback",
        "query": "checksum guard bytes",
        "purpose": "answer",
        "policy": "compiled-first-v1",
        "expected_ids": [_PURPOSE_CALIBRATION_FIXTURE_ID],
    },
)

_VARIANT_CONFIGS: tuple[dict[str, Any], ...] = (
    {
        "variant_id": "expansion_on",
        "status": "executed",
        "execution_status": "executed",
        "mode": "auto",
        "expansion_mode": "default_v2",
        "description": "Public retrieval seam with the pinned default v2 query expansion profile.",
    },
    {
        "variant_id": "expansion_off",
        "status": "executed",
        "execution_status": "executed",
        "mode": "auto",
        "expansion_mode": "disabled_process_local_adapter",
        "description": "Public retrieval seam with only deterministic base search terms.",
    },
    {
        "variant_id": "lexical_only",
        "status": "executed",
        "execution_status": "executed",
        "mode": "lexical",
        "expansion_mode": "default_v2",
        "description": "Public lexical mode; its exact/key/phrase subchannels remain visible.",
    },
    {
        "variant_id": "dense_only",
        "status": "not_executed",
        "execution_status": "not_executed",
        "mode": "semantic",
        "expansion_mode": "not_applicable",
        "reason": (
            "semantic mode requires an explicitly verified local dense index; the offline "
            "harness provisions no index and invokes no model"
        ),
    },
    {
        "variant_id": "graph_only",
        "status": "not_executed",
        "execution_status": "not_executed",
        "mode": "graph",
        "expansion_mode": "default_v2",
        "reason": (
            "the source-free fixture has no admitted relation with exact evidence bindings; "
            "graph mode's observed lexical fallback is not counted as graph execution"
        ),
    },
    {
        "variant_id": "hybrid",
        "status": "executed",
        "execution_status": "degraded",
        "mode": "hybrid",
        "expansion_mode": "default_v2",
        "degraded_reasons": (
            "dense discovery was unavailable; the dense channel was not executed",
            "no eligible graph candidate was observed; only exact/lexical/temporal channels "
            "are reported",
        ),
        "description": "Public hybrid mode with dense and graph results explicitly absent.",
    },
    {
        "variant_id": "compiled_first",
        "status": "not_executed",
        "execution_status": "not_executed",
        "mode": "purpose_aware",
        "expansion_mode": "not_applicable",
        "reason": (
            "the held-out denominator is source-free by contract; purpose-aware compiled-first "
            "execution is recorded separately against a synthetic source-bound autonomous "
            "fixture"
        ),
    },
    {
        "variant_id": "targeted_evidence_fallback",
        "status": "not_executed",
        "execution_status": "not_executed",
        "mode": "purpose_aware",
        "expansion_mode": "not_applicable",
        "reason": (
            "the held-out denominator is source-free by contract; targeted evidence fallback "
            "execution is recorded separately against a synthetic Source Revision fixture"
        ),
    },
)

_FIXTURE_ASSETS: tuple[dict[str, str], ...] = (
    {
        "fixture_id": "fixture.aurora.boundary",
        "kind": "constraint",
        "title": "Aurora 快照封存边界",
        "statement": "Aurora 快照封存边界要求 owner-scoped local storage.",
    },
    {
        "fixture_id": "fixture.aurora.recovery",
        "kind": "procedure",
        "title": "Aurora sealed snapshot reopening",
        "statement": (
            "Aurora sealed snapshot reopening verifies the sealed snapshot before work resumes."
        ),
    },
    {
        "fixture_id": "fixture.meridian.audit",
        "kind": "fact",
        "title": "Meridian 审计收据 handoff",
        "statement": "Meridian 审计收据 records an append-only handoff.",
    },
    {
        "fixture_id": "fixture.nova.retry",
        "kind": "rule",
        "title": "Nova bounded retry token",
        "statement": "Nova bounded retry token appears after the third attempt.",
    },
    {
        "fixture_id": "fixture.meridian.link",
        "kind": "procedure",
        "title": "Meridian 收据 linked recovery routine",
        "statement": "Meridian 收据 links the audit receipt to the recovery routine.",
    },
    {
        "fixture_id": "fixture.harbor.distractor",
        "kind": "fact",
        "title": "Harbor 极光配色晨报",
        "statement": "Harbor 极光配色晨报 uses a color palette for morning reports.",
    },
)


def _hash_body(value: Mapping[str, Any], field: str) -> str:
    body = dict(value)
    body.pop(field, None)
    return sha256_bytes(canonical_json(body).encode("utf-8"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ValueError("query ablation JSON cannot be read") from error
    if not isinstance(value, dict):
        raise ValueError("query ablation JSON must be an object")
    return value


def verify_query_ablation_corpus(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("query ablation corpus must be an object")
    corpus = dict(value)
    required = {
        "schema_version",
        "corpus_id",
        "source_free",
        "query_count",
        "queries",
        "limits",
        "corpus_sha256",
    }
    if set(corpus) != required or corpus["schema_version"] != CORPUS_SCHEMA_VERSION:
        raise ValueError("query ablation corpus shape is not closed")
    if corpus["corpus_id"] != "v013-heldout-query-ablation-v1" or corpus["source_free"] is not True:
        raise ValueError("query ablation corpus identity or source-free flag is invalid")
    if (
        corpus["corpus_sha256"] != EXPECTED_CORPUS_SHA256
        or _hash_body(corpus, "corpus_sha256") != corpus["corpus_sha256"]
    ):
        raise ValueError("query ablation corpus digest mismatch")
    queries = corpus["queries"]
    if not isinstance(queries, list) or len(queries) != corpus["query_count"] or len(queries) != 8:
        raise ValueError("query ablation corpus query count mismatch")
    ids: set[str] = set()
    positives = 0
    negatives = 0
    for item in queries:
        if not isinstance(item, Mapping) or set(item) != {
            "query_id",
            "language",
            "query",
            "expected_ids",
            "negative",
        }:
            raise ValueError("query ablation corpus query shape is not closed")
        query_id = item["query_id"]
        if not isinstance(query_id, str) or not query_id or query_id in ids:
            raise ValueError("query ablation corpus query identity is invalid")
        ids.add(query_id)
        if item["language"] not in {"zh", "en", "cross"}:
            raise ValueError("query ablation corpus language is invalid")
        query = item["query"]
        if not isinstance(query, str) or not query.strip() or len(query) > 4_000:
            raise ValueError("query ablation corpus query text is invalid")
        expected_ids = item["expected_ids"]
        if (
            not isinstance(expected_ids, list)
            or len(set(expected_ids)) != len(expected_ids)
            or any(
                not isinstance(identifier, str) or not identifier.startswith("fixture.")
                for identifier in expected_ids
            )
        ):
            raise ValueError("query ablation expected IDs are invalid")
        if item["negative"] is not (len(expected_ids) == 0):
            raise ValueError("query ablation negative label does not match expected IDs")
        expansions = query_expansion_terms(query)
        if expansions:
            raise ValueError("held-out paraphrase unexpectedly appears in v2 expansion lexicon")
        normalized_query = normalize_query_text(query).casefold()
        fixture_texts = [
            normalize_query_text(text).casefold()
            for fixture in _FIXTURE_ASSETS
            for text in (fixture["title"], fixture["statement"])
        ]
        if any(normalized_query == text for text in fixture_texts):
            raise ValueError("held-out query is an exact fixture title or statement")
        if any(normalized_query and normalized_query in text for text in fixture_texts):
            raise ValueError("held-out query is a fixture-text substring, not a paraphrase")
        if expected_ids:
            positives += 1
        else:
            negatives += 1
    limits = corpus["limits"]
    if not isinstance(limits, Mapping) or set(limits) != {
        "k",
        "max_chars",
        "max_tokens",
        "repeats",
    }:
        raise ValueError("query ablation corpus limits are invalid")
    if dict(limits) != {
        "k": K,
        "max_chars": MAX_CHARS,
        "max_tokens": MAX_TOKENS,
        "repeats": REPEATS,
    }:
        raise ValueError("query ablation corpus limits drifted")
    return {
        "corpus_id": corpus["corpus_id"],
        "corpus_sha256": corpus["corpus_sha256"],
        "query_count": len(queries),
        "positive_query_count": positives,
        "negative_query_count": negatives,
    }


def _fixture_digest() -> str:
    return sha256_bytes(canonical_json(list(_FIXTURE_ASSETS)).encode("utf-8"))


@contextmanager
def _synthetic_vault() -> Iterator[tuple[Path, dict[str, str]]]:
    with tempfile.TemporaryDirectory(prefix="deeplaw-v013-ablation-") as temporary:
        root = Path(temporary) / "vault"
        initialize_knowledge_vault(root, name="v013 source-free ablation", scope="project")
        fixture_ids: dict[str, str] = {}
        with KnowledgeVault(root, read_only=False) as vault:
            for item in _FIXTURE_ASSETS:
                proposal = vault.propose_asset(
                    kind=item["kind"],
                    memory_tier="project",
                    title=item["title"],
                    statement=item["statement"],
                    semantic_key=item["fixture_id"],
                    sensitivity="private",
                )
                active = vault.approve_asset(
                    proposal.asset_id,
                    confirm_reviewed=True,
                    reviewer_id="v013-synthetic-harness",
                    review_reason="Synthetic source-free fixture activation.",
                )
                fixture_ids[item["fixture_id"]] = active.asset_id
        yield root, fixture_ids


@contextmanager
def _synthetic_purpose_fixture() -> Iterator[tuple[Path, dict[str, str]]]:
    """Build a bounded source/evidence plus source-bound autonomous fixture.

    This fixture is deliberately separate from the source-free held-out Vault so
    mechanism calibration cannot change the headline denominator.  IDs are
    retained only in-process for integrity checks and are never emitted into the
    report; stable semantic fixture IDs are used for the calibration metrics.
    """

    with tempfile.TemporaryDirectory(prefix="deeplaw-v013-purpose-") as temporary:
        root = Path(temporary) / "vault"
        initialize_knowledge_vault(root, name="v013 purpose calibration", scope="project")
        initialize_autonomous_core(root)
        source_path = Path(temporary) / "aurora.md"
        source_path.write_text(_PURPOSE_SOURCE_TEXT, encoding="utf-8")
        with KnowledgeVault(root, read_only=False) as vault:
            compiled = compile_source(
                vault,
                source_path,
                source_kind="document",
                sensitivity="private",
                confirm_no_case_data=True,
            )
        source = compiled.get("source")
        if not isinstance(source, Mapping):
            raise RuntimeError("purpose calibration source metadata is missing")
        source_id = source.get("source_id")
        source_revision_id = source.get("source_revision_id")
        if not isinstance(source_id, str) or not isinstance(source_revision_id, str):
            raise RuntimeError("purpose calibration source identity is missing")
        with KnowledgeVault(root, read_only=False) as vault:
            manifest = vault.source_review_manifest(source_id)
            vault.approve_source_assets(
                source_id,
                confirm_reviewed=True,
                confirm_quarantined=True,
                review_manifest_sha256=manifest["review_manifest_sha256"],
            )
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            grant = store.enable_grant(
                writer_id="v013-purpose-calibration",
                operations=tuple(sorted(SINK_OPERATIONS)),
            )
            remembered = store.remember(
                grant_id=grant["grant_id"],
                idempotency_key="v013-purpose-calibration-aurora",
                title="Aurora compiled snapshot",
                body=_PURPOSE_COMPILED_BODY,
                kind="claim",
                scope="project",
                sensitivity="private",
                source_refs=[{"source_id": source_id}],
                semantic_key=_PURPOSE_CALIBRATION_FIXTURE_ID,
                model_id="deterministic-synthetic",
                tool_id="v013-harness",
                confirm_no_case_data=True,
            )
        knowledge_id = remembered.get("knowledge_id")
        revision_id = remembered.get("revision_id")
        if not isinstance(knowledge_id, str) or not isinstance(revision_id, str):
            raise RuntimeError("purpose calibration autonomous revision identity is missing")
        yield root, {
            "source_id": source_id,
            "source_revision_id": source_revision_id,
            "knowledge_id": knowledge_id,
            "revision_id": revision_id,
        }


@contextmanager
def _expansion_disabled() -> Iterator[None]:
    original = retrieval_fabric.query_search_terms

    def base_terms(text: str, *, limit: int | None = None, cover_tail: bool = False) -> list[str]:
        return search_terms(text, limit=limit, cover_tail=cover_tail)

    retrieval_fabric.query_search_terms = base_terms
    try:
        yield
    finally:
        retrieval_fabric.query_search_terms = original


def _proxy_tokens(query: str, returned: list[dict[str, Any]]) -> int:
    serialized = query + "".join(str(item.get("excerpt", "")) for item in returned)
    return len(serialized.encode("utf-8"))


def _metrics_from_ids(
    *,
    query_id: str,
    expected_ids: list[str],
    returned_ids: list[str],
    elapsed_ms: float,
    token_proxy: int,
    observed_channels: list[str],
    k: int,
) -> dict[str, Any]:
    expected = set(expected_ids)
    returned = returned_ids[:k]
    returned_set = set(returned)
    true_positive = len(expected & returned_set)
    false_positive = len(returned_set - expected)
    recall = (
        1.0
        if not expected and not returned_set
        else true_positive / len(expected)
        if expected
        else 0.0
    )
    precision_denominator = min(k, len(returned_set))
    precision = (
        true_positive / precision_denominator
        if precision_denominator
        else 1.0
        if not expected
        else 0.0
    )
    false_positive_rate = false_positive / len(returned_set) if returned_set else 0.0
    return {
        "query_id": query_id,
        "expected_ids": sorted(expected),
        "returned_ids": returned,
        "true_positive_count": true_positive,
        "false_positive_count": false_positive,
        "recall_at_k": round(recall, 6),
        "precision_at_k": round(precision, 6),
        "false_positive_rate": round(false_positive_rate, 6),
        "latency_ms": round(elapsed_ms, 6),
        "token_proxy": token_proxy,
        "observed_channels": sorted(set(observed_channels)),
    }


def _query_metrics(
    query: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    elapsed_ms: float,
    k: int,
) -> dict[str, Any]:
    returned = [
        str(item.get("knowledge_key"))
        for item in result.get("results", [])[:k]
        if item.get("knowledge_key") is not None
    ]
    return _metrics_from_ids(
        query_id=str(query["query_id"]),
        expected_ids=[str(identifier) for identifier in query["expected_ids"]],
        returned_ids=returned,
        elapsed_ms=elapsed_ms,
        token_proxy=_proxy_tokens(query["query"], result.get("results", [])),
        observed_channels=[
            str(channel.get("channel"))
            for item in result.get("results", [])
            for channel in item.get("channels", [])
            if isinstance(channel, Mapping) and channel.get("channel")
        ],
        k=k,
    )


def _aggregate(per_query: list[dict[str, Any]], *, variant_id: str) -> dict[str, Any]:
    if not per_query:
        return {
            "variant_id": variant_id,
            "recall_at_k": None,
            "precision_at_k": None,
            "false_positive_rate": None,
            "latency_ms": {"mean": None, "p50": None, "p95": None, "sample_count": 0},
            "throughput_qps": {"mean": None, "method": "not_executed"},
            "token_proxy": {"total": 0, "mean": None, "method": "not_executed"},
        }

    def percentile(values: list[float], fraction: float) -> float:
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
        return ordered[index]

    latencies = [float(item["latency_ms"]) for item in per_query]
    tokens = [int(item["token_proxy"]) for item in per_query]
    return {
        "variant_id": variant_id,
        "recall_at_k": round(statistics.fmean(item["recall_at_k"] for item in per_query), 6),
        "precision_at_k": round(statistics.fmean(item["precision_at_k"] for item in per_query), 6),
        "false_positive_rate": round(
            statistics.fmean(item["false_positive_rate"] for item in per_query), 6
        ),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 6),
            "p50": round(percentile(latencies, 0.50), 6),
            "p95": round(percentile(latencies, 0.95), 6),
            "sample_count": len(latencies),
            "scope": "wall-clock public retrieve call, one invocation per query",
        },
        "throughput_qps": {
            "mean": round(1000 / statistics.fmean(latencies), 6)
            if statistics.fmean(latencies) > 0
            else None,
            "method": "query count divided by aggregate public retrieve wall-clock; one run",
        },
        "token_proxy": {
            "total": sum(tokens),
            "mean": round(statistics.fmean(tokens), 6),
            "method": "UTF-8 byte length of query plus selected excerpts; no model tokens",
        },
    }


def _purpose_returned_ids(
    result: Mapping[str, Any], *, fixture: Mapping[str, str]
) -> list[str]:
    """Map calibration cards to stable IDs through exact governed identities."""

    fixture_id = _PURPOSE_CALIBRATION_FIXTURE_ID
    source_revision_id = fixture.get("source_revision_id")
    returned: list[str] = []
    for item in result.get("compiled", []):
        if not isinstance(item, Mapping):
            continue
        returned.append(
            fixture_id
            if item.get("semantic_key") == fixture_id
            else _PURPOSE_CALIBRATION_UNEXPECTED_ID
        )
    for item in result.get("evidence", []):
        if not isinstance(item, Mapping):
            continue
        references = item.get("source_refs", [])
        bound = isinstance(source_revision_id, str) and any(
            isinstance(reference, Mapping)
            and reference.get("source_revision_id") == source_revision_id
            for reference in references
        )
        returned.append(
            fixture_id if bound else _PURPOSE_CALIBRATION_UNEXPECTED_ID
        )
    return returned


def _run_purpose_calibration(
    root: Path,
    *,
    fixture: Mapping[str, str],
    variant_id: str,
) -> dict[str, Any]:
    service = PurposeAwareRetrievalService(root)
    per_query: list[dict[str, Any]] = []
    observed_channels: set[str] = set()
    query_cases = (
        _PURPOSE_CALIBRATION_QUERIES[:1]
        if variant_id == "compiled_first"
        else _PURPOSE_CALIBRATION_QUERIES[1:]
    )
    for query in query_cases:
        started = time.perf_counter_ns()
        result = service.query(
            str(query["query"]),
            purpose=str(query["purpose"]),  # type: ignore[arg-type]
            policy=str(query["policy"]),  # type: ignore[arg-type]
            scope="project",
            max_sensitivity="private",
            limit=K,
            max_chars=MAX_CHARS,
            max_tokens=MAX_TOKENS,
            query_plan_version="5",
        )
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        plan = result.get("query_plan", {})
        channels = [
            str(channel)
            for channel in plan.get("used_channels", [])
            if isinstance(channel, str)
        ]
        returned_ids = _purpose_returned_ids(
            result,
            fixture=fixture,
        )
        selected_text = [
            str(item.get("content", item.get("excerpt", "")))
            for item in [*result.get("compiled", []), *result.get("evidence", [])]
            if isinstance(item, Mapping)
        ]
        per_query.append(
            _metrics_from_ids(
                query_id=str(query["query_id"]),
                expected_ids=[str(identifier) for identifier in query["expected_ids"]],
                returned_ids=returned_ids,
                elapsed_ms=elapsed_ms,
                token_proxy=len(
                    (str(query["query"]) + "".join(selected_text)).encode("utf-8")
                ),
                observed_channels=channels,
                k=K,
            )
        )
        observed_channels.update(channels)
        if variant_id == "compiled_first":
            if channels != ["compiled_knowledge"] or not result.get("compiled"):
                raise RuntimeError("compiled-first calibration did not stay compiled-only")
            if result.get("evidence"):
                raise RuntimeError("compiled-first calibration unexpectedly returned evidence")
        else:
            fallback = plan.get("fallback", {})
            source_revision_id = fixture.get("source_revision_id")
            fallback_source_ids = (
                fallback.get("source_revision_ids", [])
                if isinstance(fallback, Mapping)
                else []
            )
            if (
                not isinstance(fallback, Mapping)
                or fallback.get("used") is not True
                or not result.get("evidence")
                or result.get("compiled")
                or not isinstance(source_revision_id, str)
                or source_revision_id not in fallback_source_ids
            ):
                raise RuntimeError("evidence fallback calibration was not source-bound")
    if variant_id == "compiled_first":
        expected_channels = {"compiled_knowledge"}
    else:
        expected_channels = {"source_evidence", "raw_fragment_fallback"}
    if not expected_channels.intersection(observed_channels):
        raise RuntimeError(
            f"purpose calibration for {variant_id} did not observe the expected channel"
        )
    return {
        "fixture": "synthetic_source_bound_autonomous_v1",
        "fixture_sha256": sha256_bytes(
            canonical_json(
                {
                    "source_text": _PURPOSE_SOURCE_TEXT,
                    "compiled_body": _PURPOSE_COMPILED_BODY,
                    "semantic_key": _PURPOSE_CALIBRATION_FIXTURE_ID,
                }
            ).encode("utf-8")
        ),
        "execution_status": "executed",
        "query_count": len(per_query),
        "per_query": per_query,
        "metrics": _aggregate(per_query, variant_id=f"{variant_id}:calibration"),
        "observed_channels": sorted(observed_channels),
        "source_revision_bound": bool(fixture.get("source_revision_id")),
    }


def _run_variant(
    config: Mapping[str, Any],
    queries: list[dict[str, Any]],
    vault: KnowledgeVault,
    *,
    purpose_calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    variant_id = str(config["variant_id"])
    common = {
        "variant_id": variant_id,
        "status": config["status"],
        "mode": config["mode"],
        "expansion_mode": config["expansion_mode"],
        "config_digest": sha256_bytes(canonical_json(dict(config)).encode("utf-8")),
        "execution_status": config["execution_status"],
        "degraded_reasons": list(config.get("degraded_reasons", ())),
    }
    if config["status"] != "executed":
        return {
            **common,
            "not_executed_reason": config["reason"],
            "per_query": [],
            "metrics": _aggregate([], variant_id=variant_id),
            "observed_channels": [],
            "calibration": purpose_calibration,
        }
    per_query: list[dict[str, Any]] = []
    observed_channels: set[str] = set()
    context = (
        _expansion_disabled()
        if config["expansion_mode"] == "disabled_process_local_adapter"
        else nullcontext()
    )
    with context:
        for query in queries:
            started = time.perf_counter_ns()
            result = retrieval_fabric.retrieve(
                vault,
                query["query"],
                mode=config["mode"],
                limit=K,
                max_chars=MAX_CHARS,
            )
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            metrics = _query_metrics(query, result, elapsed_ms=elapsed_ms, k=K)
            per_query.append(metrics)
            observed_channels.update(metrics["observed_channels"])
    return {
        **common,
        "not_executed_reason": None,
        "per_query": per_query,
        "metrics": _aggregate(per_query, variant_id=variant_id),
        "observed_channels": sorted(observed_channels),
        "calibration": purpose_calibration,
    }


def build_query_ablation_report(corpus: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if corpus is None:
        corpus = _load_json(Path(__file__).with_name(CORPUS_FILENAME))
    corpus_receipt = verify_query_ablation_corpus(corpus)
    queries = [dict(item) for item in corpus["queries"]]
    with _synthetic_vault() as (root, _fixture_ids), KnowledgeVault(root, read_only=True) as vault:
        variants = [_run_variant(config, queries, vault) for config in _VARIANT_CONFIGS]
    with _synthetic_purpose_fixture() as (purpose_root, purpose_fixture):
        purpose_variants = {
            variant_id: _run_purpose_calibration(
                purpose_root,
                fixture=purpose_fixture,
                variant_id=variant_id,
            )
            for variant_id in ("compiled_first", "targeted_evidence_fallback")
        }
    for variant in variants:
        calibration = purpose_variants.get(variant["variant_id"])
        if calibration is not None:
            variant["calibration"] = calibration
    profile = QUERY_EXPANSION_PROFILE_V2_METADATA
    body: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "corpus": {
            **corpus_receipt,
            "filename": CORPUS_FILENAME,
            "source_free": True,
            "fixture_sha256": _fixture_digest(),
        },
        "denominator": {
            "query_count": corpus_receipt["query_count"],
            "positive_query_count": corpus_receipt["positive_query_count"],
            "negative_query_count": corpus_receipt["negative_query_count"],
            "k": K,
            "expected_id_unit": "synthetic fixture semantic_key",
            "recall_denominator": (
                "expected fixture IDs; empty expected set scores 1 only when no result is returned"
            ),
            "precision_denominator": (
                "min(K, unique returned fixture IDs); empty negative result scores 1"
            ),
            "false_positive_denominator": "unique returned fixture IDs; empty result scores 0",
        },
        "budget": {
            "max_chars": MAX_CHARS,
            "max_tokens": MAX_TOKENS,
            "repeats": REPEATS,
            "token_measurement_method": "UTF-8 byte proxy; no model invocation",
        },
        "profile_binding": {
            "query_expansion_profile": profile["profile_id"],
            "query_expansion_profile_sha256": profile["profile_sha256"],
            "query_expansion_lexicon_sha256": profile["lexicon_sha256"],
            "retrieval_implementation_revision": "retrieval-fabric/3",
            "purpose_aware_retrieval_revision": "purpose-aware-retrieval/v5",
            "tokenizer_profile": "deeplaw-mixed-cjk-code/2",
            "dense_model_used": False,
            "network_used": False,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "machine": platform.machine(),
            "source_free_synthetic_vault": True,
            "purpose_calibration_source_fixture": True,
            "network": "not_used_by_code_path",
            "model": "not_invoked",
        },
        "variants": variants,
        "competitive_claim_eligible": False,
        "limitations": [
            "This is source-free synthetic fixture evidence, not a semantic Gold evaluation.",
            (
                "The frozen held-out corpus contains no v2 expansion aliases; expansion_on "
                "versus expansion_off headline metric deltas are zero."
            ),
            (
                "Dense and graph-only modes are explicitly not executed when their required "
                "channels are unavailable; hybrid is executed in a degraded lexical/temporal "
                "configuration with no dense or graph results."
            ),
            (
                "Compiled-first and targeted evidence fallback are calibrated separately on a "
                "synthetic Source Revision plus source-bound autonomous claim; those results "
                "are excluded from the source-free held-out denominator."
            ),
            "Latency is one-run wall-clock observation and token_proxy is not model tokenization.",
            "Throughput is one-run aggregate queries per second; it is not a capacity claim.",
        ],
    }
    if (
        body["profile_binding"]["query_expansion_profile_sha256"]
        != QUERY_EXPANSION_PROFILE_V2_SHA256
    ):
        raise RuntimeError("query expansion profile digest drifted")
    body["report_sha256"] = _hash_body(body, "report_sha256")
    return body


def verify_query_ablation_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "schema_version": "deeplaw.query-ablation-verification/v1",
            "valid": False,
            "reason": "not_object",
        }
    report = dict(value)
    try:
        required = {
            "schema_version",
            "corpus",
            "denominator",
            "budget",
            "profile_binding",
            "environment",
            "variants",
            "competitive_claim_eligible",
            "limitations",
            "report_sha256",
        }
        if set(report) != required or report["schema_version"] != SCHEMA_VERSION:
            raise ValueError("closed report shape mismatch")
        if report["competitive_claim_eligible"] is not False:
            raise ValueError("competitive claim flag must remain false")
        if report["report_sha256"] != _hash_body(report, "report_sha256"):
            raise ValueError("report digest mismatch")
        corpus = report["corpus"]
        if not isinstance(corpus, Mapping) or corpus.get("corpus_sha256") != EXPECTED_CORPUS_SHA256:
            raise ValueError("report corpus binding mismatch")
        profile_binding = report["profile_binding"]
        if not isinstance(profile_binding, Mapping):
            raise ValueError("report profile binding is invalid")
        if profile_binding.get("purpose_aware_retrieval_revision") != "purpose-aware-retrieval/v5":
            raise ValueError("report purpose-aware retrieval binding is invalid")
        variants = report["variants"]
        if not isinstance(variants, list) or len(variants) != len(_VARIANT_CONFIGS):
            raise ValueError("report variant count mismatch")
        expected_ids = {config["variant_id"] for config in _VARIANT_CONFIGS}
        observed_ids = {item.get("variant_id") for item in variants if isinstance(item, Mapping)}
        if observed_ids != expected_ids:
            raise ValueError("report variant identity mismatch")
        for item in variants:
            if not isinstance(item, Mapping):
                raise ValueError("report variant is not an object")
            if item.get("status") not in {"executed", "not_executed"}:
                raise ValueError("report variant status is invalid")
            if item.get("execution_status") not in {"executed", "degraded", "not_executed"}:
                raise ValueError("report variant execution status is invalid")
            degraded_reasons = item.get("degraded_reasons")
            if not isinstance(degraded_reasons, list) or any(
                not isinstance(reason, str) or not reason for reason in degraded_reasons
            ):
                raise ValueError("report variant degraded reasons are invalid")
            if item["status"] == "not_executed" and not isinstance(
                item.get("not_executed_reason"), str
            ):
                raise ValueError("not-executed variant must state a reason")
            if item["status"] == "executed" and item.get("not_executed_reason") is not None:
                raise ValueError("executed variant cannot claim a not-executed reason")
            if item["status"] == "not_executed" and item["execution_status"] != "not_executed":
                raise ValueError("not-executed variant execution status is inconsistent")
            if item["execution_status"] == "degraded" and not degraded_reasons:
                raise ValueError("degraded variant must explain its missing channels")
            calibration = item.get("calibration")
            if item.get("variant_id") in {"compiled_first", "targeted_evidence_fallback"}:
                if (
                    not isinstance(calibration, Mapping)
                    or calibration.get("execution_status") != "executed"
                ):
                    raise ValueError("purpose-aware variant must include executed calibration")
            elif calibration is not None:
                raise ValueError("non-purpose variant cannot include calibration")
        return {
            "schema_version": "deeplaw.query-ablation-verification/v1",
            "valid": True,
            "reason": "verified",
            "report_sha256": report["report_sha256"],
            "corpus_sha256": corpus["corpus_sha256"],
            "variant_count": len(variants),
        }
    except (KeyError, TypeError, ValueError):
        return {
            "schema_version": "deeplaw.query-ablation-verification/v1",
            "valid": False,
            "reason": "verification_failed",
            "report_sha256": report.get("report_sha256")
            if isinstance(report.get("report_sha256"), str)
            else None,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="write the closed current report JSON to this path",
    )
    parser.add_argument("--corpus", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="re-open the written report and fail if its integrity receipt is invalid",
    )
    args = parser.parse_args(argv)
    corpus = _load_json(args.corpus) if args.corpus is not None else None
    report = build_query_ablation_report(corpus)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(report) + "\n", encoding="utf-8")
    verified = verify_query_ablation_report(
        _load_json(args.output) if args.verify else report
    )
    print(canonical_json(verified))
    return 0 if verified["valid"] else 1


if __name__ == "__main__":  # pragma: no cover - exercised by the validation command
    raise SystemExit(main())
