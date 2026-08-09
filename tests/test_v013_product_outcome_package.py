from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.v013.product_outcome_package import (
    ProductOutcomePackageError,
    build_synthetic_fixture,
    canonical_json,
    package_sha256,
    result_sha256,
    validate_product_outcome_package,
)

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/v013-product-outcome-package.v1.schema.json"


def _fixture(tmp_path: Path) -> dict[str, object]:
    return build_synthetic_fixture(tmp_path)


def _refresh(package: dict[str, object]) -> None:
    package["package_sha256"] = package_sha256(package)  # type: ignore[arg-type]


def _rewrite_gate(
    tmp_path: Path,
    package: dict[str, object],
    product: str,
    mutate,
) -> None:
    outcome = next(  # type: ignore[union-attr]
        item for item in package["outcomes"] if item["product_id"] == product
    )
    descriptor = next(  # type: ignore[union-attr]
        item
        for item in package["artifacts"]
        if item["artifact_id"] == outcome["gate_result_artifact_id"]
    )
    path = tmp_path / descriptor["relative_path"]
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    document["result_sha256"] = result_sha256(document)
    raw = canonical_json(document).encode("utf-8")
    path.write_bytes(raw)
    descriptor["byte_size"] = len(raw)
    descriptor["file_sha256"] = hashlib.sha256(raw).hexdigest()
    descriptor["record_sha256"] = document["result_sha256"]
    _refresh(package)


def test_contract_is_closed_and_synthetic_dry_run_is_not_external_evidence(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    package = _fixture(tmp_path)
    Draft202012Validator(schema).validate(package)
    assert validate_product_outcome_package(package, root=tmp_path) == package
    assert package["benchmark_only"] is True
    assert package["claim_eligible"] is False
    assert package["assembly_policy"]["assembly_enabled"] is False
    assert package["evidence_kind"] == "synthetic_dry_run"
    assert package["lifecycle_status"] == "prepared_not_executed"
    assert package["package_version"] == "0.12.0"
    assert package["candidate_binding"]["package_version"] == "0.12.0"
    assert {row["product_id"] for row in package["outcomes"]} == {
        "continuity",
        "wiki",
        "legal",
    }
    assert all(row["status"] == "prepared_not_executed" for row in package["outcomes"])


def test_content_addressing_reopens_every_artifact_and_rejects_drift(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    (tmp_path / "candidate" / "deeplaw-0.12.0-py3-none-any.whl").write_bytes(b"drift")
    with pytest.raises(ProductOutcomePackageError, match=r"byte_size|file_sha256"):
        validate_product_outcome_package(package, root=tmp_path)

    package = _fixture(tmp_path)
    package["artifacts"][0]["relative_path"] = (
        "candidate/../candidate/deeplaw-0.12.0-py3-none-any.whl"  # type: ignore[index]
    )
    _refresh(package)
    with pytest.raises(ProductOutcomePackageError, match=r"schema violation|relative POSIX"):
        validate_product_outcome_package(package, root=tmp_path)


def test_symlink_and_unconsumed_artifacts_fail_closed(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    wheel = tmp_path / "candidate" / "deeplaw-0.12.0-py3-none-any.whl"
    wheel.unlink()
    wheel.symlink_to(tmp_path / "protocol" / "qualification-protocol-v1.json")
    with pytest.raises(ProductOutcomePackageError, match="non-symlink"):
        validate_product_outcome_package(package, root=tmp_path)

    package = _fixture(tmp_path)
    package["artifacts"][0]["consumed_by"] = []  # type: ignore[index]
    _refresh(package)
    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(package, root=tmp_path)


def test_explicit_mount_mapping_cannot_silently_fall_back_to_default_root(
    tmp_path: Path,
) -> None:
    package = _fixture(tmp_path)

    with pytest.raises(ProductOutcomePackageError, match="no explicitly provided root"):
        validate_product_outcome_package(package, root=tmp_path, roots={})


def test_package_digest_and_nonfinite_values_cannot_be_forged(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    package["lifecycle_status"] = "failed"
    with pytest.raises(ProductOutcomePackageError, match="package_sha256"):
        validate_product_outcome_package(package, root=tmp_path)

    package = _fixture(tmp_path)
    package["nonfinite_probe"] = math.nan
    with pytest.raises(ProductOutcomePackageError, match="non-finite"):
        validate_product_outcome_package(package, root=tmp_path)


def test_three_outcomes_cannot_self_report_passed_or_carry_pass_result(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    package["outcomes"][0]["status"] = "passed"  # type: ignore[index]
    _refresh(package)
    with pytest.raises(ProductOutcomePackageError, match=r"self-report|incompatible|passed"):
        validate_product_outcome_package(package, root=tmp_path)

    package = _fixture(tmp_path)
    _rewrite_gate(
        tmp_path,
        package,
        "continuity",
        lambda document: document.update(status="passed"),
    )
    with pytest.raises(ProductOutcomePackageError, match="cannot accept passed evidence"):
        validate_product_outcome_package(package, root=tmp_path)

    package = _fixture(tmp_path)
    output = tmp_path / "outputs" / "continuity.json"
    document = json.loads(output.read_text(encoding="utf-8"))
    document["status"] = "passed"
    output.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(
        ProductOutcomePackageError,
        match=r"byte_size|record_sha256|self-reports",
    ):
        validate_product_outcome_package(package, root=tmp_path)


def test_product_gate_id_must_match_the_exact_frozen_identity(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    _rewrite_gate(
        tmp_path,
        package,
        "continuity",
        lambda document: document.update(gate_id="attacker.continuity.shadow"),
    )

    with pytest.raises(ProductOutcomePackageError, match="not dedicated"):
        validate_product_outcome_package(package, root=tmp_path)


def test_gate_protocol_and_corpus_bytes_must_match_package_bindings(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    _rewrite_gate(
        tmp_path,
        package,
        "continuity",
        lambda document: document["protocol_binding"].update(protocol_id="wrong-protocol"),
    )
    with pytest.raises(ProductOutcomePackageError, match="protocol binding differs"):
        validate_product_outcome_package(package, root=tmp_path)

    package = _fixture(tmp_path)
    _rewrite_gate(
        tmp_path,
        package,
        "continuity",
        lambda document: document["corpus"].update(sha256="f" * 64),
    )
    with pytest.raises(ProductOutcomePackageError, match="corpus bytes differ"):
        validate_product_outcome_package(package, root=tmp_path)


def test_qualification_diagnostic_use_requires_downgraded_development(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    package["data_layers"]["qualification_holdout"]["diagnostic_or_tuning_used"] = True  # type: ignore[index]
    _refresh(package)
    with pytest.raises(ProductOutcomePackageError, match="downgraded_development"):
        validate_product_outcome_package(package, root=tmp_path)


def test_final_blind_failure_requires_new_replacement_corpus_and_gold(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    event = {
        "event_id": "final-blind-failed",
        "event_type": "final_blind_failed",
        "actor_role": "evaluator",
        "occurred_at": "synthetic",
        "outcome_id": "legal",
        "artifact_refs": ["candidate-wheel"],
        "reason_code": "threshold_failure",
        "record_sha256": "0" * 64,
    }
    from benchmarks.v013.product_outcome_package import event_record_sha256

    event["record_sha256"] = event_record_sha256(event)
    package["lifecycle_events"].append(event)  # type: ignore[union-attr]
    _refresh(package)
    with pytest.raises(
        ProductOutcomePackageError,
        match=r"failed corpus|replacement event",
    ):
        validate_product_outcome_package(package, root=tmp_path)


def test_refs_are_closed_and_owner_evaluator_attestations_are_required(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    package["outcomes"][1]["gate_result_artifact_id"] = "missing-gate"  # type: ignore[index]
    _refresh(package)
    with pytest.raises(
        ProductOutcomePackageError,
        match=r"undeclared artifact|schema violation",
    ):
        validate_product_outcome_package(package, root=tmp_path)

    package = _fixture(tmp_path)
    package["attestations"] = package["attestations"][:1]  # type: ignore[index]
    _refresh(package)
    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(package, root=tmp_path)
