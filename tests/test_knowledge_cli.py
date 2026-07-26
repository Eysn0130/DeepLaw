from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_knowledge_cli_lifecycle_compiles_a_verified_capsule(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialized = _run_cli(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "cli-project",
        "--scope",
        "project",
    )
    assert initialized.returncode == 0, initialized.stderr

    proposed = _run_cli(
        "knowledge",
        "propose",
        "--vault",
        str(vault),
        "--kind",
        "constraint",
        "--memory-tier",
        "project",
        "--title",
        "Stable storage boundary",
        "--statement",
        "Preserve the accepted storage contract during migration.",
        "--semantic-key",
        "storage.boundary",
        "--sensitivity",
        "internal",
        "--confirm-no-case-data",
    )
    assert proposed.returncode == 0, proposed.stderr
    asset_id = json.loads(proposed.stdout)["asset_id"]

    approved = _run_cli(
        "knowledge",
        "approve",
        "--vault",
        str(vault),
        "--asset-id",
        asset_id,
        "--confirm-reviewed",
    )
    assert approved.returncode == 0, approved.stderr
    assert json.loads(approved.stdout)["status"] == "active"

    capsule_path = tmp_path / "capsule.json"
    compiled = _run_cli(
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "migrate storage while preserving the accepted contract",
        "--confirm-no-case-data",
        "--output",
        str(capsule_path),
    )
    assert compiled.returncode == 0, compiled.stderr
    capsule = json.loads(compiled.stdout)
    assert capsule["constraints"][0]["asset_id"] == asset_id
    assert capsule_path.is_file()

    verified = _run_cli(
        "knowledge",
        "verify-capsule",
        "--capsule",
        str(capsule_path),
        "--vault",
        str(vault),
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["valid"] is True


def test_knowledge_cli_rejects_a_manual_proposal_without_case_boundary(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    assert (
        _run_cli(
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "boundary",
        ).returncode
        == 0
    )
    rejected = _run_cli(
        "knowledge",
        "propose",
        "--vault",
        str(vault),
        "--kind",
        "fact",
        "--memory-tier",
        "project",
        "--title",
        "Unconfirmed material",
        "--statement",
        "This statement has no explicit case boundary confirmation.",
    )

    assert rejected.returncode == 2
    assert "--confirm-no-case-data" in rejected.stderr


def test_knowledge_cli_refuses_to_overwrite_a_capsule_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    initialized = _run_cli(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "capsule-output",
    )
    assert initialized.returncode == 0, initialized.stderr
    capsule_path = tmp_path / "existing.json"
    capsule_path.write_text("user content", encoding="utf-8")

    result = _run_cli(
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "compile a bounded context",
        "--confirm-no-case-data",
        "--output",
        str(capsule_path),
    )

    assert result.returncode == 2
    assert "already exists" in result.stderr
    assert capsule_path.read_text(encoding="utf-8") == "user content"


def test_knowledge_cli_does_not_offer_verified_source_as_user_input(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    assert (
        _run_cli(
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "trust-boundary",
        ).returncode
        == 0
    )
    result = _run_cli(
        "knowledge",
        "propose",
        "--vault",
        str(vault),
        "--kind",
        "fact",
        "--memory-tier",
        "domain",
        "--title",
        "False authority",
        "--statement",
        "A user cannot self-assert publisher verification.",
        "--trust",
        "verified_source",
        "--confirm-no-case-data",
    )

    assert result.returncode == 2
    assert "invalid choice" in result.stderr
