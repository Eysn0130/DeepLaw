from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from deeplaw.api import KnowledgeOS, KnowledgeOSConflictError
from deeplaw.knowledge_autonomy import (
    SINK_OPERATIONS,
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="v013-python-read-runtime", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="v013-python-read-runtime",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )
        store.remember(
            grant_id=grant["grant_id"],
            idempotency_key="v013-python-read-runtime-seed",
            title="Python read runtime probe",
            body="The warm Python Capsule request reuses its verified snapshot.",
            kind="concept",
            operation="upsert_concept",
            confirm_no_case_data=True,
        )
    return root


def test_python_facade_reuses_startup_snapshot_and_closes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    counts = {"legacy": 0, "autonomous": 0}
    original_legacy = KnowledgeVault.verify_integrity
    original_autonomous = AutonomousKnowledgeStore.verify

    def counted_legacy(self: KnowledgeVault, *args: Any, **kwargs: Any) -> dict[str, Any]:
        counts["legacy"] += 1
        return original_legacy(self, *args, **kwargs)

    def counted_autonomous(
        self: AutonomousKnowledgeStore,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        counts["autonomous"] += 1
        return original_autonomous(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgeVault, "verify_integrity", counted_legacy)
    monkeypatch.setattr(AutonomousKnowledgeStore, "verify", counted_autonomous)

    knowledge_os = KnowledgeOS.open(root)
    try:
        assert counts == {"legacy": 1, "autonomous": 1}
        first = knowledge_os.context.compile(
            task="warm Python Capsule request",
            confirm_no_case_data=True,
        )
        second = knowledge_os.context.compile(
            task="warm Python Capsule request",
            confirm_no_case_data=True,
        )
        assert first["schema_version"] == "deeplaw.knowledge-capsule/v3"
        assert second["schema_version"] == "deeplaw.knowledge-capsule/v3"
        assert first["query_plan"]["schema_version"] == "deeplaw.knowledge-query-plan/v6"
        assert second["query_plan"]["schema_version"] == "deeplaw.knowledge-query-plan/v6"
        assert counts == {"legacy": 2, "autonomous": 2}

        verified = knowledge_os.verify()
        assert verified["valid"] is True
        assert counts == {"legacy": 3, "autonomous": 3}
    finally:
        knowledge_os.close()

    with pytest.raises(KnowledgeOSConflictError, match="state changed"):
        knowledge_os.context.compile(
            task="closed runtime",
            confirm_no_case_data=True,
        )


def test_python_retrieval_reuses_startup_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    counts = {"legacy": 0, "autonomous": 0}
    original_legacy = KnowledgeVault.verify_integrity
    original_autonomous = AutonomousKnowledgeStore.verify

    def counted_legacy(self: KnowledgeVault, *args: Any, **kwargs: Any) -> dict[str, Any]:
        counts["legacy"] += 1
        return original_legacy(self, *args, **kwargs)

    def counted_autonomous(
        self: AutonomousKnowledgeStore,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        counts["autonomous"] += 1
        return original_autonomous(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgeVault, "verify_integrity", counted_legacy)
    monkeypatch.setattr(AutonomousKnowledgeStore, "verify", counted_autonomous)

    knowledge_os = KnowledgeOS.open(root)
    try:
        first = knowledge_os.retrieval.query(
            "warm Python Capsule request",
            query_plan_version="5",
            purpose="answer",
            scope="project",
            max_sensitivity="private",
            limit=8,
            max_chars=8_000,
            max_tokens=4_000,
        )
        second = knowledge_os.retrieval.query(
            "warm Python Capsule request",
            query_plan_version="5",
            purpose="answer",
            scope="project",
            max_sensitivity="private",
            limit=8,
            max_chars=8_000,
            max_tokens=4_000,
        )
        assert first["schema_version"] == "deeplaw.purpose-aware-retrieval/v2"
        assert second["schema_version"] == first["schema_version"]
        assert second["compiled"] == first["compiled"]
        assert second["evidence"] == first["evidence"]
        assert second["contradictions"] == first["contradictions"]
        assert second["gaps"] == first["gaps"]
        assert counts == {"legacy": 2, "autonomous": 2}
    finally:
        knowledge_os.close()

    with pytest.raises(KnowledgeOSConflictError, match="state changed"):
        knowledge_os.retrieval.query(
            "warm Python Capsule request",
            query_plan_version="5",
            purpose="answer",
            scope="project",
            max_sensitivity="private",
            limit=8,
            max_chars=8_000,
            max_tokens=4_000,
        )


def test_python_facade_reopens_after_bounded_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    counts = {"legacy": 0, "autonomous": 0}
    original_legacy = KnowledgeVault.verify_integrity
    original_autonomous = AutonomousKnowledgeStore.verify

    def counted_legacy(self: KnowledgeVault, *args: Any, **kwargs: Any) -> dict[str, Any]:
        counts["legacy"] += 1
        return original_legacy(self, *args, **kwargs)

    def counted_autonomous(
        self: AutonomousKnowledgeStore,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        counts["autonomous"] += 1
        return original_autonomous(self, *args, **kwargs)

    monkeypatch.setattr(KnowledgeVault, "verify_integrity", counted_legacy)
    monkeypatch.setattr(AutonomousKnowledgeStore, "verify", counted_autonomous)

    with KnowledgeOS.open(root) as knowledge_os:
        knowledge_os.context.compile(
            task="initial Python Capsule request",
            confirm_no_case_data=True,
        )
        knowledge_os.retrieval.query(
            "initial Python Capsule request",
            query_plan_version="5",
        )
        with AutonomousKnowledgeStore(root, read_only=False) as store:
            store.enable_grant(
                writer_id="v013-python-read-runtime-change",
                operations=("remember",),
                max_mutations_per_minute=120,
            )
        knowledge_os.retrieval.query(
            "reopened Python Capsule request",
            query_plan_version="5",
        )
        knowledge_os.context.compile(
            task="reopened Python Capsule request",
            confirm_no_case_data=True,
        )
        assert counts == {"legacy": 3, "autonomous": 3}
