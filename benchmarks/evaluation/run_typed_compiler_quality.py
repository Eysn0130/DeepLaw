from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from benchmarks.typed_compiler.score import score_suite
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import (
    canonical_json,
    sha256_bytes,
    sha256_file,
    strict_json_loads,
)

SUITE_SCHEMA = "deeplaw.typed-compiler-quality-suite/v1"
REPORT_SCHEMA = "deeplaw.typed-compiler-quality-report/v1"
_MAX_SUITE_BYTES = 2 * 1024 * 1024


def _load_suite(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not 1 <= resolved.stat().st_size <= _MAX_SUITE_BYTES:
        raise ValueError("Typed Compiler quality suite is not a bounded regular file")
    suite = strict_json_loads(resolved.read_bytes())
    if not isinstance(suite, dict) or set(suite) != {
        "schema_version",
        "suite_id",
        "status",
        "frozen_at",
        "compiler_identity",
        "network_policy",
        "quality_gate",
        "sources",
        "limitations",
    }:
        raise ValueError("Typed Compiler quality suite does not match its closed contract")
    if (
        suite["schema_version"] != SUITE_SCHEMA
        or suite["suite_id"] != "deeplaw-public-typed-compiler-gold-v1"
        or suite["status"] != "public_time_frozen_benchmark"
        or suite["compiler_identity"] != "deeplaw-deterministic/v2"
        or suite["network_policy"] != "offline"
    ):
        raise ValueError("Typed Compiler quality suite governance is invalid")
    source_ids: set[str] = set()
    file_names: set[str] = set()
    claim_ids: set[str] = set()
    for source in suite["sources"]:
        if source["source_id"] in source_ids or source["file_name"] in file_names:
            raise ValueError("Typed Compiler quality suite contains duplicate sources")
        source_ids.add(source["source_id"])
        file_names.add(source["file_name"])
        if (
            sha256_bytes(source["content"].encode("utf-8"))
            != source["content_sha256"]
        ):
            raise ValueError("Typed Compiler source content hash changed")
        for claim in source["gold_claims"]:
            if claim["claim_id"] in claim_ids:
                raise ValueError("Typed Compiler quality suite contains duplicate claims")
            claim_ids.add(claim["claim_id"])
    return suite


def _prediction_id(source_id: str, index: int) -> str:
    return f"prediction-{source_id}-{index:03d}"


def run_suite(path: str | Path) -> dict[str, Any]:
    selected = Path(path)
    suite = _load_suite(selected)
    benchmark_cases: list[dict[str, Any]] = []
    with TemporaryDirectory(prefix="deeplaw-typed-evaluation-") as temporary:
        root = Path(temporary)
        vault_root = root / "vault"
        initialize_knowledge_vault(vault_root, name="typed-compiler-eval", scope="project")
        with KnowledgeVault(vault_root, read_only=False) as vault:
            for source in suite["sources"]:
                source_path = root / source["file_name"]
                source_path.write_text(source["content"], encoding="utf-8")
                result = compile_source(
                    vault,
                    source_path,
                    source_kind="document",
                    confirm_no_case_data=True,
                    typed_extraction="deterministic-v2",
                )
                typed_assets = [
                    vault.get_asset(asset_id, include_inactive=True)
                    for asset_id in result["asset_ids"]
                ]
                typed_assets = [asset for asset in typed_assets if asset.kind != "reference"]
                expected_refs = {
                    (claim["locator"], claim["quote_sha256"]): f"gold-{claim['claim_id']}"
                    for claim in source["gold_claims"]
                }
                source_refs = [
                    {
                        "ref_id": ref_id,
                        "source_id": source["source_id"],
                        "locator": locator,
                        "text_sha256": quote_sha256,
                    }
                    for (locator, quote_sha256), ref_id in expected_refs.items()
                ]
                observed_refs: dict[tuple[str, str], str] = {}
                for asset in typed_assets:
                    for ref in asset.source_refs:
                        key = (ref.locator, ref.quote_sha256)
                        if key in expected_refs or key in observed_refs:
                            continue
                        ref_id = f"observed-{source['source_id']}-{len(observed_refs):03d}"
                        observed_refs[key] = ref_id
                        source_refs.append(
                            {
                                "ref_id": ref_id,
                                "source_id": source["source_id"],
                                "locator": ref.locator,
                                "text_sha256": ref.quote_sha256,
                            }
                        )
                gold_by_content = {
                    (claim["kind"], claim["title"], claim["statement"]): claim
                    for claim in source["gold_claims"]
                }
                predictions: list[dict[str, Any]] = []
                for index, asset in enumerate(typed_assets, start=1):
                    key = (asset.kind, asset.title, asset.statement)
                    matched = gold_by_content.get(key)
                    prediction_ref_ids = []
                    for ref in asset.source_refs:
                        ref_key = (ref.locator, ref.quote_sha256)
                        prediction_ref_ids.append(
                            expected_refs[ref_key]
                            if ref_key in expected_refs
                            else observed_refs[ref_key]
                        )
                    exact_refs = bool(
                        matched
                        and prediction_ref_ids == [f"gold-{matched['claim_id']}"]
                    )
                    predictions.append(
                        {
                            "prediction_id": _prediction_id(
                                source["source_id"], index
                            ),
                            "kind": asset.kind,
                            "statement": asset.statement,
                            "source_ref_ids": prediction_ref_ids,
                            "matched_gold_claim_id": (
                                matched["claim_id"] if matched else None
                            ),
                            "claim_equivalent": matched is not None,
                            "support_label": (
                                "supported"
                                if exact_refs
                                else "unsupported"
                                if matched
                                else "hallucinated"
                            ),
                            "review_decision": "accept" if exact_refs else "reject",
                            "cross_document": False,
                        }
                    )
                benchmark_cases.append(
                    {
                        "case_id": source["source_id"],
                        "source_refs": source_refs,
                        "gold_claims": [
                            {
                                "claim_id": claim["claim_id"],
                                "kind": claim["kind"],
                                "statement": claim["statement"],
                                "source_ref_ids": [f"gold-{claim['claim_id']}"],
                                "cross_document": False,
                            }
                            for claim in source["gold_claims"]
                        ],
                        "predicted_claims": predictions,
                    }
                )
        scorer_input = {
            "schema_version": "deeplaw.typed-compiler-benchmark-input/v1",
            "suite_id": suite["suite_id"],
            "candidate_line": "evaluation-protocol-v1",
            "compiler_identity": suite["compiler_identity"],
            "cases": benchmark_cases,
            "claim_eligible": False,
            "limitations": suite["limitations"],
        }
        scorer_path = root / "scorer-input.json"
        scorer_path.write_text(
            json.dumps(scorer_input, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        scorer_report = score_suite(scorer_path)

    metrics = scorer_report["metrics"]
    thresholds = suite["quality_gate"]
    checks = {
        "precision": metrics["precision"] >= thresholds["minimum_precision"],
        "recall": metrics["recall"] >= thresholds["minimum_recall"],
        "f1": metrics["f1"] >= thresholds["minimum_f1"],
        "hallucinated_claim_rate": metrics["hallucinated_claim_rate"]
        <= thresholds["maximum_hallucinated_claim_rate"],
        "unsupported_claim_rate": metrics["unsupported_claim_rate"]
        <= thresholds["maximum_unsupported_claim_rate"],
        "source_span_correctness": metrics["source_span_correctness"]
        >= thresholds["minimum_source_span_correctness"],
        "duplicate_claim_rate": metrics["duplicate_claim_rate"]
        <= thresholds["maximum_duplicate_claim_rate"],
    }
    report = {
        "schema_version": REPORT_SCHEMA,
        "suite_id": suite["suite_id"],
        "suite_sha256": sha256_file(selected.resolve(strict=True)),
        "compiler_identity": suite["compiler_identity"],
        "network_policy": suite["network_policy"],
        "source_count": len(suite["sources"]),
        "case_count": len(benchmark_cases),
        "scorer_report": scorer_report,
        "quality_gate": {
            "thresholds": thresholds,
            "checks": checks,
            "passed": all(checks.values()),
        },
        "public_benchmark": True,
        "comparative_superiority_claim_eligible": False,
        "limitations": suite["limitations"],
    }
    report["report_sha256"] = sha256_bytes(canonical_json(report).encode("utf-8"))
    return report
