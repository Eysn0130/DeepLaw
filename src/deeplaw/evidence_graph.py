from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from .models import Segment, SourceDocument
from .util import compact_text, sha256_bytes, stable_id

RelationType = Literal[
    "cites",
    "amends",
    "repeals",
    "replaces",
    "implements",
    "exception_to",
]

RELATION_TYPES: frozenset[str] = frozenset(
    {"cites", "amends", "repeals", "replaces", "implements", "exception_to"}
)
REVIEW_STATUSES: frozenset[str] = frozenset({"deterministic_exact"})
_NON_ASSERTIVE_PREFIXES = (
    "不",
    "未",
    "尚未",
    "并未",
    "不得",
    "不予",
    "不应",
    "不能",
    "无需",
    "无须",
    "禁止",
    "是否",
    "有待",
    "尚待",
    "拟",
    "计划",
    "可能",
    "建议",
)


@dataclass(frozen=True)
class EvidenceRelation:
    relation_id: str
    subject_document_id: str
    predicate: RelationType
    object_document_id: str
    provenance_segment_id: str
    evidence_sha256: str
    derivation: str
    review_status: str
    valid_from: str | None = None
    valid_to: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _document_names(document: SourceDocument) -> tuple[str, ...]:
    names: dict[str, None] = {}
    for value in (document.title, document.document_number or "", *document.aliases):
        normalized = compact_text(value)
        if len(normalized) >= 4:
            names[normalized] = None
        if "（" in normalized:
            without_version = normalized.split("（", 1)[0]
            if len(without_version) >= 4:
                names[without_version] = None
    return tuple(sorted(names, key=len, reverse=True))


def _nearest_relation_verb(text: str, target_start: int, target_end: int) -> str | None:
    window_start = max(0, target_start - 80)
    window_end = min(len(text), target_end + 80)
    window = text[window_start:window_end]
    target_middle = target_start - window_start + (target_end - target_start) // 2
    candidates: list[tuple[int, str]] = []
    for predicate, verbs in (
        ("repeals", ("废止", "停止执行")),
        ("replaces", ("代替", "替代")),
        ("amends", ("修改", "修正")),
        ("exception_to", ("除外", "不适用")),
    ):
        for verb in verbs:
            start = 0
            while (index := window.find(verb, start)) >= 0:
                prefix = window[max(0, index - 8) : index]
                if not any(prefix.endswith(marker) for marker in _NON_ASSERTIVE_PREFIXES):
                    candidates.append(
                        (abs(index + len(verb) // 2 - target_middle), predicate)
                    )
                start = index + len(verb)
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[1]


def _infer_predicate(
    source: SourceDocument,
    target: SourceDocument,
    segment_text: str,
    *,
    target_start: int,
    target_end: int,
) -> RelationType:
    source_title = compact_text(source.title)
    target_title = compact_text(target.title)
    if "实施细则" in source_title and target_title.replace("实施细则", "") in source_title:
        return "implements"
    if "修正案" in source_title and "刑法" in target_title and "修正案" not in target_title:
        return "amends"
    if nearest := _nearest_relation_verb(segment_text, target_start, target_end):
        return nearest  # type: ignore[return-value]
    return "cites"


def derive_relations(
    documents: list[SourceDocument],
    segments: list[Segment],
) -> tuple[EvidenceRelation, ...]:
    """Build a bounded, provenance-carrying graph from exact document references.

    This function never uses an LLM or fuzzy entity matching. Derived edges are
    navigation aids and retain the exact source segment that caused the edge.
    """

    by_document = {document.document_id: document for document in documents}
    names = {
        document.document_id: _document_names(document)
        for document in documents
    }
    relations: dict[tuple[str, str, str, str], EvidenceRelation] = {}
    for segment in segments:
        source = by_document[segment.document_id]
        text = compact_text(segment.text)
        if not text:
            continue
        for target in documents:
            if source.document_id == target.document_id:
                continue
            matched_name = next((name for name in names[target.document_id] if name in text), None)
            if not matched_name:
                continue
            target_start = text.index(matched_name)
            predicate = _infer_predicate(
                source,
                target,
                text,
                target_start=target_start,
                target_end=target_start + len(matched_name),
            )
            evidence_sha256 = sha256_bytes(segment.text.encode("utf-8"))
            relation_id = stable_id(
                "lawedge",
                source.document_id,
                predicate,
                target.document_id,
                segment.segment_id,
                evidence_sha256,
            )
            relation = EvidenceRelation(
                relation_id=relation_id,
                subject_document_id=source.document_id,
                predicate=predicate,
                object_document_id=target.document_id,
                provenance_segment_id=segment.segment_id,
                evidence_sha256=evidence_sha256,
                derivation="exact_document_reference/v1",
                review_status="deterministic_exact",
                valid_from=source.effective_from,
                valid_to=source.effective_to,
            )
            key = (
                relation.subject_document_id,
                relation.predicate,
                relation.object_document_id,
                relation.provenance_segment_id,
            )
            relations[key] = relation
    return tuple(sorted(relations.values(), key=lambda value: value.relation_id))
