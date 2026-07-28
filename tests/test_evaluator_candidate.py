from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

import benchmarks.release.evaluator_candidate as evaluator_candidate
from benchmarks.baselines.registry import freeze_candidate_registry, load_registry
from benchmarks.release.evaluator_candidate import (
    ATTESTATION_SCHEMA,
    BASELINE_GATE_SCHEMA,
    CORPUS_SCHEMA,
    KIT_SCHEMA,
    MODEL_SCHEMA,
    ArtifactStore,
    CandidateError,
    EvidenceCollection,
    _source_archive,
    build_corpus_commitment,
    build_model_manifest,
    build_profile_commitment,
    freeze_evaluator_kit,
    validate_corpus_commitment,
    validate_git_candidate,
    validate_internal_gate,
    validate_model_manifest,
    validate_oci_archive,
    verify_attestation,
    verify_evaluator_kit,
)
from deeplaw.util import canonical_json, sha256_bytes, sha256_file

REPOSITORY = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict[str, object]:
    value = json.loads((REPOSITORY / "contracts" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return value


def _record(body: dict[str, object], field: str) -> dict[str, object]:
    return {
        **body,
        field: sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _clean_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "src" / "deeplaw").mkdir(parents=True)
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "deeplaw"\nversion = "0.7.0"\n',
        encoding="utf-8",
    )
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    implementations = {
        "context_compiler.py": "TOKENIZER_VERSION = '2'\n",
        "knowledge_discovery.py": "DISCOVERY = False\n",
        "knowledge_store.py": "INDEX = 'sqlite-fts5'\n",
        "local_reranker.py": "RERANKER = False\n",
        "retrieval_fabric.py": (
            "TOKENIZER_PROFILE = 'deeplaw-mixed-cjk-code/2'\n"
            "FUSION_PROFILE = 'rrf-duty-diversity/1'\n"
        ),
        "retrieval_profiles.py": "PROFILE_VERSION = 1\n",
    }
    for name, source in implementations.items():
        (repository / "src" / "deeplaw" / name).write_text(source, encoding="utf-8")
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Evaluator Test")
    _git(repository, "config", "user.email", "evaluator@example.test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "frozen candidate")
    return repository, _git(repository, "rev-parse", "HEAD")


def _oci_archive(
    path: Path,
    *,
    commit: str,
    version: str,
    wheel_sha256: str = "1" * 64,
    sdist_sha256: str = "2" * 64,
    lock_sha256: str = "3" * 64,
) -> None:
    def encoded(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    labels = {
        "org.opencontainers.image.revision": commit,
        "org.opencontainers.image.version": version,
        "dev.deeplaw.wheel.sha256": wheel_sha256,
        "dev.deeplaw.sdist.sha256": sdist_sha256,
        "dev.deeplaw.lock.sha256": lock_sha256,
    }
    config = encoded({"architecture": "amd64", "os": "linux", "config": {"Labels": labels}})
    layer = b"layer"
    config_digest = hashlib.sha256(config).hexdigest()
    layer_digest = hashlib.sha256(layer).hexdigest()
    manifest = encoded(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": f"sha256:{config_digest}",
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": f"sha256:{layer_digest}",
                    "size": len(layer),
                }
            ],
        }
    )
    manifest_digest = hashlib.sha256(manifest).hexdigest()
    index = encoded(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": f"sha256:{manifest_digest}",
                    "size": len(manifest),
                    "platform": {"os": "linux", "architecture": "amd64"},
                    "annotations": labels,
                }
            ],
        }
    )
    members = {
        "oci-layout": encoded({"imageLayoutVersion": "1.0.0"}),
        "index.json": index,
        f"blobs/sha256/{config_digest}": config,
        f"blobs/sha256/{layer_digest}": layer,
        f"blobs/sha256/{manifest_digest}": manifest,
    }
    with tarfile.open(path, "w") as archive:
        for directory in ("blobs", "blobs/sha256"):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def test_model_corpus_and_internal_gate_contracts_fail_closed(tmp_path: Path) -> None:
    expected_model = {"model_id": "org/model", "revision": "a" * 40}
    model_body = {
        "schema_version": MODEL_SCHEMA,
        "alias": "embedding",
        "model_id": "org/model",
        "revision": "a" * 40,
        "source": "https://example.test/model",
        "license": "Apache-2.0",
        "files": [{"path": "model.onnx", "sha256": "b" * 64, "byte_size": 42}],
        "total_byte_size": 42,
    }
    model = _record(model_body, "manifest_sha256")
    validate_model_manifest(model, expected_alias="embedding", expected_model=expected_model)
    _schema("evaluator-model-manifest.v1.schema.json")
    Draft202012Validator(_schema("evaluator-model-manifest.v1.schema.json")).validate(model)
    tampered_model = {**model, "total_byte_size": 41}
    with pytest.raises(CandidateError, match="byte total"):
        validate_model_manifest(
            tampered_model,
            expected_alias="embedding",
            expected_model=expected_model,
        )

    model_root = tmp_path / "model"
    model_root.mkdir()
    (model_root / "config.json").write_text("{}\n", encoding="utf-8")
    (model_root / "model.bin").write_bytes(b"weights")
    generated_model = build_model_manifest(
        registry_path=REPOSITORY / "benchmarks" / "baselines" / "registry-v0.7.json",
        alias="deeplaw-discovery-zh",
        model_root=model_root,
        source="https://huggingface.co/jinaai/jina-embeddings-v2-base-zh",
        license_name="Apache-2.0",
    )
    assert generated_model["total_byte_size"] == 10
    assert [item["path"] for item in generated_model["files"]] == [
        "config.json",
        "model.bin",
    ]

    corpus_body = {
        "schema_version": CORPUS_SCHEMA,
        "commitment_id": "frozen-public-corpus",
        "evaluator_organization": "Independent Evaluator",
        "commitment_stage": "before_candidate_delivery",
        "corpus_sha256": "c" * 64,
        "queries_sha256": "d" * 64,
        "query_case_ids_sha256": "e" * 64,
        "corpus_record_count": 10,
        "query_case_count": 4,
        "labels_access": "public_frozen",
        "committed_at": "2026-07-28T00:00:00Z",
        "claim_eligible": False,
    }
    corpus = _record(corpus_body, "record_sha256")
    validate_corpus_commitment(
        corpus,
        corpus_sha256="c" * 64,
        queries_sha256="d" * 64,
        query_case_ids_sha256="e" * 64,
        corpus_record_count=10,
        query_case_count=4,
    )
    Draft202012Validator(_schema("benchmark-corpus-commitment.v1.schema.json")).validate(
        corpus
    )
    corpus_path = tmp_path / "corpus.jsonl"
    queries_path = tmp_path / "queries.jsonl"
    corpus_path.write_text(
        '{"id":"doc-1","title":"Title","text":"Evidence"}\n',
        encoding="utf-8",
    )
    queries_path.write_text(
        '{"case_id":"case-1","query":"Find evidence"}\n',
        encoding="utf-8",
    )
    generated_corpus = build_corpus_commitment(
        corpus_path=corpus_path,
        queries_path=queries_path,
        commitment_id="pre-delivery-1",
        evaluator_organization="Independent Evaluator",
        labels_access="external_evaluator_only",
        committed_at="2026-07-28T00:00:00Z",
    )
    assert generated_corpus["corpus_record_count"] == 1
    assert generated_corpus["query_case_count"] == 1
    assert generated_corpus["claim_eligible"] is False
    corpus_path.write_text(
        (
            '{"id":"doc-1","title":"Title","text":"Evidence"}\n'
            '{"id":"doc-1","title":"Other","text":"Duplicate"}\n'
        ),
        encoding="utf-8",
    )
    with pytest.raises(CandidateError, match="duplicate document IDs"):
        build_corpus_commitment(
            corpus_path=corpus_path,
            queries_path=queries_path,
            commitment_id="invalid",
            evaluator_organization="Independent Evaluator",
            labels_access="public_frozen",
            committed_at="2026-07-28T00:00:00Z",
        )

    gate_body = {
        "schema_version": BASELINE_GATE_SCHEMA,
        "candidate_commit": "f" * 40,
        "registry_sha256": "1" * 64,
        "collection_report_sha256": "2" * 64,
        "statistical_protocol_sha256": "3" * 64,
        "case_results_manifest_sha256": "4" * 64,
        "comparisons_manifest_sha256": "5" * 64,
        "paired_bootstrap_iterations": 10_000,
        "confidence_level": 0.95,
        "multiple_comparison_correction": "holm-bonferroni",
        "threshold_adjustments_used": 1,
        "thresholds_frozen_at": "2026-07-28T00:00:00Z",
        "gate_completed_at": "2026-07-28T01:00:00Z",
        "thresholds_frozen_before_held_out": True,
        "case_level_results_retained": True,
        "failures_retained": True,
        "professional_baseline_gates_passed": True,
        "aggregate_gate_passed": True,
        "security_regression_count": 0,
        "gate_status": "passed",
        "claim_eligible": False,
    }
    gate = _record(gate_body, "record_sha256")
    validate_internal_gate(
        gate,
        candidate_commit="f" * 40,
        registry_digest="1" * 64,
        collection_report_digest="2" * 64,
    )
    Draft202012Validator(_schema("internal-baseline-gate.v1.schema.json")).validate(gate)
    with pytest.raises(CandidateError, match="has not passed"):
        validate_internal_gate(
            {**gate, "aggregate_gate_passed": False},
            candidate_commit="f" * 40,
            registry_digest="1" * 64,
            collection_report_digest="2" * 64,
        )


def test_clean_git_candidate_binds_source_tree_and_profiles(tmp_path: Path) -> None:
    repository, commit = _clean_repository(tmp_path)
    candidate = validate_git_candidate(repository, commit)
    frozen_registry = freeze_candidate_registry(
        load_registry(),
        candidate_commit=commit,
        reviewed_at="2026-07-28",
    )
    profile = build_profile_commitment(
        repository,
        candidate=candidate,
        registry=frozen_registry,
    )
    Draft202012Validator(
        _schema("evaluator-retrieval-profile-commitment.v1.schema.json")
    ).validate(profile)
    assert profile["tokenizer_profile"] == "deeplaw-mixed-cjk-code/2"
    assert profile["tokenizer_version"] == "2"
    archive = tmp_path / "source.tar"
    inventory = _source_archive(repository, commit, archive)
    assert inventory["file_count"] == 8

    (repository / "untracked.txt").write_text("drift", encoding="utf-8")
    with pytest.raises(CandidateError, match="completely clean"):
        validate_git_candidate(repository, commit)


def test_oci_archive_requires_candidate_labels_and_exact_blob_hashes(tmp_path: Path) -> None:
    archive = tmp_path / "candidate.oci.tar"
    _oci_archive(archive, commit="a" * 40, version="0.7.0")
    report = validate_oci_archive(
        archive,
        candidate_commit="a" * 40,
        package_version="0.7.0",
        wheel_sha256="1" * 64,
        sdist_sha256="2" * 64,
        lock_sha256="3" * 64,
    )
    assert report == {
        "platform_count": 1,
        "platforms": [{"os": "linux", "architecture": "amd64"}],
        "blob_count": 3,
        "artifact_bindings": {
            "wheel_sha256": "1" * 64,
            "sdist_sha256": "2" * 64,
            "lock_sha256": "3" * 64,
        },
    }
    with pytest.raises(CandidateError, match="candidate identity"):
        validate_oci_archive(
            archive,
            candidate_commit="b" * 40,
            package_version="0.7.0",
            wheel_sha256="1" * 64,
            sdist_sha256="2" * 64,
            lock_sha256="3" * 64,
        )


def test_freezer_assembles_only_complete_bound_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _ = _clean_repository(tmp_path)
    extra_files = {
        ".github/workflows/release.yml": "name: release\n",
        "benchmarks/external/benchlib.py": "# signature dependency\n",
        "benchmarks/external/build_suite_manifest.py": "# suite builder\n",
        "benchmarks/external/claim_gate.py": "# signature verifier\n",
        "benchmarks/release/evaluator_candidate.py": "# kit verifier\n",
        "contracts/dummy.json": "{}\n",
    }
    for relative, payload in extra_files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "add evaluator tooling")
    commit = _git(repository, "rev-parse", "HEAD")
    registry = freeze_candidate_registry(
        load_registry(),
        candidate_commit=commit,
        reviewed_at="2026-07-28",
    )
    registry_path = tmp_path / "frozen-registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    model_paths: dict[str, Path] = {}
    model_digests: dict[str, str] = {}
    for alias, expected in registry["shared_models"].items():
        body = {
            "schema_version": MODEL_SCHEMA,
            "alias": alias,
            "model_id": expected["model_id"],
            "revision": expected["revision"],
            "source": f"https://example.test/{alias}",
            "license": "Apache-2.0",
            "files": [{"path": "model.bin", "sha256": "a" * 64, "byte_size": 1}],
            "total_byte_size": 1,
        }
        manifest = _record(body, "manifest_sha256")
        path = tmp_path / f"{alias}.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        model_paths[alias] = path
        model_digests[alias] = manifest["manifest_sha256"]

    corpus_path = tmp_path / "corpus.jsonl"
    queries_path = tmp_path / "queries.jsonl"
    corpus_path.write_text(
        '{"id":"doc-1","title":"Title","text":"Evidence"}\n',
        encoding="utf-8",
    )
    queries_path.write_text(
        '{"case_id":"case-1","query":"Find evidence"}\n',
        encoding="utf-8",
    )
    corpus_commitment = build_corpus_commitment(
        corpus_path=corpus_path,
        queries_path=queries_path,
        commitment_id="pre-delivery",
        evaluator_organization="Independent Evaluator",
        labels_access="external_evaluator_only",
        committed_at="2026-07-28T00:00:00Z",
    )
    corpus_commitment_path = tmp_path / "corpus-commitment.json"
    corpus_commitment_path.write_text(json.dumps(corpus_commitment), encoding="utf-8")

    evidence_payload = tmp_path / "evidence.bin"
    evidence_payload.write_bytes(b"retained evidence")
    evidence_files: list[tuple[str, Path, str]] = []
    for index in range(17):
        evidence_files.extend(
            [
                (f"system-{index}/raw-output", evidence_payload, "application/x-ndjson"),
                (f"system-{index}/resource-record", evidence_payload, "application/json"),
            ]
        )
    report = {
        "collection_id": "complete-collection",
        "expected_system_count": 17,
        "successful_run_count": 17,
        "report_sha256": "b" * 64,
        "common_bindings": {
            "corpus_sha256": corpus_commitment["corpus_sha256"],
            "queries_sha256": corpus_commitment["queries_sha256"],
            "query_case_ids_sha256": corpus_commitment["query_case_ids_sha256"],
        },
    }
    evidence = EvidenceCollection(
        registry=registry,
        registry_digest=sha256_bytes(canonical_json(registry).encode()),
        report=report,
        files=tuple(evidence_files),
        model_manifest_digests=model_digests,
        corpus_path=corpus_path,
        queries_path=queries_path,
        query_case_count=1,
    )
    monkeypatch.setattr(
        evaluator_candidate,
        "collect_baseline_evidence",
        lambda **_kwargs: evidence,
    )
    monkeypatch.setattr(
        evaluator_candidate,
        "verify_evaluator_kit",
        lambda _root: {"integrity_verified": True},
    )

    protocol = tmp_path / "statistical-protocol.json"
    results = tmp_path / "case-results.json"
    comparisons = tmp_path / "comparisons.json"
    for path, value in ((protocol, {}), (results, []), (comparisons, [])):
        path.write_text(json.dumps(value), encoding="utf-8")
    gate_body = {
        "schema_version": BASELINE_GATE_SCHEMA,
        "candidate_commit": commit,
        "registry_sha256": evidence.registry_digest,
        "collection_report_sha256": report["report_sha256"],
        "statistical_protocol_sha256": sha256_file(protocol),
        "case_results_manifest_sha256": sha256_file(results),
        "comparisons_manifest_sha256": sha256_file(comparisons),
        "paired_bootstrap_iterations": 10_000,
        "confidence_level": 0.95,
        "multiple_comparison_correction": "holm-bonferroni",
        "threshold_adjustments_used": 0,
        "thresholds_frozen_at": "2026-07-28T00:30:00Z",
        "gate_completed_at": "2026-07-28T01:30:00Z",
        "thresholds_frozen_before_held_out": True,
        "case_level_results_retained": True,
        "failures_retained": True,
        "professional_baseline_gates_passed": True,
        "aggregate_gate_passed": True,
        "security_regression_count": 0,
        "gate_status": "passed",
        "claim_eligible": False,
    }
    gate = _record(gate_body, "record_sha256")
    gate_path = tmp_path / "internal-gate.json"
    gate_path.write_text(json.dumps(gate), encoding="utf-8")

    wheel = tmp_path / "deeplaw-0.7.0-py3-none-any.whl"
    sdist = tmp_path / "deeplaw-0.7.0.tar.gz"
    wheel.write_bytes(b"wheel")
    sdist.write_bytes(b"sdist")
    build_report = {
        "schema_version": "deeplaw.reproducible-build-report/v2",
        "binding": {},
        "environment": {},
        "repository_commit": commit,
        "working_tree_dirty": False,
        "source_date_epoch": 946684800,
        "build_constraints_sha256": "c" * 64,
        "lock_sha256": sha256_file(repository / "uv.lock"),
        "build_dependencies": {
            "hatchling": "1.31.0",
            "packaging": "26.2",
            "pathspec": "1.1.1",
            "pluggy": "1.6.0",
            "trove-classifiers": "2026.6.1.19",
        },
        "reproducible": True,
        "package_inventory_verified": True,
        "artifacts": [
            {
                "name": path.name,
                "sha256": sha256_file(path),
                "byte_size": path.stat().st_size,
                "path_count": 1,
                "inventory_sha256": "d" * 64,
            }
            for path in (wheel, sdist)
        ],
        "artifact_release_eligible": True,
        "artifact_release_blockers": [],
    }
    build_report = _record(build_report, "record_sha256")
    build_report_path = tmp_path / "reproducible-build.json"
    build_report_path.write_text(json.dumps(build_report), encoding="utf-8")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "version": 1,
                "metadata": {
                    "component": {"type": "library", "name": "deeplaw", "version": "0.7.0"}
                },
                "components": [{"type": "library", "name": "deeplaw", "version": "0.7.0"}],
            }
        ),
        encoding="utf-8",
    )
    licenses = tmp_path / "licenses.json"
    licenses.write_text(
        json.dumps(
            {
                "schema_version": "deeplaw.installed-license-inventory/v1",
                "policy_schema_version": "deeplaw.release-license-policy/v1",
                "package_count": 1,
                "status": "passed",
                "blocked": [],
                "review_required": [],
                "packages": [
                    {
                        "name": "deeplaw",
                        "normalized_name": "deeplaw",
                        "version": "0.7.0",
                        "license_expression": "Apache-2.0",
                        "declared_license": "Apache-2.0",
                        "license_classifiers": [],
                        "status": "approved",
                        "reason": "approved policy",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    container = tmp_path / "candidate.oci.tar"
    _oci_archive(
        container,
        commit=commit,
        version="0.7.0",
        wheel_sha256=sha256_file(wheel),
        sdist_sha256=sha256_file(sdist),
        lock_sha256=sha256_file(repository / "uv.lock"),
    )
    collection_path = tmp_path / "collection.json"
    collection_report_path = tmp_path / "collection-report.json"
    collection_path.write_text("{}", encoding="utf-8")
    collection_report_path.write_text("{}", encoding="utf-8")

    destination = tmp_path / "frozen-kit"
    manifest = freeze_evaluator_kit(
        repository=repository,
        candidate_commit=commit,
        destination=destination,
        registry_path=registry_path,
        collection_path=collection_path,
        collection_report_path=collection_report_path,
        corpus_commitment_path=corpus_commitment_path,
        internal_gate_path=gate_path,
        statistical_protocol_path=protocol,
        case_results_manifest_path=results,
        comparisons_manifest_path=comparisons,
        model_manifest_paths=model_paths,
        wheel_path=wheel,
        sdist_path=sdist,
        container_path=container,
        sbom_path=sbom,
        license_inventory_path=licenses,
        reproducible_build_path=build_report_path,
        created_at="2026-07-28T02:00:00Z",
    )
    Draft202012Validator(_schema("external-evaluator-kit.v1.schema.json")).validate(
        manifest
    )
    assert destination.is_dir()
    assert manifest["baseline_evidence"]["raw_output_count"] == 17
    assert manifest["baseline_evidence"]["resource_record_count"] == 17
    assert manifest["external_verification"]["claim_eligible"] is False


def _minimal_kit(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "kit"
    root.mkdir()
    source = tmp_path / "artifact.bin"
    source.write_bytes(b"immutable")
    store = ArtifactStore(root)

    def artifact(name: str) -> dict[str, object]:
        return store.add_file(source, logical_name=name, media_type="application/octet-stream")

    file_binding = {"path": "tool.py", "sha256": "a" * 64, "byte_size": 1}
    body: dict[str, object] = {
        "schema_version": KIT_SCHEMA,
        "kit_id": "deeplaw-0.7.0-aaaaaaaaaaaa",
        "created_at": "2026-07-28T00:00:00Z",
        "candidate": {
            "candidate_line": "0.7.0-frozen-evaluation-candidate",
            "package_version": "0.7.0",
            "commit": "a" * 40,
            "git_tree": "b" * 40,
            "source_archive": artifact("source/source.tar"),
            "file_count": 1,
            "inventory_sha256": "c" * 64,
        },
        "contracts": {"count": 1, "inventory_sha256": "d" * 64, "files": [file_binding]},
        "release_artifacts": {
            name: artifact(f"release/{name}")
            for name in (
                "lock",
                "wheel",
                "sdist",
                "container",
                "sbom",
                "license_inventory",
                "reproducible_build",
            )
        },
        "container": {
            "platform_count": 1,
            "platforms": [{"os": "linux", "architecture": "amd64"}],
            "blob_count": 3,
            "artifact_bindings": {
                "wheel_sha256": "1" * 64,
                "sdist_sha256": "2" * 64,
                "lock_sha256": "3" * 64,
            },
        },
        "models": [
            {
                "alias": "generation-reader",
                "model_id": "org/model",
                "revision": "e" * 40,
                "manifest_sha256": "f" * 64,
                "artifact": artifact("models/reader"),
            }
        ],
        "retrieval_profiles": {
            "record_sha256": "1" * 64,
            "artifact": artifact("profiles/retrieval"),
        },
        "benchmark_commitments": {
            "registry_sha256": "2" * 64,
            "corpus_record_sha256": "3" * 64,
            "internal_gate_record_sha256": "4" * 64,
            "artifacts": {
                name: artifact(f"commitments/{name}")
                for name in (
                    "corpus",
                    "baseline_registry",
                    "internal_gate",
                    "statistical_protocol",
                    "case_results_manifest",
                    "comparisons_manifest",
                )
            },
        },
        "baseline_evidence": {
            "collection_id": "collection-1",
            "system_count": 17,
            "successful_system_count": 17,
            "collection_report_sha256": "5" * 64,
            "raw_output_count": 17,
            "resource_record_count": 17,
            "artifacts": [artifact("evidence/raw")],
        },
        "signature_tools": [file_binding],
        "external_verification": {
            "claim_eligible": False,
            "secret_held_out_runs_complete": False,
            "independent_org_attestations_complete": False,
            "required_secret_held_out_count": 2,
            "required_independent_org_count": 2,
            "attestation_schema": ATTESTATION_SCHEMA,
            "blockers": [
                "two_secret_held_out_runs_absent",
                "two_independent_organization_attestations_absent",
            ],
        },
        "kit_integrity_complete": True,
    }
    manifest = _record(body, "manifest_sha256")
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return root, manifest


def test_portable_kit_schema_integrity_and_trusted_signature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, manifest = _minimal_kit(tmp_path)
    monkeypatch.setattr(
        evaluator_candidate,
        "_semantic_kit_verification",
        lambda **_kwargs: None,
    )
    Draft202012Validator(_schema("external-evaluator-kit.v1.schema.json")).validate(
        manifest
    )
    verification = verify_evaluator_kit(root)
    assert verification["integrity_verified"] is True
    assert verification["claim_eligible"] is False

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    payload = (root / "manifest.json").read_bytes()
    attestation = {
        "schema_version": ATTESTATION_SCHEMA,
        "organization": "Independent Evaluator",
        "kit_manifest_file_sha256": sha256_bytes(payload),
        "kit_manifest_sha256": manifest["manifest_sha256"],
        "issued_at": "2026-07-28T01:00:00Z",
        "signature_payload": "exact-manifest-bytes",
        "public_key_base64": base64.b64encode(public_key).decode(),
        "signature_base64": base64.b64encode(private_key.sign(payload)).decode(),
    }
    Draft202012Validator(
        _schema("external-evaluator-kit-attestation.v1.schema.json")
    ).validate(attestation)
    attestation_path = tmp_path / "attestation.json"
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    result = verify_attestation(
        kit_root=root,
        attestation_path=attestation_path,
        trusted_public_key_hex=public_key.hex(),
    )
    assert result["signature_valid"] is True
    assert result["organization_identity_independently_verified"] is False
    assert result["claim_eligible"] is False

    blob = next((root / "blobs").rglob("[0-9a-f]" * 64))
    os.chmod(blob, 0o644)
    blob.write_bytes(b"tampered")
    with pytest.raises(CandidateError, match="blob differs"):
        verify_evaluator_kit(root)
