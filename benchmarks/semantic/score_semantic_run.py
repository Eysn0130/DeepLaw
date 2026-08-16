from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.release.evidence import repository_binding
from benchmarks.semantic.review_gold import validate_candidate
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.util import canonical_json, sha256_bytes, stable_id, strict_json_loads

THRESHOLDS = {
    "entity_target_scoped_precision": 0.95,
    "entity_recall": 0.85,
    "concept_target_scoped_precision": 0.90,
    "concept_fusion_recall": 0.80,
    "required_target_extraction_completeness": 1.0,
    "claim_level_content_entailment_accuracy": 1.0,
    "source_summary_supported_claim_rate": 1.0,
    "unsupported_claim_rate_max": 0.0,
    "claim_evidence_binding_accuracy": 1.0,
    "contradiction_precision": 0.95,
    "contradiction_recall": 0.80,
    "source_coverage": 0.95,
    "stale_withdrawal_prevention": 1.0,
}
HARD_FAILURE_KEYS = (
    "wrong_entity_merge",
    "invented_source_revision",
    "invalid_locator",
    "invalid_quote_hash",
    "unsupported_authoritative_claim",
    "authority_elevation",
    "unauthorized_mutation",
    "stale_prohibited_selection",
    "withdrawn_content_admission",
    "unauthorized_disclosure",
    "restricted_disclosure",
    "invalid_official_citation",
    "prompt_injection",
    "provider_hard_limit_violation",
    "silent_fallback",
    "partial_publication_reported_complete",
    "unrecoverable_run",
)


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _schema(name: str) -> dict[str, Any]:
    value = _load(_repository() / "contracts" / name)
    Draft202012Validator.check_schema(value)
    return value


def _validate(name: str, value: dict[str, Any]) -> None:
    Draft202012Validator(
        _schema(name),
        format_checker=FormatChecker(),
    ).validate(value)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator / denominator), 6) if denominator else 1.0


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w]+", folded, flags=re.UNICODE))


def _matches_label(revision: dict[str, Any], expected: dict[str, Any]) -> bool:
    candidates = [revision["title"]]
    aliases = revision["metadata"].get("aliases", [])
    if isinstance(aliases, list):
        candidates.extend(item for item in aliases if isinstance(item, str))
    candidate_norms = {_normalize(value) for value in candidates}
    normalized = _normalize(expected["canonical_label"])
    if normalized in candidate_norms:
        return True
    expected_tokens = set(normalized.split())
    return len(expected_tokens) >= 2 and any(
        expected_tokens.issubset(set(candidate.split())) for candidate in candidate_norms
    )


def _aliases_present(revision: dict[str, Any], expected: dict[str, Any]) -> bool:
    actual = revision.get("metadata", {}).get("aliases", [])
    if not isinstance(actual, list):
        return not expected["aliases"]
    normalized_actual = {_normalize(item) for item in actual if isinstance(item, str)}
    return all(_normalize(alias) in normalized_actual for alias in expected["aliases"])


def _sequence_fidelity(body: str | None, sequence: list[str]) -> float:
    if not body or not sequence:
        return 0.0 if sequence else 1.0
    normalized_body = _normalize(body)
    positions = [normalized_body.find(_normalize(item)) for item in sequence]
    present = sum(position >= 0 for position in positions)
    if not positions or present != len(positions):
        return _ratio(present, len(positions))
    return 1.0 if positions == sorted(positions) else 0.0


def _content_assertion_valid(
    revision: dict[str, Any],
    assertion: dict[str, Any],
    *,
    store: AutonomousKnowledgeStore,
    source_ids: dict[str, str],
) -> bool:
    body = _normalize(str(revision.get("body") or ""))
    terms_valid = all(_normalize(term) in body for term in assertion["required_terms"])
    expected_sources = {source_ids[source_key] for source_key in assertion["source_keys"]}
    actual_sources = {
        reference.get("source_revision_id")
        for reference in revision.get("source_refs", [])
        if isinstance(reference, dict)
    }
    if revision.get("kind") == "synthesis":
        row = store.connection.execute(
            """
            SELECT source_revision_ids_json FROM synthesis_input_sets_v1
            WHERE synthesis_revision_id = ?
            """,
            (revision["revision_id"],),
        ).fetchone()
        if row is not None:
            input_sources = strict_json_loads(row["source_revision_ids_json"])
            if isinstance(input_sources, list):
                actual_sources.update(
                    item for item in input_sources if isinstance(item, str)
                )
    return bool(terms_valid and expected_sources.issubset(actual_sources))


def _source_ref_state(store: AutonomousKnowledgeStore, reference: dict[str, Any]) -> str:
    source_revision_id = reference.get("source_revision_id")
    if not isinstance(source_revision_id, str):
        return "invented_source_revision"
    source = store.connection.execute(
        "SELECT 1 FROM source_revisions_v2 WHERE source_revision_id = ?",
        (source_revision_id,),
    ).fetchone()
    if source is None:
        return "invented_source_revision"
    fragment_id = reference.get("fragment_id")
    if fragment_id is None:
        return "valid"
    fragment = store.connection.execute(
        """
        SELECT fragments.locator, fragments.text_sha256,
               compilations.source_revision_id
        FROM fragments_v2 AS fragments
        JOIN compilations_v2 AS compilations USING(compilation_id)
        WHERE fragments.fragment_revision_id = ?
        """,
        (fragment_id,),
    ).fetchone()
    if fragment is None or fragment["source_revision_id"] != source_revision_id:
        return "invented_source_revision"
    if reference.get("locator") != fragment["locator"]:
        return "invalid_locator"
    if reference.get("quote_sha256") != fragment["text_sha256"]:
        return "invalid_quote_hash"
    return "valid"


def _synthesis_has_frozen_inputs(
    store: AutonomousKnowledgeStore,
    revision_id: str,
) -> bool:
    row = store.connection.execute(
        """
        SELECT source_revision_ids_json, compilation_run_ids_json,
               input_set_sha256
        FROM synthesis_input_sets_v1 WHERE synthesis_revision_id = ?
        """,
        (revision_id,),
    ).fetchone()
    if row is None:
        return False
    source_ids = strict_json_loads(row["source_revision_ids_json"])
    run_ids = strict_json_loads(row["compilation_run_ids_json"])
    if not isinstance(source_ids, list) or not source_ids or not isinstance(run_ids, list):
        return False
    return bool(row["input_set_sha256"])


def _cross_packet_identity_consistency(
    store: AutonomousKnowledgeStore,
    *,
    compilation_run_id: str,
    expected: dict[str, Any],
    final_knowledge_ids: set[str],
    final_semantic_keys: set[str],
) -> dict[str, Any]:
    rows = store.connection.execute(
        """
        SELECT observations.packet_id, observations.observation_json,
               dispositions.target_ref
        FROM semantic_observations_v2 AS observations
        LEFT JOIN semantic_observation_dispositions_v1 AS dispositions
          USING(compilation_run_id, observation_id)
        WHERE observations.compilation_run_id = ?
          AND observations.kind = 'entity'
        ORDER BY observations.packet_id, observations.observation_id
        """,
        (compilation_run_id,),
    ).fetchall()
    matching = []
    for row in rows:
        observation = strict_json_loads(row["observation_json"])
        candidate = {
            "title": observation.get("title_candidate") or "",
            "metadata": {"aliases": observation.get("aliases", [])},
        }
        if _matches_label(candidate, expected) and _aliases_present(candidate, expected):
            matching.append(row)
    packet_ids = {row["packet_id"] for row in matching}
    disposition_targets = {
        row["target_ref"]
        for row in matching
        if isinstance(row["target_ref"], str)
    }
    valid = bool(
        len(packet_ids) >= 2
        and len(final_knowledge_ids) == 1
        and len(disposition_targets) == 1
        and disposition_targets.issubset(final_semantic_keys)
    )
    return {
        "valid": valid,
        "packet_ids": sorted(packet_ids),
        "disposition_targets": sorted(disposition_targets),
        "final_knowledge_ids": sorted(final_knowledge_ids),
        "final_semantic_keys": sorted(final_semantic_keys),
    }


def _outputs(
    store: AutonomousKnowledgeStore,
    report: dict[str, Any],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_revision: dict[str, dict[str, Any]] = {}
    for run in report["runs"]:
        run_id = run["compilation_run_id"]
        if run_id is None:
            continue
        rows = store.connection.execute(
            """
            SELECT output_id, object_id FROM source_compilation_outputs_v1
            WHERE compilation_run_id = ? AND output_kind = 'knowledge_revision'
            ORDER BY output_id
            """,
            (run_id,),
        ).fetchall()
        for row in rows:
            revision_row = store.connection.execute(
                "SELECT * FROM knowledge_revisions_v3 WHERE revision_id = ?",
                (row["output_id"],),
            ).fetchone()
            if revision_row is None:
                continue
            revision = store._revision_row(revision_row, include_body=True)
            by_source[run["source_key"]].append(revision)
            by_revision[revision["revision_id"]] = revision
    return by_source, by_revision


def _relation_records(
    store: AutonomousKnowledgeStore,
    report: dict[str, Any],
) -> dict[frozenset[str], list[dict[str, Any]]]:
    records: dict[frozenset[str], list[dict[str, Any]]] = defaultdict(list)
    for run in report["runs"]:
        if run["compilation_run_id"] is None:
            continue
        rows = store.connection.execute(
            """
            SELECT relations.subject_knowledge_id, relations.object_knowledge_id,
                   relations.predicate, relations.valid_from, relations.valid_to,
                   relations.evidence_refs_json
            FROM source_compilation_outputs_v1 AS outputs
            JOIN knowledge_relation_revisions_v3 AS relations
              ON relations.relation_revision_id = outputs.output_id
            WHERE outputs.compilation_run_id = ?
              AND outputs.output_kind = 'relation_revision'
            """,
            (run["compilation_run_id"],),
        ).fetchall()
        for row in rows:
            if row["predicate"] in {"contradicts", "conflicts_with", "inconsistent_with"}:
                pair = frozenset(
                    (row["subject_knowledge_id"], row["object_knowledge_id"])
                )
                record = dict(row)
                evidence_refs = strict_json_loads(record.pop("evidence_refs_json"))
                if not isinstance(evidence_refs, list):
                    raise ValueError("relation evidence_refs_json must contain a list")
                record["evidence_refs"] = evidence_refs
                records[pair].append(record)
    return records


def _same_applicability_conflict(
    left: dict[str, Any],
    right: dict[str, Any],
    relation: dict[str, Any],
) -> bool:
    required_terms = {
        "atlas",
        "production",
        "public api",
        "diagnostic logs",
        "worldwide",
        "2026",
    }
    left_body = _normalize(str(left.get("body") or ""))
    right_body = _normalize(str(right.get("body") or ""))
    same_interval = bool(
        left.get("valid_from") == right.get("valid_from") == relation.get("valid_from")
        and left.get("valid_to") == right.get("valid_to") == relation.get("valid_to")
    )
    return bool(
        same_interval
        and all(_normalize(term) in left_body for term in required_terms)
        and all(_normalize(term) in right_body for term in required_terms)
    )


def _manual_review(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {
            "status": "not_recorded",
            "correction_count": None,
            "correction_time_seconds": None,
            "reviewer_id": None,
        }
    expected = {"status", "correction_count", "correction_time_seconds", "reviewer_id"}
    if set(value) != expected or value["status"] != "recorded":
        raise ValueError("manual review record is not closed or not explicitly recorded")
    if (
        not isinstance(value["correction_count"], int)
        or value["correction_count"] < 0
        or not isinstance(value["correction_time_seconds"], (int, float))
        or value["correction_time_seconds"] < 0
        or not isinstance(value["reviewer_id"], str)
        or not value["reviewer_id"].strip()
    ):
        raise ValueError("manual review metrics are invalid")
    return value


def _query_cost(
    value: dict[str, Any] | None,
    *,
    gold_id: str,
    compiler_report_id: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    schema_version = value.get("schema_version")
    if schema_version == "deeplaw.semantic-query-cost/v1":
        _validate("semantic-query-cost.v1.schema.json", value)
    elif schema_version == "deeplaw.semantic-query-cost/v2":
        _validate("semantic-query-cost.v2.schema.json", value)
    else:
        raise ValueError("semantic query cost schema is unsupported")
    if (
        value["gold_id"] != gold_id
        or value["compiler_report_id"] != compiler_report_id
    ):
        raise ValueError("semantic query cost does not bind Gold and compiler evidence")
    return value


def _query_report(
    value: dict[str, Any] | None,
    *,
    gold_id: str,
    compiler_report_id: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    _validate("semantic-query-run.v1.schema.json", value)
    if (
        value["gold_id"] != gold_id
        or value["compiler_report_id"] != compiler_report_id
    ):
        raise ValueError("semantic query report does not bind Gold and compiler evidence")
    return value


def score(
    *,
    gold: dict[str, Any],
    host_report: dict[str, Any],
    vault: Path,
    manual_review: dict[str, Any] | None = None,
    query_cost: dict[str, Any] | None = None,
    query_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gold_sha256 = validate_candidate(gold, repository=_repository())
    if gold["status"] != "maintainer_confirmed":
        raise ValueError("semantic scoring requires maintainer-confirmed Semantic Gold")
    host_schema = host_report.get("schema_version")
    if host_schema == "deeplaw.real-semantic-host-report/v1":
        _validate("real-semantic-host-report.v1.schema.json", host_report)
    elif host_schema == "deeplaw.real-semantic-host-report/v2":
        _validate("real-semantic-host-report.v2.schema.json", host_report)
    else:
        raise ValueError("real semantic host report schema is unsupported")
    if host_report["gold_id"] != gold["gold_id"]:
        raise ValueError("host report does not bind the selected Semantic Gold")
    if host_report["gold_status"] != "maintainer_confirmed":
        raise ValueError("host report was not executed against confirmed Semantic Gold")
    if host_schema == "deeplaw.real-semantic-host-report/v2":
        current = repository_binding(_repository())
        expected_binding = {
            "commit": current["commit"],
            "tree": current["tree"],
            "package_version": current["package_version"],
            "lock_sha256": current["lock_sha256"],
            "pyproject_sha256": current["pyproject_sha256"],
            "contracts_inventory_sha256": current["contracts"]["inventory_sha256"],
            "migrations_inventory_sha256": current["migrations"]["inventory_sha256"],
            "worktree_clean": current["worktree_clean"],
        }
        if host_report["binding"] != expected_binding:
            raise ValueError("host report does not bind the exact repository candidate")
    reviewed = _manual_review(manual_review)
    measured_query_cost = _query_cost(
        query_cost,
        gold_id=gold["gold_id"],
        compiler_report_id=host_report["report_id"],
    )
    measured_query_report = _query_report(
        query_report,
        gold_id=gold["gold_id"],
        compiler_report_id=host_report["report_id"],
    )
    query_cases = {item["case_id"]: item for item in (measured_query_report or {}).get("cases", [])}
    host_report_sha256 = hashlib.sha256(canonical_json(host_report).encode("utf-8")).hexdigest()
    hard_failures = dict.fromkeys(HARD_FAILURE_KEYS, 0)
    failure_cases: list[str] = []
    source_ids = {
        item["source_key"]: item["source_revision_id"] for item in host_report["runs"]
    }
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        by_source, by_revision = _outputs(store, host_report)
        all_outputs = list(by_revision.values())
        relation_records = _relation_records(store, host_report)
        relation_pairs = set(relation_records)
        matched_by_label: dict[str, set[str]] = defaultdict(set)
        label_knowledge_ids: dict[str, set[str]] = defaultdict(set)
        case_results: list[dict[str, Any]] = []
        expected_by_kind: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = (
            defaultdict(list)
        )
        procedure_scores: list[float] = []
        event_scores: list[float] = []
        assertion_results: list[bool] = []
        for case in gold["cases"]:
            pool_by_revision = {
                revision["revision_id"]: revision
                for source_key in case["source_keys"]
                for revision in by_source.get(source_key, [])
            }
            pool = list(pool_by_revision.values())
            matched_labels: list[str] = []
            missing_labels: list[str] = []
            case_event_matches: list[dict[str, Any]] = []
            for expected in case["expected_objects"]:
                matches = [
                    revision
                    for revision in pool
                    if revision["kind"] == expected["kind"]
                    and _matches_label(revision, expected)
                    and (
                        case["task_type"]
                        not in {"long_document_cross_packet_entity", "entity_aliases"}
                        or _aliases_present(revision, expected)
                    )
                ]
                expected_by_kind[expected["kind"]].append((expected, matches))
                if matches:
                    matched_labels.append(expected["label_id"])
                    matched_by_label[expected["label_id"]].update(
                        revision["revision_id"] for revision in matches
                    )
                    label_knowledge_ids[expected["label_id"]].update(
                        revision["knowledge_id"] for revision in matches
                    )
                    sequence = case.get("expected_sequence", [])
                    if case["task_type"] == "procedure_extraction":
                        procedure_scores.append(
                            max(_sequence_fidelity(item.get("body"), sequence) for item in matches)
                        )
                    if case["task_type"] == "event_timeline":
                        case_event_matches.extend(matches)
                    for assertion in expected.get("content_assertions", []):
                        assertion_results.append(
                            any(
                                _content_assertion_valid(
                                    item,
                                    assertion,
                                    store=store,
                                    source_ids=source_ids,
                                )
                                for item in matches
                            )
                        )
                elif expected["required"]:
                    missing_labels.append(expected["label_id"])
                    assertion_results.extend(
                        False for _ in expected.get("content_assertions", [])
                    )
            if case["task_type"] == "event_timeline":
                observed_dates = []
                for item in case_event_matches:
                    valid_from = item.get("valid_from")
                    if isinstance(valid_from, str):
                        observed_dates.append(valid_from[:10])
                    else:
                        match = re.search(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", item.get("body") or "")
                        if match is not None:
                            observed_dates.append(match.group(0))
                event_scores.append(
                    1.0
                    if sorted(set(observed_dates)) == case.get("expected_sequence", [])
                    else 0.0
                )
            outcome_pass = not missing_labels
            if case["task_type"] == "source_withdrawal":
                lifecycle_rows = store.connection.execute(
                    """
                    SELECT lifecycle.status
                    FROM source_revisions_v2 AS revisions
                    JOIN source_revision_bindings_v2 AS bindings USING(source_revision_id)
                    JOIN source_lifecycle AS lifecycle USING(source_id)
                    WHERE revisions.source_revision_id = ?
                    """,
                    (
                        next(
                            item["source_revision_id"]
                            for item in host_report["runs"]
                            if item["source_key"] == case["source_keys"][0]
                        ),
                    ),
                ).fetchall()
                outcome_pass = bool(lifecycle_rows) and all(
                    row["status"] in {"removed", "withdrawn", "rejected", "deleted"}
                    for row in lifecycle_rows
                )
            elif case["task_type"] == "unanswerable":
                outcome_pass = False
            query_case = query_cases.get(case["case_id"])
            if query_case is not None:
                if case["expected_objects"] or case["task_type"] == "source_withdrawal":
                    outcome_pass = bool(outcome_pass and query_case["status"] == "passed")
                else:
                    outcome_pass = query_case["status"] == "passed"
            status = "passed" if outcome_pass else "failed"
            if not outcome_pass:
                failure_cases.append(
                    f"{case['case_id']}: required semantic outcome was not demonstrated"
                )
            case_results.append(
                {
                    "case_id": case["case_id"],
                    "task_type": case["task_type"],
                    "status": status,
                    "matched_label_ids": sorted(matched_labels),
                    "missing_label_ids": sorted(missing_labels),
                    "notes": case["notes"],
                }
            )

        wrong_merges = 0
        for case in gold["cases"]:
            for left, right in case["forbidden_merges"]:
                if label_knowledge_ids[left] & label_knowledge_ids[right]:
                    wrong_merges += 1
        duplicate_identities = sum(
            max(0, len(knowledge_ids) - 1) for knowledge_ids in label_knowledge_ids.values()
        )
        identity_groups: dict[str, set[str]] = defaultdict(set)
        for case in gold["cases"]:
            for expected in case["expected_objects"]:
                group = expected.get("stable_identity_group")
                if group is not None:
                    identity_groups[group].update(
                        label_knowledge_ids[expected["label_id"]]
                    )
        duplicate_identities += sum(
            max(0, len(knowledge_ids) - 1)
            for knowledge_ids in identity_groups.values()
        )
        hard_failures["wrong_entity_merge"] = wrong_merges

        ref_states: list[str] = []
        for revision in all_outputs:
            if revision["authority"] != "agent_derived" or revision["legal_authority"]:
                hard_failures["authority_elevation"] += 1
            for reference in revision["source_refs"]:
                state = _source_ref_state(store, reference)
                ref_states.append(state)
                if state in {"invented_source_revision", "invalid_locator", "invalid_quote_hash"}:
                    hard_failures[state] += 1
        claims = [item for item in all_outputs if item["kind"] == "claim"]
        unsupported_claims = [
            item for item in claims if item["source_free"] or not item["source_refs"]
        ]
        claim_bindings_valid = [
            item
            for item in claims
            if item["source_refs"]
            and all(_source_ref_state(store, ref) == "valid" for ref in item["source_refs"])
        ]
        summaries = [
            item
            for item in all_outputs
            if item["kind"] == "synthesis"
            and isinstance(item.get("semantic_key"), str)
            and item["semantic_key"].startswith("source-summary:")
        ]
        supported_summaries = [
            item
            for item in summaries
            if item["source_refs"]
            and not item["source_free"]
            and all(_source_ref_state(store, ref) == "valid" for ref in item["source_refs"])
        ]
        syntheses = [item for item in all_outputs if item["kind"] == "synthesis"]
        grounded_syntheses = [
            item
            for item in syntheses
            if item["source_refs"]
            and all(_source_ref_state(store, ref) == "valid" for ref in item["source_refs"])
            and _synthesis_has_frozen_inputs(store, item["revision_id"])
        ]

        expected_entities = expected_by_kind["entity"]
        matched_entities = sum(bool(matches) for _, matches in expected_entities)
        expected_concepts = expected_by_kind["concept"]
        matched_concepts = sum(bool(matches) for _, matches in expected_concepts)
        entity_target_predictions = sum(
            len({match["knowledge_id"] for match in matches})
            for _expected, matches in expected_entities
        )
        concept_target_predictions = sum(
            len({match["knowledge_id"] for match in matches})
            for _expected, matches in expected_concepts
        )
        required_targets = [
            (expected, matches)
            for kind in expected_by_kind.values()
            for expected, matches in kind
            if expected["required"]
        ]

        expected_conflict_pairs: set[frozenset[str]] = set()
        expected_relation_pairs_by_case: dict[str, set[frozenset[str]]] = defaultdict(set)
        expectations_by_pair: dict[
            frozenset[str], list[dict[str, Any]]
        ] = defaultdict(list)
        for case in gold["cases"]:
            for expectation in case.get("expected_relations", []):
                pairs = {
                    frozenset((left, right))
                    for left in label_knowledge_ids[expectation["subject_label_id"]]
                    for right in label_knowledge_ids[expectation["object_label_id"]]
                }
                expected_conflict_pairs.update(pairs)
                expected_relation_pairs_by_case[case["case_id"]].update(pairs)
                for pair in pairs:
                    expectations_by_pair[pair].append(expectation)
        current_by_knowledge = {
            revision["knowledge_id"]: revision for revision in all_outputs
        }
        correct_conflicts: set[frozenset[str]] = set()
        for pair in relation_pairs & expected_conflict_pairs:
            pair_items = tuple(sorted(pair))
            if len(pair_items) != 2 or not all(
                identity in current_by_knowledge for identity in pair_items
            ):
                continue
            if any(
                _same_applicability_conflict(
                    current_by_knowledge[pair_items[0]],
                    current_by_knowledge[pair_items[1]],
                    relation,
                )
                and any(
                    relation["predicate"] == expectation["predicate"]
                    and relation["valid_from"] == expectation["valid_from"]
                    and relation["valid_to"] == expectation["valid_to"]
                    and bool(relation["evidence_refs"])
                    and {
                        reference.get("source_revision_id")
                        for reference in relation["evidence_refs"]
                        if isinstance(reference, dict)
                    }.issubset(
                        {source_ids[key] for key in expectation["source_keys"]}
                    )
                    and {
                        reference.get("source_revision_id")
                        for identity in pair_items
                        for reference in current_by_knowledge[identity]["source_refs"]
                        if isinstance(reference, dict)
                    }
                    == {source_ids[key] for key in expectation["source_keys"]}
                    for expectation in expectations_by_pair[pair]
                )
                for relation in relation_records[pair]
            ):
                correct_conflicts.add(pair)
        for case_id, expected_pairs in expected_relation_pairs_by_case.items():
            if expected_pairs and expected_pairs.issubset(correct_conflicts):
                continue
            case_result = next(
                item for item in case_results if item["case_id"] == case_id
            )
            case_result["status"] = "failed"
            failure = f"{case_id}: frozen typed relation expectation was not demonstrated"
            if failure not in failure_cases:
                failure_cases.append(failure)

        batches = store.connection.execute(
            """
            SELECT SUM(json_array_length(covered_fragment_ids_json)) AS covered,
                   SUM(json_array_length(covered_fragment_ids_json) +
                       json_array_length(omitted_fragments_json)) AS total
            FROM semantic_observation_batches_v2
            WHERE compilation_run_id IN ({})
            """.format(",".join("?" for _ in host_report["runs"])),
            tuple(item["compilation_run_id"] for item in host_report["runs"]),
        ).fetchone()
        source_coverage = _ratio(batches["covered"] or 0, batches["total"] or 0)

        cross_packet_case = next(
            case
            for case in gold["cases"]
            if case["task_type"] == "long_document_cross_packet_entity"
        )
        cross_expected = cross_packet_case["expected_objects"][0]
        cross_label = cross_expected["label_id"]
        cross_run_id = next(
            item["compilation_run_id"]
            for item in host_report["runs"]
            if item["source_key"] == cross_packet_case["source_keys"][0]
        )
        final_revisions = [
            by_revision[revision_id]
            for revision_id in matched_by_label[cross_label]
            if revision_id in by_revision
        ]
        final_semantic_keys = {
            item["semantic_key"]
            for item in final_revisions
            if isinstance(item.get("semantic_key"), str)
        }
        cross_packet_state = _cross_packet_identity_consistency(
            store,
            compilation_run_id=cross_run_id,
            expected=cross_expected,
            final_knowledge_ids=label_knowledge_ids[cross_label],
            final_semantic_keys=final_semantic_keys,
        )
        cross_packet_consistency = 1.0 if cross_packet_state["valid"] else 0.0
        if cross_packet_consistency != 1.0:
            failure_cases.append(
                "semantic-case-01: fewer than two distinct packet observations were "
                "merged into one stable Entity Knowledge ID"
            )

        update_case = next(
            case for case in gold["cases"] if case["task_type"] == "source_successor_update"
        )
        update_label = update_case["expected_objects"][0]["label_id"]
        update_correct = bool(label_knowledge_ids[update_label])
        old_source_id = next(
            item["source_revision_id"]
            for item in host_report["runs"]
            if item["source_key"] == "update-v1"
        )
        stale_old = store.connection.execute(
            """
            SELECT COUNT(*) FROM knowledge_dependencies_v1
            WHERE source_revision_id = ? AND freshness IN ('stale', 'invalidated')
            """,
            (old_source_id,),
        ).fetchone()[0]
        update_propagation = 1.0 if update_correct and stale_old > 0 else 0.0

        withdrawn_source_id = next(
            item["source_revision_id"]
            for item in host_report["runs"]
            if item["source_key"] == "retention-a"
        )
        withdrawn_dependencies = store.connection.execute(
            """
            SELECT COUNT(*) FROM knowledge_dependencies_v1
            WHERE source_revision_id = ? AND freshness IN ('stale', 'invalidated')
            """,
            (withdrawn_source_id,),
        ).fetchone()[0]
        withdrawn_current_rows = store.connection.execute(
            """
            SELECT revisions.*
            FROM knowledge_dependencies_v1 AS dependencies
            JOIN knowledge_objects_v3 AS objects
              ON objects.current_revision_id = dependencies.consumer_revision_id
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = objects.current_revision_id
            WHERE dependencies.source_revision_id = ?
              AND dependencies.freshness IN ('stale', 'invalidated')
              AND revisions.lifecycle = 'active'
            """,
            (withdrawn_source_id,),
        ).fetchall()
        withdrawn_current = sum(
            store.revision_provenance_admitted(store._revision_row(row, include_body=False))
            for row in withdrawn_current_rows
        )
        stale_withdrawal_prevention = (
            1.0 if withdrawn_dependencies > 0 and withdrawn_current == 0 else 0.0
        )
        hard_failures["withdrawn_content_admission"] = withdrawn_current
        hard_failures["stale_prohibited_selection"] = withdrawn_current
        if measured_query_report is not None:
            query_metrics = measured_query_report["metrics"]
            hard_failures["unauthorized_mutation"] += query_metrics["unauthorized_writes"]
            hard_failures["unauthorized_mutation"] += query_metrics[
                "unauthorized_mutation_failures"
            ]
            hard_failures["authority_elevation"] += query_metrics["authority_elevations"]
            hard_failures["silent_fallback"] += query_metrics["silent_fallbacks"]
            hard_failures["silent_fallback"] += query_metrics[
                "silent_fallback_challenge_failures"
            ]
            hard_failures["stale_prohibited_selection"] += query_metrics[
                "stale_prohibited_selections"
            ]
            hard_failures["unsupported_authoritative_claim"] += query_metrics[
                "unsupported_authoritative_claims"
            ]
            hard_failures["restricted_disclosure"] += query_metrics[
                "restricted_disclosures"
            ]
            hard_failures["unauthorized_disclosure"] += query_metrics[
                "restricted_disclosures"
            ]
            hard_failures["invalid_official_citation"] += query_metrics[
                "invalid_official_citations"
            ]
            hard_failures["prompt_injection"] += query_metrics[
                "prompt_injection_failures"
            ]
            hard_failures["provider_hard_limit_violation"] += query_metrics[
                "provider_hard_limit_violations"
            ]

        partial_complete = sum(
            1
            for run in host_report["runs"]
            if run["transaction_status"] == "succeeded" and run["semantic_status"] != "complete"
        )
        hard_failures["partial_publication_reported_complete"] = partial_complete
        hard_failures["unrecoverable_run"] = sum(
            1 for run in host_report["runs"] if run["transaction_status"] in {"failed", "aborted"}
        )

        token_total = 0
        token_measured = False
        for run in host_report["runs"]:
            row = store.connection.execute(
                """
                SELECT token_usage_json FROM source_compilation_run_metadata_v1
                WHERE compilation_run_id = ?
                """,
                (run["compilation_run_id"],),
            ).fetchone()
            if row is None:
                continue
            usage = strict_json_loads(row["token_usage_json"])
            if isinstance(usage, dict):
                for key, value in usage.items():
                    if "token" in key and isinstance(value, int) and value >= 0:
                        token_total += value
                        token_measured = True
        if not token_measured and host_schema == "deeplaw.real-semantic-host-report/v2":
            phase_usage = [item["token_usage"] for item in host_report["phases"]]
            if phase_usage and all(
                item["status"] == "provider_reported" and isinstance(item["total_tokens"], int)
                for item in phase_usage
            ):
                token_total = sum(item["total_tokens"] for item in phase_usage)
                token_measured = True

        metrics = {
            "entity_target_scoped_precision": _ratio(
                matched_entities, entity_target_predictions
            ),
            "entity_recall": _ratio(matched_entities, len(expected_entities)),
            "wrong_merge_count": wrong_merges,
            "duplicate_identity_count": duplicate_identities,
            "concept_target_scoped_precision": _ratio(
                matched_concepts, concept_target_predictions
            ),
            "concept_fusion_recall": _ratio(matched_concepts, len(expected_concepts)),
            "required_target_extraction_completeness": _ratio(
                sum(bool(matches) for _expected, matches in required_targets),
                len(required_targets),
            ),
            "claim_level_content_entailment_accuracy": _ratio(
                sum(assertion_results), len(assertion_results)
            ),
            "source_summary_supported_claim_rate": _ratio(len(supported_summaries), len(summaries)),
            "unsupported_claim_rate": _ratio(len(unsupported_claims), len(claims)),
            "claim_evidence_binding_accuracy": _ratio(len(claim_bindings_valid), len(claims)),
            "contradiction_precision": _ratio(len(correct_conflicts), len(relation_pairs)),
            "contradiction_recall": _ratio(len(correct_conflicts), len(expected_conflict_pairs)),
            "procedure_step_fidelity": (
                round(sum(procedure_scores) / len(procedure_scores), 6) if procedure_scores else 0.0
            ),
            "event_temporal_fidelity": (
                round(sum(event_scores) / len(event_scores), 6) if event_scores else 0.0
            ),
            "source_coverage": source_coverage,
            "cross_packet_consistency": cross_packet_consistency,
            "synthesis_groundedness": _ratio(len(grounded_syntheses), len(syntheses)),
            "update_propagation_correctness": update_propagation,
            "stale_withdrawal_prevention": stale_withdrawal_prevention,
            "failure_recovery_rate": _ratio(
                sum(run["transaction_status"] == "succeeded" for run in host_report["runs"]),
                len(host_report["runs"]),
            ),
            "build_tokens": token_total if token_measured else None,
            "query_tokens": (
                measured_query_cost.get("total_query_tokens")
                if measured_query_cost is not None
                and measured_query_cost["schema_version"]
                == "deeplaw.semantic-query-cost/v1"
                else measured_query_cost.get("actual_provider_input_tokens")
                if measured_query_cost is not None
                else None
            ),
        }
        verification = store.verify()
        if not verification["valid"]:
            hard_failures["unrecoverable_run"] += 1

    threshold_pass = (
        metrics["entity_target_scoped_precision"]
        >= THRESHOLDS["entity_target_scoped_precision"]
        and metrics["entity_recall"] >= THRESHOLDS["entity_recall"]
        and metrics["concept_target_scoped_precision"]
        >= THRESHOLDS["concept_target_scoped_precision"]
        and metrics["concept_fusion_recall"] >= THRESHOLDS["concept_fusion_recall"]
        and metrics["required_target_extraction_completeness"]
        >= THRESHOLDS["required_target_extraction_completeness"]
        and metrics["claim_level_content_entailment_accuracy"]
        >= THRESHOLDS["claim_level_content_entailment_accuracy"]
        and metrics["source_summary_supported_claim_rate"]
        >= THRESHOLDS["source_summary_supported_claim_rate"]
        and metrics["unsupported_claim_rate"] <= THRESHOLDS["unsupported_claim_rate_max"]
        and metrics["claim_evidence_binding_accuracy"]
        >= THRESHOLDS["claim_evidence_binding_accuracy"]
        and metrics["contradiction_precision"] >= THRESHOLDS["contradiction_precision"]
        and metrics["contradiction_recall"] >= THRESHOLDS["contradiction_recall"]
        and metrics["source_coverage"] >= THRESHOLDS["source_coverage"]
        and metrics["stale_withdrawal_prevention"] >= THRESHOLDS["stale_withdrawal_prevention"]
    )
    passed = bool(
        host_report["status"] == "passed"
        and measured_query_report is not None
        and measured_query_report["status"] == "passed"
        and threshold_pass
        and not any(hard_failures.values())
        and all(item["status"] == "passed" for item in case_results)
    )
    formal_release_eligible = bool(
        passed
        and host_report.get("formal_release_evidence_ready") is True
        and measured_query_report is not None
        and measured_query_report["status"] == "passed"
        and metrics["build_tokens"] is not None
        and metrics["query_tokens"] is not None
    )
    evaluated_at = _timestamp()
    report = {
        "schema_version": "deeplaw.semantic-quality-report/v1",
        "report_id": stable_id(
            "semanticquality", gold["gold_id"], host_report["report_id"], evaluated_at
        ),
        "gold_id": gold["gold_id"],
        "gold_sha256": gold_sha256,
        "host_report_id": host_report["report_id"],
        "host_report_sha256": host_report_sha256,
        "query_report_id": (
            measured_query_report["report_id"] if measured_query_report is not None else None
        ),
        "query_report_sha256": (
            sha256_bytes(canonical_json(measured_query_report).encode("utf-8"))
            if measured_query_report is not None
            else None
        ),
        "host": host_report["host"],
        "host_version": host_report["host_version"],
        "model_identity": host_report["model_identity"],
        "evaluated_at": evaluated_at,
        "metrics": metrics,
        "thresholds": THRESHOLDS,
        "hard_failures": hard_failures,
        "case_results": case_results,
        "failure_cases": sorted(set(failure_cases)),
        "manual_review": reviewed,
        "passed": passed,
        "formal_release_eligible": formal_release_eligible,
        "competitive_claim_eligible": False,
        "limitations": [
            "The scorer uses maintainer-confirmed labels and deterministic governed-state "
            "checks; it does not ask the producing model to score itself.",
            (
                "Query token cost is bound to a frozen first-party CLI query run."
                if measured_query_cost is not None
                else "Query token cost remains null until a separately frozen first-party "
                "CLI query run is attached."
            ),
            "A passed report is not a comparative claim and does not generalize to unknown "
            "hosts or models.",
        ],
    }
    _validate("semantic-quality-report.v1.schema.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score a real-host Semantic v2 run against confirmed Semantic Gold."
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--host-report", required=True, type=Path)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--manual-review", type=Path)
    parser.add_argument("--query-cost", type=Path)
    parser.add_argument("--query-report", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    manual = _load(arguments.manual_review) if arguments.manual_review else None
    result = score(
        gold=_load(arguments.gold),
        host_report=_load(arguments.host_report),
        vault=arguments.vault,
        manual_review=manual,
        query_cost=_load(arguments.query_cost) if arguments.query_cost else None,
        query_report=_load(arguments.query_report) if arguments.query_report else None,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(result) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
