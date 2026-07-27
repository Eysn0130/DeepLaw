from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from deeplaw import __version__
from deeplaw.knowledge_models import utc_now
from deeplaw.util import sha256_file


def _source_tree_sha256(repository: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((repository / "src" / "deeplaw").rglob("*.py")):
        digest.update(path.relative_to(repository).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def build_manifest(
    repository: Path,
    *,
    diagnostic: Path,
    wheel: Path,
    sdist: Path,
    candidate_commit: str,
    reproducible_build: bool,
) -> dict[str, object]:
    candidate_commit = _git(repository, "rev-parse", f"{candidate_commit}^{{commit}}")
    if len(candidate_commit) != 40:
        raise RuntimeError("candidate commit did not resolve to a full Git identity")
    diagnostic_report = json.loads(diagnostic.read_text(encoding="utf-8"))
    if diagnostic_report["implementation"]["python_source_tree_sha256"] != _source_tree_sha256(
        repository
    ):
        raise RuntimeError("candidate diagnostic does not bind the current Python source tree")
    if diagnostic_report["implementation"]["package_version"] != __version__:
        raise RuntimeError("candidate diagnostic package version is stale")
    tracked_dirty = bool(
        _git(repository, "status", "--porcelain", "--untracked-files=no")
    )
    return {
        "schema_version": "deeplaw.knowledge-os-candidate/v1",
        "status": "release_candidate_internal_not_held_out",
        "recorded_at_utc": utc_now(),
        "package_version": __version__,
        "implementation": {
            "pyproject_sha256": sha256_file(repository / "pyproject.toml"),
            "uv_lock_sha256": sha256_file(repository / "uv.lock"),
            "python_source_tree_sha256": _source_tree_sha256(repository),
            "python_source_tree_hash_method": (
                "sha256(sorted(path + NUL + bytes + NUL) for src/deeplaw/**/*.py)"
            ),
            "candidate_code_commit": candidate_commit,
            "manifest_generation_head": _git(repository, "rev-parse", "HEAD"),
            "tracked_worktree_dirty_at_manifest_generation": tracked_dirty,
            "local_build_artifacts": {
                "wheel_path": wheel.relative_to(repository).as_posix(),
                "wheel_sha256": sha256_file(wheel),
                "sdist_path": sdist.relative_to(repository).as_posix(),
                "sdist_sha256": sha256_file(sdist),
                "two_consecutive_builds_byte_identical": reproducible_build,
                "fresh_wheel_lifecycle_verified": True,
                "external_candidate_frozen": False,
            },
        },
        "evidence": {
            "knowledge_control_diagnostic": {
                "path": diagnostic.relative_to(repository).as_posix(),
                "sha256": sha256_file(diagnostic),
                "claim_eligible": False,
            }
        },
        "activation_decision": {
            "source_control_plane": "accepted_for_internal_release_candidate",
            "default_semantic_discovery": "rejected",
            "reason": (
                "The synthetic control diagnostic and regression suite passed source-version, "
                "review, Capsule provenance, Run Receipt, feedback, migration recovery, and "
                "integrity gates. They do not evaluate external semantic leadership."
            ),
        },
        "external_proof": {
            "status": "pending_external_execution",
            "current_candidate_frozen": False,
            "unbounded_claim_allowed": False,
            "historical_evaluator_kit_protocol": "deeplaw-external-proof/2026-07-26-v3",
            "historical_frozen_candidate": "benchmarks/external/candidate-v0.5.0.json",
            "reason": (
                "The v0.6.0 internal candidate has reproducible local artifacts, but the existing "
                "evaluator kit is bound to historical v0.5.0. Secret held-out commitments and "
                "two independent signed evaluator runs have not been completed for v0.6.0."
            ),
        },
        "limitations": [
            "The control-plane diagnostic is synthetic and claim_eligible=false.",
            (
                "Native Windows ACL equivalence remains not_verified; Windows CI proves "
                "functional smoke only."
            ),
            (
                "The exact-token diagnostic does not establish semantic generalization or "
                "cross-system superiority."
            ),
            "No secret held-out run or independent evaluator signature exists for v0.6.0.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate-commit")
    parser.add_argument("--reproducible-build", action="store_true")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    output = args.output.expanduser().absolute()
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise FileExistsError("candidate manifest output must be a regular non-symlink path")
    if output.exists() and not args.replace:
        raise FileExistsError("candidate manifest exists; pass --replace to replace it atomically")
    manifest = build_manifest(
        repository,
        diagnostic=args.diagnostic.expanduser().absolute(),
        wheel=args.wheel.expanduser().absolute(),
        sdist=args.sdist.expanduser().absolute(),
        candidate_commit=args.candidate_commit or _git(repository, "rev-parse", "HEAD"),
        reproducible_build=args.reproducible_build,
    )
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    main()
