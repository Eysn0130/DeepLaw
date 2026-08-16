from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

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


def _add_mount(
    package: dict[str, object],
    *,
    mount_id: str,
    purpose: str,
    visibility: str,
) -> None:
    package["mounts"].append(  # type: ignore[union-attr]
        {
            "mount_id": mount_id,
            "purpose": purpose,
            "visibility": visibility,
            "read_only": True,
        }
    )


def _relocate_artifact(
    package: dict[str, object],
    root: Path,
    *,
    artifact_id: str,
    mount_id: str,
) -> Path:
    descriptor: dict[str, Any] = next(  # type: ignore[assignment]
        item for item in package["artifacts"] if item["artifact_id"] == artifact_id  # type: ignore[index]
    )
    source = root / str(descriptor["relative_path"])
    mount_root = root / mount_id
    mount_root.mkdir(parents=True, exist_ok=True)
    target = mount_root / source.name
    target.write_bytes(source.read_bytes())
    descriptor["root"] = mount_id
    descriptor["relative_path"] = target.name
    return mount_root


def _owner_bound_fixture(root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    package = _fixture(root)
    package["evidence_kind"] = "owner_bound_external"
    purpose_by_kind = {
        "candidate_wheel": ("candidate", "compiler_evaluator"),
        "protocol_manifest": ("protocol", "owner_evaluator"),
        "threshold_manifest": ("thresholds", "owner_evaluator"),
        "classification_manifest": ("classification", "owner_evaluator"),
        "corpus_manifest": ("development_corpus", "compiler_only"),
        "gold_manifest": ("gold", "evaluator_only"),
        "compiler_isolation_receipt": ("compiler_receipt", "owner_evaluator"),
        "evaluator_isolation_receipt": ("evaluator_receipt", "owner_evaluator"),
        "owner_attestation": ("attestation", "owner_evaluator"),
        "evaluator_attestation": ("attestation", "owner_evaluator"),
        "raw_outcome_output": ("outcome_output", "owner_evaluator"),
        "provenance_gate_result": ("outcome_output", "owner_evaluator"),
        "scorer_source": ("scorer", "evaluator_only"),
        "scorer_executable": ("scorer", "evaluator_only"),
        "validator_source": ("validator", "owner_evaluator"),
        "validator_executable": ("validator", "owner_evaluator"),
    }
    package["mounts"] = []
    declared: set[str] = set()
    for descriptor in package["artifacts"]:  # type: ignore[union-attr]
        purpose, visibility = purpose_by_kind[descriptor["artifact_kind"]]
        mount_id = f"external-{purpose}"
        if mount_id not in declared:
            _add_mount(
                package,
                mount_id=mount_id,
                purpose=purpose,
                visibility=visibility,
            )
            declared.add(mount_id)
        relative_path = Path(descriptor["relative_path"])
        source = root / relative_path
        mount_root = (
            root / "external-evaluator-workspace"
            if purpose in {"outcome_output", "validator"}
            else root / mount_id
        )
        target = mount_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        descriptor["root"] = mount_id
    _refresh(package)
    return package


def _owner_bound_roots(root: Path, package: dict[str, object]) -> dict[str, Path]:
    return {
        mount["mount_id"]: (
            root / "external-evaluator-workspace"
            if mount["purpose"] in {"outcome_output", "validator"}
            else root / mount["mount_id"]
        )
        for mount in package["mounts"]  # type: ignore[union-attr]
    }


def _stage_artifact(
    package: dict[str, object],
    source_root: Path,
    target_root: Path,
    *,
    artifact_id: str,
) -> None:
    descriptor: dict[str, Any] = next(  # type: ignore[assignment]
        item for item in package["artifacts"] if item["artifact_id"] == artifact_id  # type: ignore[index]
    )
    relative_path = Path(str(descriptor["relative_path"]))
    source = source_root / relative_path
    target = target_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


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


def test_mount_ids_are_unique_even_when_mount_descriptors_differ(tmp_path: Path) -> None:
    package = _fixture(tmp_path)
    duplicate = dict(package["mounts"][0])  # type: ignore[index]
    duplicate.update(purpose="candidate", visibility="compiler_only")
    package["mounts"].append(duplicate)  # type: ignore[union-attr]
    _refresh(package)

    with pytest.raises(ProductOutcomePackageError, match="mount_id"):
        validate_product_outcome_package(package, root=tmp_path)


def test_owner_bound_external_requires_complete_explicit_mount_roots(tmp_path: Path) -> None:
    root_only = tmp_path / "root-only"
    package = _owner_bound_fixture(root_only)
    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(package, root=root_only)

    incomplete = tmp_path / "incomplete"
    package = _owner_bound_fixture(incomplete)
    incomplete_roots = _owner_bound_roots(incomplete, package)
    incomplete_roots.pop(next(iter(incomplete_roots)))
    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(
            package,
            root=incomplete,
            roots=incomplete_roots,
        )

    complete = tmp_path / "complete"
    package = _owner_bound_fixture(complete)
    assert (
        validate_product_outcome_package(
            package,
            root=complete,
            roots=_owner_bound_roots(complete, package),
        )
        == package
    )


def test_compiler_and_evaluator_candidate_and_evaluator_gold_cannot_share_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "candidate-gold-same-root"
    package = _owner_bound_fixture(root)
    shared_root = root / "shared"
    shared_root.mkdir()
    _stage_artifact(package, root, shared_root, artifact_id="candidate-wheel")
    _stage_artifact(package, root, shared_root, artifact_id="development-gold")
    roots = _owner_bound_roots(root, package)
    roots["external-candidate"] = shared_root
    roots["external-gold"] = shared_root

    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(
            package,
            root=root,
            roots=roots,
        )


@pytest.mark.parametrize("topology", ["parent-child", "child-parent"])
def test_compiler_evaluator_and_evaluator_only_mounts_cannot_use_nested_roots(
    tmp_path: Path,
    topology: str,
) -> None:
    root = tmp_path / topology
    package = _owner_bound_fixture(root)
    if topology == "parent-child":
        candidate_root = root / "compiler-root"
        gold_root = candidate_root / "evaluator-child"
    else:
        gold_root = root / "evaluator-root"
        candidate_root = gold_root / "compiler-child"
    _stage_artifact(package, root, candidate_root, artifact_id="candidate-wheel")
    _stage_artifact(package, root, gold_root, artifact_id="development-gold")
    roots = _owner_bound_roots(root, package)
    roots["external-candidate"] = candidate_root
    roots["external-gold"] = gold_root

    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(
            package,
            root=root,
            roots=roots,
        )


def test_compiler_and_evaluator_and_evaluator_only_mounts_cannot_share_symlink_resolved_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "symlink-resolved-root"
    package = _owner_bound_fixture(root)
    resolved_root = root / "resolved"
    resolved_root.mkdir()
    alias = root / "alias"
    alias.symlink_to(root, target_is_directory=True)
    _stage_artifact(package, root, resolved_root, artifact_id="candidate-wheel")
    _stage_artifact(package, root, resolved_root, artifact_id="development-gold")
    roots = _owner_bound_roots(root, package)
    roots["external-candidate"] = resolved_root
    roots["external-gold"] = alias / "resolved"

    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(
            package,
            root=root,
            roots=roots,
        )


def test_compiler_and_evaluator_and_evaluator_only_mounts_allow_disjoint_roots(
    tmp_path: Path,
) -> None:
    root = tmp_path / "disjoint-roots"
    package = _owner_bound_fixture(root)
    roots = _owner_bound_roots(root, package)
    evaluator_shared_root = root / "evaluator-shared"
    evaluator_shared_root.mkdir()
    _stage_artifact(package, root, evaluator_shared_root, artifact_id="development-gold")
    for product in ("continuity", "wiki", "legal"):
        _stage_artifact(
            package,
            root,
            evaluator_shared_root,
            artifact_id=f"scorer-source-{product}",
        )
        _stage_artifact(
            package,
            root,
            evaluator_shared_root,
            artifact_id=f"scorer-executable-{product}",
        )
    roots["external-gold"] = evaluator_shared_root
    roots["external-scorer"] = evaluator_shared_root

    assert roots["external-candidate"] != roots["external-gold"]
    assert not roots["external-candidate"].is_relative_to(roots["external-gold"])
    assert not roots["external-gold"].is_relative_to(roots["external-candidate"])
    assert (
        validate_product_outcome_package(
            package,
            root=root,
            roots=roots,
        )
        == package
    )


def test_compiler_and_evaluator_mounts_cannot_share_a_resolved_root(tmp_path: Path) -> None:
    root = tmp_path / "role-root-collision"
    package = _owner_bound_fixture(root)
    _add_mount(
        package,
        mount_id="compiler-mount",
        purpose="candidate",
        visibility="compiler_only",
    )
    _add_mount(
        package,
        mount_id="evaluator-mount",
        purpose="gold",
        visibility="evaluator_only",
    )
    shared_root = root / "shared"
    shared_root.mkdir()
    _refresh(package)
    roots = _owner_bound_roots(root, package)
    roots["compiler-mount"] = shared_root
    roots["evaluator-mount"] = shared_root / "."

    with pytest.raises(
        ProductOutcomePackageError,
        match="compiler-visible and protected mounts must use disjoint resolved roots",
    ):
        validate_product_outcome_package(
            package,
            root=root,
            roots=roots,
        )


@pytest.mark.parametrize(
    ("purpose", "visibility"),
    [
        ("gold", "compiler_only"),
        ("scorer", "compiler_evaluator"),
        ("development_corpus", "evaluator_only"),
        ("outcome_output", "compiler_only"),
        ("package_workspace", "compiler_evaluator"),
    ],
)
def test_mount_purpose_and_visibility_are_closed(
    tmp_path: Path,
    purpose: str,
    visibility: str,
) -> None:
    root = tmp_path / f"{purpose}-{visibility}"
    root.mkdir()
    package = _fixture(root)
    package["mounts"][0]["purpose"] = purpose  # type: ignore[index]
    package["mounts"][0]["visibility"] = visibility  # type: ignore[index]
    _refresh(package)

    with pytest.raises(ProductOutcomePackageError, match=r"visibility|owner_only"):
        validate_product_outcome_package(package, root=root)


@pytest.mark.parametrize(
    ("artifact_id", "purpose"),
    [
        ("development-gold", "gold"),
        ("scorer-source-continuity", "scorer"),
        ("scorer-executable-continuity", "scorer"),
        ("qualification-protocol", "protocol"),
        ("threshold-manifest", "thresholds"),
        ("classification-manifest", "classification"),
    ],
)
def test_compiler_visible_mount_cannot_bind_protected_artifacts(
    tmp_path: Path,
    artifact_id: str,
    purpose: str,
) -> None:
    root = tmp_path / artifact_id
    root.mkdir()
    package = _fixture(root)
    _add_mount(
        package,
        mount_id="compiler-visible",
        purpose=purpose,
        visibility="compiler_only",
    )
    compiler_root = _relocate_artifact(
        package,
        root,
        artifact_id=artifact_id,
        mount_id="compiler-visible",
    )
    _refresh(package)

    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(
            package,
            root=root,
            roots={"workspace": root, "compiler-visible": compiler_root},
        )


def test_corpus_cannot_bind_a_gold_mount(tmp_path: Path) -> None:
    root = tmp_path / "corpus-gold-mount"
    root.mkdir()
    package = _fixture(root)
    _add_mount(
        package,
        mount_id="gold-mount",
        purpose="gold",
        visibility="evaluator_only",
    )
    gold_root = _relocate_artifact(
        package,
        root,
        artifact_id="development-corpus",
        mount_id="gold-mount",
    )
    _refresh(package)

    with pytest.raises(ProductOutcomePackageError):
        validate_product_outcome_package(
            package,
            root=root,
            roots={"workspace": root, "gold-mount": gold_root},
        )


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
