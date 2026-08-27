from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.release import release_policy

REPOSITORY = Path(__file__).resolve().parents[1]
V3_CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v3.json"
V4_CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v4.json"
V5_CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v5.json"
V6_CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v6.json"
V8_CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v8.json"
V9_CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v9.json"
V3_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v3.schema.json"
V4_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v4.schema.json"
V5_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v5.schema.json"
V6_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v6.schema.json"
V8_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v8.schema.json"
V9_SCHEMA = REPOSITORY / "contracts/v013-release-gate-classification.v9.schema.json"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _gates(classification: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gates = classification["gates"]
    assert isinstance(gates, list)
    return {gate["gate_id"]: gate for gate in gates}


def test_v9_is_the_active_self_consistent_classification() -> None:
    assert release_policy.V013_ACTIVE_CLASSIFICATION_PATH == V9_CLASSIFICATION
    assert release_policy.V013_ACTIVE_CLASSIFICATION_SCHEMA_PATH == V9_SCHEMA
    assert release_policy.V013_ACTIVE_CLASSIFICATION_SCHEMA_VERSION == (
        "deeplaw.v013-release-gate-classification/v9"
    )
    assert release_policy.V013_ACTIVE_CLASSIFICATION_ID == "deeplaw-v013-commercial-gates-v9"

    schema = _load(V9_SCHEMA)
    classification = _load(V9_CLASSIFICATION)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(classification)

    assert schema["$id"] == (
        "https://deeplaw.dev/contracts/v013-release-gate-classification.v9.schema.json"
    )
    assert schema["properties"]["schema_version"]["const"] == classification[
        "schema_version"
    ]
    assert schema["properties"]["classification_id"]["const"] == classification[
        "classification_id"
    ]
    assert classification["schema_version"] == (
        release_policy.V013_ACTIVE_CLASSIFICATION_SCHEMA_VERSION
    )
    assert classification["classification_id"] == release_policy.V013_ACTIVE_CLASSIFICATION_ID
    assert {
        gate["gate_id"] for gate in classification["gates"] if gate["category"] == "Core"
    } == release_policy.V013_CORE_GATE_IDS
    assert len(release_policy.V013_CORE_GATE_IDS) == 13


def test_v8_remains_historical_and_self_consistent() -> None:
    schema = _load(V8_SCHEMA)
    classification = _load(V8_CLASSIFICATION)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(classification)
    assert classification["schema_version"] == (
        "deeplaw.v013-release-gate-classification/v8"
    )


def test_v6_remains_the_legacy_commercial_envelope_classification() -> None:
    assert release_policy.V013_V6_CLASSIFICATION_PATH == V6_CLASSIFICATION
    assert release_policy.V013_V6_CLASSIFICATION_SCHEMA_PATH == V6_SCHEMA
    assert release_policy.V013_V6_CLASSIFICATION_SCHEMA_VERSION == (
        "deeplaw.v013-release-gate-classification/v6"
    )
    assert release_policy.V013_V6_CLASSIFICATION_ID == "deeplaw-v013-commercial-gates-v6"
    Draft202012Validator(_load(V6_SCHEMA)).validate(_load(V6_CLASSIFICATION))


def test_v4_is_only_the_allowed_v3_rotation() -> None:
    v3 = _load(V3_CLASSIFICATION)
    v4 = _load(V4_CLASSIFICATION)
    expected = deepcopy(v3)
    expected["schema_version"] = "deeplaw.v013-release-gate-classification/v4"
    expected["classification_id"] = "deeplaw-v013-commercial-gates-v4"
    opencode = next(gate for gate in expected["gates"] if gate["gate_id"] == "opencode")
    opencode["constraints"]["tool_version"] = "1.18.16"
    assert v4 == expected

    v4_gates = _gates(v4)
    assert v4_gates["codex"]["constraints"]["tool_version"] == "0.147.0-alpha.1.2"
    assert v4_gates["opencode"]["constraints"]["tool_version"] == "1.18.16"
    assert v4["categories"] == v3["categories"]
    assert v4["assembly_policy"] == v3["assembly_policy"]


def test_v5_only_rotates_the_current_host_receipt_contract() -> None:
    v4 = _load(V4_CLASSIFICATION)
    v5 = _load(V5_CLASSIFICATION)
    expected = deepcopy(v4)
    expected["schema_version"] = "deeplaw.v013-release-gate-classification/v5"
    expected["classification_id"] = "deeplaw-v013-commercial-gates-v5"
    for gate in expected["gates"]:
        if gate["gate_id"] in {"codex", "opencode"}:
            gate["accepted_input_schema_versions"] = [
                "deeplaw.host-continuity-qualification/v2"
            ]
    assert v5 == expected
    assert v5["categories"] == v4["categories"]
    assert v5["assembly_policy"] == v4["assembly_policy"]


def test_v3_classification_and_schema_bytes_remain_frozen() -> None:
    expected = {
        V3_CLASSIFICATION: "c09209112e8656fc62be4b535cc93b092bc3ef2a1818418f7ffcfe40a7879e0a",
        V3_SCHEMA: "89a75e066ab83adb5e56108e1548fdb21e2dd57dc3fd7a4b64c4307a1ca0cbbf",
    }
    for path, digest in expected.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_v4_schema_is_only_a_versioned_copy_of_v3_schema() -> None:
    v3 = _load(V3_SCHEMA)
    v4 = _load(V4_SCHEMA)
    expected = deepcopy(v3)
    expected["$id"] = "https://deeplaw.dev/contracts/v013-release-gate-classification.v4.schema.json"
    expected["title"] = "DeepLaw v0.13 provenance-bound release gate classification v4"
    expected["properties"]["schema_version"]["const"] = (
        "deeplaw.v013-release-gate-classification/v4"
    )
    expected["properties"]["classification_id"]["const"] = "deeplaw-v013-commercial-gates-v4"
    assert v4 == expected


def test_v4_bytes_are_frozen_and_v5_schema_is_only_a_versioned_copy() -> None:
    assert hashlib.sha256(V4_CLASSIFICATION.read_bytes()).hexdigest() == (
        "07079b9f00021753426db7a98eb2ada4be05a50af96e8c6fc6565b94128d7c58"
    )
    assert hashlib.sha256(V4_SCHEMA.read_bytes()).hexdigest() == (
        "168afeb255883d3baf2fef85cb5c0ed7d236dd4857228e76d8c379663bd28c3d"
    )
    v4 = _load(V4_SCHEMA)
    v5 = _load(V5_SCHEMA)
    expected = deepcopy(v4)
    expected["$id"] = (
        "https://deeplaw.dev/contracts/v013-release-gate-classification.v5.schema.json"
    )
    expected["title"] = "DeepLaw v0.13 provenance-bound release gate classification v5"
    expected["properties"]["schema_version"]["const"] = (
        "deeplaw.v013-release-gate-classification/v5"
    )
    expected["properties"]["classification_id"]["const"] = (
        "deeplaw-v013-commercial-gates-v5"
    )
    assert v5 == expected
