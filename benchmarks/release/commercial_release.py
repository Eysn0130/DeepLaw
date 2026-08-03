from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.evaluation.run_protocol import verify_report_directory
from benchmarks.living_wiki.compare_quality import compare as compare_living_wiki_quality
from benchmarks.release.evidence import (
    environment_manifest,
    file_record,
    load_json,
    repository_binding,
    verify_record_digest,
    write_report,
)
from benchmarks.semantic.build_machine_review_consensus import (
    candidate_binding as semantic_candidate_binding,
)
from benchmarks.semantic.build_machine_review_consensus import (
    validate_packet as validate_machine_review_packet,
)
from benchmarks.semantic.review_gold import validate_candidate
from deeplaw.catalog_signing import verify_catalog_signature
from deeplaw.util import canonical_json

SCHEMA_VERSION = "deeplaw.commercial-release-manifest/v5"
LIVING_WIKI_BASELINE_COMMIT = "42382b264f4297965c25aaac6e85619e9e0d49b7"
LIVING_WIKI_BASELINE_WHEEL_SHA256 = (
    "9bda60831e4380092c9a3bdb80103b5ec8abbf5a2be0adf6ffd57f61cfa46ca0"
)
COMPETITIVE_EVIDENCE_MISSING = [
    "named_baseline_results_17",
    "paired_confidence_intervals",
    "comparative_failure_and_cost_inventory",
]


class CommercialReleaseError(RuntimeError):
    pass


def _same_binding(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    scalar_binding_matches = all(
        expected.get(field) == observed.get(field)
        for field in ("commit", "tree", "package_version", "lock_sha256", "pyproject_sha256")
    )
    return (
        scalar_binding_matches
        and expected.get("contracts", {}).get("inventory_sha256")
        == observed.get("contracts", {}).get("inventory_sha256")
        and expected.get("migrations", {}).get("inventory_sha256")
        == observed.get("migrations", {}).get("inventory_sha256")
    )


def _require_report(
    path: Path,
    *,
    schema_version: str,
    binding: dict[str, Any],
    field: str,
) -> dict[str, Any]:
    report = load_json(path)
    verify_record_digest(report, field=field)
    if report.get("schema_version") != schema_version:
        raise CommercialReleaseError(f"{field} schema is unsupported")
    if not _same_binding(binding, report.get("binding", {})):
        raise CommercialReleaseError(f"{field} targets a different release commit")
    if report.get("passed") is not True:
        raise CommercialReleaseError(f"{field} did not pass")
    return report


def _unified_versions(repository: Path) -> dict[str, str]:
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    values = {
        "package": project["project"]["version"],
        "python": __import__("deeplaw").__version__,
        "claude_marketplace": load_json(repository / ".claude-plugin/marketplace.json")["version"],
        "claude_legal": load_json(repository / "plugins/deeplaw/.claude-plugin/plugin.json")[
            "version"
        ],
        "claude_knowledge": load_json(
            repository / "plugins/deeplaw-knowledge-os/.claude-plugin/plugin.json"
        )["version"],
        "codex_legal": load_json(repository / "plugins/deeplaw/.codex-plugin/plugin.json")[
            "version"
        ],
        "codex_knowledge": load_json(
            repository / "plugins/deeplaw-knowledge-os/.codex-plugin/plugin.json"
        )["version"],
        "opencode_adapter": load_json(repository / "adapters/opencode/manifest.json")["version"],
        "obsidian_manifest": load_json(repository / "adapters/obsidian/plugin/manifest.json")[
            "version"
        ],
        "obsidian_package": load_json(repository / "adapters/obsidian/plugin/package.json")[
            "version"
        ],
    }
    if set(values.values()) != {version}:
        raise CommercialReleaseError(f"release versions are not unified: {values}")
    marketplace = load_json(repository / ".claude-plugin/marketplace.json")
    if {item.get("version") for item in marketplace.get("plugins", [])} != {version}:
        raise CommercialReleaseError("Claude marketplace entries do not match the package version")
    return values


def _artifact_inventory(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise CommercialReleaseError("release assets contain a symbolic link")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "commercial-release-manifest.json":
            continue
        records.append(file_record(path, logical_name=relative))
        records[-1]["path"] = records[-1].pop("logical_name")
    if len(records) < 24:
        raise CommercialReleaseError("release asset inventory is incomplete")
    return records


def _sbom(path: Path, *, version: str) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("bomFormat") != "CycloneDX" or payload.get("specVersion") not in {
        "1.5",
        "1.6",
    }:
        raise CommercialReleaseError("release SBOM is not CycloneDX 1.5/1.6")
    components = payload.get("components")
    metadata_component = payload.get("metadata", {}).get("component")
    candidates = [metadata_component, *(components if isinstance(components, list) else [])]
    if not any(
        isinstance(item, dict) and item.get("name") == "deeplaw" and item.get("version") == version
        for item in candidates
    ):
        raise CommercialReleaseError("release SBOM does not bind the package version")
    return {
        "format": "CycloneDX",
        "spec_version": payload["specVersion"],
        "component_count": len(components) if isinstance(components, list) else 0,
    }


def _licenses(path: Path, *, binding: dict[str, Any]) -> dict[str, Any]:
    payload = load_json(path)
    verify_record_digest(payload, field="installed license inventory")
    if (
        payload.get("schema_version") != "deeplaw.installed-license-inventory/v1"
        or payload.get("status") != "passed"
        or payload.get("blocked") != []
        or payload.get("review_required") != []
        or not _same_binding(binding, payload.get("binding", {}))
    ):
        raise CommercialReleaseError("installed license inventory did not pass")
    return {"status": "passed", "package_count": payload.get("package_count")}


def _openvex(path: Path, *, version: str) -> dict[str, Any]:
    payload = load_json(path)
    statements = payload.get("statements")
    if not isinstance(statements, list) or not statements:
        raise CommercialReleaseError("OpenVEX has no statements")
    expected = f"pkg:pypi/deeplaw@{version}"
    for statement in statements:
        products = statement.get("products") if isinstance(statement, dict) else None
        if not isinstance(products, list) or expected not in {
            item.get("@id") for item in products if isinstance(item, dict)
        }:
            raise CommercialReleaseError("OpenVEX statement is not bound to the package version")
    return {"statement_count": len(statements), "product": expected}


def _docs(repository: Path) -> dict[str, bool]:
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    major, minor, _patch = version.split(".")
    acceptance = f"docs/V{major}_{minor}_ACCEPTANCE_MATRIX.md"
    release_notes = f"docs/RELEASE_NOTES_v{version}.md"
    required = {
        "README.md": ("本地单用户 Agent Knowledge OS", "Knowledge Capsule", f"v{version}"),
        "README_EN.md": (
            "Local single-user Agent Knowledge OS",
            "Knowledge Capsule",
            f"v{version}",
        ),
        "CHANGELOG.md": (version, "competitive_claim_eligible=false"),
        "SECURITY.md": (
            f"v{version}",
            "commercial_release_eligible=true",
            "quality_protocol_eligible=true",
        ),
        "docs/INSTALL_UPGRADE_ROLLBACK.md": ("0.6.0", version),
        acceptance: (
            "commercial_release_eligible=true",
            "quality_protocol_eligible=true",
            "competitive_claim_eligible=false",
        ),
        release_notes: (
            "commercial_release_eligible=true",
            "quality_protocol_eligible=true",
            "competitive_claim_eligible=false",
        ),
    }
    result: dict[str, bool] = {}
    for relative, markers in required.items():
        text = (repository / relative).read_text(encoding="utf-8")
        if any(marker not in text for marker in markers):
            raise CommercialReleaseError(f"release documentation is incomplete: {relative}")
        positioning_text = text.casefold().replace(
            "commercial-release-manifest.json", "release-manifest.json"
        )
        if relative in {"README.md", "README_EN.md"} and (
            "商业" in text or "commercial" in positioning_text
        ):
            raise CommercialReleaseError(
                f"public repository homepage contains release-positioning copy: {relative}"
            )
        result[relative] = True
    return result


def _living_wiki_quality(
    repository: Path,
    path: Path,
    *,
    binding: dict[str, Any],
    wheel_hashes: set[str],
) -> dict[str, Any]:
    report = load_json(path)
    verify_record_digest(report, field="Living Wiki quality report")
    schema = load_json(repository / "contracts/living-wiki-quality-report.v1.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)
    candidate = report.get("candidate", {})
    security = report.get("security", {})
    cli_coverage = report.get("cli_coverage", {})
    gate_checks = report.get("retrieval", {}).get("gate_checks", {})
    required_cli_checks = (
        "source_list_get_verify_fragment_diff",
        "compilation_status_explain",
        "freshness_contradictions_gaps",
        "context",
        "verify",
        "law_support_boundary",
    )
    if (
        report.get("schema_version") != "deeplaw.living-wiki-quality-report/v1"
        or report.get("passed") is not True
        or report.get("competitive_claim_eligible") is not False
        or candidate.get("role") != "fresh_wheel"
        or candidate.get("commit") != binding["commit"]
        or candidate.get("version") != binding["package_version"]
        or candidate.get("artifact_sha256") not in wheel_hashes
        or not cli_coverage
        or not all(cli_coverage.get(field) is True for field in required_cli_checks)
        or not gate_checks
        or not all(value is True for value in gate_checks.values())
        or security.get("unauthorized_disclosure") != 0
        or security.get("silent_fallback") != 0
        or security.get("stale_prohibited_selection") != 0
        or security.get("invalid_official_citation") != 0
        or security.get("provider_hard_limit_violation") != 0
        or security.get("authority_elevation_by_ranking_or_model") != 0
        or security.get("unauthorized_write_rejected") is not True
    ):
        raise CommercialReleaseError("Living Wiki fresh-wheel quality gate did not pass")
    suite_path = repository / "benchmarks/living_wiki/quality-suite-v1.json"
    runner_path = repository / "benchmarks/living_wiki/run_quality_gate.py"
    if (
        report.get("suite", {}).get("suite_sha256") != file_record(suite_path)["sha256"]
        or report.get("suite", {}).get("runner_sha256") != file_record(runner_path)["sha256"]
    ):
        raise CommercialReleaseError("Living Wiki quality report uses a different suite")
    return report


def _living_wiki_comparison(
    repository: Path,
    *,
    baseline_path: Path,
    comparison_path: Path,
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    report_schema = load_json(repository / "contracts/living-wiki-quality-report.v1.schema.json")
    comparison_schema = load_json(
        repository / "contracts/living-wiki-quality-comparison.v1.schema.json"
    )
    Draft202012Validator.check_schema(report_schema)
    Draft202012Validator.check_schema(comparison_schema)
    baseline = load_json(baseline_path)
    comparison = load_json(comparison_path)
    verify_record_digest(baseline, field="Living Wiki baseline quality report")
    verify_record_digest(comparison, field="Living Wiki quality comparison")
    Draft202012Validator(report_schema).validate(baseline)
    Draft202012Validator(comparison_schema).validate(comparison)
    baseline_candidate = baseline.get("candidate", {})
    if (
        baseline.get("schema_version") != "deeplaw.living-wiki-quality-report/v1"
        or baseline.get("competitive_claim_eligible") is not False
        or baseline_candidate.get("role") != "baseline"
        or baseline_candidate.get("commit") != LIVING_WIKI_BASELINE_COMMIT
        or baseline_candidate.get("version") != "0.10.0"
        or baseline_candidate.get("artifact_sha256") != LIVING_WIKI_BASELINE_WHEEL_SHA256
        or baseline.get("suite", {}).get("suite_sha256")
        != candidate.get("suite", {}).get("suite_sha256")
        or baseline.get("suite", {}).get("runner_sha256")
        != candidate.get("suite", {}).get("runner_sha256")
    ):
        raise CommercialReleaseError("Living Wiki baseline is not the frozen candidate")
    expected_comparison = compare_living_wiki_quality(baseline, candidate)
    if comparison != expected_comparison:
        raise CommercialReleaseError("Living Wiki baseline comparison is not reproducible")
    return baseline, comparison


def _source_quality_matrix(
    repository: Path,
    path: Path,
    *,
    binding: dict[str, Any],
    artifact_sha256: str,
) -> dict[str, Any]:
    matrix = load_json(path)
    verify_record_digest(matrix, field="28-source decision matrix")
    schema = load_json(
        repository / "contracts/authoritative-source-quality-decision-matrix.v2.schema.json"
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(matrix)
    sources = matrix.get("sources", [])
    catalog_path = repository / "catalogs/deeplaw-official-cn.json"
    signature_path = repository / "catalogs/deeplaw-official-cn.json.sig"
    signature_verification = verify_catalog_signature(
        catalog_path.read_bytes(),
        signature_path.read_bytes(),
        trust_store_path=repository / "trust/official-catalog-keys.v1.json",
    )
    catalog = load_json(catalog_path)
    expected_sources = sorted(
        (item["sha256"], item["byteSize"], item["format"]) for item in catalog.get("documents", [])
    )
    observed_sources = sorted(
        (
            item.get("immutable_bytes_sha256"),
            item.get("byte_size"),
            item.get("format"),
        )
        for item in sources
    )
    security = matrix.get("security", {})
    security_failures = (
        "unauthorized_disclosure",
        "restricted_disclosure",
        "unauthorized_mutation",
        "silent_fallback",
        "stale_prohibited_selection",
        "invalid_official_citation",
        "unsupported_authoritative_claim",
        "authority_elevation",
        "provider_hard_limit_violation",
    )
    required_challenges = {
        "unauthorized_disclosure",
        "restricted_disclosure",
        "silent_fallback",
        "unsupported_authoritative_claim",
        "authority_elevation",
        "prompt_injection",
        "tampered_receipt_rejected",
        "tampered_signature_rejected",
        "signed_catalog_rollback_rejected",
        "law_support_mutation_rejected",
    }
    challenge_execution = security.get("challenge_execution", {})
    decision_summary = matrix.get("decision_summary", {})
    if (
        matrix.get("status") != "executed_and_verified"
        or matrix.get("release_target") != binding["package_version"]
        or matrix.get("candidate_binding")
        != {
            "commit": binding["commit"],
            "tree": binding["tree"],
            "version": binding["package_version"],
            "artifact_sha256": artifact_sha256,
        }
        or matrix.get("competitive_claim_eligible") is not False
        or len(sources) != 28
        or len({item.get("stable_source_id") for item in sources}) != 28
        or any(item.get("execution_status") != "verified" for item in sources)
        or matrix.get("active_release", {}).get("verified") is not True
        or matrix.get("retrieval_quality", {}).get("quality_regression") is not False
        or any(security.get(field) != 0 for field in security_failures)
        or security.get("challenge_attempt_count") != 10
        or set(challenge_execution) != required_challenges
        or any(value is not True for value in challenge_execution.values())
        or sum(decision_summary.values()) != 28
        or decision_summary.get("blocked_invalid_evidence") != 0
        or matrix.get("snapshot", {}).get("restore_verified") is not True
        or matrix.get("reproducibility", {}).get("database_byte_identical") is not True
        or matrix.get("reproducibility", {}).get("build_report_identical") is not True
        or matrix.get("reproducibility", {}).get("query_result_semantics_identical")
        is not True
        or signature_verification.get("verified") is not True
        or matrix.get("catalog", {}).get("sha256") != signature_verification.get("catalog_sha256")
        or matrix.get("catalog", {}).get("signature_sha256")
        != signature_verification.get("signature_sha256")
        or matrix.get("catalog", {}).get("signature_key_id") != signature_verification.get("key_id")
        or expected_sources != observed_sources
    ):
        raise CommercialReleaseError("28-source decision matrix is incomplete")
    return matrix


def _semantic_quality(
    repository: Path,
    *,
    binding: dict[str, Any],
    gold_path: Path,
    lifecycle_path: Path,
    query_path: Path,
    cost_path: Path,
    consensus_path: Path,
    machine_review_paths: list[Path],
    owner_review_english_path: Path,
    owner_review_chinese_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    gold = load_json(gold_path)
    gold_sha256 = validate_candidate(gold, repository=repository)
    lifecycle = load_json(lifecycle_path)
    query = load_json(query_path)
    cost = load_json(cost_path)
    consensus = load_json(consensus_path)
    for name, value in (
        ("deterministic-semantic-lifecycle.v1.schema.json", lifecycle),
        ("semantic-query-run.v1.schema.json", query),
        ("semantic-query-cost.v1.schema.json", cost),
        ("semantic-machine-review-consensus.v1.schema.json", consensus),
    ):
        schema = load_json(repository / "contracts" / name)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    expected_binding = {
        "commit": binding["commit"],
        "tree": binding["tree"],
        "package_version": binding["package_version"],
        "lock_sha256": binding["lock_sha256"],
        "pyproject_sha256": binding["pyproject_sha256"],
        "contracts_inventory_sha256": binding["contracts"]["inventory_sha256"],
        "migrations_inventory_sha256": binding["migrations"]["inventory_sha256"],
        "worktree_clean": True,
    }
    deterministic_lifecycle_valid = (
        lifecycle.get("binding") == expected_binding
        and lifecycle.get("status") == "passed"
        and lifecycle.get("formal_release_evidence_ready") is False
        and lifecycle.get("external_model_execution") == "not_executed"
        and lifecycle.get("model_identity") is None
        and lifecycle.get("network_policy") == "offline"
        and lifecycle.get("vault_verification_valid") is True
    )
    human_review_policy = {
        "status": "not_required",
        "reason": "owner-approved deterministic machine-consensus release scope",
    }
    release_policy = gold.get("release_review_policy", {})
    if (
        gold.get("status") != "machine_review_pending"
        or release_policy.get("human_gold_review") != human_review_policy
        or release_policy.get("maintainer_confirmed") is not False
        or release_policy.get("reviewer_id") is not None
        or release_policy.get("independent_machine_review", {}).get("status")
        != "pending"
        or release_policy.get("external_real_model_semantic_execution")
        != "not_executed"
        or release_policy.get("competitive_claim_eligible") is not False
        or not deterministic_lifecycle_valid
        or query.get("status") != "passed"
        or query.get("compiler_report_id") != lifecycle.get("report_id")
        or cost.get("compiler_report_id") != lifecycle.get("report_id")
        or query.get("gold_id") != gold.get("gold_id")
        or len(query.get("cases", [])) != 15
        or query.get("metrics", {}).get("passed_count") != 15
        or query.get("cross_packet_identity", {}).get("valid") is not True
        or query.get("cross_packet_identity", {}).get("run_packet_count", 0) < 2
        or len(query.get("cross_packet_identity", {}).get("distinct_packet_ids", []))
        < 2
        or len(query.get("cross_packet_identity", {}).get("final_knowledge_ids", []))
        != 1
        or query.get("competitive_claim_eligible") is not False
        or consensus.get("candidate_binding") != semantic_candidate_binding(repository)
        or consensus.get("machine_review_consensus") != "confirmed"
        or consensus.get("independent_machine_review", {}).get("status") != "confirmed"
        or consensus.get("human_gold_review") != human_review_policy
        or consensus.get("maintainer_confirmed") is not False
        or consensus.get("reviewer_id") is not None
        or consensus.get("external_real_model_semantic_execution") != "not_executed"
        or consensus.get("competitive_claim_eligible") is not False
        or consensus.get("candidate_binding", {}).get("gold_canonical_sha256")
        != gold_sha256
    ):
        raise CommercialReleaseError(
            "Semantic Living Wiki deterministic machine-consensus gate did not pass"
        )
    safety_fields = (
        "provider_hard_limit_violations",
        "unauthorized_writes",
        "authority_elevations",
        "invalid_official_citations",
        "silent_fallbacks",
        "stale_prohibited_selections",
        "prompt_injection_failures",
        "unsupported_authoritative_claims",
        "restricted_disclosures",
        "unauthorized_mutation_failures",
        "silent_fallback_challenge_failures",
    )
    metrics = query.get("metrics", {})
    if (
        any(metrics.get(field) != 0 for field in safety_fields)
        or metrics.get("citation_validity") != 1
        or metrics.get("claim_evidence_binding_accuracy") != 1
        or metrics.get("recall_at_k") != 1
        or metrics.get("target_scoped_precision_at_k") != 1
        or set(metrics.get("challenge_execution_counts", {}))
        != {
            "prompt_injection",
            "unsupported_authoritative_claim",
            "restricted_disclosure",
            "unauthorized_mutation",
            "silent_fallback",
        }
        or any(
            count < 1
            for count in metrics.get("challenge_execution_counts", {}).values()
        )
    ):
        raise CommercialReleaseError("Semantic retrieval or safety metrics did not pass")
    packets = [load_json(path) for path in machine_review_paths]
    semantic_binding = semantic_candidate_binding(repository)
    if len(packets) != 6:
        raise CommercialReleaseError("Semantic release requires six machine review packets")
    for packet in packets:
        # Each auditor uses an isolated Vault, so its generated revision IDs and
        # Query Plan receipts intentionally differ from the canonical query run.
        validate_machine_review_packet(
            packet,
            repository=repository,
            binding=semantic_binding,
        )
    packet_by_role = {packet["auditor_role"]: packet for packet in packets}
    consensus_records = {
        item["auditor_role"]: item for item in consensus["auditor_packets"]
    }
    if (
        len(packet_by_role) != 6
        or set(packet_by_role) != set(consensus_records)
        or any(
            consensus_records[role]["packet_sha256"] != packet["packet_sha256"]
            or consensus_records[role]["evidence_sha256"] != packet["evidence_sha256"]
            for role, packet in packet_by_role.items()
        )
    ):
        raise CommercialReleaseError("Machine consensus does not bind the six packet bytes")
    owner_packets = {
        "en": load_json(owner_review_english_path),
        "zh-CN": load_json(owner_review_chinese_path),
    }
    owner_schema = load_json(
        repository / "contracts/semantic-owner-review-packet.v1.schema.json"
    )
    for language, packet in owner_packets.items():
        Draft202012Validator(owner_schema).validate(packet)
        digest = __import__("hashlib").sha256(
            canonical_json(
                {key: value for key, value in packet.items() if key != "packet_sha256"}
            ).encode("utf-8")
        ).hexdigest()
        if (
            packet.get("language") != language
            or packet.get("packet_sha256") != digest
            or packet.get("candidate_binding") != semantic_binding
            or packet.get("machine_review_consensus_sha256")
            != consensus.get("consensus_sha256")
            or packet.get("human_final_decision") != "not_required"
            or packet.get("maintainer_confirmed") is not False
            or packet.get("reviewer_id") is not None
        ):
            raise CommercialReleaseError("Owner review packet binding is invalid")
    if owner_packets["zh-CN"].get("counterpart_packet_sha256") != owner_packets["en"].get(
        "packet_sha256"
    ):
        raise CommercialReleaseError("Chinese Owner packet does not bind the English packet")
    return gold, {
        "lifecycle": lifecycle,
        "query": query,
        "cost": cost,
        "consensus": consensus,
        "packets": packets,
        "owner_packets": owner_packets,
    }


def _authoritative_evidence_quality(
    repository: Path,
    *,
    binding: dict[str, Any],
    quality_path: Path,
    evaluation_path: Path,
) -> dict[str, Any]:
    report = load_json(quality_path)
    verify_record_digest(report, field="Authoritative evidence quality report")
    schema = load_json(repository / "contracts/authoritative-evidence-quality.v1.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)
    expected_binding = {
        "commit": binding["commit"],
        "tree": binding["tree"],
        "package_version": binding["package_version"],
        "lock_sha256": binding["lock_sha256"],
        "pyproject_sha256": binding["pyproject_sha256"],
        "contracts_inventory_sha256": binding["contracts"]["inventory_sha256"],
        "migrations_inventory_sha256": binding["migrations"]["inventory_sha256"],
        "worktree_clean": True,
    }
    expected_schemas = {
        "authoritative_challenge_trace": file_record(
            repository / "contracts/authoritative-challenge-trace.v1.schema.json"
        )["sha256"],
        "authoritative_challenge_replay": file_record(
            repository / "contracts/authoritative-challenge-replay.v1.schema.json"
        )["sha256"],
        "evidence_capabilities": file_record(
            repository / "contracts/evidence-capabilities.v1.schema.json"
        )["sha256"],
        "citation_audit": file_record(repository / "contracts/citation-audit.v1.schema.json")[
            "sha256"
        ],
    }
    if (
        report.get("binding") != expected_binding
        or report.get("frozen_evaluation", {}).get("sha256")
        != file_record(evaluation_path)["sha256"]
        or report.get("schemas") != expected_schemas
        or report.get("passed") is not True
        or report.get("competitive_claim_eligible") is not False
        or not all(report.get("checks", {}).values())
        or any(report.get("security_failures", {}).values())
        or (
            report.get("expert_gold", {}).get("status") == "expert_review_pending"
            and report.get("expert_gold", {}).get("expert_quality_claimed") is not False
        )
    ):
        raise CommercialReleaseError("Authoritative evidence quality gate did not pass")
    return report


def assemble(
    repository: Path,
    *,
    assets_root: Path,
    platform_paths: list[Path],
    host_path: Path,
    reproducible_path: Path,
    oci_report_path: Path,
    audit_paths: list[Path],
    sbom_path: Path,
    licenses_path: Path,
    openvex_path: Path,
    evaluation_path: Path,
    living_wiki_quality_path: Path,
    living_wiki_baseline_path: Path,
    living_wiki_comparison_path: Path,
    source_quality_matrix_path: Path,
    semantic_gold_path: Path,
    semantic_lifecycle_path: Path,
    semantic_query_path: Path,
    semantic_query_cost_path: Path,
    semantic_consensus_path: Path,
    semantic_machine_review_paths: list[Path],
    semantic_owner_review_english_path: Path,
    semantic_owner_review_chinese_path: Path,
    authoritative_evidence_quality_path: Path,
    obsidian_artifact_path: Path,
    tolaria_report_path: Path,
    source_date_epoch: int,
) -> dict[str, Any]:
    binding = repository_binding(repository)
    version = binding["package_version"]
    tag = f"v{version}"
    if not binding["worktree_clean"]:
        raise CommercialReleaseError("commercial manifest requires a clean release commit")
    versions = _unified_versions(repository)
    platform_reports = [
        _require_report(
            path,
            schema_version="deeplaw.platform-release-gate/v1",
            binding=binding,
            field=f"platform report {path.name}",
        )
        for path in platform_paths
    ]
    platform_matrix = sorted(
        (
            report["environment"]["platform_system"],
            ".".join(report["environment"]["python_version"].split(".")[:2]),
        )
        for report in platform_reports
    )
    expected_platform_matrix = sorted(
        (system, python_version)
        for system in ("Darwin", "Linux", "Windows")
        for python_version in ("3.11", "3.12", "3.13")
    )
    if platform_matrix != expected_platform_matrix:
        raise CommercialReleaseError(f"platform/Python reports are incomplete: {platform_matrix}")
    systems = sorted({system for system, _python in platform_matrix})
    python_versions = sorted({python_version for _system, python_version in platform_matrix})
    wheel_hashes = {report["distribution_lifecycle"]["wheel_sha256"] for report in platform_reports}
    sdist_hashes = {report["distribution_lifecycle"]["sdist_sha256"] for report in platform_reports}
    if len(wheel_hashes) != 1 or len(sdist_hashes) != 1:
        raise CommercialReleaseError("operating systems did not install identical distributions")
    living_wiki_quality = _living_wiki_quality(
        repository,
        living_wiki_quality_path,
        binding=binding,
        wheel_hashes=wheel_hashes,
    )
    living_wiki_baseline, living_wiki_comparison = _living_wiki_comparison(
        repository,
        baseline_path=living_wiki_baseline_path,
        comparison_path=living_wiki_comparison_path,
        candidate=living_wiki_quality,
    )
    source_quality_matrix = _source_quality_matrix(
        repository,
        source_quality_matrix_path,
        binding=binding,
        artifact_sha256=next(iter(wheel_hashes)),
    )
    _semantic_gold, semantic_quality = _semantic_quality(
        repository,
        binding=binding,
        gold_path=semantic_gold_path,
        lifecycle_path=semantic_lifecycle_path,
        query_path=semantic_query_path,
        cost_path=semantic_query_cost_path,
        consensus_path=semantic_consensus_path,
        machine_review_paths=semantic_machine_review_paths,
        owner_review_english_path=semantic_owner_review_english_path,
        owner_review_chinese_path=semantic_owner_review_chinese_path,
    )

    host = _require_report(
        host_path,
        schema_version="deeplaw.no-model-host-acceptance/v1",
        binding=binding,
        field="no-model host acceptance",
    )
    if (
        host.get("model_task_acceptance") is not False
        or host.get("model_task_results_claimed") is not False
        or host.get("isolation", {}).get("model_or_api_call_attempted") is not False
        or set(host.get("hosts", {})) != {"codex", "claude_code", "opencode"}
    ):
        raise CommercialReleaseError("host acceptance scope is overstated or incomplete")

    reproducible = load_json(reproducible_path)
    verify_record_digest(reproducible, field="reproducible distribution report")
    if (
        reproducible.get("schema_version") != "deeplaw.reproducible-build-report/v2"
        or not _same_binding(binding, reproducible.get("binding", {}))
        or reproducible.get("repository_commit") != binding["commit"]
        or reproducible.get("lock_sha256") != binding["lock_sha256"]
        or reproducible.get("working_tree_dirty") is not False
        or reproducible.get("reproducible") is not True
        or reproducible.get("artifact_release_eligible") is not True
        or reproducible.get("artifact_release_blockers") != []
    ):
        raise CommercialReleaseError("reproducible distribution report did not pass")
    distribution_hashes = {item["sha256"] for item in reproducible.get("artifacts", [])}
    if distribution_hashes != wheel_hashes | sdist_hashes:
        raise CommercialReleaseError("platform artifacts differ from reproducible build bytes")

    evaluation = verify_report_directory(
        evaluation_path.parent,
        repository=repository,
        require_eligible=True,
    )
    candidate = evaluation["candidate"]
    if (
        evaluation_path.name != "evaluation-report.json"
        or candidate.get("version") != version
        or candidate.get("commit") != binding["commit"]
        or candidate.get("tree") != binding["tree"]
        or candidate.get("worktree_clean") is not True
        or candidate.get("artifact_type") != "wheel"
        or candidate.get("artifact_sha256") not in wheel_hashes
        or evaluation.get("freeze", {}).get("freeze_valid") is not True
        or evaluation.get("scoring", {}).get("quality_gate_passed") is not True
        or evaluation.get("hard_failures") != []
        or evaluation.get("claims", {}).get("quality_protocol_eligible") is not True
        or evaluation.get("claims", {}).get("comparative_superiority_claim_eligible") is not False
        or evaluation.get("claims", {}).get("external_institution_certification_required")
        is not False
    ):
        raise CommercialReleaseError(
            "Evaluation Protocol report is ineligible or targets different bytes"
        )
    authoritative_evidence_quality = _authoritative_evidence_quality(
        repository,
        binding=binding,
        quality_path=authoritative_evidence_quality_path,
        evaluation_path=evaluation_path,
    )

    oci = _require_report(
        oci_report_path,
        schema_version="deeplaw.oci-release-report/v1",
        binding=binding,
        field="OCI release report",
    )
    if not all(oci.get("gates", {}).values()):
        raise CommercialReleaseError("OCI gate is incomplete")

    expected_profiles = {"default", "build", "discovery", "document-engine"}
    audit_reports = [load_json(path) for path in audit_paths]
    for report in audit_reports:
        verify_record_digest(report, field="dependency audit")
        if (
            report.get("schema_version") != "deeplaw.dependency-audit/v1"
            or report.get("status") != "passed"
            or not _same_binding(binding, report.get("binding", {}))
        ):
            raise CommercialReleaseError("dependency audit report did not pass")
    profiles = {report.get("profile") for report in audit_reports}
    if profiles != expected_profiles:
        raise CommercialReleaseError(f"dependency audit profiles are incomplete: {profiles}")

    sbom = _sbom(sbom_path, version=version)
    licenses = _licenses(licenses_path, binding=binding)
    openvex = _openvex(openvex_path, version=version)
    docs = _docs(repository)
    artifacts = _artifact_inventory(assets_root)
    artifact_by_path = {item["path"]: item for item in artifacts}
    expected_dist = {f"dist/{item['name']}": item["sha256"] for item in reproducible["artifacts"]}
    for relative, digest in expected_dist.items():
        if artifact_by_path.get(relative, {}).get("sha256") != digest:
            raise CommercialReleaseError(f"verified distribution bytes are absent: {relative}")
    if (
        artifact_by_path.get(f"oci/deeplaw-{version}-linux-amd64.oci.tar", {}).get("sha256")
        != oci["oci_archive"]["sha256"]
    ):
        raise CommercialReleaseError("verified OCI bytes are absent from release assets")
    evaluation_asset = artifact_by_path.get("evaluation/evaluation-report.json", {})
    if evaluation_asset.get("sha256") != file_record(evaluation_path)["sha256"]:
        raise CommercialReleaseError(
            "verified Evaluation Protocol report is absent from release assets"
        )
    living_wiki_quality_asset = artifact_by_path.get("quality/living-wiki-quality-report.json", {})
    if living_wiki_quality_asset.get("sha256") != file_record(living_wiki_quality_path)["sha256"]:
        raise CommercialReleaseError(
            "verified Living Wiki quality report is absent from release assets"
        )
    living_wiki_baseline_asset = artifact_by_path.get(
        "quality/living-wiki-quality-baseline.json", {}
    )
    if living_wiki_baseline_asset.get("sha256") != file_record(living_wiki_baseline_path)["sha256"]:
        raise CommercialReleaseError(
            "verified Living Wiki baseline report is absent from release assets"
        )
    living_wiki_comparison_asset = artifact_by_path.get(
        "quality/living-wiki-quality-comparison.json", {}
    )
    if (
        living_wiki_comparison_asset.get("sha256")
        != file_record(living_wiki_comparison_path)["sha256"]
    ):
        raise CommercialReleaseError(
            "verified Living Wiki comparison is absent from release assets"
        )
    source_quality_asset = artifact_by_path.get("quality/v0.12-28-source-decision-matrix.json", {})
    if source_quality_asset.get("sha256") != file_record(source_quality_matrix_path)["sha256"]:
        raise CommercialReleaseError(
            "verified 28-source decision matrix is absent from release assets"
        )
    authoritative_quality_asset = artifact_by_path.get(
        "quality/authoritative-evidence-quality.json", {}
    )
    if (
        authoritative_quality_asset.get("sha256")
        != file_record(authoritative_evidence_quality_path)["sha256"]
    ):
        raise CommercialReleaseError(
            "verified Authoritative evidence quality report is absent from release assets"
        )
    semantic_assets = {
        "gold": ("semantic/semantic-gold.json", semantic_gold_path),
        "lifecycle": (
            "semantic/deterministic-semantic-lifecycle.json",
            semantic_lifecycle_path,
        ),
        "query": ("semantic/semantic-query-report.json", semantic_query_path),
        "cost": ("semantic/semantic-query-cost.json", semantic_query_cost_path),
        "consensus": (
            "semantic/machine-review-consensus.json",
            semantic_consensus_path,
        ),
        "owner_en": (
            "semantic/owner-review-packet.en.json",
            semantic_owner_review_english_path,
        ),
        "owner_zh": (
            "semantic/owner-review-packet.zh-CN.json",
            semantic_owner_review_chinese_path,
        ),
    }
    for field, (relative, source) in semantic_assets.items():
        if artifact_by_path.get(relative, {}).get("sha256") != file_record(source)["sha256"]:
            raise CommercialReleaseError(
                f"verified Semantic Living Wiki {field} artifact is absent"
            )
    machine_review_assets: dict[str, tuple[str, Path]] = {}
    for path in semantic_machine_review_paths:
        packet = load_json(path)
        role = packet["auditor_role"]
        relative = f"semantic/machine-reviews/{role}.json"
        if role in machine_review_assets:
            raise CommercialReleaseError("duplicate Semantic machine review role")
        machine_review_assets[role] = (relative, path)
        if artifact_by_path.get(relative, {}).get("sha256") != file_record(path)["sha256"]:
            raise CommercialReleaseError(
                f"verified Semantic machine review artifact is absent: {role}"
            )
    editor_assets = {
        "obsidian": ("editors/deeplaw-obsidian-plugin.zip", obsidian_artifact_path),
        "tolaria": ("editors/tolaria-integration-report.json", tolaria_report_path),
    }
    for field, (relative, source) in editor_assets.items():
        if artifact_by_path.get(relative, {}).get("sha256") != file_record(source)["sha256"]:
            raise CommercialReleaseError(f"verified {field} editor artifact is absent")
    major, minor, _patch = version.split(".")
    release_notes_relative = f"docs/RELEASE_NOTES_v{version}.md"
    acceptance_relative = f"docs/V{major}_{minor}_ACCEPTANCE_MATRIX.md"
    release_notes_asset = artifact_by_path.get(f"documentation/RELEASE_NOTES_v{version}.md", {})
    acceptance_asset = artifact_by_path.get(
        f"documentation/V{major}_{minor}_ACCEPTANCE_MATRIX.md", {}
    )
    if (
        release_notes_asset.get("sha256")
        != file_record(repository / release_notes_relative)["sha256"]
        or acceptance_asset.get("sha256") != file_record(repository / acceptance_relative)["sha256"]
    ):
        raise CommercialReleaseError(
            "release notes or acceptance matrix are absent from release assets"
        )

    mandatory_tests = sum(report["mandatory_suite"]["tests"] for report in platform_reports)
    mandatory_skips = sum(report["mandatory_suite"]["skipped"] for report in platform_reports)
    if mandatory_tests < 5220 or mandatory_skips != 0:
        raise CommercialReleaseError("mandatory test total is incomplete or includes skips")
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": environment_manifest(),
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": version,
            "tag": tag,
            "commit": binding["commit"],
            "tree": binding["tree"],
            "source_date_epoch": source_date_epoch,
        },
        "bindings": {
            "lock_sha256": binding["lock_sha256"],
            "pyproject_sha256": binding["pyproject_sha256"],
            "contracts_inventory_sha256": binding["contracts"]["inventory_sha256"],
            "contracts_count": binding["contracts"]["count"],
            "migrations_inventory_sha256": binding["migrations"]["inventory_sha256"],
            "migration_identities": binding["migrations"]["identities"],
            "versions": versions,
        },
        "artifacts": artifacts,
        "platform_gates": {
            "systems": systems,
            "python_versions": python_versions,
            "matrix": [
                {"system": system, "python_version": python_version}
                for system, python_version in platform_matrix
            ],
            "mandatory_tests": mandatory_tests,
            "mandatory_skips": mandatory_skips,
            "windows_native_acl_junction_reparse": True,
            "identical_wheel_sha256": next(iter(wheel_hashes)),
            "identical_sdist_sha256": next(iter(sdist_hashes)),
            "passed": True,
        },
        "host_acceptance": {
            "hosts": sorted(host["hosts"]),
            "scope": host["acceptance_scope"],
            "model_or_api_call_attempted": False,
            "model_task_acceptance": False,
            "passed": True,
        },
        "supply_chain": {
            "reproducible_wheel_sdist": True,
            "oci_manifest_digest": oci["inventory"]["manifest_digest"],
            "oci_non_root_no_listener": True,
            "dependency_audit_profiles": sorted(profiles),
            "sbom": sbom,
            "licenses": licenses,
            "openvex": openvex,
            "sigstore_oidc_required_by_release_workflow": True,
            "github_provenance_required_by_release_workflow": True,
            "exact_verified_bytes_required_by_release_workflow": True,
        },
        "commercial_gates": {
            "clean_final_commit": True,
            "version_unified": True,
            "three_os_no_skip_mandatory_suite": True,
            "distribution_install_upgrade_uninstall": True,
            "cli_migration_rollback_snapshot_restore": True,
            "mcp_stdio_and_read_only_tools": True,
            "security_corruption_lock_permissions": True,
            "windows_acl_junction_reparse": True,
            "real_no_model_host_lifecycle": True,
            "byte_reproducible_wheel_sdist": True,
            "non_root_networkless_oci": True,
            "sbom_license_audit_openvex": True,
            "evaluation_protocol_v1": True,
            "living_wiki_fresh_wheel_quality": True,
            "living_wiki_baseline_no_regression": True,
            "authoritative_28_source_quality": True,
            "semantic_deterministic_machine_consensus_quality": True,
            "obsidian_tolaria_integration": True,
            "documentation": docs,
        },
        "evaluation_protocol": {
            "protocol_id": evaluation["protocol_id"],
            "protocol_version": evaluation["protocol_version"],
            "report_path": "evaluation/evaluation-report.json",
            "report_artifact_sha256": evaluation_asset["sha256"],
            "report_sha256": evaluation["report_sha256"],
            "scoring_digest": evaluation["scoring_digest"],
            "overall_score": evaluation["scoring"]["overall_score"],
            "freeze_commit": evaluation["freeze"]["freeze_commit"],
            "freeze_valid": True,
            "quality_gate_passed": True,
            "candidate_wheel_sha256": candidate["artifact_sha256"],
        },
        "living_wiki_quality": {
            "suite_id": living_wiki_quality["suite"]["suite_id"],
            "report_path": "quality/living-wiki-quality-report.json",
            "report_sha256": living_wiki_quality_asset["sha256"],
            "baseline_report_path": "quality/living-wiki-quality-baseline.json",
            "baseline_report_sha256": living_wiki_baseline_asset["sha256"],
            "baseline_record_sha256": living_wiki_baseline["record_sha256"],
            "baseline_commit": LIVING_WIKI_BASELINE_COMMIT,
            "baseline_version": living_wiki_baseline["candidate"]["version"],
            "baseline_wheel_sha256": living_wiki_baseline["candidate"]["artifact_sha256"],
            "baseline_passed": living_wiki_baseline["passed"],
            "baseline_failure_codes": sorted(
                str(item.get("code")) for item in living_wiki_baseline["failures"]
            ),
            "comparison_path": "quality/living-wiki-quality-comparison.json",
            "comparison_sha256": living_wiki_comparison_asset["sha256"],
            "comparison_record_sha256": living_wiki_comparison["record_sha256"],
            "candidate_wheel_sha256": living_wiki_quality["candidate"]["artifact_sha256"],
            "recall_at_k": living_wiki_quality["retrieval"]["recall_at_k"],
            "precision_at_k": living_wiki_quality["retrieval"]["precision_at_k"],
            "mrr": living_wiki_quality["retrieval"]["mrr"],
            "citation_validity": living_wiki_quality["retrieval"]["citation_validity"],
            "security_failures": 0,
            "quality_regression": False,
            "performance_regression": False,
            "passed": True,
        },
        "authoritative_source_quality": {
            "matrix_path": "quality/v0.12-28-source-decision-matrix.json",
            "matrix_sha256": source_quality_asset["sha256"],
            "record_sha256": source_quality_matrix["record_sha256"],
            "catalog_sha256": source_quality_matrix["catalog"]["sha256"],
            "active_release_id": source_quality_matrix["active_release"]["release_id"],
            "active_database_sha256": source_quality_matrix["active_release"]["database_sha256"],
            "source_count": len(source_quality_matrix["sources"]),
            "decision_summary": source_quality_matrix["decision_summary"],
            "quality_regression": False,
            "passed": True,
        },
        "semantic_living_wiki_quality": {
            "gold_path": semantic_assets["gold"][0],
            "gold_sha256": artifact_by_path[semantic_assets["gold"][0]]["sha256"],
            "deterministic_lifecycle_path": semantic_assets["lifecycle"][0],
            "deterministic_lifecycle_sha256": artifact_by_path[
                semantic_assets["lifecycle"][0]
            ]["sha256"],
            "query_report_path": semantic_assets["query"][0],
            "query_report_sha256": artifact_by_path[semantic_assets["query"][0]]["sha256"],
            "query_cost_path": semantic_assets["cost"][0],
            "query_cost_sha256": artifact_by_path[semantic_assets["cost"][0]]["sha256"],
            "machine_review_consensus_path": semantic_assets["consensus"][0],
            "machine_review_consensus_sha256": artifact_by_path[
                semantic_assets["consensus"][0]
            ]["sha256"],
            "machine_review_packet_paths": [
                machine_review_assets[role][0]
                for role in sorted(machine_review_assets)
            ],
            "machine_review_packet_sha256": [
                artifact_by_path[machine_review_assets[role][0]]["sha256"]
                for role in sorted(machine_review_assets)
            ],
            "owner_review_packet_english_path": semantic_assets["owner_en"][0],
            "owner_review_packet_english_sha256": artifact_by_path[
                semantic_assets["owner_en"][0]
            ]["sha256"],
            "owner_review_packet_chinese_path": semantic_assets["owner_zh"][0],
            "owner_review_packet_chinese_sha256": artifact_by_path[
                semantic_assets["owner_zh"][0]
            ]["sha256"],
            "human_gold_review": semantic_quality["consensus"]["human_gold_review"],
            "maintainer_confirmed": False,
            "reviewer_id": None,
            "independent_machine_review": semantic_quality["consensus"][
                "independent_machine_review"
            ],
            "external_real_model_semantic_execution": "not_executed",
            "recall_at_k": semantic_quality["query"]["metrics"]["recall_at_k"],
            "target_scoped_precision_at_k": semantic_quality["query"]["metrics"][
                "target_scoped_precision_at_k"
            ],
            "citation_validity": semantic_quality["query"]["metrics"][
                "citation_validity"
            ],
            "claim_evidence_binding_accuracy": semantic_quality["query"]["metrics"][
                "claim_evidence_binding_accuracy"
            ],
            "provider_payload_bytes": semantic_quality["query"]["metrics"][
                "provider_payload_bytes"
            ],
            "provider_content_bytes": semantic_quality["query"]["metrics"][
                "provider_content_bytes"
            ],
            "hard_failures": 0,
            "formal_release_eligible": True,
            "passed": True,
        },
        "authoritative_evidence_quality": {
            "report_path": "quality/authoritative-evidence-quality.json",
            "report_sha256": authoritative_quality_asset["sha256"],
            "record_sha256": authoritative_evidence_quality["record_sha256"],
            "challenge_trace_schema_sha256": authoritative_evidence_quality["schemas"][
                "authoritative_challenge_trace"
            ],
            "challenge_replay_schema_sha256": authoritative_evidence_quality["schemas"][
                "authoritative_challenge_replay"
            ],
            "capability_schema_sha256": authoritative_evidence_quality["schemas"][
                "evidence_capabilities"
            ],
            "citation_audit_schema_sha256": authoritative_evidence_quality["schemas"][
                "citation_audit"
            ],
            "expert_gold_status": authoritative_evidence_quality["expert_gold"]["status"],
            "expert_quality_claimed": authoritative_evidence_quality["expert_gold"][
                "expert_quality_claimed"
            ],
            "authority_elevation_failures": authoritative_evidence_quality["security_failures"][
                "authority_elevation"
            ],
            "passed": True,
        },
        "editor_integrations": {
            "obsidian_artifact": {
                "path": editor_assets["obsidian"][0],
                "sha256": artifact_by_path[editor_assets["obsidian"][0]]["sha256"],
            },
            "tolaria_report": {
                "path": editor_assets["tolaria"][0],
                "sha256": artifact_by_path[editor_assets["tolaria"][0]]["sha256"],
            },
            "canonical_writes": 0,
            "passed": True,
        },
        "release_documentation": {
            "release_notes_path": f"documentation/RELEASE_NOTES_v{version}.md",
            "release_notes_sha256": release_notes_asset["sha256"],
            "acceptance_matrix_path": (f"documentation/V{major}_{minor}_ACCEPTANCE_MATRIX.md"),
            "acceptance_matrix_sha256": acceptance_asset["sha256"],
            "known_limitations_declared": True,
            "unclaimed_capabilities_declared": True,
        },
        "commercial_release_eligible": True,
        "quality_protocol_eligible": True,
        "competitive_claim_eligible": False,
        "competitive_evidence_missing": COMPETITIVE_EVIDENCE_MISSING,
        "claim_policy": {
            "commercial_ga_is_independent_from_competitive_leadership": True,
            "model_task_e2e_counted_as_completed": False,
            "static_or_lifecycle_checks_counted_as_model_acceptance": False,
            "external_institution_certification_required": False,
            "public_temporal_holdout_is_secret": False,
            "best_sota_or_overall_leadership_claims_permitted": False,
        },
    }


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Assemble the DeepLaw commercial GA manifest.")
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--platform", type=Path, action="append", required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--reproducible", type=Path, required=True)
    parser.add_argument("--oci-report", type=Path, required=True)
    parser.add_argument("--audit", type=Path, action="append", required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--licenses", type=Path, required=True)
    parser.add_argument("--openvex", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--living-wiki-quality", type=Path, required=True)
    parser.add_argument("--living-wiki-baseline", type=Path, required=True)
    parser.add_argument("--living-wiki-comparison", type=Path, required=True)
    parser.add_argument("--source-quality-matrix", type=Path, required=True)
    parser.add_argument("--semantic-gold", type=Path, required=True)
    parser.add_argument("--semantic-lifecycle", type=Path, required=True)
    parser.add_argument("--semantic-query", type=Path, required=True)
    parser.add_argument("--semantic-query-cost", type=Path, required=True)
    parser.add_argument("--semantic-consensus", type=Path, required=True)
    parser.add_argument("--semantic-machine-review", type=Path, action="append", required=True)
    parser.add_argument("--semantic-owner-review-english", type=Path, required=True)
    parser.add_argument("--semantic-owner-review-chinese", type=Path, required=True)
    parser.add_argument("--authoritative-evidence-quality", type=Path, required=True)
    parser.add_argument("--obsidian-artifact", type=Path, required=True)
    parser.add_argument("--tolaria-report", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, default=946684800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = assemble(
            args.repository.resolve(),
            assets_root=args.assets_root.resolve(),
            platform_paths=[item.resolve() for item in args.platform],
            host_path=args.host.resolve(),
            reproducible_path=args.reproducible.resolve(),
            oci_report_path=args.oci_report.resolve(),
            audit_paths=[item.resolve() for item in args.audit],
            sbom_path=args.sbom.resolve(),
            licenses_path=args.licenses.resolve(),
            openvex_path=args.openvex.resolve(),
            evaluation_path=args.evaluation.resolve(),
            living_wiki_quality_path=args.living_wiki_quality.resolve(),
            living_wiki_baseline_path=args.living_wiki_baseline.resolve(),
            living_wiki_comparison_path=args.living_wiki_comparison.resolve(),
            source_quality_matrix_path=args.source_quality_matrix.resolve(),
            semantic_gold_path=args.semantic_gold.resolve(),
            semantic_lifecycle_path=args.semantic_lifecycle.resolve(),
            semantic_query_path=args.semantic_query.resolve(),
            semantic_query_cost_path=args.semantic_query_cost.resolve(),
            semantic_consensus_path=args.semantic_consensus.resolve(),
            semantic_machine_review_paths=[
                item.resolve() for item in args.semantic_machine_review
            ],
            semantic_owner_review_english_path=(
                args.semantic_owner_review_english.resolve()
            ),
            semantic_owner_review_chinese_path=(
                args.semantic_owner_review_chinese.resolve()
            ),
            authoritative_evidence_quality_path=args.authoritative_evidence_quality.resolve(),
            obsidian_artifact_path=args.obsidian_artifact.resolve(),
            tolaria_report_path=args.tolaria_report.resolve(),
            source_date_epoch=args.source_date_epoch,
        )
        schema = load_json(
            args.repository.resolve() / "contracts/commercial-release-manifest.v5.schema.json"
        )
        Draft202012Validator.check_schema(schema)
        write_report(args.output.resolve(), report)
        Draft202012Validator(schema).validate(load_json(args.output.resolve()))
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
