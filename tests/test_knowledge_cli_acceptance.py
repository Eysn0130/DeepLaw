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
