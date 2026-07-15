from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, cast

from .evidence_compiler import (
    EvidenceCandidate,
    EvidenceDuty,
    compile_evidence,
)
from .legal_topics import LegalTopicAnchor, resolve_legal_topic
from .models import (
    EvidenceCard,
    GraphPath,
    ObligationCoverage,
    SearchGap,
    SearchRequest,
    SearchResponse,
)
from .query_plan import ObligationId, QueryObligation, QueryPlan, compile_query_plan
from .store import (
    SCHEMA_VERSION,
    connect_readonly,
    release_info,
    resolve_active_database,
    verify_release_artifact,
)
from .util import (
    article_pattern,
    canonical_date,
    compact_text,
    excerpt,
    fts_query,
    normalize_article_label,
    normalize_text,
    search_terms,
    sha256_bytes,
    stable_id,
)

SEARCH_RESPONSE_SCHEMA = "deeplaw.search-response/v2"
EVIDENCE_CARD_SCHEMA = "deeplaw.legal-evidence-card/v2"
MAX_GRAPH_PATHS = 4
MAX_GRAPH_HOPS = 1
_COUNTEREVIDENCE_MARKERS = ("但书", "除外", "不适用", "除非", "另有规定", "但是", "例外")
_COUNTEREVIDENCE_PREDICATES = {"exception_to", "repeals", "replaces", "amends"}
_ELEMENTS_EVIDENCE_MARKERS = (
    "构成要件",
    "成立条件",
    "适用范围",
    "应当具备",
    "本法所称",
    "本规定所称",
    "是指",
    "定义",
)
_INTERPRETATION_EVIDENCE_MARKERS = ("本法所称", "本规定所称", "是指", "含义", "解释")
_PROCEDURE_EVIDENCE_MARKERS = (
    "程序",
    "管辖",
    "期限",
    "时限",
    "立案",
    "受理",
    "申请",
    "审查",
    "举证",
    "执行",
)
_DEFERRED_OR_ABSENT_CONTENT_MARKERS = (
    "另见",
    "详见",
    "参见",
    "见附件",
    "另行规定",
    "具体内容见",
    "具体规定见",
    "未收录",
    "不载明",
    "未载明",
)
_PRIMARY_RULE_MARKERS = (
    "应当",
    "不得",
    "可以",
    "依照",
    "按照",
    "有权",
    "负责",
    "必须",
    "是指",
    "认定",
    "适用",
    "义务",
    "责任",
)
_NON_CURRENT_STATUSES = {
    "verified_historical",
    "not_yet_effective",
    "repealed",
    "superseded",
}
_GapCode = Literal[
    "exact_target_unresolved",
    "query_focus_unresolved",
    "temporal_metadata_unverified",
    "temporal_out_of_scope",
    "required_obligation_uncovered",
    "required_obligation_uncertain",
    "no_primary_evidence",
]
_TITLE_QUALIFIER = re.compile(r"[（(][^）)]{1,40}[）)]")
_APPLICABILITY_INTERPRETATION_TITLE = re.compile(r"适用[《〈](?P<law>[^》〉]{2,120})[》〉]的解释")
_QUERY_VERSION_SUFFIX = re.compile(
    r"(?:"
    r"\d{4}年?(?:修正|修订|修改|施行|版)?|"
    r"现行(?:有效|整合文本|版本)?|"
    r"最新版本"
    r")$"
)
_FOCUS_PHRASE_SPLIT = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
_GENERIC_NAVIGATION_TERMS = {"法律", "法规", "条例", "规定", "办法", "意见", "通知"}
_CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_TOPIC_LEFT_CONTEXTS = (
    "关于",
    "办理",
    "审理",
    "惩治",
    "适用",
    "有关",
    "涉嫌",
    "构成",
    "触犯",
    "认定为",
    "按照",
    "犯",
)
_TOPIC_INTENT_MARKERS = tuple(
    sorted(
        {
            "数额特别巨大标准",
            "立案追诉标准",
            "数额较大标准",
            "数额巨大标准",
            "构成要件",
            "成立条件",
            "适用范围",
            "数额标准",
            "金额标准",
            "数量标准",
            "立案标准",
            "追诉标准",
            "入罪标准",
            "定罪标准",
            "量刑标准",
            "如何认定",
            "怎么认定",
            "如何理解",
            "法律依据",
            "司法解释",
            "办理程序",
            "程序和期限",
            "要件",
            "定义",
            "概念",
            "立案",
            "管辖",
            "期限",
            "例外",
            "但书",
            "除外",
            "不适用",
        },
        key=lambda value: (-len(value), value),
    )
)
_TOPIC_PREFIX_NOISE = ("请问", "关于", "有关", "针对")
_TOPIC_SUFFIX_NOISE = ("是什么", "有哪些", "如何", "怎么", "是否", "相关", "的", "吗", "呢")
_NUMBER_TOKEN = r"[0-9〇零一二两三四五六七八九十百千万亿点\.]+"
_AMOUNT_THRESHOLD_PATTERN = re.compile(
    rf"(?:数额|金额|价款|价值|所得|损失|资金).{{0,80}}?"
    rf"{_NUMBER_TOKEN}(?:万|亿)?元(?:以上|以下|以内|不满|超过|达到|至)?"
)
_COUNT_THRESHOLD_PATTERN = re.compile(
    rf"(?:数量|次数|人数).{{0,80}}?{_NUMBER_TOKEN}(?:次|人|件|个)"
    r"(?:以上|以下|以内|不满|超过|达到|至)?"
)
_SENTENCING_THRESHOLD_PATTERN = re.compile(
    rf"处.{{0,20}}?(?:{_NUMBER_TOKEN}年(?:以上|以下|以内|不满)?|拘役|管制|无期徒刑|死刑)"
)
_FILING_STANDARD_MARKERS = (
    "应予立案追诉",
    "应当立案追诉",
    "应予追诉",
    "涉嫌下列情形之一",
)


def _simplify_document_key(value: str) -> str:
    return value.replace("关于", "").replace("的", "")


def _defers_or_disclaims_content(text: str) -> bool:
    """Return true when a hit points away from content absent from this release."""

    compact = compact_text(text)
    return any(marker in compact for marker in _DEFERRED_OR_ABSENT_CONTENT_MARKERS)


def _contains_primary_rule(text: str) -> bool:
    if _defers_or_disclaims_content(text):
        return False
    normalized = normalize_text(text)
    if bool(article_pattern().search(normalized)):
        return True
    # A chapter title such as “法律责任” is navigation, not a substantive rule.
    # Marker-only text must contain enough surrounding language to express an
    # actual proposition before it can witness the primary-rule duty.
    return len(compact_text(normalized)) >= 16 and any(
        marker in normalized for marker in _PRIMARY_RULE_MARKERS
    )


def _normalize_focus_text(value: str) -> str:
    """Canonicalize small drafting variants without rewriting legal meaning."""

    return compact_text(value).replace("以内", "内").replace("之内", "内")


def _focus_phrases(value: str) -> tuple[str, ...]:
    phrases: dict[str, None] = {}
    for raw in _FOCUS_PHRASE_SPLIT.split(normalize_text(value)):
        normalized = _normalize_focus_text(raw)
        if len(normalized) >= 2:
            phrases[normalized] = None
        if len(phrases) >= 16:
            break
    return tuple(phrases)


def _focus_relevance(value: str, body: str) -> float:
    """Score query focus against body text, excluding document-title leakage."""

    if not value:
        return 0.0
    normalized_body = _normalize_focus_text(body)
    phrases = _focus_phrases(value)
    phrase_weight = sum(min(len(phrase), 8) for phrase in phrases)
    phrase_coverage = (
        sum(min(len(phrase), 8) for phrase in phrases if phrase in normalized_body) / phrase_weight
        if phrase_weight
        else 0.0
    )
    query_terms = set(search_terms(_normalize_focus_text(value), limit=36))
    body_terms = set(search_terms(normalized_body, limit=256))
    term_coverage = len(query_terms & body_terms) / max(1, len(query_terms))
    return phrase_coverage * 0.7 + term_coverage * 0.3


def _topic_inquiry_phrase(value: str, *, navigation: bool) -> str | None:
    """Extract a short deterministic topic only for bounded issue/navigation queries."""

    normalized = compact_text(value)
    if not normalized:
        return None
    has_intent = any(marker in normalized for marker in _TOPIC_INTENT_MARKERS)
    if not has_intent and not navigation:
        return None
    topic = normalized
    if has_intent:
        marker_offsets = [
            normalized.find(compact_text(marker))
            for marker in _TOPIC_INTENT_MARKERS
            if compact_text(marker) in normalized
        ]
        first_issue_offset = min(marker_offsets)
        # Legal questions overwhelmingly put the legal topic before the issue
        # phrase. Taking that prefix also handles overlapping expressions such
        # as “办理程序和期限”, which must not leave “办理” behind as a fake topic.
        if first_issue_offset > 0:
            topic = normalized[:first_issue_offset]
        else:
            for marker in _TOPIC_INTENT_MARKERS:
                topic = topic.replace(compact_text(marker), "")
    changed = True
    while changed and topic:
        changed = False
        for prefix in _TOPIC_PREFIX_NOISE:
            if topic.startswith(prefix):
                topic = topic[len(prefix) :]
                changed = True
        for suffix in _TOPIC_SUFFIX_NOISE:
            if topic.endswith(suffix):
                topic = topic[: -len(suffix)]
                changed = True
    if 2 <= len(topic) <= 24 and topic not in _GENERIC_NAVIGATION_TERMS:
        return topic
    return None


def _topic_phrase_matches(phrase: str, value: str) -> bool:
    """Match exact topic text while rejecting embedded compound offence names."""

    topic = compact_text(phrase)
    body = compact_text(value)
    if not topic or not body:
        return False
    strict_left_boundary = topic.endswith(("罪", "犯罪"))
    offset = 0
    while (index := body.find(topic, offset)) >= 0:
        if not strict_left_boundary or index == 0:
            return True
        prefix = body[:index]
        if not _CJK_CHARACTER.fullmatch(prefix[-1]) or prefix.endswith(_TOPIC_LEFT_CONTEXTS):
            return True
        offset = index + 1
    return False


def _anchor_matches_card(anchor: LegalTopicAnchor, card: EvidenceCard) -> bool:
    if any(
        card.title == locator.document_title
        and card.source_sha256 == locator.source_sha256
        and card.article_label == locator.article_label
        for locator in anchor.locators
    ):
        return True
    visible_text = f"{card.title} {card.heading or ''} {card.excerpt}"
    return _topic_phrase_matches(anchor.canonical_term, visible_text)


def _anchor_primary_matches_card(anchor: LegalTopicAnchor, card: EvidenceCard) -> bool:
    return (
        card.title == anchor.document_title
        and card.source_sha256 == anchor.source_sha256
        and card.article_label == anchor.article_label
    )


def _anchor_matches_row(anchor: LegalTopicAnchor, row: sqlite3.Row) -> bool:
    if any(
        row["title"] == locator.document_title
        and row["source_sha256"] == locator.source_sha256
        and row["article_label"] == locator.article_label
        for locator in anchor.locators
    ):
        return True
    return _topic_phrase_matches(
        anchor.canonical_term,
        f"{row['title']} {row['heading'] or ''} {row['text']}",
    )


def _row_matches_topic(
    row: sqlite3.Row,
    *,
    anchor: LegalTopicAnchor | None,
    topic_phrase: str | None,
) -> bool:
    if anchor is not None:
        return _anchor_matches_row(anchor, row)
    if topic_phrase is None:
        return True
    return _topic_phrase_matches(
        topic_phrase,
        f"{row['title']} {row['heading'] or ''} {row['text']}",
    )


def _card_matches_topic(
    card: EvidenceCard,
    *,
    anchor: LegalTopicAnchor | None,
    topic_phrase: str | None,
) -> bool:
    if anchor is not None:
        return _anchor_matches_card(anchor, card)
    if topic_phrase is None:
        return True
    return _topic_phrase_matches(
        topic_phrase,
        f"{card.title} {card.heading or ''} {card.excerpt}",
    )


def _contains_threshold_standard(obligation: QueryObligation, text: str) -> bool:
    normalized_text = re.sub(r"\s+", "", normalize_text(text))
    cues = obligation.query_cues
    checks: list[bool] = []
    if any("立案" in cue or "追诉" in cue for cue in cues):
        checks.append(any(marker in normalized_text for marker in _FILING_STANDARD_MARKERS))
    if any(any(term in cue for term in ("数额", "金额")) for cue in cues):
        checks.append(bool(_AMOUNT_THRESHOLD_PATTERN.search(normalized_text)))
    if any("数量" in cue for cue in cues):
        checks.append(bool(_COUNT_THRESHOLD_PATTERN.search(normalized_text)))
    if any("量刑" in cue for cue in cues):
        checks.append(bool(_SENTENCING_THRESHOLD_PATTERN.search(normalized_text)))
    if any("入罪" in cue or "定罪" in cue for cue in cues):
        checks.append(
            bool(
                _AMOUNT_THRESHOLD_PATTERN.search(normalized_text)
                or _COUNT_THRESHOLD_PATTERN.search(normalized_text)
                or any(marker in normalized_text for marker in _FILING_STANDARD_MARKERS)
            )
        )
    return bool(checks) and all(checks)


def _card_matches_obligation(obligation: QueryObligation, card: EvidenceCard) -> bool:
    substantive = not _defers_or_disclaims_content(card.excerpt)
    if obligation.id is ObligationId.EXACT_CITATION:
        return card.retrieval_channel in {"article_exact", "title_exact"}
    if obligation.id is ObligationId.PRIMARY_RULE:
        return (
            substantive
            and card.document_type != "case_reference"
            and _contains_primary_rule(card.excerpt)
        )
    if obligation.id is ObligationId.TEMPORAL_STATUS_VERSION:
        return card.temporal_classification in {
            "verified_in_scope",
            "unverified_metadata",
        }
    if obligation.id is ObligationId.ELEMENTS_DEFINITIONS:
        return substantive and any(marker in card.excerpt for marker in _ELEMENTS_EVIDENCE_MARKERS)
    if obligation.id is ObligationId.INTERPRETATION:
        return substantive and (
            card.document_type == "judicial_interpretation"
            or "解释" in card.title
            or any(marker in card.excerpt for marker in _INTERPRETATION_EVIDENCE_MARKERS)
        )
    if obligation.id is ObligationId.PROCEDURE:
        return substantive and any(marker in card.excerpt for marker in _PROCEDURE_EVIDENCE_MARKERS)
    if obligation.id is ObligationId.THRESHOLD_STANDARD:
        return substantive and _contains_threshold_standard(obligation, card.excerpt)
    if obligation.id is ObligationId.EXCEPTIONS_COUNTEREVIDENCE:
        return substantive and any(marker in card.excerpt for marker in _COUNTEREVIDENCE_MARKERS)
    if obligation.id is ObligationId.CASE_REFERENCE:
        return substantive and card.document_type == "case_reference"
    return False


def _card_duty_ids(plan: QueryPlan, card: EvidenceCard) -> tuple[str, ...]:
    """Return deterministic duties witnessed by one exact evidence card."""

    witnessed: list[str] = []
    for obligation in plan.obligations:
        if _card_matches_obligation(obligation, card):
            witnessed.append(obligation.id.value)
    return tuple(witnessed)


def _document_query_keys(title: str) -> tuple[tuple[str, int], ...]:
    raw = compact_text(title)
    core = compact_text(_TITLE_QUALIFIER.sub("", title))
    keys: dict[str, int] = {raw: 3, core: 3}
    prefix = compact_text("中华人民共和国")
    for key in (raw, core):
        if key.startswith(prefix) and len(key) > len(prefix):
            keys[key[len(prefix) :]] = max(keys.get(key[len(prefix) :], 0), 2)
    for suffix in (
        "实施细则",
        "管理办法",
        "办法",
        "条例",
        "规定",
        "解释",
        "决定",
        "意见",
        "通知",
        "公告",
    ):
        if core.endswith(suffix) and len(core) - len(suffix) >= 4:
            keys[core[: -len(suffix)]] = max(keys.get(core[: -len(suffix)], 0), 1)
    for key, priority in tuple(keys.items()):
        simplified = _simplify_document_key(key)
        if len(simplified) >= 2:
            keys[simplified] = max(keys.get(simplified, 0), min(priority, 2))
    interpretation_match = _APPLICABILITY_INTERPRETATION_TITLE.search(normalize_text(title))
    if interpretation_match:
        law_name = compact_text(interpretation_match.group("law"))
        for value in (law_name, law_name.removeprefix(compact_text("中华人民共和国"))):
            if len(value) >= 4:
                alias = f"{value}解释"
                keys[alias] = max(keys.get(alias, 0), 3)
    return tuple((key, priority) for key, priority in keys.items() if len(key) >= 2)


def _target_query_forms(query: str) -> tuple[str, ...]:
    forms: dict[str, None] = {}
    compact = compact_text(query)
    for value in (compact, _simplify_document_key(compact)):
        if len(value) >= 2:
            forms[value] = None
        without_version = _QUERY_VERSION_SUFFIX.sub("", value)
        if len(without_version) >= 4:
            forms[without_version] = None
    return tuple(forms)


class DeepLaw:
    def __init__(
        self,
        database: str | Path | None = None,
        *,
        home: str | Path | None = None,
        expected_scope: Literal["official", "user_private"] | None = None,
    ):
        self.database = resolve_active_database(explicit_db=database, home=home)
        self.artifact = verify_release_artifact(self.database)
        self.connection = connect_readonly(self.database)
        self.info = release_info(self.connection)
        if self.info.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported DeepLaw release schema: {self.info.get('schema_version')}"
            )
        self.release_id = str(self.info["release_id"])
        if self.artifact.get("release_id") != self.release_id:
            self.connection.close()
            raise RuntimeError("release database metadata does not match release.json")
        self.collection_scope = str(self.artifact.get("collection_scope", "official"))
        if expected_scope is not None and self.collection_scope != expected_scope:
            self.connection.close()
            raise RuntimeError(f"expected {expected_scope} release, got {self.collection_scope}")
        release = self.info.get("release", {})
        artifact_release = {
            key: value for key, value in self.artifact.items() if key != "database_sha256"
        }
        if (
            not isinstance(release, dict)
            or release != artifact_release
            or self.info.get("document_count") != self.artifact.get("document_count")
            or self.info.get("segment_count") != self.artifact.get("segment_count")
        ):
            self.connection.close()
            raise RuntimeError("release database metadata does not match release.json")
        self.temporal_metadata_verified = release.get("temporal_status") == "verified"
        self.temporal_reviewed_on = release.get("reviewed_on")
        self.document_identifiers = self._load_document_identifiers()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DeepLaw:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def release_info(self) -> dict[str, Any]:
        value = dict(self.info)
        value["release"] = dict(self.artifact)
        value["database_sha256"] = self.artifact["database_sha256"]
        return value

    def search(self, request: SearchRequest) -> SearchResponse:
        request = request.normalized()
        if not request.query:
            raise ValueError("query is required")
        if request.as_of:
            canonical_date(request.as_of, field="as_of")

        route = self._route(request)
        document_title_only = self._is_document_title_query(request.query)
        compiled_plan = compile_query_plan(
            request.query,
            request.purpose,
            route,
            request.as_of,
            document_title_only=document_title_only,
        )
        temporal_intent = any(
            obligation.id is ObligationId.TEMPORAL_STATUS_VERSION
            for obligation in compiled_plan.obligations
        )
        exact_target_resolved = route != "exact" or bool(self._target_document_ids(request.query))
        explicit_document_target = bool(
            self._target_document_ids(request.query) or self._mentioned_document_ids(request.query)
        )
        focus_query = self._query_focus(request.query)
        topic_phrase = (
            None
            if explicit_document_target
            else _topic_inquiry_phrase(
                focus_query,
                navigation=route == "navigation",
            )
        )
        topic_anchor = resolve_legal_topic(topic_phrase or "")
        if topic_anchor is None and not explicit_document_target:
            # A host may explicitly route a short legal topic to research
            # instead of navigation. Known source-bound topics must retain the
            # same identity gate regardless of that host-side routing choice.
            topic_anchor = resolve_legal_topic(focus_query)
            if topic_anchor is not None:
                topic_phrase = topic_anchor.canonical_term
        candidates = self._candidate_rows(request, route)
        if topic_anchor is not None:
            anchor_rows = self._topic_anchor_rows(
                topic_anchor,
                document_types=request.document_types,
            )
            if anchor_rows:
                candidates = [*anchor_rows, *candidates]
            else:
                # A source-bound registry entry is not portable proof that the
                # same artifact exists in a user-owned or synthetic release.
                # Keep the textual topic gate, but never pretend the absent
                # official locator was resolved.
                topic_anchor = None
        navigation_title_targets = (
            self._navigation_title_document_ids(request.query) if route == "navigation" else ()
        )
        if route == "navigation":
            candidates = self._navigation_representatives(
                candidates,
                focus=focus_query,
                title_targets=navigation_title_targets,
                topic_anchor=topic_anchor,
                topic_phrase=topic_phrase,
            )
        ranked_rows: list[tuple[sqlite3.Row, str, bool]] = []
        uncertain_rows: list[tuple[sqlite3.Row, str]] = []
        temporal_outside_count = 0
        seen: set[tuple[str, str | None]] = set()
        for row in candidates:
            dedupe_key = (
                (row["document_id"], None)
                if route == "navigation"
                else (row["document_id"], row["article_label"] or row["segment_id"])
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            temporal_classification = self._temporal_classification(
                row,
                request.as_of,
                temporal_intent=temporal_intent,
            )
            if temporal_classification == "outside_effective_interval":
                temporal_outside_count += 1
            elif temporal_classification == "unverified_metadata" or bool(
                row["extraction_review_required"]
            ):
                uncertain_rows.append((row, temporal_classification))
                ranked_rows.append((row, temporal_classification, True))
            else:
                ranked_rows.append((row, temporal_classification, False))

        result_limit = min(request.limit, 3) if route in {"navigation", "exact"} else request.limit
        candidate_cards: dict[str, tuple[EvidenceCard, bool]] = {}
        prepared_candidates: list[tuple[EvidenceCard, bool, float, int, bool]] = []
        for candidate_index, (row, temporal_classification, is_uncertain) in enumerate(
            ranked_rows[:256]
        ):
            budget = min(800 if route != "navigation" else 320, request.max_chars)
            card = self._card_from_row(
                row,
                request,
                route=route,
                max_excerpt_chars=budget,
                temporal_classification=cast(
                    Literal["not_evaluated", "verified_in_scope", "unverified_metadata"],
                    temporal_classification,
                ),
            )
            candidate_cards[card.segment_id] = (card, is_uncertain)
            focus_relevance = _focus_relevance(
                focus_query,
                f"{card.article_label or ''} {card.heading or ''} {card.excerpt}",
            )
            prepared_candidates.append(
                (
                    card,
                    is_uncertain,
                    focus_relevance,
                    candidate_index,
                    _card_matches_topic(
                        card,
                        anchor=topic_anchor,
                        topic_phrase=topic_phrase,
                    ),
                )
            )

        strict_topic_gate = topic_phrase is not None
        best_focus_relevance = max(
            (
                coverage
                for _, _, coverage, _, topic_match in prepared_candidates
                if not strict_topic_gate or topic_match
            ),
            default=0.0,
        )
        admission_floor = best_focus_relevance * 0.75
        focus_floor = best_focus_relevance * 0.95
        title_target_set = set(navigation_title_targets)
        has_focus_duty = True
        compiler_candidates: list[EvidenceCandidate] = []
        for (
            card,
            is_uncertain,
            focus_relevance,
            candidate_index,
            topic_match,
        ) in prepared_candidates:
            if not focus_query and route == "exact":
                admitted = card.retrieval_channel in {"article_exact", "title_exact"}
                witnesses_focus = admitted
            elif strict_topic_gate:
                admitted = topic_match
                witnesses_focus = topic_match
            else:
                admitted = (
                    best_focus_relevance > 0.0 and focus_relevance >= admission_floor
                ) or explicit_document_target
                if title_target_set:
                    witnesses_focus = card.document_id in title_target_set
                else:
                    witnesses_focus = best_focus_relevance > 0.0 and (
                        focus_relevance >= focus_floor
                    )
            duty_ids: tuple[str, ...] = ()
            if admitted:
                obligation_duty_ids = _card_duty_ids(compiled_plan, card) if witnesses_focus else ()
                if (
                    topic_anchor is not None
                    and ObligationId.PRIMARY_RULE.value in obligation_duty_ids
                    and not _anchor_primary_matches_card(topic_anchor, card)
                ):
                    obligation_duty_ids = tuple(
                        duty_id
                        for duty_id in obligation_duty_ids
                        if duty_id != ObligationId.PRIMARY_RULE.value
                    )
                duty_ids = tuple(
                    dict.fromkeys(
                        (
                            *(("query_focus",) if witnesses_focus else ()),
                            *obligation_duty_ids,
                            "discovery_lead",
                        )
                    )
                )
            channel_priority = {
                "article_exact": 3.0,
                "title_exact": 2.0,
                "chinese_fts": 1.0,
            }[card.retrieval_channel]
            discovery_score = (
                focus_relevance * 100.0
                + channel_priority * 10.0
                + max(0.0, (256 - candidate_index) / 256.0)
            )
            compiler_candidates.append(
                EvidenceCandidate(
                    candidate_id=card.segment_id,
                    document_id=card.document_id,
                    score=discovery_score,
                    chars=max(1, len(card.excerpt)),
                    is_uncertain=is_uncertain,
                    duty_ids=duty_ids,
                    authority_rank=card.authority_rank,
                )
            )

        compilation = compile_evidence(
            (
                (
                    EvidenceDuty(
                        duty_id="query_focus",
                        role="identity",
                        required=True,
                    ),
                )
                if has_focus_duty
                else ()
            )
            + tuple(
                EvidenceDuty(
                    duty_id=obligation.id.value,
                    role=obligation.role.value,
                    required=obligation.required,
                )
                for obligation in compiled_plan.obligations
            )
            + (
                EvidenceDuty(
                    duty_id="discovery_lead",
                    role="navigation",
                    required=False,
                ),
            ),
            tuple(compiler_candidates),
            max_items=result_limit,
            max_chars=request.max_chars,
        )
        evidence: list[EvidenceCard] = []
        uncertain_evidence: list[EvidenceCard] = []
        for candidate_id in compilation.selected_ids:
            card, is_uncertain = candidate_cards[candidate_id]
            (uncertain_evidence if is_uncertain else evidence).append(card)
        used_characters = compilation.total_chars

        graph_paths = self._graph_paths(
            tuple(evidence),
            as_of=request.as_of,
            temporal_intent=temporal_intent,
        )
        obligation_coverage = self._obligation_coverage(
            compiled_plan,
            evidence=tuple(evidence),
            uncertain_evidence=tuple(uncertain_evidence),
            graph_paths=graph_paths,
            as_of=request.as_of,
            topic_anchor=topic_anchor,
        )
        temporal_uncertain_count = sum(
            temporal_classification == "unverified_metadata"
            for _, temporal_classification in uncertain_rows
        )
        query_focus_witness = next(
            (witness for witness in compilation.duty_witnesses if witness.duty_id == "query_focus"),
            None,
        )
        gaps = self._search_gaps(
            route=route,
            exact_target_resolved=exact_target_resolved,
            query_focus_status=(
                query_focus_witness.status if query_focus_witness is not None else "uncovered"
            ),
            query_focus_candidate_count=sum(
                "query_focus" in candidate.duty_ids for candidate in compiler_candidates
            ),
            temporal_intent=temporal_intent,
            temporal_uncertain_count=temporal_uncertain_count,
            temporal_outside_count=temporal_outside_count,
            evidence_count=len(evidence),
            obligation_coverage=obligation_coverage,
        )

        notices: list[str] = [
            "检索结果是研究证据候选，不等同于本案法律适用结论。",
            "DeepLaw 2.0 未使用模型记忆、自动 Web 回退或向量 top-k 注入。",
        ]
        if self.collection_scope == "user_private":
            notices.insert(
                0,
                "当前结果来自用户私有资料库，未经 DeepLaw 官方团队审核，不得冒充官方法源。",
            )
        all_returned_evidence = (*evidence, *uncertain_evidence)
        if any(
            card.temporal_classification == "unverified_metadata" for card in uncertain_evidence
        ):
            notices.append(
                "时效检索中，效力起点缺失或状态未验证的候选已从主证据分离；正式引用前必须复核。"
            )
        if any(card.extraction_review_required for card in uncertain_evidence):
            notices.append(
                "存在未完成人工对照的抽取风险，相关候选已从主证据分离；引用前必须按页对照原件。"
            )
        if temporal_outside_count:
            notices.append(
                f"另有 {temporal_outside_count} 项候选按已知状态或效力区间不属于目标时点，"
                "未返回为证据。"
            )
        if temporal_intent and request.as_of is None and self.temporal_reviewed_on:
            notices.append(
                "未提供 as_of；“现行”仅按当前固定 release 的 reviewed_on="
                f"{self.temporal_reviewed_on} 状态解释。"
            )
        if any(card.temporal_review_required for card in all_returned_evidence):
            notices.append("至少一项法源缺少完整效力元数据，正式引用前必须复核时效。")
        if any(card.extraction_review_required for card in all_returned_evidence):
            notices.append("至少一项证据来自 OCR 或存在解析警告，引用前必须对照原件。")
        if not evidence:
            if uncertain_evidence:
                notices.append(
                    "当前 release 未形成已验证的主证据；不确定候选必须先解决时效或抽取风险。"
                )
            else:
                notices.append("当前 release 未找到足够证据；这不表示相关法律不存在。")

        next_questions: tuple[str, ...] = ()
        if route == "navigation":
            next_questions = (
                "请指定法条、文号或行为发生日期。",
                "可继续选择构成要件、立案追诉、程序证据或资金监管规则。",
            )

        channels = ["exact_metadata", "article_locator", "chinese_fts"]
        graph_used = bool(graph_paths)
        if graph_used:
            channels.append("legal_graph")
        query_plan = compiled_plan.to_dict()
        if request.as_of is not None:
            temporal_reference_date = request.as_of
            temporal_reference_source = "explicit_as_of"
        elif temporal_intent and self.temporal_reviewed_on is not None:
            temporal_reference_date = self.temporal_reviewed_on
            temporal_reference_source = "release_reviewed_on"
        elif temporal_intent:
            temporal_reference_date = None
            temporal_reference_source = "release_review_unavailable"
            notices.append(
                "当前 release 缺少 reviewed_on，无法把未指定 as_of 的问法解释为已复核的现行状态。"
            )
        else:
            temporal_reference_date = None
            temporal_reference_source = "not_evaluated"
        query_plan.update(
            {
                "channels": channels,
                "document_types": list(request.document_types),
                "max_evidence": result_limit,
                "max_chars": request.max_chars,
                "max_graph_paths": MAX_GRAPH_PATHS,
                "max_hops": MAX_GRAPH_HOPS,
                "graph_used": graph_used,
                "temporal_reference_date": temporal_reference_date,
                "temporal_reference_source": temporal_reference_source,
            }
        )

        return SearchResponse(
            schema_version=SEARCH_RESPONSE_SCHEMA,
            release_id=self.release_id,
            mode=route,
            query_plan=query_plan,
            evidence_compilation=compilation.to_dict(),
            evidence=tuple(evidence),
            uncertain_evidence=tuple(uncertain_evidence),
            graph_paths=graph_paths,
            obligation_coverage=obligation_coverage,
            gaps=gaps,
            notices=tuple(notices),
            next_questions=next_questions,
            total_excerpt_chars=used_characters,
        )

    def get(self, segment_id: str, *, max_chars: int = 6000) -> dict[str, Any]:
        max_chars = max(500, min(max_chars, 12000))
        row = self.connection.execute(
            """
            SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                   d.document_number, d.jurisdiction, d.promulgated_on,
                   d.official_source, d.source_sha256, d.effective_from, d.effective_to,
                   d.status, d.note, d.extraction_method, d.extraction_version,
                   d.extraction_configuration_json,
                   d.extraction_warnings_json AS document_extraction_warnings_json
            FROM segments s JOIN documents d USING(document_id)
            WHERE s.segment_id = ?
            """,
            (segment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown segment_id: {segment_id}")
        text = row["text"]
        truncated = len(text) > max_chars
        return {
            "schema_version": "deeplaw.segment/v2",
            "release_id": self.release_id,
            "receipt_id": self._receipt_id(row),
            "segment_id": row["segment_id"],
            "document_id": row["document_id"],
            "title": row["title"],
            "document_number": row["document_number"],
            "jurisdiction": row["jurisdiction"],
            "promulgated_on": row["promulgated_on"],
            "document_type": row["document_type"],
            "issuer": row["issuer"],
            "authority_rank": row["authority_rank"],
            "official_source": row["official_source"],
            "source_sha256": row["source_sha256"],
            "segment_sha256": row["text_sha256"],
            "ordinal": row["ordinal"],
            "kind": row["kind"],
            "heading": row["heading"],
            "article_label": row["article_label"],
            "part_index": row["part_index"],
            "page_start": row["page_start"],
            "page_end": row["page_end"],
            "paragraph_start": row["paragraph_start"],
            "paragraph_end": row["paragraph_end"],
            "text": text[:max_chars],
            "truncated": truncated,
            "effective_from": row["effective_from"],
            "effective_to": row["effective_to"],
            "status": row["status"],
            "temporal_review_required": self._temporal_review_required(row),
            "temporal_metadata_verified": not self._temporal_review_required(row),
            "extraction_method": row["extraction_method"],
            "extraction_version": row["extraction_version"],
            "extraction_configuration": json.loads(row["extraction_configuration_json"]),
            "extraction_review_required": bool(row["extraction_review_required"]),
            "extraction_warnings": json.loads(row["extraction_risk_flags_json"]),
        }

    def verify(self, segment_id: str, receipt_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT s.*, d.source_sha256, d.title, d.official_source
            FROM segments s JOIN documents d USING(document_id)
            WHERE s.segment_id = ?
            """,
            (segment_id,),
        ).fetchone()
        if row is None:
            return {"valid": False, "reason": "unknown_segment", "release_id": self.release_id}
        actual_text_hash = sha256_bytes(row["text"].encode("utf-8"))
        if actual_text_hash != row["text_sha256"]:
            return {
                "valid": False,
                "reason": "segment_hash_mismatch",
                "release_id": self.release_id,
            }
        expected = self._receipt_id(row)
        return {
            "valid": expected == receipt_id,
            "reason": "verified" if expected == receipt_id else "receipt_mismatch",
            "release_id": self.release_id,
            "segment_id": segment_id,
            "source_sha256": row["source_sha256"],
            "segment_sha256": row["text_sha256"],
        }

    def _route(self, request: SearchRequest) -> str:
        if request.purpose != "auto":
            if request.purpose == "broad_topic":
                return "navigation"
            if request.purpose in {"exact_citation", "citation_verify", "as_of_version"}:
                return "exact"
            return "research"
        if normalize_article_label(request.query):
            return "exact"
        if self._is_document_title_query(request.query):
            return "exact"
        compact = compact_text(request.query)
        if 1 < len(compact) <= 8 and not any(
            token in request.query
            for token in (
                "如何",
                "是否",
                "为什么",
                "构成",
                "要件",
                "数额",
                "立案",
                "追诉",
                "认定",
                "依据",
                "适用",
                "有效",
                "现行",
                "生效",
                "废止",
                "失效",
                "版本",
            )
        ):
            return "navigation"
        return "research"

    def _is_document_title_query(self, query: str) -> bool:
        """Return true only when the complete query is a known title or alias."""

        compact_query = compact_text(query)
        return any(
            identifier == compact_query and priority == 3
            for _, identifiers in self.document_identifiers
            for identifier, priority in identifiers
        )

    def _load_document_identifiers(
        self,
    ) -> tuple[tuple[str, tuple[tuple[str, int], ...]], ...]:
        values: list[tuple[str, tuple[tuple[str, int], ...]]] = []
        rows = self.connection.execute(
            "SELECT document_id, title, document_number, aliases_json FROM documents"
        ).fetchall()
        for row in rows:
            identifiers = dict(_document_query_keys(row["title"]))
            if row["document_number"]:
                identifiers[compact_text(row["document_number"])] = 3
            for alias in json.loads(row["aliases_json"]):
                if normalized := compact_text(alias):
                    identifiers[normalized] = 3
            values.append(
                (
                    row["document_id"],
                    tuple(
                        sorted(
                            (identifier, priority)
                            for identifier, priority in identifiers.items()
                            if len(identifier) >= 2
                        )
                    ),
                )
            )
        return tuple(values)

    def _target_document_ids(self, query: str) -> tuple[str, ...]:
        normalized_query = normalize_text(query)
        article_match = article_pattern().search(normalized_query)
        target_query = (
            normalized_query[: article_match.start()] if article_match else normalized_query
        )
        query_forms = set(_target_query_forms(target_query))
        matches: dict[str, int] = {}
        for document_id, identifiers in self.document_identifiers:
            priority = max(
                (
                    identifier_priority
                    for identifier, identifier_priority in identifiers
                    if identifier in query_forms
                ),
                default=0,
            )
            if priority:
                matches[document_id] = priority
        if not matches:
            return ()
        best_priority = max(matches.values())
        return tuple(
            sorted(
                document_id
                for document_id, priority in matches.items()
                if priority == best_priority
            )[:100]
        )

    def _mentioned_document_ids(self, query: str) -> tuple[str, ...]:
        """Resolve explicit long-form document names inside a research question."""

        compact_query = compact_text(query)
        query_phrases = _focus_phrases(query)
        matches: list[tuple[str, int, int]] = []
        for document_id, identifiers in self.document_identifiers:
            best = max(
                (
                    (priority, len(identifier))
                    for identifier, priority in identifiers
                    if priority >= 2
                    and len(identifier) >= 4
                    and (
                        identifier in compact_query
                        or (
                            len(query_phrases) >= 2
                            and all(phrase in identifier for phrase in query_phrases)
                        )
                    )
                ),
                default=None,
            )
            if best is not None:
                matches.append((document_id, *best))
        if not matches:
            return ()
        best_priority = max(priority for _, priority, _ in matches)
        best_length = max(length for _, priority, length in matches if priority == best_priority)
        return tuple(
            sorted(
                document_id
                for document_id, priority, length in matches
                if priority == best_priority and length == best_length
            )[:100]
        )

    def _matching_document_identifier(self, query: str) -> str | None:
        """Return the longest reviewed title/alias explicitly present in a query."""

        compact_query = compact_text(query)
        query_phrases = _focus_phrases(query)
        matches = {
            identifier
            for _, identifiers in self.document_identifiers
            for identifier, priority in identifiers
            if priority >= 2
            and len(identifier) >= 4
            and (
                identifier in compact_query
                or (
                    len(query_phrases) >= 2
                    and all(phrase in identifier for phrase in query_phrases)
                )
            )
        }
        return max(matches, key=lambda value: (len(value), value)) if matches else None

    def _query_focus(self, query: str) -> str:
        """Remove an explicit document identity so relevance is measured on its issue."""

        identifier = self._matching_document_identifier(query)
        if identifier is None:
            return normalize_text(query)
        compact_query = compact_text(query)
        if identifier in compact_query:
            return compact_query.replace(identifier, "", 1)
        return " ".join(phrase for phrase in _focus_phrases(query) if phrase not in identifier)

    def _navigation_title_document_ids(self, query: str) -> tuple[str, ...]:
        """Resolve a narrow topic that is visibly present in a title or reviewed alias."""

        compact_query = compact_text(query)
        if len(compact_query) < 2 or compact_query in _GENERIC_NAVIGATION_TERMS:
            return ()
        matches: list[tuple[str, int, int]] = []
        for document_id, identifiers in self.document_identifiers:
            best = max(
                (
                    (priority, len(identifier))
                    for identifier, priority in identifiers
                    if priority >= 2 and compact_query in identifier
                ),
                default=None,
            )
            if best is not None:
                matches.append((document_id, *best))
        # A very broad term matching many titles is not a deterministic anchor.
        if not matches or len(matches) > 3:
            return ()
        best_priority = max(priority for _, priority, _ in matches)
        return tuple(
            sorted(document_id for document_id, priority, _ in matches if priority == best_priority)
        )

    def _navigation_representatives(
        self,
        rows: list[sqlite3.Row],
        *,
        focus: str,
        title_targets: tuple[str, ...],
        topic_anchor: LegalTopicAnchor | None = None,
        topic_phrase: str | None = None,
    ) -> list[sqlite3.Row]:
        """Choose one substantive, query-focused representative for each document."""

        grouped: dict[str, list[tuple[int, sqlite3.Row]]] = {}
        for index, row in enumerate(rows):
            grouped.setdefault(str(row["document_id"]), []).append((index, row))

        title_target_set = set(title_targets)
        representatives: list[tuple[tuple[float, ...], sqlite3.Row]] = []
        for document_id, document_rows in grouped.items():
            first_index = document_rows[0][0]

            def row_quality(item: tuple[int, sqlite3.Row]) -> tuple[float, ...]:
                index, row = item
                body = f"{row['article_label'] or ''} {row['heading'] or ''} {row['text']}"
                return (
                    float(
                        _row_matches_topic(
                            row,
                            anchor=topic_anchor,
                            topic_phrase=topic_phrase,
                        )
                    ),
                    float(_contains_primary_rule(str(row["text"]))),
                    float(bool(row["article_label"])),
                    _focus_relevance(focus, body),
                    float(-index),
                )

            _, representative = max(document_rows, key=row_quality)
            representative_body = (
                f"{representative['article_label'] or ''} "
                f"{representative['heading'] or ''} {representative['text']}"
            )
            ordering = (
                float(
                    _row_matches_topic(
                        representative,
                        anchor=topic_anchor,
                        topic_phrase=topic_phrase,
                    )
                ),
                float(document_id in title_target_set),
                _focus_relevance(focus, representative_body),
                float(-first_index),
            )
            representatives.append((ordering, representative))
        return [
            row
            for _, row in sorted(
                representatives,
                key=lambda item: item[0],
                reverse=True,
            )
        ]

    def _temporal_review_required(self, row: sqlite3.Row) -> bool:
        status = str(row["status"])
        effective_from = row["effective_from"]
        effective_to = row["effective_to"]
        return (
            not self.temporal_metadata_verified
            or not self.temporal_reviewed_on
            or status in {"unknown", "unverified_current"}
            or not effective_from
            or (status in {"verified_historical", "repealed", "superseded"} and not effective_to)
            or (
                status == "verified_current"
                and (
                    not self.temporal_reviewed_on
                    or effective_from > self.temporal_reviewed_on
                    or (effective_to is not None and effective_to <= self.temporal_reviewed_on)
                )
            )
        )

    def _temporal_classification(
        self,
        row: sqlite3.Row,
        as_of: str | None,
        *,
        temporal_intent: bool = False,
    ) -> str:
        return self._temporal_values_classification(
            status=str(row["status"]),
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
            as_of=as_of,
            temporal_intent=temporal_intent,
        )

    def _temporal_values_classification(
        self,
        *,
        status: str,
        effective_from: str | None,
        effective_to: str | None,
        as_of: str | None,
        temporal_intent: bool = False,
    ) -> str:
        if as_of is None:
            if not temporal_intent:
                return "not_evaluated"
            if status in _NON_CURRENT_STATUSES:
                return "outside_effective_interval"
            if (
                self.temporal_reviewed_on
                and effective_to
                and effective_to <= self.temporal_reviewed_on
            ):
                return "outside_effective_interval"
            if (
                not self.temporal_metadata_verified
                or status != "verified_current"
                or not effective_from
                or not self.temporal_reviewed_on
                or effective_from > self.temporal_reviewed_on
            ):
                return "unverified_metadata"
            return "verified_in_scope"
        if effective_from and effective_from > as_of:
            return "outside_effective_interval"
        if effective_to and effective_to <= as_of:
            return "outside_effective_interval"
        if self.temporal_reviewed_on and as_of > self.temporal_reviewed_on:
            return "unverified_metadata"
        if (
            not self.temporal_metadata_verified
            or not self.temporal_reviewed_on
            or status in {"unknown", "unverified_current"}
            or not effective_from
            or (status in {"verified_historical", "repealed", "superseded"} and not effective_to)
        ):
            return "unverified_metadata"
        return "verified_in_scope"

    def _topic_anchor_rows(
        self,
        anchor: LegalTopicAnchor,
        *,
        document_types: tuple[str, ...] = (),
    ) -> list[sqlite3.Row]:
        """Load bounded, source-hash-bound locators outside the FTS top-N."""

        rows: list[sqlite3.Row] = []
        seen: set[str] = set()
        allowed_document_types = set(document_types)
        for locator in anchor.locators:
            located = self.connection.execute(
                """
                SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                       d.document_number, d.jurisdiction, d.promulgated_on,
                       d.official_source, d.source_sha256, d.effective_from,
                       d.effective_to, d.status, d.note,
                       d.extraction_method, d.extraction_version,
                       d.extraction_configuration_json,
                       d.extraction_warnings_json AS document_extraction_warnings_json,
                       -1000.0 AS fts_rank, 'article_exact' AS channel
                FROM segments s JOIN documents d USING(document_id)
                WHERE d.title = ?
                  AND d.source_sha256 = ?
                  AND REPLACE(s.article_label, ' ', '') = REPLACE(?, ' ', '')
                ORDER BY d.authority_rank DESC, s.ordinal ASC
                LIMIT 16
                """,
                (locator.document_title, locator.source_sha256, locator.article_label),
            ).fetchall()
            for row in located:
                if (
                    allowed_document_types
                    and str(row["document_type"]) not in allowed_document_types
                ):
                    continue
                segment_id = str(row["segment_id"])
                if segment_id not in seen:
                    seen.add(segment_id)
                    rows.append(row)
        return rows[:16]

    def _candidate_rows(self, request: SearchRequest, route: str) -> list[sqlite3.Row]:
        terms = search_terms(request.query, limit=36)
        query = fts_query(terms)
        filters: list[str] = []
        parameters: list[Any] = []
        resolved_targets = self._target_document_ids(request.query) if route == "exact" else ()
        mentioned_targets = (
            self._mentioned_document_ids(request.query) if route == "research" else ()
        )
        candidate_targets = resolved_targets or mentioned_targets
        if route == "exact" and not resolved_targets:
            return []
        if candidate_targets:
            placeholders = ",".join("?" for _ in candidate_targets)
            filters.append(f"d.document_id IN ({placeholders})")
            parameters.extend(candidate_targets)
        if request.document_types:
            placeholders = ",".join("?" for _ in request.document_types)
            filters.append(f"d.document_type IN ({placeholders})")
            parameters.extend(request.document_types)
        where_suffix = "" if not filters else " AND " + " AND ".join(filters)

        rows: list[sqlite3.Row] = []
        if query:
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                           d.document_number, d.jurisdiction, d.promulgated_on,
                           d.official_source, d.source_sha256, d.effective_from,
                           d.effective_to, d.status, d.note,
                           d.extraction_method, d.extraction_version,
                           d.extraction_configuration_json,
                           d.extraction_warnings_json AS document_extraction_warnings_json,
                           bm25(segment_search, 0.0, 8.0, 2.0, 5.0) AS fts_rank,
                           'chinese_fts' AS channel
                    FROM segment_search
                    JOIN segments s ON s.segment_id = segment_search.segment_id
                    JOIN documents d USING(document_id)
                    WHERE segment_search MATCH ? {where_suffix}
                    ORDER BY fts_rank ASC, d.authority_rank DESC, s.ordinal ASC
                    LIMIT 100
                    """,
                    (query, *parameters),
                ).fetchall()
            )

        article = normalize_article_label(request.query)
        exact_rows: list[sqlite3.Row] = []
        if article:
            article_filters = list(filters)
            article_filters.append("REPLACE(s.article_label, ' ', '') = REPLACE(?, ' ', '')")
            article_params = [*parameters, article]
            exact_suffix = " AND ".join(article_filters)
            exact_rows = self.connection.execute(
                f"""
                SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                       d.document_number, d.jurisdiction, d.promulgated_on,
                       d.official_source, d.source_sha256, d.effective_from,
                       d.effective_to, d.status, d.note,
                       d.extraction_method, d.extraction_version,
                       d.extraction_configuration_json,
                       d.extraction_warnings_json AS document_extraction_warnings_json,
                       -1000.0 AS fts_rank, 'article_exact' AS channel
                FROM segments s JOIN documents d USING(document_id)
                WHERE {exact_suffix}
                ORDER BY d.authority_rank DESC, s.ordinal ASC
                LIMIT 50
                """,
                tuple(article_params),
            ).fetchall()

        title_compact = compact_text(request.query)
        title_rows: list[sqlite3.Row] = []
        if len(title_compact) >= (2 if route == "navigation" else 4):
            title_where = "(d.normalized_title LIKE ? OR d.normalized_names LIKE ?)"
            title_parameters: list[Any] = [f"%{title_compact}%", f"%{title_compact}%"]
            document_filter = "" if not filters else " AND " + " AND ".join(filters)
            title_rows = self.connection.execute(
                f"""
                SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                       d.document_number, d.jurisdiction, d.promulgated_on,
                       d.official_source, d.source_sha256, d.effective_from,
                       d.effective_to, d.status, d.note,
                       d.extraction_method, d.extraction_version,
                       d.extraction_configuration_json,
                       d.extraction_warnings_json AS document_extraction_warnings_json,
                       -500.0 AS fts_rank, 'title_exact' AS channel
                FROM segments s
                JOIN documents d USING(document_id)
                WHERE {title_where} {document_filter}
                ORDER BY d.authority_rank DESC, s.ordinal ASC
                LIMIT 50
                """,
                (*title_parameters, *parameters),
            ).fetchall()

        merged: dict[str, sqlite3.Row] = {}
        for row in [*exact_rows, *title_rows, *rows]:
            merged.setdefault(row["segment_id"], row)
        query_term_set = set(terms)
        compact_query = compact_text(request.query)

        def score(row: sqlite3.Row) -> tuple[float, int, int]:
            text_terms = set(
                search_terms(f"{row['title']} {row['article_label'] or ''} {row['text']}")
            )
            title_terms = set(search_terms(row["title"]))
            coverage = len(query_term_set & text_terms) / max(1, len(query_term_set))
            title_coverage = len(query_term_set & title_terms) / max(1, len(query_term_set))
            explicit_title_match = any(
                key in compact_query for key, _ in _document_query_keys(row["title"])
            )
            document_type_match = (
                4.0
                if row["document_type"] == "judicial_interpretation" and "解释" in request.query
                else 0.0
            )
            channel_boost = {
                "article_exact": 5.0,
                "title_exact": 3.0,
                "chinese_fts": 0.0,
            }.get(row["channel"], 0.0)
            authority = row["authority_rank"] / 100.0
            raw_rank = float(row["fts_rank"])
            fts_component = min(1.5, max(0.0, -raw_rank) * 100_000)
            total = (
                channel_boost
                + coverage * 4.0
                + title_coverage * 2.0
                + (4.0 if explicit_title_match else 0.0)
                + document_type_match
                + authority * 0.4
                + fts_component
            )
            return (total, row["authority_rank"], -row["ordinal"])

        ranked = sorted(merged.values(), key=score, reverse=True)
        document_matches: dict[str, int] = {}
        for row in ranked:
            matched_length = max(
                (len(key) for key, _ in _document_query_keys(row["title"]) if key in compact_query),
                default=0,
            )
            if matched_length:
                document_matches[row["document_id"]] = max(
                    document_matches.get(row["document_id"], 0), matched_length
                )
        target_documents: set[str] = set(candidate_targets)
        if not target_documents and route == "exact" and title_rows:
            target_documents = {row["document_id"] for row in title_rows}
        elif not target_documents and document_matches:
            best_length = max(document_matches.values())
            target_documents = {
                document_id
                for document_id, matched_length in document_matches.items()
                if matched_length == best_length
            }
        if route == "exact" and target_documents:
            ranked = [row for row in ranked if row["document_id"] in target_documents]
        if route == "exact" and article:
            ranked = [
                row
                for row in ranked
                if compact_text(row["article_label"] or "") == compact_text(article)
            ]
        return ranked[:100]

    def _card_from_row(
        self,
        row: sqlite3.Row,
        request: SearchRequest,
        *,
        route: str,
        max_excerpt_chars: int,
        temporal_classification: Literal[
            "not_evaluated", "verified_in_scope", "unverified_metadata"
        ],
    ) -> EvidenceCard:
        hit_reason = {
            "article_exact": "精确命中条款编号",
            "title_exact": "精确命中文件题名",
            "chinese_fts": "中文词元召回后经权威等级与覆盖率重排",
        }.get(row["channel"], "结构化检索命中")
        if route == "navigation":
            locator = row["article_label"] or row["heading"] or "文档导航"
            prefix = f"{locator}："
            if len(prefix) >= max_excerpt_chars:
                text_excerpt = prefix[:max_excerpt_chars]
            else:
                text_excerpt = prefix + excerpt(
                    row["text"],
                    request.query,
                    max_chars=max_excerpt_chars - len(prefix),
                )
        else:
            text_excerpt = excerpt(row["text"], request.query, max_chars=max_excerpt_chars)
        rank = float(row["fts_rank"])
        channel_score = {"article_exact": 1.0, "title_exact": 0.9}.get(
            row["channel"], min(0.8, max(0.0, -rank) * 100_000)
        )
        score = round(channel_score + row["authority_rank"] / 1000.0, 6)
        retrieval_channel = cast(
            Literal["article_exact", "title_exact", "chinese_fts"],
            str(row["channel"]),
        )
        return EvidenceCard(
            schema_version=EVIDENCE_CARD_SCHEMA,
            release_id=self.release_id,
            receipt_id=self._receipt_id(row),
            segment_id=row["segment_id"],
            document_id=row["document_id"],
            title=row["title"],
            document_number=row["document_number"],
            jurisdiction=row["jurisdiction"],
            promulgated_on=row["promulgated_on"],
            document_type=row["document_type"],
            issuer=row["issuer"],
            authority_rank=row["authority_rank"],
            official_source=row["official_source"],
            source_sha256=row["source_sha256"],
            segment_sha256=row["text_sha256"],
            score=score,
            hit_reason=hit_reason,
            retrieval_channel=retrieval_channel,
            temporal_classification=temporal_classification,
            excerpt=text_excerpt,
            article_label=row["article_label"],
            heading=row["heading"],
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
            status=row["status"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            paragraph_start=row["paragraph_start"],
            paragraph_end=row["paragraph_end"],
            temporal_review_required=self._temporal_review_required(row),
            extraction_method=row["extraction_method"],
            extraction_configuration=tuple(json.loads(row["extraction_configuration_json"])),
            extraction_review_required=bool(row["extraction_review_required"]),
            extraction_warnings=tuple(json.loads(row["extraction_risk_flags_json"])),
        )

    def _graph_paths(
        self,
        evidence: tuple[EvidenceCard, ...],
        *,
        as_of: str | None,
        temporal_intent: bool,
    ) -> tuple[GraphPath, ...]:
        seed_titles: dict[str, str] = {}
        for card in evidence:
            seed_titles.setdefault(card.document_id, card.title)
        if not seed_titles:
            return ()

        seed_ids = tuple(seed_titles)
        placeholders = ",".join("?" for _ in seed_ids)
        rows = self.connection.execute(
            f"""
            SELECT e.*,
                   sd.title AS subject_title,
                   sd.document_type AS subject_document_type,
                   sd.effective_from AS subject_effective_from,
                   sd.effective_to AS subject_effective_to,
                   sd.status AS subject_status,
                   od.title AS object_title,
                   od.document_type AS object_document_type,
                   od.effective_from AS object_effective_from,
                   od.effective_to AS object_effective_to,
                   od.status AS object_status,
                   ps.document_id AS provenance_document_id,
                   ps.text AS provenance_text,
                   ps.text_sha256 AS provenance_segment_sha256,
                   ps.extraction_review_required AS provenance_extraction_review_required,
                   pd.source_sha256 AS provenance_source_sha256
            FROM legal_edges e
            JOIN documents sd ON sd.document_id = e.subject_document_id
            JOIN documents od ON od.document_id = e.object_document_id
            JOIN segments ps ON ps.segment_id = e.provenance_segment_id
            JOIN documents pd ON pd.document_id = ps.document_id
            WHERE (
                e.subject_document_id IN ({placeholders})
                OR e.object_document_id IN ({placeholders})
            )
              AND e.review_status = 'deterministic_exact'
              AND ps.document_id = e.subject_document_id
            ORDER BY e.relation_id ASC
            LIMIT 100
            """,
            (*seed_ids, *seed_ids),
        ).fetchall()

        paths: list[GraphPath] = []
        seen: set[str] = set()
        for seed_document_id, seed_title in seed_titles.items():
            for row in rows:
                if row["subject_document_id"] == seed_document_id:
                    direction = "outbound"
                    target_prefix = "object"
                    target_document_id = row["object_document_id"]
                elif row["object_document_id"] == seed_document_id:
                    direction = "inbound"
                    target_prefix = "subject"
                    target_document_id = row["subject_document_id"]
                else:
                    continue
                relation_id = str(row["relation_id"])
                if relation_id in seen:
                    continue

                provenance_segment_sha256 = str(row["provenance_segment_sha256"])
                actual_provenance_sha256 = sha256_bytes(str(row["provenance_text"]).encode("utf-8"))
                if (
                    row["evidence_sha256"] != provenance_segment_sha256
                    or actual_provenance_sha256 != provenance_segment_sha256
                    or bool(row["provenance_extraction_review_required"])
                ):
                    continue

                if as_of is not None:
                    if not row["valid_from"]:
                        continue
                    if row["valid_from"] > as_of:
                        continue
                    if row["valid_to"] and row["valid_to"] <= as_of:
                        continue
                    target_temporal = self._temporal_values_classification(
                        status=str(row[f"{target_prefix}_status"]),
                        effective_from=row[f"{target_prefix}_effective_from"],
                        effective_to=row[f"{target_prefix}_effective_to"],
                        as_of=as_of,
                    )
                    if target_temporal != "verified_in_scope":
                        continue
                elif temporal_intent:
                    target_temporal = self._temporal_values_classification(
                        status=str(row[f"{target_prefix}_status"]),
                        effective_from=row[f"{target_prefix}_effective_from"],
                        effective_to=row[f"{target_prefix}_effective_to"],
                        as_of=None,
                        temporal_intent=True,
                    )
                    if target_temporal != "verified_in_scope":
                        continue

                predicate = str(row["predicate"])
                if predicate not in {
                    "cites",
                    "amends",
                    "repeals",
                    "replaces",
                    "implements",
                    "exception_to",
                }:
                    continue
                review_status = str(row["review_status"])
                if review_status != "deterministic_exact":
                    continue
                seen.add(relation_id)
                paths.append(
                    GraphPath(
                        path_id=stable_id(
                            "lawpath",
                            self.release_id,
                            seed_document_id,
                            row["relation_id"],
                            target_document_id,
                        ),
                        seed_document_id=seed_document_id,
                        seed_title=seed_title,
                        target_document_id=target_document_id,
                        target_title=row[f"{target_prefix}_title"],
                        target_document_type=row[f"{target_prefix}_document_type"],
                        relation_id=row["relation_id"],
                        predicate=cast(
                            Literal[
                                "cites",
                                "amends",
                                "repeals",
                                "replaces",
                                "implements",
                                "exception_to",
                            ],
                            predicate,
                        ),
                        direction=cast(Literal["outbound", "inbound"], direction),
                        provenance_segment_id=row["provenance_segment_id"],
                        provenance_receipt_id=self._receipt_from_parts(
                            document_id=row["provenance_document_id"],
                            segment_id=row["provenance_segment_id"],
                            source_sha256=row["provenance_source_sha256"],
                            segment_sha256=provenance_segment_sha256,
                        ),
                        review_status=cast(Literal["deterministic_exact"], review_status),
                        derivation=row["derivation"],
                        authority="derived_navigation",
                        hops=1,
                    )
                )
                if len(paths) >= MAX_GRAPH_PATHS:
                    return tuple(paths)
        return tuple(paths)

    def _obligation_coverage(
        self,
        plan: QueryPlan,
        *,
        evidence: tuple[EvidenceCard, ...],
        uncertain_evidence: tuple[EvidenceCard, ...],
        graph_paths: tuple[GraphPath, ...],
        as_of: str | None,
        topic_anchor: LegalTopicAnchor | None,
    ) -> tuple[ObligationCoverage, ...]:
        coverage: list[ObligationCoverage] = []
        for obligation in plan.obligations:
            substantive_evidence = [
                card for card in evidence if not _defers_or_disclaims_content(card.excerpt)
            ]
            substantive_uncertain_evidence = [
                card
                for card in uncertain_evidence
                if not _defers_or_disclaims_content(card.excerpt)
            ]
            primary_cards = list(substantive_evidence)
            uncertain_cards = list(substantive_uncertain_evidence)
            matching_paths: list[GraphPath] = []

            if obligation.id is ObligationId.PRIMARY_RULE:
                primary_cards = [
                    card
                    for card in substantive_evidence
                    if card.document_type != "case_reference"
                    and _contains_primary_rule(card.excerpt)
                    and (
                        topic_anchor is None
                        or _anchor_primary_matches_card(topic_anchor, card)
                    )
                ]
                uncertain_cards = [
                    card
                    for card in substantive_uncertain_evidence
                    if card.document_type != "case_reference"
                    and _contains_primary_rule(card.excerpt)
                    and (
                        topic_anchor is None
                        or _anchor_primary_matches_card(topic_anchor, card)
                    )
                ]
            elif obligation.id is ObligationId.EXACT_CITATION:
                primary_cards = [
                    card
                    for card in evidence
                    if card.retrieval_channel in {"article_exact", "title_exact"}
                ]
                uncertain_cards = [
                    card
                    for card in uncertain_evidence
                    if card.retrieval_channel in {"article_exact", "title_exact"}
                ]
            elif obligation.id is ObligationId.TEMPORAL_STATUS_VERSION:
                if as_of is None:
                    primary_cards = [
                        card
                        for card in evidence
                        if card.temporal_classification == "verified_in_scope"
                    ]
                    uncertain_cards = [
                        card
                        for card in uncertain_evidence
                        if card.temporal_classification == "unverified_metadata"
                    ]
                else:
                    primary_cards = [
                        card
                        for card in evidence
                        if card.temporal_classification == "verified_in_scope"
                    ]
                    uncertain_cards = [
                        card
                        for card in uncertain_evidence
                        if card.temporal_classification == "unverified_metadata"
                    ]
            elif obligation.id is ObligationId.ELEMENTS_DEFINITIONS:
                primary_cards = [
                    card
                    for card in substantive_evidence
                    if any(marker in card.excerpt for marker in _ELEMENTS_EVIDENCE_MARKERS)
                ]
                uncertain_cards = [
                    card
                    for card in substantive_uncertain_evidence
                    if any(marker in card.excerpt for marker in _ELEMENTS_EVIDENCE_MARKERS)
                ]
            elif obligation.id is ObligationId.INTERPRETATION:
                primary_cards = [
                    card
                    for card in substantive_evidence
                    if card.document_type == "judicial_interpretation"
                    or "解释" in card.title
                    or any(marker in card.excerpt for marker in _INTERPRETATION_EVIDENCE_MARKERS)
                ]
                uncertain_cards = [
                    card
                    for card in substantive_uncertain_evidence
                    if card.document_type == "judicial_interpretation"
                    or "解释" in card.title
                    or any(marker in card.excerpt for marker in _INTERPRETATION_EVIDENCE_MARKERS)
                ]
                matching_paths = [
                    path
                    for path in graph_paths
                    if path.target_document_type == "judicial_interpretation"
                    or "解释" in path.target_title
                ]
            elif obligation.id is ObligationId.PROCEDURE:
                primary_cards = [
                    card
                    for card in substantive_evidence
                    if any(marker in card.excerpt for marker in _PROCEDURE_EVIDENCE_MARKERS)
                ]
                uncertain_cards = [
                    card
                    for card in substantive_uncertain_evidence
                    if any(marker in card.excerpt for marker in _PROCEDURE_EVIDENCE_MARKERS)
                ]
            elif obligation.id is ObligationId.THRESHOLD_STANDARD:
                primary_cards = [
                    card
                    for card in substantive_evidence
                    if _contains_threshold_standard(obligation, card.excerpt)
                ]
                uncertain_cards = [
                    card
                    for card in substantive_uncertain_evidence
                    if _contains_threshold_standard(obligation, card.excerpt)
                ]
            elif obligation.id is ObligationId.EXCEPTIONS_COUNTEREVIDENCE:
                primary_cards = [
                    card
                    for card in substantive_evidence
                    if any(marker in card.excerpt for marker in _COUNTEREVIDENCE_MARKERS)
                ]
                uncertain_cards = [
                    card
                    for card in substantive_uncertain_evidence
                    if any(marker in card.excerpt for marker in _COUNTEREVIDENCE_MARKERS)
                ]
                matching_paths = [
                    path for path in graph_paths if path.predicate in _COUNTEREVIDENCE_PREDICATES
                ]
            elif obligation.id is ObligationId.CASE_REFERENCE:
                primary_cards = [
                    card for card in substantive_evidence if card.document_type == "case_reference"
                ]
                uncertain_cards = [
                    card
                    for card in substantive_uncertain_evidence
                    if card.document_type == "case_reference"
                ]
                matching_paths = [
                    path for path in graph_paths if path.target_document_type == "case_reference"
                ]

            if primary_cards:
                status = "covered"
                selected_cards = primary_cards
                selected_paths: list[GraphPath] = []
            elif uncertain_cards or matching_paths:
                status = "uncertain"
                selected_cards = uncertain_cards
                selected_paths = matching_paths
            else:
                status = "gap"
                selected_cards = []
                selected_paths = []
            coverage.append(
                ObligationCoverage(
                    obligation_id=obligation.id.value,
                    role=obligation.role.value,
                    required=obligation.required,
                    status=cast(Literal["covered", "uncertain", "gap"], status),
                    evidence_segment_ids=tuple(
                        dict.fromkeys(card.segment_id for card in selected_cards)
                    ),
                    graph_path_ids=tuple(dict.fromkeys(path.path_id for path in selected_paths)),
                )
            )
        return tuple(coverage)

    def _search_gaps(
        self,
        *,
        route: str,
        exact_target_resolved: bool,
        query_focus_status: str,
        query_focus_candidate_count: int,
        temporal_intent: bool,
        temporal_uncertain_count: int,
        temporal_outside_count: int,
        evidence_count: int,
        obligation_coverage: tuple[ObligationCoverage, ...],
    ) -> tuple[SearchGap, ...]:
        gaps: list[SearchGap] = []
        seen: set[tuple[str, str | None]] = set()

        def add_gap(
            code: _GapCode,
            message: str,
            *,
            obligation_id: str | None = None,
            candidate_count: int = 0,
            blocking: bool = True,
        ) -> None:
            key = (code, obligation_id)
            if key in seen or len(gaps) >= 16:
                return
            seen.add(key)
            gaps.append(
                SearchGap(
                    code=code,
                    obligation_id=obligation_id,
                    message=message,
                    blocking=blocking,
                    candidate_count=max(0, min(candidate_count, 100)),
                )
            )

        if route == "exact" and not exact_target_resolved:
            add_gap(
                "exact_target_unresolved",
                "未在当前 release 中解析出精确文件题名、别名或文号，未扩大到相似文件。",
            )
        if query_focus_status != "covered":
            add_gap(
                "query_focus_unresolved",
                "未找到能够在同一证据卡中可靠匹配查询主题的证据，已停止用相邻主题补位。",
                obligation_id="query_focus",
                candidate_count=query_focus_candidate_count,
            )
        if temporal_intent and temporal_uncertain_count:
            add_gap(
                "temporal_metadata_unverified",
                "存在相关候选，但其效力起点缺失、文件状态未验证或 release 时效元数据未验证。",
                obligation_id=ObligationId.TEMPORAL_STATUS_VERSION.value,
                candidate_count=temporal_uncertain_count,
            )
        if temporal_intent and temporal_outside_count:
            add_gap(
                "temporal_out_of_scope",
                "候选按已知状态或 effective_from/effective_to 不属于目标时点，已排除。",
                obligation_id=ObligationId.TEMPORAL_STATUS_VERSION.value,
                candidate_count=temporal_outside_count,
                blocking=False,
            )
        for item in obligation_coverage:
            if not item.required or item.status == "covered":
                continue
            candidate_count = len(item.evidence_segment_ids) + len(item.graph_path_ids)
            if item.status == "uncertain":
                add_gap(
                    "required_obligation_uncertain",
                    "该必需检索义务只有不确定候选，不能计为已覆盖。",
                    obligation_id=item.obligation_id,
                    candidate_count=candidate_count,
                )
            else:
                add_gap(
                    "required_obligation_uncovered",
                    "当前有界检索未覆盖该必需检索义务。",
                    obligation_id=item.obligation_id,
                )
        if not evidence_count:
            add_gap(
                "no_primary_evidence",
                "当前有界检索未形成可进入主证据桶的候选。",
                candidate_count=temporal_uncertain_count,
            )
        return tuple(gaps)

    def _receipt_from_parts(
        self,
        *,
        document_id: str,
        segment_id: str,
        source_sha256: str,
        segment_sha256: str,
    ) -> str:
        payload = {
            "release_id": self.release_id,
            "document_id": document_id,
            "segment_id": segment_id,
            "source_sha256": source_sha256,
            "segment_sha256": segment_sha256,
        }
        digest = sha256_bytes(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
        return f"lawrcpt_{digest[:32]}"

    def _receipt_id(self, row: sqlite3.Row) -> str:
        return self._receipt_from_parts(
            document_id=row["document_id"],
            segment_id=row["segment_id"],
            source_sha256=row["source_sha256"],
            segment_sha256=row["text_sha256"],
        )


def response_json(value: Any) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
