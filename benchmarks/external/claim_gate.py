from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

if __package__:
    from .benchlib import (
        SCHEMA_COMPARISON,
        SCHEMA_METRIC_REPORT,
        SCHEMA_REPORT,
        canonical_json,
        paired_comparison,
        read_json,
        sha256_file,
        strict_json_loads,
        write_json,
    )
else:
    from benchlib import (
        SCHEMA_COMPARISON,
        SCHEMA_METRIC_REPORT,
        SCHEMA_REPORT,
        canonical_json,
        paired_comparison,
        read_json,
        sha256_file,
        strict_json_loads,
        write_json,
    )

PROTOCOL_SCHEMA = "deeplaw.external-proof-protocol/v2"
EVIDENCE_SCHEMA = "deeplaw.claim-evidence/v1"
GATE_SCHEMA = "deeplaw.claim-gate/v1"
SUITE_EVIDENCE_SCHEMA = "deeplaw.external-suite-evidence/v1"
FROZEN_PROTOCOL_CANONICAL_SHA256 = (
    "d3a472c48df3d18d7f43310bb55658ad28d46a8691a981bb883074ec39d1f369"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MAX_BOUND_JSON_BYTES = 8 * 1024 * 1024
_MAX_COMPARISONS_PER_SUITE = 512
_UNBOUNDED_CLAIMS = (
    re.compile(r"全面超过所有(?:知识库|系统|方法)"),
    re.compile(r"超过(?:全部|一切|所有)(?:知识库|系统|方法)"),
    re.compile(r"(?:世界|全球)(?:最强|第一)(?:的)?知识库"),
    re.compile(r"\boutperform(?:s|ed)? all (?:knowledge|memory|retrieval) systems\b", re.I),
    re.compile(r"\bbest (?:knowledge|memory) system in the world\b", re.I),
)


def _artifact_path(value: Any, *, base: Path) -> Path | None:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        return None
    path_value = value.get("path")
    digest = value.get("sha256")
    if not isinstance(path_value, str) or not _SHA256.fullmatch(str(digest)):
        return None
    relative = Path(path_value)
    if relative.is_absolute() or not relative.parts:
        return None
    base_path = base.resolve()
    unresolved = base_path / relative
    try:
        path = unresolved.resolve(strict=True)
        path.relative_to(base_path)
    except (OSError, ValueError):
        return None
    if path != unresolved or not path.is_file() or path.is_symlink():
        return None
    return path


def _artifact_valid(value: Any, *, base: Path) -> bool:
    path = _artifact_path(value, base=base)
    return path is not None and sha256_file(path) == value["sha256"]


def _read_bound_json(
    artifact: Any,
    *,
    base: Path,
    maximum_bytes: int = _MAX_BOUND_JSON_BYTES,
) -> dict[str, Any]:
    path = _artifact_path(artifact, base=base)
    if path is None:
        raise ValueError("artifact path is invalid")
    payload = path.read_bytes()
    if len(payload) > maximum_bytes:
        raise ValueError("bound JSON artifact exceeds its byte limit")
    if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
        raise ValueError("bound JSON artifact SHA-256 mismatch")
    value = strict_json_loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("bound JSON artifact must contain an object")
    return value


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _finite_nonnegative(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def _signed_evaluator_attestations(
    evidence: dict[str, Any],
    *,
    evidence_base: Path,
    candidate_owner: str | None,
    protocol_id: Any,
    candidate: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    attestations: dict[str, dict[str, Any]] = {}
    evaluators = evidence.get("independent_evaluators")
    if not isinstance(evaluators, list):
        return attestations, ["independent_evaluators must be a list"]
    for index, evaluator in enumerate(evaluators):
        prefix = f"independent_evaluator[{index}]"
        if not isinstance(evaluator, dict):
            errors.append(f"{prefix} must be an object")
            continue
        organization = evaluator.get("organization")
        if (
            not isinstance(organization, str)
            or not organization
            or organization == candidate_owner
            or evaluator.get("independent") is not True
        ):
            errors.append(f"{prefix} is not an independent named organization")
            continue
        artifact = evaluator.get("attestation_artifact")
        if not _artifact_valid(artifact, base=evidence_base):
            errors.append(f"{prefix} attestation artifact is missing or invalid")
            continue
        try:
            artifact_path = _artifact_path(artifact, base=evidence_base)
            if artifact_path is None:
                raise ValueError
            public_key = base64.b64decode(
                evaluator.get("public_key_ed25519_base64", ""),
                validate=True,
            )
            signature = base64.b64decode(
                evaluator.get("signature_base64", ""),
                validate=True,
            )
            if len(public_key) != 32 or len(signature) != 64:
                raise ValueError
            payload = artifact_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != artifact["sha256"]:
                raise ValueError
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
            attestation = read_json(artifact_path)
        except (
            OSError,
            binascii.Error,
            InvalidSignature,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ):
            errors.append(f"{prefix} Ed25519 attestation signature is invalid")
            continue
        if (
            set(attestation)
            != {
                "schema_version",
                "organization",
                "protocol_id",
                "candidate",
                "suite_runs",
                "issued_at",
            }
            or attestation.get("schema_version") != "deeplaw.external-attestation/v1"
            or attestation.get("organization") != organization
            or attestation.get("protocol_id") != protocol_id
            or attestation.get("candidate") != candidate
            or _timestamp(attestation.get("issued_at")) is None
        ):
            errors.append(f"{prefix} attestation does not bind candidate and protocol")
            continue
        suite_runs = attestation.get("suite_runs")
        if not isinstance(suite_runs, list) or not suite_runs:
            errors.append(f"{prefix} attestation has no suite runs")
            continue
        seen_suites: set[str] = set()
        valid_suite_runs = True
        for suite_run in suite_runs:
            if (
                not isinstance(suite_run, dict)
                or set(suite_run) != {"suite_id", "evidence_manifest_sha256"}
                or not isinstance(suite_run.get("suite_id"), str)
                or not suite_run["suite_id"]
                or suite_run["suite_id"] in seen_suites
                or not _SHA256.fullmatch(
                    str(suite_run.get("evidence_manifest_sha256"))
                )
            ):
                valid_suite_runs = False
                break
            seen_suites.add(suite_run["suite_id"])
        if not valid_suite_runs:
            errors.append(f"{prefix} attestation has invalid or duplicate suite runs")
            continue
        if organization in attestations:
            errors.append(f"duplicate independent evaluator: {organization}")
            continue
        attestations[organization] = attestation
    return attestations, errors


def _report_errors(
    report: dict[str, Any],
    *,
    suite_id: str,
    system_id: str,
    prefix: str,
) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") not in {SCHEMA_REPORT, SCHEMA_METRIC_REPORT}:
        errors.append(f"{prefix} has an unsupported report schema")
    if report.get("suite_id") != suite_id or report.get("system_id") != system_id:
        errors.append(f"{prefix} does not bind the expected suite and system")
    if report.get("complete") is not True or report.get("claim_eligible") is not True:
        errors.append(f"{prefix} is incomplete or claim-ineligible")
    if report.get("claim_ineligibility_reason") is not None:
        errors.append(f"{prefix} contains a claim-ineligibility reason")
    if not _SHA256.fullmatch(str(report.get("cases_sha256"))):
        errors.append(f"{prefix} does not pin the frozen case set")
    case_count = report.get("case_count")
    per_case = report.get("per_case")
    if (
        isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or not 1 <= case_count <= 100_000
        or not isinstance(per_case, list)
        or len(per_case) != case_count
    ):
        errors.append(f"{prefix} case_count does not match bounded per-case results")
    elif len(
        {
            item.get("case_id")
            for item in per_case
            if isinstance(item, dict) and isinstance(item.get("case_id"), str)
        }
    ) != case_count:
        errors.append(f"{prefix} has missing or duplicate case IDs")
    return errors


def _metric_value(item: dict[str, Any], metric: str) -> Any:
    if metric in item:
        return item[metric]
    metrics = item.get("metrics")
    return metrics.get(metric) if isinstance(metrics, dict) else None


def _comparison_checks(
    run: dict[str, Any],
    *,
    suite: dict[str, Any],
    candidate_system_id: str,
    statistics: dict[str, Any],
    evidence_base: Path,
    required_dimensions: dict[str, dict[str, Any]],
) -> tuple[list[str], set[tuple[str, str]], list[tuple[str, float]]]:
    errors: list[str] = []
    pairs: set[tuple[str, str]] = set()
    primary_p_values: list[tuple[str, float]] = []
    comparisons = run.get("comparisons")
    if (
        not isinstance(comparisons, list)
        or not comparisons
        or len(comparisons) > _MAX_COMPARISONS_PER_SUITE
    ):
        return ["comparisons must be a non-empty bounded list"], pairs, primary_p_values
    expected_comparison_fields = {
        "schema_version",
        "suite_id",
        "candidate_system_id",
        "baseline_system_id",
        "candidate_report_sha256",
        "baseline_report_sha256",
        "metric",
        "direction",
        "case_count",
        "samples",
        "confidence",
        "seed",
        "noninferiority_margin",
        "minimum_effect",
        "candidate_minus_baseline",
        "ci_low",
        "ci_high",
        "superiority_p_value",
        "superior",
        "noninferior",
    }
    allowed_baselines = set(suite.get("named_baselines", []))
    for index, entry in enumerate(comparisons):
        prefix = f"comparison[{index}]"
        if (
            not isinstance(entry, dict)
            or set(entry)
            != {
                "artifact",
                "candidate_report_artifact",
                "baseline_report_artifact",
            }
        ):
            errors.append(f"{prefix} does not match the closed evidence shape")
            continue
        try:
            comparison = _read_bound_json(entry["artifact"], base=evidence_base)
            candidate_report = _read_bound_json(
                entry["candidate_report_artifact"],
                base=evidence_base,
            )
            baseline_report = _read_bound_json(
                entry["baseline_report_artifact"],
                base=evidence_base,
            )
        except (OSError, UnicodeDecodeError, ValueError) as error:
            errors.append(f"{prefix} artifact is invalid: {error}")
            continue
        if (
            comparison.get("schema_version") != SCHEMA_COMPARISON
            or set(comparison) != expected_comparison_fields
        ):
            errors.append(f"{prefix} has an unsupported or open comparison schema")
            continue
        baseline_id = comparison.get("baseline_system_id")
        metric = comparison.get("metric")
        if not isinstance(baseline_id, str) or baseline_id not in allowed_baselines:
            errors.append(f"{prefix} baseline is not registered for this suite")
            continue
        if not isinstance(metric, str) or metric not in required_dimensions:
            errors.append(f"{prefix} metric is not registered in the protocol")
            continue
        pair = (baseline_id, metric)
        if pair in pairs:
            errors.append(f"{prefix} duplicates baseline/metric {pair}")
            continue
        expected = required_dimensions[metric]
        report_errors = _report_errors(
            candidate_report,
            suite_id=run["suite_id"],
            system_id=candidate_system_id,
            prefix=f"{prefix} candidate report",
        )
        report_errors.extend(
            _report_errors(
                baseline_report,
                suite_id=run["suite_id"],
                system_id=baseline_id,
                prefix=f"{prefix} baseline report",
            )
        )
        if report_errors:
            errors.extend(report_errors)
            continue
        if (
            candidate_report.get("cases_sha256") != baseline_report.get("cases_sha256")
            or candidate_report.get("case_count") != baseline_report.get("case_count")
        ):
            errors.append(f"{prefix} reports do not share the same frozen cases")
            continue
        if (
            comparison.get("suite_id") != run["suite_id"]
            or comparison.get("candidate_system_id") != candidate_system_id
            or comparison.get("candidate_report_sha256")
            != _canonical_sha256(candidate_report)
            or comparison.get("baseline_report_sha256")
            != _canonical_sha256(baseline_report)
            or comparison.get("case_count") != candidate_report.get("case_count")
        ):
            errors.append(f"{prefix} does not bind its exact reports")
            continue
        if (
            comparison.get("direction") != expected.get("direction")
            or comparison.get("samples") != statistics.get("paired_bootstrap_samples")
            or comparison.get("confidence") != statistics.get("confidence")
            or comparison.get("seed") != statistics.get("seed")
            or comparison.get("minimum_effect") != expected.get("minimum_effect")
            or comparison.get("noninferiority_margin")
            != expected.get("noninferiority_margin")
        ):
            errors.append(f"{prefix} differs from the frozen statistical protocol")
            continue
        try:
            recomputed = paired_comparison(
                candidate_report,
                baseline_report,
                metric=metric,
                direction=expected["direction"],
                samples=statistics["paired_bootstrap_samples"],
                confidence=statistics["confidence"],
                seed=statistics["seed"],
                noninferiority_margin=expected["noninferiority_margin"],
                minimum_effect=expected["minimum_effect"],
            )
        except (KeyError, TypeError, ValueError) as error:
            errors.append(
                f"{prefix} could not be deterministically recomputed: {error}"
            )
            continue
        if comparison != recomputed:
            errors.append(
                f"{prefix} differs from deterministic paired-bootstrap recomputation"
            )
            continue
        candidate_items = {
            item["case_id"]: item for item in candidate_report["per_case"]
        }
        baseline_items = {
            item["case_id"]: item for item in baseline_report["per_case"]
        }
        if set(candidate_items) != set(baseline_items):
            errors.append(f"{prefix} reports do not contain identical case IDs")
            continue
        deltas: list[float] = []
        invalid_metric = False
        for case_id in sorted(candidate_items):
            candidate_value = _metric_value(candidate_items[case_id], metric)
            baseline_value = _metric_value(baseline_items[case_id], metric)
            if (
                isinstance(candidate_value, bool)
                or not isinstance(candidate_value, (int, float))
                or not math.isfinite(candidate_value)
                or isinstance(baseline_value, bool)
                or not isinstance(baseline_value, (int, float))
                or not math.isfinite(baseline_value)
            ):
                invalid_metric = True
                break
            deltas.append(float(candidate_value) - float(baseline_value))
        mean_delta = math.fsum(deltas) / len(deltas) if deltas else math.nan
        if invalid_metric or not math.isclose(
            mean_delta,
            comparison.get("candidate_minus_baseline", math.nan),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            errors.append(f"{prefix} mean effect does not match per-case reports")
            continue
        numeric_fields = (
            "ci_low",
            "ci_high",
            "candidate_minus_baseline",
            "superiority_p_value",
        )
        if any(
            not isinstance(comparison.get(field), (int, float))
            or isinstance(comparison.get(field), bool)
            or not math.isfinite(comparison[field])
            for field in numeric_fields
        ) or not 0 <= comparison["superiority_p_value"] <= 1:
            errors.append(f"{prefix} contains invalid statistical values")
            continue
        gate_field = expected["gate"]
        if comparison.get(gate_field) is not True:
            errors.append(f"{prefix} did not pass the {gate_field} gate")
            continue
        pairs.add(pair)
        if gate_field == "superior":
            primary_p_values.append(
                (f"{run['suite_id']}:{baseline_id}:{metric}", comparison["superiority_p_value"])
            )
    return errors, pairs, primary_p_values


def _holm_errors(
    hypotheses: list[tuple[str, float]],
    *,
    alpha: Any,
) -> list[str]:
    if (
        isinstance(alpha, bool)
        or not isinstance(alpha, (int, float))
        or not 0 < alpha < 1
    ):
        return ["frozen superiority_familywise_alpha is invalid"]
    if not hypotheses:
        return ["no superiority hypotheses were supplied for Holm correction"]
    errors: list[str] = []
    ordered = sorted(hypotheses, key=lambda item: (item[1], item[0]))
    total = len(ordered)
    for index, (label, p_value) in enumerate(ordered):
        threshold = float(alpha) / (total - index)
        if p_value > threshold:
            errors.append(
                f"Holm-Bonferroni rejected the proof gate at {label}: "
                f"p={p_value:.12g}, threshold={threshold:.12g}"
            )
            break
    return errors


_RUN_FIELDS = {
    "suite_id",
    "repository_revision",
    "dataset_revision",
    "dataset_sha256",
    "full_suite",
    "protocol_frozen_before_run",
    "no_post_freeze_tuning",
    "same_reader_model",
    "same_context_budget",
    "all_failures_retained",
    "reader_model",
    "reader_model_revision",
    "context_token_budget",
    "hardware",
    "index_build_seconds",
    "peak_memory_bytes",
    "disk_bytes",
    "model_cost_usd",
    "started_at",
    "completed_at",
    "labels_access",
    "independent_evaluator",
    "evaluator_organization",
    "raw_output_artifact",
    "comparisons",
    "evidence_manifest_artifact",
}


def _expected_run_manifest(
    run: dict[str, Any],
    *,
    protocol_id: Any,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SUITE_EVIDENCE_SCHEMA,
        "protocol_id": protocol_id,
        "candidate": candidate,
        **{
            field: run[field]
            for field in sorted(_RUN_FIELDS - {"evidence_manifest_artifact"})
        },
    }


def evaluate_claim(
    protocol: dict[str, Any],
    evidence: dict[str, Any],
    *,
    evidence_path: Path,
    requested_claim: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_evidence_fields = {
        "schema_version",
        "protocol_id",
        "candidate",
        "runs",
        "independent_evaluators",
        "status",
    }
    if protocol.get("schema_version") != PROTOCOL_SCHEMA:
        errors.append("unsupported protocol schema")
    if _canonical_sha256(protocol) != FROZEN_PROTOCOL_CANONICAL_SHA256:
        errors.append("protocol content differs from the frozen v2 commitment")
    if evidence.get("schema_version") != EVIDENCE_SCHEMA:
        errors.append("unsupported evidence schema")
    if set(evidence) != expected_evidence_fields:
        errors.append("claim evidence does not match the closed top-level shape")
    if evidence.get("status") != "complete":
        errors.append("claim evidence status is not complete")
    if protocol.get("protocol_id") != evidence.get("protocol_id"):
        errors.append("evidence does not bind the frozen protocol")
    claim_policy = protocol.get("claim_policy")
    if not isinstance(claim_policy, dict):
        errors.append("protocol claim_policy is invalid")
        claim_policy = {}
    if claim_policy.get("unbounded_universal_claim_allowed") is not False:
        errors.append("protocol must permanently reject unbounded universal claims")
    if requested_claim and any(pattern.search(requested_claim) for pattern in _UNBOUNDED_CLAIMS):
        errors.append("requested claim is unbounded and cannot be proven")
    protocol_candidate = protocol.get("candidate")
    if not isinstance(protocol_candidate, dict):
        errors.append("protocol candidate is invalid")
        protocol_candidate = {}
    candidate = evidence.get("candidate")
    if not isinstance(candidate, dict):
        errors.append("candidate evidence is missing")
        candidate = {}
    elif set(candidate) != {"system_id", "version", "git_commit", "artifact_sha256"}:
        errors.append("candidate evidence does not match the closed shape")
    if not _GIT_COMMIT.fullmatch(str(candidate.get("git_commit"))):
        errors.append("candidate git_commit is not pinned")
    if not _SHA256.fullmatch(str(candidate.get("artifact_sha256"))):
        errors.append("candidate artifact_sha256 is not pinned")
    if (
        candidate.get("system_id") != protocol_candidate.get("system_id")
        or candidate.get("version") != protocol_candidate.get("version")
    ):
        errors.append("candidate identity differs from the protocol")
    evaluator_attestations, evaluator_errors = _signed_evaluator_attestations(
        evidence,
        evidence_base=evidence_path.parent,
        candidate_owner=protocol_candidate.get("maintainer_organization"),
        protocol_id=protocol.get("protocol_id"),
        candidate=candidate,
    )
    errors.extend(evaluator_errors)

    required_dimensions = protocol.get("required_dimensions")
    if not isinstance(required_dimensions, dict) or not required_dimensions:
        errors.append("protocol required_dimensions is invalid")
        required_dimensions = {}
    statistics = protocol.get("statistics")
    if not isinstance(statistics, dict):
        errors.append("protocol statistics are invalid")
        statistics = {}
    suites = protocol.get("suites")
    suite_index: dict[str, dict[str, Any]] = {}
    if not isinstance(suites, list) or not suites:
        errors.append("protocol suites are invalid")
    else:
        for suite in suites:
            suite_id = suite.get("suite_id") if isinstance(suite, dict) else None
            if not isinstance(suite_id, str) or not suite_id or suite_id in suite_index:
                errors.append("protocol suites contain an invalid or duplicate suite_id")
                continue
            suite_index[suite_id] = suite
    runs = evidence.get("runs")
    if not isinstance(runs, list):
        errors.append("evidence runs must be a list")
        runs = []
    run_index: dict[str, dict[str, Any]] = {}
    all_baselines: set[str] = set()
    covered_dimensions: set[str] = set()
    primary_p_values: list[tuple[str, float]] = []
    hidden_run_valid = False
    evidence_base = evidence_path.parent
    for run_index_number, run in enumerate(runs):
        prefix = f"run[{run_index_number}]"
        if not isinstance(run, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if set(run) != _RUN_FIELDS:
            errors.append(f"{prefix} does not match the closed run evidence shape")
            continue
        suite_id = run.get("suite_id")
        if not isinstance(suite_id, str) or suite_id not in suite_index:
            errors.append(f"{prefix} suite_id is not registered")
            continue
        if suite_id in run_index:
            errors.append(f"duplicate suite run: {suite_id}")
            continue
        run_index[suite_id] = run
        suite = suite_index[suite_id]
        for field in (
            "full_suite",
            "protocol_frozen_before_run",
            "no_post_freeze_tuning",
            "same_reader_model",
            "same_context_budget",
            "all_failures_retained",
        ):
            if run.get(field) is not True:
                errors.append(f"{suite_id} did not attest {field}")
        if run.get("repository_revision") != suite.get("repository_revision"):
            errors.append(f"{suite_id} repository revision differs from the protocol")
        expected_dataset_revision = suite.get("dataset_revision")
        actual_dataset_revision = run.get("dataset_revision")
        if isinstance(expected_dataset_revision, str) and (
            expected_dataset_revision.startswith("sha256-required")
            or expected_dataset_revision.startswith("third-party-commitment-required")
        ):
            if (
                not isinstance(actual_dataset_revision, str)
                or not actual_dataset_revision
                or actual_dataset_revision == expected_dataset_revision
                or len(actual_dataset_revision) > 256
            ):
                errors.append(f"{suite_id} dataset revision was not fixed at execution")
        elif actual_dataset_revision != expected_dataset_revision:
            errors.append(f"{suite_id} dataset revision differs from the protocol")
        if not _SHA256.fullmatch(str(run.get("dataset_sha256"))):
            errors.append(f"{suite_id} dataset_sha256 is not pinned")
        for field in ("reader_model", "reader_model_revision", "hardware"):
            if (
                not isinstance(run.get(field), str)
                or not run[field]
                or len(run[field]) > 500
            ):
                errors.append(f"{suite_id} {field} is missing or unbounded")
        context_budget = run.get("context_token_budget")
        if (
            isinstance(context_budget, bool)
            or not isinstance(context_budget, int)
            or not 1 <= context_budget <= 10_000_000
        ):
            errors.append(f"{suite_id} context_token_budget is invalid")
        for field in (
            "index_build_seconds",
            "peak_memory_bytes",
            "disk_bytes",
            "model_cost_usd",
        ):
            if not _finite_nonnegative(run.get(field)):
                errors.append(f"{suite_id} {field} is not a finite resource measure")
        started_at = _timestamp(run.get("started_at"))
        completed_at = _timestamp(run.get("completed_at"))
        if started_at is None or completed_at is None or completed_at < started_at:
            errors.append(f"{suite_id} run timestamps are invalid")
        expected_labels_access = (
            "external_evaluator_only"
            if suite.get("role") == "external_hidden"
            else "public_frozen"
        )
        if (
            run.get("labels_access") != expected_labels_access
            or run.get("independent_evaluator") is not True
            or run.get("evaluator_organization") not in evaluator_attestations
        ):
            errors.append(f"{suite_id} is not bound to an independent evaluator")
        if not _artifact_valid(run.get("raw_output_artifact"), base=evidence_base):
            errors.append(f"{suite_id} raw output artifact is missing or invalid")
        comparison_errors, comparison_pairs, comparison_p_values = _comparison_checks(
            run,
            suite=suite,
            candidate_system_id=str(protocol_candidate.get("system_id", "")),
            statistics=statistics,
            evidence_base=evidence_base,
            required_dimensions=required_dimensions,
        )
        errors.extend(f"{suite_id}: {error}" for error in comparison_errors)
        allowed_baselines = set(suite.get("named_baselines", []))
        suite_dimensions = set(suite.get("required_dimensions", []))
        expected_pairs = {
            (baseline_id, dimension)
            for baseline_id in allowed_baselines
            for dimension in suite_dimensions
        }
        missing_pairs = expected_pairs - comparison_pairs
        if missing_pairs:
            errors.append(
                f"{suite_id} lacks passing pre-registered comparisons: "
                f"{sorted(missing_pairs)[:20]}"
            )
        unexpected_pairs = comparison_pairs - expected_pairs
        if unexpected_pairs:
            errors.append(
                f"{suite_id} contains unexpected comparisons: "
                f"{sorted(unexpected_pairs)[:20]}"
            )
        all_baselines.update(baseline_id for baseline_id, _ in comparison_pairs)
        covered_dimensions.update(dimension for _, dimension in comparison_pairs)
        primary_p_values.extend(comparison_p_values)
        evidence_manifest = run.get("evidence_manifest_artifact")
        try:
            actual_manifest = _read_bound_json(evidence_manifest, base=evidence_base)
        except (OSError, UnicodeDecodeError, ValueError) as error:
            errors.append(f"{suite_id} evidence manifest is invalid: {error}")
            actual_manifest = None
        if actual_manifest != _expected_run_manifest(
            run,
            protocol_id=protocol.get("protocol_id"),
            candidate=candidate,
        ):
            errors.append(f"{suite_id} evidence manifest does not bind the complete run")
        evaluator_organization = run.get("evaluator_organization")
        attestation = evaluator_attestations.get(evaluator_organization)
        attested_manifests = {
            item["suite_id"]: item["evidence_manifest_sha256"]
            for item in attestation.get("suite_runs", [])
        } if attestation is not None else {}
        manifest_sha256 = (
            evidence_manifest.get("sha256")
            if isinstance(evidence_manifest, dict)
            else None
        )
        independently_attested = (
            attested_manifests.get(suite_id) == manifest_sha256
        )
        if (
            independently_attested
            and completed_at is not None
            and attestation is not None
            and _timestamp(attestation.get("issued_at")) is not None
            and _timestamp(attestation["issued_at"]) < completed_at
        ):
            errors.append(f"{suite_id} attestation predates run completion")
            independently_attested = False
        if not independently_attested:
            errors.append(f"{suite_id} evidence manifest is not independently attested")
        if suite.get("role") == "external_hidden":
            hidden_run_valid = independently_attested
            if not hidden_run_valid:
                errors.append(
                    f"{suite_id} lacks an independent hidden-label attestation"
                )

    required_suite_ids = set(suite_index)
    missing_suites = sorted(required_suite_ids - set(run_index))
    if missing_suites:
        errors.append(f"required external suites are missing: {missing_suites}")
    minimum_suites = claim_policy.get("minimum_external_suites")
    if not isinstance(minimum_suites, int) or len(run_index) < minimum_suites:
        errors.append("minimum external suite count was not met")
    minimum_baselines = claim_policy.get("minimum_distinct_named_baselines")
    if not isinstance(minimum_baselines, int) or len(all_baselines) < minimum_baselines:
        errors.append("minimum distinct named baseline count was not met")
    if set(required_dimensions) - covered_dimensions:
        errors.append(
            "claim evidence does not cover every registered quality and safety dimension"
        )
    if statistics.get("familywise_primary_correction") != "holm-bonferroni":
        errors.append("only the frozen Holm-Bonferroni primary correction is accepted")
    else:
        errors.extend(
            _holm_errors(
                primary_p_values,
                alpha=statistics.get("superiority_familywise_alpha"),
            )
        )
    if claim_policy.get("requires_external_hidden_labels") is True and not hidden_run_valid:
        errors.append("an external hidden-label run is required")
    attested_suite_ids = {
        suite_run["suite_id"]
        for attestation in evaluator_attestations.values()
        for suite_run in attestation["suite_runs"]
        if suite_run["suite_id"] in run_index
        and suite_run["evidence_manifest_sha256"]
        == (
            run_index[suite_run["suite_id"]]["evidence_manifest_artifact"].get(
                "sha256"
            )
            if isinstance(
                run_index[suite_run["suite_id"]].get("evidence_manifest_artifact"),
                dict,
            )
            else None
        )
    }
    if (
        claim_policy.get("requires_all_runs_independently_attested") is True
        and attested_suite_ids != set(run_index)
    ):
        errors.append("every external run must be covered by a signed independent attestation")
    contributing_evaluators = {
        organization
        for organization, attestation in evaluator_attestations.items()
        if any(
            suite_run["suite_id"] in run_index
            and suite_run["evidence_manifest_sha256"]
            == (
                run_index[suite_run["suite_id"]]["evidence_manifest_artifact"].get(
                    "sha256"
                )
                if isinstance(
                    run_index[suite_run["suite_id"]].get(
                        "evidence_manifest_artifact"
                    ),
                    dict,
                )
                else None
            )
            for suite_run in attestation["suite_runs"]
        )
    }
    minimum_evaluators = claim_policy.get("minimum_independent_evaluators")
    if (
        not isinstance(minimum_evaluators, int)
        or len(contributing_evaluators) < minimum_evaluators
    ):
        errors.append("minimum independent evaluator count was not met")

    passed = not errors
    allowed_claim = None
    if passed:
        allowed_claim = str(claim_policy["allowed_claim_template"]).format(
            version=candidate["version"],
            protocol_id=protocol["protocol_id"],
            baseline_count=len(all_baselines),
            suite_count=len(run_index),
        )
    return {
        "schema_version": GATE_SCHEMA,
        "protocol_id": protocol.get("protocol_id"),
        "passed": passed,
        "unbounded_universal_claim_allowed": False,
        "suite_count": len(run_index),
        "baseline_count": len(all_baselines),
        "signed_independent_evaluator_count": len(evaluator_attestations),
        "independent_evaluator_count": len(contributing_evaluators),
        "covered_dimensions": sorted(covered_dimensions),
        "allowed_claim": allowed_claim,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail closed unless external benchmark evidence supports a scoped claim."
    )
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--requested-claim")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = evaluate_claim(
        read_json(args.protocol),
        read_json(args.evidence),
        evidence_path=args.evidence,
        requested_claim=args.requested_claim,
    )
    if args.output:
        write_json(args.output, result)
    else:
        print("PASS" if result["passed"] else "BLOCKED")
        for error in result["errors"]:
            print(f"- {error}")
        if result["allowed_claim"]:
            print(result["allowed_claim"])
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
