from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Purpose = Literal[
    "auto",
    "exact_citation",
    "as_of_version",
    "elements",
    "legal_issue_screen",
    "citation_verify",
    "broad_topic",
]

PURPOSES: frozenset[str] = frozenset(
    {
        "auto",
        "exact_citation",
        "as_of_version",
        "elements",
        "legal_issue_screen",
        "citation_verify",
        "broad_topic",
    }
)


@dataclass(frozen=True)
class TextBlock:
    text: str
    page: int | None = None
    paragraph: int | None = None
    style: str | None = None
    kind: str = "paragraph"
    bbox: tuple[float, float, float, float] | None = None
    source: str = "unknown"
    confidence: float | None = None
    review_required: bool = False
    risk_flags: tuple[str, ...] = ()
    block_id: str | None = None


@dataclass(frozen=True)
class DocumentBlock:
    block_id: str
    document_id: str
    ordinal: int
    text: str
    text_sha256: str
    page: int | None = None
    paragraph: int | None = None
    style: str | None = None
    kind: str = "paragraph"
    bbox: tuple[float, float, float, float] | None = None
    source: str = "unknown"
    confidence: float | None = None
    review_required: bool = False
    risk_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class PageExtractionEvidence:
    page: int
    image_sha256: str
    native_text_sha256: str
    ocr_text_sha256: str | None
    selected_text_sha256: str
    native_character_count: int
    ocr_character_count: int
    selected_character_count: int
    selected_source: Literal[
        "native",
        "ocr",
        "document_engine",
        "machine_consensus",
        "reviewed",
        "none",
    ]
    review_status: Literal["not_reviewed", "human_reviewed"] = "not_reviewed"
    review_required: bool = True
    ocr_confidence: float | None = None
    native_ocr_consistency: float | None = None
    document_engine_text_sha256: str | None = None
    document_engine_character_count: int = 0
    document_engine_name: str | None = None
    document_engine_version: str | None = None
    document_engine_schema: str | None = None
    document_engine_method: str | None = None
    document_engine_backend: str | None = None
    document_engine_language: str | None = None
    ocr_document_engine_consistency: float | None = None
    critical_tokens_match: bool | None = None
    risk_flags: tuple[str, ...] = ()
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    review_file_sha256: str | None = None


@dataclass(frozen=True)
class ExtractionQuality:
    extractor: str
    extractor_version: str | None
    block_count: int
    page_count: int | None
    character_count: int
    low_text_pages: int = 0
    needs_ocr: bool = False
    review_required: bool = False
    source_sha256: str | None = None
    reviewed_page_count: int = 0
    page_evidence: tuple[PageExtractionEvidence, ...] = ()
    warnings: tuple[str, ...] = ()
    configuration: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionResult:
    blocks: tuple[TextBlock, ...]
    quality: ExtractionQuality


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    title: str
    document_number: str | None
    aliases: tuple[str, ...]
    promulgated_on: str | None
    jurisdiction: str
    relative_path: str
    format: str
    official_source: str
    source_sha256: str
    byte_size: int
    document_type: str
    issuer: str
    authority_rank: int
    effective_from: str | None = None
    effective_to: str | None = None
    status: str = "unverified_current"
    note: str | None = None
    extraction_method: str = "unknown"
    extraction_version: str | None = None
    extraction_configuration: tuple[str, ...] = ()
    extraction_review_required: bool = True
    extraction_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Segment:
    segment_id: str
    document_id: str
    ordinal: int
    kind: str
    text: str
    text_sha256: str
    heading: str | None = None
    article_label: str | None = None
    part_index: int = 1
    page_start: int | None = None
    page_end: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    source_block_ids: tuple[str, ...] = ()
    extraction_review_required: bool = False
    extraction_risk_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchRequest:
    query: str
    purpose: Purpose = "auto"
    as_of: str | None = None
    limit: int = 5
    max_chars: int = 3500
    document_types: tuple[str, ...] = ()

    def normalized(self) -> SearchRequest:
        query = self.query.strip()
        if len(query) > 8000:
            raise ValueError("query must not exceed 8000 characters")
        if self.purpose not in PURPOSES:
            raise ValueError(f"unsupported search purpose: {self.purpose}")
        if len(self.document_types) > 8:
            raise ValueError("document_types must not contain more than 8 values")
        return SearchRequest(
            query=query,
            purpose=self.purpose,
            as_of=self.as_of,
            limit=max(1, min(self.limit, 5)),
            max_chars=max(500, min(self.max_chars, 6000)),
            document_types=tuple(dict.fromkeys(self.document_types)),
        )


@dataclass(frozen=True)
class EvidenceCard:
    schema_version: str
    release_id: str
    receipt_id: str
    segment_id: str
    document_id: str
    title: str
    document_number: str | None
    jurisdiction: str
    promulgated_on: str | None
    document_type: str
    issuer: str
    authority_rank: int
    official_source: str
    source_sha256: str
    segment_sha256: str
    score: float
    hit_reason: str
    retrieval_channel: Literal["article_exact", "title_exact", "chinese_fts"]
    temporal_classification: Literal[
        "not_evaluated", "verified_in_scope", "unverified_metadata"
    ]
    excerpt: str
    article_label: str | None = None
    heading: str | None = None
    effective_from: str | None = None
    effective_to: str | None = None
    status: str = "unverified_current"
    page_start: int | None = None
    page_end: int | None = None
    paragraph_start: int | None = None
    paragraph_end: int | None = None
    temporal_review_required: bool = True
    extraction_method: str = "unknown"
    extraction_configuration: tuple[str, ...] = ()
    extraction_review_required: bool = True
    extraction_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["extraction_configuration"] = list(self.extraction_configuration)
        value["extraction_warnings"] = list(self.extraction_warnings)
        return value


@dataclass(frozen=True)
class GraphPath:
    path_id: str
    seed_document_id: str
    seed_title: str
    target_document_id: str
    target_title: str
    target_document_type: str
    relation_id: str
    predicate: Literal[
        "cites", "amends", "repeals", "replaces", "implements", "exception_to"
    ]
    direction: Literal["outbound", "inbound"]
    provenance_segment_id: str
    provenance_receipt_id: str
    review_status: Literal["deterministic_exact"]
    derivation: str
    authority: Literal["derived_navigation"]
    hops: Literal[1]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObligationCoverage:
    obligation_id: str
    role: str
    required: bool
    status: Literal["covered", "uncertain", "gap"]
    evidence_segment_ids: tuple[str, ...] = ()
    graph_path_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_segment_ids"] = list(self.evidence_segment_ids)
        value["graph_path_ids"] = list(self.graph_path_ids)
        return value


@dataclass(frozen=True)
class SearchGap:
    code: Literal[
        "exact_target_unresolved",
        "query_focus_unresolved",
        "temporal_metadata_unverified",
        "temporal_out_of_scope",
        "required_obligation_uncovered",
        "required_obligation_uncertain",
        "no_primary_evidence",
    ]
    obligation_id: str | None
    message: str
    blocking: bool = True
    candidate_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SearchResponse:
    schema_version: str
    release_id: str
    mode: str
    query_plan: dict[str, Any]
    evidence_compilation: dict[str, Any]
    evidence: tuple[EvidenceCard, ...]
    uncertain_evidence: tuple[EvidenceCard, ...]
    graph_paths: tuple[GraphPath, ...]
    obligation_coverage: tuple[ObligationCoverage, ...]
    gaps: tuple[SearchGap, ...]
    notices: tuple[str, ...] = ()
    next_questions: tuple[str, ...] = ()
    total_excerpt_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence"] = [card.to_dict() for card in self.evidence]
        value["uncertain_evidence"] = [card.to_dict() for card in self.uncertain_evidence]
        value["graph_paths"] = [path.to_dict() for path in self.graph_paths]
        value["obligation_coverage"] = [item.to_dict() for item in self.obligation_coverage]
        value["gaps"] = [gap.to_dict() for gap in self.gaps]
        value["notices"] = list(self.notices)
        value["next_questions"] = list(self.next_questions)
        return value


@dataclass
class BuildReport:
    schema_version: str
    release_id: str
    document_count: int = 0
    segment_count: int = 0
    relation_count: int = 0
    source_bytes: int = 0
    extractors: dict[str, int] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
