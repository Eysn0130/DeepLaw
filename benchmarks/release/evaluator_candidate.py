from __future__ import annotations

import argparse
import ast
import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from benchmarks.baselines.collection_gate import build_collection_report
from benchmarks.baselines.official_adapter import _queries_binding
from benchmarks.baselines.registry import (
    FROZEN_EVALUATION_CANDIDATE_LINE,
    load_registry,
    registry_sha256,
)
from deeplaw.util import canonical_json, sha256_bytes, sha256_file, strict_json_loads

KIT_SCHEMA = "deeplaw.external-evaluator-kit/v1"
MODEL_SCHEMA = "deeplaw.evaluator-model-manifest/v1"
CORPUS_SCHEMA = "deeplaw.benchmark-corpus-commitment/v1"
BASELINE_GATE_SCHEMA = "deeplaw.internal-baseline-gate/v1"
ATTESTATION_SCHEMA = "deeplaw.external-evaluator-kit-attestation/v1"
PROFILE_SCHEMA = "deeplaw.evaluator-retrieval-profile-commitment/v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TREE = re.compile(r"^[0-9a-f]{40,64}$")
_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,79}$")
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_MODEL_FILES = 1_000_000
_MAX_EVIDENCE_FILES = 512
_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
_OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
_OCI_LAYERS = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
    "application/vnd.oci.image.layer.nondistributable.v1.tar",
    "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
    "application/vnd.oci.image.layer.nondistributable.v1.tar+zstd",
}
_OCI_ARTIFACT_LABELS = {
    "wheel_sha256": "dev.deeplaw.wheel.sha256",
    "sdist_sha256": "dev.deeplaw.sdist.sha256",
    "lock_sha256": "dev.deeplaw.lock.sha256",
}
_PROFILE_IMPLEMENTATION_PATHS = (
    "src/deeplaw/context_compiler.py",
    "src/deeplaw/knowledge_discovery.py",
    "src/deeplaw/knowledge_store.py",
    "src/deeplaw/local_reranker.py",
    "src/deeplaw/retrieval_fabric.py",
    "src/deeplaw/retrieval_profiles.py",
)
_SIGNATURE_TOOL_PATHS = (
    ".github/workflows/release.yml",
    "benchmarks/external/benchlib.py",
    "benchmarks/external/build_suite_manifest.py",
    "benchmarks/external/claim_gate.py",
    "benchmarks/release/evaluator_candidate.py",
)


class CandidateError(ValueError):
    pass


def _closed(value: Any, *, field: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CandidateError(f"{field} does not match its closed contract")
    return value


def _bounded_string(value: Any, *, field: str, maximum: int = 1_000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise CandidateError(f"{field} must be a bounded canonical string")
    return value


def _timestamp(value: Any, *, field: str) -> datetime:
    text = _bounded_string(value, field=field, maximum=40)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as error:
        raise CandidateError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None:
        raise CandidateError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _safe_relative(value: Any, *, field: str) -> str:
    text = _bounded_string(value, field=field, maximum=4_096)
    path = PurePosixPath(text)
    if path.is_absolute() or not path.parts or ".." in path.parts or "" in path.parts:
        raise CandidateError(f"{field} must be a safe relative POSIX path")
    return path.as_posix()


def _read_json(path: Path, *, field: str, maximum: int = _MAX_JSON_BYTES) -> dict[str, Any]:
    selected = path.expanduser().absolute()
    if selected.is_symlink() or not selected.is_file():
        raise CandidateError(f"{field} must be a regular non-symlink file")
    size = selected.stat().st_size
    if not 1 <= size <= maximum:
        raise CandidateError(f"{field} violates its byte bound")
    try:
        value = strict_json_loads(selected.read_bytes())
    except (UnicodeDecodeError, ValueError) as error:
        raise CandidateError(f"{field} must contain strict JSON") from error
    if not isinstance(value, dict):
        raise CandidateError(f"{field} must contain a JSON object")
    return value


def _record_sha256(value: dict[str, Any], *, field: str, digest_field: str) -> str:
    digest = value.get(digest_field)
    body = {key: item for key, item in value.items() if key != digest_field}
    expected = sha256_bytes(canonical_json(body).encode("utf-8"))
    if digest != expected:
        raise CandidateError(f"{field} canonical digest is invalid")
    return expected


def _schema(name: str) -> dict[str, Any]:
    source_path = Path(__file__).resolve().parents[2] / "contracts" / name
    try:
        payload = (
            source_path.read_text(encoding="utf-8")
            if source_path.is_file()
            else resources.files("deeplaw").joinpath("contracts", name).read_text(encoding="utf-8")
        )
        value = strict_json_loads(payload)
    except (FileNotFoundError, ModuleNotFoundError, UnicodeDecodeError, ValueError) as error:
        raise CandidateError(f"published contract is unavailable: {name}") from error
    if not isinstance(value, dict):
        raise CandidateError(f"published contract is invalid: {name}")
    return value


def _validate_schema(value: Any, *, name: str, field: str) -> None:
    schema = _schema(name)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except (SchemaError, ValidationError) as error:
        raise CandidateError(f"{field} violates its published schema") from error


def validate_model_manifest(
    value: Any,
    *,
    expected_alias: str,
    expected_model: dict[str, Any],
) -> dict[str, Any]:
    model = _closed(
        value,
        field=f"model manifest {expected_alias}",
        keys={
            "schema_version",
            "alias",
            "model_id",
            "revision",
            "source",
            "license",
            "files",
            "total_byte_size",
            "manifest_sha256",
        },
    )
    if model.get("schema_version") != MODEL_SCHEMA or model.get("alias") != expected_alias:
        raise CandidateError(f"model manifest identity differs for {expected_alias}")
    if (
        model.get("model_id") != expected_model.get("model_id")
        or model.get("revision") != expected_model.get("revision")
    ):
        raise CandidateError(f"model manifest revision differs for {expected_alias}")
    _bounded_string(model.get("source"), field=f"{expected_alias}.source", maximum=1_000)
    _bounded_string(model.get("license"), field=f"{expected_alias}.license", maximum=200)
    files = model.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= _MAX_MODEL_FILES:
        raise CandidateError(f"model manifest files are invalid for {expected_alias}")
    paths: set[str] = set()
    total = 0
    for index, item in enumerate(files):
        artifact = _closed(
            item,
            field=f"{expected_alias}.files[{index}]",
            keys={"path", "sha256", "byte_size"},
        )
        path = _safe_relative(
            artifact.get("path"),
            field=f"{expected_alias}.files[{index}].path",
        )
        size = artifact.get("byte_size")
        if (
            path in paths
            or not _SHA256.fullmatch(str(artifact.get("sha256")))
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise CandidateError(f"model file binding is invalid for {expected_alias}")
        paths.add(path)
        total += size
        if total > 2**63 - 1:
            raise CandidateError(f"model byte inventory is oversized for {expected_alias}")
    if model.get("total_byte_size") != total:
        raise CandidateError(f"model byte total differs for {expected_alias}")
    _record_sha256(model, field=f"model manifest {expected_alias}", digest_field="manifest_sha256")
    return model


def build_model_manifest(
    *,
    registry_path: Path,
    alias: str,
    model_root: Path,
    source: str,
    license_name: str,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    expected = registry["shared_models"].get(alias)
    if not isinstance(expected, dict):
        raise CandidateError("model alias is absent from the baseline registry")
    _bounded_string(source, field="model source", maximum=1_000)
    _bounded_string(license_name, field="model license", maximum=200)
    root = model_root.expanduser().absolute()
    if root.is_symlink() or not root.is_dir():
        raise CandidateError("model root must be a regular non-symlink directory")
    files: list[dict[str, Any]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise CandidateError("model root contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise CandidateError("model root contains a non-regular file")
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total += size
        if len(files) >= _MAX_MODEL_FILES or total > 2**63 - 1:
            raise CandidateError("model root inventory exceeds its closed bounds")
        files.append({"path": relative, "sha256": sha256_file(path), "byte_size": size})
    if not files:
        raise CandidateError("model root contains no regular files")
    body = {
        "schema_version": MODEL_SCHEMA,
        "alias": alias,
        "model_id": expected["model_id"],
        "revision": expected["revision"],
        "source": source,
        "license": license_name,
        "files": files,
        "total_byte_size": total,
    }
    manifest = {
        **body,
        "manifest_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }
    validate_model_manifest(manifest, expected_alias=alias, expected_model=expected)
    return manifest


def validate_corpus_commitment(
    value: Any,
    *,
    corpus_sha256: str,
    queries_sha256: str,
    query_case_ids_sha256: str,
    corpus_record_count: int,
    query_case_count: int,
) -> dict[str, Any]:
    commitment = _closed(
        value,
        field="benchmark corpus commitment",
        keys={
            "schema_version",
            "commitment_id",
            "evaluator_organization",
            "commitment_stage",
            "corpus_sha256",
            "queries_sha256",
            "query_case_ids_sha256",
            "corpus_record_count",
            "query_case_count",
            "labels_access",
            "committed_at",
            "claim_eligible",
            "record_sha256",
        },
    )
    if (
        commitment.get("schema_version") != CORPUS_SCHEMA
        or commitment.get("commitment_stage") != "before_candidate_delivery"
        or commitment.get("claim_eligible") is not False
    ):
        raise CandidateError("benchmark corpus commitment overstates its state")
    for field in ("commitment_id", "evaluator_organization"):
        _bounded_string(commitment.get(field), field=field, maximum=200)
    if commitment.get("labels_access") not in {"public_frozen", "external_evaluator_only"}:
        raise CandidateError("benchmark corpus label access is invalid")
    _timestamp(commitment.get("committed_at"), field="committed_at")
    expected = {
        "corpus_sha256": corpus_sha256,
        "queries_sha256": queries_sha256,
        "query_case_ids_sha256": query_case_ids_sha256,
        "corpus_record_count": corpus_record_count,
        "query_case_count": query_case_count,
    }
    if any(commitment.get(field) != expected_value for field, expected_value in expected.items()):
        raise CandidateError("benchmark corpus commitment differs from collected evidence")
    _record_sha256(
        commitment,
        field="benchmark corpus commitment",
        digest_field="record_sha256",
    )
    return commitment


def build_corpus_commitment(
    *,
    corpus_path: Path,
    queries_path: Path,
    commitment_id: str,
    evaluator_organization: str,
    labels_access: str,
    committed_at: str,
) -> dict[str, Any]:
    corpus = corpus_path.expanduser().absolute()
    queries = queries_path.expanduser().absolute()
    corpus_count = _count_jsonl(corpus, field="benchmark corpus")
    try:
        query_binding = _queries_binding(queries)
    except (OSError, RuntimeError, ValueError) as error:
        raise CandidateError("benchmark queries do not match the baseline protocol") from error
    body = {
        "schema_version": CORPUS_SCHEMA,
        "commitment_id": commitment_id,
        "evaluator_organization": evaluator_organization,
        "commitment_stage": "before_candidate_delivery",
        "corpus_sha256": sha256_file(corpus),
        "queries_sha256": query_binding["sha256"],
        "query_case_ids_sha256": query_binding["case_ids_sha256"],
        "corpus_record_count": corpus_count,
        "query_case_count": query_binding["case_count"],
        "labels_access": labels_access,
        "committed_at": committed_at,
        "claim_eligible": False,
    }
    commitment = {
        **body,
        "record_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }
    validate_corpus_commitment(
        commitment,
        corpus_sha256=body["corpus_sha256"],
        queries_sha256=body["queries_sha256"],
        query_case_ids_sha256=body["query_case_ids_sha256"],
        corpus_record_count=corpus_count,
        query_case_count=query_binding["case_count"],
    )
    return commitment


def validate_internal_gate(
    value: Any,
    *,
    candidate_commit: str,
    registry_digest: str,
    collection_report_digest: str,
) -> dict[str, Any]:
    gate = _closed(
        value,
        field="internal baseline gate",
        keys={
            "schema_version",
            "candidate_commit",
            "registry_sha256",
            "collection_report_sha256",
            "statistical_protocol_sha256",
            "case_results_manifest_sha256",
            "comparisons_manifest_sha256",
            "paired_bootstrap_iterations",
            "confidence_level",
            "multiple_comparison_correction",
            "threshold_adjustments_used",
            "thresholds_frozen_at",
            "gate_completed_at",
            "thresholds_frozen_before_held_out",
            "case_level_results_retained",
            "failures_retained",
            "professional_baseline_gates_passed",
            "aggregate_gate_passed",
            "security_regression_count",
            "gate_status",
            "claim_eligible",
            "record_sha256",
        },
    )
    expected = {
        "schema_version": BASELINE_GATE_SCHEMA,
        "candidate_commit": candidate_commit,
        "registry_sha256": registry_digest,
        "collection_report_sha256": collection_report_digest,
        "paired_bootstrap_iterations": 10_000,
        "confidence_level": 0.95,
        "multiple_comparison_correction": "holm-bonferroni",
        "thresholds_frozen_before_held_out": True,
        "case_level_results_retained": True,
        "failures_retained": True,
        "professional_baseline_gates_passed": True,
        "aggregate_gate_passed": True,
        "security_regression_count": 0,
        "gate_status": "passed",
        "claim_eligible": False,
    }
    if any(gate.get(field) != expected_value for field, expected_value in expected.items()):
        raise CandidateError("internal baseline gate has not passed its closed release policy")
    if gate.get("threshold_adjustments_used") not in {0, 1}:
        raise CandidateError("internal baseline gate threshold adjustment count is invalid")
    thresholds_frozen_at = _timestamp(
        gate.get("thresholds_frozen_at"),
        field="internal gate thresholds_frozen_at",
    )
    gate_completed_at = _timestamp(
        gate.get("gate_completed_at"),
        field="internal gate gate_completed_at",
    )
    if thresholds_frozen_at > gate_completed_at:
        raise CandidateError("internal baseline thresholds were frozen after gate completion")
    for field in (
        "statistical_protocol_sha256",
        "case_results_manifest_sha256",
        "comparisons_manifest_sha256",
    ):
        if not _SHA256.fullmatch(str(gate.get(field))):
            raise CandidateError(f"internal baseline gate {field} is invalid")
    _record_sha256(gate, field="internal baseline gate", digest_field="record_sha256")
    return gate


def _git(repository: Path, *arguments: str, binary: bool = False) -> str | bytes:
    process = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=not binary,
        timeout=30,
    )
    if process.returncode != 0:
        stderr = process.stderr if isinstance(process.stderr, str) else process.stderr.decode()
        raise CandidateError(f"Git command failed: {stderr.strip()}")
    if binary:
        return process.stdout
    return process.stdout.strip()


@dataclass(frozen=True)
class GitCandidate:
    commit: str
    tree: str
    package_version: str


def validate_git_candidate(repository: Path, candidate_commit: str) -> GitCandidate:
    selected = repository.expanduser().resolve()
    if not selected.is_dir():
        raise CandidateError("candidate repository is unavailable")
    root = Path(str(_git(selected, "rev-parse", "--show-toplevel"))).resolve()
    if root != selected:
        raise CandidateError("candidate repository must be the exact Git worktree root")
    commit = str(_git(selected, "rev-parse", f"{candidate_commit}^{{commit}}"))
    head = str(_git(selected, "rev-parse", "HEAD"))
    if not _COMMIT.fullmatch(commit) or head != commit:
        raise CandidateError("candidate commit must be the exact current HEAD")
    if str(_git(selected, "status", "--porcelain", "--untracked-files=all")):
        raise CandidateError("candidate worktree must be completely clean")
    stages = str(_git(selected, "ls-files", "--stage")).splitlines()
    if any(line.startswith("160000 ") for line in stages):
        raise CandidateError("source-tree archive cannot omit Git submodule contents")
    tree = str(_git(selected, "rev-parse", f"{commit}^{{tree}}"))
    if not _TREE.fullmatch(tree):
        raise CandidateError("candidate Git tree identity is invalid")
    pyproject = tomllib.loads(_git_file(selected, commit, "pyproject.toml").decode("utf-8"))
    version = pyproject.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise CandidateError("candidate package version is unavailable")
    return GitCandidate(commit=commit, tree=tree, package_version=version)


def _git_file(repository: Path, commit: str, path: str) -> bytes:
    value = _git(repository, "show", f"{commit}:{path}", binary=True)
    if not isinstance(value, bytes):
        raise CandidateError("Git file reader returned text unexpectedly")
    return value


def _git_file_binding(repository: Path, commit: str, path: str) -> dict[str, Any]:
    payload = _git_file(repository, commit, path)
    return {
        "path": path,
        "sha256": sha256_bytes(payload),
        "byte_size": len(payload),
    }


def _git_inventory(
    repository: Path,
    commit: str,
    *,
    prefix: str,
    suffix: str | None = None,
) -> list[dict[str, Any]]:
    output = str(_git(repository, "ls-tree", "-r", "--name-only", commit, "--", prefix))
    paths = [
        path
        for path in output.splitlines()
        if path and (suffix is None or path.endswith(suffix))
    ]
    if not paths:
        raise CandidateError(f"candidate source tree has no tracked {prefix} inventory")
    return [_git_file_binding(repository, commit, path) for path in sorted(paths)]


def _source_archive_inventory(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    inventory: list[dict[str, Any]] = []
    observed: set[str] = set()
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            name = _safe_relative(member.name, field="source archive path")
            if name in observed or not (member.isdir() or member.isreg()):
                raise CandidateError("source archive contains a duplicate or unsafe entry")
            observed.add(name)
            if member.isreg():
                stream = archive.extractfile(member)
                if stream is None:
                    raise CandidateError("source archive regular file cannot be read")
                digest = hashlib.sha256()
                while block := stream.read(1024 * 1024):
                    digest.update(block)
                inventory.append(
                    {"path": name, "sha256": digest.hexdigest(), "byte_size": member.size}
                )
    if not inventory:
        raise CandidateError("source archive has no regular files")
    inventory.sort(key=lambda item: item["path"])
    report = {
        "file_count": len(inventory),
        "inventory_sha256": sha256_bytes(canonical_json(inventory).encode("utf-8")),
    }
    return report, {item["path"]: item for item in inventory}


def _source_archive(repository: Path, commit: str, output: Path) -> dict[str, Any]:
    process = subprocess.run(
        ["git", "archive", "--format=tar", "--output", str(output), commit],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode != 0:
        raise CandidateError(f"Git source archive failed: {process.stderr.strip()}")
    report, inventory = _source_archive_inventory(output)
    tracked = str(_git(repository, "ls-tree", "-r", "--name-only", commit)).splitlines()
    if sorted(inventory) != sorted(tracked):
        raise CandidateError("Git source archive does not cover the complete tracked tree")
    return report


def _constant(source: bytes, name: str) -> str:
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as error:
        raise CandidateError("retrieval profile source cannot be parsed") from error
    values: list[str] = []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            values.append(node.value.value)
    if len(values) != 1:
        raise CandidateError(f"candidate source does not define one literal {name}")
    return values[0]


def build_profile_commitment(
    repository: Path,
    *,
    candidate: GitCandidate,
    registry: dict[str, Any],
) -> dict[str, Any]:
    source = _git_file(repository, candidate.commit, "src/deeplaw/retrieval_fabric.py")
    tokenizer_profile = _constant(source, "TOKENIZER_PROFILE")
    fusion_profile = _constant(source, "FUSION_PROFILE")
    if "/" not in tokenizer_profile:
        raise CandidateError("tokenizer profile lacks an immutable version")
    candidate_profiles = [
        {
            "system_id": system["system_id"],
            "configuration": system["configuration"],
            "model_aliases": system["model_aliases"],
        }
        for system in registry["systems"]
        if system["system_id"].startswith("deeplaw/")
    ]
    if len(candidate_profiles) != 3:
        raise CandidateError("profile commitment must cover three DeepLaw operating points")
    body = {
        "schema_version": PROFILE_SCHEMA,
        "candidate_commit": candidate.commit,
        "tokenizer_profile": tokenizer_profile,
        "tokenizer_version": tokenizer_profile.rsplit("/", maxsplit=1)[1],
        "fusion_profile": fusion_profile,
        "candidate_profiles": candidate_profiles,
        "implementation_files": [
            _git_file_binding(repository, candidate.commit, path)
            for path in _PROFILE_IMPLEMENTATION_PATHS
        ],
    }
    return {
        **body,
        "record_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def _descriptor(value: Any, *, field: str, media_types: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CandidateError(f"{field} must be an OCI descriptor")
    media_type = value.get("mediaType")
    digest = value.get("digest")
    size = value.get("size")
    if (
        media_type not in media_types
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or not _SHA256.fullmatch(digest.removeprefix("sha256:"))
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
    ):
        raise CandidateError(f"{field} has an invalid OCI descriptor")
    return value


def validate_oci_archive(
    path: Path,
    *,
    candidate_commit: str,
    package_version: str,
    wheel_sha256: str,
    sdist_sha256: str,
    lock_sha256: str,
) -> dict[str, Any]:
    selected = path.expanduser().absolute()
    if selected.is_symlink() or not selected.is_file():
        raise CandidateError("container must be a regular OCI archive")
    artifact_bindings = {
        "wheel_sha256": wheel_sha256,
        "sdist_sha256": sdist_sha256,
        "lock_sha256": lock_sha256,
    }
    if any(not _SHA256.fullmatch(str(value)) for value in artifact_bindings.values()):
        raise CandidateError("OCI candidate artifact bindings are invalid")
    entries: dict[str, tarfile.TarInfo] = {}
    with tarfile.open(selected, "r:*") as archive:
        for member in archive:
            name = _safe_relative(member.name, field="OCI archive path")
            if name in entries or not (member.isdir() or member.isreg()):
                raise CandidateError("OCI archive contains a duplicate or unsafe entry")
            entries[name] = member
        if set(entries) < {"oci-layout", "index.json"}:
            raise CandidateError("OCI archive lacks oci-layout or index.json")

        def read_entry(name: str, *, field: str, maximum: int) -> bytes:
            member = entries.get(name)
            if member is None or not member.isreg() or not 1 <= member.size <= maximum:
                raise CandidateError(f"{field} is absent or oversized")
            stream = archive.extractfile(member)
            if stream is None:
                raise CandidateError(f"{field} cannot be read")
            return stream.read()

        def blob(descriptor: dict[str, Any], *, field: str, read: bool) -> bytes | None:
            digest = descriptor["digest"].removeprefix("sha256:")
            name = f"blobs/sha256/{digest}"
            member = entries.get(name)
            if (
                member is None
                or not member.isreg()
                or member.size != descriptor["size"]
            ):
                raise CandidateError(f"{field} blob is absent or differs from its descriptor")
            stream = archive.extractfile(member)
            if stream is None:
                raise CandidateError(f"{field} blob cannot be read")
            hasher = hashlib.sha256()
            retained = bytearray() if read else None
            while block := stream.read(1024 * 1024):
                hasher.update(block)
                if retained is not None:
                    if len(retained) + len(block) > _MAX_JSON_BYTES:
                        raise CandidateError(f"{field} metadata blob is oversized")
                    retained.extend(block)
            if hasher.hexdigest() != digest:
                raise CandidateError(f"{field} blob digest is invalid")
            return bytes(retained) if retained is not None else None

        try:
            layout = strict_json_loads(
                read_entry("oci-layout", field="OCI layout", maximum=4_096)
            )
            index = strict_json_loads(
                read_entry("index.json", field="OCI index", maximum=_MAX_JSON_BYTES)
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise CandidateError("OCI root metadata is invalid") from error
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise CandidateError("OCI layout version is not 1.0.0")
        if not isinstance(index, dict) or index.get("schemaVersion") != 2:
            raise CandidateError("OCI image index is invalid")
        manifests = index.get("manifests")
        if not isinstance(manifests, list) or not 1 <= len(manifests) <= 16:
            raise CandidateError("OCI image index manifest inventory is invalid")

        platforms: list[dict[str, str]] = []
        for index_number, raw_descriptor in enumerate(manifests):
            descriptor = _descriptor(
                raw_descriptor,
                field=f"OCI index manifests[{index_number}]",
                media_types={_OCI_MANIFEST},
            )
            annotations = descriptor.get("annotations")
            if not isinstance(annotations, dict) or (
                annotations.get("org.opencontainers.image.revision") != candidate_commit
                or annotations.get("org.opencontainers.image.version") != package_version
            ):
                raise CandidateError("OCI manifest descriptor does not bind candidate identity")
            try:
                manifest_payload = blob(
                    descriptor,
                    field="OCI image manifest",
                    read=True,
                )
                if manifest_payload is None:
                    raise CandidateError("OCI image manifest bytes are unavailable")
                manifest = strict_json_loads(manifest_payload)
            except (UnicodeDecodeError, ValueError) as error:
                raise CandidateError("OCI image manifest is invalid JSON") from error
            if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
                raise CandidateError("OCI image manifest contract is invalid")
            config_descriptor = _descriptor(
                manifest.get("config"),
                field="OCI image config",
                media_types={_OCI_CONFIG},
            )
            layers = manifest.get("layers")
            if not isinstance(layers, list):
                raise CandidateError("OCI image layers are invalid")
            for layer_number, layer in enumerate(layers):
                layer_descriptor = _descriptor(
                    layer,
                    field=f"OCI image layers[{layer_number}]",
                    media_types=_OCI_LAYERS,
                )
                blob(layer_descriptor, field=f"OCI image layers[{layer_number}]", read=False)
            try:
                config_payload = blob(
                    config_descriptor,
                    field="OCI image config",
                    read=True,
                )
                if config_payload is None:
                    raise CandidateError("OCI image config bytes are unavailable")
                config = strict_json_loads(config_payload)
            except (UnicodeDecodeError, ValueError) as error:
                raise CandidateError("OCI image config is invalid JSON") from error
            labels = (
                config.get("config", {}).get("Labels") if isinstance(config, dict) else None
            )
            if not isinstance(labels, dict) or (
                labels.get("org.opencontainers.image.revision") != candidate_commit
                or labels.get("org.opencontainers.image.version") != package_version
                or any(
                    labels.get(_OCI_ARTIFACT_LABELS[field]) != digest
                    for field, digest in artifact_bindings.items()
                )
            ):
                raise CandidateError("OCI image config does not bind candidate identity")
            platform = descriptor.get("platform")
            if not isinstance(platform, dict):
                raise CandidateError("OCI image descriptor lacks a platform")
            os_name = _bounded_string(
                platform.get("os"), field="OCI platform.os", maximum=50
            )
            architecture = _bounded_string(
                platform.get("architecture"),
                field="OCI platform.architecture",
                maximum=50,
            )
            platforms.append({"os": os_name, "architecture": architecture})
    if len({canonical_json(item) for item in platforms}) != len(platforms):
        raise CandidateError("OCI archive contains duplicate platforms")
    return {
        "platform_count": len(platforms),
        "platforms": sorted(platforms, key=lambda item: (item["os"], item["architecture"])),
        "blob_count": sum(name.startswith("blobs/sha256/") for name in entries),
        "artifact_bindings": artifact_bindings,
    }


def _count_jsonl(path: Path, *, field: str) -> int:
    selected = path.expanduser().absolute()
    if selected.is_symlink() or not selected.is_file():
        raise CandidateError(f"{field} is unavailable")
    if not 1 <= selected.stat().st_size <= 64 * 1024 * 1024:
        raise CandidateError(f"{field} violates the closed corpus byte bound")
    count = 0
    character_count = 0
    document_ids: set[str] = set()
    with selected.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = strict_json_loads(line)
            except (UnicodeDecodeError, ValueError) as error:
                raise CandidateError(f"{field} line {line_number} is invalid") from error
            if not isinstance(value, dict) or set(value) != {"id", "title", "text"}:
                raise CandidateError(f"{field} line {line_number} must contain id/title/text")
            for key, maximum in (("id", 500), ("title", 500), ("text", 20 * 1024 * 1024)):
                text = value.get(key)
                if (
                    not isinstance(text, str)
                    or not text
                    or text != text.strip()
                    or len(text) > maximum
                ):
                    raise CandidateError(
                        f"{field} line {line_number} {key} is not bounded canonical text"
                    )
            document_id = value["id"]
            if document_id in document_ids:
                raise CandidateError(f"{field} contains duplicate document IDs")
            document_ids.add(document_id)
            character_count += len(value["text"])
            if character_count > 20 * 1024 * 1024 or count >= 100_000:
                raise CandidateError(f"{field} exceeds the closed corpus content bound")
            count += 1
    if count < 1:
        raise CandidateError(f"{field} is empty")
    return count


@dataclass(frozen=True)
class EvidenceCollection:
    registry: dict[str, Any]
    registry_digest: str
    report: dict[str, Any]
    files: tuple[tuple[str, Path, str], ...]
    model_manifest_digests: dict[str, str]
    corpus_path: Path
    queries_path: Path
    query_case_count: int


def _artifact_path(value: Any, *, field: str) -> Path:
    if not isinstance(value, dict):
        raise CandidateError(f"{field} artifact is invalid")
    raw = value.get("path_hint")
    if not isinstance(raw, str):
        raise CandidateError(f"{field} path is invalid")
    path = Path(raw)
    if not path.is_absolute():
        raise CandidateError(f"{field} path must be absolute")
    if path.is_symlink() or not path.is_file():
        raise CandidateError(f"{field} artifact is unavailable")
    if value.get("sha256") != sha256_file(path) or value.get("byte_size") != path.stat().st_size:
        raise CandidateError(f"{field} artifact differs from its receipt")
    return path


def collect_baseline_evidence(
    *,
    registry_path: Path,
    collection_path: Path,
    collection_report_path: Path,
    candidate_commit: str,
) -> EvidenceCollection:
    registry = load_registry(registry_path)
    if registry["candidate_line"] != FROZEN_EVALUATION_CANDIDATE_LINE:
        raise CandidateError("evaluator kit requires a frozen candidate registry")
    for system in registry["systems"]:
        if system["system_id"].startswith("deeplaw/") and (
            system["implementation"]["revision"] != candidate_commit
        ):
            raise CandidateError("frozen registry targets a different candidate commit")
    computed_report = build_collection_report(
        registry_path=registry_path,
        collection_path=collection_path,
    )
    supplied_report = _read_json(collection_report_path, field="collection report")
    if canonical_json(computed_report) != canonical_json(supplied_report):
        raise CandidateError("collection report differs from a fresh evidence validation")
    if (
        not computed_report["collection_complete"]
        or computed_report["successful_run_count"] != computed_report["expected_system_count"]
    ):
        raise CandidateError("all named baseline executions must be complete and successful")
    collection = _read_json(collection_path, field="collection manifest")
    files: list[tuple[str, Path, str]] = [
        ("collection-manifest", collection_path, "application/json"),
        ("collection-report", collection_report_path, "application/json"),
    ]
    model_digests: dict[str, str] = {}
    corpus_path: Path | None = None
    queries_path: Path | None = None
    query_case_count: int | None = None
    for run in collection["runs"]:
        system_id = run["system_id"]
        safe_id = system_id.replace("/", "--")
        plan_path = Path(run["plan_path"])
        receipt_path = Path(run["receipt_path"])
        plan = _read_json(plan_path, field=f"{system_id} plan")
        receipt = _read_json(receipt_path, field=f"{system_id} receipt")
        files.extend(
            [
                (f"{safe_id}/plan", plan_path, "application/json"),
                (f"{safe_id}/receipt", receipt_path, "application/json"),
            ]
        )
        environment_path = Path(plan["evaluation_environment"]["path_hint"])
        environment = _read_json(environment_path, field=f"{system_id} environment")
        files.append((f"{safe_id}/environment", environment_path, "application/json"))
        for model in [*environment["models"], environment["reader"]]:
            alias = model["alias"]
            digest = model["artifact_manifest_sha256"]
            previous = model_digests.setdefault(alias, digest)
            if previous != digest:
                raise CandidateError(f"model manifest digest differs across runs for {alias}")
        observed_corpus = Path(plan["corpus"]["path_hint"])
        observed_queries = Path(plan["queries"]["path_hint"])
        observed_case_count = plan["queries"]["case_count"]
        if corpus_path is None:
            corpus_path = observed_corpus
            queries_path = observed_queries
            query_case_count = observed_case_count
        elif (
            corpus_path != observed_corpus
            or queries_path != observed_queries
            or query_case_count != observed_case_count
        ):
            raise CandidateError("collected evidence does not share exact input paths")
        for field, media_type in (
            ("raw_output", "application/x-ndjson"),
            ("resource_record", "application/json"),
        ):
            artifact = receipt.get(field)
            if artifact is None:
                raise CandidateError(f"successful {system_id} receipt lacks {field}")
            files.append(
                (
                    f"{safe_id}/{field.replace('_', '-')}",
                    _artifact_path(artifact, field=field),
                    media_type,
                )
            )
        if "stdout" in receipt:
            for field in ("stdout", "stderr"):
                files.append(
                    (
                        f"{safe_id}/{field}",
                        _artifact_path(receipt[field], field=field),
                        "text/plain",
                    )
                )
        manual_artifact = receipt.get("manual_record")
        if manual_artifact is not None:
            manual_path = _artifact_path(manual_artifact, field="manual record")
            manual_record = _read_json(manual_path, field="manual record")
            files.append((f"{safe_id}/manual-record", manual_path, "application/json"))
            for field, media_type in (
                ("screen_recording", "video/octet-stream"),
                ("vault_before_archive", "application/octet-stream"),
                ("vault_after_archive", "application/octet-stream"),
            ):
                files.append(
                    (
                        f"{safe_id}/{field.replace('_', '-')}",
                        _artifact_path(manual_record[field], field=field),
                        media_type,
                    )
                )
    if corpus_path is None or queries_path is None or query_case_count is None:
        raise CandidateError("baseline evidence collection contains no runs")
    if len(files) > _MAX_EVIDENCE_FILES:
        raise CandidateError("baseline evidence artifact inventory is oversized")
    return EvidenceCollection(
        registry=registry,
        registry_digest=registry_sha256(registry),
        report=computed_report,
        files=tuple(files),
        model_manifest_digests=model_digests,
        corpus_path=corpus_path,
        queries_path=queries_path,
        query_case_count=query_case_count,
    )


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.names: set[str] = set()

    def add_file(self, path: Path, *, logical_name: str, media_type: str) -> dict[str, Any]:
        selected = path.expanduser().absolute()
        if selected.is_symlink() or not selected.is_file():
            raise CandidateError(f"artifact is missing or unsafe: {logical_name}")
        digest = sha256_file(selected)
        return self._store(
            source=selected,
            payload=None,
            logical_name=logical_name,
            media_type=media_type,
            digest=digest,
            size=selected.stat().st_size,
        )

    def add_json(self, value: dict[str, Any], *, logical_name: str) -> dict[str, Any]:
        payload = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        return self._store(
            source=None,
            payload=payload,
            logical_name=logical_name,
            media_type="application/json",
            digest=sha256_bytes(payload),
            size=len(payload),
        )

    def _store(
        self,
        *,
        source: Path | None,
        payload: bytes | None,
        logical_name: str,
        media_type: str,
        digest: str,
        size: int,
    ) -> dict[str, Any]:
        name = _safe_relative(logical_name, field="artifact logical_name")
        if name in self.names:
            raise CandidateError(f"duplicate artifact logical name: {name}")
        self.names.add(name)
        relative = PurePosixPath("blobs", "sha256", digest[:2], digest).as_posix()
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    if source is not None:
                        with source.open("rb") as source_stream:
                            shutil.copyfileobj(source_stream, stream, length=1024 * 1024)
                    else:
                        if payload is None:
                            raise CandidateError("generated artifact payload is unavailable")
                        stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        if destination.stat().st_size != size or sha256_file(destination) != digest:
            raise CandidateError(f"stored artifact differs: {name}")
        return {
            "logical_name": name,
            "blob_path": relative,
            "sha256": digest,
            "byte_size": size,
            "media_type": media_type,
        }


def _release_report(
    path: Path,
    *,
    candidate: GitCandidate,
    wheel: Path,
    sdist: Path,
    lock_sha256: str,
    wheel_name: str | None = None,
    sdist_name: str | None = None,
) -> dict[str, Any]:
    report = _read_json(path, field="reproducible build report")
    _validate_schema(
        report,
        name="reproducible-build-report.v2.schema.json",
        field="reproducible build report",
    )
    if (
        report.get("schema_version") != "deeplaw.reproducible-build-report/v2"
        or report.get("repository_commit") != candidate.commit
        or report.get("working_tree_dirty") is not False
        or report.get("reproducible") is not True
        or report.get("package_inventory_verified") is not True
        or report.get("artifact_release_eligible") is not True
        or report.get("artifact_release_blockers") != []
        or report.get("lock_sha256") != lock_sha256
    ):
        raise CandidateError("reproducible build report is not a clean frozen-candidate report")
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 2:
        raise CandidateError("reproducible build report artifact inventory is invalid")
    expected_paths = {wheel_name or wheel.name: wheel, sdist_name or sdist.name: sdist}
    if set(expected_paths) != {item.get("name") for item in artifacts if isinstance(item, dict)}:
        raise CandidateError("release files differ from the reproducible build report")
    for item in artifacts:
        selected = expected_paths[item["name"]]
        if (
            selected.is_symlink()
            or not selected.is_file()
            or item.get("sha256") != sha256_file(selected)
            or item.get("byte_size") != selected.stat().st_size
        ):
            raise CandidateError("release artifact bytes differ from the reproducible report")
    return report


def _sbom(path: Path, *, package_version: str) -> dict[str, Any]:
    value = _read_json(path, field="SBOM")
    component = value.get("metadata", {}).get("component")
    if (
        value.get("bomFormat") != "CycloneDX"
        or value.get("specVersion") != "1.5"
        or not isinstance(component, dict)
        or component.get("name") != "deeplaw"
        or component.get("version") != package_version
        or not isinstance(value.get("components"), list)
        or not value["components"]
    ):
        raise CandidateError("SBOM does not describe the frozen DeepLaw package")
    return value


def _license_inventory(path: Path) -> dict[str, Any]:
    value = _read_json(path, field="license inventory")
    _validate_schema(
        value,
        name="installed-license-inventory.v1.schema.json",
        field="license inventory",
    )
    if (
        value.get("schema_version") != "deeplaw.installed-license-inventory/v1"
        or value.get("status") != "passed"
        or value.get("blocked") != []
        or value.get("review_required") != []
        or value.get("package_count") != len(value.get("packages", []))
    ):
        raise CandidateError("license inventory has not passed its release policy")
    return value


def _bound_external_file(
    path: Path,
    *,
    expected_sha256: str,
    field: str,
) -> None:
    if path.is_symlink() or not path.is_file() or sha256_file(path) != expected_sha256:
        raise CandidateError(f"{field} differs from the internal baseline gate")


def freeze_evaluator_kit(
    *,
    repository: Path,
    candidate_commit: str,
    destination: Path,
    registry_path: Path,
    collection_path: Path,
    collection_report_path: Path,
    corpus_commitment_path: Path,
    internal_gate_path: Path,
    statistical_protocol_path: Path,
    case_results_manifest_path: Path,
    comparisons_manifest_path: Path,
    model_manifest_paths: dict[str, Path],
    wheel_path: Path,
    sdist_path: Path,
    container_path: Path,
    sbom_path: Path,
    license_inventory_path: Path,
    reproducible_build_path: Path,
    created_at: str,
) -> dict[str, Any]:
    repository = repository.expanduser().resolve()
    candidate = validate_git_candidate(repository, candidate_commit)
    created_timestamp = _timestamp(created_at, field="created_at")
    selected_destination = destination.expanduser().absolute()
    if selected_destination.exists() or selected_destination.is_symlink():
        raise FileExistsError("evaluator kit destination must be new")
    if selected_destination.parent.is_symlink() or not selected_destination.parent.is_dir():
        raise CandidateError("evaluator kit destination parent must be an existing safe directory")
    evidence = collect_baseline_evidence(
        registry_path=registry_path.expanduser().absolute(),
        collection_path=collection_path.expanduser().absolute(),
        collection_report_path=collection_report_path.expanduser().absolute(),
        candidate_commit=candidate.commit,
    )
    if set(model_manifest_paths) != set(evidence.registry["shared_models"]):
        raise CandidateError("model manifests must cover every registered shared model")
    model_manifests: dict[str, tuple[dict[str, Any], Path]] = {}
    for alias, expected_model in evidence.registry["shared_models"].items():
        path = model_manifest_paths[alias].expanduser().absolute()
        manifest = validate_model_manifest(
            _read_json(path, field=f"model manifest {alias}"),
            expected_alias=alias,
            expected_model=expected_model,
        )
        if evidence.model_manifest_digests.get(alias) != manifest["manifest_sha256"]:
            raise CandidateError(f"collected environment does not bind model manifest {alias}")
        model_manifests[alias] = (manifest, path)
    if set(evidence.model_manifest_digests) != set(model_manifests):
        raise CandidateError("collected evidence has an unexpected model manifest alias")

    common = evidence.report["common_bindings"]
    corpus_count = _count_jsonl(evidence.corpus_path, field="benchmark corpus")
    commitment = validate_corpus_commitment(
        _read_json(corpus_commitment_path, field="benchmark corpus commitment"),
        corpus_sha256=common["corpus_sha256"],
        queries_sha256=common["queries_sha256"],
        query_case_ids_sha256=common["query_case_ids_sha256"],
        corpus_record_count=corpus_count,
        query_case_count=evidence.query_case_count,
    )
    if _timestamp(commitment["committed_at"], field="committed_at") >= created_timestamp:
        raise CandidateError("corpus commitment must predate evaluator kit creation")
    gate = validate_internal_gate(
        _read_json(internal_gate_path, field="internal baseline gate"),
        candidate_commit=candidate.commit,
        registry_digest=evidence.registry_digest,
        collection_report_digest=evidence.report["report_sha256"],
    )
    if _timestamp(gate["gate_completed_at"], field="gate_completed_at") > created_timestamp:
        raise CandidateError("internal baseline gate completion postdates kit creation")
    for path, digest, field in (
        (
            statistical_protocol_path,
            gate["statistical_protocol_sha256"],
            "statistical protocol",
        ),
        (
            case_results_manifest_path,
            gate["case_results_manifest_sha256"],
            "case results manifest",
        ),
        (
            comparisons_manifest_path,
            gate["comparisons_manifest_sha256"],
            "comparisons manifest",
        ),
    ):
        _bound_external_file(path.expanduser().absolute(), expected_sha256=digest, field=field)

    lock_binding = _git_file_binding(repository, candidate.commit, "uv.lock")
    _release_report(
        reproducible_build_path.expanduser().absolute(),
        candidate=candidate,
        wheel=wheel_path.expanduser().absolute(),
        sdist=sdist_path.expanduser().absolute(),
        lock_sha256=lock_binding["sha256"],
    )
    _sbom(sbom_path.expanduser().absolute(), package_version=candidate.package_version)
    _license_inventory(license_inventory_path.expanduser().absolute())
    oci = validate_oci_archive(
        container_path,
        candidate_commit=candidate.commit,
        package_version=candidate.package_version,
        wheel_sha256=sha256_file(wheel_path.expanduser().absolute()),
        sdist_sha256=sha256_file(sdist_path.expanduser().absolute()),
        lock_sha256=lock_binding["sha256"],
    )
    profile = build_profile_commitment(
        repository,
        candidate=candidate,
        registry=evidence.registry,
    )
    contracts = _git_inventory(
        repository,
        candidate.commit,
        prefix="contracts",
        suffix=".json",
    )
    signature_tools = [
        _git_file_binding(repository, candidate.commit, path)
        for path in _SIGNATURE_TOOL_PATHS
    ]

    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{selected_destination.name}.",
            dir=selected_destination.parent,
        )
    )
    try:
        store = ArtifactStore(staging)
        with tempfile.TemporaryDirectory(prefix="deeplaw-source-archive-") as temporary:
            archive_path = Path(temporary) / "source-tree.tar"
            source_inventory = _source_archive(repository, candidate.commit, archive_path)
            source_artifact = store.add_file(
                archive_path,
                logical_name="source/source-tree.tar",
                media_type="application/x-tar",
            )
        release_artifacts = {
            "lock": store.add_file(
                repository / "uv.lock",
                logical_name="source/uv.lock",
                media_type="text/plain",
            ),
            "wheel": store.add_file(
                wheel_path, logical_name=f"release/{wheel_path.name}", media_type="application/zip"
            ),
            "sdist": store.add_file(
                sdist_path, logical_name=f"release/{sdist_path.name}", media_type="application/gzip"
            ),
            "container": store.add_file(
                container_path,
                logical_name=f"release/{container_path.name}",
                media_type="application/vnd.oci.image.layout.v1.tar",
            ),
            "sbom": store.add_file(
                sbom_path, logical_name="release/deeplaw.cdx.json", media_type="application/json"
            ),
            "license_inventory": store.add_file(
                license_inventory_path,
                logical_name="release/installed-licenses.json",
                media_type="application/json",
            ),
            "reproducible_build": store.add_file(
                reproducible_build_path,
                logical_name="release/reproducible-build.json",
                media_type="application/json",
            ),
        }
        profile_artifact = store.add_json(
            profile,
            logical_name="commitments/retrieval-profiles.json",
        )
        model_entries = []
        for alias, (manifest, path) in sorted(model_manifests.items()):
            model_entries.append(
                {
                    "alias": alias,
                    "model_id": manifest["model_id"],
                    "revision": manifest["revision"],
                    "manifest_sha256": manifest["manifest_sha256"],
                    "artifact": store.add_file(
                        path,
                        logical_name=f"commitments/models/{alias}.json",
                        media_type="application/json",
                    ),
                }
            )
        evidence_artifacts = [
            store.add_file(path, logical_name=f"evidence/{name}", media_type=media_type)
            for name, path, media_type in evidence.files
        ]
        commitments = {
            "corpus": store.add_file(
                corpus_commitment_path,
                logical_name="commitments/benchmark-corpus.json",
                media_type="application/json",
            ),
            "baseline_registry": store.add_file(
                registry_path,
                logical_name="commitments/frozen-baseline-registry.json",
                media_type="application/json",
            ),
            "internal_gate": store.add_file(
                internal_gate_path,
                logical_name="commitments/internal-baseline-gate.json",
                media_type="application/json",
            ),
            "statistical_protocol": store.add_file(
                statistical_protocol_path,
                logical_name="commitments/statistical-protocol.json",
                media_type="application/json",
            ),
            "case_results_manifest": store.add_file(
                case_results_manifest_path,
                logical_name="commitments/case-results-manifest.json",
                media_type="application/json",
            ),
            "comparisons_manifest": store.add_file(
                comparisons_manifest_path,
                logical_name="commitments/comparisons-manifest.json",
                media_type="application/json",
            ),
        }
        body = {
            "schema_version": KIT_SCHEMA,
            "kit_id": f"deeplaw-{candidate.package_version}-{candidate.commit[:12]}",
            "created_at": created_at,
            "candidate": {
                "candidate_line": FROZEN_EVALUATION_CANDIDATE_LINE,
                "package_version": candidate.package_version,
                "commit": candidate.commit,
                "git_tree": candidate.tree,
                "source_archive": source_artifact,
                **source_inventory,
            },
            "contracts": {
                "count": len(contracts),
                "inventory_sha256": sha256_bytes(canonical_json(contracts).encode("utf-8")),
                "files": contracts,
            },
            "release_artifacts": release_artifacts,
            "container": oci,
            "models": model_entries,
            "retrieval_profiles": {
                "record_sha256": profile["record_sha256"],
                "artifact": profile_artifact,
            },
            "benchmark_commitments": {
                "registry_sha256": evidence.registry_digest,
                "corpus_record_sha256": commitment["record_sha256"],
                "internal_gate_record_sha256": gate["record_sha256"],
                "artifacts": commitments,
            },
            "baseline_evidence": {
                "collection_id": evidence.report["collection_id"],
                "system_count": evidence.report["expected_system_count"],
                "successful_system_count": evidence.report["successful_run_count"],
                "collection_report_sha256": evidence.report["report_sha256"],
                "raw_output_count": sum(item[0].endswith("raw-output") for item in evidence.files),
                "resource_record_count": sum(
                    item[0].endswith("resource-record") for item in evidence.files
                ),
                "artifacts": evidence_artifacts,
            },
            "signature_tools": signature_tools,
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
        manifest = {
            **body,
            "manifest_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
        }
        manifest_path = staging / "manifest.json"
        payload = (
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode()
        descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        verify_evaluator_kit(staging)
        os.replace(staging, selected_destination)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _manifest_artifacts(value: Any) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            if set(item) == {"logical_name", "blob_path", "sha256", "byte_size", "media_type"}:
                artifacts.append(item)
            else:
                for nested in item.values():
                    visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return artifacts


def _kit_json(path: Path, *, field: str, schema_name: str | None = None) -> dict[str, Any]:
    value = _read_json(path, field=field)
    if schema_name is not None:
        _validate_schema(value, name=schema_name, field=field)
    return value


def _verify_source_bound_files(
    *,
    source_files: dict[str, dict[str, Any]],
    declared: list[dict[str, Any]],
    field: str,
) -> None:
    if not isinstance(declared, list):
        raise CandidateError(f"{field} inventory is invalid")
    observed_paths: set[str] = set()
    for binding in declared:
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256", "byte_size"}:
            raise CandidateError(f"{field} file binding is invalid")
        path = binding.get("path")
        if not isinstance(path, str) or path in observed_paths or source_files.get(path) != binding:
            raise CandidateError(f"{field} differs from the source archive")
        observed_paths.add(path)


def _semantic_kit_verification(
    *,
    manifest: dict[str, Any],
    artifact_paths: dict[str, Path],
) -> None:
    candidate_record = manifest["candidate"]
    candidate = GitCandidate(
        commit=candidate_record["commit"],
        tree=candidate_record["git_tree"],
        package_version=candidate_record["package_version"],
    )
    source_path = artifact_paths[candidate_record["source_archive"]["logical_name"]]
    source_report, source_files = _source_archive_inventory(source_path)
    if any(candidate_record.get(field) != value for field, value in source_report.items()):
        raise CandidateError("source archive inventory differs from the kit manifest")

    contract_files = [
        item
        for path, item in sorted(source_files.items())
        if path.startswith("contracts/") and path.endswith(".json")
    ]
    contracts = manifest["contracts"]
    if (
        contracts["count"] != len(contract_files)
        or contracts["files"] != contract_files
        or contracts["inventory_sha256"]
        != sha256_bytes(canonical_json(contract_files).encode("utf-8"))
    ):
        raise CandidateError("contract inventory differs from the source archive")
    _verify_source_bound_files(
        source_files=source_files,
        declared=manifest["signature_tools"],
        field="signature tools",
    )
    lock_artifact = manifest["release_artifacts"]["lock"]
    if source_files.get("uv.lock", {}).get("sha256") != lock_artifact["sha256"]:
        raise CandidateError("portable lock bytes differ from the source archive")

    profile_binding = manifest["retrieval_profiles"]["artifact"]
    profile = _kit_json(
        artifact_paths[profile_binding["logical_name"]],
        field="retrieval profile commitment",
        schema_name="evaluator-retrieval-profile-commitment.v1.schema.json",
    )
    _record_sha256(
        profile,
        field="retrieval profile commitment",
        digest_field="record_sha256",
    )
    if (
        profile["candidate_commit"] != candidate.commit
        or profile["record_sha256"] != manifest["retrieval_profiles"]["record_sha256"]
    ):
        raise CandidateError("retrieval profile commitment targets a different candidate")
    _verify_source_bound_files(
        source_files=source_files,
        declared=profile["implementation_files"],
        field="retrieval profile implementation",
    )

    commitments = manifest["benchmark_commitments"]
    commitment_artifacts = commitments["artifacts"]
    registry_path = artifact_paths[commitment_artifacts["baseline_registry"]["logical_name"]]
    registry = load_registry(registry_path)
    registry_digest = registry_sha256(registry)
    if (
        registry["candidate_line"] != FROZEN_EVALUATION_CANDIDATE_LINE
        or registry_digest != commitments["registry_sha256"]
    ):
        raise CandidateError("portable baseline registry is not the frozen commitment")
    for system in registry["systems"]:
        if system["system_id"].startswith("deeplaw/") and (
            system["implementation"]["revision"] != candidate.commit
        ):
            raise CandidateError("portable baseline registry targets another candidate")

    corpus_path = artifact_paths[commitment_artifacts["corpus"]["logical_name"]]
    corpus = _kit_json(
        corpus_path,
        field="benchmark corpus commitment",
        schema_name="benchmark-corpus-commitment.v1.schema.json",
    )
    corpus_digest = _record_sha256(
        corpus,
        field="benchmark corpus commitment",
        digest_field="record_sha256",
    )
    if corpus_digest != commitments["corpus_record_sha256"]:
        raise CandidateError("portable corpus commitment digest differs")
    created_timestamp = _timestamp(manifest["created_at"], field="kit created_at")
    if _timestamp(corpus["committed_at"], field="corpus committed_at") >= created_timestamp:
        raise CandidateError("portable corpus commitment does not predate the kit")

    evidence_by_name = {
        item["logical_name"]: item for item in manifest["baseline_evidence"]["artifacts"]
    }
    try:
        collection_binding = evidence_by_name["evidence/collection-manifest"]
        report_binding = evidence_by_name["evidence/collection-report"]
    except KeyError as error:
        raise CandidateError("portable evidence lacks collection records") from error
    _kit_json(
        artifact_paths[collection_binding["logical_name"]],
        field="evidence collection manifest",
        schema_name="baseline-evidence-collection.v1.schema.json",
    )
    collection_report = _kit_json(
        artifact_paths[report_binding["logical_name"]],
        field="evidence collection report",
        schema_name="baseline-evidence-collection-report.v1.schema.json",
    )
    report_digest = _record_sha256(
        collection_report,
        field="evidence collection report",
        digest_field="report_sha256",
    )
    baseline_evidence = manifest["baseline_evidence"]
    if (
        report_digest != baseline_evidence["collection_report_sha256"]
        or collection_report["collection_complete"] is not True
        or collection_report["successful_run_count"]
        != collection_report["expected_system_count"]
        or collection_report["expected_system_count"] != baseline_evidence["system_count"]
        or collection_report["successful_run_count"]
        != baseline_evidence["successful_system_count"]
    ):
        raise CandidateError("portable collection report is not a complete successful run")

    gate_path = artifact_paths[commitment_artifacts["internal_gate"]["logical_name"]]
    gate = _kit_json(
        gate_path,
        field="internal baseline gate",
        schema_name="internal-baseline-gate.v1.schema.json",
    )
    validate_internal_gate(
        gate,
        candidate_commit=candidate.commit,
        registry_digest=registry_digest,
        collection_report_digest=report_digest,
    )
    if _timestamp(gate["gate_completed_at"], field="gate_completed_at") > created_timestamp:
        raise CandidateError("portable internal gate postdates the evaluator kit")
    if gate["record_sha256"] != commitments["internal_gate_record_sha256"]:
        raise CandidateError("portable internal baseline gate digest differs")
    for name, field in (
        ("statistical_protocol", "statistical_protocol_sha256"),
        ("case_results_manifest", "case_results_manifest_sha256"),
        ("comparisons_manifest", "comparisons_manifest_sha256"),
    ):
        if commitment_artifacts[name]["sha256"] != gate[field]:
            raise CandidateError(f"portable {name} differs from the internal baseline gate")

    expected_models = registry["shared_models"]
    model_digests: dict[str, str] = {}
    for model_entry in manifest["models"]:
        alias = model_entry["alias"]
        if alias in model_digests or alias not in expected_models:
            raise CandidateError("portable model inventory is invalid or duplicated")
        model = _kit_json(
            artifact_paths[model_entry["artifact"]["logical_name"]],
            field=f"model manifest {alias}",
            schema_name="evaluator-model-manifest.v1.schema.json",
        )
        validate_model_manifest(
            model,
            expected_alias=alias,
            expected_model=expected_models[alias],
        )
        if any(
            model_entry[field] != model[field]
            for field in ("model_id", "revision", "manifest_sha256")
        ):
            raise CandidateError(f"portable model summary differs for {alias}")
        model_digests[alias] = model["manifest_sha256"]
    if set(model_digests) != set(expected_models):
        raise CandidateError("portable model inventory does not cover the frozen registry")
    for logical_name, path in artifact_paths.items():
        if logical_name.startswith("evidence/") and logical_name.endswith("/environment"):
            environment = _kit_json(
                path,
                field=f"{logical_name} record",
                schema_name="baseline-evaluation-environment.v1.schema.json",
            )
            for model in [*environment["models"], environment["reader"]]:
                if model_digests.get(model["alias"]) != model["artifact_manifest_sha256"]:
                    raise CandidateError("portable environment model binding differs")

    raw_count = sum(name.endswith("/raw-output") for name in evidence_by_name)
    resource_count = sum(name.endswith("/resource-record") for name in evidence_by_name)
    if (
        raw_count != baseline_evidence["raw_output_count"]
        or resource_count != baseline_evidence["resource_record_count"]
        or raw_count != baseline_evidence["system_count"]
        or resource_count != baseline_evidence["system_count"]
    ):
        raise CandidateError("portable raw-output or resource-record inventory is incomplete")

    release = manifest["release_artifacts"]
    _release_report(
        artifact_paths[release["reproducible_build"]["logical_name"]],
        candidate=candidate,
        wheel=artifact_paths[release["wheel"]["logical_name"]],
        sdist=artifact_paths[release["sdist"]["logical_name"]],
        lock_sha256=release["lock"]["sha256"],
        wheel_name=PurePosixPath(release["wheel"]["logical_name"]).name,
        sdist_name=PurePosixPath(release["sdist"]["logical_name"]).name,
    )
    _sbom(
        artifact_paths[release["sbom"]["logical_name"]],
        package_version=candidate.package_version,
    )
    _license_inventory(artifact_paths[release["license_inventory"]["logical_name"]])
    container_report = validate_oci_archive(
        artifact_paths[release["container"]["logical_name"]],
        candidate_commit=candidate.commit,
        package_version=candidate.package_version,
        wheel_sha256=release["wheel"]["sha256"],
        sdist_sha256=release["sdist"]["sha256"],
        lock_sha256=release["lock"]["sha256"],
    )
    if canonical_json(container_report) != canonical_json(manifest["container"]):
        raise CandidateError("portable container summary differs from the OCI archive")


def verify_evaluator_kit(root: Path) -> dict[str, Any]:
    selected = root.expanduser().absolute()
    if selected.is_symlink() or not selected.is_dir():
        raise CandidateError("evaluator kit root must be a regular directory")
    manifest_path = selected / "manifest.json"
    manifest = _read_json(manifest_path, field="evaluator kit manifest")
    if manifest.get("schema_version") != KIT_SCHEMA:
        raise CandidateError("evaluator kit schema is invalid")
    _validate_schema(
        manifest,
        name="external-evaluator-kit.v1.schema.json",
        field="evaluator kit manifest",
    )
    _record_sha256(manifest, field="evaluator kit manifest", digest_field="manifest_sha256")
    if (
        manifest.get("kit_integrity_complete") is not True
        or manifest.get("external_verification", {}).get("claim_eligible") is not False
    ):
        raise CandidateError("evaluator kit overstates or understates its closed state")
    artifacts = _manifest_artifacts(manifest)
    logical_names: set[str] = set()
    referenced_blobs: set[str] = set()
    artifact_paths: dict[str, Path] = {}
    for artifact in artifacts:
        logical_name = _safe_relative(artifact.get("logical_name"), field="logical_name")
        blob_path = _safe_relative(artifact.get("blob_path"), field="blob_path")
        digest = artifact.get("sha256")
        size = artifact.get("byte_size")
        if (
            logical_name in logical_names
            or not _SHA256.fullmatch(str(digest))
            or blob_path != f"blobs/sha256/{digest[:2]}/{digest}"
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise CandidateError("evaluator kit artifact binding is invalid")
        logical_names.add(logical_name)
        referenced_blobs.add(blob_path)
        root_resolved = selected.resolve()
        unresolved = root_resolved / blob_path
        try:
            resolved = unresolved.resolve(strict=True)
            resolved.relative_to(root_resolved)
        except (OSError, ValueError) as error:
            raise CandidateError("evaluator kit blob escapes its root") from error
        if (
            resolved != unresolved
            or unresolved.is_symlink()
            or not resolved.is_file()
            or resolved.stat().st_size != size
            or sha256_file(resolved) != digest
        ):
            raise CandidateError(f"evaluator kit blob differs: {logical_name}")
        artifact_paths[logical_name] = resolved
    actual_blobs = {
        path.relative_to(selected).as_posix()
        for path in (selected / "blobs").rglob("*")
        if path.is_file()
    }
    if actual_blobs != referenced_blobs:
        raise CandidateError("evaluator kit contains missing or unreferenced blobs")
    _semantic_kit_verification(
        manifest=manifest,
        artifact_paths=artifact_paths,
    )
    return {
        "schema_version": "deeplaw.external-evaluator-kit-verification/v1",
        "kit_id": manifest.get("kit_id"),
        "manifest_file_sha256": sha256_file(manifest_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "artifact_reference_count": len(artifacts),
        "unique_blob_count": len(referenced_blobs),
        "integrity_verified": True,
        "claim_eligible": False,
    }


def verify_attestation(
    *,
    kit_root: Path,
    attestation_path: Path,
    trusted_public_key_hex: str,
) -> dict[str, Any]:
    verification = verify_evaluator_kit(kit_root)
    manifest_path = kit_root.expanduser().absolute() / "manifest.json"
    payload = manifest_path.read_bytes()
    attestation = _closed(
        _read_json(attestation_path, field="evaluator kit attestation"),
        field="evaluator kit attestation",
        keys={
            "schema_version",
            "organization",
            "kit_manifest_file_sha256",
            "kit_manifest_sha256",
            "issued_at",
            "signature_payload",
            "public_key_base64",
            "signature_base64",
        },
    )
    _validate_schema(
        attestation,
        name="external-evaluator-kit-attestation.v1.schema.json",
        field="evaluator kit attestation",
    )
    if (
        attestation.get("schema_version") != ATTESTATION_SCHEMA
        or attestation.get("signature_payload") != "exact-manifest-bytes"
        or attestation.get("kit_manifest_file_sha256") != sha256_bytes(payload)
        or attestation.get("kit_manifest_sha256") != verification["manifest_sha256"]
    ):
        raise CandidateError("evaluator kit attestation targets different bytes")
    organization = _bounded_string(
        attestation.get("organization"), field="attestation organization", maximum=200
    )
    _timestamp(attestation.get("issued_at"), field="attestation issued_at")
    try:
        trusted = bytes.fromhex(trusted_public_key_hex)
        public_key = base64.b64decode(attestation["public_key_base64"], validate=True)
        signature = base64.b64decode(attestation["signature_base64"], validate=True)
    except (ValueError, binascii.Error) as error:
        raise CandidateError(
            "evaluator attestation key or signature encoding is invalid"
        ) from error
    if len(trusted) != 32 or public_key != trusted or len(signature) != 64:
        raise CandidateError("evaluator attestation does not use the trusted public key")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (InvalidSignature, ValueError) as error:
        raise CandidateError("evaluator kit signature is invalid") from error
    return {
        "schema_version": "deeplaw.external-evaluator-kit-attestation-verification/v1",
        "organization": organization,
        "kit_manifest_file_sha256": verification["manifest_file_sha256"],
        "signature_valid": True,
        "organization_identity_independently_verified": False,
        "claim_eligible": False,
    }


def _model_argument(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise CandidateError("--model-manifest must use ALIAS=/absolute/path form")
        alias, path_value = value.split("=", maxsplit=1)
        if not _ALIAS.fullmatch(alias) or alias in result:
            raise CandidateError("--model-manifest alias is invalid or duplicated")
        path = Path(path_value)
        if not path.is_absolute():
            raise CandidateError("--model-manifest path must be absolute")
        result[alias] = path
    return result


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    selected = path.expanduser().absolute()
    if selected.exists() or selected.is_symlink():
        raise FileExistsError("output path must be new")
    if selected.parent.is_symlink() or not selected.parent.is_dir():
        raise CandidateError("output parent must be an existing safe directory")
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(selected, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _freeze_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("freeze", help="Freeze a complete external evaluator kit")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--collection-report", type=Path, required=True)
    parser.add_argument("--corpus-commitment", type=Path, required=True)
    parser.add_argument("--internal-gate", type=Path, required=True)
    parser.add_argument("--statistical-protocol", type=Path, required=True)
    parser.add_argument("--case-results-manifest", type=Path, required=True)
    parser.add_argument("--comparisons-manifest", type=Path, required=True)
    parser.add_argument("--model-manifest", action="append", default=[])
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--container", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--license-inventory", type=Path, required=True)
    parser.add_argument("--reproducible-build", type=Path, required=True)
    parser.add_argument("--created-at", required=True)


def _preparation_parsers(subparsers: Any) -> None:
    model = subparsers.add_parser(
        "model-manifest",
        help="Hash a complete local model directory against a registered alias",
    )
    model.add_argument("--registry", type=Path, required=True)
    model.add_argument("--alias", required=True)
    model.add_argument("--model-root", type=Path, required=True)
    model.add_argument("--source", required=True)
    model.add_argument("--license", dest="license_name", required=True)
    model.add_argument("--output", type=Path, required=True)
    corpus = subparsers.add_parser(
        "corpus-commitment",
        help="Commit exact corpus and query bytes before candidate delivery",
    )
    corpus.add_argument("--corpus", type=Path, required=True)
    corpus.add_argument("--queries", type=Path, required=True)
    corpus.add_argument("--commitment-id", required=True)
    corpus.add_argument("--evaluator-organization", required=True)
    corpus.add_argument(
        "--labels-access",
        choices=("public_frozen", "external_evaluator_only"),
        required=True,
    )
    corpus.add_argument("--committed-at", required=True)
    corpus.add_argument("--output", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Freeze or verify the claim-ineligible DeepLaw external evaluator kit."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _freeze_parser(subparsers)
    _preparation_parsers(subparsers)
    verify_parser = subparsers.add_parser("verify", help="Verify a portable evaluator kit")
    verify_parser.add_argument("--kit", type=Path, required=True)
    attestation_parser = subparsers.add_parser(
        "verify-attestation", help="Verify an evaluator signature with a trusted key"
    )
    attestation_parser.add_argument("--kit", type=Path, required=True)
    attestation_parser.add_argument("--attestation", type=Path, required=True)
    attestation_parser.add_argument("--trusted-public-key-hex", required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            result = verify_evaluator_kit(args.kit)
        elif args.command == "verify-attestation":
            result = verify_attestation(
                kit_root=args.kit,
                attestation_path=args.attestation,
                trusted_public_key_hex=args.trusted_public_key_hex,
            )
        elif args.command == "model-manifest":
            result = build_model_manifest(
                registry_path=args.registry,
                alias=args.alias,
                model_root=args.model_root,
                source=args.source,
                license_name=args.license_name,
            )
            _write_json_exclusive(args.output, result)
        elif args.command == "corpus-commitment":
            result = build_corpus_commitment(
                corpus_path=args.corpus,
                queries_path=args.queries,
                commitment_id=args.commitment_id,
                evaluator_organization=args.evaluator_organization,
                labels_access=args.labels_access,
                committed_at=args.committed_at,
            )
            _write_json_exclusive(args.output, result)
        else:
            result = freeze_evaluator_kit(
                repository=args.repository,
                candidate_commit=args.candidate_commit,
                destination=args.destination,
                registry_path=args.registry,
                collection_path=args.collection,
                collection_report_path=args.collection_report,
                corpus_commitment_path=args.corpus_commitment,
                internal_gate_path=args.internal_gate,
                statistical_protocol_path=args.statistical_protocol,
                case_results_manifest_path=args.case_results_manifest,
                comparisons_manifest_path=args.comparisons_manifest,
                model_manifest_paths=_model_argument(args.model_manifest),
                wheel_path=args.wheel,
                sdist_path=args.sdist,
                container_path=args.container,
                sbom_path=args.sbom,
                license_inventory_path=args.license_inventory,
                reproducible_build_path=args.reproducible_build,
                created_at=args.created_at,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (CandidateError, FileExistsError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
