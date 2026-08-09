from __future__ import annotations

import pytest

from benchmarks.verify_fresh_wheel import _compiled_query_hit


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
