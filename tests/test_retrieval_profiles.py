from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplaw.context_compiler import compile_context
from deeplaw.knowledge_feedback import create_run_receipt, record_structured_feedback
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval_fabric import retrieve
from deeplaw.retrieval_profiles import (
    BASE_CHANNEL_WEIGHTS,
    activate_retrieval_profile,
    evaluate_retrieval_profile,
    load_active_retrieval_profile,
    rollback_retrieval_profile,
    train_retrieval_profile,
)


def _vault_with_feedback(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="profiles", scope="project")
    capsule_path = tmp_path / "capsule.json"
    with KnowledgeVault(root, read_only=False) as vault:
        useful = vault.propose_asset(
            kind="procedure",
            memory_tier="project",
            title="Alpha procedure",
            statement="Use the Alpha verified procedure.",
        )
        noisy = vault.propose_asset(
            kind="reference",
            memory_tier="project",
            title="Alpha unrelated note",
            statement="Alpha appears in an unrelated note.",
        )
        useful = vault.approve_asset(useful.asset_id, confirm_reviewed=True)
        noisy = vault.approve_asset(noisy.asset_id, confirm_reviewed=True)
        capsule = compile_context(
            vault,
            task="Apply the Alpha procedure.",
            confirm_no_case_data=True,
            max_items=1,
        )
        capsule_path.write_text(json.dumps(capsule), encoding="utf-8")
        run = create_run_receipt(
            vault,
            capsule_path=capsule_path,
            status="partial",
            host_name="codex",
            host_version="test",
        )
        feedback = record_structured_feedback(
            vault,
            run_id=run["run_id"],
            outcome="partial",
            helpful_asset_ids=(useful.asset_id,),
            observation="The procedure was useful and the note was noise.",
            recommended_action="Prefer the procedure while preserving admission rules.",
        )
    return root, feedback["feedback_id"], useful.asset_id


def test_profile_requires_regression_gate_before_ranking_only_activation(
    tmp_path: Path,
) -> None:
    root, feedback_id, expected_asset_id = _vault_with_feedback(tmp_path)
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": "deeplaw.retrieval-regression-suite/v1",
                "cases": [
                    {
                        "case_id": "alpha-procedure",
                        "query": "Alpha procedure",
                        "mode": "lexical",
                        "expected_asset_ids": [expected_asset_id],
                        "forbidden_asset_ids": [],
                        "max_items": 1,
                    }
                ],
                "gates": {
                    "min_recall": 1.0,
                    "max_irrelevant_rate": 0.0,
                    "max_safety_failures": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    with KnowledgeVault(root, read_only=True) as vault:
        profile = train_retrieval_profile(vault, feedback_ids=(feedback_id,))
        evaluation = evaluate_retrieval_profile(
            vault,
            profile_id=profile["profile_id"],
            suite_path=suite_path,
        )
        evaluation_path = (
            root / "derived" / "retrieval-profiles" / evaluation["evaluation_file"]
        )
        activated = activate_retrieval_profile(
            vault,
            profile_id=profile["profile_id"],
            evaluation_path=evaluation_path,
        )
        response = retrieve(vault, "Alpha procedure", mode="lexical", limit=1)

    assert evaluation["passed"] is True
    assert activated["authority_changed"] is False
    assert response["trace"]["query_plan"]["retrieval_profile"]["profile_id"] == profile[
        "profile_id"
    ]

    with KnowledgeVault(root, read_only=True) as vault:
        rolled_back = rollback_retrieval_profile(vault)
        assert rolled_back["active_profile_id"] is None
        assert load_active_retrieval_profile(vault) is None


def test_profile_activation_rejects_failed_evaluation(tmp_path: Path) -> None:
    root, feedback_id, _ = _vault_with_feedback(tmp_path)
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": "deeplaw.retrieval-regression-suite/v1",
                "cases": [
                    {
                        "case_id": "impossible",
                        "query": "Alpha procedure",
                        "mode": "lexical",
                        "expected_asset_ids": ["asset_000000000000000000000000"],
                        "forbidden_asset_ids": [],
                        "max_items": 1,
                    }
                ],
                "gates": {
                    "min_recall": 1.0,
                    "max_irrelevant_rate": 0.0,
                    "max_safety_failures": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=True) as vault:
        profile = train_retrieval_profile(vault, feedback_ids=(feedback_id,))
        evaluation = evaluate_retrieval_profile(
            vault,
            profile_id=profile["profile_id"],
            suite_path=suite_path,
        )
        path = root / "derived" / "retrieval-profiles" / evaluation["evaluation_file"]
        with pytest.raises(ValueError, match="did not pass"):
            activate_retrieval_profile(
                vault,
                profile_id=profile["profile_id"],
                evaluation_path=path,
            )

    assert evaluation["passed"] is False


def test_profile_training_consumes_every_structured_feedback_signal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "all-signals-vault"
    initialize_knowledge_vault(root, name="all feedback signals", scope="project")
    capsule_path = tmp_path / "all-signals-capsule.json"
    with KnowledgeVault(root, read_only=False) as vault:
        assets = []
        signals = ("HELPFUL-771", "NOISE-882", "HARMFUL-993", "STALE-664")
        for index, (kind, signal) in enumerate(
            zip(("procedure", "reference", "risk", "experience"), signals, strict=True)
        ):
            proposal = vault.propose_asset(
                kind=kind,
                memory_tier="project",
                title=f"Feedback signal {index}",
                statement=f"The independently classified marker is {signal}.",
            )
            assets.append(vault.approve_asset(proposal.asset_id, confirm_reviewed=True))
        capsule = compile_context(
            vault,
            task="Review HELPFUL-771, NOISE-882, HARMFUL-993, and STALE-664.",
            confirm_no_case_data=True,
            max_items=4,
        )
        selected_ids = {
            item["asset_id"]
            for group in ("constraints", "decisions", "knowledge_assets", "experiences")
            for item in capsule[group]
        }
        assert selected_ids == {asset.asset_id for asset in assets}
        capsule_path.write_text(json.dumps(capsule), encoding="utf-8")
        run = create_run_receipt(
            vault,
            capsule_path=capsule_path,
            status="partial",
            host_name="codex",
            host_version="test",
        )
        feedback = record_structured_feedback(
            vault,
            run_id=run["run_id"],
            outcome="partial",
            helpful_asset_ids=(assets[0].asset_id,),
            irrelevant_asset_ids=(assets[1].asset_id,),
            harmful_asset_ids=(assets[2].asset_id,),
            stale_asset_ids=(assets[3].asset_id,),
            missing_knowledge=("The recovery owner is missing.",),
            missing_sources=("The offline key ceremony record is missing.",),
            incorrect_relations=("The procedure must not depend on the stale experience.",),
            budget_failures=("The previous Capsule exhausted its evidence budget.",),
            observation="Every supported feedback category was explicitly classified.",
            recommended_action="Train a candidate profile and run its full regression gate.",
        )
        profile = train_retrieval_profile(
            vault,
            feedback_ids=(feedback["feedback_id"],),
        )

    assert feedback["helpful_asset_ids"] == [assets[0].asset_id]
    assert feedback["irrelevant_asset_ids"] == [assets[1].asset_id]
    assert feedback["harmful_asset_ids"] == [assets[2].asset_id]
    assert feedback["stale_asset_ids"] == [assets[3].asset_id]
    assert feedback["missing_knowledge"] and feedback["missing_sources"]
    assert feedback["incorrect_relations"] and feedback["budget_failures"]
    assert profile["channel_weights"]["temporal"] > BASE_CHANNEL_WEIGHTS["temporal"]
    assert profile["channel_weights"]["graph"] < BASE_CHANNEL_WEIGHTS["graph"]
    assert profile["channel_weights"]["tree"] > BASE_CHANNEL_WEIGHTS["tree"]
    assert profile["authority_effect"] == "ranking-only"
