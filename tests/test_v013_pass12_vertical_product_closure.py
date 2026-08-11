from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

import deeplaw.host_connect as host_connect_module
from benchmarks.hosts.deterministic_fake_agent import compile_with_fake_mcp_agent
from deeplaw.compilation.models import SEMANTIC_COMPILER_GRANT_OPERATIONS
from deeplaw.host_connect import build_host_connect_plan
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault

from .helpers import write_docx

_CLI_TIMEOUT_SECONDS = 120 if sys.platform == "win32" else 30


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deeplaw", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=_CLI_TIMEOUT_SECONDS,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _write_multiformat_source(path: Path) -> None:
    statement = "The synthetic retention duty is exactly thirty days and remains bounded."
    if path.suffix == ".md":
        path.write_text(f"# Retention\n{statement}\n", encoding="utf-8")
    elif path.suffix == ".html":
        path.write_text(
            f"<!doctype html><html><body><h1>Retention</h1><p>{statement}</p></body></html>",
            encoding="utf-8",
        )
    elif path.suffix == ".docx":
        write_docx(path, ["Retention", statement])
    elif path.suffix == ".pdf":
        document = canvas.Canvas(str(path))
        document.drawString(72, 760, "Retention")
        document.drawString(72, 730, statement)
        document.save()
    else:  # pragma: no cover - the parametrization is closed below
        raise AssertionError(f"unsupported test fixture: {path.suffix}")


def test_root_and_nested_source_routes_share_source_only_v6_gap(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    source.write_text(
        "# Retention\nThe project retention window is thirty days.\n",
        encoding="utf-8",
    )
    routes = (
        ("root", tmp_path / "root-vault", tmp_path / "project"),
        ("nested", tmp_path / "nested-vault", None),
    )

    for route, vault, project_root in routes:
        if route == "root":
            assert project_root is not None
            project_root.mkdir()
            initialized = _json(
                _run_cli(
                    "init",
                    str(vault),
                    "--project-root",
                    str(project_root),
                )
            )
            added = _json(
                _run_cli(
                    "add",
                    str(source),
                    "--vault",
                    str(vault),
                    "--confirm-no-case-data",
                    "--format",
                    "json",
                )
            )
        else:
            initialized = _json(
                _run_cli(
                    "knowledge",
                    "init",
                    "--vault",
                    str(vault),
                    "--name",
                    "nested",
                )
            )
            added = _json(
                _run_cli(
                    "knowledge",
                    "source",
                    "add",
                    "--vault",
                    str(vault),
                    "--source",
                    str(source),
                    "--confirm-no-case-data",
                )
            )

        assert initialized["autonomous_core"]["verification"]["valid"] is True
        assert added["source_knowledge_status"]["state"] == "compilation_required"
        assert added["source_knowledge_status"]["source_registered"] is True
        context = _json(
            _run_cli(
                "knowledge",
                "context",
                "--vault",
                str(vault),
                "--task",
                "Verify the project retention window.",
                "--purpose",
                "verify",
                "--confirm-no-case-data",
            )
        )
        assert context["schema_version"] == "deeplaw.knowledge-capsule/v3"
        assert context["statements"] == []
        assert "uncompiled_source" in {gap["code"] for gap in context["gaps"]}
        assert context["provider_capsule"]["delivery"]["provider_content_bytes"] <= 65_536


@pytest.mark.parametrize("suffix", [".md", ".html", ".docx", ".pdf"])
def test_public_source_only_gap_preserves_multiformat_evidence(
    tmp_path: Path,
    suffix: str,
) -> None:
    vault = tmp_path / f"vault-{suffix[1:]}"
    source = tmp_path / f"source{suffix}"
    _write_multiformat_source(source)
    original_bytes = source.read_bytes()
    _json(_run_cli("knowledge", "init", "--vault", str(vault), "--name", "formats"))
    added = _json(
        _run_cli(
            "knowledge",
            "source",
            "add",
            "--vault",
            str(vault),
            "--source",
            str(source),
            "--confirm-no-case-data",
        )
    )
    assert added["source_knowledge_status"]["state"] == "compilation_required"
    source_id = added["source"]["source_id"]
    with KnowledgeVault(vault, read_only=True) as legacy:
        assert legacy.source_file_path(source_id).read_bytes() == original_bytes
        assert legacy.verify_integrity()["valid"] is True

    context = _json(
        _run_cli(
            "knowledge",
            "context",
            "--vault",
            str(vault),
            "--task",
            "Verify the synthetic retention duty.",
            "--purpose",
            "verify",
            "--confirm-no-case-data",
        )
    )
    assert context["schema_version"] == "deeplaw.knowledge-capsule/v3"
    assert context["statements"] == []
    assert "uncompiled_source" in {gap["code"] for gap in context["gaps"]}
    assert context["provider_capsule"]["delivery"]["provider_content_bytes"] <= 65_536


def test_host_connect_preflight_calls_source_only_context_seam(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text("# Boundary\nOnly compiled knowledge may answer.\n", encoding="utf-8")
    initialize_knowledge_vault(vault, name="host-source-only", scope="project")
    initialize_autonomous_core(vault)
    with KnowledgeVault(vault, read_only=False) as legacy:
        compile_source(
            legacy,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    with AutonomousKnowledgeStore(vault, read_only=False):
        pass

    plan = build_host_connect_plan(host="codex", vault_path=vault)

    assert plan["preflight"] == {
        "vault_ready": True,
        "canonical_valid": True,
        "autonomous_core_installed": True,
        "schema_core_installed": True,
        "read_seam_callable": True,
        "compiled_knowledge_available": False,
        "source_only_honest_gap_available": True,
        "blocked": False,
    }
    assert plan["context_preflight"]["status"] == "source_only_gap"
    assert "uncompiled_source" in plan["context_preflight"]["gap_codes"]
    assert plan["context_preflight"]["provider_payload_bytes"] <= 65_536
    assert plan["context_preflight"]["write_performed"] is False
    assert plan["context_preflight"]["audit_head_unchanged"] is True


def test_host_connect_fails_closed_when_real_context_seam_is_not_callable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="host-blocked", scope="project")
    initialize_autonomous_core(vault)

    def fail_open(_path: Path) -> None:
        raise RuntimeError("synthetic Context failure")

    monkeypatch.setattr(host_connect_module.KnowledgeOS, "open", staticmethod(fail_open))
    with pytest.raises(RuntimeError, match="Host connect blocked"):
        build_host_connect_plan(host="codex", vault_path=vault)


def test_host_connect_loads_contract_from_installed_package_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed_module = tmp_path / "site-packages" / "deeplaw" / "host_connect.py"
    packaged_contract = installed_module.parent / "contracts" / (
        "host-connect-plan.v1.schema.json"
    )
    packaged_contract.parent.mkdir(parents=True)
    shutil.copy2(
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "host-connect-plan.v1.schema.json",
        packaged_contract,
    )
    installed_module.touch()
    monkeypatch.setattr(host_connect_module, "__file__", str(installed_module))

    contract = host_connect_module._contract()

    assert contract["$id"].endswith("host-connect-plan.v1.schema.json")


def test_owner_forget_routes_explicit_asset_knowledge_and_source_targets(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text(
        "# Forget boundary\nKnowledge may be forgotten without deleting source evidence.\n",
        encoding="utf-8",
    )
    initialize_knowledge_vault(vault, name="target-aware-forget", scope="project")
    initialize_autonomous_core(vault)
    with KnowledgeVault(vault, read_only=False) as legacy:
        compiled = compile_source(
            legacy,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        source_id = compiled["source"]["source_id"]
        source_revision_id = compiled["identity"]["source_revision_id"]
        asset_id = compiled["asset_ids"][0]
        stored_source = legacy.source_file_path(source_id)
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="pass12-owner",
            operations=("remember", "forget"),
        )["grant_id"]
        remembered = store.remember(
            grant_id=grant_id,
            idempotency_key="pass12-remember",
            title="Forget boundary",
            body="Knowledge may be forgotten without deleting source evidence.",
            kind="claim",
            source_refs=[{"source_revision_id": source_revision_id}],
            confirm_no_case_data=True,
        )
        wrong_scope_grant_id = store.enable_grant(
            writer_id="pass12-wrong-scope",
            allowed_scope="personal",
            operations=("forget",),
        )["grant_id"]
        public_only_grant_id = store.enable_grant(
            writer_id="pass12-public-only",
            max_sensitivity="public",
            operations=("forget",),
        )["grant_id"]

    asset_receipt = _json(
        _run_cli(
            "knowledge",
            "forget",
            "--vault",
            str(vault),
            "--asset-id",
            asset_id,
            "--reason",
            "Remove the legacy proposal from current recall.",
            "--confirm",
        )
    )
    assert asset_receipt["target_type"] == "legacy_asset"
    assert asset_receipt["current_retrieval_eligible"] is False

    for grant, key in (
        (wrong_scope_grant_id, "pass12-forget-wrong-scope"),
        (public_only_grant_id, "pass12-forget-wrong-sensitivity"),
    ):
        denied = _run_cli(
            "knowledge",
            "forget",
            "--vault",
            str(vault),
            "--knowledge-id",
            remembered["knowledge_id"],
            "--expected-revision-id",
            remembered["revision_id"],
            "--grant-id",
            grant,
            "--idempotency-key",
            key,
            "--reason",
            "A mismatched grant must fail closed.",
            "--confirm",
            "--confirm-no-case-data",
        )
        assert denied.returncode != 0
        assert "unavailable" in denied.stderr

    knowledge_receipt = _json(
        _run_cli(
            "knowledge",
            "forget",
            "--vault",
            str(vault),
            "--knowledge-id",
            remembered["knowledge_id"],
            "--expected-revision-id",
            remembered["revision_id"],
            "--grant-id",
            grant_id,
            "--idempotency-key",
            "pass12-forget",
            "--reason",
            "Owner requested governed Knowledge Object forgetting.",
            "--confirm",
            "--confirm-no-case-data",
        )
    )
    assert knowledge_receipt["target_type"] == "autonomous_knowledge"
    assert knowledge_receipt["knowledge_id"] == remembered["knowledge_id"]
    assert knowledge_receipt["lifecycle"] == "forgotten"
    replayed_knowledge_receipt = _json(
        _run_cli(
            "knowledge",
            "forget",
            "--vault",
            str(vault),
            "--knowledge-id",
            remembered["knowledge_id"],
            "--expected-revision-id",
            remembered["revision_id"],
            "--grant-id",
            grant_id,
            "--idempotency-key",
            "pass12-forget",
            "--reason",
            "Owner requested governed Knowledge Object forgetting.",
            "--confirm",
            "--confirm-no-case-data",
        )
    )
    assert replayed_knowledge_receipt["revision_id"] == knowledge_receipt["revision_id"]
    assert replayed_knowledge_receipt["idempotent_replay"] is True
    stale = _run_cli(
        "knowledge",
        "forget",
        "--vault",
        str(vault),
        "--knowledge-id",
        remembered["knowledge_id"],
        "--expected-revision-id",
        remembered["revision_id"],
        "--grant-id",
        grant_id,
        "--idempotency-key",
        "pass12-forget-stale",
        "--reason",
        "A stale revision must fail closed.",
        "--confirm",
        "--confirm-no-case-data",
    )
    assert stale.returncode != 0
    assert "compare-and-swap conflict" in stale.stderr
    assert stored_source.is_file()
    with KnowledgeVault(vault, read_only=True) as legacy:
        assert legacy.source_info(source_id)["status"] == "pending"
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.recall(remembered["knowledge_id"])["results"] == []
        assert store.verify()["valid"] is True

    source_receipt = _json(
        _run_cli(
            "knowledge",
            "forget",
            "--vault",
            str(vault),
            "--source-revision-id",
            source_revision_id,
            "--reason",
            "Owner removed this exact Source Revision from current admission.",
            "--confirm",
        )
    )
    assert source_receipt["target_type"] == "source_revision"
    assert source_receipt["source_revision_id"] == source_revision_id
    assert stored_source.is_file()
    with KnowledgeVault(vault, read_only=True) as legacy:
        assert legacy.source_info(source_id)["status"] == "removed"
        assert legacy.verify_integrity()["valid"] is True


def test_compile_handoff_keeps_read_and_sink_boundaries_explicit(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text(
        "# Handoff\nThe host must preserve the read and write boundary.\n",
        encoding="utf-8",
    )
    initialize_knowledge_vault(vault, name="compile-handoff", scope="project")
    initialize_autonomous_core(vault)
    with KnowledgeVault(vault, read_only=False) as legacy:
        compiled = compile_source(
            legacy,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        source_revision_id = compiled["identity"]["source_revision_id"]
    with AutonomousKnowledgeStore(vault, read_only=False):
        pass

    handoff = _json(
        _run_cli(
            "knowledge",
            "compile",
            "handoff",
            "--vault",
            str(vault),
            "--source-revision-id",
            source_revision_id,
        )
    )

    assert handoff["schema_version"] == "deeplaw.compilation-handoff/v1"
    assert handoff["source_revision_id"] == source_revision_id
    assert handoff["source_status"] == "compilation_required"
    assert handoff["compiler_profile"]["compiler_profile_version"] == "3"
    assert handoff["boundaries"] == {
        "read_leaf": "knowledge_support",
        "write_leaf": "knowledge_sink",
        "grant_required": True,
        "grant_included": False,
        "model_invoked": False,
        "write_performed": False,
    }
    assert handoff["sink_operations"] == [
        "begin_compilation",
        "stage_semantic_observations",
        "freeze_semantic_inventory",
        "finalize_semantic_compilation",
        "validate_compilation",
        "commit_compilation",
        "resume_compilation",
        "abort_compilation",
    ]
    assert [step["leaf"] for step in handoff["steps"]] == [
        "knowledge_support",
        "knowledge_sink",
        "knowledge_support",
        "knowledge_sink",
        "knowledge_sink",
        "knowledge_support",
        "knowledge_sink",
        "knowledge_sink",
        "knowledge_sink",
        "knowledge_sink",
        "knowledge_support",
    ]
    assert handoff["write_performed"] is False


def test_deterministic_fake_host_uses_split_public_compile_seams(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text(
        "# Public compile\nThe release retention window is exactly thirty days.\n",
        encoding="utf-8",
    )
    initialize_knowledge_vault(vault, name="public-fake-host", scope="project")
    initialize_autonomous_core(vault)
    with KnowledgeVault(vault, read_only=False) as legacy:
        compiled = compile_source(
            legacy,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        review = legacy.source_review_manifest(compiled["source"]["source_id"])
        legacy.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=review["review_manifest_sha256"],
            reviewer_id="pass12-deterministic-owner",
            review_reason="Activate the exact synthetic Source Revision.",
        )
    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="deeplaw-deterministic-fake-agent",
            operations=SEMANTIC_COMPILER_GRANT_OPERATIONS,
        )["grant_id"]

    report = compile_with_fake_mcp_agent(
        vault=vault,
        grant_id=grant_id,
        source_revision_id=compiled["identity"]["source_revision_id"],
    )

    assert report["status"] == "succeeded"
    assert report["schema_version"] == (
        "deeplaw.deterministic-fake-mcp-agent-compile/v1"
    )
    assert report["semantic_status"] == "partial"
    assert report["read_leaf"] == "knowledge_support"
    assert report["write_leaf"] == "knowledge_sink"
    assert report["compiler_profile_version"] == "3"
    assert report["query_plan_version"] == "6"
    assert report["compiled_result_count"] > 0
    assert report["provider_payload_bytes"] <= 65_536
    assert report["verification_valid"] is True
    assert report["network_used"] is False
    assert report["external_credentials_used"] is False

    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        audit_after_compile = store.audit_head

    context = handle_knowledge_support(
        operation="context",
        task="Verify the exact release retention window.",
        purpose="verify",
        query_plan_version="6",
        confirm_no_case_data=True,
        vault_path=vault,
    )["result"]
    matching = [
        statement
        for statement in context["capsule"]["statements"]
        if statement["statement_text"]
        == "The release retention window is exactly thirty days."
    ]
    assert len(matching) == 1
    claim_id = matching[0]["knowledge_id"]
    claim_revision_id = matching[0]["knowledge_revision_id"]
    source_ref = matching[0]["source_refs"][0]
    assert source_ref["source_revision_id"] == compiled["identity"][
        "source_revision_id"
    ]
    assert source_ref["fragment_id"].startswith("fragment_")
    assert source_ref["locator"]
    page = handle_knowledge_support(
        operation="wiki",
        wiki_action="page",
        knowledge_id=claim_id,
        vault_path=vault,
    )["result"]
    assert claim_id in page["content"]
    assert source_ref["source_revision_id"] in page["content"]
    outlinks = handle_knowledge_support(
        operation="wiki",
        wiki_action="outlinks",
        knowledge_id=claim_id,
        vault_path=vault,
    )["result"]
    assert any(source_ref["source_revision_id"] in link for link in outlinks["links"])
    backlinks = handle_knowledge_support(
        operation="wiki",
        wiki_action="backlinks",
        wiki_path=f"wiki/sources/{source_ref['source_revision_id']}.md",
        vault_path=vault,
    )["result"]
    assert any(claim_id in link for link in backlinks["links"])
    fragment = handle_knowledge_support(
        operation="source",
        source_action="fragment",
        fragment_id=source_ref["fragment_id"],
        vault_path=vault,
    )["result"]["fragment"]
    assert fragment["source_revision_id"] == source_ref["source_revision_id"]
    assert fragment["locator"] == source_ref["locator"]
    assert fragment["text"] == matching[0]["statement_text"]

    source.write_text(
        "# Public compile\nThe release retention window is exactly forty-five days.\n",
        encoding="utf-8",
    )
    successor = _json(
        _run_cli(
            "knowledge",
            "source",
            "update",
            "--vault",
            str(vault),
            "--source-key",
            compiled["identity"]["source_key"],
            "--source",
            str(source),
            "--confirm-no-case-data",
        )
    )
    assert successor["identity"]["source_revision_id"] != source_ref[
        "source_revision_id"
    ]
    successor_manifest = _json(
        _run_cli(
            "knowledge",
            "review",
            "manifest",
            "--vault",
            str(vault),
            "--source-id",
            successor["source"]["source_id"],
        )
    )
    _json(
        _run_cli(
            "knowledge",
            "review",
            "approve-source",
            "--vault",
            str(vault),
            "--source-id",
            successor["source"]["source_id"],
            "--review-manifest-sha256",
            successor_manifest["review_manifest_sha256"],
            "--reviewer-id",
            "pass12-deterministic-owner",
            "--reason",
            "Activate the exact synthetic successor Source Revision.",
            "--confirm-reviewed",
        )
    )
    refreshed = handle_knowledge_sink(
        {
            "operation": "refresh_compilation",
            "idempotency_key": "pass12-refresh-successor",
            "confirm_no_case_data": True,
            "source_revision_id": source_ref["source_revision_id"],
            "replacement_source_revision_id": successor["identity"][
                "source_revision_id"
            ],
        },
        grant_id=grant_id,
        vault_path=vault,
    )["result"]
    assert refreshed["changed_fragment_ids"]
    _json(_run_cli("knowledge", "autonomy", "rebuild", "--vault", str(vault)))

    snapshot = tmp_path / "snapshot"
    created_snapshot = _json(
        _run_cli(
            "knowledge",
            "snapshot",
            "create",
            "--vault",
            str(vault),
            "--output",
            str(snapshot),
        )
    )
    assert created_snapshot["valid"] is True
    assert _json(
        _run_cli("knowledge", "snapshot", "verify", "--snapshot", str(snapshot))
    )["valid"] is True
    restored = _json(
        _run_cli(
            "knowledge",
            "snapshot",
            "restore",
            "--vault",
            str(vault),
            "--snapshot",
            str(snapshot),
            "--confirm",
        )
    )
    assert restored["valid"] is True

    with AutonomousKnowledgeStore(vault, read_only=False) as store:
        forget_grant_id = store.enable_grant(
            writer_id="pass12-forget-owner",
            operations=("forget",),
        )["grant_id"]
        current_claim = store.get_current(claim_id)
    forgotten = _json(
        _run_cli(
            "knowledge",
            "forget",
            "--vault",
            str(vault),
            "--knowledge-id",
            claim_id,
            "--expected-revision-id",
            current_claim["revision_id"],
            "--grant-id",
            forget_grant_id,
            "--idempotency-key",
            "pass12-public-vertical-forget",
            "--reason",
            "Owner removed the exact compiled claim from current recall.",
            "--confirm",
            "--confirm-no-case-data",
        )
    )
    assert forgotten["lifecycle"] == "forgotten"
    _json(_run_cli("knowledge", "autonomy", "rebuild", "--vault", str(vault)))

    for _ in range(2):
        after_forget = handle_knowledge_support(
            operation="context",
            task="Verify the exact thirty-day release retention window.",
            purpose="verify",
            query_plan_version="6",
            confirm_no_case_data=True,
            vault_path=vault,
        )["result"]
        serialized = json.dumps(after_forget, sort_keys=True)
        assert claim_id not in serialized
        assert claim_revision_id not in serialized
        assert "exactly thirty days" not in serialized
    with pytest.raises(KeyError, match="unavailable"):
        handle_knowledge_support(
            operation="wiki",
            wiki_action="page",
            knowledge_id=claim_id,
            vault_path=vault,
        )
    with KnowledgeVault(vault, read_only=True) as legacy:
        assert legacy.source_file_path(compiled["source"]["source_id"]).is_file()
        assert legacy.verify_integrity()["valid"] is True
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        assert store.recall(claim_id)["results"] == []
        verification = store.verify()
        assert verification["valid"] is True
        assert verification["derived_ready"] is True
        assert store.audit_head != audit_after_compile
