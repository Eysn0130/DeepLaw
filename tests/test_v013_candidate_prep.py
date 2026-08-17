from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import benchmarks.release.prepare_v013_candidate as prep

REPOSITORY = Path(__file__).resolve().parents[1]


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
    for relative in prep.CURRENT_SURFACE_FILES:
        if relative == prep.ACTIVE_RELATIVE:
            continue
        source = REPOSITORY / relative
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        if relative in prep.VERSION_SURFACE_FILES:
            raw = raw.replace(b"0.13.0", b"0.12.0")
        target.write_bytes(raw)
        shutil.copymode(source, target)
    active_source = REPOSITORY / prep.ACTIVE_RELATIVE
    active = json.loads(active_source.read_text(encoding="utf-8"))
    active["status"] = "machine_evaluation_pending"
    active["candidate_version"] = prep.OLD_VERSION
    active["candidate_binding"].update(
        {
            "package_version": prep.OLD_VERSION,
            "source_commit": None,
            "source_tree": None,
            "lock_sha256": hashlib.sha256((repository / "uv.lock").read_bytes()).hexdigest(),
            "wheel_filename": None,
            "wheel_sha256": None,
            "sdist_filename": None,
            "sdist_sha256": None,
            "artifact_manifest_sha256": None,
        }
    )
    active["external_inputs"] = {
        name: None for name in prep.EXTERNAL_INPUT_NAMES
    }
    active["release_ready"] = False
    active["claim_eligible"] = False
    active["machine_qualification_claim_eligible"] = False
    active["competitive_claim_eligible"] = False
    active["blocker"] = "machine_evaluation_not_executed"
    active_target = repository / prep.ACTIVE_RELATIVE
    active_target.parent.mkdir(parents=True, exist_ok=True)
    active_target.write_text(
        json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copymode(active_source, active_target)
    historical = repository / "benchmarks/v013/qualification-protocol-v1.json"
    historical.parent.mkdir(parents=True, exist_ok=True)
    historical.write_bytes(
        (REPOSITORY / "benchmarks/v013/qualification-protocol-v1.json").read_bytes()
    )
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "candidate-prep@example.invalid")
    _git(repository, "config", "user.name", "candidate-prep")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "seed current surfaces")
    return repository


def _inputs(tmp_path: Path) -> tuple[dict[str, Path], dict[str, str]]:
    paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    for index, name in enumerate(prep.EXTERNAL_INPUT_NAMES, start=1):
        path = tmp_path / f"external-{index}.sealed"
        raw = f"synthetic owner input {index}\n".encode()
        path.write_bytes(raw)
        path.chmod(0o600)
        paths[name] = path
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return paths, hashes


def _commit(repository: Path) -> str:
    return _git(repository, "rev-parse", "HEAD")


def _snapshot(repository: Path) -> dict[str, bytes]:
    return {
        relative: (repository / relative).read_bytes()
        for relative in prep.CURRENT_SURFACE_FILES
    }


def _run(
    repository: Path,
    inputs: dict[str, Path],
    hashes: dict[str, str],
    *,
    apply: bool = False,
    run_lock_check: bool = False,
) -> dict[str, Any]:
    return prep.prepare_candidate(
        repository=repository,
        integration_commit=_commit(repository),
        external_inputs=inputs,
        expected_hashes=hashes,
        apply=apply,
        run_lock_check=run_lock_check,
    )


def test_dry_run_is_no_write_and_does_not_disclose_external_paths_or_content(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    inputs, hashes = _inputs(tmp_path)
    before = _snapshot(repository)

    result = _run(repository, inputs, hashes)

    assert result["mode"] == "dry-run"
    assert result["write_performed"] is False
    assert result["candidate_identity"]["package_version"] == "0.13.0"
    assert result["candidate_identity"]["gate_classification"] == "v8"
    assert set(result["planned_targets"]) == {
        *prep.VERSION_SURFACE_FILES,
        prep.ACTIVE_RELATIVE,
    }
    assert _snapshot(repository) == before
    serialized = json.dumps(result, sort_keys=True)
    for path in inputs.values():
        assert str(path) not in serialized
    assert "synthetic owner input" not in serialized


def test_repository_root_is_canonicalized_before_containment_checks(
    tmp_path: Path,
) -> None:
    repository = _seed_repository(tmp_path)
    alias = tmp_path / "repository-alias"
    alias.symlink_to(repository, target_is_directory=True)
    inputs, hashes = _inputs(tmp_path)

    result = prep.prepare_candidate(
        repository=alias,
        integration_commit=_commit(repository),
        external_inputs=inputs,
        expected_hashes=hashes,
    )

    assert result["mode"] == "dry-run"
    assert result["write_performed"] is False


@pytest.mark.parametrize("failure", ["dirty", "wrong_head"])
def test_preflight_rejects_dirty_or_wrong_exact_integration(
    tmp_path: Path, failure: str
) -> None:
    repository = _seed_repository(tmp_path)
    inputs, hashes = _inputs(tmp_path)
    if failure == "dirty":
        (repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    else:
        integration = "f" * 40
        with pytest.raises(prep.CandidatePrepError, match="HEAD"):
            prep.prepare_candidate(
                repository=repository,
                integration_commit=integration,
                external_inputs=inputs,
                expected_hashes=hashes,
            )
        return
    with pytest.raises(prep.CandidatePrepError, match="clean"):
        _run(repository, inputs, hashes)


def test_apply_is_rejected_on_main_and_preserves_the_template(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    _git(repository, "branch", "-M", "main")
    inputs, hashes = _inputs(tmp_path)
    before = _snapshot(repository)

    with pytest.raises(prep.CandidatePrepError, match="main branch"):
        _run(repository, inputs, hashes, apply=True)

    assert _snapshot(repository) == before


@pytest.mark.parametrize("case", ["missing", "duplicate", "unsafe_mode", "wrong_hash"])
def test_external_input_inventory_is_fail_closed(tmp_path: Path, case: str) -> None:
    repository = _seed_repository(tmp_path)
    inputs, hashes = _inputs(tmp_path)
    if case == "missing":
        inputs.pop(prep.EXTERNAL_INPUT_NAMES[-1])
        hashes.pop(prep.EXTERNAL_INPUT_NAMES[-1])
    elif case == "duplicate":
        inputs[prep.EXTERNAL_INPUT_NAMES[-1]] = inputs[prep.EXTERNAL_INPUT_NAMES[0]]
        hashes[prep.EXTERNAL_INPUT_NAMES[-1]] = hashes[prep.EXTERNAL_INPUT_NAMES[0]]
    elif case == "unsafe_mode":
        inputs[prep.EXTERNAL_INPUT_NAMES[0]].chmod(0o644)
    else:
        hashes[prep.EXTERNAL_INPUT_NAMES[0]] = "f" * 64
    with pytest.raises(prep.CandidatePrepError):
        _run(repository, inputs, hashes)
    assert json.loads(
        (repository / prep.ACTIVE_RELATIVE).read_text(encoding="utf-8")
    )["candidate_version"] == "0.12.0"


def test_apply_updates_only_current_surfaces_and_preserves_history(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    _git(repository, "checkout", "-q", "-b", "codex/v013-candidate-prep")
    inputs, hashes = _inputs(tmp_path)
    before = _snapshot(repository)
    historical = (repository / "benchmarks/v013/qualification-protocol-v1.json").read_bytes()

    result = _run(repository, inputs, hashes, apply=True, run_lock_check=True)

    assert result["write_performed"] is True
    assert (repository / "pyproject.toml").read_text(encoding="utf-8").count(
        'version = "0.13.0"'
    ) == 1
    assert 'version = "0.13.0"' in (repository / "uv.lock").read_text(encoding="utf-8")
    claude_marketplace = json.loads(
        (repository / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    )
    assert claude_marketplace["version"] == "0.13.0"
    assert {item["version"] for item in claude_marketplace["plugins"]} == {"0.13.0"}
    product_surface_schema = json.loads(
        (
            repository / "contracts/product-surface-manifest.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert product_surface_schema["properties"]["package_version"]["const"] == "0.13.0"
    source_matrix_schema = json.loads(
        (
            repository
            / "contracts/authoritative-source-quality-decision-matrix.v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert source_matrix_schema["properties"]["release_target"]["const"] == "0.13.0"
    for name in (
        "semantic-machine-review-packet.v1.schema.json",
        "semantic-machine-review-consensus.v1.schema.json",
        "semantic-owner-review-packet.v1.schema.json",
    ):
        schema = json.loads((repository / "contracts" / name).read_text(encoding="utf-8"))
        assert schema["$defs"]["candidate_binding"]["properties"]["version"]["const"] == (
            "0.13.0"
        )
    machine_review_builder = (
        repository / "benchmarks/semantic/build_machine_review_consensus.py"
    ).read_text(encoding="utf-8")
    assert 'CANDIDATE_VERSION = "0.13.0"' in machine_review_builder
    openvex = json.loads(
        (repository / "security/openvex.json").read_text(encoding="utf-8")
    )
    assert {
        product["@id"]
        for statement in openvex["statements"]
        for product in statement["products"]
    } == {"pkg:pypi/deeplaw@0.13.0"}
    active = json.loads(
        (repository / prep.ACTIVE_RELATIVE).read_text(encoding="utf-8")
    )
    assert active["status"] == prep.CONSTRUCTION_STATUS
    assert active["candidate_version"] == "0.13.0"
    assert active["candidate_binding"]["package_version"] == "0.13.0"
    assert active["candidate_binding"]["source_commit"] is None
    assert active["candidate_binding"]["source_tree"] is None
    assert active["candidate_binding"]["lock_sha256"] == hashlib.sha256(
        (repository / "uv.lock").read_bytes()
    ).hexdigest()
    assert active["external_inputs"] == hashes
    assert active["blocker"] == "candidate_artifact_not_built"
    assert active["release_ready"] is False
    assert active["claim_eligible"] is False
    active_schema = json.loads(
        (REPOSITORY / "contracts/v013-active-qualification.v2.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(active_schema).validate(active)
    assert (
        repository / "benchmarks/v013/qualification-protocol-v1.json"
    ).read_bytes() == historical
    assert (repository / ".agents/plugins/marketplace.json").read_bytes() == before[
        ".agents/plugins/marketplace.json"
    ]
    assert (repository / "adapters/obsidian/manifest.json").read_bytes() == before[
        "adapters/obsidian/manifest.json"
    ]


def test_apply_rolls_back_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _seed_repository(tmp_path)
    _git(repository, "checkout", "-q", "-b", "codex/v013-candidate-prep")
    inputs, hashes = _inputs(tmp_path)
    before = _snapshot(repository)
    original_replace = prep.os.replace
    calls = 0

    def fail_on_second(
        source: str | bytes | os.PathLike[str], target: str | bytes | os.PathLike[str]
    ) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replace failure")
        original_replace(source, target)

    monkeypatch.setattr(prep.os, "replace", fail_on_second)
    with pytest.raises(prep.CandidatePrepError, match="rolled back"):
        _run(repository, inputs, hashes, apply=True)
    assert _snapshot(repository) == before
    assert not list(repository.glob("**/.deeplaw-candidate-prep-*"))


def test_current_freezer_still_rejects_unprepared_012_template(tmp_path: Path) -> None:
    repository = _seed_repository(tmp_path)
    active = json.loads(
        (repository / prep.ACTIVE_RELATIVE).read_text(encoding="utf-8")
    )
    assert active["status"] == "machine_evaluation_pending"
    assert active["candidate_version"] == "0.12.0"
    assert all(value is None for value in active["external_inputs"].values())
    assert not hasattr(prep, "freeze_candidate")
