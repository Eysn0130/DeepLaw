"""Fail-closed Pass 16 Human Gold preflight and Host run scorer.

This evaluator only consumes owner-supplied artifacts.  It never creates a Gold
case, reviewer identity, model output, or Provider result.  A structurally valid
artifact is evidence of shape and binding only; it is not proof that a human
author, independence claim, or freeze actually happened.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.pass13_evidence import (
    EvidenceValidationError,
    validate_host_report_consistency,
)

REPOSITORY = Path(__file__).resolve().parents[2]
TASK_CASES_PATH = REPOSITORY / "benchmarks" / "hosts" / "pass16-continuity-task-cases-v1.json"
GOLD_SCHEMA_PATH = REPOSITORY / "contracts" / "host-continuity-human-gold.v1.schema.json"
TASK_SCHEMA_PATH = REPOSITORY / "contracts" / "host-continuity-task-cases.v1.schema.json"
QUALIFICATION_SCHEMA_PATH = (
    REPOSITORY / "contracts" / "host-continuity-qualification.v1.schema.json"
)
REVIEW_SCHEMA_PATH = (
    REPOSITORY / "contracts" / "host-continuity-pass16-blind-review.v1.schema.json"
)
SCORE_SCHEMA_PATH = REPOSITORY / "contracts" / "host-continuity-pass16-run-score.v1.schema.json"

GOLD_SCHEMA_VERSION = "deeplaw.host-continuity-human-gold/v1"
SCORE_SCHEMA_VERSION = "deeplaw.host-continuity-pass16-run-score/v1"
HUMAN_REVIEW_SCHEMA_VERSION = "deeplaw.host-continuity-pass16-blind-review/v1"
TASK_SCHEMA_VERSION = "deeplaw.host-continuity-task-cases/v1"
QUALIFICATION_SCHEMA_VERSION = "deeplaw.host-continuity-qualification/v1"
HOSTS = ("codex", "opencode")
SCENARIOS = ("cold_start", "resume_fork", "compaction_forget")
PROVIDER_HARD_LIMIT = 65_536
STRUCTURED_OUTPUT_HARD_LIMIT = 262_144
MAX_GOLD_BYTES = 256 * 1024
MAX_TASK_BYTES = 256 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_REVIEW_BYTES = 256 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OID = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s=:\"'])/(?!/)[A-Za-z0-9._~-]+(?:/[^\s\"'\\]*)?|"
    r"[A-Za-z]:[\\/]|\\\\[A-Za-z0-9._$-]+[\\/]"
)
_CREDENTIAL_FIELD = re.compile(
    r'"(?:[A-Za-z0-9_]*(?:api_key|authorization|cookie|credential|password|secret|'
    r'capability_token)[A-Za-z0-9_]*|token)"\s*:',
    re.IGNORECASE,
)

EXPECTED_HOST = {
    "codex": {
        "tool_version": "codex-cli 0.147.0-alpha.1.2",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "binary_name": "codex",
    },
    "opencode": {
        "tool_version": "1.18.16",
        "model": "deepseek/deepseek-v4-flash",
        "reasoning_effort": "max",
        "binary_name": "opencode",
    },
}


class Pass16EvaluationError(ValueError):
    """An owner artifact is unavailable or cannot be trusted structurally."""


class HumanGoldValidationError(Pass16EvaluationError):
    """The supplied Gold failed its external-file and frozen-case preconditions."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Pass16EvaluationError("artifact is not canonical JSON") from exc


def _parse_json(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Pass16EvaluationError(f"{label} must be UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise Pass16EvaluationError(f"{label} must contain one JSON object")
    return dict(value)


def _read_bounded(path: Path, *, maximum: int, label: str) -> tuple[dict[str, Any], bytes]:
    if not isinstance(path, Path):
        raise Pass16EvaluationError(f"{label} path is invalid")
    if path.is_symlink() or not path.is_file():
        raise Pass16EvaluationError(f"{label} must be one bounded regular file")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise Pass16EvaluationError(f"{label} cannot be stat'ed") from exc
    if size <= 0 or size > maximum:
        raise Pass16EvaluationError(f"{label} exceeds its bounded byte range")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise Pass16EvaluationError(f"{label} cannot be read") from exc
    if len(data) != size:
        raise Pass16EvaluationError(f"{label} changed while being read")
    return _parse_json(data, label=label), data


def _schema(path: Path, value: Mapping[str, Any], *, label: str) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(raw)
        errors = sorted(
            Draft202012Validator(raw, format_checker=FormatChecker()).iter_errors(value),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise Pass16EvaluationError(f"{label} contract is unavailable") from exc
    if errors:
        raise Pass16EvaluationError(f"{label} does not satisfy its schema")


def _sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise Pass16EvaluationError(f"{field} is not a SHA-256 digest")
    return value


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Pass16EvaluationError(f"{field} must be an object")
    return dict(value)


def _gold_cases(gold: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = gold.get("task_cases")
    if not isinstance(rows, list) or len(rows) != 3:
        raise HumanGoldValidationError("Human Gold must contain exactly three frozen cases")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        case = _mapping(raw, field=f"Human Gold case {index}")
        decision = case.get("current_decision")
        next_action = case.get("next_action")
        if not isinstance(decision, str) or not decision:
            raise HumanGoldValidationError("Human Gold case current decision is missing")
        if not isinstance(next_action, str) or not next_action:
            raise HumanGoldValidationError("Human Gold case next action is missing")
        post = case.get("post_forget")
        rubric = case.get("rubric")
        if not isinstance(rubric, Mapping):
            raise HumanGoldValidationError("Human Gold case rubric is missing")
        markers = case.get("forbidden_markers")
        if not isinstance(markers, list) or any(not isinstance(item, str) for item in markers):
            raise HumanGoldValidationError("Human Gold forbidden markers are invalid")
        result.append(
            {
                "task_case": case.get("task_case"),
                "scenario": case.get("scenario"),
                "current_decision": decision,
                "next_action": next_action,
                "forbidden_markers": list(markers),
                "post_forget": post,
                "rubric": dict(rubric),
            }
        )
    return result


def _rubric_ids(rubric: Mapping[str, Any], *, language: str | None = None) -> set[str]:
    selected: Any = rubric
    if language is not None and isinstance(rubric.get(language), Mapping):
        selected = rubric[language]
    if not isinstance(selected, Mapping):
        return set()
    ids = selected.get("criterion_ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        return set()
    return set(ids)


def _task_cases(path: Path) -> tuple[dict[str, Any], bytes]:
    value, data = _read_bounded(path, maximum=MAX_TASK_BYTES, label="frozen task cases")
    _schema(TASK_SCHEMA_PATH, value, label="frozen task cases")
    if (
        value.get("schema_version") != TASK_SCHEMA_VERSION
        or value.get("status") != "qualification_input_frozen"
        or value.get("model_outputs_seen_before_freeze") is not False
        or value.get("development_tuning_material") is not False
    ):
        raise HumanGoldValidationError("frozen task-case provenance is not current")
    rows = value.get("task_cases")
    if not isinstance(rows, list) or len(rows) != 3:
        raise HumanGoldValidationError("frozen task cases must contain exactly three cases")
    return value, data


def _case_matches(gold_case: Mapping[str, Any], task_case: Mapping[str, Any]) -> bool:
    current = task_case.get("current_checkpoint")
    challenges = task_case.get("wrong_state_challenges")
    if not isinstance(current, Mapping) or not isinstance(challenges, list):
        return False
    expected_forbidden = [
        challenge.get("marker")
        for challenge in challenges
        if isinstance(challenge, Mapping)
        and challenge.get("challenge") in {"stale_checkpoint", "wrong_task_line", "wrong_worktree"}
    ]
    post = task_case.get("post_forget_requirement")
    rubric = task_case.get("required_human_review")
    if not isinstance(rubric, Mapping):
        return False
    return (
        gold_case.get("task_case") == task_case.get("task_case")
        and gold_case.get("scenario") == task_case.get("scenario")
        and gold_case.get("current_decision") == current.get("decision")
        and gold_case.get("next_action") == current.get("next_action")
        and set(gold_case.get("forbidden_markers", [])) == set(expected_forbidden)
        and gold_case.get("post_forget") == post
        and gold_case.get("rubric") == dict(rubric)
    )


def _gold_receipt(gold: Mapping[str, Any], *, gold_data: bytes, task_data: bytes) -> dict[str, Any]:
    return {
        "status": "validated_structurally",
        "gold_id": gold.get("gold_id"),
        "gold_sha256": _sha256(gold_data),
        "gold_bytes": len(gold_data),
        "task_cases_sha256": _sha256(task_data),
        "task_cases_bytes": len(task_data),
        "case_count": 3,
        # This is intentionally explicit: shape, hash, and cross-file binding
        # cannot prove that the claimed human author or freeze was genuine.
        "structural_validation_only": True,
        "authenticity_proven": False,
    }


def load_human_gold(
    path: Path,
    *,
    task_cases_path: Path | None = None,
    repository: Path | None = None,
) -> dict[str, Any]:
    """Load one external Gold without manufacturing or asserting authenticity."""

    selected_task_cases = TASK_CASES_PATH if task_cases_path is None else Path(task_cases_path)
    repository_root = REPOSITORY if repository is None else Path(repository)
    try:
        repository_root = repository_root.resolve(strict=True)
        gold_resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HumanGoldValidationError("Human Gold location is unavailable") from exc
    if repository_root == gold_resolved or repository_root in gold_resolved.parents:
        raise HumanGoldValidationError("Human Gold must be repository-external")
    try:
        gold, gold_data = _read_bounded(
            Path(path), maximum=MAX_GOLD_BYTES, label="Human Gold"
        )
        _schema(GOLD_SCHEMA_PATH, gold, label="Human Gold")
    except Pass16EvaluationError as exc:
        raise HumanGoldValidationError(str(exc)) from exc
    if gold.get("claim_eligible") is not False:
        raise HumanGoldValidationError("Human Gold cannot be claim-eligible")
    task_cases, task_data = _task_cases(selected_task_cases)
    try:
        gold_frozen_at = datetime.fromisoformat(str(gold.get("frozen_at")))
        task_frozen_at = datetime.fromisoformat(str(task_cases.get("frozen_at")))
    except (TypeError, ValueError) as exc:
        raise HumanGoldValidationError("Human Gold freeze time is invalid") from exc
    if gold_frozen_at <= task_frozen_at:
        raise HumanGoldValidationError(
            "Human Gold must be frozen after the exact task-case file"
        )
    expected_task_digest = _sha256(task_data)
    if gold.get("task_cases_sha256") != expected_task_digest:
        raise HumanGoldValidationError("Human Gold task-case digest does not bind the frozen file")
    gold_rows = _gold_cases(gold)
    task_rows = task_cases.get("task_cases")
    if not isinstance(task_rows, list) or len(task_rows) != 3:
        raise HumanGoldValidationError("frozen task cases are incomplete")
    if [row.get("scenario") for row in gold_rows] != [row.get("scenario") for row in task_rows]:
        raise HumanGoldValidationError("Human Gold scenario order does not bind the frozen cases")
    if not all(
        _case_matches(gold_row, task_row)
        for gold_row, task_row in zip(gold_rows, task_rows, strict=True)
    ):
        raise HumanGoldValidationError(
            "Human Gold cases do not exactly match frozen task-case decisions, markers, or rubric"
        )
    # The schema is closed; this additional guard keeps accidental post-output
    # fields from being accepted if a future schema adds them.
    forbidden_names = {"result", "score", "model_output", "transcript", "after_output"}
    if forbidden_names & set(gold):
        raise HumanGoldValidationError("Human Gold contains candidate-output material")
    # Keep local variables used by receipt helpers available to callers without
    # adding path-bearing fields to the Gold artifact itself.
    _ = gold_data
    return gold


def load_gold(path: Path, **kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for evaluator callers that use the v2 naming."""

    return load_human_gold(path, **kwargs)


def human_gold_receipt(
    path: Path,
    *,
    task_cases_path: Path | None = None,
    repository: Path | None = None,
) -> dict[str, Any]:
    """Return only a path-free structural receipt for a validated Gold file."""

    selected_task_cases = TASK_CASES_PATH if task_cases_path is None else Path(task_cases_path)
    gold, gold_data = _read_bounded(Path(path), maximum=MAX_GOLD_BYTES, label="Human Gold")
    # Re-run all preconditions, including repository externality and exact case binding.
    load_human_gold(path, task_cases_path=selected_task_cases, repository=repository)
    _, task_data = _task_cases(selected_task_cases)
    return _gold_receipt(gold, gold_data=gold_data, task_data=task_data)


def load_human_gold_with_receipt(
    path: Path,
    *,
    task_cases_path: Path | None = None,
    repository: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    gold = load_human_gold(path, task_cases_path=task_cases_path, repository=repository)
    receipt = human_gold_receipt(path, task_cases_path=task_cases_path, repository=repository)
    return gold, receipt


def _scan_untrusted(value: Any) -> tuple[bool, bool]:
    """Return (path_leak, secret_leak) without retaining the offending value."""

    try:
        encoded = _canonical(value).decode("utf-8")
    except Pass16EvaluationError:
        return True, True
    path_leak = bool(_ABSOLUTE_PATH.search(encoded) or "file://" in encoded.casefold())
    # These are closed safety booleans, not retained secrets.  Mask them before
    # scanning credential-shaped field names.
    credential_scan = encoded
    for field in (
        "secret_values_retained",
        "authentication_material_retained",
        "auth_file_read",
        "secret_leak",
    ):
        credential_scan = credential_scan.replace(f'"{field}":false', '"safe_flag":false')
    secret_leak = bool(_CREDENTIAL_FIELD.search(credential_scan))
    lowered = encoded.casefold()
    secret_leak = secret_leak or any(
        token in lowered
        for token in ("api_key=", "authorization:", "bearer ", "password=", "secret=")
    )
    return path_leak, secret_leak


def _load_report_input(value: Any, *, host: str) -> tuple[dict[str, Any], list[str]]:
    failures: list[str] = []
    if isinstance(value, Path):
        try:
            report, _ = _read_bounded(value, maximum=MAX_REPORT_BYTES, label=f"{host} Host report")
        except Pass16EvaluationError:
            return {}, ["host_report_invalid"]
    elif isinstance(value, Mapping):
        report = dict(value)
    else:
        return {}, ["host_report_invalid"]
    try:
        _schema(QUALIFICATION_SCHEMA_PATH, report, label=f"{host} Host report")
    except Pass16EvaluationError:
        failures.append("host_report_invalid")
    if report.get("schema_version") != QUALIFICATION_SCHEMA_VERSION:
        failures.append("host_report_invalid")
    try:
        validate_host_report_consistency(report)
    except (EvidenceValidationError, KeyError, TypeError, ValueError):
        failures.append("host_report_invalid")
    path_leak, secret_leak = _scan_untrusted(report)
    if path_leak:
        failures.append("path_leak")
    if secret_leak:
        failures.append("secret_leak")
    return report, sorted(set(failures))


def _reports_by_host(reports: Any) -> dict[str, Any]:
    if isinstance(reports, Mapping):
        if all(host in reports for host in HOSTS):
            return {host: reports[host] for host in HOSTS}
        # A nested {host: {report: ...}} shape is intentionally not accepted:
        # accepting an ambiguous key would make host binding non-deterministic.
        return {host: reports.get(host) for host in HOSTS}
    if isinstance(reports, Sequence) and not isinstance(reports, (str, bytes, bytearray)):
        selected: dict[str, Any] = {}
        for report in reports:
            if isinstance(report, Mapping) and isinstance(report.get("host"), str):
                host = str(report["host"])
                if host in HOSTS and host not in selected:
                    selected[host] = report
        return {host: selected.get(host) for host in HOSTS}
    return {host: None for host in HOSTS}


def _reviews_by_key(reviews: Any) -> dict[tuple[str, str], Any]:
    result: dict[tuple[str, str], Any] = {}
    if isinstance(reviews, Mapping):
        for key, value in reviews.items():
            if isinstance(key, tuple) and len(key) == 2:
                host, task_case = key
                if isinstance(host, str) and isinstance(task_case, str):
                    result[(host, task_case)] = value
            elif isinstance(key, str) and key in HOSTS and isinstance(value, Mapping):
                for nested_key, nested_value in value.items():
                    if isinstance(nested_key, str):
                        result[(key, nested_key)] = nested_value
    elif isinstance(reviews, Sequence) and not isinstance(reviews, (str, bytes, bytearray)):
        # A tuple (host, task_case, review) is accepted for caller convenience;
        # review JSON itself remains the closed Pass 16 blind-review/v1 shape.
        for row in reviews:
            if isinstance(row, tuple) and len(row) == 3:
                host, task_case, review = row
                if isinstance(host, str) and isinstance(task_case, str):
                    result[(host, task_case)] = review
    return result


def _review_failures(
    review: Any,
    *,
    gold: Mapping[str, Any],
    gold_sha256: str,
    task_case: Mapping[str, Any],
    candidate_sha256: Any,
) -> tuple[list[str], dict[str, Any] | None]:
    failures: list[str] = []
    if not isinstance(review, Mapping):
        return ["review_missing"], None
    selected = dict(review)
    try:
        _schema(REVIEW_SCHEMA_PATH, selected, label="Human review")
    except Pass16EvaluationError:
        failures.append("review_schema_invalid")
    if selected.get("schema_version") != HUMAN_REVIEW_SCHEMA_VERSION:
        failures.append("review_schema_invalid")
    if selected.get("gold_id") != gold.get("gold_id"):
        failures.append("gold_mismatch")
    if selected.get("gold_sha256") != gold_sha256:
        failures.append("gold_digest_mismatch")
    if selected.get("case_id") != task_case.get("task_case"):
        failures.append("review_case_mismatch")
    if not isinstance(candidate_sha256, str) or _SHA256.fullmatch(candidate_sha256) is None:
        failures.append("evidence_binding_missing")
    elif selected.get("anonymized_candidate_sha256") != candidate_sha256:
        failures.append("review_candidate_digest_mismatch")
    rubric = task_case.get("rubric")
    criteria = selected.get("criterion_results")
    expected = _rubric_ids(rubric if isinstance(rubric, Mapping) else {})
    if not isinstance(criteria, Mapping) or any(
        not isinstance(value, bool) for value in criteria.values()
    ):
        failures.append("review_criteria_invalid")
        criteria = {}
    if set(criteria) != expected:
        failures.append("review_criterion_mismatch")
    if selected.get("reviewer_id") == gold.get("author_id"):
        failures.append("reviewer_identity_not_independent")
    for flag in (
        "reviewer_is_human",
        "independent",
        "blind_to_host",
        "blind_to_tool_model",
        "blind_to_other_runs",
        "order_randomized",
    ):
        if selected.get(flag) is not True:
            failures.append("blind_review_invalid")
    try:
        reviewed_at = datetime.fromisoformat(str(selected.get("reviewed_at")))
        frozen_at = datetime.fromisoformat(str(gold.get("frozen_at")))
    except (TypeError, ValueError):
        failures.append("review_time_invalid")
    else:
        if reviewed_at <= frozen_at:
            failures.append("review_precedes_gold_freeze")
    failed_criteria = {
        name for name, value in criteria.items() if isinstance(name, str) and value is not True
    }
    decision_failed = selected.get("decision") != "pass"
    if decision_failed or failed_criteria:
        failures.append("human_gold_threshold_miss")
    if "authority_boundary" in failed_criteria:
        failures.append("wrong_authority")
    if "wrong_state_rejection" in failed_criteria:
        failures.append("wrong_state_admission")
    if "bounded_read_only_context" in failed_criteria:
        failures.append("hidden_write")
    review_failure_case = selected.get("failure_case")
    review_hard_failures = selected.get("hard_failure_ids")
    if review_failure_case != (decision_failed or bool(failed_criteria)):
        failures.append("review_failure_case_mismatch")
    if not isinstance(review_hard_failures, list):
        failures.append("review_hard_failures_invalid")
        review_hard_failures = []
    if review_failure_case is True and not review_hard_failures:
        failures.append("review_hard_failures_missing")
    failures.extend(
        code
        for code in review_hard_failures
        if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_.-]{0,99}", code)
    )
    if selected.get("claim_eligible") is not False:
        failures.append("review_claim_eligible")
    if not failures:
        refs = {
            "review_id": selected.get("review_id"),
            "gold_id": selected.get("gold_id"),
            "gold_sha256": selected.get("gold_sha256"),
            "candidate_sha256": selected.get("anonymized_candidate_sha256"),
        }
        return [], refs
    return sorted(set(failures)), None


def _token_values(
    host: str, turns: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, int | str], list[str]]:
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    values: dict[str, int | str] = {}
    failures: list[str] = []
    normalized_turns: list[dict[str, int]] = []
    for turn in turns:
        usage = turn.get("usage")
        if not isinstance(usage, Mapping) or any(
            not isinstance(usage.get(field), int)
            or isinstance(usage.get(field), bool)
            or int(usage[field]) < 0
            for field in fields
        ):
            failures.append("actual_token_unreported")
            continue
        normalized = {field: int(usage[field]) for field in fields}
        expected_total = (
            normalized["input_tokens"] + normalized["output_tokens"]
            if host == "codex"
            else sum(normalized[field] for field in fields if field != "total_tokens")
        )
        if normalized["total_tokens"] != expected_total:
            failures.append("actual_token_arithmetic_invalid")
        if host == "codex" and (
            normalized["cached_input_tokens"] > normalized["input_tokens"]
            or normalized["reasoning_output_tokens"] > normalized["output_tokens"]
        ):
            failures.append("actual_token_arithmetic_invalid")
        normalized_turns.append(normalized)
    for field in fields:
        if normalized_turns and len(normalized_turns) == len(turns):
            values[field] = sum(value[field] for value in normalized_turns)
        else:
            values[field] = "unreported"
            failures.append("actual_token_unreported")
    return values, sorted(set(failures))


def _run_provider_and_security(run: Mapping[str, Any]) -> tuple[int | None, list[str]]:
    failures: list[str] = []
    total = 0
    observed = False
    turns = run.get("turns")
    if not isinstance(turns, list) or not turns:
        return None, ["provider_payload_missing"]
    for turn in turns:
        if not isinstance(turn, Mapping):
            failures.append("provider_payload_missing")
            continue
        before = turn.get("ledger_audit_head_before")
        after = turn.get("ledger_audit_head_after")
        if turn.get("ledger_unchanged") is not True or before != after:
            failures.append("ledger_changed")
        safe_read = turn.get("safe_read")
        if not isinstance(safe_read, Mapping):
            failures.append("provider_payload_missing")
            continue
        payloads = safe_read.get("provider_payloads")
        if not isinstance(payloads, list) or not payloads:
            failures.append("provider_payload_missing")
            continue
        for payload in payloads:
            if not isinstance(payload, Mapping):
                failures.append("provider_payload_missing")
                continue
            provider_bytes = payload.get("provider_bytes")
            structured_bytes = payload.get("structured_output_bytes")
            if not isinstance(provider_bytes, int) or isinstance(provider_bytes, bool):
                failures.append("provider_bytes_unreported")
            else:
                observed = True
                total += provider_bytes
                if provider_bytes < 1 or provider_bytes > PROVIDER_HARD_LIMIT:
                    failures.append("provider_payload_overflow")
            if not isinstance(structured_bytes, int) or isinstance(structured_bytes, bool):
                failures.append("structured_output_unreported")
            elif structured_bytes < 1 or structured_bytes > STRUCTURED_OUTPUT_HARD_LIMIT:
                failures.append("provider_payload_overflow")
            if payload.get("delivery_match") is not True:
                failures.append("provider_delivery_mismatch")
            if payload.get("write_performed") is not False:
                failures.append("hidden_write")
    if not observed:
        return None, sorted(set(failures or ["provider_bytes_unreported"]))
    if total > 524_288:
        failures.append("provider_payload_overflow")
    return total, sorted(set(failures))


def _run_extra_failures(
    run: Mapping[str, Any], *, scenario: str
) -> tuple[dict[str, Any], list[str]]:
    metrics = run.get("metrics")
    if not isinstance(metrics, Mapping):
        metrics = {}
    values = {
        "first_correct_action": metrics.get("first_correct_action"),
        "decision_preservation": metrics.get("decision_preservation"),
        "wrong_state_admission": metrics.get("wrong_state_admission"),
        "stale_state_rejected": metrics.get("stale_state_rejected"),
        "forgotten_state_admission": metrics.get("forgotten_state_admission"),
        "gap_observed": metrics.get("gap_observed"),
    }
    failures: list[str] = []
    if values["first_correct_action"] is not True:
        failures.append("first_correct_action_miss")
    # Pass 16 intentionally does not inherit the older nullable semantics:
    # Decision Preservation=null is a threshold miss, including cold starts.
    if values["decision_preservation"] is not True:
        failures.append("decision_preservation_miss")
    if values["wrong_state_admission"] != 0:
        failures.append("wrong_state_admission")
    if values["stale_state_rejected"] is not True:
        failures.append("stale_state_admission")
    if scenario == "compaction_forget":
        if values["forgotten_state_admission"] != 0:
            failures.append("forgotten_state_admission")
        if values["gap_observed"] is not True:
            failures.append("gap_miss")
    if metrics.get("provider_boundary_correct") is not True:
        failures.append("wrong_authority")
    if any(
        "authority" in str(code).casefold()
        for code in run.get("failure_codes", [])
        if isinstance(code, str)
    ):
        failures.append("wrong_authority")
    if run.get("status") != "passed":
        failures.append("host_run_failed")
    if run.get("failure_codes"):
        failures.append("host_failure_code")
    boundaries = run.get("mutation_boundaries")
    if isinstance(boundaries, list):
        for boundary in boundaries:
            if isinstance(boundary, Mapping):
                if boundary.get("read_mcp_write_performed") is not False:
                    failures.append("hidden_write")
                if boundary.get("kind") == "none" and boundary.get("audit_changed") is not False:
                    failures.append("hidden_write")
    else:
        failures.append("hidden_write")
    evidence = metrics.get("evidence_sha256")
    if not isinstance(evidence, str) or _SHA256.fullmatch(evidence) is None:
        failures.append("evidence_binding_missing")
    values["evidence_sha256"] = evidence
    return values, sorted(set(failures))


def _stable_run_id(*, host: str, task_case: str, commit: Any, tree: Any) -> str:
    digest = _sha256(
        _canonical({"host": host, "task_case": task_case, "commit": commit, "tree": tree})
    )
    return f"pass16run_{digest[:24]}"


def _empty_run(scenario: str) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "status": "failed",
        "metrics": {},
        "turns": [],
        "mutation_boundaries": [],
        "failure_codes": ["host_report_invalid"],
    }


def _score_one(
    *,
    host: str,
    report: Mapping[str, Any],
    report_failures: Sequence[str],
    task_case: Mapping[str, Any],
    review: Any,
    gold: Mapping[str, Any],
    gold_sha256: str,
    candidate_commit: Any,
    candidate_tree: Any,
    cross_binding_failures: Sequence[str],
) -> dict[str, Any]:
    scenario = str(task_case.get("scenario"))
    task_case_id = str(task_case.get("task_case"))
    runs = report.get("runs")
    selected: Mapping[str, Any] = _empty_run(scenario)
    if isinstance(runs, list):
        for run in runs:
            if isinstance(run, Mapping) and run.get("scenario") == scenario:
                selected = run
                break
    metrics, metric_failures = _run_extra_failures(selected, scenario=scenario)
    provider_bytes, provider_failures = _run_provider_and_security(selected)
    token_values, token_failures = _token_values(
        host,
        [turn for turn in selected.get("turns", []) if isinstance(turn, Mapping)]
        if isinstance(selected.get("turns"), list)
        else [],
    )
    review_failures, review_refs = _review_failures(
        review,
        gold=gold,
        gold_sha256=gold_sha256,
        task_case=task_case,
        candidate_sha256=metrics.get("evidence_sha256"),
    )
    failures = sorted(
        set(
            [
                *report_failures,
                *cross_binding_failures,
                *metric_failures,
                *provider_failures,
                *token_failures,
                *review_failures,
            ]
        )
    )
    # Report-level attestation and security fields are intentionally checked for
    # every score so a single bad Host cannot hide behind a good sibling.
    expected = EXPECTED_HOST[host]
    attestation = report.get("host_attestation")
    observed_tool_version = expected["tool_version"]
    observed_model = expected["model"]
    if not isinstance(attestation, Mapping):
        failures.append("host_attestation_invalid")
    else:
        if isinstance(attestation.get("version"), str) and attestation["version"]:
            observed_tool_version = attestation["version"]
        if isinstance(attestation.get("model"), str) and attestation["model"]:
            observed_model = attestation["model"]
        if attestation.get("binary_name") != expected["binary_name"]:
            failures.append("tool_version_mismatch")
        if attestation.get("version") != expected["tool_version"]:
            failures.append("tool_version_mismatch")
        if attestation.get("model") != expected["model"]:
            failures.append("model_substitution")
        if attestation.get("reasoning_effort") != expected["reasoning_effort"]:
            failures.append("model_substitution")
    security = report.get("security")
    if not isinstance(security, Mapping):
        failures.append("security_receipt_invalid")
    else:
        if security.get("absolute_path_leak") is True:
            failures.append("path_leak")
        if security.get("secret_leak") is True:
            failures.append("secret_leak")
        if security.get("mcp_child_closed_environment") is not True:
            failures.append("hidden_write")
        if security.get("only_knowledge_support_enabled") is not True:
            failures.append("wrong_authority")
        if security.get("authentication_material_retained") is not False:
            failures.append("secret_leak")
        if security.get("raw_transcript_retained") is not False:
            failures.append("path_leak")
        if security.get("hidden_reasoning_retained") is not False:
            failures.append("secret_leak")
    if report.get("release_ready") is True or report.get("claim_eligible") is True:
        failures.append("release_state_open")
    if not isinstance(candidate_commit, str) or _GIT_OID.fullmatch(candidate_commit) is None:
        failures.append("candidate_binding_invalid")
    if not isinstance(candidate_tree, str) or _GIT_OID.fullmatch(candidate_tree) is None:
        failures.append("candidate_binding_invalid")
    if provider_bytes is None:
        output_provider_bytes: int | None = None
    else:
        output_provider_bytes = provider_bytes
    if report.get("host") != host:
        failures.append("host_binding_mismatch")
    # A failed or missing review must never receive a synthetic reviewer ref.
    blind_refs = None if review_refs is None else review_refs
    failures = sorted(set(failures))
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "run_id": _stable_run_id(
            host=host,
            task_case=task_case_id,
            commit=candidate_commit,
            tree=candidate_tree,
        ),
        "task_case": task_case_id,
        "scenario": scenario,
        "host": host,
        "tool_version": observed_tool_version,
        "model": observed_model,
        "platform": (
            {
                field: report["environment"].get(field, "unknown")
                for field in ("operating_system", "architecture", "python_version")
            }
            if isinstance(report.get("environment"), Mapping)
            else {
                "operating_system": "unknown",
                "architecture": "unknown",
                "python_version": "unknown",
            }
        ),
        "candidate_commit": candidate_commit if isinstance(candidate_commit, str) else "0" * 40,
        "candidate_tree": candidate_tree if isinstance(candidate_tree, str) else "0" * 40,
        "provider_bytes": output_provider_bytes,
        **token_values,
        "first_correct_action": metrics.get("first_correct_action"),
        "decision_preservation": metrics.get("decision_preservation"),
        "wrong_state_admission": metrics.get("wrong_state_admission"),
        "stale_state_rejected": metrics.get("stale_state_rejected"),
        "forgotten_state_admission": metrics.get("forgotten_state_admission"),
        "gap_observed": metrics.get("gap_observed"),
        "blind_review_refs": blind_refs,
        "failure_cases": failures,
        "hard_failures": failures,
        "status": "passed" if not failures else "failed",
        "release_ready": False,
        "claim_eligible": False,
    }


def _validate_score(score: Mapping[str, Any]) -> None:
    _schema(SCORE_SCHEMA_PATH, score, label="Pass 16 run score")


def score_reports(
    reports: Mapping[str, Any] | Sequence[Any],
    reviews: Mapping[Any, Any] | Sequence[Any],
    *,
    gold: Mapping[str, Any] | None = None,
    gold_path: Path | None = None,
    task_cases_path: Path | None = None,
    repository: Path | None = None,
) -> dict[str, Any]:
    """Score exactly six Host/scenario runs and return path-free receipts.

    ``gold`` is accepted for callers that already loaded an external artifact,
    but the preferred path is ``gold_path`` so its exact bytes can be hashed and
    bound to each blind review.  A caller-supplied mapping alone is rejected: it
    cannot establish the required external-file digest.
    """

    if gold_path is None:
        raise HumanGoldValidationError("Pass 16 scoring requires an external Gold file")
    loaded_gold, gold_receipt = load_human_gold_with_receipt(
        Path(gold_path), task_cases_path=task_cases_path, repository=repository
    )
    if gold is not None and dict(gold) != loaded_gold:
        raise HumanGoldValidationError("caller Gold mapping does not match external Gold bytes")
    gold = loaded_gold
    selected_task_cases = TASK_CASES_PATH if task_cases_path is None else Path(task_cases_path)
    task_value, _ = _task_cases(selected_task_cases)
    frozen_rows = task_value.get("task_cases")
    if not isinstance(frozen_rows, list) or len(frozen_rows) != 3:
        raise HumanGoldValidationError("frozen task cases are incomplete")
    task_by_scenario = {
        str(row.get("scenario")): row for row in frozen_rows if isinstance(row, Mapping)
    }
    reports_by_host = _reports_by_host(reports)
    reviews_by_key = _reviews_by_key(reviews)
    loaded_reports: dict[str, dict[str, Any]] = {}
    report_failures: dict[str, list[str]] = {}
    for host in HOSTS:
        loaded_reports[host], report_failures[host] = _load_report_input(
            reports_by_host.get(host), host=host
        )
    bindings: dict[str, tuple[Any, Any]] = {}
    platforms: dict[str, Any] = {}
    for host, report in loaded_reports.items():
        binding = report.get("binding") if isinstance(report, Mapping) else None
        bindings[host] = (
            binding.get("commit") if isinstance(binding, Mapping) else None,
            binding.get("tree") if isinstance(binding, Mapping) else None,
        )
        environment = report.get("environment") if isinstance(report, Mapping) else None
        platforms[host] = (
            {
                field: environment.get(field)
                for field in ("operating_system", "architecture", "python_version")
            }
            if isinstance(environment, Mapping)
            else None
        )
    global_binding_failures: list[str] = []
    if bindings["codex"] != bindings["opencode"]:
        global_binding_failures.append("candidate_binding_mismatch")
    if (
        platforms["codex"] is not None
        and platforms["opencode"] is not None
        and platforms["codex"] != platforms["opencode"]
    ):
        global_binding_failures.append("platform_mismatch")
    expected_review_keys = {
        (host, str(row.get("task_case")))
        for host in HOSTS
        for row in frozen_rows
        if isinstance(row, Mapping)
    }
    review_ids = [
        review.get("review_id")
        for review in reviews_by_key.values()
        if isinstance(review, Mapping)
    ]
    blind_labels = [
        review.get("blind_label")
        for review in reviews_by_key.values()
        if isinstance(review, Mapping)
    ]
    if (
        set(reviews_by_key) != expected_review_keys
        or len(review_ids) != 6
        or len(set(review_ids)) != 6
        or len(blind_labels) != 6
        or len(set(blind_labels)) != 6
    ):
        global_binding_failures.append("blind_review_set_invalid")
    scores: list[dict[str, Any]] = []
    for host in HOSTS:
        report = loaded_reports[host]
        for scenario in SCENARIOS:
            task_case = task_by_scenario.get(scenario)
            if not isinstance(task_case, Mapping):
                raise HumanGoldValidationError("frozen task-case scenario is missing")
            gold_case = next(
                (case for case in _gold_cases(gold) if case.get("scenario") == scenario),
                None,
            )
            if gold_case is None:
                raise HumanGoldValidationError("Human Gold scenario is missing")
            review = reviews_by_key.get((host, str(task_case.get("task_case"))))
            score = _score_one(
                host=host,
                report=report,
                report_failures=report_failures[host],
                task_case=gold_case,
                review=review,
                gold=gold,
                gold_sha256=str(gold_receipt["gold_sha256"]),
                candidate_commit=bindings[host][0],
                candidate_tree=bindings[host][1],
                cross_binding_failures=global_binding_failures,
            )
            try:
                _validate_score(score)
            except Pass16EvaluationError:
                # A receipt must remain schema-valid even for a failed run.  Do
                # not emit a partially trusted score or raw diagnostic payload.
                score["status"] = "failed"
                score["release_ready"] = False
                score["claim_eligible"] = False
                _validate_score(score)
            scores.append(score)
    all_passed = len(scores) == 6 and all(score.get("status") == "passed" for score in scores)
    hard_failures = sorted({code for score in scores for code in score.get("hard_failures", [])})
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "gold_receipt": gold_receipt,
        "scores": scores,
        "aggregate": {
            "run_count": len(scores),
            "passed_runs": sum(score.get("status") == "passed" for score in scores),
            "failed_runs": sum(score.get("status") != "passed" for score in scores),
            "structural_run_set_passed": all_passed,
            # Declarations in an unsigned JSON file cannot prove a human
            # author's identity or independence.  A separate external human
            # authority must close that provenance step; this scorer never
            # upgrades structural validity into qualification eligibility.
            "authenticity_proven": False,
            "aggregate_eligible": False,
            "hard_failures": hard_failures,
            "release_ready": False,
            "claim_eligible": False,
        },
        "release_ready": False,
        "claim_eligible": False,
    }


def score(
    reports: Mapping[str, Any] | Sequence[Any],
    reviews: Mapping[Any, Any] | Sequence[Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Short alias for :func:`score_reports`."""

    return score_reports(reports, reviews, **kwargs)


def score_files(
    *,
    codex_report: Path,
    opencode_report: Path,
    gold_path: Path,
    review_paths: Mapping[tuple[str, str], Path],
    task_cases_path: Path | None = None,
    repository: Path | None = None,
) -> dict[str, Any]:
    reports = {"codex": Path(codex_report), "opencode": Path(opencode_report)}
    reviews: dict[tuple[str, str], Any] = {}
    for key, path in review_paths.items():
        value, _ = _read_bounded(Path(path), maximum=MAX_REVIEW_BYTES, label="Human review")
        reviews[key] = value
    return score_reports(
        reports,
        reviews,
        gold_path=Path(gold_path),
        task_cases_path=task_cases_path,
        repository=repository,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score Pass 16 Host continuity runs")
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--codex-report", required=True, type=Path)
    parser.add_argument("--opencode-report", required=True, type=Path)
    parser.add_argument("--review", action="append", default=[], type=str, metavar="HOST:CASE=PATH")
    parser.add_argument("--task-cases", type=Path, default=None)
    parser.add_argument("--repository", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    review_paths: dict[tuple[str, str], Path] = {}
    for raw in arguments.review:
        if "=" not in raw or ":" not in raw.split("=", 1)[0]:
            raise SystemExit("--review must be HOST:CASE=PATH")
        key, selected_path = raw.split("=", 1)
        host, task_case = key.split(":", 1)
        review_paths[(host, task_case)] = Path(selected_path)
    result = score_files(
        codex_report=arguments.codex_report,
        opencode_report=arguments.opencode_report,
        gold_path=arguments.gold,
        review_paths=review_paths,
        task_cases_path=arguments.task_cases,
        repository=arguments.repository,
    )
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if arguments.output is None:
        print(encoded)
    else:
        arguments.output.write_text(encoded + "\n", encoding="utf-8")
    return 0


load_gold_receipt = human_gold_receipt
score_pass16 = score_reports


if __name__ == "__main__":
    raise SystemExit(main())
