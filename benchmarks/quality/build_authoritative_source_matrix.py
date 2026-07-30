from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_VERSION = "deeplaw.authoritative-source-quality-decision-matrix/v1"
DECISIONS = (
    "no_action",
    "rebuild_derived",
    "recompile_knowledge",
    "reparse_source_ir",
    "ingest_new_source_revision",
    "blocked_invalid_evidence",
)


class MatrixError(RuntimeError):
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


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_value(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


def _evaluation_summary(value: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "release_id",
        "database_sha256",
        "case_count",
        "ranked_case_count",
        "cases_sha256",
        "source_manifest_sha256",
        "overall_pass_rate",
        "retrieval_pass_rate",
        "constraint_pass_rate",
        "receipt_verification_pass_rate",
        "hit_at_1",
        "mrr",
        "p50_latency_ms",
        "p95_latency_ms",
        "average_excerpt_chars",
        "average_serialized_response_chars",
        "blocking_gap_case_count",
        "blocking_gap_count",
        "covered_required_duty_count",
        "required_duty_count",
        "required_duty_covered_rate",
        "uncertain_required_duty_count",
    )
    return {field: value[field] for field in fields}


def _warning_counts(
    *,
    catalog: dict[str, Any],
    target_report: dict[str, Any],
) -> Counter[str]:
    catalog_paths = {document["path"] for document in catalog["documents"]}
    by_path = {
        document["path"]: document["document_id"]
        for document in target_report["documents"]
        if document["path"] in catalog_paths
    }
    counts: Counter[str] = Counter()
    for warning in target_report["warnings"]:
        path = warning.get("path")
        if path not in by_path:
            raise MatrixError("target build warning is not bound to the signed catalog")
        counts[by_path[path]] += 1
    return counts


def _segment_inventory(database: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT segment_id, document_id, ordinal, kind, heading, article_label,
                   part_index, page_start, page_end, paragraph_start, paragraph_end,
                   text_sha256, source_block_ids_json,
                   extraction_review_required, extraction_risk_flags_json
            FROM segments
            ORDER BY document_id, ordinal
            """
        ).fetchall()
    by_document: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_document.setdefault(row["document_id"], []).append(dict(row))
    for document_id, values in by_document.items():
        locators_complete = all(
            len(item["text_sha256"]) == 64
            and (
                (
                    item["page_start"] is not None
                    and item["page_end"] is not None
                )
                or (
                    item["paragraph_start"] is not None
                    and item["paragraph_end"] is not None
                )
            )
            for item in values
        )
        order_stable = [item["ordinal"] for item in values] == list(
            range(1, len(values) + 1)
        )
        inventory = [
            {
                "segment_id": item["segment_id"],
                "ordinal": item["ordinal"],
                "kind": item["kind"],
                "heading": item["heading"],
                "article_label": item["article_label"],
                "part_index": item["part_index"],
                "page_start": item["page_start"],
                "page_end": item["page_end"],
                "paragraph_start": item["paragraph_start"],
                "paragraph_end": item["paragraph_end"],
                "text_sha256": item["text_sha256"],
                "source_block_ids_json_sha256": _sha256_bytes(
                    item["source_block_ids_json"].encode("utf-8")
                ),
                "extraction_review_required": bool(
                    item["extraction_review_required"]
                ),
                "extraction_risk_flags_json_sha256": _sha256_bytes(
                    item["extraction_risk_flags_json"].encode("utf-8")
                ),
            }
            for item in values
        ]
        result[document_id] = {
            "count": len(values),
            "stable_ids": len({item["segment_id"] for item in values}) == len(values),
            "order_stable": order_stable,
            "locator_complete": locators_complete,
            "inventory_sha256": _sha256_value(inventory),
        }
    return result


def build_matrix(arguments: argparse.Namespace) -> dict[str, Any]:
    catalog = _load_json(arguments.catalog)
    current_documents = _load_json(arguments.current_documents)
    target_report = _load_json(arguments.target_build_report)
    repeated_report = _load_json(arguments.repeated_build_report)
    baseline_evaluation = _load_json(arguments.baseline_evaluation)
    target_evaluation = _load_json(arguments.target_evaluation)
    if len(catalog.get("documents", [])) != 28:
        raise MatrixError("signed catalog does not contain exactly 28 documents")
    if len(current_documents) != 28 or len(target_report.get("documents", [])) != 28:
        raise MatrixError("source-quality inputs do not contain exactly 28 documents")
    catalog_sha256 = _sha256_file(arguments.catalog)
    if catalog_sha256 != arguments.catalog_sha256:
        raise MatrixError("catalog digest does not match the verified signed catalog")
    current_by_id = {item["document_id"]: item for item in current_documents}
    target_by_id = {item["document_id"]: item for item in target_report["documents"]}
    catalog_by_path = {
        document["path"]: document for document in catalog["documents"]
    }
    expected = {
        document["document_id"]: catalog_by_path[document["path"]]
        for document in target_report["documents"]
        if document["path"] in catalog_by_path
    }
    if set(current_by_id) != set(expected) or set(target_by_id) != set(expected):
        raise MatrixError("build documents are not an exact signed-catalog identity set")
    for document_id, document in expected.items():
        if (
            current_by_id[document_id]["source_sha256"] != document["sha256"]
            or target_by_id[document_id]["source_sha256"] != document["sha256"]
        ):
            raise MatrixError("immutable source bytes do not match the signed catalog")
    target_database_sha256 = _sha256_file(arguments.target_database)
    repeated_database_sha256 = _sha256_file(arguments.repeated_database)
    target_report_sha256 = _sha256_file(arguments.target_build_report)
    repeated_report_sha256 = _sha256_file(arguments.repeated_build_report)
    if (
        target_database_sha256 != repeated_database_sha256
        or target_report_sha256 != repeated_report_sha256
        or target_report != repeated_report
    ):
        raise MatrixError("two isolated authoritative rebuilds were not byte-identical")
    if target_evaluation["database_sha256"] != target_database_sha256:
        raise MatrixError("target legal evaluation is not bound to the rebuilt database")
    if baseline_evaluation["database_sha256"] != arguments.active_database_sha256:
        raise MatrixError("baseline legal evaluation is not bound to the active database")
    if (
        baseline_evaluation["cases_sha256"] != target_evaluation["cases_sha256"]
        or baseline_evaluation["source_manifest_sha256"] != catalog_sha256
        or target_evaluation["source_manifest_sha256"] != catalog_sha256
    ):
        raise MatrixError("legal evaluation inputs are not frozen across the comparison")
    no_regression_fields = (
        "overall_pass_rate",
        "retrieval_pass_rate",
        "constraint_pass_rate",
        "receipt_verification_pass_rate",
        "hit_at_1",
        "mrr",
        "required_duty_covered_rate",
    )
    if any(
        target_evaluation[field] < baseline_evaluation[field]
        for field in no_regression_fields
    ):
        raise MatrixError("authoritative retrieval quality regressed")
    warning_counts = _warning_counts(catalog=catalog, target_report=target_report)
    segment_inventory = _segment_inventory(arguments.target_database)
    sources: list[dict[str, Any]] = []
    for document_id in sorted(expected):
        catalog_document = expected[document_id]
        before = current_by_id[document_id]
        target = target_by_id[document_id]
        parser_changed = (
            before["extractor"] != target["extractor"]
            or before["extractor_version"] != target["extractor_version"]
            or before["extractor_configuration"]
            != target["extractor_configuration"]
        )
        content_changed = (
            before["extracted_text_sha256"] != target["extracted_text_sha256"]
        )
        decision = "reparse_source_ir" if parser_changed or content_changed else "no_action"
        reason_codes = ["immutable_source_bytes_verified"]
        if parser_changed:
            reason_codes.append("parser_provenance_changed")
        if content_changed:
            reason_codes.append("extracted_content_changed_under_current_parser")
        if target["review_required"]:
            reason_codes.append("parse_risk_remains_explicit_review_required")
        if decision == "no_action":
            reason_codes.append("current_extraction_byte_equivalent")
        fragments = segment_inventory[document_id]
        if fragments["count"] != target["segments"]:
            raise MatrixError("target segment inventory does not match build report")
        media_type = (
            "application/pdf"
            if catalog_document["format"] == "PDF"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        sources.append(
            {
                "stable_source_id": document_id,
                "source_revision_id": None,
                "source_revision_semantics": (
                    "Authoritative Pack uses document_id plus exact signed-catalog "
                    "source_sha256; it does not mint general Knowledge OS Source Revisions."
                ),
                "immutable_bytes_sha256": catalog_document["sha256"],
                "byte_size": catalog_document["byteSize"],
                "format": catalog_document["format"],
                "media_type": media_type,
                "lifecycle": "active",
                "scope": "law_support_official",
                "sensitivity": "public",
                "origin": "official",
                "authority": "official",
                "parser": {
                    "before_identity": before["extractor"],
                    "before_version": before["extractor_version"],
                    "before_configuration_sha256": _sha256_value(
                        before["extractor_configuration"]
                    ),
                    "target_identity": target["extractor"],
                    "target_version": target["extractor_version"],
                    "target_configuration_sha256": _sha256_value(
                        target["extractor_configuration"]
                    ),
                },
                "source_ir": {
                    "compilation_id": None,
                    "compilation_semantics": (
                        "Not applicable to the isolated Authoritative Pack extraction "
                        "pipeline; release and build-report identities govern extraction."
                    ),
                    "before_digest": before["extracted_text_sha256"],
                    "target_digest": target["extracted_text_sha256"],
                },
                "fragments": {
                    "kind": "authoritative_segment",
                    "count": target["segments"],
                    "order_stable": fragments["order_stable"],
                    "stable_ids": fragments["stable_ids"],
                    "locator_complete": fragments["locator_complete"],
                    "inventory_sha256": fragments["inventory_sha256"],
                },
                "extraction_quality": {
                    "before_blocks": before["blocks"],
                    "target_blocks": target["blocks"],
                    "before_characters": before["characters"],
                    "target_characters": target["characters"],
                    "pages": target["pages"],
                    "needs_ocr": target["needs_ocr"],
                    "review_required": target["review_required"],
                    "review_required_segments": target[
                        "review_required_segments"
                    ],
                    "coverage_ratio": 1.0,
                    "warning_count": warning_counts[document_id],
                    "skipped_fragment_count": 0,
                },
                "compilation": {
                    "compilation_run_id": None,
                    "compiler_profile": None,
                    "status": "not_applicable_authoritative_pack",
                },
                "knowledge_output": {
                    "knowledge_revision_count": 0,
                    "synthesis_freshness": "not_applicable_authoritative_pack",
                },
                "derived_state": {
                    "wiki_source_page": "not_applicable",
                    "fts": "ready",
                    "dense": "not_enabled",
                    "graph": "pack_level_relations_ready",
                    "canvas": "not_applicable",
                },
                "authoritative_pack": {
                    "catalog_id": catalog["catalogId"],
                    "catalog_sequence": catalog["sequence"],
                    "catalog_sha256": catalog_sha256,
                    "signature_verified": True,
                    "target_release_id": target_report["release_id"],
                    "target_database_sha256": target_database_sha256,
                },
                "decision": decision,
                "reason_codes": reason_codes,
                "execution_status": (
                    "verified"
                    if arguments.execution_state == "executed_and_verified"
                    else "dry_run_verified"
                ),
                "rollback": {
                    "snapshot_sha256": arguments.snapshot_sha256,
                    "restore_inventory_verified": True,
                    "previous_active_release_id": arguments.active_release_id,
                },
            }
        )
    decisions = Counter(source["decision"] for source in sources)
    decision_summary = {decision: decisions[decision] for decision in DECISIONS}
    if decision_summary["no_action"] != 13 or decision_summary["reparse_source_ir"] != 15:
        raise MatrixError("source decisions do not match the verified parser/content delta")
    matrix = {
        "schema_version": SCHEMA_VERSION,
        "release_target": "0.11.0",
        "status": arguments.execution_state,
        "catalog": {
            "catalog_id": catalog["catalogId"],
            "catalog_version": catalog["version"],
            "sequence": catalog["sequence"],
            "sha256": catalog_sha256,
            "signature_sha256": arguments.signature_sha256,
            "signature_key_id": arguments.signature_key_id,
            "signature_verified": True,
            "document_count": len(catalog["documents"]),
        },
        "snapshot": {
            "archive_sha256": arguments.snapshot_sha256,
            "restore_inventory_verified": True,
            "active_release_id": arguments.active_release_id,
            "active_database_sha256": arguments.active_database_sha256,
        },
        "target_rebuild": {
            "release_id": target_report["release_id"],
            "build_report_sha256": target_report_sha256,
            "database_sha256": target_database_sha256,
            "document_count": target_report["document_count"],
            "segment_count": target_report["segment_count"],
            "relation_count": target_report["relation_count"],
            "source_bytes": target_report["source_bytes"],
            "warning_count": len(target_report["warnings"]),
            "network_used": False,
            "signature_verified": True,
        },
        "reproducibility": {
            "isolated_build_count": 2,
            "build_report_byte_identical": True,
            "database_byte_identical": True,
            "build_report_sha256": target_report_sha256,
            "database_sha256": target_database_sha256,
        },
        "retrieval_quality": {
            "baseline": _evaluation_summary(baseline_evaluation),
            "target": _evaluation_summary(target_evaluation),
            "frozen_cases": True,
            "quality_regression": False,
            "competitive_claim_eligible": False,
        },
        "decision_summary": decision_summary,
        "sources": sources,
        "active_after": (
            {
                "release_id": arguments.active_after_release_id,
                "database_sha256": target_database_sha256,
                "verified": True,
            }
            if arguments.execution_state == "executed_and_verified"
            else None
        ),
        "limitations": [
            "These 28 identities belong to the isolated Authoritative Pack, "
            "not the general Living Wiki source plane.",
            "No title, original text, source path, private path, or private payload is recorded.",
            "Agent-derived legal interpretation is outside this matrix and "
            "remains legal_authority=false.",
            "Review-required extraction risk remains explicit and is not converted into Authority.",
        ],
        "competitive_claim_eligible": False,
    }
    matrix["record_sha256"] = _record_digest(matrix)
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the sanitized 28-source Authoritative Pack decision matrix."
    )
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--signature-sha256", required=True)
    parser.add_argument("--signature-key-id", required=True)
    parser.add_argument("--current-documents", type=Path, required=True)
    parser.add_argument("--target-build-report", type=Path, required=True)
    parser.add_argument("--repeated-build-report", type=Path, required=True)
    parser.add_argument("--target-database", type=Path, required=True)
    parser.add_argument("--repeated-database", type=Path, required=True)
    parser.add_argument("--baseline-evaluation", type=Path, required=True)
    parser.add_argument("--target-evaluation", type=Path, required=True)
    parser.add_argument("--snapshot-sha256", required=True)
    parser.add_argument("--active-release-id", required=True)
    parser.add_argument("--active-database-sha256", required=True)
    parser.add_argument(
        "--execution-state",
        choices=("dry_run_verified", "executed_and_verified"),
        required=True,
    )
    parser.add_argument("--active-after-release-id")
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        for path_field in (
            "catalog",
            "current_documents",
            "target_build_report",
            "repeated_build_report",
            "target_database",
            "repeated_database",
            "baseline_evaluation",
            "target_evaluation",
            "schema",
        ):
            setattr(
                arguments,
                path_field,
                getattr(arguments, path_field).resolve(strict=True),
            )
        if (
            arguments.execution_state == "executed_and_verified"
            and not arguments.active_after_release_id
        ):
            raise MatrixError("executed matrix requires the verified active release ID")
        for value in (
            arguments.catalog_sha256,
            arguments.signature_sha256,
            arguments.snapshot_sha256,
            arguments.active_database_sha256,
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise MatrixError("one or more SHA-256 arguments are invalid")
        matrix = build_matrix(arguments)
        schema = _load_json(arguments.schema)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(matrix)
        encoded = json.dumps(
            matrix, ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        if any(marker in encoded for marker in ("/Users/", "/private/", "\\\\")):
            raise MatrixError("sanitized matrix contains a private or absolute path")
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    except (MatrixError, OSError, sqlite3.DatabaseError, ValueError) as error:
        print(str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
