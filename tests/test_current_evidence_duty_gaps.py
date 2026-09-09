"""Current public retrieval preserves declared evidence gaps and denied scope.

The missing annex is declared by the synthetic compiler input, not inferred by
a keyword classifier. This does not establish legal or semantic completeness.
"""
from deeplaw.compilation.coordinator import CompilationCoordinator
from deeplaw.evidence import build_input_set_sha256
from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore
from deeplaw.knowledge_mcp_server import handle_knowledge_support
from deeplaw.retrieval import PurposeAwareRetrievalService
from deeplaw.util import canonical_json, sha256_bytes

from . import test_v013_statement_evidence as fixture


def test_current_query_preserves_missing_definition_annex_and_denied_evidence(
    tmp_path, monkeypatch,
):
    original = fixture._statement
    declared_gap = {
        "gap_id": "missing-archive-annex",
        "reason": "兰花档案规则引用的附件甲未提供；术语定义和例外范围尚未核实。",
    }

    def statement_with_gap(**kwargs):
        statement = original(**kwargs)
        statement["gaps"] = [declared_gap]
        statement["input_set_sha256"] = build_input_set_sha256(**{
            key: statement[key] for key in (
                "source_refs", "knowledge_revision_refs", "relation_revision_refs",
                "valid_from", "valid_to", "statement_type", "support_status",
                "limitation", "gaps",
            )
        })
        return statement

    monkeypatch.setattr(fixture, "_statement", statement_with_gap)
    root, grant, run, _, statement = fixture._prepared_v3_run(
        tmp_path,
        source_text="# 兰花档案规则\n兰花档案应使用琥珀标签；术语和除外条件见附件甲。",
    )
    CompilationCoordinator(root).commit(
        grant_id=grant, compilation_run_id=run, confirm_no_case_data=True,
    )
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        before = (store.audit_head, store.legacy_audit_head)
    service = PurposeAwareRetrievalService(root)
    arguments = dict(
        purpose="verify", applicable_duties=("definition", "exception", "source_evidence"),
    )
    result = service.query("兰花档案琥珀标签定义与例外", **arguments)
    assert result["query_plan"]["schema_version"] == "deeplaw.knowledge-query-plan/v7"
    assert result["statements"]
    assert any(gap["message"] == declared_gap["reason"] for gap in result["gaps"])
    duties = {item["duty"]: item for item in result["query_plan"]["duties"]}
    assert duties["definition"]["status"] == "unresolved"
    assert duties["exception"]["status"] == "unresolved"
    assert duties["source_evidence"]["status"] == "satisfied"
    for evidence in result["evidence"]:
        assert sha256_bytes(evidence["excerpt"].encode()) == evidence["content_sha256"]
    provider = handle_knowledge_support(
        operation="query", query="兰花档案琥珀标签定义与例外", vault_path=root, **arguments,
    )
    assert provider["schema_version"] == "deeplaw.knowledge-support-output/v8"
    assert any(
        gap["message"] == declared_gap["reason"]
        for gap in provider["result"]["capsule"]["gaps"]
    )

    denied = service.query(
        "兰花档案琥珀标签定义与例外", max_sensitivity="public", **arguments,
    )
    assert denied["statements"] == denied["evidence"] == []
    assert any(gap["duty"] == "source_evidence" for gap in denied["gaps"])
    public = canonical_json(denied["capsule"])
    for reference in statement["source_refs"]:
        assert reference["source_revision_id"] not in public
        assert reference["fragment_id"] not in public
    assert declared_gap["reason"] not in public
    assert "rejections" not in public and "rejected_count" not in public
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert (store.audit_head, store.legacy_audit_head) == before


def test_large_projection_never_truncates_hash_bound_source_evidence(tmp_path, monkeypatch):
    original = fixture._statement

    def statement_with_long_gaps(**kwargs):
        statement = original(**kwargs)
        statement["gaps"] = [
            {"gap_id": f"annex-{number}", "reason": ("缺失附件证据" + str(number)) * 280}
            for number in range(8)
        ]
        statement["input_set_sha256"] = build_input_set_sha256(**{
            key: statement[key] for key in (
                "source_refs", "knowledge_revision_refs", "relation_revision_refs",
                "valid_from", "valid_to", "statement_type", "support_status",
                "limitation", "gaps",
            )
        })
        return statement

    monkeypatch.setattr(fixture, "_statement", statement_with_long_gaps)
    passage = "兰花档案" * 800
    root, grant, run, *_ = fixture._prepared_v3_run(
        tmp_path, source_text="# Source\n" + passage,
    )
    CompilationCoordinator(root).commit(
        grant_id=grant, compilation_run_id=run, confirm_no_case_data=True,
    )
    result = PurposeAwareRetrievalService(root).query(
        "兰花档案", purpose="verify", max_tokens=12000, max_chars=10000,
    )
    assert len(result["gaps"]) >= 8
    # The large UTF-8 payload reaches projection fitting. Only derived summaries
    # may shrink; local and provider evidence must retain the complete passage.
    assert len(result["capsule"]["statements"][0]["statement_text"]) == 512
    for evidence in (result["evidence"], result["capsule"]["evidence"]):
        assert evidence and evidence[0]["excerpt"] == passage
        assert sha256_bytes(evidence[0]["excerpt"].encode()) == evidence[0]["content_sha256"]
    assert len(canonical_json(result["capsule"]).encode()) <= 65536
    with AutonomousKnowledgeStore(root, read_only=True) as store:
        assert store.verify()["valid"]
