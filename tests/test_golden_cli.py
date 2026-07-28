from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import deeplaw.source_connectors as source_connectors
from deeplaw.cli import _parser
from deeplaw.golden_cli import handle_golden_command
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import sha256_bytes

_REPOSITORY = Path(__file__).resolve().parents[1]


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=_REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _json(*arguments: str) -> dict[str, Any]:
    result = _run(*arguments, "--format", "json")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _git(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("git executable is unavailable")
    result = subprocess.run(
        [executable, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def test_five_command_golden_path_requires_no_internal_ids(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "release.md").write_text(
        "# Procedure\nAlways verify the signed release artifact before deployment.\n",
        encoding="utf-8",
    )

    initialized = _json(
        "init",
        str(vault),
        "--name",
        "golden",
        "--project-root",
        str(tmp_path),
    )
    assert initialized["vault_id"].startswith("vault_")
    assert (tmp_path / ".deeplaw.json").is_file()

    ingested = _json(
        "add",
        str(sources),
        "--project-root",
        str(tmp_path),
        "--confirm-no-case-data",
    )
    assert ingested["state"] == "completed"
    assert ingested["summary"]["succeeded"] == 1

    reviewed = _json(
        "review",
        "--project-root",
        str(tmp_path),
        "--approve-all",
        "--confirm-reviewed",
    )
    assert reviewed["remaining"] == 0

    recalled = _json(
        "recall",
        "How should a release artifact be deployed?",
        "--project-root",
        str(tmp_path),
        "--confirm-no-case-data",
    )
    assert recalled["capsule_verification"]["valid"] is True
    assert recalled["capsule"]["knowledge_assets"]

    explained = _json("explain", "--last", "--project-root", str(tmp_path))
    assert explained["trace"]["query_plan"]["query_plan_id"].startswith("queryplan_")

    feedback = _json(
        "feedback",
        "--project-root",
        str(tmp_path),
        "--outcome",
        "success",
        "--observation",
        "The verified release procedure was useful.",
        "--recommended-action",
        "Retain the reviewed procedure.",
        "--mark-selected-helpful",
        "--confirm-no-case-data",
    )
    assert feedback["run"]["valid"] is True
    assert feedback["feedback"]["valid"] is True
    assert feedback["task_success_inferred"] is False


def test_golden_status_doctor_and_failure_streams_are_stable(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _json("init", str(vault), "--project-root", str(tmp_path))
    status = _json("status", "--project-root", str(tmp_path))
    assert status["vault_id"].startswith("vault_")

    doctor = _json(
        "doctor",
        "--knowledge",
        "--project-root",
        str(tmp_path),
    )
    assert doctor["canonical_valid"] is True

    failed = _run(
        "add",
        str(tmp_path / "missing.md"),
        "--project-root",
        str(tmp_path),
        "--confirm-no-case-data",
        "--format",
        "json",
    )
    assert failed.returncode == 2
    assert failed.stdout == ""
    assert failed.stderr.startswith("deeplaw: ")


def test_golden_path_ingests_and_reviews_a_mixed_format_directory(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    sources = tmp_path / "mixed"
    sources.mkdir()
    fixtures = {
        "policy.md": "# Policy\nVerify the Atlas release before deployment.\n",
        "page.html": "<h1>Guide</h1><p>Retain the Atlas source digest.</p>\n",
        "record.json": '{"rule": "Atlas artifacts remain local"}\n',
        "events.jsonl": '{"event": "Atlas build"}\n{"event": "Atlas review"}\n',
        "config.yaml": "policy: Atlas requires review\n",
        "settings.toml": 'policy = "Atlas uses owner-only storage"\n',
        "matrix.csv": "name,requirement\nAtlas,verified source\n",
        "matrix.tsv": "name\trequirement\nAtlas\tbounded context\n",
        "schema.sql": "CREATE TABLE atlas_release (digest TEXT NOT NULL);\n",
        "checks.py": "def verify_atlas_release(digest: str) -> bool:\n    return bool(digest)\n",
    }
    for name, content in fixtures.items():
        (sources / name).write_text(content, encoding="utf-8")

    _json("init", str(vault), "--project-root", str(tmp_path))
    ingested = _json(
        "add",
        str(sources),
        "--project-root",
        str(tmp_path),
        "--confirm-no-case-data",
    )
    assert ingested["state"] == "completed"
    assert ingested["summary"] == {
        "pending": 0,
        "running": 0,
        "succeeded": len(fixtures),
        "failed": 0,
        "cancelled": 0,
    }

    reviewed = _json(
        "review",
        "--project-root",
        str(tmp_path),
        "--approve-all",
        "--confirm-reviewed",
    )
    assert reviewed["remaining"] == 0
    recalled = _json(
        "recall",
        "What must happen to the Atlas release?",
        "--project-root",
        str(tmp_path),
        "--confirm-no-case-data",
    )
    assert recalled["capsule_verification"]["valid"] is True
    assert recalled["capsule"]["knowledge_assets"]


def test_golden_recall_and_explain_accept_explicit_local_dense_index() -> None:
    recall = _parser().parse_args(
        [
            "recall",
            "semantic query",
            "--mode",
            "semantic",
            "--discovery-index",
            "derived/discovery",
            "--model-root",
            "models/discovery",
            "--threads",
            "2",
        ]
    )
    explain = _parser().parse_args(
        [
            "explain",
            "semantic query",
            "--mode",
            "hybrid",
            "--discovery-index",
            "derived/discovery",
            "--model-root",
            "models/discovery",
            "--threads",
            "2",
        ]
    )

    assert recall.discovery_index == Path("derived/discovery")
    assert recall.model_root == Path("models/discovery")
    assert recall.threads == 2
    assert explain.discovery_index == recall.discovery_index


def test_golden_review_carries_relations_forward_without_internal_ids(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    source = tmp_path / "relations.md"
    initialize_knowledge_vault(vault_path, name="relation golden path", scope="project")
    source.write_text(
        "# Alpha\nAlpha depends on Beta.\n\n# Beta\nBeta is stable evidence.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(vault_path, read_only=False) as vault:
        first = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(first["source"]["source_id"])
        vault.approve_source_assets(
            first["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        assets = [vault.get_asset(asset_id) for asset_id in first["asset_ids"]]
        vault.add_relation(
            subject_asset_id=assets[0].asset_id,
            predicate="depends_on",
            object_asset_id=assets[1].asset_id,
            evidence_fragment_id=assets[0].source_refs[0].fragment_id,
            confirm_reviewed=True,
        )
        source.write_text(
            "# Alpha\nAlpha depends on Beta.\n\n"
            "# Beta\nBeta is stable evidence.\n\n"
            "# Gamma\nGamma is newly observed.\n",
            encoding="utf-8",
        )
        compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )

    source_review = _json(
        "review",
        "--vault",
        str(vault_path),
        "--approve-all",
        "--confirm-reviewed",
    )
    assert source_review["remaining_asset_proposals"] == 0
    assert source_review["remaining_relation_proposals"] == 1
    assert source_review["relation_proposal_generation"]["created_count"] == 1

    relation_review = _json(
        "review",
        "--vault",
        str(vault_path),
        "--approve-all",
        "--confirm-reviewed",
    )
    assert relation_review["remaining"] == 0
    assert any(
        decision.get("schema_version")
        == "deeplaw.relation-carry-forward-review/v1"
        for decision in relation_review["decisions"]
    )
    with KnowledgeVault(vault_path, read_only=True) as vault:
        assert len(vault.temporal_relations(mode="current")["relations"]) == 1


def test_golden_add_local_git_exact_revision_requires_no_internal_ids(
    tmp_path: Path,
) -> None:
    vault_path = tmp_path / "vault"
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Golden Path Tests")
    _git(repository, "config", "user.email", "golden@deeplaw.invalid")
    (repository / "release.md").write_text(
        "# Git release procedure\nVerify the exact commit before release.\n",
        encoding="utf-8",
    )
    _git(repository, "add", "release.md")
    _git(repository, "commit", "--quiet", "-m", "release procedure")
    revision = _git(repository, "rev-parse", "HEAD")

    _json("init", str(vault_path), "--project-root", str(tmp_path))
    ingested = _json(
        "add",
        "--git-repository",
        str(repository),
        "--git-revision",
        revision,
        "--git-repository-id",
        "golden-repository",
        "--include",
        "*.md",
        "--confirm-local-repository",
        "--confirm-no-case-data",
        "--project-root",
        str(tmp_path),
    )
    assert ingested["state"] == "completed"
    assert ingested["summary"]["succeeded"] == 1
    assert ingested["registration"]["enabled"] is False

    reviewed = _json(
        "review",
        "--project-root",
        str(tmp_path),
        "--approve-all",
        "--confirm-reviewed",
    )
    assert reviewed["remaining"] == 0
    recalled = _json(
        "recall",
        "How should the Git release be verified?",
        "--project-root",
        str(tmp_path),
        "--confirm-no-case-data",
    )
    assert recalled["capsule_verification"]["valid"] is True
    assert recalled["capsule"]["knowledge_assets"]

    with KnowledgeVault(vault_path, read_only=True) as vault:
        source = vault.source_info(ingested["items"][0]["source_id"])
        assert source["origin_uri"].startswith(
            f"deeplaw-git://golden-repository/{revision}/"
        )
        assert str(repository) not in source["origin_uri"]


def test_golden_https_preflight_is_network_and_write_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault_path = tmp_path / "vault"
    initialize_knowledge_vault(vault_path, name="HTTPS preflight", scope="project")

    def fail_download(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not perform network capture")

    monkeypatch.setattr(source_connectors, "_download_https", fail_download)
    args = _parser().parse_args(
        [
            "add",
            "--url",
            "https://example.com/guide.md",
            "--dry-run",
            "--confirm-no-case-data",
            "--vault",
            str(vault_path),
        ]
    )
    result = handle_golden_command(args)

    assert result["network_performed"] is False
    assert result["canonical_requested_url"] == "https://example.com/guide.md"
    assert not (vault_path / "operations").exists()


def test_golden_https_capture_forces_untrusted_review_gating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault_path = tmp_path / "vault"
    initialize_knowledge_vault(vault_path, name="HTTPS Golden Path", scope="project")
    content = b"# HTTPS procedure\nVerify the captured source hash.\n"
    requested = "https://example.com/guide.md"
    monkeypatch.setattr(
        source_connectors,
        "_download_https",
        lambda *_args, **_kwargs: (
            requested,
            content,
            "text/markdown",
            [requested],
            ["93.184.216.34"],
        ),
    )
    args = _parser().parse_args(
        [
            "add",
            "--url",
            requested,
            "--expected-sha256",
            sha256_bytes(content),
            "--confirm-network",
            "--confirm-no-case-data",
            "--vault",
            str(vault_path),
        ]
    )
    ingested = handle_golden_command(args)

    assert ingested["state"] == "completed"
    assert ingested["registration"]["enabled"] is False
    with KnowledgeVault(vault_path, read_only=True) as vault:
        source = vault.source_info(ingested["items"][0]["source_id"])
        assert source["kind"] == "web"
        assert source["trust"] == "untrusted"
        assert source["status"] == "pending"

    reviewed = _json(
        "review",
        "--vault",
        str(vault_path),
        "--approve-all",
        "--confirm-reviewed",
    )
    assert reviewed["remaining"] == 0
