"""Deterministic semantic validation for the v0.13 commercial evidence report.

The closed report contains observations, not caller-declared pass/release/claim decisions.
:func:`validate_report` reads every embedded artifact, checks its independent digest and derives
all statuses from those observations.  This keeps a hash-valid arbitrary document or stale binding
from becoming release evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPORT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / (
    "commercial-evidence-report.v1.schema.json"
)
RELEASE_MANIFEST_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / (
    "commercial-release-manifest.v6.schema.json"
)
CLASSIFICATION_PATH = Path(__file__).with_name("v013-gate-classification-v1.json")
REPORT_SCHEMA_VERSION = "deeplaw.commercial-evidence-report/v1"
OBSERVATION_SCHEMA_VERSION = "deeplaw.commercial-gate-observation/v1"
STATUS_VALUES = frozenset(
    {"passed", "failed", "not_applicable", "not_executed", "not_claimed"}
)
MAX_REPORT_BYTES = 1_000_000
MAX_DEPTH = 16
MAX_NODES = 50_000
MAX_STRING_LENGTH = 8_192

_SECRET_CANARY_RE = re.compile(
    r"(?i)DEEPLAW_TEST_AMBIENT_SECRET"
    r"|(?:secret|provider|ambient|private)[^A-Za-z0-9]{0,32}canary"
    r"|canary[^A-Za-z0-9]{0,32}(?:secret|provider|ambient|private)"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9])/(?:Users|home|private|var|tmp|root|etc|opt|Volumes)(?:/|$))"
    r"|(?:^|[\s\"'])~/(?:[^\s\"']*)"
    r"|(?:^|[\s\"'])[A-Za-z]:[\\/](?:[^\s\"']*)",
    re.IGNORECASE,
)


class SemanticEvidenceError(ValueError):
    """Raised when a report is not a closed, safely bound evidence object."""


SemanticEvidenceValidationError = SemanticEvidenceError


@dataclass(frozen=True)
class ExpectedBindings:
    """Optional external bindings supplied by the release owner.

    All populated fields are compared exactly.  Callers may provide only a subset for a local
    diagnostic, while a release decision should populate every field.
    """

    candidate_commit: str | None = None
    candidate_tree: str | None = None
    wheel_sha256: str | None = None
    sdist_sha256: str | None = None
    protocol_sha256: str | None = None
    threshold_sha256: str | None = None
    gold_sha256: str | None = None
    protocol_id: str | None = None
    threshold_id: str | None = None
    corpus_role: str | None = None

    @classmethod
    def from_values(
        cls,
        expected: Mapping[str, Any] | None = None,
        **values: Any,
    ) -> ExpectedBindings:
        """Normalize direct keyword and mapping forms used by callers and tests."""

        merged: dict[str, Any] = {}
        if expected is not None:
            merged.update(expected)
            candidate = expected.get("candidate_binding")
            if isinstance(candidate, Mapping):
                merged.update(candidate)
            protocol = expected.get("protocol_binding")
            if isinstance(protocol, Mapping):
                merged.update(protocol)
                if "protocol_sha256" in protocol:
                    merged["protocol_sha256"] = protocol["protocol_sha256"]
            threshold = expected.get("threshold_binding")
            if isinstance(threshold, Mapping):
                merged.update(threshold)
                if "threshold_sha256" in threshold:
                    merged["threshold_sha256"] = threshold["threshold_sha256"]
            gold = expected.get("gold_binding")
            if isinstance(gold, Mapping):
                merged.update(gold)
                if "gold_sha256" in gold:
                    merged["gold_sha256"] = gold["gold_sha256"]
        merged.update({key: value for key, value in values.items() if value is not None})

        aliases = {
            "expected_candidate_commit": "candidate_commit",
            "expected_candidate_tree": "candidate_tree",
            "expected_wheel_sha256": "wheel_sha256",
            "expected_sdist_sha256": "sdist_sha256",
            "expected_candidate_wheel_sha256": "wheel_sha256",
            "expected_candidate_sdist_sha256": "sdist_sha256",
            "expected_protocol_sha256": "protocol_sha256",
            "expected_threshold_sha256": "threshold_sha256",
            "expected_gold_sha256": "gold_sha256",
            "expected_protocol_id": "protocol_id",
            "expected_threshold_id": "threshold_id",
            "expected_corpus_role": "corpus_role",
            "candidate_wheel_sha256": "wheel_sha256",
            "candidate_sdist_sha256": "sdist_sha256",
            "candidate_protocol_sha256": "protocol_sha256",
            "candidate_threshold_sha256": "threshold_sha256",
        }
        normalized: dict[str, Any] = {}
        for key, value in merged.items():
            normalized[aliases.get(key, key)] = value
        allowed = {
            "candidate_commit",
            "candidate_tree",
            "wheel_sha256",
            "sdist_sha256",
            "protocol_sha256",
            "threshold_sha256",
            "gold_sha256",
            "protocol_id",
            "threshold_id",
            "corpus_role",
        }
        return cls(**{key: normalized[key] for key in allowed if key in normalized})

    def populated(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "candidate_commit": self.candidate_commit,
                "candidate_tree": self.candidate_tree,
                "wheel_sha256": self.wheel_sha256,
                "sdist_sha256": self.sdist_sha256,
                "protocol_sha256": self.protocol_sha256,
                "threshold_sha256": self.threshold_sha256,
                "gold_sha256": self.gold_sha256,
                "protocol_id": self.protocol_id,
                "threshold_id": self.threshold_id,
                "corpus_role": self.corpus_role,
            }.items()
            if value is not None
        }


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used for all evidence digests."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_payload_sha256(value: Mapping[str, Any], *, self_field: str) -> str:
    """Hash a canonical payload after removing its self-digest field."""

    if not isinstance(value, Mapping):
        raise TypeError("canonical payload must be a JSON object")
    payload = {key: item for key, item in value.items() if key != self_field}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def artifact_sha256(artifact: Mapping[str, Any]) -> str:
    """Return the independent artifact digest (excluding ``artifact_sha256`` itself)."""

    return canonical_payload_sha256(artifact, self_field="artifact_sha256")


def report_sha256(report: Mapping[str, Any]) -> str:
    """Return the report digest (excluding ``report_sha256`` itself)."""

    return canonical_payload_sha256(report, self_field="report_sha256")


# Friendly aliases for integrations that use the longer name.
canonical_payload_digest = canonical_payload_sha256
canonical_artifact_sha256 = artifact_sha256
canonical_report_sha256 = report_sha256


def _load_json_file(path: Path) -> dict[str, Any]:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise SemanticEvidenceError("evidence report must be a regular file")
    selected = candidate.resolve(strict=True)
    if not selected.is_file():
        raise SemanticEvidenceError("evidence report must be a regular file")
    if selected.stat().st_size > MAX_REPORT_BYTES:
        raise SemanticEvidenceError("evidence report exceeds the bounded input size")
    try:
        text = selected.read_text(encoding="utf-8")
        payload = json.loads(text, parse_constant=_reject_nonfinite_json)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SemanticEvidenceError(f"invalid evidence report JSON: {error}") from error
    if not isinstance(payload, dict):
        raise SemanticEvidenceError("evidence report must be a JSON object")
    return payload


def _reject_nonfinite_json(value: str) -> Any:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _as_document(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(value, (str, Path)):
        return _load_json_file(Path(value))
    if not isinstance(value, Mapping):
        raise SemanticEvidenceError("evidence report must be a mapping or JSON path")
    document = dict(value)
    _check_bounds_and_secrets(document)
    return document


def _check_bounds_and_secrets(
    value: Any, *, depth: int = 0, state: list[int] | None = None
) -> None:
    if state is None:
        state = [0]
    state[0] += 1
    if state[0] > MAX_NODES:
        raise SemanticEvidenceError("evidence input exceeds the node bound")
    if depth > MAX_DEPTH:
        raise SemanticEvidenceError("evidence input exceeds the nesting bound")
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise SemanticEvidenceError("evidence string exceeds the bounded input size")
        if _SECRET_CANARY_RE.search(value) or _ABSOLUTE_PATH_RE.search(value):
            raise SemanticEvidenceError("secret canary or private absolute path in evidence")
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise SemanticEvidenceError("non-finite evidence number")
        return
    if isinstance(value, bool):
        return
    if isinstance(value, Mapping):
        if len(value) > 128:
            raise SemanticEvidenceError("evidence object exceeds the key bound")
        for key, item in value.items():
            if not isinstance(key, str):
                raise SemanticEvidenceError("evidence object keys must be strings")
            _check_bounds_and_secrets(item, depth=depth + 1, state=state)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > 256:
            raise SemanticEvidenceError("evidence array exceeds the item bound")
        for item in value:
            _check_bounds_and_secrets(item, depth=depth + 1, state=state)
        return
    if value is not None:
        raise SemanticEvidenceError(f"unsupported evidence value type: {type(value).__name__}")


def _schema_validate(report: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(REPORT_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        errors = sorted(
            Draft202012Validator(schema).iter_errors(report),
            key=lambda error: list(error.path),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SemanticEvidenceError(
            f"commercial evidence schema is unavailable: {error}"
        ) from error
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "$"
        raise SemanticEvidenceError(
            f"commercial evidence schema violation at {location}: {first.message}"
        )


def _classification_payload(value: Mapping[str, Any] | str | Path | None) -> dict[str, Any]:
    if value is None:
        return _load_json_file(CLASSIFICATION_PATH)
    return _as_document(value)


def _classification_map(classification: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    gates = classification.get("gates")
    if not isinstance(gates, list):
        raise SemanticEvidenceError("gate classification must contain a gate array")
    result: dict[str, dict[str, Any]] = {}
    for gate in gates:
        if not isinstance(gate, Mapping):
            raise SemanticEvidenceError("gate classification contains a non-object gate")
        gate_id = gate.get("gate_id")
        if not isinstance(gate_id, str) or gate_id in result:
            raise SemanticEvidenceError("gate classification contains duplicate or invalid gate")
        result[gate_id] = dict(gate)
    expected_categories = {
        "Core": {
            "canonical_integrity",
            "migration_recovery",
            "secret_host_isolation",
            "bounded_context",
            "legal_evidence",
            "source_citation_locator",
            "scale_performance",
            "supported_platforms",
            "reproducible_supply_chain",
            "human_gold_isolation",
            "codex",
            "selective_forget",
        },
        "Capability": {"timeline", "semantic_restore", "claude", "opencode"},
        "Competitive Claim": {"comparative_incremental_benefit", "superiority", "sota"},
    }
    observed: dict[str, set[str]] = {key: set() for key in expected_categories}
    for gate_id, gate in result.items():
        category = gate.get("category")
        if category not in observed or gate.get("category_id") != category.casefold().replace(
            " ", "_"
        ):
            raise SemanticEvidenceError(f"invalid gate classification category for {gate_id}")
        observed[category].add(gate_id)
    if observed != expected_categories:
        raise SemanticEvidenceError("v0.13 gate classification does not match the frozen core set")
    categories = classification.get("categories")
    if not isinstance(categories, list):
        raise SemanticEvidenceError("gate classification must contain category declarations")
    category_rows = {
        row.get("category"): row for row in categories if isinstance(row, Mapping)
    }
    if set(category_rows) != set(expected_categories):
        raise SemanticEvidenceError("gate classification categories are incomplete")
    for category, gate_ids in expected_categories.items():
        row = category_rows[category]
        required = category == "Core"
        if (
            set(row.get("gate_ids", [])) != gate_ids
            or row.get("required") is not required
            or row.get("not_claimed_allowed") is not (not required)
        ):
            raise SemanticEvidenceError(
                f"gate classification category semantics differ for {category}"
            )
        for gate_id in gate_ids:
            if result[gate_id].get("required") is not required:
                raise SemanticEvidenceError(
                    f"gate classification required flag differs for {gate_id}"
                )
    return result


def _compare_bindings(report: Mapping[str, Any], expected: ExpectedBindings) -> None:
    candidate = report["candidate_binding"]
    observed = {
        "candidate_commit": candidate["candidate_commit"],
        "candidate_tree": candidate["candidate_tree"],
        "wheel_sha256": candidate["candidate_wheel_sha256"],
        "sdist_sha256": candidate["candidate_sdist_sha256"],
        "protocol_sha256": report["protocol_binding"]["protocol_sha256"],
        "threshold_sha256": report["threshold_binding"]["threshold_sha256"],
        "gold_sha256": report["gold_binding"]["gold_sha256"],
        "protocol_id": report["protocol_binding"]["protocol_id"],
        "threshold_id": report["threshold_binding"]["threshold_id"],
        "corpus_role": report["corpus"]["role"],
    }
    for key, value in expected.populated().items():
        if observed.get(key) != value:
            raise SemanticEvidenceError(
                "evidence binding mismatch for "
                f"{key}: expected {value!r}, observed {observed.get(key)!r}"
            )


def _validate_corpus(report: Mapping[str, Any], expected: ExpectedBindings) -> list[str]:
    corpus = report["corpus"]
    role = corpus["role"]
    source = corpus["source"]
    frozen = corpus["frozen"]
    issues: list[str] = []
    if report["protocol_binding"]["frozen"] is not True:
        issues.append("protocol_not_frozen")
    if report["threshold_binding"]["frozen"] is not True:
        issues.append("threshold_not_frozen")
    if report["gold_binding"]["frozen"] is not True:
        issues.append("gold_not_frozen")
    if role == "development":
        if source != "repository":
            raise SemanticEvidenceError("development corpus must be repository-resident")
        if expected.corpus_role in {"qualification_holdout", "final_blind"}:
            raise SemanticEvidenceError("development corpus cannot satisfy a blind expected role")
        issues.append("development_corpus_is_not_blind_evidence")
    elif source != "repository_external" or frozen is not True:
        raise SemanticEvidenceError(
            "qualification or final-blind corpus must be frozen and external"
        )
    gold = report["gold_binding"]
    if role == "final_blind":
        if (
            gold["role"] != "final_blind_gold"
            or gold["source"] != "repository_external"
            or not gold["frozen"]
        ):
            raise SemanticEvidenceError("final-blind evidence must bind frozen external Gold")
    elif role == "qualification_holdout" and (
        gold["role"] != "qualification_gold"
        or gold["source"] != "repository_external"
        or not gold["frozen"]
    ):
        raise SemanticEvidenceError("qualification evidence must bind frozen external Gold")
    return issues


def _validate_artifact_digests(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for artifact in report["artifacts"]:
        artifact_id = artifact["artifact_id"]
        if artifact_id in artifacts:
            raise SemanticEvidenceError(f"duplicate evidence artifact: {artifact_id}")
        expected = artifact_sha256(artifact)
        if artifact["artifact_sha256"] != expected:
            raise SemanticEvidenceError(f"artifact {artifact_id} has an invalid self digest")
        if artifact["gate_id"] != artifact["content"]["gate_id"]:
            raise SemanticEvidenceError(f"artifact {artifact_id} gate identity is inconsistent")
        artifacts[artifact_id] = dict(artifact)
    return artifacts


def _observation_binding(observation: Mapping[str, Any]) -> dict[str, str]:
    bindings = observation["bindings"]
    return {
        "candidate_commit": bindings["candidate_commit"],
        "candidate_tree": bindings["candidate_tree"],
        "wheel_sha256": bindings["candidate_wheel_sha256"],
        "sdist_sha256": bindings["candidate_sdist_sha256"],
        "protocol_sha256": bindings["protocol_sha256"],
        "threshold_sha256": bindings["threshold_sha256"],
        "gold_sha256": bindings["gold_sha256"],
    }


def _report_binding_values(report: Mapping[str, Any]) -> dict[str, str]:
    candidate = report["candidate_binding"]
    return {
        "candidate_commit": candidate["candidate_commit"],
        "candidate_tree": candidate["candidate_tree"],
        "wheel_sha256": candidate["candidate_wheel_sha256"],
        "sdist_sha256": candidate["candidate_sdist_sha256"],
        "protocol_sha256": report["protocol_binding"]["protocol_sha256"],
        "threshold_sha256": report["threshold_binding"]["threshold_sha256"],
        "gold_sha256": report["gold_binding"]["gold_sha256"],
    }


def _derive_observation_status(
    observation: Mapping[str, Any],
    *,
    classification: Mapping[str, Any],
    corpus: Mapping[str, Any],
    corpus_issues: Sequence[str],
) -> tuple[str, list[str]]:
    gate_id = observation["gate_id"]
    definition = classification[gate_id]
    category = definition["category"]
    issues = list(corpus_issues)
    if observation.get("applicability") == "not_applicable":
        if category == "Core":
            return "failed", ["core_gate_cannot_be_not_applicable"]
        return "not_applicable", []

    command = observation["command"]
    if command["exit_code"] != 0:
        issues.append("command_exit_nonzero")
    if command["run_count"] < definition["minimum_runs"]:
        issues.append("run_count_below_frozen_minimum")
    environment = observation["environment"]
    if definition["model_required"] and environment["model_id"] is None:
        issues.append("exact_model_identity_missing")
    expected_thresholds = {
        item["metric"]: (item["minimum"], item["maximum"])
        for item in definition["thresholds"]
    }
    observed_thresholds = {item["metric"]: item for item in observation["thresholds"]}
    if len(observed_thresholds) != len(observation["thresholds"]):
        raise SemanticEvidenceError(f"artifact {gate_id} has duplicate threshold metrics")
    if set(observed_thresholds) != set(expected_thresholds):
        raise SemanticEvidenceError(
            f"artifact {gate_id} threshold inventory differs from the frozen classification"
        )
    for metric, threshold in observed_thresholds.items():
        expected_minimum, expected_maximum = expected_thresholds[metric]
        if (
            threshold["minimum"] != expected_minimum
            or threshold["maximum"] != expected_maximum
        ):
            raise SemanticEvidenceError(
                f"artifact {gate_id} threshold bounds differ for {metric}"
            )
        observed = threshold["observed"]
        minimum = threshold["minimum"]
        maximum = threshold["maximum"]
        if not math.isfinite(observed):
            issues.append(f"threshold_non_finite:{threshold['metric']}")
            continue
        if minimum is None and maximum is None:
            issues.append(f"threshold_has_no_bound:{threshold['metric']}")
        if minimum is not None and observed < minimum:
            issues.append(f"threshold_below_minimum:{threshold['metric']}")
        if maximum is not None and observed > maximum:
            issues.append(f"threshold_above_maximum:{threshold['metric']}")
    hard_failures = {item["failure_id"]: item for item in observation["hard_failures"]}
    if len(hard_failures) != len(observation["hard_failures"]):
        raise SemanticEvidenceError(f"artifact {gate_id} has duplicate hard-zero counters")
    if set(hard_failures) != set(definition["hard_zero_ids"]):
        raise SemanticEvidenceError(
            f"artifact {gate_id} hard-zero inventory differs from the frozen classification"
        )
    for failure in hard_failures.values():
        if failure["count"] > failure["maximum_allowed"]:
            issues.append(f"hard_zero_nonzero:{failure['failure_id']}")
    for failure in observation["failure_inventory"]:
        if failure["count"] > 0:
            issues.append(f"failure_inventory_nonempty:{failure['failure_id']}")
    redaction = observation["redaction"]
    if redaction["secret_canary_count"] != 0:
        issues.append("secret_canary_observed")
    if redaction["private_path_count"] != 0:
        issues.append("private_absolute_path_observed")
    if redaction["output_redacted"] is not True:
        issues.append("redaction_not_confirmed")
    if observation["corpus"] != corpus:
        raise SemanticEvidenceError(f"artifact {gate_id} reports a different corpus binding")
    if issues:
        return "failed", issues
    return "passed", []


def _normalise_expected(
    expected: Mapping[str, Any] | ExpectedBindings | None,
    direct: Mapping[str, Any],
) -> ExpectedBindings:
    if isinstance(expected, ExpectedBindings):
        values = expected
        if any(value is not None for value in direct.values()):
            return ExpectedBindings.from_values(
                {key: value for key, value in values.populated().items()}, **direct
            )
        return values
    return ExpectedBindings.from_values(expected, **direct)


def validate_report(
    report: Mapping[str, Any] | str | Path,
    expected: Mapping[str, Any] | ExpectedBindings | None = None,
    *,
    expected_candidate_commit: str | None = None,
    expected_candidate_tree: str | None = None,
    expected_wheel_sha256: str | None = None,
    expected_sdist_sha256: str | None = None,
    expected_protocol_sha256: str | None = None,
    expected_threshold_sha256: str | None = None,
    expected_gold_sha256: str | None = None,
    expected_protocol_id: str | None = None,
    expected_threshold_id: str | None = None,
    expected_corpus_role: str | None = None,
    classification: Mapping[str, Any] | str | Path | None = None,
) -> dict[str, Any]:
    """Validate and semantically derive a commercial evidence report.

    Structural, digest, binding and privacy violations raise :class:`SemanticEvidenceError`.
    A well-formed report whose observations fail a gate returns a deterministic ``status`` of
    ``failed``/``not_executed`` rather than trusting any declared booleans in the report.
    """

    document = _as_document(report)
    _check_bounds_and_secrets(document)
    _schema_validate(document)
    if document["schema_version"] != REPORT_SCHEMA_VERSION:
        raise SemanticEvidenceError("unsupported commercial evidence report schema")
    if document["report_kind"] != "v013_commercial_gate_collection":
        raise SemanticEvidenceError("unsupported commercial evidence report kind")
    if document["report_sha256"] != report_sha256(document):
        raise SemanticEvidenceError("commercial evidence report has an invalid self digest")

    classification_document = _classification_payload(classification)
    _check_bounds_and_secrets(classification_document)
    try:
        classification_schema = json.loads(
            (
                REPORT_SCHEMA_PATH.parent / "v013-release-gate-classification.v1.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(classification_schema)
        Draft202012Validator(classification_schema).validate(classification_document)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SemanticEvidenceError(f"gate classification is unavailable: {error}") from error
    except Exception as error:
        raise SemanticEvidenceError(f"gate classification is invalid: {error}") from error
    classification_map = _classification_map(classification_document)
    bindings = _normalise_expected(
        expected,
        {
            "expected_candidate_commit": expected_candidate_commit,
            "expected_candidate_tree": expected_candidate_tree,
            "expected_wheel_sha256": expected_wheel_sha256,
            "expected_sdist_sha256": expected_sdist_sha256,
            "expected_protocol_sha256": expected_protocol_sha256,
            "expected_threshold_sha256": expected_threshold_sha256,
            "expected_gold_sha256": expected_gold_sha256,
            "expected_protocol_id": expected_protocol_id,
            "expected_threshold_id": expected_threshold_id,
            "expected_corpus_role": expected_corpus_role,
        },
    )
    _compare_bindings(document, bindings)
    corpus_issues = _validate_corpus(document, bindings)
    artifacts = _validate_artifact_digests(document)
    report_binding = _report_binding_values(document)

    declarations: dict[str, dict[str, Any]] = {}
    for declaration in document["gates"]:
        gate_id = declaration["gate_id"]
        if gate_id in declarations:
            raise SemanticEvidenceError(f"duplicate gate declaration: {gate_id}")
        definition = classification_map.get(gate_id)
        if definition is None:
            raise SemanticEvidenceError(f"gate is not in the frozen classification: {gate_id}")
        if declaration["category"] != definition["category"]:
            raise SemanticEvidenceError(f"gate category mismatch: {gate_id}")
        artifact_id = declaration["artifact_id"]
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            raise SemanticEvidenceError(f"gate {gate_id} references a missing artifact")
        if artifact["gate_id"] != gate_id:
            raise SemanticEvidenceError(f"gate {gate_id} references an unrelated artifact")
        observation = artifact["content"]
        if _observation_binding(observation) != report_binding:
            raise SemanticEvidenceError(
                f"artifact {artifact_id} has a stale candidate/protocol binding"
            )
        declarations[gate_id] = declaration
    declared_artifacts = {item["artifact_id"] for item in declarations.values()}
    if declared_artifacts != set(artifacts):
        raise SemanticEvidenceError("evidence report contains an unreferenced artifact")

    computed: dict[str, dict[str, Any]] = {}
    for gate_id, definition in classification_map.items():
        declaration = declarations.get(gate_id)
        if declaration is None:
            status = "not_executed" if definition["category"] == "Core" else "not_claimed"
            computed[gate_id] = {"category": definition["category"], "status": status, "issues": []}
            continue
        artifact = artifacts[declaration["artifact_id"]]
        status, issues = _derive_observation_status(
            artifact["content"],
            classification=classification_map,
            corpus=document["corpus"],
            corpus_issues=corpus_issues,
        )
        computed[gate_id] = {"category": definition["category"], "status": status, "issues": issues}

    core_statuses = [item["status"] for item in computed.values() if item["category"] == "Core"]
    if any(status == "failed" for status in core_statuses):
        overall_status = "failed"
    elif any(status == "not_executed" for status in core_statuses):
        overall_status = "not_executed"
    elif any(status == "not_applicable" for status in core_statuses):
        overall_status = "failed"
    elif core_statuses and all(status == "passed" for status in core_statuses):
        overall_status = "passed"
    else:
        overall_status = "not_executed"
    hard_zero = all(
        failure["count"] == 0
        for artifact in artifacts.values()
        for failure in artifact["content"]["hard_failures"]
    ) and all(
        artifact["content"]["redaction"]["secret_canary_count"] == 0
        and artifact["content"]["redaction"]["private_path_count"] == 0
        for artifact in artifacts.values()
    )
    release_ready = (
        overall_status == "passed"
        and document["corpus"]["role"] in {"qualification_holdout", "final_blind"}
        and document["corpus"]["frozen"] is True
        and hard_zero
    )
    claim_eligible = release_ready
    competitive = claim_eligible and all(
        item["status"] == "passed"
        for item in computed.values()
        if item["category"] == "Competitive Claim"
    )
    return {
        "status": overall_status,
        "gate_statuses": {gate_id: item["status"] for gate_id, item in computed.items()},
        "computed": computed,
        "hard_zero": hard_zero,
        "release_ready": release_ready,
        "claim_eligible": claim_eligible,
        "competitive_claim_eligible": competitive,
    }


def validate_report_file(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    """Path-oriented alias for integrations that keep reports on disk."""

    return validate_report(Path(path), **kwargs)


def validate_semantic_evidence(
    report: Mapping[str, Any] | str | Path,
    expected: Mapping[str, Any] | ExpectedBindings | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility alias for the public validator seam."""

    return validate_report(report, expected, **kwargs)


def _asset_path(assets_root: Path, logical_path: str) -> Path:
    if (
        not isinstance(logical_path, str)
        or not logical_path
        or "\\" in logical_path
        or logical_path.startswith("/")
        or any(part in {"", ".", ".."} for part in logical_path.split("/"))
    ):
        raise SemanticEvidenceError("semantic evidence uses an unsafe artifact path")
    root = assets_root.expanduser().resolve(strict=True)
    candidate = root.joinpath(*logical_path.split("/"))
    if not candidate.exists():
        candidate = root / logical_path.rsplit("/", maxsplit=1)[-1]
    if candidate.is_symlink():
        raise SemanticEvidenceError("semantic evidence artifact must not be a symbolic link")
    selected = candidate.resolve(strict=True)
    if not selected.is_file() or not selected.is_relative_to(root):
        raise SemanticEvidenceError("semantic evidence artifact escapes the asset root")
    return selected


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_release_manifest_semantics(
    manifest: Mapping[str, Any] | str | Path,
    *,
    assets_root: str | Path,
) -> dict[str, Any]:
    """Validate actual v6 report/classification bytes and the derived manifest receipt."""

    document = _as_document(manifest)
    _check_bounds_and_secrets(document)
    try:
        manifest_schema = json.loads(RELEASE_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(manifest_schema)
        errors = sorted(
            Draft202012Validator(manifest_schema).iter_errors(document),
            key=lambda error: list(error.path),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SemanticEvidenceError(f"release manifest schema is unavailable: {error}") from error
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.path) or "$"
        raise SemanticEvidenceError(
            f"release manifest schema violation at {location}: {first.message}"
        )
    if document.get("schema_version") != "deeplaw.commercial-release-manifest/v6":
        raise SemanticEvidenceError("semantic release validation requires manifest v6")
    try:
        bindings = document["bindings"]
        semantic = document["semantic_evidence"]
        artifacts = document["artifacts"]
    except KeyError as error:
        raise SemanticEvidenceError("semantic release manifest is incomplete") from error
    if not isinstance(bindings, Mapping) or not isinstance(semantic, Mapping):
        raise SemanticEvidenceError("semantic release manifest bindings are invalid")
    if not isinstance(artifacts, list):
        raise SemanticEvidenceError("semantic release artifact inventory is invalid")
    basenames = [
        item.get("path", "").rsplit("/", maxsplit=1)[-1]
        for item in artifacts
        if isinstance(item, Mapping)
    ]
    if len(basenames) != len(set(basenames)):
        raise SemanticEvidenceError("semantic release artifact basenames must be unique")
    artifact_index = {
        item.get("path"): item
        for item in artifacts
        if isinstance(item, Mapping) and isinstance(item.get("path"), str)
    }
    if len(artifact_index) != len(artifacts):
        raise SemanticEvidenceError("semantic release artifact paths must be unique")
    report_path = _asset_path(Path(assets_root), semantic["report_path"])
    report_artifact_sha256 = _file_sha256(report_path)
    if report_artifact_sha256 != semantic["report_artifact_sha256"]:
        raise SemanticEvidenceError("semantic report artifact hash differs from actual bytes")
    report_record = artifact_index.get(semantic["report_path"])
    if (
        not isinstance(report_record, Mapping)
        or report_record.get("sha256") != report_artifact_sha256
        or report_record.get("byte_size") != report_path.stat().st_size
    ):
        raise SemanticEvidenceError("semantic report artifact is not bound by the manifest")

    classification_path = _asset_path(Path(assets_root), bindings["gate_classification_path"])
    classification_sha256 = _file_sha256(classification_path)
    if classification_sha256 != bindings["gate_classification_sha256"]:
        raise SemanticEvidenceError("gate classification hash differs from actual bytes")
    classification_record = artifact_index.get(bindings["gate_classification_path"])
    if (
        not isinstance(classification_record, Mapping)
        or classification_record.get("sha256") != classification_sha256
        or classification_record.get("byte_size") != classification_path.stat().st_size
    ):
        raise SemanticEvidenceError("gate classification is not bound by the manifest")

    report = _load_json_file(report_path)
    result = validate_report(
        report,
        expected_candidate_commit=bindings["candidate_commit"],
        expected_candidate_tree=bindings["candidate_tree"],
        expected_wheel_sha256=bindings["candidate_wheel_sha256"],
        expected_sdist_sha256=bindings["candidate_sdist_sha256"],
        expected_protocol_sha256=bindings["qualification_protocol_sha256"],
        expected_threshold_sha256=bindings["thresholds_sha256"],
        expected_gold_sha256=bindings["human_gold_manifest_sha256"],
        expected_corpus_role="final_blind",
        classification=classification_path,
    )
    if semantic.get("report_record_sha256") != report["report_sha256"]:
        raise SemanticEvidenceError("semantic report record digest differs from actual content")
    classification = _classification_map(_load_json_file(classification_path))
    expected_statuses = [
        {
            "gate_id": gate_id,
            "category": classification[gate_id]["category"],
            "status": result["gate_statuses"][gate_id],
        }
        for gate_id in sorted(classification)
    ]
    expected_receipt = {
        "report_path": semantic["report_path"],
        "report_artifact_sha256": report_artifact_sha256,
        "report_record_sha256": report["report_sha256"],
        "report_kind": report["report_kind"],
        "status": result["status"],
        "hard_zero": result["hard_zero"],
        "release_ready": result["release_ready"],
        "claim_eligible": result["claim_eligible"],
        "competitive_claim_eligible": result["competitive_claim_eligible"],
        "gate_statuses": expected_statuses,
    }
    if dict(semantic) != expected_receipt:
        raise SemanticEvidenceError("manifest semantic receipt differs from validated report")
    return result


def _main() -> int:
    parser = argparse.ArgumentParser(description="Validate v0.13 semantic release evidence")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_release_manifest_semantics(
            args.manifest,
            assets_root=args.assets_root,
        )
    except (OSError, SemanticEvidenceError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(canonical_json(result))
    return 0


__all__ = [
    "CLASSIFICATION_PATH",
    "RELEASE_MANIFEST_SCHEMA_PATH",
    "REPORT_SCHEMA_PATH",
    "STATUS_VALUES",
    "ExpectedBindings",
    "SemanticEvidenceError",
    "SemanticEvidenceValidationError",
    "artifact_sha256",
    "canonical_artifact_sha256",
    "canonical_json",
    "canonical_payload_digest",
    "canonical_payload_sha256",
    "canonical_report_sha256",
    "report_sha256",
    "validate_release_manifest_semantics",
    "validate_report",
    "validate_report_file",
    "validate_semantic_evidence",
]


if __name__ == "__main__":
    raise SystemExit(_main())
