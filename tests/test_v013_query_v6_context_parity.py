from __future__ import annotations

import json
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from deeplaw.api import KnowledgeOS, KnowledgeOSValidationError
from deeplaw.context_compiler import verify_capsule
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_mcp_server import (
    _validate_knowledge_tool_arguments,
    handle_knowledge_support,
    knowledge_tool_definition,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes, stable_id

_QUERY_RECEIPT = re.compile(r"^queryreceipt_[0-9a-f]{24}$")
_TASK = "v6 context parity probe"


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-context-parity", scope="project")
    initialize_autonomous_core(root)
    # Seed only through the public knowledge mutation seam.  The fixture does
    # not write the canonical Ledger directly and never invokes a Provider.
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="v013-context-parity",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )
        store.remember(
            grant_id=grant["grant_id"],
            idempotency_key="v013-context-parity-seed",
            title="Context parity probe",
            body="The context path must preserve the Query Plan v6 controls.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
    return root


def _run_cli(root: Path, *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "deeplaw", "knowledge", *arguments],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _assert_v6_query(
    result: dict[str, Any],
    *,
    force_canonical_lexical: bool,
) -> None:
    assert result["schema_version"] == "deeplaw.purpose-aware-retrieval/v3"
    plan = result["query_plan"]
    assert plan["schema_version"] == "deeplaw.knowledge-query-plan/v6"
    assert plan["retrieval_controls"] == {
        "graph_hops": 2,
        "retrieval_mode": "lexical",
        "force_canonical_lexical": force_canonical_lexical,
    }
    assert _QUERY_RECEIPT.fullmatch(result["receipt_id"])
    assert plan["receipt_id"] == result["receipt_id"]
    assert isinstance(plan["duties"], list) and plan["duties"]
    assert isinstance(result["statements"], list)


def _assert_v6_context(result: dict[str, Any]) -> None:
    """Assert the additive context contract required by the v6 parity fix."""

    plan = result["query_plan"]
    assert plan["schema_version"] == "deeplaw.knowledge-query-plan/v6"
    assert plan["retrieval_controls"] == {
        "graph_hops": 2,
        "retrieval_mode": "lexical",
        # The fixture intentionally has no derived index, so the read path
        # must make canonical lexical fallback explicit in the v6 plan.
        "force_canonical_lexical": True,
    }
    receipt_id = result.get("receipt_id")
    if receipt_id is None:
        receipt_id = plan.get("receipt_id")
    assert isinstance(receipt_id, str) and _QUERY_RECEIPT.fullmatch(receipt_id)
    assert isinstance(plan["duties"], list) and plan["duties"]
    # Context must retain the selected Statement surface (possibly empty for
    # this source-free fixture) rather than reducing v6 to object-level v5.
    assert "statements" in result


def _selected_context_identity(value: dict[str, Any], *, provider: bool = False) -> tuple:
    body: Any = value
    if provider:
        body = value.get("capsule", {})
    statements = body.get("statements", []) if isinstance(body, dict) else []
    evidence = body.get("evidence", []) if isinstance(body, dict) else []
    gaps = body.get("gaps", []) if isinstance(body, dict) else []
    statement_ids = tuple(
        item.get("statement_id")
        for item in statements
        if isinstance(item, dict) and isinstance(item.get("statement_id"), str)
    )
    revision_ids = tuple(
        sorted(
            {
                item.get("knowledge_revision_id")
                for item in statements
                if isinstance(item, dict)
                and isinstance(item.get("knowledge_revision_id"), str)
            }
        )
    )
    source_ids = tuple(
        sorted(
            {
                reference.get("source_revision_id")
                for item in [*statements, *evidence]
                if isinstance(item, dict)
                for reference in item.get("source_refs", [])
                if isinstance(reference, dict)
                and isinstance(reference.get("source_revision_id"), str)
            }
        )
    )
    gap_codes = tuple(
        sorted(
            {
                item.get("code")
                for item in gaps
                if isinstance(item, dict) and isinstance(item.get("code"), str)
            }
        )
    )
    return statement_ids, revision_ids, source_ids, gap_codes


def _reseal_capsule(capsule: dict[str, Any]) -> None:
    body = {
        key: value
        for key, value in capsule.items()
        if key not in {"capsule_id", "capsule_digest"}
    }
    digest = sha256_bytes(canonical_json(body).encode("utf-8"))
    capsule["capsule_digest"] = digest
    capsule["capsule_id"] = stable_id("capsule", capsule["vault_id"], digest)


def test_python_query_and_context_default_to_v6(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeOS.open(root) as knowledge_os:
        query = knowledge_os.retrieval.query(
            _TASK,
            graph_hops=2,
            retrieval_mode="lexical",
            force_canonical_lexical=True,
        )
        _assert_v6_query(query, force_canonical_lexical=True)

        context = knowledge_os.context.compile(
            task=_TASK,
            graph_hops=2,
            retrieval_mode="lexical",
            confirm_no_case_data=True,
        )
        _assert_v6_context(context)


def test_python_context_explicit_v5_remains_compatibility_only(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with KnowledgeOS.open(root) as knowledge_os:
        query_v5 = knowledge_os.retrieval.query(
            _TASK,
            query_plan_version="5",
        )
        assert query_v5["schema_version"] == "deeplaw.purpose-aware-retrieval/v2"
        assert query_v5["query_plan"]["schema_version"] == (
            "deeplaw.knowledge-query-plan/v5"
        )

        # The context facade accepts v5 only as an explicit compatibility request.
        context_v5 = knowledge_os.context.compile(
            task=_TASK,
            query_plan_version="5",
            confirm_no_case_data=True,
        )
        assert context_v5["query_plan"]["schema_version"] == (
            "deeplaw.knowledge-query-plan/v5"
        )


def test_cli_query_defaults_to_v6(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    query = _run_cli(
        root,
        "query",
        "--vault",
        str(root),
        "--query",
        _TASK,
        "--graph-hops",
        "2",
        "--retrieval-mode",
        "lexical",
    )
    _assert_v6_query(query, force_canonical_lexical=False)


def test_cli_context_defaults_to_v6(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    context = _run_cli(
        root,
        "context",
        "--vault",
        str(root),
        "--task",
        _TASK,
        "--graph-hops",
        "2",
        "--retrieval-mode",
        "lexical",
        "--confirm-no-case-data",
    )
    _assert_v6_context(context)


def test_cli_autonomy_context_defaults_to_v6(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    autonomy_context = _run_cli(
        root,
        "autonomy",
        "context",
        "--vault",
        str(root),
        "--task",
        _TASK,
        "--graph-hops",
        "2",
        "--retrieval-mode",
        "lexical",
        "--confirm-no-case-data",
    )
    _assert_v6_context(autonomy_context)


def test_cli_context_v5_requires_an_explicit_compatibility_request(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    context_v5 = _run_cli(
        root,
        "context",
        "--vault",
        str(root),
        "--task",
        _TASK,
        "--query-plan-version",
        "5",
        "--confirm-no-case-data",
    )
    assert context_v5["query_plan"]["schema_version"] == (
        "deeplaw.knowledge-query-plan/v5"
    )


def test_mcp_query_and_context_default_to_v6(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    query = handle_knowledge_support(
        operation="query",
        query=_TASK,
        graph_hops=2,
        retrieval_mode="lexical",
        vault_path=root,
    )
    assert query["schema_version"] == "deeplaw.knowledge-support-output/v6"
    assert query["result"]["schema_version"] == "deeplaw.provider-knowledge-capsule/v2"
    assert query["result"]["receipt"]["receipt_id"].startswith("queryreceipt_")
    assert query["result"]["delivery"]["hard_limit_bytes"] == 65_536

    context = handle_knowledge_support(
        operation="context",
        task=_TASK,
        graph_hops=2,
        retrieval_mode="lexical",
        confirm_no_case_data=True,
        vault_path=root,
    )
    assert context["schema_version"] == "deeplaw.knowledge-support-output/v6"
    assert context["result"]["schema_version"] == "deeplaw.provider-knowledge-capsule/v2"


def test_task_binding_has_python_cli_mcp_v6_parity_and_never_reaches_provider(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    binding = build_task_context_binding(
        sha256_bytes(b"v013-context-parity-project"),
        sha256_bytes(b"v013-context-parity-task-line"),
    )
    encoded_binding = canonical_json(binding)

    with KnowledgeOS.open(root) as knowledge_os:
        python_query = knowledge_os.retrieval.query(_TASK, task_binding=binding)
        python_context = knowledge_os.context.compile(
            task=_TASK,
            scope="project",
            max_sensitivity="public",
            limit=8,
            max_chars=6_000,
            max_tokens=6_000,
            max_sources=12,
            task_binding=binding,
            confirm_no_case_data=True,
        )
        with pytest.raises(KnowledgeOSValidationError) as error:
            knowledge_os.retrieval.query(
                _TASK,
                query_plan_version="5",
                task_binding=binding,
            )
        assert error.value.code == "invalid_request"
    assert python_query["query_plan"]["task_binding"] == binding
    assert python_context["task_binding"] == binding
    assert python_context["query_plan"]["task_binding"] == binding
    assert binding["binding_sha256"] not in canonical_json(
        python_context["provider_capsule"]
    )

    cli_query = _run_cli(
        root,
        "query",
        "--vault",
        str(root),
        "--query",
        _TASK,
        "--task-binding",
        encoded_binding,
    )
    cli_context = _run_cli(
        root,
        "context",
        "--vault",
        str(root),
        "--task",
        _TASK,
        "--scope",
        "project",
        "--max-sensitivity",
        "public",
        "--task-binding",
        encoded_binding,
        "--confirm-no-case-data",
    )
    assert cli_query["query_plan"]["task_binding"] == binding
    assert cli_context["task_binding"] == binding
    assert cli_context["query_plan"]["task_binding"] == binding
    assert binding["binding_sha256"] not in canonical_json(
        cli_context["provider_capsule"]
    )

    mcp_context = handle_knowledge_support(
        operation="context",
        task=_TASK,
        scope="project",
        max_sensitivity="public",
        limit=8,
        max_chars=6_000,
        max_tokens=6_000,
        max_sources=12,
        task_binding=binding,
        confirm_no_case_data=True,
        vault_path=root,
    )
    assert python_context["query_plan"]["scope"] == "project"
    assert python_context["query_plan"]["max_sensitivity"] == "public"
    assert cli_context["query_plan"]["scope"] == "project"
    assert cli_context["query_plan"]["max_sensitivity"] == "public"
    assert (
        python_context["provider_capsule"]
        == cli_context["provider_capsule"]
        == mcp_context["result"]
    )
    assert _selected_context_identity(
        python_context["provider_capsule"], provider=True
    ) == _selected_context_identity(mcp_context["result"], provider=True)

    for operation, fields in (
        ("query", {"query": _TASK}),
        ("context", {"task": _TASK, "confirm_no_case_data": True}),
    ):
        response = handle_knowledge_support(
            operation=operation,
            task_binding=binding,
            vault_path=root,
            **fields,
        )
        assert response["schema_version"] == "deeplaw.knowledge-support-output/v6"
        assert response["result"]["schema_version"] == (
            "deeplaw.provider-knowledge-capsule/v2"
        )
        assert binding["binding_sha256"] not in canonical_json(response["result"])
    with pytest.raises(ValueError, match="query_plan_version=6"):
        handle_knowledge_support(
            operation="context",
            task=_TASK,
            query_plan_version="5",
            task_binding=binding,
            confirm_no_case_data=True,
            vault_path=root,
        )


def test_audit_context_recursively_redacts_owner_local_route_metadata(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    binding = build_task_context_binding(
        sha256_bytes(b"v013-recursive-redaction-project"),
        sha256_bytes(b"v013-recursive-redaction-task-line"),
        repository_sha256=sha256_bytes(b"v013-recursive-redaction-repository"),
        worktree_sha256=sha256_bytes(b"v013-recursive-redaction-worktree"),
        base_revision="a" * 40,
        dirty_state_sha256=sha256_bytes(b"v013-recursive-redaction-dirty-state"),
    )
    with KnowledgeOS.open(root) as knowledge_os:
        local = knowledge_os.context.compile(
            task=_TASK,
            task_binding=binding,
            projection="audit",
            confirm_no_case_data=True,
        )

    provider = local["provider_capsule"]
    private_keys = {
        "task_binding",
        "canonical_binding",
        "binding_sha256",
        "project_sha256",
        "task_lineage_sha256",
        "repository_sha256",
        "worktree_sha256",
        "base_revision",
        "dirty_state_sha256",
        "task_route_sha256",
        "task_snapshot_sha256",
        "route_revision_ids",
    }

    def assert_redacted(value: object) -> None:
        if isinstance(value, list):
            for item in value:
                assert_redacted(item)
        elif isinstance(value, dict):
            assert not private_keys.intersection(value)
            for item in value.values():
                assert_redacted(item)

    assert_redacted(provider)
    serialized = canonical_json(provider)
    assert all(item not in serialized for item in binding.values() if isinstance(item, str))
    assert str(root) not in serialized


def test_mcp_context_v5_is_available_only_when_explicitly_requested(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    context_v5 = handle_knowledge_support(
        operation="context",
        task=_TASK,
        query_plan_version="5",
        plane="autonomous",
        confirm_no_case_data=True,
        vault_path=root,
    )
    assert context_v5["schema_version"] == "deeplaw.knowledge-support-output/v3"
    assert context_v5["result"]["schema_version"] == "deeplaw.knowledge-capsule/v2"
    assert context_v5["result"]["query_plan"]["schema_version"] == (
        "deeplaw.knowledge-query-plan/v5"
    )


def test_mcp_context_contract_defaults_v6_and_closes_v5_controls() -> None:
    validator = Draft202012Validator(
        knowledge_tool_definition(autonomous=True).inputSchema
    )
    validator.validate({"operation": "query", "query": _TASK})
    validator.validate(
        {
            "operation": "context",
            "task": _TASK,
            "confirm_no_case_data": True,
        }
    )
    legacy_v5 = {
        "operation": "context",
        "query_plan_version": "5",
        "task": _TASK,
        "confirm_no_case_data": True,
        "plane": "autonomous",
    }
    assert list(validator.iter_errors(legacy_v5))
    assert _validate_knowledge_tool_arguments(legacy_v5, autonomous=True) == (
        "internal_compatibility"
    )
    assert list(
        validator.iter_errors(
            {
                "operation": "context",
                "query_plan_version": "5",
                "task": _TASK,
                "confirm_no_case_data": True,
                "query_target": _TASK,
            }
        )
    )
    assert list(
        validator.iter_errors(
            {
                "operation": "context",
                "task": _TASK,
                "confirm_no_case_data": True,
                "plane": "autonomous",
            }
        )
    )
    assert list(
        validator.iter_errors(
            {
                "operation": "context",
                "task": _TASK,
                "confirm_no_case_data": True,
                "applicable_duties": ["unknown_duty"],
            }
        )
    )


def test_v6_context_verifier_binds_plan_receipt_and_provider_projection(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeOS.open(root) as knowledge_os:
        capsule = knowledge_os.context.compile(
            task=_TASK,
            graph_hops=2,
            retrieval_mode="lexical",
            confirm_no_case_data=True,
        )
    with KnowledgeVault(root, read_only=True) as vault:
        verified = verify_capsule(capsule, vault=vault)
    assert verified["valid"] is True
    assert verified["receipt_derivation_valid"] is True
    assert verified["query_identity_valid"] is True
    assert verified["selection_identity_valid"] is True
    assert verified["provider_projection_consistent"] is True
    assert verified["budget_identity_valid"] is True

    plan_tampered = deepcopy(capsule)
    plan_tampered["query_plan"]["retrieval_controls"]["graph_hops"] = 0
    plan_sha256 = sha256_bytes(
        canonical_json(plan_tampered["query_plan"]).encode("utf-8")
    )
    plan_tampered["query_plan_sha256"] = plan_sha256
    plan_tampered["audit"]["query_plan_sha256"] = plan_sha256
    _reseal_capsule(plan_tampered)
    plan_result = verify_capsule(plan_tampered)
    assert plan_result["query_plan_valid"] is True
    assert plan_result["receipt_derivation_valid"] is False
    assert plan_result["valid"] is False

    provider_tampered = deepcopy(capsule)
    provider_tampered["provider_capsule"]["receipt"]["receipt_id"] = stable_id(
        "queryreceipt", "tampered"
    )
    _reseal_capsule(provider_tampered)
    provider_result = verify_capsule(provider_tampered)
    assert provider_result["receipt_identity_valid"] is False
    assert provider_result["valid"] is False

    projection_tampered = deepcopy(capsule)
    projection_tampered["gaps"][0]["message"] = "tampered local-only gap"
    _reseal_capsule(projection_tampered)
    projection_result = verify_capsule(projection_tampered)
    assert projection_result["selection_identity_valid"] is True
    assert projection_result["provider_projection_consistent"] is False
    assert projection_result["valid"] is False

    budget_tampered = deepcopy(capsule)
    budget_tampered["budget"]["provider_payload_bytes"] -= 1
    _reseal_capsule(budget_tampered)
    budget_result = verify_capsule(budget_tampered)
    assert budget_result["budget_identity_valid"] is False
    assert budget_result["valid"] is False
