from __future__ import annotations

from deeplaw.legal_topics import resolve_legal_topic


def test_reviewed_topic_locators_are_source_bound_and_include_supporting_rules() -> None:
    anchor = resolve_legal_topic("贷款诈骗罪")

    assert anchor is not None
    assert [(locator.article_label, len(locator.source_sha256)) for locator in anchor.locators] == [
        ("第一百九十三条", 64),
        ("第四十五条", 64),
    ]
    assert anchor.locators[0].document_title == "中华人民共和国刑法（2020年修正）"
    assert "立案追诉标准" in anchor.locators[1].document_title


def test_unknown_or_ambiguous_topic_does_not_inherit_a_neighboring_anchor() -> None:
    assert resolve_legal_topic("诈骗犯罪") is None
    assert resolve_legal_topic("金融诈骗犯罪") is None
    assert resolve_legal_topic("不存在的罪名") is None


def test_bare_fraud_navigation_resolves_only_the_reviewed_general_rule() -> None:
    anchor = resolve_legal_topic("诈骗")

    assert anchor is not None
    assert anchor.canonical_term == "诈骗罪"
    assert [locator.article_label for locator in anchor.locators] == ["第二百六十六条"]
