from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.hosts import v013_task_domain_driver as driver
from benchmarks.v013 import evidence_wiki_candidate
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.util import canonical_json, sha256_bytes

_TASK = "Exact Source Revision bytes, quotes, locators, and version metadata remain evidence."


def _seed() -> dict:
    return driver.build_task_seed(
        "living_wiki",
        task=_TASK,
        knowledge_id="knowledge_111111111111111111111111",
        knowledge_revision_id="knowledgerev_222222222222222222222222",
        source_id="source_333333333333333333333333",
        source_revision_id="sourcerev_444444444444444444444444",
        fragment_id="fragment_555555555555555555555555",
        locator="section:1;paragraphs:2-2",
        quote_sha256="1" * 64,
        content_sha256="2" * 64,
        expected_gaps=({"code": "duty_unresolved", "duty": "limitation"},),
    )


def _prepared_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    root = tmp_path / "candidate-vault"

    class _Keeper:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> str:
            root.mkdir()
            return str(root)

        def __exit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr(evidence_wiki_candidate.tempfile, "TemporaryDirectory", _Keeper)
    source = {
        "schema_version": "deeplaw.evidence-wiki-development-source/v1",
        "case_id": "s075-development-fixture",
        "source_filename": "canary.md",
        "source_text": f"# Canary\n\n{_TASK}",
        "agent_interpretation": {
            "title": "Evidence claim",
            "body": "A bounded source-bound interpretation.",
            "semantic_key": "evidence-wiki:agent-interpretation",
        },
        "human_task": "Quote the exact source evidence.",
        "agent_task": "Quote the exact source evidence.",
    }
    candidate = evidence_wiki_candidate.run_candidate(source)
    with KnowledgeVault(root, read_only=True) as store:
        source_id = store.all_sources()[0]["source_id"]
    source_facts = candidate["source"]
    statement = candidate["statement"]
    return root, driver.build_task_seed(
        "living_wiki",
        task=source["source_text"].split("\n\n", maxsplit=1)[1],
        knowledge_id=statement["knowledge_id"],
        knowledge_revision_id=statement["knowledge_revision_id"],
        source_id=source_id,
        source_revision_id=source_facts["source_revision_id"],
        fragment_id=source_facts["fragment_id"],
        locator=source_facts["locator"],
        quote_sha256=source_facts["fragment_text_sha256"],
        content_sha256=source_facts["content_sha256"],
        expected_gaps=({"code": "duty_unresolved", "duty": "limitation"},),
    )


def test_public_api_is_bounded_and_seed_is_development_only() -> None:
    seed = _seed()
    assert not hasattr(driver, "run_task_domain_driver")
    assert seed["formal_admission"] is False
    assert seed["claim_eligible"] is False
    assert set(seed["source_policy"]) == {"scope", "sensitivity"}
    assert seed["driver_kind"] == "task_domain_driver"


def test_wrong_seed_scope_and_quote_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    wrong_scope = copy.deepcopy(seed)
    wrong_scope["source_policy"]["scope"] = "personal"
    with pytest.raises(driver.TaskDomainDriverError):
        driver.validate_task_seed(wrong_scope)

    root, actual = _prepared_vault(tmp_path, monkeypatch)
    wrong_quote = copy.deepcopy(actual)
    wrong_quote["expected"]["include"]["quote_sha256"] = "a" * 64
    with pytest.raises(driver.TaskDomainDriverError):
        driver.collect_task_domain(
            wrong_quote,
            vault=root,
            deeplaw_executable=sys.executable,
            deeplaw_prefix=("-m", "deeplaw"),
        )


def test_capsule_internal_trace_is_rejected() -> None:
    seed = _seed()
    include = seed["expected"]["include"]
    quote = "Exact source quote."
    include["quote_sha256"] = sha256_bytes(quote.encode("utf-8"))
    reference = {
        "source_revision_id": include["source_revision_id"],
        "fragment_id": include["fragment_id"],
        "locator": include["locator"],
        "quote_sha256": include["quote_sha256"],
    }
    capsule = {
        "statements": [
            {
                "knowledge_id": include["knowledge_id"],
                "knowledge_revision_id": include["knowledge_revision_id"],
                "authority": "agent_derived",
                "legal_authority": False,
                "verification": "source_bound",
                "source_refs": [reference],
            }
        ],
        "evidence": [
            {
                "source_revision_id": include["source_revision_id"],
                "fragment_id": include["fragment_id"],
                "content_sha256": include["quote_sha256"],
                "excerpt": quote,
                "verification": "verified_source",
                "source_refs": [reference],
            }
        ],
        "gaps": [{"code": "duty_unresolved", "duty": "limitation"}],
        "query_trace": {"audit_head": "must-not-be-delivered"},
    }
    clean = {key: value for key, value in capsule.items() if key != "query_trace"}
    driver._capsule_observation(clean, seed["expected"], operation="context")
    with pytest.raises(driver.TaskDomainDriverError, match="leaked local trace"):
        driver._capsule_observation(capsule, seed["expected"], operation="context")


def test_provider_content_must_match_structured_capsule() -> None:
    capsule = {
        "schema_version": "deeplaw.knowledge-capsule-projection/v1",
        "statements": [],
        "evidence": [],
        "gaps": [],
    }
    provider_text = canonical_json(capsule)
    result = SimpleNamespace(
        structuredContent={"result": {"capsule": capsule}},
        content=[SimpleNamespace(type="text", text=provider_text)],
    )
    byte_size, digest = driver._provider_content(result, capsule, operation="query")
    provider_bytes = provider_text.encode("utf-8")
    assert byte_size == len(provider_bytes)
    assert digest == sha256_bytes(provider_bytes)

    tampered = SimpleNamespace(
        structuredContent=result.structuredContent,
        content=[SimpleNamespace(type="text", text=provider_text + " ")]
    )
    with pytest.raises(driver.TaskDomainDriverError, match="provider content"):
        driver._provider_content(tampered, capsule, operation="query")

    oversized = {**capsule, "padding": "x" * 65_536}
    oversized_result = SimpleNamespace(
        content=[SimpleNamespace(type="text", text=canonical_json(oversized))]
    )
    with pytest.raises(driver.TaskDomainDriverError, match="byte bound"):
        driver._provider_content(oversized_result, oversized, operation="query")


def test_capsule_exact_quote_is_recomputed_from_returned_text() -> None:
    seed = _seed()
    include = seed["expected"]["include"]
    quote = "Exact source quote."
    include["quote_sha256"] = sha256_bytes(quote.encode("utf-8"))
    reference = {
        key: include[key]
        for key in ("source_revision_id", "fragment_id", "locator", "quote_sha256")
    }
    capsule = {
        "statements": [{
            **{key: include[key] for key in (
                "knowledge_id", "knowledge_revision_id", "authority",
                "legal_authority", "verification",
            )},
            "source_refs": [reference],
        }],
        "evidence": [{
            "source_revision_id": include["source_revision_id"],
            "fragment_id": include["fragment_id"],
            "content_sha256": include["quote_sha256"],
            "excerpt": quote,
            "verification": "verified_source",
            "source_refs": [reference],
        }],
        "gaps": seed["expected"]["gaps"],
    }
    driver._capsule_observation(capsule, seed["expected"], operation="context")
    for field, value in (
        ("excerpt", "Altered quote."),
        ("fragment_id", "fragment_000000000000000000000000"),
        ("content_sha256", "0" * 64),
    ):
        changed = copy.deepcopy(capsule)
        changed["evidence"][0][field] = value
        with pytest.raises(driver.TaskDomainDriverError, match="binding"):
            driver._capsule_observation(changed, seed["expected"], operation="context")


def test_collect_records_bounded_call_facts_and_rejects_ledger_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _seed()
    vault = tmp_path / "vault"
    vault.mkdir()
    identity = {"audit_head": "a" * 64, "legacy_audit_head": "b" * 64}
    monkeypatch.setattr(driver, "_read_identity", lambda _vault: dict(identity))
    monkeypatch.setattr(
        driver,
        "_source_observation",
        lambda _seed, _vault: {"source_id": "source_333333333333333333333333"},
    )
    monkeypatch.setattr(
        driver,
        "_wiki_observation",
        lambda _seed, _vault: {"wiki_path": "wiki/claims/fixture.md"},
    )
    async def fake_public_v7_reads(*_args, **_kwargs):
        return (
            {"selected_knowledge_ids": [seed["expected"]["include"]["knowledge_id"]]},
            {"selected_knowledge_ids": [seed["expected"]["include"]["knowledge_id"]]},
            {
                "input_audit_head": identity["audit_head"],
                "input_legacy_audit_head": identity["legacy_audit_head"],
                "selected_statement_ids": [],
                "query_plan_sha256": "c" * 64,
                "receipt_sha256": "d" * 64,
            },
        )

    monkeypatch.setattr(driver, "_public_v7_reads", fake_public_v7_reads)
    result = driver.collect_task_domain(seed, vault=vault)
    assert result["caller"] == "task_domain_driver"
    assert result["driver_kind"] == "task_domain_driver"

    snapshots = iter(
        (
            identity,
            {"audit_head": "e" * 64, "legacy_audit_head": identity["legacy_audit_head"]},
        )
    )
    monkeypatch.setattr(driver, "_read_identity", lambda _vault: next(snapshots))
    with pytest.raises(driver.TaskDomainDriverError, match="Ledger"):
        driver.collect_task_domain(seed, vault=vault)


def test_seed_file_has_a_bounded_read(tmp_path: Path) -> None:
    path = tmp_path / "seed.json"
    path.write_text(
        json.dumps({"oversized": "x" * (driver._MAX_SEED_BYTES + 1)}),
        encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            driver,
            "strict_json_loads",
            lambda _raw: pytest.fail("oversized seed must be rejected before parsing"),
        )
        with pytest.raises(driver.TaskDomainDriverError, match="exceeds"):
            driver._load_seed(path)


def test_real_closed_stdio_positive_and_explicit_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, seed = _prepared_vault(tmp_path, monkeypatch)
    result = driver.collect_task_domain(
        seed,
        vault=root,
        deeplaw_executable=sys.executable,
        deeplaw_prefix=("-m", "deeplaw"),
    )
    assert result["status"] == "executed"
    assert result["formal_admission"] is False
    assert result["claim_eligible"] is False
    for name in ("source_read", "wiki_read", "query", "context"):
        calls = result["observations"][name]["calls"]
        assert calls
        for call in calls:
            assert call["caller"] == "task_domain_driver"
            request_bytes = canonical_json(call["request"]).encode("utf-8")
            projection_bytes = canonical_json(call["projection"]).encode("utf-8")
            assert call["request_byte_size"] == len(request_bytes)
            assert call["request_sha256"] == sha256_bytes(request_bytes)
            assert call["response_kind"] == "bounded_projection"
            assert call["projection_byte_size"] == len(projection_bytes)
            assert call["projection_sha256"] == sha256_bytes(projection_bytes)
            if call["action"] in {"query", "context"}:
                observation = result["observations"][call["action"]]
                assert call["structured_response_kind"] == "canonical_json"
                assert call["structured_response_byte_size"] > 0
                assert len(call["structured_response_sha256"]) == 64
                assert call["provider_content_kind"] == "mcp_text_content"
                assert call["provider_content_bytes"] == call["projection"][
                    "provider_content_bytes"
                ]
                assert call["provider_content_sha256"] == observation[
                    "provider_content_sha256"
                ]
    assert result["observations"]["query"]["calls"][0]["action"] == "query"
    assert result["observations"]["context"]["calls"][0]["action"] == "context"
    assert "duty:rename_move" in result["not_executed"]
    assert "duty:external_edit_reconcile" in result["not_executed"]
    assert "native_host_events" in result["not_executed"]
    assert result["observations"]["ledger"]["unchanged"] is True
