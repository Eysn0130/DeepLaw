from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import benchmarks.release.prepare_v013_candidate as prep

REPOSITORY = Path(__file__).resolve().parents[1]
HISTORY = (
    "benchmarks/v013/active-qualification-v2.json",
    "benchmarks/v013/qualification-protocol-v2.json",
    "benchmarks/release/v013-gate-classification-v8.json",
)


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _seed_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    relatives = (*prep.CURRENT_SURFACE_FILES, *prep.CONTRACT_FILES, *HISTORY)
    for relative in relatives:
        source = REPOSITORY / relative
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        if relative == "pyproject.toml":
            raw = raw.replace(
                b'version = "0.13.0"', b'version = "0.12.0"', 1
            )
        elif relative == "uv.lock":
            raw = raw.replace(
                b'[[package]]\nname = "deeplaw"\nversion = "0.13.0"',
                b'[[package]]\nname = "deeplaw"\nversion = "0.12.0"',
                1,
            )
        elif relative in prep.VERSION_SURFACE_FILES:
            raw = raw.replace(b"0.13.0", b"0.12.0")
        target.write_bytes(raw)
        shutil.copymode(source, target)
    active_path = repository / prep.ACTIVE_RELATIVE
    active = json.loads(active_path.read_text(encoding="utf-8"))
    active["status"] = "machine_evaluation_pending"
    active["candidate_version"] = prep.OLD_VERSION
    active["blocker"] = "machine_evaluation_not_executed"
    active["candidate_binding"]["package_version"] = prep.OLD_VERSION
    active["candidate_binding"]["lock_sha256"] = hashlib.sha256(
        (repository / "uv.lock").read_bytes()
    ).hexdigest()
    active_path.write_text(
        json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "candidate-prep@example.invalid")
    _git(repository, "config", "user.name", "candidate-prep")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "seed current surfaces")
    return repository


def _commit(repository: Path) -> str:
    return _git(repository, "rev-parse", "HEAD")


def _snapshot(repository: Path, relatives: tuple[str, ...]) -> dict[str, bytes]:
    return {relative: (repository / relative).read_bytes() for relative in relatives}


def _run(repository: Path, *, apply: bool = False) -> dict[str, Any]:
    return prep.prepare_candidate(
        repository=repository,
        integration_commit=_commit(repository),
        apply=apply,
    )


def test_construction_dry_run_is_no_write_and_uses_v3_contracts(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    tracked = (*prep.CURRENT_SURFACE_FILES, *HISTORY)
    before = _snapshot(repository, tracked)

    result = _run(repository)

    assert result["mode"] == "dry-run"
    assert result["write_performed"] is False
    assert result["candidate_identity"] == {
        "package_version": "0.13.0",
        "profile": "kernel_release_core",
        "gate_classification": "v9",
        "status": prep.CONSTRUCTION_STATUS,
    }
    assert "external_inputs" not in result
    assert set(result["planned_targets"]) == {
        *prep.VERSION_SURFACE_FILES,
        prep.ACTIVE_RELATIVE,
    }
    assert _snapshot(repository, tracked) == before


def test_apply_requires_independent_candidate_branch_and_preserves_v2_v8_history(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    _git(repository, "checkout", "-q", "-b", "codex/v013-candidate-prep")
    before_history = _snapshot(repository, HISTORY)

    result = _run(repository, apply=True)

    assert result["write_performed"] is True
    assert 'version = "0.13.0"' in (repository / "pyproject.toml").read_text()
    assert 'version = "0.13.0"' in (repository / "uv.lock").read_text()
    active = json.loads(
        (repository / prep.ACTIVE_RELATIVE).read_text(encoding="utf-8")
    )
    assert active["status"] == prep.CONSTRUCTION_STATUS
    assert active["candidate_version"] == "0.13.0"
    assert active["candidate_binding"]["package_version"] == "0.13.0"
    assert active["candidate_binding"]["source_commit"] is None
    assert active["candidate_binding"]["source_tree"] is None
    assert all(
        active["external_inputs"][name] is None
        for name in prep.OPTIONAL_EXTERNAL_HASH_NAMES
    )
    assert active["external_inputs"]["null_is_non_blocking"] is True
    assert active["external_inputs"]["required_for_candidate_binding"] is False
    assert active["release_ready"] is False
    assert all(
        row["status"] == "not_executed"
        and row["passed"] is False
        and row["claim"] is False
        for section in ("core_statuses", "capability_claims", "competitive_claims")
        for row in active[section]
    )
    assert _snapshot(repository, HISTORY) == before_history


def test_apply_is_rejected_on_main_without_writing(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    _git(repository, "branch", "-M", "main")
    before = _snapshot(repository, prep.CURRENT_SURFACE_FILES)
    with pytest.raises(prep.CandidatePrepError, match="main branch"):
        _run(repository, apply=True)
    assert _snapshot(repository, prep.CURRENT_SURFACE_FILES) == before


def test_dirty_or_wrong_git_identity_fails_before_contract_transition(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    (repository / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(prep.CandidatePrepError, match="clean"):
        _run(repository)

    clean_root = tmp_path / "clean"
    clean_root.mkdir()
    clean_repository = _seed_repository(clean_root)
    with pytest.raises(prep.CandidatePrepError, match="HEAD"):
        prep.prepare_candidate(
            repository=clean_repository,
            integration_commit="f" * 40,
            run_lock_check=False,
        )


@pytest.mark.parametrize("kind", ["wrong_version", "wrong_hash"])
def test_wrong_construction_contract_or_hash_fails(tmp_path: Path, kind: str) -> None:
    repository = _seed_repository(tmp_path)
    if kind == "wrong_version":
        path = repository / "pyproject.toml"
        path.write_bytes(
            path.read_bytes().replace(
                b'version = "0.12.0"', b'version = "0.11.0"', 1
            )
        )
    else:
        path = repository / prep.CLASSIFICATION_RELATIVE
        value = json.loads(path.read_text(encoding="utf-8"))
        value["profile"] = "tampered"
        path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "tamper")
    with pytest.raises(prep.CandidatePrepError):
        _run(repository)


def test_old_external_input_cli_arguments_are_rejected(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    with pytest.raises(SystemExit):
        prep.main(
            [
                "--repository",
                str(repository),
                "--integration-commit",
                _commit(repository),
                "--external-input",
                "holdout=/tmp/forbidden",
            ]
        )
