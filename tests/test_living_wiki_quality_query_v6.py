from __future__ import annotations

from typing import Any

import pytest

from benchmarks.living_wiki.run_quality_gate import QualityGateError, _query


def _configuration() -> dict[str, Any]:
    return {
        "scope": "project",
        "max_sensitivity": "public",
        "max_characters": 4_000,
        "max_tokens": 1_000,
        "max_sources": 5,
        "graph_hops": 1,
        "retrieval_mode": "hybrid",
    }


def _v5_result() -> dict[str, Any]:
    return {
        "schema_version": "deeplaw.purpose-aware-retrieval/v2",
        "query_plan": {"schema_version": "deeplaw.knowledge-query-plan/v5"},
        "compiled": [],
        "evidence": [],
        "gaps": [],
    }


def test_current_quality_candidate_pins_published_v5_compatibility() -> None:
    class FakeCli:
        query_plan_version = "5"

        def run(self, *arguments: str, **_kwargs: Any) -> tuple[Any, float]:
            version_index = arguments.index("--query-plan-version")
            assert arguments[version_index + 1] == "5"
            return _v5_result(), 1.0

    result, elapsed = _query(
        FakeCli(),  # type: ignore[arg-type]
        vault=None,  # type: ignore[arg-type]
        query="Exact governed identity",
        purpose="answer",
        top_k=5,
        configuration=_configuration(),
        label="pinned v5 compatibility",
    )

    assert elapsed == 1.0
    assert result["schema_version"] == "deeplaw.purpose-aware-retrieval/v2"


def test_v6_default_drift_cannot_be_scored_as_legacy_quality() -> None:
    class FakeCli:
        query_plan_version = "5"

        def run(self, *arguments: str, **_kwargs: Any) -> tuple[Any, float]:
            assert "--query-plan-version" in arguments
            return {
                "schema_version": "deeplaw.purpose-aware-retrieval/v3",
                "query_plan": {"schema_version": "deeplaw.knowledge-query-plan/v6"},
                "statements": [],
                "evidence": [],
                "gaps": [],
            }, 1.0

    with pytest.raises(QualityGateError, match="unexpected retrieval contract"):
        _query(
            FakeCli(),  # type: ignore[arg-type]
            vault=None,  # type: ignore[arg-type]
            query="Exact governed identity",
            purpose="answer",
            top_k=5,
            configuration=_configuration(),
            label="reject v6 as legacy evidence",
        )


def test_v010_baseline_keeps_its_original_v4_contract() -> None:
    class FakeCli:
        query_plan_version = None

        def run(self, *arguments: str, **_kwargs: Any) -> tuple[Any, float]:
            assert "--query-plan-version" not in arguments
            return {
                "schema_version": "deeplaw.purpose-aware-retrieval/v1",
                "query_plan": {"schema_version": "deeplaw.knowledge-query-plan/v4"},
                "compiled": [],
                "evidence": [],
                "gaps": [],
            }, 1.0

    result, _elapsed = _query(
        FakeCli(),  # type: ignore[arg-type]
        vault=None,  # type: ignore[arg-type]
        query="Exact governed identity",
        purpose="answer",
        top_k=5,
        configuration=_configuration(),
        label="v0.10 baseline",
    )

    assert result["query_plan"]["schema_version"] == (
        "deeplaw.knowledge-query-plan/v4"
    )
