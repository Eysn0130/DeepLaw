"""Prepare the exact, machine-only v0.13 construction candidate.

This command is deliberately smaller than the v2 freezer.  It turns the
tracked 0.12 source-candidate surfaces into a 0.13 construction template only
after the current Git identity and the Gate v9/qualification v3 contracts have
been checked.  It does not build, qualify, sign, tag, publish, or freeze an
artifact.  Dry-run is the default; ``--apply`` is the only write mode.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPOSITORY = Path(__file__).resolve().parents[2]
OLD_VERSION = "0.12.0"
CANDIDATE_VERSION = "0.13.0"
ACTIVE_RELATIVE = "benchmarks/v013/active-qualification-v3.json"
ACTIVE_SCHEMA_RELATIVE = "contracts/v013-active-qualification.v3.schema.json"
PROTOCOL_RELATIVE = "benchmarks/v013/qualification-protocol-v3.json"
PROTOCOL_HASH_RELATIVE = "benchmarks/v013/qualification-protocol-v3.sha256"
PROTOCOL_SCHEMA_RELATIVE = "contracts/v013-qualification-protocol.v3.schema.json"
CLASSIFICATION_RELATIVE = "benchmarks/release/v013-gate-classification-v9.json"
CLASSIFICATION_SCHEMA_RELATIVE = "contracts/v013-release-gate-classification.v9.schema.json"
ACTIVE_SCHEMA_VERSION = "deeplaw.v013-active-qualification/v3"
PROTOCOL_SCHEMA_VERSION = "deeplaw.v013-qualification-protocol/v3"
CLASSIFICATION_SCHEMA_VERSION = "deeplaw.v013-release-gate-classification/v9"
CONSTRUCTION_STATUS = "construction_candidate_machine_evaluation_pending"
PROFILE = "kernel_release_core"
GATE_CLASSIFICATION = "v9"
INTEGRATED_MAIN_REF = "refs/remotes/origin/main"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

# This is an explicit inventory, not a glob.  In particular, historical
# evidence, v1 files, and version strings in tests are intentionally absent.
CURRENT_SURFACE_FILES = (
    "pyproject.toml",
    "uv.lock",
    "src/deeplaw/__init__.py",
    ".agents/plugins/marketplace.json",
    ".claude-plugin/marketplace.json",
    "plugins/deeplaw/.codex-plugin/plugin.json",
    "plugins/deeplaw/.claude-plugin/plugin.json",
    "plugins/deeplaw-knowledge-os/.codex-plugin/plugin.json",
    "plugins/deeplaw-knowledge-os/.claude-plugin/plugin.json",
    "adapters/opencode/manifest.json",
    "adapters/obsidian/manifest.json",
    "adapters/obsidian/plugin/manifest.json",
    "adapters/obsidian/plugin/package.json",
    "adapters/obsidian/plugin/package-lock.json",
    "governance/product-surface-manifest.v1.json",
    "contracts/product-surface-manifest.v1.schema.json",
    "contracts/authoritative-source-quality-decision-matrix.v2.schema.json",
    "contracts/semantic-machine-review-packet.v1.schema.json",
    "contracts/semantic-machine-review-consensus.v1.schema.json",
    "contracts/semantic-owner-review-packet.v1.schema.json",
    "benchmarks/semantic/build_machine_review_consensus.py",
    "security/openvex.json",
    ACTIVE_RELATIVE,
)

CONTRACT_FILES = (
    ACTIVE_SCHEMA_RELATIVE,
    PROTOCOL_RELATIVE,
    PROTOCOL_HASH_RELATIVE,
    PROTOCOL_SCHEMA_RELATIVE,
    CLASSIFICATION_RELATIVE,
    CLASSIFICATION_SCHEMA_RELATIVE,
)

VERSION_SURFACE_FILES = (
    "pyproject.toml",
    "uv.lock",
    "src/deeplaw/__init__.py",
    ".claude-plugin/marketplace.json",
    "plugins/deeplaw/.codex-plugin/plugin.json",
    "plugins/deeplaw/.claude-plugin/plugin.json",
    "plugins/deeplaw-knowledge-os/.codex-plugin/plugin.json",
    "plugins/deeplaw-knowledge-os/.claude-plugin/plugin.json",
    "adapters/opencode/manifest.json",
    "adapters/obsidian/plugin/manifest.json",
    "adapters/obsidian/plugin/package.json",
    "adapters/obsidian/plugin/package-lock.json",
    "governance/product-surface-manifest.v1.json",
    "contracts/product-surface-manifest.v1.schema.json",
    "contracts/authoritative-source-quality-decision-matrix.v2.schema.json",
    "contracts/semantic-machine-review-packet.v1.schema.json",
    "contracts/semantic-machine-review-consensus.v1.schema.json",
    "contracts/semantic-owner-review-packet.v1.schema.json",
    "benchmarks/semantic/build_machine_review_consensus.py",
    "security/openvex.json",
)


class CandidatePrepError(ValueError):
    """Raised when candidate preparation cannot close its exact boundary."""


PreparationError = CandidatePrepError


def _fail(message: str) -> None:
    raise CandidatePrepError(message)


def _run_git(repository: Path, arguments: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CandidatePrepError("Git identity verification failed") from error
    if result.returncode != 0:
        raise CandidatePrepError("Git identity verification failed")
    return result.stdout.strip()


def _has_symlink_component(path: Path) -> bool:
    selected = Path(path).expanduser()
    if selected.is_absolute():
        current = Path(selected.anchor)
        parts = selected.parts[1:]
    else:
        current = Path.cwd()
        parts = selected.parts
    for part in parts:
        current /= part
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


def _regular_repo_file(repository: Path, relative: str) -> tuple[Path, bytes]:
    if relative not in CURRENT_SURFACE_FILES and relative not in CONTRACT_FILES:
        _fail("candidate-prep attempted an unlisted tracked target")
    selected = repository / relative
    if _has_symlink_component(selected) or selected.is_symlink():
        _fail(f"tracked target is not a regular file: {relative}")
    try:
        resolved = selected.resolve(strict=True)
        if repository not in resolved.parents:
            _fail(f"tracked target escapes the repository: {relative}")
        file_stat = resolved.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            _fail(f"tracked target is not a regular file: {relative}")
        raw = resolved.read_bytes()
    except CandidatePrepError:
        raise
    except OSError as error:
        raise CandidatePrepError(f"tracked target is unavailable: {relative}") from error
    return resolved, raw


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                _fail(f"{label} contains a duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=pairs)
    except CandidatePrepError:
        raise
    except (UnicodeError, TypeError, ValueError) as error:
        raise CandidatePrepError(f"{label} is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        _fail(f"{label} must be a JSON object")
    return value


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _assert_git_identity(
    repository: Path,
    integration_commit: str,
    frozen_main_commit: str,
    *,
    apply: bool,
) -> tuple[str, str, str, str]:
    if not isinstance(integration_commit, str) or not GIT_OBJECT.fullmatch(integration_commit):
        _fail("--integration-commit must be an exact Git commit")
    if not isinstance(frozen_main_commit, str) or not GIT_OBJECT.fullmatch(frozen_main_commit):
        _fail("--frozen-main-commit must be an exact Git commit")
    try:
        repository = repository.expanduser().resolve(strict=True)
    except OSError as error:
        raise CandidatePrepError("repository is unavailable") from error
    shown_root = _run_git(repository, ["rev-parse", "--show-toplevel"])
    try:
        if Path(shown_root).resolve(strict=True) != repository:
            _fail("repository root does not match Git")
    except OSError as error:
        raise CandidatePrepError("repository root does not match Git") from error

    status = _run_git(repository, ["status", "--porcelain=v1", "--untracked-files=all"])
    if status:
        _fail("repository must be clean before candidate preparation")

    head = _run_git(repository, ["rev-parse", "HEAD"])
    if head != integration_commit:
        _fail("HEAD does not equal --integration-commit")
    verified = _run_git(repository, ["rev-parse", f"{integration_commit}^{{commit}}"])
    if verified != integration_commit:
        _fail("--integration-commit is not the exact current commit")
    try:
        frozen_main = _run_git(
            repository,
            [
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{frozen_main_commit}^{{commit}}",
            ],
        )
    except CandidatePrepError as error:
        raise CandidatePrepError("--frozen-main-commit cannot be resolved") from error
    if not GIT_OBJECT.fullmatch(frozen_main):
        _fail("--frozen-main-commit cannot be resolved")
    if frozen_main != frozen_main_commit:
        _fail("--frozen-main-commit is not the exact current commit")
    try:
        integrated_main = _run_git(
            repository,
            [
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{INTEGRATED_MAIN_REF}^{{commit}}",
            ],
        )
    except CandidatePrepError as error:
        raise CandidatePrepError("remote-tracking origin/main cannot be resolved") from error
    if not GIT_OBJECT.fullmatch(integrated_main):
        _fail("remote-tracking origin/main cannot be resolved")
    if integrated_main != integration_commit:
        _fail("remote-tracking origin/main does not equal --integration-commit")
    if frozen_main == integration_commit:
        _fail("--frozen-main-commit must differ from --integration-commit")
    try:
        parent_record = _run_git(
            repository,
            [
                "rev-list",
                "--parents",
                "-n",
                "1",
                "--end-of-options",
                integration_commit,
            ],
        )
    except CandidatePrepError as error:
        raise CandidatePrepError("integration commit parents cannot be resolved") from error
    parent_tokens = parent_record.split()
    if not parent_tokens or parent_tokens[0] != integration_commit:
        _fail("integration commit identity cannot be resolved")
    if len(parent_tokens) < 3:
        _fail("integration commit must be a merge with a first parent")
    first_parent = parent_tokens[1]
    if not GIT_OBJECT.fullmatch(first_parent):
        _fail("integration commit first parent cannot be resolved")
    if first_parent != frozen_main:
        _fail("integration commit first parent differs from --frozen-main-commit")
    tree = _run_git(repository, ["rev-parse", "HEAD^{tree}"])
    if not GIT_OBJECT.fullmatch(tree):
        _fail("HEAD tree cannot be resolved")
    branch = _run_git(repository, ["branch", "--show-current"])
    if apply:
        if not branch or branch.casefold() in {"main", "master"}:
            _fail("apply is forbidden on the main branch")
        if not re.search(r"(?:candidate|release)", branch.casefold()):
            _fail("apply requires a candidate/release branch")
    return head, tree, branch, frozen_main


def _expect_once(raw: bytes, old: bytes, new: bytes, *, label: str) -> bytes:
    count = raw.count(old)
    if count != 1:
        _fail(f"{label} has an unexpected current version surface")
    return raw.replace(old, new, 1)


def _expect_regex(raw: bytes, pattern: bytes, replacement: bytes, *, label: str) -> bytes:
    result, count = re.subn(pattern, replacement, raw, count=1, flags=re.MULTILINE)
    if count != 1:
        _fail(f"{label} has an unexpected current version surface")
    return result


OPTIONAL_EXTERNAL_HASH_NAMES = (
    "semantic_machine_proposal_sha256",
    "qualification_holdout_sha256",
    "final_blind_holdout_sha256",
    "agent_review_panel_sha256",
    "scorer_a_sha256",
    "scorer_b_sha256",
    "arbiter_sha256",
)


def _validate_contract(
    value: Mapping[str, Any], schema: Mapping[str, Any], *, label: str
) -> None:
    try:
        errors = sorted(
            Draft202012Validator(schema).iter_errors(value),
            key=lambda error: list(error.path),
        )
    except Exception as error:
        raise CandidatePrepError(f"{label} schema validation failed") from error
    if errors:
        location = ".".join(str(item) for item in errors[0].path) or "$"
        _fail(f"{label} schema validation failed at {location}")


def _load_contract(
    repository: Path, relative: str, *, label: str
) -> tuple[dict[str, Any], bytes]:
    _resolved, raw = _regular_repo_file(repository, relative)
    value = _strict_json(raw, label=label)
    return value, raw


def _check_active_template(active: Mapping[str, Any], lock_raw: bytes) -> None:
    if active.get("schema_version") != ACTIVE_SCHEMA_VERSION:
        _fail("active qualification schema is not v3")
    if active.get("profile") != PROFILE or active.get("status") != "machine_evaluation_pending":
        _fail("active qualification is not the clean 0.12 machine-pending template")
    if active.get("candidate_version") != OLD_VERSION:
        _fail("active qualification is not the 0.12 template")
    if active.get("construction_package_version") != OLD_VERSION:
        _fail("active qualification construction package is not 0.12")
    if active.get("release_target") != CANDIDATE_VERSION:
        _fail("active qualification release target is not 0.13")
    binding = active.get("candidate_binding")
    if not isinstance(binding, dict) or binding.get("package_version") != OLD_VERSION:
        _fail("active qualification candidate binding is not 0.12")
    for field in (
        "source_commit",
        "source_tree",
        "wheel_filename",
        "wheel_sha256",
        "sdist_filename",
        "sdist_sha256",
        "artifact_manifest_sha256",
    ):
        if binding.get(field) is not None:
            _fail("active qualification already contains a candidate artifact identity")
    if binding.get("lock_sha256") != _sha256(lock_raw):
        _fail("active qualification lock digest is stale")
    external = active.get("external_inputs")
    if not isinstance(external, dict):
        _fail("active qualification external input inventory is invalid")
    if set(external) != {
        *OPTIONAL_EXTERNAL_HASH_NAMES,
        "status",
        "null_is_non_blocking",
        "required_for_candidate_binding",
    }:
        _fail("active qualification external input inventory is not v3")
    if any(external[name] is not None for name in OPTIONAL_EXTERNAL_HASH_NAMES):
        _fail("active qualification already contains external input hashes")
    if external.get("status") != "not_executed" or external.get("null_is_non_blocking") is not True:
        _fail("active qualification optional inputs are not explicitly non-blocking")
    if external.get("required_for_candidate_binding") is not False:
        _fail("active qualification optional inputs are candidate prerequisites")
    if active.get("blocker") != "machine_evaluation_not_executed":
        _fail("active qualification blocker is not machine-evaluation pending")
    for section in ("core_statuses", "capability_claims", "competitive_claims"):
        rows = active.get(section)
        if not isinstance(rows, list) or any(
            row.get("status") != "not_executed"
            or row.get("passed") is not False
            or row.get("claim") is not False
            for row in rows
            if isinstance(row, Mapping)
        ):
            _fail(f"active qualification {section} is not not-executed")
    for field in (
        "release_ready",
        "claim_eligible",
        "kernel_release_claim_eligible",
        "competitive_claim_eligible",
    ):
        if active.get(field) is not False:
            _fail("active qualification contains a release or claim assertion")


def _check_contracts(
    repository: Path, active: Mapping[str, Any], lock_raw: bytes
) -> None:
    active_schema, _ = _load_contract(
        repository, ACTIVE_SCHEMA_RELATIVE, label="active qualification v3 schema"
    )
    protocol, protocol_raw = _load_contract(
        repository, PROTOCOL_RELATIVE, label="qualification protocol v3"
    )
    _resolved, protocol_hash_raw = _regular_repo_file(repository, PROTOCOL_HASH_RELATIVE)
    protocol_schema, _ = _load_contract(
        repository, PROTOCOL_SCHEMA_RELATIVE, label="qualification protocol v3 schema"
    )
    classification, classification_raw = _load_contract(
        repository, CLASSIFICATION_RELATIVE, label="Gate v9 classification"
    )
    classification_schema, _ = _load_contract(
        repository, CLASSIFICATION_SCHEMA_RELATIVE, label="Gate v9 classification schema"
    )
    for schema, label in (
        (active_schema, "active qualification v3 schema"),
        (protocol_schema, "qualification protocol v3 schema"),
        (classification_schema, "Gate v9 classification schema"),
    ):
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as error:
            raise CandidatePrepError(f"{label} is invalid") from error
    _validate_contract(active, active_schema, label="active qualification v3")
    _validate_contract(protocol, protocol_schema, label="qualification protocol v3")
    _validate_contract(classification, classification_schema, label="Gate v9 classification")
    classification_digest = _sha256(classification_raw)
    protocol_digest = _sha256(protocol_raw)
    try:
        sidecar = protocol_hash_raw.decode("ascii").split()
    except UnicodeError as error:
        raise CandidatePrepError("qualification protocol hash sidecar is not ASCII") from error
    if sidecar != [protocol_digest, "qualification-protocol-v3.json"]:
        _fail("qualification protocol hash sidecar differs from exact bytes")
    active_classification = active.get("classification_binding")
    active_protocol = active.get("protocol_binding")
    if not isinstance(active_classification, Mapping) or not isinstance(active_protocol, Mapping):
        _fail("active qualification contract bindings are missing")
    if (
        active_classification.get("relative_path") != CLASSIFICATION_RELATIVE
        or active_classification.get("sha256") != classification_digest
    ):
        _fail("active qualification Gate v9 binding differs from exact bytes")
    if (
        active_protocol.get("relative_path") != PROTOCOL_RELATIVE
        or active_protocol.get("sha256") != protocol_digest
    ):
        _fail("active qualification protocol v3 binding differs from exact bytes")
    protocol_classification = protocol.get("classification_binding")
    if (
        not isinstance(protocol_classification, Mapping)
        or protocol_classification.get("sha256") != classification_digest
    ):
        _fail("qualification protocol Gate v9 binding differs from exact bytes")
    protocol_binding = protocol.get("candidate_binding")
    if (
        not isinstance(protocol_binding, Mapping)
        or protocol_binding.get("package_version") != OLD_VERSION
        or protocol_binding.get("lock_sha256") != _sha256(lock_raw)
    ):
        _fail("qualification protocol construction binding is not exact 0.12")


def _build_updates(
    repository: Path,
    original: Mapping[str, bytes],
) -> dict[str, bytes]:
    updates: dict[str, bytes] = {}
    updates["pyproject.toml"] = _expect_regex(
        original["pyproject.toml"],
        rb'^(version\s*=\s*")0\.12\.0("\s*)$',
        rb'\g<1>0.13.0\g<2>',
        label="pyproject.toml",
    )
    updates["uv.lock"] = _expect_regex(
        original["uv.lock"],
        rb'(^\[\[package\]\]\nname = "deeplaw"\nversion = ")0\.12\.0("$)',
        rb'\g<1>0.13.0\g<2>',
        label="uv.lock",
    )
    updates["src/deeplaw/__init__.py"] = _expect_once(
        original["src/deeplaw/__init__.py"],
        b'__version__ = "0.12.0"',
        b'__version__ = "0.13.0"',
        label="src/deeplaw/__init__.py",
    )
    for relative in VERSION_SURFACE_FILES[3:]:
        if relative == ".claude-plugin/marketplace.json":
            old = b'"version": "0.12.0"'
            if original[relative].count(old) != 3:
                _fail(f"{relative} has an unexpected current version surface")
            updates[relative] = original[relative].replace(old, b'"version": "0.13.0"')
            continue
        if relative == "security/openvex.json":
            old = b'"@id": "pkg:pypi/deeplaw@0.12.0"'
            count = original[relative].count(old)
            if count != 7:
                _fail(f"{relative} has an unexpected current version surface")
            updates[relative] = original[relative].replace(
                old, b'"@id": "pkg:pypi/deeplaw@0.13.0"'
            )
            continue
        if relative == "adapters/obsidian/plugin/package-lock.json":
            old = b'"version": "0.12.0"'
            if original[relative].count(old) != 2:
                _fail(f"{relative} has an unexpected current version surface")
            updates[relative] = original[relative].replace(old, b'"version": "0.13.0"')
            continue
        if relative == "contracts/product-surface-manifest.v1.schema.json":
            updates[relative] = _expect_once(
                original[relative],
                b'"package_version": {"const": "0.12.0"}',
                b'"package_version": {"const": "0.13.0"}',
                label=relative,
            )
            continue
        if relative == "contracts/authoritative-source-quality-decision-matrix.v2.schema.json":
            updates[relative] = _expect_once(
                original[relative],
                b'"release_target": {"const": "0.12.0"}',
                b'"release_target": {"const": "0.13.0"}',
                label=relative,
            )
            continue
        if relative in {
            "contracts/semantic-machine-review-packet.v1.schema.json",
            "contracts/semantic-machine-review-consensus.v1.schema.json",
            "contracts/semantic-owner-review-packet.v1.schema.json",
        }:
            updates[relative] = _expect_once(
                original[relative],
                b'"version": {"const": "0.12.0"}',
                b'"version": {"const": "0.13.0"}',
                label=relative,
            )
            continue
        if relative == "benchmarks/semantic/build_machine_review_consensus.py":
            updates[relative] = _expect_once(
                original[relative],
                b'CANDIDATE_VERSION = "0.12.0"',
                b'CANDIDATE_VERSION = "0.13.0"',
                label=relative,
            )
            continue
        old = (
            b'"package_version": "0.12.0"'
            if relative == "governance/product-surface-manifest.v1.json"
            else b'"version": "0.12.0"'
        )
        new = (
            b'"package_version": "0.13.0"'
            if relative == "governance/product-surface-manifest.v1.json"
            else b'"version": "0.13.0"'
        )
        updates[relative] = _expect_once(original[relative], old, new, label=relative)

    active = _strict_json(
        original[ACTIVE_RELATIVE], label="active qualification construction template"
    )
    _check_active_template(active, original["uv.lock"])
    _check_contracts(repository, active, original["uv.lock"])
    active["status"] = CONSTRUCTION_STATUS
    active["candidate_version"] = CANDIDATE_VERSION
    binding = active["candidate_binding"]
    binding["package_version"] = CANDIDATE_VERSION
    binding["lock_sha256"] = _sha256(updates["uv.lock"])
    active["release_ready"] = False
    active["claim_eligible"] = False
    active["kernel_release_claim_eligible"] = False
    active["competitive_claim_eligible"] = False
    active["blocker"] = "candidate_artifact_not_built"
    updates[ACTIVE_RELATIVE] = (
        json.dumps(active, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    return updates


def _atomic_apply(
    repository: Path, updates: Mapping[str, bytes], original: Mapping[str, bytes]
) -> None:
    staged: list[tuple[Path, Path]] = []
    replaced: list[Path] = []
    try:
        for relative, value in updates.items():
            target = repository / relative
            if target.read_bytes() != original[relative]:
                _fail(f"tracked target changed during candidate preparation: {relative}")
            file_stat = target.stat()
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".deeplaw-candidate-prep-", dir=target.parent
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(value)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, stat.S_IMODE(file_stat.st_mode))
            staged.append((temporary, target))
        for temporary, target in staged:
            os.replace(temporary, target)
            replaced.append(target)
        for _temporary, target in staged:
            if target.read_bytes() != updates[target.relative_to(repository).as_posix()]:
                _fail("candidate-prep write verification failed")
    except Exception as error:
        rollback_errors: list[OSError] = []
        for target in replaced:
            try:
                target.write_bytes(original[target.relative_to(repository).as_posix()])
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise CandidatePrepError(
                "candidate-prep write failed and rollback could not restore every target"
            ) from error
        raise CandidatePrepError("candidate-prep write failed and was rolled back") from error
    finally:
        for temporary, _target in staged:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)


def _run_lock_check(repository: Path) -> None:
    try:
        result = subprocess.run(
            ["uv", "lock", "--check"],
            cwd=repository,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CandidatePrepError("uv lock --check could not be executed") from error
    if result.returncode != 0:
        raise CandidatePrepError("uv lock --check failed; candidate-prep was rolled back")


def prepare_candidate(
    *,
    integration_commit: str,
    frozen_main_commit: str,
    repository: Path = REPOSITORY,
    apply: bool = False,
    run_lock_check: bool = True,
) -> dict[str, Any]:
    """Validate and optionally apply the exact construction-candidate transition."""

    try:
        repository = Path(repository).expanduser().resolve(strict=True)
    except OSError as error:
        raise CandidatePrepError("repository is unavailable") from error
    head, tree, branch, frozen_main = _assert_git_identity(
        repository,
        integration_commit,
        frozen_main_commit,
        apply=apply,
    )
    original: dict[str, bytes] = {}
    for relative in CURRENT_SURFACE_FILES:
        _resolved, original[relative] = _regular_repo_file(repository, relative)
    updates = _build_updates(repository, original)
    result: dict[str, Any] = {
        "schema_version": "deeplaw.v013-candidate-prep/v1",
        "mode": "apply" if apply else "dry-run",
        "base": {
            "commit": head,
            "integration_commit": head,
            "tree": tree,
            "branch": branch or None,
            "frozen_main_commit": frozen_main,
        },
        "candidate_identity": {
            "package_version": CANDIDATE_VERSION,
            "profile": PROFILE,
            "gate_classification": GATE_CLASSIFICATION,
            "status": CONSTRUCTION_STATUS,
        },
        "allowed_current_surfaces": list(CURRENT_SURFACE_FILES),
        "planned_targets": sorted(updates),
        "write_performed": False,
        "release_ready": False,
        "claim_eligible": False,
    }
    if apply:
        _atomic_apply(repository, updates, original)
        try:
            if run_lock_check:
                _run_lock_check(repository)
        except Exception:
            # Restore the exact preflight bytes if post-write validation fails.
            _atomic_apply(
                repository,
                {relative: original[relative] for relative in updates},
                updates,
            )
            raise
        result["write_performed"] = True
    return result


prepare_v013_candidate = prepare_candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    parser.add_argument("--integration-commit", required=True)
    parser.add_argument("--frozen-main-commit", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the construction transition (never allowed on main/master)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = prepare_candidate(
            repository=args.repository,
            integration_commit=args.integration_commit,
            frozen_main_commit=args.frozen_main_commit,
            apply=args.apply,
        )
    except (CandidatePrepError, OSError, ValueError) as error:
        print(f"candidate-prep failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


__all__ = [
    "ACTIVE_RELATIVE",
    "CANDIDATE_VERSION",
    "CONSTRUCTION_STATUS",
    "CURRENT_SURFACE_FILES",
    "GATE_CLASSIFICATION",
    "INTEGRATED_MAIN_REF",
    "CandidatePrepError",
    "PreparationError",
    "main",
    "prepare_candidate",
    "prepare_v013_candidate",
]


if __name__ == "__main__":
    raise SystemExit(main())
