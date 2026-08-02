from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path
from statistics import median

import pytest

import benchmarks.semantic.run_query_suite as semantic_query_suite
from benchmarks.hosts.run_semantic_host_harness import (
    _provider_token_usage,
    not_executed_report,
)
from benchmarks.semantic.build_machine_review_consensus import (
    AUDITOR_ROLES,
    build_consensus,
    build_owner_packet,
    candidate_binding,
    case_evidence_sha256,
    challenge_evidence_sha256,
    packet_evidence_sha256,
    validate_packet,
)
from benchmarks.semantic.compare_query_replicates import (
    _bootstrap_metric,
    _median_metric,
)
from benchmarks.semantic.compare_query_runs import HIGHER_IS_BETTER, _metric
from benchmarks.semantic.deterministic_gold_agent import compile_source as compile_gold_source
from benchmarks.semantic.export_review_bundle import export_review_bundle
from benchmarks.semantic.prepare_host_corpus import _run_cli
from benchmarks.semantic.review_gold import (
    CANONICAL_JSON_PROFILE,
    COMMITMENT_PROFILES,
    QUERY_SET_PROJECTION,
    confirm_candidate,
    query_set_projection,
    query_set_sha256,
    validate_candidate,
    validate_freeze,
)
from benchmarks.semantic.run_query_suite import (
    _case_result,
    _claim_evidence_checks,
    _compiled_hit_ratio,
    _evaluate_read_challenge,
    _execution_environment,
    _rank_metrics,
    _relation_checks,
    _retrieval_coverage_source_keys,
    _retrieval_sequence_check,
    _runtime_python,
    _source_ir_coverage_counts,
)
from benchmarks.semantic.score_semantic_run import (
    _content_assertion_valid,
    _cross_packet_identity_consistency,
    _query_cost,
    _same_applicability_conflict,
    score,
)
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_intelligence import normalize_identity_text
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.retrieval.purpose import (
    PurposeAwareRetrievalService,
    _is_comparison_query,
    _matches_structured_query_anchor,
)
from deeplaw.util import canonical_json, sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[1]
CANDIDATE = REPOSITORY / "benchmarks/semantic/semantic-gold-candidate-v1.json"


def _candidate() -> dict:
    return json.loads(CANDIDATE.read_text(encoding="utf-8"))


def test_semantic_gold_candidate_requires_unanimous_machine_review() -> None:
    value = _candidate()
    digest = validate_candidate(value, repository=REPOSITORY)
    assert value["status"] == "machine_review_pending"
    assert value["review"] is None
    assert value["release_review_policy"]["human_gold_review"] == {
        "status": "not_required",
        "reason": "owner-approved deterministic machine-consensus release scope",
    }
    assert value["release_review_policy"]["maintainer_confirmed"] is False
    assert value["release_review_policy"]["reviewer_id"] is None
    assert len(value["sources"]) == 12
    assert len(value["cases"]) == 15
    assert len({case["task_type"] for case in value["cases"]}) == 15
    assert len(digest) == 64


def test_semantic_gold_freeze_binds_candidate_schema_queries_and_policy() -> None:
    freeze = json.loads(
        (REPOSITORY / "benchmarks/semantic/semantic-gold-freeze-v1.json").read_text(
            encoding="utf-8"
        )
    )
    validate_freeze(freeze, candidate=_candidate(), repository=REPOSITORY)
    assert freeze["canonical_json_profile"] == CANONICAL_JSON_PROFILE
    assert freeze["query_set_projection"] == QUERY_SET_PROJECTION
    assert freeze["commitment_profiles"] == COMMITMENT_PROFILES
    assert freeze["query_set_sha256"] == query_set_sha256(_candidate())
    candidate = _candidate()
    assert freeze["candidate_sha256"] == validate_candidate(
        candidate, repository=REPOSITORY
    )
    assert freeze["fixture_manifest_sha256"] == hashlib.sha256(
        "".join(source["bytes_sha256"] for source in candidate["sources"]).encode(
            "ascii"
        )
    ).hexdigest()
    assert freeze["semantic_gold_schema_sha256"] == hashlib.sha256(
        (REPOSITORY / "contracts/semantic-gold.v1.schema.json").read_bytes()
    ).hexdigest()
    assert freeze["scoring_policy_sha256"] == hashlib.sha256(
        canonical_json(candidate["scoring_policy"]).encode("utf-8")
    ).hexdigest()
    assert freeze["security_challenges_sha256"] == hashlib.sha256(
        canonical_json(candidate["security_challenges"]).encode("utf-8")
    ).hexdigest()
    assert [set(item) for item in query_set_projection(_candidate())] == [
        {"case_id", "query", "purpose", "phase", "as_of"}
    ] * 15
    freeze["query_set_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="does not bind"):
        validate_freeze(freeze, candidate=_candidate(), repository=REPOSITORY)


def test_retrieval_source_coverage_excludes_prohibited_predecessors() -> None:
    cases = {case["task_type"]: case for case in _candidate()["cases"]}

    assert _retrieval_coverage_source_keys(cases["source_successor_update"]) == (
        "update-v2",
    )
    assert _retrieval_coverage_source_keys(cases["overview_refresh"]) == (
        "update-v2",
    )
    assert _retrieval_coverage_source_keys(cases["source_withdrawal"]) == ()
    assert _retrieval_coverage_source_keys(cases["source_conflict"]) == (
        "retention-a",
        "retention-b",
    )


def test_cross_packet_fixture_produces_two_packets_and_one_stable_entity(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    prefix = [sys.executable, "-m", "deeplaw"]
    _run_cli(prefix, "init", "--vault", str(vault), "--name", "cross packet", "--scope", "personal")
    source = _run_cli(
        prefix,
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(REPOSITORY / "benchmarks/semantic/fixtures/01-cross-packet-entity.md"),
        "--source-kind",
        "document",
        "--title",
        "cross-packet-entity",
        "--trust",
        "user_provided",
        "--sensitivity",
        "public",
        "--confirm-no-case-data",
    )["source"]
    manifest = _run_cli(
        prefix,
        "review",
        "manifest",
        "--vault",
        str(vault),
        "--source-id",
        source["source_id"],
    )
    _run_cli(
        prefix,
        "review",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        source["source_id"],
        "--review-manifest-sha256",
        manifest["review_manifest_sha256"],
        "--reviewer-id",
        "semantic-test-maintainer",
        "--reason",
        "Approve the frozen public cross-packet fixture.",
        "--confirm-reviewed",
    )
    grant = _run_cli(
        prefix,
        "sink",
        "enable",
        "--vault",
        str(vault),
        "--writer-id",
        "semantic-cross-packet-test",
        "--profile",
        "semantic-compiler",
        "--scope",
        "personal",
        "--max-sensitivity",
        "public",
    )
    result = compile_gold_source(
        vault=vault,
        grant_id=grant["grant_id"],
        source_key="cross-packet-entity",
        source_revision_id=source["source_revision_id"],
        prior_runs={},
        packet_max_fragments=32,
    )
    expected = next(
        case for case in _candidate()["cases"] if case["case_id"] == "semantic-case-01"
    )["expected_objects"][0]
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        row = store.connection.execute(
            """
            SELECT objects.knowledge_id, revisions.semantic_key
            FROM knowledge_objects_v3 AS objects
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = objects.current_revision_id
            WHERE objects.kind = 'entity' AND revisions.semantic_key = ?
            """,
            ("entity:meridian-research-cooperative",),
        ).fetchone()
        assert row is not None
        state = _cross_packet_identity_consistency(
            store,
            compilation_run_id=result["compilation_run_id"],
            expected=expected,
            final_knowledge_ids={row["knowledge_id"]},
            final_semantic_keys={row["semantic_key"]},
        )
        tampered = _cross_packet_identity_consistency(
            store,
            compilation_run_id=result["compilation_run_id"],
            expected=expected,
            final_knowledge_ids={row["knowledge_id"], "knowledge_" + "f" * 24},
            final_semantic_keys={row["semantic_key"]},
        )
    assert result["packet_count"] >= 2
    assert len(set(result["packet_ids"])) >= 2
    assert state["valid"] is True
    assert len(state["final_knowledge_ids"]) == 1
    assert tampered["valid"] is False


def test_cross_source_synthesis_binds_every_input_source_in_its_receipt(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    prefix = [sys.executable, "-m", "deeplaw"]
    _run_cli(
        prefix,
        "init",
        "--vault",
        str(vault),
        "--name",
        "cross source synthesis",
        "--scope",
        "personal",
    )
    sources = {}
    for source_key, filename in (
        ("retention-a", "04-retention-policy-a.md"),
        ("retention-b", "05-retention-policy-b.md"),
    ):
        source = _run_cli(
            prefix,
            "source",
            "add",
            "--vault",
            str(vault),
            "--source",
            str(REPOSITORY / "benchmarks/semantic/fixtures" / filename),
            "--source-kind",
            "document",
            "--title",
            source_key,
            "--trust",
            "user_provided",
            "--sensitivity",
            "public",
            "--confirm-no-case-data",
        )["source"]
        manifest = _run_cli(
            prefix,
            "review",
            "manifest",
            "--vault",
            str(vault),
            "--source-id",
            source["source_id"],
        )
        _run_cli(
            prefix,
            "review",
            "approve-source",
            "--vault",
            str(vault),
            "--source-id",
            source["source_id"],
            "--review-manifest-sha256",
            manifest["review_manifest_sha256"],
            "--reviewer-id",
            "semantic-test-maintainer",
            "--reason",
            "Approve the frozen public retention fixture.",
            "--confirm-reviewed",
        )
        sources[source_key] = source
    grant = _run_cli(
        prefix,
        "sink",
        "enable",
        "--vault",
        str(vault),
        "--writer-id",
        "semantic-cross-source-test",
        "--profile",
        "semantic-compiler",
        "--scope",
        "personal",
        "--max-sensitivity",
        "public",
    )
    first_run = compile_gold_source(
        vault=vault,
        grant_id=grant["grant_id"],
        source_key="retention-a",
        source_revision_id=sources["retention-a"]["source_revision_id"],
        prior_runs={},
    )
    compile_gold_source(
        vault=vault,
        grant_id=grant["grant_id"],
        source_key="retention-b",
        source_revision_id=sources["retention-b"]["source_revision_id"],
        prior_runs={
            "retention-a": {
                "source_revision_id": sources["retention-a"]["source_revision_id"],
                "compilation_run_id": first_run["compilation_run_id"],
            }
        },
    )

    result = PurposeAwareRetrievalService(vault).query(
        "Retention policy comparison",
        purpose="answer",
        limit=1,
        query_plan_version="5",
    )

    synthesis = result["compiled"][0]
    expected_sources = {
        sources["retention-a"]["source_revision_id"],
        sources["retention-b"]["source_revision_id"],
    }
    assert synthesis["semantic_key"] == (
        "synthesis:atlas-retention-policy-comparison:2026"
    )
    assert {item["source_revision_id"] for item in synthesis["source_refs"]} == (
        expected_sources
    )
    assert synthesis["synthesis_evidence_receipt"]["complete"] is True
    assert {
        item["source_revision_id"]
        for item in synthesis["synthesis_evidence_receipt"]["source_refs"]
    } == expected_sources


def test_target_scoped_precision_excludes_valid_unlabelled_objects() -> None:
    case = next(
        case for case in _candidate()["cases"] if case["case_id"] == "semantic-case-04"
    )
    target_source = "sourcerev_" + "c" * 24
    extra_source = "sourcerev_" + "d" * 24
    value = {
        "compiled": [
            {
                "knowledge_id": "knowledge_" + "a" * 24,
                "kind": "concept",
                "title": "Evidence admission",
                "semantic_key": "concept:evidence-admission",
                "content": (
                    "Evidence admission accepts a source only after identity, lifecycle, "
                    "scope, sensitivity, and provenance checks succeed. Ranking never "
                    "establishes Authority. Verify the exact Source Revision bytes and every "
                    "fragment locator and quote hash. The admission policy was drafted, "
                    "locator validation became mandatory, and silent fallback was prohibited."
                ),
                "aliases": ["admission policy"],
                "source_refs": [{"source_revision_id": target_source}],
            },
            {
                "knowledge_id": "knowledge_" + "b" * 24,
                "kind": "concept",
                "title": "A valid extra concept",
                "semantic_key": "concept:valid-extra",
                "aliases": [],
                "source_refs": [{"source_revision_id": extra_source}],
            },
        ]
    }
    metrics = _rank_metrics(
        case=case,
        value=value,
        source_ids={"concept-procedure-events": target_source},
    )
    assert metrics["recall_at_k"] == 1.0
    assert metrics["target_scoped_precision_at_k"] == 1.0


def test_target_scoped_precision_excludes_other_generic_source_summaries() -> None:
    case = next(
        case for case in _candidate()["cases"] if case["case_id"] == "semantic-case-06"
    )
    target_source = "sourcerev_" + "e" * 24
    other_source = "sourcerev_" + "f" * 24
    target_content = (
        "Evidence admission requires identity, lifecycle, scope, sensitivity, and provenance "
        "checks. Evidence ranking never establishes Authority."
    )
    value = {
        "compiled": [
            {
                "knowledge_id": "knowledge_" + "a" * 24,
                "kind": "synthesis",
                "title": "Source summary",
                "semantic_key": f"source-summary:{target_source}",
                "content": target_content,
                "source_refs": [{"source_revision_id": target_source}],
            },
            {
                "knowledge_id": "knowledge_" + "b" * 24,
                "kind": "synthesis",
                "title": "Source summary",
                "semantic_key": f"source-summary:{other_source}",
                "content": "A valid summary for another source.",
                "source_refs": [{"source_revision_id": other_source}],
            },
        ]
    }
    metrics = _rank_metrics(
        case=case,
        value=value,
        source_ids={"concept-procedure-events": target_source},
    )
    assert metrics["matched_label_ids"] == ["label-source-summary"]
    assert metrics["target_scoped_precision_at_k"] == 1.0


def test_compiled_hit_ratio_excludes_explicit_gap_only_cases() -> None:
    cases = [
        {"matched_label_ids": ["label-compiled"]},
        {"matched_label_ids": []},
        {"matched_label_ids": []},
    ]
    gold_cases = [
        {"expected_objects": [{"required": True}]},
        {"expected_objects": []},
        {"expected_objects": []},
    ]
    assert _compiled_hit_ratio(cases, gold_cases) == 1.0


def test_source_ir_fragment_coverage_uses_ledger_batch_counts() -> None:
    rows = [
        {
            "compilation_run_id": "run-a",
            "covered_fragment_ids_json": '["fragment-a","fragment-b"]',
            "omitted_fragments_json": '[{"fragment_id":"fragment-c","reason":"risk"}]',
        },
        {
            "compilation_run_id": "run-b",
            "covered_fragment_ids_json": '["fragment-d"]',
            "omitted_fragments_json": "[]",
        },
    ]

    counts = _source_ir_coverage_counts(rows, expected_run_ids={"run-a", "run-b"})

    assert counts == {
        "covered_fragment_count": 3,
        "omitted_fragment_count": 1,
        "total_fragment_count": 4,
        "ratio": 0.75,
    }
    assert "source_ir_fragment_coverage" in HIGHER_IS_BETTER


def test_source_ir_fragment_coverage_rejects_missing_compiler_run() -> None:
    rows = [
        {
            "compilation_run_id": "run-a",
            "covered_fragment_ids_json": '["fragment-a"]',
            "omitted_fragments_json": "[]",
        }
    ]

    with pytest.raises(ValueError, match="does not bind every compiler run"):
        _source_ir_coverage_counts(rows, expected_run_ids={"run-a", "run-b"})


def test_gold_distinguishes_source_ir_and_retrieval_coverage() -> None:
    policy = _candidate()["scoring_policy"]

    assert policy["source_coverage_metric"] == "source_ir_fragment_coverage"
    assert "Source IR fragments" in policy["source_coverage_definition"]
    assert "Source Revisions selected" in policy[
        "retrieval_source_coverage_definition"
    ]


def test_same_condition_comparison_has_zero_tolerance() -> None:
    slower = _metric(
        name="warm_latency_p95_ms", baseline=100, candidate=101, direction="lower"
    )
    lower_quality = _metric(
        name="recall_at_k", baseline=1.0, candidate=0.999, direction="higher"
    )

    assert slower["non_regression"] is False
    assert lower_quality["non_regression"] is False


def test_replicate_deterministic_metrics_have_zero_tolerance() -> None:
    slower = _median_metric(
        name="provider_bytes_per_matched_target",
        baseline=[100, 100, 100],
        candidate=[101, 101, 101],
        direction="lower",
    )
    lower_quality = _median_metric(
        name="recall_at_k",
        baseline=[1.0, 1.0, 1.0],
        candidate=[0.999, 0.999, 0.999],
        direction="higher",
    )

    assert slower["non_regression"] is False
    assert lower_quality["non_regression"] is False


def test_paired_bootstrap_detects_only_interval_bound_regression() -> None:
    identical = _bootstrap_metric(
        name="warm_latency_p50_ms",
        baseline=[100] * 15,
        candidate=[100] * 15,
        statistic=lambda values: float(median(values)),
        seed_material="identical",
    )
    slower = _bootstrap_metric(
        name="warm_latency_p50_ms",
        baseline=[100] * 15,
        candidate=[101] * 15,
        statistic=lambda values: float(median(values)),
        seed_material="slower",
    )

    assert identical["bootstrap_confidence_interval"]["lower_delta"] == 0
    assert identical["regression_detected"] is False
    assert slower["bootstrap_confidence_interval"]["lower_delta"] == 1
    assert slower["regression_detected"] is True


def test_query_environment_is_probed_from_first_party_runtime() -> None:
    packages = sorted(
        {
            (
                str(distribution.metadata.get("Name") or "").casefold(),
                distribution.version,
            )
            for distribution in importlib.metadata.distributions()
            if distribution.metadata.get("Name")
        }
    )
    expected_inventory_sha256 = sha256_bytes(
        json.dumps(
            packages,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    environment = _execution_environment(
        prefix=[sys.executable, "-m", "deeplaw"],
        network_policy="offline",
    )

    assert environment["python"]["version"] == platform.python_version()
    assert environment["dependency_inventory_sha256"] == expected_inventory_sha256
    assert environment["network_policy"] == "offline"


def test_runtime_probe_uses_sibling_venv_python_without_canonicalizing(
    tmp_path: Path,
) -> None:
    executable_directory = tmp_path / "runtime" / (
        "Scripts" if sys.platform == "win32" else "bin"
    )
    executable_directory.mkdir(parents=True)
    deeplaw = executable_directory / (
        "deeplaw.exe" if sys.platform == "win32" else "deeplaw"
    )
    deeplaw.write_text("runtime probe fixture\n", encoding="utf-8")
    python = executable_directory / (
        "python.exe" if sys.platform == "win32" else "python"
    )
    if sys.platform == "win32":
        python.write_text("runtime probe fixture\n", encoding="utf-8")
    else:
        python.symlink_to(sys.executable)

    selected = _runtime_python([str(deeplaw)])

    assert selected == python.absolute()
    if sys.platform != "win32":
        assert selected != python.resolve(strict=True)


def test_temporal_and_retention_gold_are_unambiguous() -> None:
    gold = _candidate()
    timeline = next(case for case in gold["cases"] if case["case_id"] == "semantic-case-08")
    assert len(timeline["expected_objects"]) == 3
    assert {item["kind"] for item in timeline["expected_objects"]} == {"event"}
    assert timeline["expected_sequence"] == ["2025-01-10", "2025-03-15", "2025-05-20"]
    multi_format = next(
        case for case in gold["cases"] if case["case_id"] == "semantic-case-15"
    )
    assert any(
        item["canonical_label"] == "Atlas publication scheduled on 2025-07-01"
        for item in multi_format["expected_objects"]
    )
    for filename in ("04-retention-policy-a.md", "05-retention-policy-b.md"):
        text = (
            REPOSITORY / "benchmarks" / "semantic" / "fixtures" / filename
        ).read_text(encoding="utf-8")
        for term in ("Atlas production service", "public", "worldwide", "2026"):
            assert term in text


def test_multi_format_timeline_selection_retains_named_concept() -> None:
    candidates = [
        {"knowledge_id": "event-a", "kind": "event", "channels": ["lexical"]},
        {"knowledge_id": "event-b", "kind": "event", "channels": ["lexical"]},
        {
            "knowledge_id": "concept-a",
            "kind": "concept",
            "channels": ["identity_alias"],
        },
        {"knowledge_id": "event-noise", "kind": "event", "channels": ["dense"]},
    ]

    selected = PurposeAwareRetrievalService._duty_aware_selection(
        candidates,
        query=(
            "Atlas 审查（Atlas review）于 2025-06-01 完成（completed）；"
            "Atlas 发布（Atlas publication）计划于 2025-07-01 进行（scheduled）；"
            "Atlas Protocol。"
        ),
        purpose="answer",
        limit=4,
    )

    assert {item["kind"] for item in selected} == {"event", "concept"}
    assert selected[0]["knowledge_id"] == "concept-a"


def test_structured_date_anchors_bypass_only_matching_relevance_candidates() -> None:
    anchors = {"2025-01-10", "2025-03-15", "2025-05-20"}

    assert _matches_structured_query_anchor(
        anchors,
        {
            "title": "Policy drafted",
            "content": "The policy was drafted on 2025-01-10.",
        },
    )
    assert not _matches_structured_query_anchor(
        anchors,
        {
            "title": "Atlas review completed",
            "content": "The Atlas review completed on 2025-06-01.",
        },
    )


def test_bilingual_comparison_intent_is_explicit() -> None:
    query = (
        "比较两项诊断日志保留政策（Policy A 与 Policy B；"
        "diagnostic log retention policies），并保留它们之间的冲突（conflict）。"
    )

    assert _is_comparison_query(normalize_identity_text(query), query)


def test_historical_timeline_selection_is_chronological() -> None:
    candidates = [
        {
            "knowledge_id": "event-c",
            "kind": "event",
            "channels": ["lexical"],
            "valid_from": "2025-05-20T00:00:00Z",
        },
        {
            "knowledge_id": "event-a",
            "kind": "event",
            "channels": ["lexical"],
            "valid_from": "2025-01-10T00:00:00Z",
        },
        {
            "knowledge_id": "event-b",
            "kind": "event",
            "channels": ["lexical"],
            "valid_from": "2025-03-15T00:00:00Z",
        },
    ]

    selected = PurposeAwareRetrievalService._duty_aware_selection(
        candidates,
        query=(
            "2025-01-10、2025-03-15 和 2025-05-20 的时间线上分别发生了什么?"
        ),
        purpose="historical",
        limit=8,
    )

    assert [item["knowledge_id"] for item in selected] == [
        "event-a",
        "event-b",
        "event-c",
    ]


def test_frozen_retrieval_sequence_rejects_out_of_order_target_events() -> None:
    case = next(
        case for case in _candidate()["cases"] if case["case_id"] == "semantic-case-08"
    )
    expected = case["expected_objects"]
    source_revision_id = "sourcerev_" + "a" * 24
    source_ids = {"concept-procedure-events": source_revision_id}
    compiled = [
        {
            "knowledge_id": f"knowledge_{index:024x}",
            "kind": item["kind"],
            "title": item["canonical_label"],
            "semantic_key": f"event:frozen:{index}",
            "content": item["content_assertions"][0]["statement"],
            "valid_from": f"{date}T00:00:00Z",
            "source_refs": [{"source_revision_id": source_revision_id}],
        }
        for index, (item, date) in enumerate(
            zip(expected, reversed(case["expected_sequence"]), strict=True), start=1
        )
    ]

    check = _retrieval_sequence_check(
        case=case,
        value={"compiled": compiled},
        source_ids=source_ids,
    )

    assert check["applicable"] is True
    assert check["actual"] == list(reversed(case["expected_sequence"]))
    assert check["valid"] is False


def test_contradiction_requires_the_same_object_scope_and_time() -> None:
    left = {
        "body": "Atlas production public API diagnostic logs worldwide during 2026: 30 days.",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    right = {
        "body": "Atlas production public API diagnostic logs worldwide during 2026: 60 days.",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    relation = {
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
    }
    assert _same_applicability_conflict(left, right, relation)
    right["body"] = "Another service has a 60-day retention period during 2026."
    assert not _same_applicability_conflict(left, right, relation)


def test_query_relation_check_binds_endpoints_time_and_two_source_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        item for item in _candidate()["cases"] if item["case_id"] == "semantic-case-05"
    )
    source_a = "sourcerev_" + "a" * 24
    source_b = "sourcerev_" + "b" * 24
    fragment_a = "fragment_" + "c" * 24
    fragment_b = "fragment_" + "d" * 24
    locator = "section:1;paragraphs:3-6"
    references = [
        {
            "source_revision_id": source_a,
            "fragment_id": fragment_a,
            "locator": locator,
            "quote_sha256": "e" * 64,
        },
        {
            "source_revision_id": source_b,
            "fragment_id": fragment_b,
            "locator": locator,
            "quote_sha256": "f" * 64,
        },
    ]
    relation = {
        "relation_revision_id": "relationrev_" + "1" * 24,
        "relation_key": "relationkey_" + "2" * 24,
        "subject_knowledge_id": "knowledge_" + "3" * 24,
        "predicate": "contradicts",
        "object_knowledge_id": "knowledge_" + "4" * 24,
        "lifecycle": "active",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
            "evidence_refs": references,
    }
    monkeypatch.setattr(
        semantic_query_suite,
        "_current_relation_records",
        lambda _vault: [relation],
    )
    fragments = {
        fragment_a: {
            **references[0],
            "text_sha256": references[0]["quote_sha256"],
        },
        fragment_b: {
            **references[1],
            "text_sha256": references[1]["quote_sha256"],
        },
    }
    monkeypatch.setattr(
        semantic_query_suite,
        "_run_json",
        lambda prefix, *args, **kwargs: (
            {"fragment": fragments[args[args.index("--fragment-id") + 1]]},
            0,
            b"",
            b"",
        ),
    )
    value = {
        "compiled": [
            {
                "knowledge_id": relation["subject_knowledge_id"],
                "kind": "claim",
                "title": "Diagnostic log retention is 30 days",
                "content": (
                    "Policy A requires ordinary Atlas production diagnostic logs generated by "
                    "public API requests for the worldwide tenant population to be retained "
                    "for 30 days after collection during 2026. Restricted payloads are never "
                    "included in diagnostic logs."
                ),
                "source_refs": [references[0]],
            },
            {
                "knowledge_id": relation["object_knowledge_id"],
                "kind": "claim",
                "title": "Diagnostic log retention is 60 days",
                "content": (
                    "Policy B requires ordinary Atlas production diagnostic logs generated by "
                    "public API requests for the worldwide tenant population to be retained "
                    "for 60 days after collection during 2026. Restricted payloads are never "
                    "included in diagnostic logs."
                ),
                "source_refs": [references[1]],
            },
        ]
    }
    checks = _relation_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value=value,
        source_ids={"retention-a": source_a, "retention-b": source_b},
    )
    assert checks[0]["valid"] is True
    relation["object_knowledge_id"] = relation["subject_knowledge_id"]
    value["compiled"][1]["knowledge_id"] = relation["subject_knowledge_id"]
    self_edge = _relation_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value=value,
        source_ids={"retention-a": source_a, "retention-b": source_b},
    )
    assert self_edge[0]["valid"] is False
    relation["object_knowledge_id"] = "knowledge_" + "4" * 24
    value["compiled"][1]["knowledge_id"] = relation["object_knowledge_id"]
    relation["evidence_refs"] = [references[1]]
    one_sided = _relation_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value=value,
        source_ids={"retention-a": source_a, "retention-b": source_b},
    )
    assert one_sided[0]["valid"] is False
    relation["evidence_refs"] = []
    tampered = _relation_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value=value,
        source_ids={"retention-a": source_a, "retention-b": source_b},
    )
    assert tampered[0]["valid"] is False


def test_gold_freezes_concept_content_typed_conflict_and_withdrawal_gap() -> None:
    cases = {item["case_id"]: item for item in _candidate()["cases"]}
    concept_assertions = cases["semantic-case-04"]["expected_objects"][0][
        "content_assertions"
    ]
    assert len(concept_assertions) == 3
    assert any(
        "locator validation became mandatory" in assertion["required_terms"]
        for assertion in concept_assertions
    )
    for case_id in ("semantic-case-05", "semantic-case-11"):
        case = cases[case_id]
        assert case["forbidden_merges"] == [
            ["label-retention-30", "label-retention-60"]
        ]
        assertions = [
            assertion
            for expected in case["expected_objects"]
            for assertion in expected.get("content_assertions", [])
        ]
        assert any("ordinary" in assertion["required_terms"] for assertion in assertions)
        assert any(
            "Restricted payloads" in assertion["required_terms"]
            and "never included" in assertion["required_terms"]
            for assertion in assertions
        )
        relation = cases[case_id]["expected_relations"]
        assert relation == [
            {
                "relation_id": "relation-gold-retention-a-contradicts-retention-b",
                "subject_label_id": "label-retention-30",
                "predicate": "contradicts",
                "object_label_id": "label-retention-60",
                "directionality": "symmetric",
                "valid_from": "2026-01-01T00:00:00Z",
                "valid_to": "2027-01-01T00:00:00Z",
                "source_keys": ["retention-a", "retention-b"],
            }
        ]
    withdrawal = cases["semantic-case-10"]
    assert "explicit_gap" in withdrawal["required_outcomes"]
    assert withdrawal["expected_gap_codes"] == ["stale_knowledge"]


def test_relation_expectation_requires_explicit_forbidden_merge() -> None:
    value = _candidate()
    case = next(item for item in value["cases"] if item["case_id"] == "semantic-case-05")
    case["forbidden_merges"] = []
    with pytest.raises(ValueError, match="explicit forbidden merge"):
        validate_candidate(value, repository=REPOSITORY)


def test_claim_level_gold_rejects_missing_required_content() -> None:
    class _Connection:
        @staticmethod
        def execute(*_args: object, **_kwargs: object) -> object:
            class _Cursor:
                @staticmethod
                def fetchone() -> None:
                    return None

            return _Cursor()

    store = type("Store", (), {"connection": _Connection()})()
    assertion = {
        "required_terms": ["identity", "lifecycle", "scope", "sensitivity", "provenance"],
        "source_keys": ["concept-procedure-events"],
    }
    revision = {
        "revision_id": "knowledgerev_" + "a" * 24,
        "kind": "synthesis",
        "body": "Identity, lifecycle, scope, sensitivity, and provenance checks are required.",
        "source_refs": [{"source_revision_id": "sourcerev_" + "b" * 24}],
    }
    source_ids = {"concept-procedure-events": "sourcerev_" + "b" * 24}
    assert _content_assertion_valid(revision, assertion, store=store, source_ids=source_ids)
    revision["body"] = "Identity and lifecycle checks are required."
    assert not _content_assertion_valid(
        revision,
        assertion,
        store=store,
        source_ids=source_ids,
    )


def test_query_claim_binding_rejects_structurally_valid_wrong_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        case for case in _candidate()["cases"] if case["case_id"] == "semantic-case-07"
    )
    source_revision_id = "sourcerev_" + "a" * 24
    fragment_id = "fragment_" + "b" * 24
    locator = "section:1;paragraphs:3-5"
    quote_sha256 = "c" * 64
    monkeypatch.setattr(
        semantic_query_suite,
        "_run_json",
        lambda *args, **kwargs: (
            {
                "fragment": {
                    "source_revision_id": source_revision_id,
                    "fragment_id": fragment_id,
                    "locator": locator,
                    "text_sha256": quote_sha256,
                    "text": "Evidence admission requires identity and provenance checks.",
                }
            },
            0,
            b"",
            b"",
        ),
    )
    procedure = next(
        item
        for item in case["expected_objects"]
        if item["label_id"] == "label-admission-workflow"
    )
    checks = _claim_evidence_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value={
            "compiled": [
                {
                    "knowledge_id": "knowledge_" + "d" * 24,
                    "revision_id": "knowledgerev_" + "e" * 24,
                    "kind": procedure["kind"],
                    "title": procedure["canonical_label"],
                    "semantic_key": "procedure:evidence-admission-workflow",
                    "content": "\n".join(case["expected_sequence"]),
                    "source_refs": [
                        {
                            "source_revision_id": source_revision_id,
                            "fragment_id": fragment_id,
                            "locator": locator,
                            "quote_sha256": quote_sha256,
                        }
                    ],
                }
            ],
            "evidence": [],
        },
        source_ids={"concept-procedure-events": source_revision_id},
    )
    assert len(checks) == 1
    assert checks[0]["content_terms_valid"] is True
    assert checks[0]["evidence_terms_valid"] is False
    assert checks[0]["valid"] is False


def test_query_claim_binding_requires_every_synthesis_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        case for case in _candidate()["cases"] if case["case_id"] == "semantic-case-11"
    )
    source_a = "sourcerev_" + "a" * 24
    source_b = "sourcerev_" + "b" * 24
    fragment_b = "fragment_" + "c" * 24
    locator = "section:1;paragraphs:3-6"
    quote_sha256 = "d" * 64
    monkeypatch.setattr(
        semantic_query_suite,
        "_run_json",
        lambda *args, **kwargs: (
            {
                "fragment": {
                    "source_revision_id": source_b,
                    "fragment_id": fragment_b,
                    "locator": locator,
                    "text_sha256": quote_sha256,
                    "text": (
                        "Policy B requires ordinary Atlas production diagnostic logs generated "
                        "by public API requests worldwide during 2026 to be retained for 60 "
                        "days. Restricted payloads are never included in diagnostic logs."
                    ),
                }
            },
            0,
            b"",
            b"",
        ),
    )
    content = (
        "Both policies apply to ordinary Atlas production diagnostic logs generated by public "
        "API requests for the worldwide tenant population during 2026. Policy A requires 30 "
        "days while Policy B requires 60 days. Restricted payloads are never included in either "
        "policy's diagnostic logs."
    )
    checks = _claim_evidence_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value={
            "compiled": [
                {
                    "knowledge_id": "knowledge_" + "e" * 24,
                    "revision_id": "knowledgerev_" + "f" * 24,
                    "kind": "synthesis",
                    "title": "Retention policy comparison",
                    "semantic_key": "synthesis:atlas-retention-policy-comparison:2026",
                    "content": content,
                    "source_refs": [
                        {
                            "source_revision_id": source_b,
                            "fragment_id": fragment_b,
                            "locator": locator,
                            "quote_sha256": quote_sha256,
                        }
                    ],
                }
            ],
            "evidence": [],
        },
        source_ids={"retention-a": source_a, "retention-b": source_b},
    )
    assert len(checks) == 7
    assert all(check["source_coverage_valid"] is False for check in checks)
    assert all(check["valid"] is False for check in checks)


def test_query_claim_binding_rejects_tampered_synthesis_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = next(
        case for case in _candidate()["cases"] if case["case_id"] == "semantic-case-11"
    )
    source_a = "sourcerev_" + "a" * 24
    source_b = "sourcerev_" + "b" * 24
    fragment_a = "fragment_" + "c" * 24
    fragment_b = "fragment_" + "d" * 24
    locator = "section:1;paragraphs:3-4"
    reference_a = {
        "source_revision_id": source_a,
        "fragment_id": fragment_a,
        "locator": locator,
        "quote_sha256": "e" * 64,
    }
    reference_b = {
        "source_revision_id": source_b,
        "fragment_id": fragment_b,
        "locator": locator,
        "quote_sha256": "f" * 64,
    }
    fragments = {
        fragment_a: {
            **reference_a,
            "text_sha256": reference_a["quote_sha256"],
            "text": (
                "Policy A applies to ordinary Atlas production diagnostic logs generated by "
                    "public API requests for the worldwide tenant population during 2026 and "
                    "requires 30 days after collection. Restricted payloads are never included in "
                    "diagnostic logs."
            ),
        },
        fragment_b: {
            **reference_b,
            "text_sha256": reference_b["quote_sha256"],
            "text": (
                "Policy B applies to ordinary Atlas production diagnostic logs generated by "
                    "public API requests for the worldwide tenant population during 2026 and "
                    "requires 60 days after collection. Restricted payloads are never included in "
                    "diagnostic logs."
            ),
        },
    }
    monkeypatch.setattr(
        semantic_query_suite,
        "_run_json",
        lambda prefix, *args, **kwargs: (
            {"fragment": fragments[args[args.index("--fragment-id") + 1]]},
            0,
            b"",
            b"",
        ),
    )
    receipt = {
        "schema_version": "deeplaw.synthesis-query-evidence-receipt/v1",
        "synthesis_revision_id": "knowledgerev_" + "0" * 24,
        "input_set_sha256": "1" * 64,
        "source_revision_ids": [source_a, source_b],
        "source_refs": [reference_a, reference_b],
        "complete": True,
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json(receipt).encode("utf-8"))
    item = {
        "knowledge_id": "knowledge_" + "2" * 24,
        "revision_id": receipt["synthesis_revision_id"],
        "kind": "synthesis",
        "title": "Retention policy comparison",
        "semantic_key": "synthesis:atlas-retention-policy-comparison:2026",
        "content": (
            "Both policies apply to ordinary Atlas production diagnostic logs generated by "
            "public API requests for the worldwide tenant population during 2026. Policy A "
            "requires 30 days after collection while Policy B requires 60 days after collection. "
            "Restricted payloads are never included in either policy's diagnostic logs."
        ),
        "source_refs": [reference_a, reference_b],
        "synthesis_evidence_receipt": receipt,
    }
    source_ids = {"retention-a": source_a, "retention-b": source_b}
    valid = _claim_evidence_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value={"compiled": [item], "evidence": []},
        source_ids=source_ids,
    )
    synthesis_valid = [
        check for check in valid if check["label_id"] == "label-retention-comparison"
    ]
    assert len(synthesis_valid) == 3
    assert all(check["valid"] for check in synthesis_valid)
    item["synthesis_evidence_receipt"]["receipt_sha256"] = "0" * 64
    tampered = _claim_evidence_checks(
        ["deeplaw"],
        vault=tmp_path,
        case=case,
        value={"compiled": [item], "evidence": []},
        source_ids=source_ids,
    )
    synthesis_tampered = [
        check for check in tampered if check["label_id"] == "label-retention-comparison"
    ]
    assert all(check["receipt_valid"] is False for check in synthesis_tampered)
    assert all(check["valid"] is False for check in synthesis_tampered)


def test_security_challenge_tampering_is_counted_as_failure() -> None:
    authority_source = "sourcerev_" + "a" * 24
    restricted_source = "sourcerev_" + "b" * 24
    source_ids = {
        "authority-adversarial": authority_source,
        "restricted-canary": restricted_source,
    }
    safe = {
        "compiled": [],
        "evidence": [
            {
                "trust": "user_provided",
                "legal_authority": False,
                "source_refs": [{"source_revision_id": authority_source}],
            }
        ],
        "gaps": [{"code": "evidence_gap"}],
        "query_plan": {"fallback": {"used": False}},
        "authority_changed_by_ranking": False,
        "write_performed": False,
    }
    passed, *_ = _evaluate_read_challenge(
        challenge_type="unsupported_authoritative_claim",
        value=safe,
        source_ids=source_ids,
    )
    assert passed is True
    safe["evidence"][0]["trust"] = "official"
    passed, *_ = _evaluate_read_challenge(
        challenge_type="unsupported_authoritative_claim",
        value=safe,
        source_ids=source_ids,
    )
    assert passed is False
    restricted = {
        **safe,
        "evidence": [
            {
                "trust": "user_provided",
                "legal_authority": False,
                "excerpt": "ORCHID-VAULT-7319",
                "source_refs": [{"source_revision_id": restricted_source}],
            }
        ],
    }
    passed, *_ = _evaluate_read_challenge(
        challenge_type="restricted_disclosure",
        value=restricted,
        source_ids=source_ids,
    )
    assert passed is False


def test_semantic_gold_machine_scope_cannot_be_self_confirmed() -> None:
    with pytest.raises(ValueError, match="does not accept maintainer confirmation"):
        confirm_candidate(
            _candidate(),
            repository=REPOSITORY,
            reviewer_id="maintainer:test-reviewer",
            reason="This release scope intentionally does not use human Gold review.",
            reviewed_at="2026-08-01T01:02:03Z",
        )


def _machine_review_packet(role: str) -> dict:
    candidate = _candidate()
    cases = []
    for gold_case in candidate["cases"]:
        chinese_review = role == "chinese_adversarial_auditor"
        stable_ids = [
            f"knowledge_{index:024x}"
            for index, _item in enumerate(gold_case["expected_objects"], start=1)
        ]
        query_plan = {
            "schema_version": "test-query-plan",
            "case_id": gold_case["case_id"],
        }
        case = {
            "case_id": gold_case["case_id"],
            "recommendation": "CONFIRM",
            "frozen_query": (
                f"中文对抗查询：{gold_case['query']}"
                if chinese_review
                else gold_case["query"]
            ),
            "query_language": "zh-CN" if chinese_review else "en",
            "canonical_query_sha256": sha256_bytes(
                gold_case["query"].encode("utf-8")
            ),
            "expected_stable_ids": stable_ids,
            "actual_stable_ids": stable_ids,
            "expected_claims": [
                assertion["statement"]
                for item in gold_case["expected_objects"]
                for assertion in item.get("content_assertions", [])
            ],
            "actual_claims": [
                assertion["statement"]
                for item in gold_case["expected_objects"]
                for assertion in item.get("content_assertions", [])
            ],
            "citations": (
                [
                    {
                        "source_revision_id": "sourcerev_" + "1" * 24,
                        "fragment_id": "fragment_" + "2" * 24,
                        "locator": "section:1;paragraphs:1-2",
                        "quote_sha256": "3" * 64,
                        "valid": True,
                    }
                ]
                if stable_ids
                else []
            ),
            "query_plan": query_plan,
            "query_plan_sha256": sha256_bytes(
                canonical_json(query_plan).encode("utf-8")
            ),
            "claim_entailment": "entailed",
            "extraction_completeness": 1.0,
            "source_coverage": 1.0,
            "discrepancy": None,
            "commands": [
                "deeplaw knowledge query",
                "deeplaw knowledge context",
                "deeplaw knowledge verify-capsule",
            ],
            "evidence_sha256": "0" * 64,
        }
        case["evidence_sha256"] = case_evidence_sha256(case)
        cases.append(case)
    challenges = [
        {
            "challenge_id": item["challenge_id"],
            "challenge_type": item["challenge_type"],
            "executed": True,
            "passed": True,
            "failure_count": 0,
            "command": "deeplaw adversarial-challenge",
            "evidence_sha256": "0" * 64,
        }
        for item in candidate["security_challenges"]
    ]
    for challenge in challenges:
        challenge["evidence_sha256"] = challenge_evidence_sha256(challenge)
    packet = {
        "schema_version": "deeplaw.semantic-machine-review-packet/v1",
        "classification": "independent_machine_review",
        "auditor_role": role,
        "auditor_identity": {
            "model": "gpt-5.6-sol",
            "reasoning_effort": "ultra",
            "identity_version": f"{role}/1",
        },
        "isolation": {
            "repository_read_only": True,
            "isolated_temporary_state": True,
            "other_auditor_conclusions_read": False,
            "external_model_provider_called": False,
        },
        "candidate_binding": candidate_binding(REPOSITORY),
        "status": "CONFIRM",
        "cases": cases,
        "security_challenges": challenges,
        "commands": ["deeplaw knowledge query", "deeplaw knowledge context"],
        "evidence_sha256": "0" * 64,
    }
    packet["evidence_sha256"] = packet_evidence_sha256(packet)
    packet["packet_sha256"] = sha256_bytes(canonical_json(packet).encode("utf-8"))
    return packet


def test_machine_review_consensus_requires_six_unanimous_isolated_packets() -> None:
    packets = [_machine_review_packet(role) for role in AUDITOR_ROLES]
    for packet in packets:
        validate_packet(
            packet,
            repository=REPOSITORY,
            binding=candidate_binding(REPOSITORY),
        )
    consensus = build_consensus(packets, repository=REPOSITORY)
    assert consensus["machine_review_consensus"] == "confirmed"
    assert consensus["human_gold_review"]["status"] == "not_required"
    assert consensus["maintainer_confirmed"] is False
    assert consensus["reviewer_id"] is None
    assert consensus["external_real_model_semantic_execution"] == "not_executed"
    assert consensus["competitive_claim_eligible"] is False
    assert all(item["auditor_confirmations"] == 6 for item in consensus["cases"])

    english = build_owner_packet(
        language="en",
        consensus=consensus,
        packets=packets,
        candidate=_candidate(),
        counterpart_packet_sha256=None,
        repository=REPOSITORY,
    )
    chinese = build_owner_packet(
        language="zh-CN",
        consensus=consensus,
        packets=packets,
        candidate=_candidate(),
        counterpart_packet_sha256=english["packet_sha256"],
        repository=REPOSITORY,
    )
    assert chinese["counterpart_packet_sha256"] == english["packet_sha256"]
    assert chinese["human_final_decision"] == "not_required"
    assert [item["case_id"] for item in chinese["cases"]] == [
        item["case_id"] for item in english["cases"]
    ]
    assert chinese["cases"][0]["frozen_query"].startswith("中文对抗查询：")
    assert english["cases"][0]["frozen_query"] == _candidate()["cases"][0]["query"]

    packets[0]["cases"][0]["recommendation"] = "RETURN_FOR_FIX"
    with pytest.raises(ValueError, match="packet digest is invalid"):
        build_consensus(packets, repository=REPOSITORY)


def test_semantic_gold_rejects_changed_fixture_bytes(tmp_path: Path) -> None:
    value = _candidate()
    fixture = value["sources"][0]
    fixture["relative_path"] = "benchmarks/semantic/fixtures/missing.md"
    with pytest.raises(FileNotFoundError):
        validate_candidate(value, repository=REPOSITORY)


def test_semantic_review_bundle_excludes_capability_material(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="semantic review", scope="personal")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="semantic-review-test",
            operations=tuple(sorted(SINK_OPERATIONS)),
        )
        assert Path(grant["token_path"]).is_file()

    output = tmp_path / "review-bundle"
    manifest = export_review_bundle(vault, output)

    assert manifest["capability_tokens_included"] is False
    assert manifest["source_vault_verified_before_export"] is True
    assert not (output / ".deeplaw" / "capabilities").exists()
    assert not list(output.rglob("*.token"))
    with AutonomousKnowledgeStore(output, read_only=True) as store:
        assert store.vault_id == manifest["vault_id"]
        assert store.audit_head == manifest["audit_head"]


def test_real_semantic_host_unavailable_is_schema_valid_not_executed() -> None:
    gold = _candidate()
    corpus = {
        "schema_version": "deeplaw.semantic-host-corpus/v1",
        "gold_id": gold["gold_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "sources": [
            {
                "source_key": source["source_key"],
                "source_revision_id": f"sourcerev_{index:024x}",
            }
            for index, source in enumerate(gold["sources"], start=1)
        ],
    }
    report = not_executed_report(
        host="claude_code",
        host_version="unavailable",
        model_identity="unavailable",
        network_policy="offline",
        grant_id="grant_0123456789abcdef01234567",
        gold=gold,
        corpus=corpus,
        reason="The external host is not installed in core CI.",
    )
    assert report["status"] == "not_executed"
    assert report["executed"] is False
    assert len(report["runs"]) == len(gold["sources"])
    assert report["formal_release_evidence_ready"] is False


def _phased_corpus(gold: dict) -> dict:
    sources = []
    atlas_key = "sourcekey_" + "a" * 24
    for index, source in enumerate(gold["sources"], start=1):
        canonical_source_key = (
            atlas_key
            if source["source_key"] in {"update-v1", "update-v2"}
            else f"sourcekey_{index:024x}"
        )
        sources.append(
            {
                "source_key": source["source_key"],
                "canonical_source_key": canonical_source_key,
                "source_id": f"source_{index:024x}",
                "source_revision_id": f"sourcerev_{index:024x}",
                "phase": ("successor" if source["source_key"] == "update-v2" else "baseline"),
                "initial_lifecycle_status": (
                    "pending" if source["source_key"] == "update-v2" else "active"
                ),
                "review_manifest_sha256": f"{index:064x}",
                "sensitivity": source["sensitivity"],
            }
        )
    return {
        "schema_version": "deeplaw.semantic-host-corpus/v2",
        "corpus_id": "semanticcorpus_0123456789abcdef01234567",
        "gold_id": gold["gold_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "vault_id": "vault_0123456789abcdef01234567",
        "snapshot_sha256": "a" * 64,
        "grant_id": "grant_0123456789abcdef01234567",
        "sources": sources,
        "transitions": [
            {
                "operation": "activate_successor",
                "predecessor_source_key": "update-v1",
                "successor_source_key": "update-v2",
            },
            {"operation": "withdraw_source", "source_key": "retention-a"},
        ],
    }


def test_phased_semantic_host_unavailable_binds_lifecycle() -> None:
    gold = _candidate()
    corpus = _phased_corpus(gold)
    report = not_executed_report(
        host="opencode",
        host_version="1.18.8",
        model_identity="not_executed",
        network_policy="offline",
        grant_id=corpus["grant_id"],
        gold=gold,
        corpus=corpus,
        reason="The external host is not installed in core CI.",
        host_discovery_status="not_found",
        version_command="opencode --version",
        authentication_status="not_checked",
        authentication_reason_code="host_not_found",
        authentication_reason="Authentication was not checked because the host was not found.",
        model_access_status="not_checked",
        model_access_reason_code="host_not_found",
        model_access_reason="Model access was not checked because the host was not found.",
    )
    assert report["schema_version"] == "deeplaw.real-semantic-host-report/v2"
    assert len(report["binding"]["commit"]) == 40
    assert report["binding"]["package_version"] == "0.12.0"
    assert [item["phase"] for item in report["phases"]] == [
        "baseline",
        "successor",
    ]
    assert [item["status"] for item in report["transitions"]] == [
        "not_executed",
        "not_executed",
    ]
    assert report["formal_release_evidence_ready"] is False
    assert report["execution_prerequisites"]["external_model_execution"] == "not_executed"


def test_claude_real_model_report_records_discovery_but_no_authentication() -> None:
    gold = _candidate()
    corpus = _phased_corpus(gold)
    report = not_executed_report(
        host="claude_code",
        host_version="2.1.220",
        model_identity="not_executed",
        network_policy="offline",
        grant_id=corpus["grant_id"],
        gold=gold,
        corpus=corpus,
        reason="Owner confirmation is pending; no external model task was executed.",
        host_discovery_status="discovered",
        version_command="claude --version",
        observed_version="2.1.220 (Claude Code)",
        authentication_status="unavailable",
        authentication_reason_code="owner_confirmation_pending",
        authentication_reason="No external model credential was supplied.",
        model_access_status="unavailable",
        model_access_reason_code="owner_confirmation_pending",
        model_access_reason="Paid model execution is prohibited before owner confirmation.",
    )
    assert report["status"] == "not_executed"
    assert report["host_version"] == "2.1.220"
    assert report["execution_prerequisites"]["host_discovery"] == {
        "status": "discovered",
        "version_command": "claude --version",
        "observed_version": "2.1.220 (Claude Code)",
    }
    assert report["execution_prerequisites"]["authentication"]["status"] == "unavailable"
    assert report["execution_prerequisites"]["external_model_execution"] == "not_executed"


def test_real_host_usage_accepts_only_provider_reported_turn_events() -> None:
    stdout = b"\n".join(
        (
            b'{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}',
            b'{"type":"item.completed","usage":{"input_tokens":999,"output_tokens":999}}',
            b'{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}',
        )
    )
    assert _provider_token_usage(stdout) == {
        "status": "provider_reported",
        "input_tokens": 17,
        "output_tokens": 5,
        "total_tokens": 22,
    }
    assert _provider_token_usage(b"not-json") == {
        "status": "unreported",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
    }


def test_phased_semantic_host_rejects_false_successor_identity() -> None:
    gold = _candidate()
    corpus = _phased_corpus(gold)
    successor = next(item for item in corpus["sources"] if item["source_key"] == "update-v2")
    successor["canonical_source_key"] = "sourcekey_" + "b" * 24
    with pytest.raises(ValueError, match="preserve its canonical Source identity"):
        not_executed_report(
            host="opencode",
            host_version="unavailable",
            model_identity="unavailable",
            network_policy="offline",
            grant_id=corpus["grant_id"],
            gold=gold,
            corpus=corpus,
            reason="unavailable",
        )


def test_semantic_scorer_refuses_pending_gold(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="maintainer-confirmed"):
        score(
            gold=_candidate(),
            host_report={},
            vault=tmp_path,
        )


def test_semantic_query_cost_is_closed_and_bound_to_the_host_run() -> None:
    value = {
        "schema_version": "deeplaw.semantic-query-cost/v1",
        "gold_id": "semanticgold_0123456789abcdef01234567",
        "compiler_report_id": "semantichostrun_0123456789abcdef01234567",
        "query_set_sha256": "0" * 64,
        "first_party_command": "deeplaw knowledge query",
        "query_count": 15,
        "total_query_tokens": 1200,
        "total_context_bytes": 4800,
        "raw_fragment_baseline_bytes": 22000,
        "measurement_method": "provider_reported",
        "budget": {
            "max_items": 8,
            "max_sources": 12,
            "max_chars": 8000,
            "max_tokens": 6000,
            "max_sensitivity": "private",
            "cold_or_warm": "warm",
        },
        "measured_at": "2026-08-01T01:02:03Z",
    }
    assert (
        _query_cost(
            value,
            gold_id=value["gold_id"],
            compiler_report_id=value["compiler_report_id"],
        )
        == value
    )
    with pytest.raises(ValueError, match="does not bind"):
        _query_cost(
            value,
            gold_id=value["gold_id"],
            compiler_report_id="semantichostrun_aaaaaaaaaaaaaaaaaaaaaaaa",
        )


def _query_output(
    *,
    compiled: list[dict] | None = None,
    gaps: list[dict] | None = None,
) -> dict:
    return {
        "compiled": compiled or [],
        "evidence": [],
        "gaps": gaps or [],
        "contradictions": [],
        "query_plan": {"fallback": {"used": False}},
        "metrics": {
            "provider_payload_bytes": 1024,
            "repeated_query_reused_compilation": True,
        },
        "write_performed": False,
        "authority_changed_by_ranking": False,
    }


def _stub_cli_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        semantic_query_suite,
        "_citation_checks",
        lambda *args, **kwargs: ([], 0, 0, True),
    )
    monkeypatch.setattr(
        semantic_query_suite,
        "_claim_evidence_checks",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        semantic_query_suite,
        "_context_verification",
        lambda *args, **kwargs: {
            "capsule_id": "capsule_0123456789abcdef01234567",
            "capsule_sha256": "a" * 64,
            "provider_payload_bytes": 1024,
            "provider_hard_limit_valid": True,
            "verification_valid": True,
        },
    )


def test_query_suite_requires_only_an_explicit_gap_for_unanswerable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_cli_audit(monkeypatch)
    case = next(case for case in _candidate()["cases"] if case["task_type"] == "unanswerable")
    output = _query_output(gaps=[{"code": "retrieval_gap"}])
    result = _case_result(
        prefix=["deeplaw"],
        vault=tmp_path,
        case=case,
        cold=output,
        warm=output,
        cold_latency_ms=5,
        warm_latency_ms=3,
        source_ids={
            "retention-a": "sourcerev_" + "a" * 24,
            "update-v1": "sourcerev_" + "b" * 24,
            "update-v2": "sourcerev_" + "c" * 24,
        },
    )
    assert result["status"] == "passed"
    assert result["explicit_gap"] is True


def test_query_suite_rejects_withdrawn_source_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_cli_audit(monkeypatch)
    case = next(case for case in _candidate()["cases"] if case["task_type"] == "source_withdrawal")
    withdrawn = "sourcerev_" + "a" * 24
    output = _query_output(
        compiled=[
            {
                "revision_id": "knowledgerev_" + "d" * 24,
                "source_refs": [{"source_revision_id": withdrawn}],
            }
        ]
    )
    result = _case_result(
        prefix=["deeplaw"],
        vault=tmp_path,
        case=case,
        cold=output,
        warm=output,
        cold_latency_ms=5,
        warm_latency_ms=3,
        source_ids={
            "retention-a": withdrawn,
            "update-v1": "sourcerev_" + "b" * 24,
            "update-v2": "sourcerev_" + "c" * 24,
        },
    )
    assert result["status"] == "failed"
    assert result["failure_reason"] == "withdrawn Source Revision was selected"


def test_query_suite_rejects_substitute_answer_for_withdrawn_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_cli_audit(monkeypatch)
    case = next(case for case in _candidate()["cases"] if case["task_type"] == "source_withdrawal")
    withdrawn = "sourcerev_" + "a" * 24
    substitute = "sourcerev_" + "b" * 24
    output = _query_output(
        compiled=[
            {
                "knowledge_id": "knowledge_" + "c" * 24,
                "revision_id": "knowledgerev_" + "d" * 24,
                "kind": "claim",
                "title": "Diagnostic log retention is 60 days",
                "semantic_key": "claim:retention:policy-b",
                "source_refs": [{"source_revision_id": substitute}],
            }
        ],
        gaps=[{"code": "stale_knowledge"}, {"code": "retrieval_gap"}],
    )
    result = _case_result(
        prefix=["deeplaw"],
        vault=tmp_path,
        case=case,
        cold=output,
        warm=output,
        cold_latency_ms=5,
        warm_latency_ms=3,
        source_ids={
            "retention-a": withdrawn,
            "update-v1": "sourcerev_" + "e" * 24,
            "update-v2": "sourcerev_" + "f" * 24,
        },
    )
    assert result["status"] == "failed"
    assert result["failure_reason"] == "withdrawn policy query returned a substitute answer"
