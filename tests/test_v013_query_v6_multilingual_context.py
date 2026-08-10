from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from benchmarks.semantic.deterministic_gold_agent import compile_source
from deeplaw.api import KnowledgeOS
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.util import (
    canonical_json,
    query_identity_anchor_match,
    query_target_anchors,
)

REPOSITORY = Path(__file__).resolve().parents[1]
_FIXTURES = REPOSITORY / "benchmarks" / "semantic" / "fixtures"


def _run_cli(prefix: list[str], *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [*prefix, "knowledge", "--format", "json", *arguments],
        cwd=REPOSITORY,
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return value


def _semantic_vault(
    tmp_path: Path,
    *,
    source_keys: tuple[str, ...],
) -> tuple[Path, list[str], dict[str, dict[str, str]], str]:
    root = tmp_path / "vault"
    prefix = [sys.executable, "-m", "deeplaw"]
    _run_cli(
        prefix,
        "init",
        "--vault",
        str(root),
        "--name",
        "v013 multilingual context",
        "--scope",
        "project",
    )
    filenames = {
        "retention-a": "04-retention-policy-a.md",
        "retention-b": "05-retention-policy-b.md",
        "prompt-injection": "08-injection.md",
    }
    sources: dict[str, dict[str, str]] = {}
    for source_key in source_keys:
        source = _run_cli(
            prefix,
            "source",
            "add",
            "--vault",
            str(root),
            "--source",
            str(_FIXTURES / filenames[source_key]),
            "--source-kind",
            "document",
            "--title",
            source_key,
            "--trust",
            "user_provided",
            "--sensitivity",
            "public",
            "--confirm-no-case-data",
        )["source"]
        manifest = _run_cli(
            prefix,
            "review",
            "manifest",
            "--vault",
            str(root),
            "--source-id",
            source["source_id"],
        )
        approval_args = [
            "review",
            "approve-source",
            "--vault",
            str(root),
            "--source-id",
            source["source_id"],
            "--review-manifest-sha256",
            manifest["review_manifest_sha256"],
            "--reviewer-id",
            "v013-query-test",
            "--reason",
            "Approve the public multilingual regression fixture.",
            "--confirm-reviewed",
        ]
        if source_key == "prompt-injection":
            approval_args.append("--confirm-quarantine")
        approval = _run_cli(prefix, *approval_args)
        assert approval["source_activated"] is True
        sources[source_key] = source
    grant = _run_cli(
        prefix,
        "sink",
        "enable",
        "--vault",
        str(root),
        "--writer-id",
        "v013-query-test",
        "--profile",
        "semantic-compiler",
        "--scope",
        "project",
        "--max-sensitivity",
        "public",
        "--max-request-bytes",
        "131072",
    )
    prior_runs: dict[str, dict[str, str]] = {}
    for source_key in source_keys:
        report = compile_source(
            vault=root,
            grant_id=grant["grant_id"],
            source_key=source_key,
            source_revision_id=sources[source_key]["source_revision_id"],
            prior_runs=prior_runs,
        )
        prior_runs[source_key] = {
            "source_revision_id": sources[source_key]["source_revision_id"],
            "compilation_run_id": report["compilation_run_id"],
        }
    return root, prefix, sources, grant["grant_id"]


def test_v6_multilingual_compound_anchor_keeps_independent_events() -> None:
    task = "Atlas 审阅完成 2025-06-01；Atlas 计划发布 2025-07-01；Atlas Protocol"
    anchors, truncated = query_target_anchors(task)
    assert anchors == ("atlas protocol", "atlas")
    assert truncated is False
    assert any(
        query_identity_anchor_match(anchor, "Atlas review completed on 2025-06-01")
        for anchor in anchors
    )
    assert any(
        query_identity_anchor_match(anchor, "Atlas publication scheduled on 2025-07-01")
        for anchor in anchors
    )
    assert query_identity_anchor_match("atlas protocol", "Atlas Protocol")


def test_v6_multilingual_context_public_seams(tmp_path: Path) -> None:
    root, prefix, _sources, _grant_id = _semantic_vault(
        tmp_path,
        source_keys=("retention-a", "retention-b", "prompt-injection"),
    )
    task = "比较诊断保留期限。"
    with KnowledgeOS.open(root) as knowledge_os:
        python_context = knowledge_os.context.compile(
            task=task,
            purpose="verify",
            scope="project",
            max_sensitivity="public",
            confirm_no_case_data=True,
        )
    cli_context = _run_cli(
        prefix,
        "context",
        "--vault",
        str(root),
        "--task",
        task,
        "--purpose",
        "verify",
        "--confirm-no-case-data",
    )
    mcp_context = handle_knowledge_support(
        operation="context",
        task=task,
        purpose="verify",
        limit=8,
        max_chars=8_000,
        max_tokens=6_000,
        max_sources=12,
        scope="project",
        max_sensitivity="public",
        confirm_no_case_data=True,
        vault_path=root,
    )
    mcp_capsule = mcp_context["result"]["capsule"]
    for value in (python_context, cli_context, mcp_capsule):
        serialized = canonical_json(value)
        assert "Both policies apply" in serialized
        assert "Policy A requires 30 days" in serialized
        assert "Policy B requires 60 days" in serialized
    assert python_context["query_plan"]["selection"]["statement_ids"]
    assert cli_context["query_plan"]["selection"]["statement_ids"]
    for value in (python_context, cli_context):
        expansion = value["query_plan"]["query_expansion"]
        assert expansion["profile"] == "deeplaw-deterministic-query-expansion/2"
        assert expansion["term_count"] <= 24
        assert expansion["terms_truncated"] is False
        assert expansion["authority_changed"] is False
        assert expansion["stored_evidence_changed"] is False
    assert mcp_capsule["selected_statement_count"] >= 3
    assert mcp_context["schema_version"] == "deeplaw.knowledge-support-output/v6"


def test_v6_multilingual_exact_badge_quote_keeps_source_evidence(tmp_path: Path) -> None:
    root, _prefix, _sources, _grant_id = _semantic_vault(
        tmp_path,
        source_keys=("prompt-injection",),
    )
    task = "引用示例验证徽章所述的精确颜色。"
    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task=task,
            purpose="quote",
            scope="project",
            max_sensitivity="public",
            confirm_no_case_data=True,
        )
    serialized = canonical_json(context)
    assert "The example verification badge is blue" in serialized
    assert "blue verification badge" in serialized
    assert context["evidence"]
    assert context["evidence"][0]["verification"] == "verified_source"


def test_v6_freshness_policy_designator_is_fail_closed_after_withdrawal(
    tmp_path: Path,
) -> None:
    root, _prefix, sources, grant_id = _semantic_vault(
        tmp_path,
        source_keys=("retention-a", "retention-b"),
    )
    from deeplaw.compilation.coordinator import CompilationCoordinator
    from deeplaw.knowledge_store import KnowledgeVault

    with KnowledgeVault(root, read_only=False) as vault:
        vault.remove_source(
            sources["retention-a"]["source_id"],
            reason="v013 freshness regression",
            confirm=True,
        )
    coordinator = CompilationCoordinator(root)
    report = coordinator.refresh(
        grant_id=grant_id,
        source_revision_id=sources["retention-a"]["source_revision_id"],
        confirm_no_case_data=True,
    )
    assert report["source_status"] == "removed"
    task = "Policy A 当前支持什么保留期限\uFF1F"
    with KnowledgeOS.open(root) as knowledge_os:
        context = knowledge_os.context.compile(
            task=task,
            purpose="freshness_check",
            scope="project",
            max_sensitivity="public",
            confirm_no_case_data=True,
        )
    query_result = PurposeAwareRetrievalService(root).query(
        task,
        purpose="freshness_check",
        scope="project",
        max_sensitivity="public",
        query_plan_version="6",
    )
    assert query_result["query_plan"]["query_expansion"]["profile"].endswith("/2")
    assert any(
        item["reason"].startswith("freshness_policy_designator_")
        for item in query_result["local_audit"]["rejections"]
    )
    assert not any(
        "Policy B" in str(item) or "Retention policy comparison" in str(item)
        for item in context["statements"]
    )
    assert any(gap["code"] == "stale_knowledge" for gap in context["gaps"])
