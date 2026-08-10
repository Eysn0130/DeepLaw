from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.knowledge_intelligence import rerank_candidate_views, rerank_candidates
from deeplaw.util import (
    QUERY_EXPANSION_CONFIGURATION,
    QUERY_EXPANSION_PROFILE,
    QUERY_EXPANSION_PROFILE_V1,
    QUERY_EXPANSION_PROFILE_V2,
    QUERY_EXPANSION_PROFILE_V2_METADATA,
    QUERY_EXPANSION_PROFILE_V2_SHA256,
    canonical_json,
    normalize_query_text,
    query_discovery_text,
    query_discovery_views,
    query_expansion_terms,
    query_identity_anchor_match,
    query_target_anchors,
    sha256_bytes,
)


def _literal_alias_maps() -> dict[str, dict[str, tuple[str, ...]]]:
    source_path = Path(__file__).parents[1] / "src" / "deeplaw" / "util.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = {
        "_QUERY_CROSS_LANGUAGE_ALIASES_V1",
        "_QUERY_CROSS_LANGUAGE_ALIASES_V2",
    }
    maps: dict[str, dict[str, tuple[str, ...]]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        name = next((target for target in targets if target in names), None)
        if name is None:
            continue
        value = ast.literal_eval(node.value)
        assert isinstance(value, dict)
        maps[name] = value
    assert set(maps) == names
    return maps


def _frozen_query_strings() -> list[str]:
    gold_path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "semantic"
        / "semantic-gold-candidate-v1.json"
    )
    frozen = json.loads(gold_path.read_text(encoding="utf-8"))
    queries: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "query" and isinstance(item, str):
                    queries.append(item)
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(frozen)
    assert queries
    return queries


def _frozen_multiword_fragments(query: str) -> set[str]:
    normalized = normalize_query_text(query).casefold()
    fragments = {normalized}
    for match in re.finditer(
        r"[a-z0-9]+(?:[-_.][a-z0-9]+)*(?:\s+[a-z0-9]+(?:[-_.][a-z0-9]+)*)+",
        normalized,
    ):
        fragments.add(match.group(0))
    for run in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", normalized):
        for size in range(3, len(run) + 1):
            fragments.update(run[index : index + size] for index in range(len(run) - size + 1))
    return fragments


def test_v2_profile_is_closed_immutable_and_canonical_hash_bound() -> None:
    contract = json.loads(
        (
            Path(__file__).parents[1]
            / "contracts"
            / "query-expansion-profile.v2.schema.json"
        ).read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(contract)
    Draft202012Validator(contract, format_checker=FormatChecker()).validate(
        QUERY_EXPANSION_PROFILE_V2_METADATA
    )
    body = {
        key: value
        for key, value in QUERY_EXPANSION_PROFILE_V2_METADATA.items()
        if key != "profile_sha256"
    }
    assert QUERY_EXPANSION_PROFILE_V2_METADATA["profile_sha256"] == sha256_bytes(
        canonical_json(body).encode("utf-8")
    )
    assert (
        QUERY_EXPANSION_PROFILE_V2_METADATA["profile_sha256"]
        == QUERY_EXPANSION_PROFILE_V2_SHA256
    )
    maps = _literal_alias_maps()
    assert QUERY_EXPANSION_PROFILE_V2_METADATA["lexicon_sha256"] == sha256_bytes(
        canonical_json(maps["_QUERY_CROSS_LANGUAGE_ALIASES_V2"]).encode("utf-8")
    )
    assert QUERY_EXPANSION_PROFILE_V2_METADATA["max_terms"] == 24
    assert QUERY_EXPANSION_PROFILE_V2_METADATA["match_policy"] == (
        "normalized-casefold-substring"
    )
    assert QUERY_EXPANSION_PROFILE == QUERY_EXPANSION_PROFILE_V2
    assert QUERY_EXPANSION_PROFILE_V1 == "deeplaw-deterministic-query-expansion/1"
    assert QUERY_EXPANSION_PROFILE_V2 == "deeplaw-deterministic-query-expansion/2"


def test_v2_uses_generic_atomic_concepts_and_excludes_benchmark_phrases() -> None:
    terms = query_expansion_terms("组织与别名来源政策之间的冲突")
    assert terms == sorted(set(terms))
    assert {"organization", "alias", "source", "policy", "conflict"} <= set(terms)
    v2_keys = {
        phrase
        for phrase in ("组织", "别名", "来源", "政策", "冲突")
        if phrase in "组织别名来源政策冲突"
    }
    assert v2_keys == {"组织", "别名", "来源", "政策", "冲突"}
    for phrase in ("Atlas", "诊断日志", "保留政策", "验证徽章", "计划发布", "审阅完成"):
        assert phrase.casefold() not in " ".join(v2_keys).casefold()
    assert query_expansion_terms("Atlas 审阅完成 2025-06-01 发布") == []
    assert {
        "diagnostic",
        "retention",
        "period",
        "support",
        "verification",
        "badge",
        "exact",
        "color",
    } <= set(query_expansion_terms("诊断保留期限支持验证徽章精确颜色"))


def test_v1_compatibility_is_explicit_and_default_is_v2() -> None:
    query = "诊断日志保留政策"
    legacy_terms = query_expansion_terms(query, profile=QUERY_EXPANSION_PROFILE_V1)
    assert {"log", "policy"} <= set(legacy_terms)
    assert "diagnostic" not in legacy_terms
    assert "retention" not in legacy_terms
    assert {"diagnostic", "retention"} <= set(query_expansion_terms(query))
    explanation = query_expansion_terms("组织与冲突", explain=True)
    assert explanation["profile_id"] == QUERY_EXPANSION_PROFILE_V2
    assert explanation["profile_sha256"] == QUERY_EXPANSION_PROFILE_V2_SHA256
    assert explanation["rule_ids"] == [
        "script-normalization-v1",
        "atomic-bilingual-concepts-v1",
    ]


def test_positive_negative_paraphrases_are_hand_written_and_bounded() -> None:
    assert "organization" in query_expansion_terms("组织准入")
    assert "admission" in query_expansion_terms("组织准入")
    assert query_expansion_terms("Atlas diagnostic badge publication") == []
    with pytest.raises(ValueError):
        query_expansion_terms("x" * 20_001)


def test_discovery_keeps_source_and_identity_anchors_are_generic_and_bounded() -> None:
    source = "诊断日志保留政策"
    discovery = query_discovery_text(source)
    assert source in discovery
    assert {"diagnostic", "retention", "policy"} <= set(discovery.split())
    views = query_discovery_views(source)
    assert views[0] == source
    assert {"diagnostic", "retention", "policy"} <= set(views[1].split())
    assert not any("诊断" in view for view in views[1:])
    assert QUERY_EXPANSION_CONFIGURATION["reranker_fusion_policy"].endswith("/1")

    assert query_target_anchors("What does Policy Alpha require?")[0] == (
        "policy alpha",
    )
    assert query_target_anchors("Compare Current requirements")[0] == ()
    assert query_target_anchors("Policy Alpha and Policy Beta comparison")[0] == (
        "policy alpha",
        "policy beta",
    )
    assert query_target_anchors("Alpha 政策 archive requirements")[0] == ("alpha",)
    # A single sentence-initial homonym is not an anchor; lexical discovery
    # must retain both meanings until another admission signal disambiguates.
    assert query_target_anchors("Mercury policy")[0] == ()
    assert query_target_anchors("Tell me about Mercury policy")[0] == ("mercury",)


def test_compound_anchor_keeps_repeated_independent_singleton_and_word_boundaries() -> None:
    query = (
        "Atlas 审阅完成 2025-06-01；Atlas 计划发布 2025-07-01；Atlas Protocol"
    )
    anchors, truncated = query_target_anchors(query)
    assert anchors == ("atlas protocol", "atlas")
    assert truncated is False
    assert query_identity_anchor_match("atlas", "Atlas review completed on 2025-06-01")
    assert not query_identity_anchor_match("atlas protocol", "AtlasProtocol release")
    assert not query_identity_anchor_match("atlas", "Borealis Atlasic release")
    opaque, opaque_truncated = query_target_anchors("NO-SUCH-FACT-CHI")
    assert opaque == ("no such fact chi",)
    assert opaque_truncated is False
    assert not query_identity_anchor_match(opaque[0], "Known Fact Alpha")


def test_reranker_view_fusion_keeps_highest_real_view_score() -> None:
    candidates = [
        {
            "knowledge_id": "knowledge_000000000000000000000001",
            "title": "Evidence source summary",
            "body": "The source summary records evidence admission.",
            "semantic_key": "source-summary:evidence-admission",
            "epistemic_state": "supported",
            "feedback_utility": 0.0,
        },
        {
            "knowledge_id": "knowledge_000000000000000000000002",
            "title": "Unrelated procedure",
            "body": "The procedure has no source summary.",
            "semantic_key": "procedure:unrelated",
            "epistemic_state": "supported",
            "feedback_utility": 0.0,
        },
    ]
    source_ranked = rerank_candidates("摘要说明证据准入来源", candidates)
    expansion_ranked = rerank_candidates("evidence admission source summary", candidates)
    fused = rerank_candidate_views(
        ("摘要说明证据准入来源", "evidence admission source summary"),
        candidates,
    )
    source_scores = {
        item["knowledge_id"]: item["reranker_score"] for item in source_ranked
    }
    expansion_scores = {
        item["knowledge_id"]: item["reranker_score"] for item in expansion_ranked
    }
    expected = {
        identity: max(source_scores[identity], expansion_scores[identity])
        for identity in source_scores
    }
    assert {
        item["knowledge_id"]: item["reranker_score"] for item in fused
    } == expected
    assert all(item["reranker_profile"] for item in fused)


def test_product_source_has_no_benchmark_or_gold_imports() -> None:
    source_path = Path(__file__).parents[1] / "src" / "deeplaw" / "util.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        assert not any(
            name.startswith(("benchmarks", "evals", "tests")) for name in names
        )


def test_ast_maps_are_atomic_and_disjoint_from_frozen_multiword_phrases() -> None:
    maps = _literal_alias_maps()
    frozen_queries = _frozen_query_strings()
    forbidden = re.compile(r"atlas|policy\s*[ab]|\b\d{4}-\d{2}-\d{2}\b", re.I)
    ascii_token = re.compile(r"^[a-z0-9]+(?:[-_.][a-z0-9]+)*$", re.I)
    cjk = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

    for mapping in maps.values():
        for raw_key, aliases in mapping.items():
            key = normalize_query_text(raw_key).casefold()
            if cjk.search(key):
                assert len(key) <= 2
                assert all(cjk.fullmatch(char) for char in key)
            else:
                assert ascii_token.fullmatch(key)
            assert not forbidden.search(key)
            assert isinstance(aliases, tuple)
            for alias in aliases:
                assert ascii_token.fullmatch(alias)
                assert not forbidden.search(alias)

            for query in frozen_queries:
                assert key not in _frozen_multiword_fragments(query)
