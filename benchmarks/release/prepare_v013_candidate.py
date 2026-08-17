"""Prepare the exact, machine-only v0.13 construction candidate.

This command is deliberately smaller than the v2 freezer.  It turns the
tracked 0.12 source-candidate surfaces into a 0.13 construction template only
after the current Git identity and all owner-supplied external input digests
have been checked.  It does not build, qualify, sign, tag, publish, or freeze
an artifact.  Dry-run is the default; ``--apply`` is the only write mode.
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

REPOSITORY = Path(__file__).resolve().parents[2]
OLD_VERSION = "0.12.0"
CANDIDATE_VERSION = "0.13.0"
ACTIVE_RELATIVE = "benchmarks/v013/active-qualification-v2.json"
ACTIVE_SCHEMA_VERSION = "deeplaw.v013-active-qualification/v2"
CONSTRUCTION_STATUS = "construction_candidate_machine_evaluation_pending"
PROFILE = "machine_evaluated_no_human_attestation"
GATE_CLASSIFICATION = "v8"
MAX_EXTERNAL_INPUT_BYTES = 512 * 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")

EXTERNAL_INPUT_NAMES = (
    "semantic_machine_proposal_sha256",
    "qualification_holdout_sha256",
    "final_blind_holdout_sha256",
    "agent_review_panel_sha256",
    "runner_sha256",
    "scorer_a_sha256",
    "scorer_b_sha256",
    "arbitration_sha256",
    "isolation_sha256",
)

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
    "security/openvex.json",
    ACTIVE_RELATIVE,
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
    if relative not in CURRENT_SURFACE_FILES:
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
    repository: Path, integration_commit: str, *, apply: bool
) -> tuple[str, str, str]:
    if not isinstance(integration_commit, str) or not GIT_OBJECT.fullmatch(integration_commit):
        _fail("--integration-commit must be an exact Git commit")
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
    tree = _run_git(repository, ["rev-parse", "HEAD^{tree}"])
    if not GIT_OBJECT.fullmatch(tree):
        _fail("HEAD tree cannot be resolved")
    branch = _run_git(repository, ["branch", "--show-current"])
    if apply:
        if not branch or branch.casefold() in {"main", "master"}:
            _fail("apply is forbidden on the main branch")
        if not re.search(r"(?:candidate|release)", branch.casefold()):
            _fail("apply requires a candidate/release branch")
    return head, tree, branch


def _verify_external_input(
    repository: Path,
    name: str,
    path: Path,
    expected: str,
    used_paths: set[Path],
) -> str:
    if not SHA256.fullmatch(expected):
        _fail(f"external input {name} expected SHA-256 is invalid")
    selected = Path(path).expanduser()
    if _has_symlink_component(selected) or selected.is_symlink():
        _fail(f"external input {name} must not use a symlink")
    try:
        resolved = selected.resolve(strict=True)
        if repository in resolved.parents or resolved == repository:
            _fail(f"external input {name} must be outside the repository")
        file_stat = resolved.stat()
        if not stat.S_ISREG(file_stat.st_mode):
            _fail(f"external input {name} must be a regular file")
        if stat.S_IMODE(file_stat.st_mode) & 0o077 or not stat.S_IMODE(file_stat.st_mode) & 0o400:
            _fail(f"external input {name} is not owner-only")
        owner_uid = getattr(os, "getuid", lambda: None)()
        if owner_uid is not None and file_stat.st_uid != owner_uid:
            _fail(f"external input {name} is not owner-controlled")
        if file_stat.st_nlink != 1:
            _fail(f"external input {name} is multiply linked")
        if not 1 <= file_stat.st_size <= MAX_EXTERNAL_INPUT_BYTES:
            _fail(f"external input {name} exceeds its byte bound")
        if resolved in used_paths:
            _fail(f"external input {name} duplicates another input")
        used_paths.add(resolved)
        digest = hashlib.sha256()
        observed_size = 0
        with resolved.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                observed_size += len(chunk)
        after = resolved.stat()
    except CandidatePrepError:
        raise
    except OSError as error:
        raise CandidatePrepError(f"external input {name} is unavailable") from error
    if (
        observed_size != file_stat.st_size
        or after.st_size != file_stat.st_size
        or after.st_ino != file_stat.st_ino
        or after.st_uid != file_stat.st_uid
        or after.st_nlink != file_stat.st_nlink
        or stat.S_IMODE(after.st_mode) != stat.S_IMODE(file_stat.st_mode)
        or after.st_ctime_ns != file_stat.st_ctime_ns
        or after.st_mtime_ns != file_stat.st_mtime_ns
    ):
        _fail(f"external input {name} changed while it was read")
    observed = digest.hexdigest()
    if observed != expected:
        _fail(f"external input {name} SHA-256 does not match the owner value")
    return observed


def _normalise_external_inputs(
    repository: Path,
    external_inputs: Mapping[str, Any],
    expected_hashes: Mapping[str, str] | None,
) -> dict[str, str]:
    if set(external_inputs) != set(EXTERNAL_INPUT_NAMES):
        _fail("external input names must exactly match active qualification v2")
    if expected_hashes is not None and set(expected_hashes) != set(EXTERNAL_INPUT_NAMES):
        _fail("external expected SHA names must exactly match active qualification v2")
    used_paths: set[Path] = set()
    result: dict[str, str] = {}
    for name in EXTERNAL_INPUT_NAMES:
        item = external_inputs[name]
        path: Any = item
        expected: Any = expected_hashes.get(name) if expected_hashes is not None else None
        if isinstance(item, Mapping):
            path = item.get("path")
            if expected is None:
                expected = item.get("sha256")
        elif isinstance(item, (tuple, list)):
            if len(item) != 2:
                _fail(f"external input {name} must contain path and SHA-256")
            path, inline_expected = item
            if expected is None:
                expected = inline_expected
        if not isinstance(path, (str, Path)) or not isinstance(expected, str):
            _fail(f"external input {name} requires an owner SHA-256")
        result[name] = _verify_external_input(
            repository, name, Path(path), expected, used_paths
        )
    return result


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


def _check_active_template(active: Mapping[str, Any], lock_raw: bytes) -> None:
    if active.get("schema_version") != ACTIVE_SCHEMA_VERSION:
        _fail("active qualification schema is not v2")
    if active.get("profile") != PROFILE or active.get("status") != "machine_evaluation_pending":
        _fail("active qualification is not the clean 0.12 machine-pending template")
    if active.get("candidate_version") != OLD_VERSION:
        _fail("active qualification is not the 0.12 template")
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
    if not isinstance(external, dict) or set(external) != set(EXTERNAL_INPUT_NAMES):
        _fail("active qualification external input inventory is not v2")
    if any(value is not None for value in external.values()):
        _fail("active qualification already contains external input hashes")
    if active.get("blocker") != "machine_evaluation_not_executed":
        _fail("active qualification blocker is not machine-evaluation pending")
    for field in (
        "release_ready",
        "claim_eligible",
        "machine_qualification_claim_eligible",
        "competitive_claim_eligible",
    ):
        if active.get(field) is not False:
            _fail("active qualification contains a release or claim assertion")


def _build_updates(
    repository: Path,
    original: Mapping[str, bytes],
    external_hashes: Mapping[str, str],
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
    active["status"] = CONSTRUCTION_STATUS
    active["candidate_version"] = CANDIDATE_VERSION
    binding = active["candidate_binding"]
    binding["package_version"] = CANDIDATE_VERSION
    binding["lock_sha256"] = _sha256(updates["uv.lock"])
    active["external_inputs"] = dict(external_hashes)
    active["release_ready"] = False
    active["claim_eligible"] = False
    active["machine_qualification_claim_eligible"] = False
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
    external_inputs: Mapping[str, Any],
    expected_hashes: Mapping[str, str] | None = None,
    repository: Path = REPOSITORY,
    apply: bool = False,
    run_lock_check: bool = True,
) -> dict[str, Any]:
    """Validate and optionally apply the exact construction-candidate transition."""

    try:
        repository = Path(repository).expanduser().resolve(strict=True)
    except OSError as error:
        raise CandidatePrepError("repository is unavailable") from error
    head, tree, branch = _assert_git_identity(repository, integration_commit, apply=apply)
    original: dict[str, bytes] = {}
    for relative in CURRENT_SURFACE_FILES:
        _resolved, original[relative] = _regular_repo_file(repository, relative)
    external_hashes = _normalise_external_inputs(repository, external_inputs, expected_hashes)
    updates = _build_updates(repository, original, external_hashes)
    result: dict[str, Any] = {
        "schema_version": "deeplaw.v013-candidate-prep/v1",
        "mode": "apply" if apply else "dry-run",
        "base": {"commit": head, "tree": tree, "branch": branch or None},
        "candidate_identity": {
            "package_version": CANDIDATE_VERSION,
            "profile": PROFILE,
            "gate_classification": GATE_CLASSIFICATION,
            "status": CONSTRUCTION_STATUS,
        },
        "external_inputs": [
            {"name": name, "sha256": external_hashes[name], "verified": True}
            for name in EXTERNAL_INPUT_NAMES
        ],
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
            _atomic_apply(repository, original, updates)
            raise
        result["write_performed"] = True
    return result


prepare_v013_candidate = prepare_candidate


def _assignment(value: str, *, label: str) -> tuple[str, str]:
    name, separator, selected = value.partition("=")
    if not separator or not name or not selected:
        _fail(f"{label} must be NAME=VALUE")
    if name not in EXTERNAL_INPUT_NAMES:
        _fail(f"{label} uses an unknown v2 external input name")
    return name, selected


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=REPOSITORY)
    parser.add_argument("--integration-commit", required=True)
    parser.add_argument(
        "--external-input",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="owner-only external input file; repeat exactly once per v2 input",
    )
    parser.add_argument(
        "--external-sha256",
        action="append",
        required=True,
        metavar="NAME=SHA256",
        help="owner-provided SHA-256 for each external input",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="apply the construction transition (never allowed on main/master)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        paths: dict[str, str] = {}
        for item in args.external_input:
            name, value = _assignment(item, label="--external-input")
            if name in paths:
                _fail("duplicate --external-input name")
            paths[name] = value
        hashes: dict[str, str] = {}
        for item in args.external_sha256:
            name, value = _assignment(item, label="--external-sha256")
            if name in hashes:
                _fail("duplicate --external-sha256 name")
            hashes[name] = value
        result = prepare_candidate(
            repository=args.repository,
            integration_commit=args.integration_commit,
            external_inputs=paths,
            expected_hashes=hashes,
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
    "EXTERNAL_INPUT_NAMES",
    "GATE_CLASSIFICATION",
    "CandidatePrepError",
    "PreparationError",
    "main",
    "prepare_candidate",
    "prepare_v013_candidate",
]


if __name__ == "__main__":
    raise SystemExit(main())
