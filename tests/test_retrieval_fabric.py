from __future__ import annotations

from pathlib import Path

import deeplaw.retrieval_fabric as retrieval_fabric
from deeplaw.context_compiler import compile_context, verify_capsule
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.retrieval_fabric import build_query_plan, compare_retrieval, recall, retrieve
from deeplaw.util import (
    QUERY_EXPANSION_PROFILE,
    query_discovery_text,
    query_expansion_terms,
    query_search_terms,
    search_terms,
)


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="retrieval fabric", scope="project")
    return root


def _active(
    vault: KnowledgeVault,
    *,
    title: str,
    statement: str,
    kind: str = "fact",
    semantic_key: str | None = None,
    sensitivity: str = "private",
) -> str:
    proposal = vault.propose_asset(
        kind=kind,
        memory_tier="project",
        title=title,
        statement=statement,
        semantic_key=semantic_key,
        sensitivity=sensitivity,
    )
    return vault.approve_asset(proposal.asset_id, confirm_reviewed=True).asset_id


def test_mixed_tokenizer_covers_traditional_chinese_and_code_shapes() -> None:
    terms = search_terms(
        "請查目前軟體設定 parseHTTPError repo_path v2.4.1 ERR-2048",
        limit=64,
        cover_tail=True,
    )

    assert {"目前", "软件", "设定", "parsehttperror", "parse", "http", "error"} <= set(
        terms
    )
    assert {"repo_path", "repo", "path", "v2.4.1", "err-2048", "2048"} <= set(terms)


def test_query_only_cross_language_expansion_is_bounded_and_auditable() -> None:
    query = "比较两项诊断日志保留政策，并保留它们之间的冲突。"
    expansions = query_expansion_terms(query)
    terms = query_search_terms(query, limit=16, cover_tail=True)
    plan = build_query_plan(query, mode="lexical")

    assert QUERY_EXPANSION_PROFILE == "deeplaw-deterministic-query-expansion/1"
    assert {"compare", "diagnostic", "logs", "retention", "policies", "conflict"} <= set(
        expansions
    )
    assert set(expansions[:5]) <= set(terms)
    assert len(terms) <= 16
    assert set(expansions[:16]) & set(plan["search_terms"])
    assert plan["implementation_revision"] == "retrieval-fabric/3"
    mixed = query_discovery_text(
        "Atlas 审阅完成 2025-06-01；Atlas 计划发布 2025-07-01；Atlas Protocol"
    )
    assert {"atlas", "2025-06-01", "2025-07-01", "protocol"} <= set(
        mixed.split()
    )
    assert {"review", "completed", "publication", "scheduled"} <= set(
        mixed.split()
    )


def test_bounded_typo_repair_recovers_one_edit_and_rejects_distant_noise(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        target = _active(
            vault,
            title="Recovery procedure",
            statement="The recovery procedure validates the signed snapshot.",
            kind="procedure",
        )

        repaired = retrieve(vault, "proceduer", mode="lexical", limit=5)
        noise = retrieve(vault, "proczzzzz", mode="lexical", limit=5)

    assert [item["asset_id"] for item in repaired["results"]] == [target]
    assert repaired["trace"]["channel_candidates"]["lexical"][0][
        "reason"
    ] == "fielded_bm25_typo_repair:proceduer->procedure"
    assert noise["results"] == []


def test_acronym_synonym_multi_entity_and_long_tail_queries_recall_targets(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        acl = _active(
            vault,
            title="ACL boundary",
            statement="The Access Control List (ACL) denies inherited write access.",
            kind="constraint",
        )
        atlas = _active(
            vault,
            title="Atlas release flow",
            statement="The Atlas release流程 always verifies the immutable artifact.",
            kind="procedure",
        )
        borealis = _active(
            vault,
            title="Borealis rollback",
            statement="Borealis rollback uses marker TAIL-NEEDLE-9042.",
            kind="experience",
        )

        acronym = retrieve(vault, "ACL", mode="lexical", limit=5)
        synonym = retrieve(vault, "Atlas 发布步骤", mode="lexical", limit=5)
        multi_entity = retrieve(
            vault,
            "比较 Atlas release 与 Borealis rollback",
            mode="global",
            limit=5,
        )
        long_tail = retrieve(
            vault,
            " ".join([*(f"background{index}" for index in range(120)), "TAIL-NEEDLE-9042"]),
            mode="hybrid",
            limit=5,
        )

    assert acronym["results"][0]["asset_id"] == acl
    assert synonym["results"][0]["asset_id"] == atlas
    assert {atlas, borealis} <= {item["asset_id"] for item in multi_entity["results"]}
    assert long_tail["results"][0]["asset_id"] == borealis


def test_query_plan_is_deterministic_and_records_duties_and_channels() -> None:
    first = build_query_plan(
        "截至 2026-07-01，为什么当前迁移流程存在冲突?",
        mode="auto",
    )
    second = build_query_plan(
        "截至 2026-07-01，为什么当前迁移流程存在冲突?",
        mode="auto",
    )

    assert first == second
    assert {"temporal", "relational", "procedure", "contradiction"} <= set(
        first["intent"]
    )
    assert {"required_procedures", "conflicts", "counterevidence"} <= set(
        first["duties"]
    )
    assert {"lexical", "graph", "temporal"} <= set(first["channels"])
    assert first["temporal_scope"]["as_of"] == "2026-07-01T23:59:59Z"
    assert first["reranker_profile"] == "off"


def test_global_recall_builds_one_verified_cross_source_capsule(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    sources = (
        (
            tmp_path / "global-constraint.md",
            "# Aurora constraint\nAURORA-GLOBAL requires owner-only storage.\n",
        ),
        (
            tmp_path / "global-procedure.md",
            "# Aurora procedure\nAURORA-GLOBAL verifies the snapshot before recovery.\n",
        ),
    )
    source_ids: set[str] = set()
    with KnowledgeVault(root, read_only=False) as vault:
        for source, content in sources:
            source.write_text(content, encoding="utf-8")
            compiled = compile_source(
                vault,
                source,
                source_kind="document",
                confirm_no_case_data=True,
            )
            manifest = vault.source_review_manifest(compiled["source"]["source_id"])
            vault.approve_source_assets(
                compiled["source"]["source_id"],
                confirm_reviewed=True,
                review_manifest_sha256=manifest["review_manifest_sha256"],
            )
            source_ids.add(compiled["source"]["source_id"])

        result = recall(
            vault,
            "Give a global overview of every AURORA-GLOBAL constraint and procedure.",
            confirm_no_case_data=True,
            mode="global",
            max_items=5,
            max_tokens=1_024,
        )
        verification = verify_capsule(result["capsule"], vault=vault)

    selected_source_ids = {
        reference["source_id"]
        for item in result["retrieval"]["results"]
        for reference in item["source_refs"]
    }
    assert "global_synthesis" in result["query_plan"]["intent"]
    assert source_ids <= selected_source_ids
    assert verification["valid"] is True
    assert result["capsule"]["budget"]["selected_tokens"] <= 1_024


def test_retrieval_fuses_exact_lexical_and_reviewed_graph_then_applies_admission(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "mercury.md"
    source.write_text(
        "# Mercury migration boundary\n"
        "Mercury migration must preserve the immutable local storage boundary.\n\n"
        "# Reviewed rollback procedure\n"
        "Create a verified snapshot before applying the migration.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        seed, neighbor = compiled["asset_ids"]
        restricted = _active(
            vault,
            title="Mercury secret",
            statement="Mercury migration has restricted operator-only details.",
            sensitivity="restricted",
        )
        proposed = vault.propose_asset(
            kind="fact",
            memory_tier="project",
            title="Mercury unreviewed",
            statement="Mercury unreviewed candidate must not enter context.",
        )
        vault.add_relation(
            subject_asset_id=seed,
            predicate="depends_on",
            object_asset_id=neighbor,
            evidence_fragment_id=vault.get_asset(seed).source_refs[0].fragment_id,
            confirm_reviewed=True,
        )

    with KnowledgeVault(root, read_only=True) as vault:
        result = retrieve(
            vault,
            "Why does Mercury migration depend on the storage boundary?",
            mode="graph",
            limit=5,
        )

    selected = [item["asset_id"] for item in result["results"]]
    assert seed in selected
    assert neighbor in selected
    assert restricted not in selected
    assert proposed.asset_id not in selected
    exclusions = {
        item["asset_id"]: item["reasons"]
        for item in result["trace"]["excluded_candidates"]
    }
    assert "sensitivity:restricted" in exclusions[restricted]
    assert any(reason.startswith("lifecycle_status:") for reason in exclusions[proposed.asset_id])
    assert result["ranking"]["numeric_confidence_exposed"] is False
    assert result["trace"]["authority_changed_by_ranking"] is False


def test_retrieval_reports_source_integrity_gap_even_when_it_is_not_first_reason(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "restricted-source.md"
    source.write_text(
        "# Restricted integrity marker\n"
        "The zephyr integrity marker remains bound to exact stored evidence.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            sensitivity="restricted",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        asset_id = compiled["asset_ids"][0]
        vault.source_file_path(compiled["source"]["source_id"]).unlink()

    with KnowledgeVault(root, read_only=True) as vault:
        result = retrieve(vault, "zephyr integrity marker", mode="lexical", limit=5)

    excluded = next(
        item
        for item in result["trace"]["excluded_candidates"]
        if item["asset_id"] == asset_id
    )
    assert excluded["reasons"][0] == "sensitivity:restricted"
    assert any(reason.startswith("source_integrity:") for reason in excluded["reasons"])
    assert (
        "one or more candidates failed stored-source integrity verification"
        in result["gaps"]
    )


def test_retrieval_graph_excludes_source_free_and_restricted_evidence_edges(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    restricted_source = tmp_path / "restricted-evidence.md"
    restricted_source.write_text(
        "# Restricted edge evidence\nA private operator note proposes this relation.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        seed = _active(
            vault,
            title="Orion relation anchor",
            statement="The Orion relation anchor controls the release decision.",
        )
        source_free_neighbor = _active(
            vault,
            title="Legacy source-free neighbor",
            statement="Create a snapshot before the unrelated operation.",
        )
        restricted_neighbor = _active(
            vault,
            title="Restricted-evidence neighbor",
            statement="Apply the isolated rollback sequence after validation.",
        )
        vault.add_relation(
            subject_asset_id=seed,
            predicate="depends_on",
            object_asset_id=source_free_neighbor,
            confirm_reviewed=True,
        )
        compiled = compile_source(
            vault,
            restricted_source,
            source_kind="document",
            sensitivity="restricted",
            confirm_no_case_data=True,
        )
        restricted_manifest = vault.source_review_manifest(
            compiled["source"]["source_id"]
        )
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=restricted_manifest["review_manifest_sha256"],
        )
        evidence = vault.get_asset(compiled["asset_ids"][0], include_inactive=True)
        vault.add_relation(
            subject_asset_id=seed,
            predicate="supports",
            object_asset_id=restricted_neighbor,
            evidence_fragment_id=evidence.source_refs[0].fragment_id,
            confirm_reviewed=True,
        )

    with KnowledgeVault(root, read_only=True) as vault:
        result = retrieve(
            vault,
            "Why does the Orion relation anchor control this decision?",
            mode="graph",
            limit=5,
        )

    selected = {item["asset_id"] for item in result["results"]}
    assert seed in selected
    assert source_free_neighbor not in selected
    assert restricted_neighbor not in selected


def test_graph_channel_follows_two_reviewed_source_bound_hops_with_a_bounded_trace(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "multi-hop.md"
    source.write_text(
        "# Orion anchor\nThe Orion-ANCHOR-7129 controls the release.\n\n"
        "# Intermediate decision\nThe intermediate decision selects the recovery plan.\n\n"
        "# Final procedure\nThe final procedure verifies the offline signature.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        anchor, intermediate, final = compiled["asset_ids"]
        vault.add_relation(
            subject_asset_id=anchor,
            predicate="depends_on",
            object_asset_id=intermediate,
            evidence_fragment_id=vault.get_asset(anchor).source_refs[0].fragment_id,
            confirm_reviewed=True,
        )
        vault.add_relation(
            subject_asset_id=intermediate,
            predicate="implements",
            object_asset_id=final,
            evidence_fragment_id=vault.get_asset(intermediate).source_refs[0].fragment_id,
            confirm_reviewed=True,
        )

        plan = build_query_plan(anchor, mode="graph", limit=5)
        seed = retrieval_fabric._Candidate(anchor)
        seed.add("exact_id", 1, "exact_asset_id_or_uri")
        candidates = {anchor: seed}
        retrieval_fabric._graph_candidates(vault, plan, candidates)

    assert candidates[intermediate].reasons["graph"] == (
        f"reviewed_relation:depends_on:{anchor}"
    )
    assert candidates[final].reasons["graph"] == (
        f"reviewed_relation:hop2:implements:{intermediate}"
    )
    graph_candidates = [
        candidate for candidate in candidates.values() if "graph" in candidate.ranks
    ]
    assert len(graph_candidates) <= plan["channel_budgets"]["graph"]


def test_graph_and_fabric_capsule_follow_latest_source_governance(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    evidence_source = tmp_path / "aquila-evidence.md"
    evidence_source.write_text(
        "# Reviewed relationship evidence\n"
        "The operator reviewed the exact evidence for this knowledge relationship.\n",
        encoding="utf-8",
    )
    query = "Why is the Aquila anchor relevant?"
    with KnowledgeVault(root, read_only=False) as vault:
        seed = _active(
            vault,
            title="Aquila anchor",
            statement="The Aquila anchor is relevant to this operating decision.",
        )
        neighbor = _active(
            vault,
            title="Isolated rollback consequence",
            statement="Create a verified snapshot before applying the rollback sequence.",
        )
        compiled = compile_source(
            vault,
            evidence_source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        evidence = vault.get_asset(compiled["asset_ids"][0])
        relation = vault.add_relation(
            subject_asset_id=seed,
            predicate="supports",
            object_asset_id=neighbor,
            evidence_fragment_id=evidence.source_refs[0].fragment_id,
            confirm_reviewed=True,
        )

        before = retrieve(vault, query, mode="graph", limit=5)
        before_capsule = compile_context(
            vault,
            task=query,
            confirm_no_case_data=True,
            retrieval_result=before,
        )
        assert neighbor in {item["asset_id"] for item in before["results"]}
        assert relation["relation_id"] in {
            item["relation_id"] for item in before_capsule["relations"]
        }

        vault.update_source_governance(
            compiled["source"]["source_id"],
            trust="user_provided",
            sensitivity="restricted",
            export_allowed=False,
            reviewer_id="local-operator",
            reason="Restrict relation evidence after current policy review.",
            confirm_reviewed=True,
        )
        assert vault.relations_for_assets(
            (seed,),
            include_restricted=False,
            require_evidence=True,
        ) == []
        after = retrieve(vault, query, mode="graph", limit=5)
        after_capsule = compile_context(
            vault,
            task=query,
            confirm_no_case_data=True,
            retrieval_result=after,
        )

    assert neighbor not in {item["asset_id"] for item in after["results"]}
    assert after_capsule["relations"] == []


def test_exact_lookup_and_no_answer_are_explicit(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        asset_id = _active(
            vault,
            title="Quasar recovery",
            statement="The recovery identifier is QUASAR-NEEDLE-2048.",
        )

    with KnowledgeVault(root, read_only=True) as vault:
        exact = retrieve(vault, asset_id, mode="exact", limit=1)
        missing = retrieve(
            vault,
            "zzyzx-unavailable-knowledge-99999",
            mode="lexical",
            limit=5,
        )

    assert [item["asset_id"] for item in exact["results"]] == [asset_id]
    assert exact["results"][0]["hit_reason"] == "retrieval_fabric:exact_id"
    assert missing["results"] == []
    assert "no active reviewed knowledge asset" in missing["gaps"][0]


def test_exact_channels_use_semantic_identity_and_indexed_phrase_candidates(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        semantic = _active(
            vault,
            title="Semantic identity target",
            statement="This record is addressed through its stable semantic identity.",
            semantic_key="release.alpha.boundary",
        )
        phrase = _active(
            vault,
            title="Cobalt policy",
            statement="The cobalt release boundary remains immutable during recovery.",
        )

        semantic_result = retrieve(
            vault,
            "release.alpha.boundary",
            mode="exact",
            limit=5,
        )
        phrase_result = retrieve(
            vault,
            'Find "cobalt release boundary" exactly',
            mode="exact",
            limit=5,
        )

    assert semantic in {item["asset_id"] for item in semantic_result["results"]}
    assert semantic_result["trace"]["channel_candidates"]["semantic_key"] == [
        {
            "asset_id": semantic,
            "rank": 1,
            "reason": "exact_semantic_key",
        }
    ]
    assert phrase in {item["asset_id"] for item in phrase_result["results"]}
    assert phrase_result["trace"]["channel_candidates"]["exact_phrase"] == [
        {
            "asset_id": phrase,
            "rank": 1,
            "reason": "exact_phrase:cobalt release boundary",
        }
    ]


def test_read_only_retrieval_does_not_create_profile_state(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        _active(
            vault,
            title="Read-only retrieval",
            statement="A read path must not create ranking profile state.",
        )
    profiles = root / "derived" / "retrieval-profiles"
    assert not profiles.exists()

    with KnowledgeVault(root, read_only=True) as vault:
        assert retrieve(vault, "read-only retrieval", mode="lexical")["results"]

    assert not profiles.exists()


def test_selective_forgetting_removes_current_asset_and_relations_but_keeps_history(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "preferences.md"
    source.write_text(
        "# Old preference\n"
        "The old preference selects the amber deployment profile.\n\n"
        "# Deployment procedure\n"
        "The deployment procedure checks the selected profile.\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        compiled = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        manifest = vault.source_review_manifest(compiled["source"]["source_id"])
        vault.approve_source_assets(
            compiled["source"]["source_id"],
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )
        forgotten, retained = compiled["asset_ids"]
        vault.add_relation(
            subject_asset_id=forgotten,
            predicate="related_to",
            object_asset_id=retained,
            evidence_fragment_id=vault.get_asset(forgotten).source_refs[0].fragment_id,
            confirm_reviewed=True,
        )
        binding = vault.connection.execute(
            """
            SELECT knowledge_revisions_v2.knowledge_key
            FROM asset_revision_bindings_v2
            JOIN knowledge_revisions_v2 USING(asset_revision_id)
            WHERE legacy_asset_id = ?
            """,
            (forgotten,),
        ).fetchone()
        assert binding is not None
        knowledge_key = binding["knowledge_key"]
        forgotten_result = vault.selectively_forget(
            knowledge_key=knowledge_key,
            reason="The operator explicitly removed this obsolete preference.",
            confirm=True,
        )
        assert forgotten_result["history_retained"] is True
        assert forgotten_result["current_relation_count"] == 0
        assert vault.temporal_relations(mode="current")["relations"] == []
        assert vault.temporal_relations(mode="past")["relations"]
        assert vault.knowledge_lineage(knowledge_key=knowledge_key)["revisions"]

    with KnowledgeVault(root, read_only=True) as vault:
        result = retrieve(vault, "amber deployment profile", mode="hybrid")
        assert forgotten not in {item["asset_id"] for item in result["results"]}


def test_selective_forgetting_by_asset_id_supports_legacy_unbound_proposals(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        asset_id = _active(
            vault,
            title="Obsolete personal preference",
            statement="Use the obsolete violet formatter profile.",
            kind="experience",
        )
        forgotten = vault.selectively_forget(
            asset_id=asset_id,
            reason="The operator explicitly removed the obsolete preference.",
            confirm=True,
        )

    assert forgotten["identity_model"] == "legacy-unbound"
    assert forgotten["knowledge_key"] is None
    assert forgotten["asset_revision_id"] is None
    assert forgotten["current_relation_count"] == 0
    assert forgotten["history_retained"] is True

    with KnowledgeVault(root, read_only=True) as vault:
        assert retrieve(vault, "violet formatter profile", mode="hybrid")["results"] == []


def test_retrieval_comparison_is_diagnostic_not_a_performance_claim(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        asset_id = _active(
            vault,
            title="Local evidence rule",
            statement="Local evidence remains source governed.",
        )
    with KnowledgeVault(root, read_only=True) as vault:
        comparison = compare_retrieval(
            vault,
            "local evidence",
            modes=("lexical", "hybrid"),
        )

    assert comparison["inventories"]["lexical"] == [asset_id]
    assert comparison["inventories"]["hybrid"] == [asset_id]
    assert comparison["comparison_is_performance_claim"] is False
