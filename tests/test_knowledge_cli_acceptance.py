from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _json(*arguments: str) -> dict[str, Any]:
    result = _run(*arguments)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _propose(
    vault: Path,
    *,
    kind: str,
    title: str,
    statement: str,
    semantic_key: str | None = None,
    sensitivity: str = "private",
    supersedes: str | None = None,
) -> str:
    arguments = [
        "knowledge",
        "propose",
        "--vault",
        str(vault),
        "--kind",
        kind,
        "--memory-tier",
        "project",
        "--title",
        title,
        "--statement",
        statement,
        "--sensitivity",
        sensitivity,
        "--confirm-no-case-data",
    ]
    if semantic_key is not None:
        arguments.extend(("--semantic-key", semantic_key))
    if supersedes is not None:
        arguments.extend(("--supersedes-asset-id", supersedes))
    return str(_json(*arguments)["asset_id"])


def _approve(vault: Path, asset_id: str) -> dict[str, Any]:
    return _json(
        "knowledge",
        "approve",
        "--vault",
        str(vault),
        "--asset-id",
        asset_id,
        "--confirm-reviewed",
    )


def test_source_alias_latest_and_active_selectors_survive_rename(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _json(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "source selectors",
        "--scope",
        "project",
    )
    original = tmp_path / "policy.md"
    original.write_text("# Policy\nUse the blue Atlas path.\n", encoding="utf-8")
    first = _json(
        "knowledge",
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(original),
        "--confirm-no-case-data",
    )
    _json(
        "review",
        "--vault",
        str(vault),
        "--approve-all",
        "--confirm-reviewed",
        "--format",
        "json",
    )

    renamed = tmp_path / "renamed-policy.md"
    renamed.write_text("# Policy\nUse the blue Atlas path.\n", encoding="utf-8")
    second = _json(
        "knowledge",
        "source",
        "update",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--source",
        str(renamed),
        "--confirm-no-case-data",
    )

    active_before_review = _json(
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--active",
    )
    latest = _json(
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--latest",
    )
    diff = _json(
        "knowledge",
        "source",
        "diff",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--latest",
    )

    assert active_before_review["source_id"] == first["source"]["source_id"]
    assert latest["source_id"] == second["source"]["source_id"]
    assert latest["status"] == "pending"
    assert diff["unchanged_count"] == 1

    _json(
        "review",
        "--vault",
        str(vault),
        "--approve-all",
        "--confirm-reviewed",
        "--format",
        "json",
    )
    active_from_historical_alias = _json(
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--active",
    )
    active_from_current_alias = _json(
        "knowledge",
        "source",
        "verify",
        "--vault",
        str(vault),
        "--alias",
        "renamed-policy.md",
        "--active",
    )
    active_list = _json(
        "knowledge",
        "source",
        "list",
        "--vault",
        str(vault),
        "--active",
    )

    assert active_from_historical_alias["source_id"] == second["source"]["source_id"]
    assert active_from_historical_alias["logical_path"] == "renamed-policy.md"
    assert active_from_current_alias["valid"] is True
    assert [item["source_id"] for item in active_list["sources"]] == [
        second["source"]["source_id"]
    ]


def test_source_alias_latest_fails_closed_for_parallel_pending_successors(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _json(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "ambiguous source selectors",
        "--scope",
        "project",
    )
    original = tmp_path / "policy.md"
    original.write_text(
        "# Policy\nThe initial policy requires the stable Atlas review path.\n",
        encoding="utf-8",
    )
    _json(
        "knowledge",
        "source",
        "add",
        "--vault",
        str(vault),
        "--source",
        str(original),
        "--confirm-no-case-data",
    )
    _json(
        "review",
        "--vault",
        str(vault),
        "--approve-all",
        "--confirm-reviewed",
        "--format",
        "json",
    )
    for index in (1, 2):
        original.write_text(
            f"# Policy\nCandidate {index} requires the reviewed Atlas successor path.\n",
            encoding="utf-8",
        )
        _json(
            "knowledge",
            "source",
            "update",
            "--vault",
            str(vault),
            "--alias",
            "policy.md",
            "--source",
            str(original),
            "--confirm-no-case-data",
        )

    result = _run(
        "knowledge",
        "source",
        "show",
        "--vault",
        str(vault),
        "--alias",
        "policy.md",
        "--latest",
    )

    assert result.returncode != 0
    assert "multiple pending successors" in result.stderr


def test_cli_full_knowledge_asset_lifecycle(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _json(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "acceptance-vault",
        "--scope",
        "project",
    )
    source = tmp_path / "release-guide.md"
    source.write_text(
        "# Release evidence\n"
        "The build receipt records the artifact digest and reviewer identity.\n",
        encoding="utf-8",
    )
    ingested = _json(
        "knowledge",
        "ingest",
        "--vault",
        str(vault),
        "--source",
        str(source),
        "--source-kind",
        "document",
        "--sensitivity",
        "private",
        "--confirm-no-case-data",
    )
    source_asset_id = str(ingested["asset_ids"][0])
    before_review = _json(
        "knowledge",
        "search",
        "--vault",
        str(vault),
        "--query",
        "artifact digest reviewer",
    )
    assert before_review["results"] == []
    _approve(vault, source_asset_id)

    decision_id = _propose(
        vault,
        kind="decision",
        title="Mercury release path",
        statement="The Mercury release uses the blue deployment path.",
        semantic_key="release.path",
    )
    constraint_id = _propose(
        vault,
        kind="constraint",
        title="Artifact signature boundary",
        statement="Every production artifact must pass signature verification.",
    )
    question_id = _propose(
        vault,
        kind="question",
        title="Compatibility approval owner",
        statement="Which owner approves the compatibility checkpoint?",
    )
    restricted_id = _propose(
        vault,
        kind="fact",
        title="Restricted operational note",
        statement="Restricted operational detail must stay outside Agent retrieval.",
        sensitivity="restricted",
    )
    for asset_id in (decision_id, constraint_id, question_id, restricted_id):
        _approve(vault, asset_id)

    _json(
        "knowledge",
        "relate",
        "--vault",
        str(vault),
        "--subject-asset-id",
        decision_id,
        "--predicate",
        "depends_on",
        "--object-asset-id",
        constraint_id,
        "--confirm-reviewed",
    )
    searched = _json(
        "knowledge",
        "search",
        "--vault",
        str(vault),
        "--query",
        "Mercury release",
    )
    assert searched["results"][0]["asset_id"] == decision_id
    assert searched["results"][0]["rank"] == 1
    assert "score" not in searched["results"][0]
    assert searched["ranking"]["numeric_confidence_exposed"] is False
    assert restricted_id not in json.dumps(searched)

    capsule_path = tmp_path / "capsule.json"
    capsule = _json(
        "knowledge",
        "context",
        "--vault",
        str(vault),
        "--task",
        "Prepare the Mercury release and resolve compatibility approval.",
        "--confirm-no-case-data",
        "--output",
        str(capsule_path),
    )
    serialized_capsule = json.dumps(capsule)
    assert decision_id in serialized_capsule
    assert constraint_id in serialized_capsule
    assert question_id in serialized_capsule
    assert restricted_id not in serialized_capsule
    assert capsule["trust_boundary"]["knowledge_assets_are_legal_authority"] is False
    assert capsule["next_actions"] == [
        f"Review unresolved question asset deeplaw://{capsule['vault_id']}"
        f"/assets/{question_id}"
    ]
    assert any(
        item["selection_reason"].startswith("reviewed_relation:depends_on:")
        for item in capsule["constraints"]
    )
    verified_capsule = _json(
        "knowledge",
        "verify-capsule",
        "--capsule",
        str(capsule_path),
        "--vault",
        str(vault),
    )
    assert verified_capsule["valid"] is True

    feedback = _json(
        "knowledge",
        "feedback",
        "--vault",
        str(vault),
        "--capsule",
        str(capsule_path),
        "--outcome",
        "partial",
        "--observation",
        "The selected context exposed the unresolved approval owner.",
        "--lesson",
        "Resolve explicit gaps before execution.",
        "--confirm-no-case-data",
    )
    assert feedback["asset"]["status"] == "proposed"
    debug = _json(
        "knowledge",
        "debug",
        "--vault",
        str(vault),
        "--question",
        "Why was deployment blocked?",
        "--cause",
        "The signature gate was not checked.",
        "--fix",
        "Verify the signed artifact before deployment.",
        "--prevention",
        "Keep the reviewed constraint in task context.",
        "--confirm-no-case-data",
    )
    assert debug["asset"]["status"] == "proposed"

    replacement_id = _propose(
        vault,
        kind="decision",
        title="Mercury release path",
        statement="The Mercury release uses the green deployment path after validation.",
        semantic_key="release.path",
        supersedes=decision_id,
    )
    _approve(vault, replacement_id)
    old_decision = _json(
        "knowledge",
        "get",
        "--vault",
        str(vault),
        "--asset-id",
        decision_id,
        "--include-inactive",
    )
    assert old_decision["status"] == "superseded"
    revoked = _json(
        "knowledge",
        "revoke",
        "--vault",
        str(vault),
        "--asset-id",
        source_asset_id,
        "--reason",
        "The release guide was replaced by the reviewed project decision.",
        "--confirm",
    )
    assert revoked["status"] == "revoked"

    package = tmp_path / "knowledge.dlk"
    exported = _json(
        "knowledge",
        "export",
        "--vault",
        str(vault),
        "--output",
        str(package),
        "--max-sensitivity",
        "private",
        "--include-evidence-text",
        "--include-source-files",
        "--confirm-export-source-files",
    )
    assert exported["asset_count"] >= 3
    package_verification = _json(
        "knowledge",
        "verify-package",
        "--package",
        str(package),
    )
    assert package_verification["valid"] is True

    markdown = tmp_path / "markdown"
    markdown_export = _json(
        "knowledge",
        "export-markdown",
        "--vault",
        str(vault),
        "--output",
        str(markdown),
        "--max-sensitivity",
        "private",
    )
    assert markdown_export["asset_count"] >= 3
    assert (markdown / "INDEX.md").is_file()

    imported_vault = tmp_path / "imported"
    _json(
        "knowledge",
        "init",
        "--vault",
        str(imported_vault),
        "--name",
        "imported-vault",
    )
    imported = _json(
        "knowledge",
        "import-package",
        "--vault",
        str(imported_vault),
        "--package",
        str(package),
        "--confirm-untrusted",
    )
    assert imported["status"] == "quarantined"
    inspection = _json(
        "knowledge",
        "inspect",
        "--vault",
        str(imported_vault),
    )
    assert inspection["agent_ready"] is False
    assert inspection["asset_status_counts"]["quarantined"] == len(
        imported["imported_asset_ids"]
    )


def test_cli_selective_forgetting_is_explicit_and_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _json(
        "knowledge",
        "init",
        "--vault",
        str(vault),
        "--name",
        "forget-vault",
        "--scope",
        "project",
    )
    asset_id = _propose(
        vault,
        kind="experience",
        title="Old preference",
        statement="Use the old cyan output profile.",
    )
    _approve(vault, asset_id)

    forgotten = _json(
        "knowledge",
        "forget",
        "--vault",
        str(vault),
        "--asset-id",
        asset_id,
        "--reason",
        "The operator explicitly removed this obsolete preference.",
        "--confirm",
    )
    repeated = _json(
        "knowledge",
        "forget",
        "--vault",
        str(vault),
        "--asset-id",
        asset_id,
        "--reason",
        "Confirm the obsolete preference remains forgotten.",
        "--confirm",
    )

    assert forgotten["identity_model"] == "legacy-unbound"
    assert forgotten["current_retrieval_eligible"] is False
    assert forgotten["history_retained"] is True
    assert repeated["already_inactive"] is True
