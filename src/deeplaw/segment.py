from __future__ import annotations

import re
from collections.abc import Iterable

from .models import DocumentBlock, Segment, TextBlock
from .util import article_pattern, normalize_article_label, normalize_text, sha256_bytes, stable_id

_HEADING = re.compile(
    r"^(第[〇零一二两三四五六七八九十百千万0-9]+[编章节])|"
    r"^(基本案情|裁判结果|裁判理由|裁判要旨|关联索引|关键词|案件编号|入库编号|法条链接)$"
)


def _expanded_blocks(blocks: Iterable[TextBlock | DocumentBlock]) -> list[TextBlock]:
    expanded: list[TextBlock] = []
    for block in blocks:
        lines = [normalize_text(line) for line in block.text.splitlines()]
        values = [line for line in lines if line]
        if not values:
            continue
        expanded.extend(
            TextBlock(
                text=value,
                page=block.page,
                paragraph=block.paragraph,
                style=block.style,
                kind=block.kind,
                bbox=block.bbox,
                source=block.source,
                confidence=block.confidence,
                review_required=block.review_required,
                risk_flags=block.risk_flags,
                block_id=block.block_id,
            )
            for value in values
        )
    return expanded


def _split_long_group(group: list[TextBlock], max_chars: int) -> list[list[TextBlock]]:
    chunks: list[list[TextBlock]] = []
    current: list[TextBlock] = []
    current_chars = 0
    blocks: list[TextBlock] = []
    for block in group:
        if len(block.text) <= max_chars:
            blocks.append(block)
            continue
        start = 0
        while start < len(block.text):
            end = min(len(block.text), start + max_chars)
            if end < len(block.text):
                search_start = start + max_chars // 2
                boundary = max(
                    (
                        block.text.rfind(mark, search_start, end)
                        for mark in ("。", "；", "\uff01", "\uff1f")
                    ),
                    default=-1,
                )
                if boundary >= search_start:
                    end = boundary + 1
            text = block.text[start:end].strip()
            if text:
                blocks.append(
                    TextBlock(
                        text=text,
                        page=block.page,
                        paragraph=block.paragraph,
                        style=block.style,
                        kind=block.kind,
                        bbox=block.bbox,
                        source=block.source,
                        confidence=block.confidence,
                        review_required=block.review_required,
                        risk_flags=block.risk_flags,
                        block_id=block.block_id,
                    )
                )
            start = end
    for block in blocks:
        block_chars = len(block.text) + 1
        if current and current_chars + block_chars > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(block)
        current_chars += block_chars
    if current:
        chunks.append(current)
    return chunks


def materialize_document_blocks(
    document_id: str,
    blocks: Iterable[TextBlock],
) -> tuple[DocumentBlock, ...]:
    """Bind transient extractor blocks to stable, source-verifiable block IDs."""

    materialized: list[DocumentBlock] = []
    for ordinal, block in enumerate(_expanded_blocks(blocks), start=1):
        text_hash = sha256_bytes(block.text.encode("utf-8"))
        block_id = stable_id(
            "lawblock",
            document_id,
            str(ordinal),
            text_hash,
        )
        materialized.append(
            DocumentBlock(
                block_id=block_id,
                document_id=document_id,
                ordinal=ordinal,
                text=block.text,
                text_sha256=text_hash,
                page=block.page,
                paragraph=block.paragraph,
                style=block.style,
                kind=block.kind,
                bbox=block.bbox,
                source=block.source,
                confidence=block.confidence,
                review_required=block.review_required,
                risk_flags=tuple(dict.fromkeys(block.risk_flags)),
            )
        )
    return tuple(materialized)


def segment_document(
    document_id: str,
    blocks: Iterable[TextBlock | DocumentBlock],
    *,
    max_chars: int = 4500,
) -> tuple[Segment, ...]:
    values = _expanded_blocks(blocks)
    groups: list[tuple[str, str | None, str | None, list[TextBlock]]] = []
    current: list[TextBlock] = []
    current_kind = "preamble"
    current_heading: str | None = None
    current_article: str | None = None

    def flush() -> None:
        nonlocal current
        if current:
            groups.append((current_kind, current_heading, current_article, current))
            current = []

    for block in values:
        article = normalize_article_label(block.text)
        article_at_start = article is not None and article_pattern().match(block.text) is not None
        heading = bool(_HEADING.match(block.text)) or bool(
            block.style and "heading" in block.style.lower()
        )
        if article_at_start:
            flush()
            current_kind = "article"
            current_article = article
            current_heading = None
        elif heading:
            flush()
            current_kind = "section"
            current_heading = block.text
            current_article = None
        current.append(block)
    flush()

    segments: list[Segment] = []
    ordinal = 0
    for kind, heading, article, group in groups:
        chunks = _split_long_group(group, max_chars=max_chars)
        for part_index, chunk in enumerate(chunks, start=1):
            ordinal += 1
            text = "\n".join(block.text for block in chunk)
            text_hash = sha256_bytes(text.encode("utf-8"))
            pages = [block.page for block in chunk if block.page is not None]
            paragraphs = [block.paragraph for block in chunk if block.paragraph is not None]
            source_block_ids = tuple(
                dict.fromkeys(
                    block.block_id for block in chunk if block.block_id is not None
                )
            )
            risk_flags = tuple(
                dict.fromkeys(
                    risk
                    for block in chunk
                    for risk in block.risk_flags
                )
            )
            segment_id = stable_id(
                "seg",
                document_id,
                str(ordinal),
                article or "",
                str(part_index),
                text_hash,
            )
            segments.append(
                Segment(
                    segment_id=segment_id,
                    document_id=document_id,
                    ordinal=ordinal,
                    kind=kind,
                    text=text,
                    text_sha256=text_hash,
                    heading=heading,
                    article_label=article,
                    part_index=part_index,
                    page_start=min(pages) if pages else None,
                    page_end=max(pages) if pages else None,
                    paragraph_start=min(paragraphs) if paragraphs else None,
                    paragraph_end=max(paragraphs) if paragraphs else None,
                    source_block_ids=source_block_ids,
                    extraction_review_required=any(
                        block.review_required for block in chunk
                    ),
                    extraction_risk_flags=risk_flags,
                )
            )
    return tuple(segments)
