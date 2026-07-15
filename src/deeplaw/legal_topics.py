from __future__ import annotations

from dataclasses import dataclass

from .util import compact_text, normalize_article_label


@dataclass(frozen=True, slots=True)
class LegalTopicLocator:
    """An immutable locator bound to one reviewed source artifact."""

    document_title: str
    source_sha256: str
    article_label: str

    def __post_init__(self) -> None:
        if not compact_text(self.document_title):
            raise ValueError("legal topic locator document_title is required")
        if len(self.source_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_sha256
        ):
            raise ValueError("legal topic locator source_sha256 must be lowercase SHA-256")
        if normalize_article_label(self.article_label) != self.article_label:
            raise ValueError("legal topic locator article_label must be canonical")


@dataclass(frozen=True, slots=True)
class LegalTopicAnchor:
    """A source-bound legal concept locator, never a free-form semantic guess."""

    canonical_term: str
    query_aliases: tuple[str, ...]
    document_title: str
    source_sha256: str
    article_label: str
    supporting_locators: tuple[LegalTopicLocator, ...] = ()

    def __post_init__(self) -> None:
        if not compact_text(self.canonical_term):
            raise ValueError("legal topic canonical_term is required")
        if len(set(self.query_aliases)) != len(self.query_aliases):
            raise ValueError("legal topic query_aliases must be unique")
        if len(self.source_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_sha256
        ):
            raise ValueError("legal topic source_sha256 must be lowercase SHA-256")
        if normalize_article_label(self.article_label) != self.article_label:
            raise ValueError("legal topic article_label must be canonical")
        if len(set(self.locators)) != len(self.locators):
            raise ValueError("legal topic locators must be unique")

    @property
    def query_terms(self) -> tuple[str, ...]:
        return (self.canonical_term, *self.query_aliases)

    @property
    def locators(self) -> tuple[LegalTopicLocator, ...]:
        return (
            LegalTopicLocator(
                document_title=self.document_title,
                source_sha256=self.source_sha256,
                article_label=self.article_label,
            ),
            *self.supporting_locators,
        )


# These locators are deliberately small and source-bound. A term is added only
# when the checked-in official source identifies a deterministic primary-rule
# locator. Unknown concepts fail closed instead of inheriting a nearby offence.
_CRIMINAL_LAW_TITLE = "中华人民共和国刑法（2020年修正）"
_CRIMINAL_LAW_SHA256 = "282fee49f12b6420a1a5ecde4ea2aa58b0e496e566cf959378bd1f8edb53f5e6"
_PROSECUTION_STANDARDS_TITLE = (
    "最高人民检察院、公安部关于公安机关管辖的刑事案件立案追诉标准的规定（二）"
)
_PROSECUTION_STANDARDS_SHA256 = "26e82081c441c0237a3b43581550f0fd77db4e817e291b40793499aa03df6954"


def _prosecution_standard(article_label: str) -> LegalTopicLocator:
    return LegalTopicLocator(
        document_title=_PROSECUTION_STANDARDS_TITLE,
        source_sha256=_PROSECUTION_STANDARDS_SHA256,
        article_label=article_label,
    )


def _criminal_law_anchor(
    canonical_term: str,
    article_label: str,
    *,
    query_aliases: tuple[str, ...] = (),
    prosecution_standard: str | None = None,
) -> LegalTopicAnchor:
    return LegalTopicAnchor(
        canonical_term=canonical_term,
        query_aliases=query_aliases,
        document_title=_CRIMINAL_LAW_TITLE,
        source_sha256=_CRIMINAL_LAW_SHA256,
        article_label=article_label,
        supporting_locators=(
            (_prosecution_standard(prosecution_standard),)
            if prosecution_standard is not None
            else ()
        ),
    )


LEGAL_TOPIC_ANCHORS: tuple[LegalTopicAnchor, ...] = (
    _criminal_law_anchor("洗钱罪", "第一百九十一条"),
    _criminal_law_anchor("集资诈骗罪", "第一百九十二条", prosecution_standard="第四十四条"),
    _criminal_law_anchor("贷款诈骗罪", "第一百九十三条", prosecution_standard="第四十五条"),
    _criminal_law_anchor("票据诈骗罪", "第一百九十四条", prosecution_standard="第四十六条"),
    _criminal_law_anchor("金融凭证诈骗罪", "第一百九十四条", prosecution_standard="第四十七条"),
    _criminal_law_anchor("信用证诈骗罪", "第一百九十五条", prosecution_standard="第四十八条"),
    _criminal_law_anchor("信用卡诈骗罪", "第一百九十六条", prosecution_standard="第四十九条"),
    _criminal_law_anchor("有价证券诈骗罪", "第一百九十七条", prosecution_standard="第五十条"),
    _criminal_law_anchor("保险诈骗罪", "第一百九十八条", prosecution_standard="第五十一条"),
    _criminal_law_anchor("合同诈骗罪", "第二百二十四条", prosecution_standard="第六十九条"),
    _criminal_law_anchor("诈骗罪", "第二百六十六条", query_aliases=("诈骗",)),
)


def resolve_legal_topic(topic: str) -> LegalTopicAnchor | None:
    normalized = compact_text(topic)
    if not normalized:
        return None
    matches = [
        anchor
        for anchor in LEGAL_TOPIC_ANCHORS
        if normalized in {compact_text(term) for term in anchor.query_terms}
    ]
    if len(matches) > 1:
        raise RuntimeError(f"ambiguous checked-in legal topic anchor: {topic}")
    return matches[0] if matches else None
