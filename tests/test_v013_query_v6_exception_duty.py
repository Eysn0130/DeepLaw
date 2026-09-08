"""Public Query v6 exception-duty regression (development-only).

The source is admitted and compiled by the existing public development fixture
when this test is run by the owner.  These checks are not Human Gold,
qualification evidence, or a legal semantic classifier.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

_REPOSITORY = Path(__file__).resolve().parents[1]
_TASK = "Policy Alpha archive temporary draft exception"
_DUTY = "exception"
_REQUIRED_SOURCE_TEXT = "Policy Alpha does not require 30 days for temporary drafts"
_OLD_DISTRACTOR = (
    "TRANSLATED_DISTRACTOR: 无关 Gamma 只讨论 unrelated translation-keyword material."
)
_NEW_DISTRACTOR = (
    "TRANSLATED_DISTRACTOR: Policy Alpha archive preview color limitation. "
    "Preview colors may differ between displays. "
    "This translated-keyword display note sets no retention rule."
)


def _fixture_module() -> Any:
    path = _REPOSITORY / "tests/test_v013_query_v6_unseen_development.py"
    spec = importlib.util.spec_from_file_location("s111_query_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("development fixture import is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exception_case(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Build one owner-run public fixture with a clearly unrelated limitation."""

    fixture = _fixture_module()
    original_statement = fixture._statement

    def fixture_statement(*, body: str, source_refs: list[dict[str, str]]) -> dict[str, Any]:
        item = original_statement(body=body, source_refs=source_refs)
        if body.startswith("TRANSLATED_DISTRACTOR:"):
            item["statement_type"] = "limitation"
            item["limitation"] = "Preview colors may differ between displays."
            item["input_set_sha256"] = fixture.build_input_set_sha256(
                **{
                    key: item[key]
                    for key in (
                        "source_refs",
                        "knowledge_revision_refs",
                        "relation_revision_refs",
                        "valid_from",
                        "valid_to",
                        "statement_type",
                        "support_status",
                        "limitation",
                        "gaps",
                    )
                }
            )
        return item

    if fixture._SOURCE_V1.count(_OLD_DISTRACTOR) != 1:
        raise AssertionError("development fixture distractor changed unexpectedly")
    source_text = fixture._SOURCE_V1.replace(_OLD_DISTRACTOR, _NEW_DISTRACTOR)
    fixture._statement = fixture_statement
    try:
        case = fixture._build_case(
            tmp_path_factory.mktemp("v013-exception-duty"),
            source_text=source_text,
        )
    finally:
        fixture._statement = original_statement
    case["fixture"] = fixture
    return case


def _ledger_heads(root: Path) -> tuple[str, str]:
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore

    with AutonomousKnowledgeStore(root, read_only=True) as store:
        return store.audit_head, store.legacy_audit_head


def _cli_args(root: Path, *, operation: str, task: str) -> tuple[str, ...]:
    common = (
        "--vault",
        str(root),
        "--purpose",
        "answer",
        "--policy",
        "compiled-first-v1",
        "--scope",
        "project",
        "--max-sensitivity",
        "public",
        "--query-plan-version",
        "6",
        "--applicable-duty",
        _DUTY,
        "--graph-hops",
        "1",
        "--retrieval-mode",
        "hybrid",
    )
    if operation == "query":
        return ("query", *common, "--query", task)
    return (
        "context",
        *common,
        "--task",
        task,
        "--confirm-no-case-data",
    )


def _public_result(
    case: dict[str, Any], *, task: str, surface: str
) -> dict[str, Any]:
    fixture = case["fixture"]
    root = case["root"]
    if surface == "cli-query":
        return fixture._run_cli(
            case["cli_home"], *_cli_args(root, operation="query", task=task)
        )
    if surface == "cli-context":
        return fixture._run_cli(
            case["cli_home"], *_cli_args(root, operation="context", task=task)
        )
    if surface == "mcp-query":
        return fixture.handle_knowledge_support(
            operation="query",
            query=task,
            purpose="answer",
            policy="compiled-first-v1",
            scope="project",
            max_sensitivity="public",
            limit=8,
            max_chars=6_000,
            max_tokens=6_000,
            max_sources=12,
            graph_hops=1,
            retrieval_mode="hybrid",
            query_plan_version="6",
            applicable_duties=[_DUTY],
            vault_path=root,
        )
    if surface == "mcp-context":
        return fixture.handle_knowledge_support(
            operation="context",
            task=task,
            purpose="answer",
            policy="compiled-first-v1",
            scope="project",
            max_sensitivity="public",
            limit=8,
            max_chars=6_000,
            max_tokens=6_000,
            max_sources=12,
            graph_hops=1,
            retrieval_mode="hybrid",
            query_plan_version="6",
            applicable_duties=[_DUTY],
            confirm_no_case_data=True,
            vault_path=root,
        )
    raise AssertionError(f"unknown public surface: {surface}")


def _provider_payload(result: dict[str, Any], *, surface: str) -> dict[str, Any]:
    if surface == "cli-query":
        return result["capsule"]
    if surface == "cli-context":
        return result["provider_capsule"]
    return result["result"]


def _provider_body(result: dict[str, Any], *, surface: str) -> dict[str, Any]:
    payload = _provider_payload(result, surface=surface)
    if surface == "cli-query":
        return payload
    return payload["capsule"]


def _check_public_bounds(
    result: dict[str, Any],
    *,
    surface: str,
    root: Path,
) -> None:
    from deeplaw.util import canonical_json

    payload = _provider_payload(result, surface=surface)
    encoded = canonical_json(payload).encode("utf-8")
    assert len(encoded) <= 65_536
    assert str(root) not in encoded.decode("utf-8")
    if surface.startswith("mcp-"):
        body = _provider_body(result, surface=surface)
        assert "query_plan" not in body
        assert "audit" not in body


def _local_duty(result: dict[str, Any], *, surface: str) -> dict[str, Any]:
    assert surface.startswith("cli-")
    return next(item for item in result["query_plan"]["duties"] if item["duty"] == _DUTY)


def _provider_gap(body: dict[str, Any]) -> bool:
    return any(
        item.get("code") == "duty_unresolved" and item.get("duty") == _DUTY
        for item in body.get("gaps", [])
        if isinstance(item, dict)
    )


def _exact_source_items(
    body: dict[str, Any], *, source_revision_id: str
) -> list[dict[str, Any]]:
    matches = [
        item
        for item in body.get("evidence", [])
        if isinstance(item, dict)
        and _REQUIRED_SOURCE_TEXT in str(item.get("excerpt", ""))
    ]
    for item in matches:
        excerpt = item["excerpt"]
        references = item["source_refs"]
        assert item["source_revision_id"] == source_revision_id
        assert item["content_sha256"] == hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        assert isinstance(references, list) and len(references) == 1
        assert references[0]["source_revision_id"] == source_revision_id
        assert references[0]["fragment_id"] == item["fragment_id"]
        assert references[0]["quote_sha256"] == item["content_sha256"]
    return matches


@pytest.mark.parametrize("surface", ("cli-query", "cli-context", "mcp-query", "mcp-context"))
def test_limitation_only_does_not_satisfy_exception_duty(
    exception_case: dict[str, Any],
    surface: str,
) -> None:
    root = exception_case["root"]
    before = _ledger_heads(root)
    result = _public_result(exception_case, task=_TASK, surface=surface)
    after = _ledger_heads(root)
    _check_public_bounds(result, surface=surface, root=root)

    body = _provider_body(result, surface=surface)
    assert _provider_gap(body)
    if surface.startswith("cli-"):
        assert result["query_plan"]["policy_id"] == "evidence-first-v1"
        duty = _local_duty(result, surface=surface)
        local = result
        limitation_ids = {
            item["statement_id"]
            for item in local["statements"]
            if item.get("statement_type") == "limitation"
        }
        assert duty["status"] == "unresolved"
        assert not limitation_ids.intersection(duty["selected_refs"])
    assert before == after


@pytest.mark.parametrize("term", ("但书", "除外", "除非", "proviso"))
def test_proviso_terms_request_source_first_exception_duty(
    exception_case: dict[str, Any], term: str
) -> None:
    root = exception_case["root"]
    before = _ledger_heads(root)
    arguments = list(
        _cli_args(root, operation="query", task=f"Policy Alpha temporary draft {term}")
    )
    duty_index = arguments.index("--applicable-duty")
    del arguments[duty_index : duty_index + 2]
    result = exception_case["fixture"]._run_cli(exception_case["cli_home"], *arguments)
    assert _ledger_heads(root) == before
    duty = _local_duty(result, surface="cli-query")
    assert result["query_plan"]["policy_id"] == "evidence-first-v1"
    assert duty["applicable"] is True
    assert duty["status"] == "unresolved"
    body = _provider_body(result, surface="cli-query")
    assert _provider_gap(body)
    assert _exact_source_items(
        body, source_revision_id=exception_case["source"]["source_revision_id"]
    )


@pytest.mark.parametrize("surface", ("cli-query", "cli-context", "mcp-query", "mcp-context"))
def test_explicit_exception_request_returns_admitted_source_passage(
    exception_case: dict[str, Any],
    surface: str,
) -> None:
    root = exception_case["root"]
    before = _ledger_heads(root)
    result = _public_result(
        exception_case,
        task="Policy Alpha temporary draft exception proviso 但书",
        surface=surface,
    )
    after = _ledger_heads(root)
    _check_public_bounds(result, surface=surface, root=root)

    body = _provider_body(result, surface=surface)
    assert _exact_source_items(
        body,
        source_revision_id=exception_case["source"]["source_revision_id"],
    )
    assert _provider_gap(body)
    assert before == after
