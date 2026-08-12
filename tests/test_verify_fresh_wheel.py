from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from benchmarks.verify_fresh_wheel import (
    _compiled_query_hit,
    _sanitize_diagnostic,
)


def _query_v6() -> dict[str, object]:
    counts = {
        "compiled_candidate_count": 1,
        "admitted_statement_count": 1,
        "selected_statement_count": 1,
    }
    return {
        "query_plan": {
            "schema_version": "deeplaw.knowledge-query-plan/v6",
            **counts,
        },
        "metrics": dict(counts),
        "statements": [
            {
                "statement_id": "statement_1",
                "current_supported": True,
                "source_refs": [
                    {
                        "source_revision_id": "source_revision_1",
                        "fragment_id": "fragment_1",
                    }
                ],
            }
        ],
    }


def test_compiled_query_hit_uses_query_v6_counts_and_source_bound_statement() -> None:
    query = _query_v6()

    assert "compiled_hit" not in query["metrics"]
    assert _compiled_query_hit(query) is True


@pytest.mark.parametrize(
    "mutate",
    (
        lambda query: query["query_plan"].update(
            schema_version="deeplaw.knowledge-query-plan/v5"
        ),
        lambda query: query["metrics"].update(compiled_candidate_count=0),
        lambda query: query["query_plan"].update(selected_statement_count=0),
        lambda query: query["statements"][0].update(current_supported=False),
        lambda query: query["statements"][0].update(source_refs=[]),
    ),
)
def test_compiled_query_hit_fails_closed_on_non_compiled_v6_results(mutate) -> None:
    query = _query_v6()
    mutate(query)

    assert _compiled_query_hit(query) is False


def test_fresh_wheel_driver_does_not_import_source_package() -> None:
    driver = Path(__file__).resolve().parents[1] / "benchmarks" / "verify_fresh_wheel.py"
    tree = ast.parse(driver.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name == "deeplaw" or name.startswith("deeplaw.") for name in imported)


@pytest.mark.parametrize(
    "relative_import_path",
    (
        "Lib/site-packages/deeplaw/__init__.py",
        "lib/python3.12/site-packages/deeplaw/__init__.py",
    ),
)
def test_fresh_wheel_receipt_accepts_platform_site_packages_layouts(
    relative_import_path: str,
) -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "fresh-wheel-journey.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    runtime_schema = schema["properties"]["runtime"]
    import_path_schema = runtime_schema["properties"][
        "import_file_relative_to_environment"
    ]

    assert list(validator.evolve(schema=import_path_schema).iter_errors(relative_import_path)) == []


def test_resume_failure_diagnostic_preserves_cause_without_local_path(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "private" / "vault"
    diagnostic = _sanitize_diagnostic(
        f"RuntimeError: projection failed below {vault / 'wiki'}",
        roots=(vault, vault.parent),
    )

    assert diagnostic == "RuntimeError: projection failed below <redacted-path>/wiki"
    assert str(tmp_path) not in diagnostic


def test_resume_failure_diagnostic_normalizes_redacted_windows_paths() -> None:
    vault = Path("C:/Users/private/vault")
    diagnostic = _sanitize_diagnostic(
        r"RuntimeError: projection failed below C:\Users\private\vault\wiki",
        roots=(vault,),
    )

    assert diagnostic == "RuntimeError: projection failed below <redacted-path>/wiki"
    assert "\\" not in diagnostic
    assert "C:" not in diagnostic
