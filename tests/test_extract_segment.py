from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from deeplaw import extract as extract_module
from deeplaw import vision
from deeplaw.extract import (
    ExtractionError,
    _text_layer_suspicious,
    extract_docx,
    extract_pdf,
    extract_text,
)
from deeplaw.models import TextBlock
from deeplaw.segment import segment_document
from deeplaw.util import excerpt, normalize_article_label, search_terms
from deeplaw.vision import _page_image_number

from .helpers import write_docx


def test_docx_preserves_footnote_at_reference(tmp_path: Path) -> None:
    path = tmp_path / "law.docx"
    write_docx(
        path,
        ["测试法", "第一条 本条含脚注。"],
        footnote=(1, "脚注原文", 1),
    )

    result = extract_docx(path)

    assert result.quality.extractor == "ooxml"
    assert result.quality.extractor_version == "deeplaw-ooxml/v1"
    assert "[注1: 脚注原文]" in result.blocks[1].text


def test_blank_pdf_is_rejected_by_native_quality_gate(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.showPage()
    pdf.save()

    result = extract_pdf(path)

    assert result.quality.page_count == 1
    assert result.quality.needs_ocr is True


def test_native_pdf_path_reuses_the_page_render_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "two-pages.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 720, "first page")
    pdf.showPage()
    pdf.drawString(72, 720, "second page")
    pdf.showPage()
    pdf.save()

    monkeypatch.setattr(vision, "_MAX_PDF_PAGES", 1)
    with pytest.raises(ExtractionError, match="page render limit"):
        extract_pdf(path)


def test_txt_streaming_limits_source_characters_and_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_limited = tmp_path / "source-limited.txt"
    source_limited.write_text("第一条 超出来源大小。\n", encoding="utf-8")
    monkeypatch.setattr(extract_module, "_MAX_TEXT_SOURCE_BYTES", 8)
    with pytest.raises(ExtractionError, match="source limit"):
        extract_text(source_limited)

    monkeypatch.setattr(extract_module, "_MAX_TEXT_SOURCE_BYTES", 1024)
    character_limited = tmp_path / "character-limited.txt"
    character_limited.write_text("第一条 超出字符预算。\n", encoding="utf-8")
    monkeypatch.setattr(extract_module, "_MAX_TEXT_CHARACTERS", 4)
    with pytest.raises(ExtractionError, match="character limit"):
        extract_text(character_limited)

    monkeypatch.setattr(extract_module, "_MAX_TEXT_CHARACTERS", 1024)
    block_limited = tmp_path / "block-limited.txt"
    block_limited.write_text("第一条 一。\n第二条 二。\n", encoding="utf-8")
    monkeypatch.setattr(extract_module, "_MAX_TEXT_BLOCKS", 1)
    with pytest.raises(ExtractionError, match="block limit"):
        extract_text(block_limited)


def test_spaced_ocr_text_layer_is_rejected() -> None:
    text = " ".join("中华人民共和国中央办公厅发布规范性文件" * 20)

    suspicious, reason = _text_layer_suspicious(text)

    assert suspicious is True
    assert reason and "inter-Han whitespace" in reason


def test_article_segmentation_is_stable_and_preserves_locators() -> None:
    blocks = [
        TextBlock("中华人民共和国测试法", paragraph=1),
        TextBlock("第一章 总则", paragraph=2),
        TextBlock("第一条 为了规范测试活动。", paragraph=3),
        TextBlock("第二条 本法适用于测试。", paragraph=4),
    ]

    first = segment_document("doc_abc", blocks)
    second = segment_document("doc_abc", blocks)

    assert first == second
    assert [item.article_label for item in first if item.kind == "article"] == [
        "第一条",
        "第二条",
    ]
    assert first[-1].paragraph_start == 4
    assert first[-1].text_sha256


def test_chinese_terms_and_article_normalization() -> None:
    assert normalize_article_label("刑法第二百六十六条之二") == "第二百六十六条之二"
    terms = search_terms("反洗钱法 客户尽职调查")
    assert "反洗钱" in terms
    assert "客户" in terms
    assert len(terms) == len(set(terms))


def test_pdftoppm_pages_are_sorted_numerically() -> None:
    pages = [Path(f"page-{number}.png") for number in (1, 10, 11, 12, 2, 3)]

    ordered = sorted(pages, key=_page_image_number)

    assert [path.name for path in ordered] == [
        "page-1.png",
        "page-2.png",
        "page-3.png",
        "page-10.png",
        "page-11.png",
        "page-12.png",
    ]


def test_single_long_paragraph_respects_segment_limit() -> None:
    segments = segment_document("doc_long", [TextBlock("第一条 " + "法" * 10_000)])

    assert len(segments) == 3
    assert all(len(segment.text) <= 4500 for segment in segments)
    assert [segment.part_index for segment in segments] == [1, 2, 3]
    assert {segment.article_label for segment in segments} == {"第一条"}


def test_excerpt_maps_compact_match_back_to_source_offset() -> None:
    text = "第一条" + "。" * 1000 + "关键目标应当出现在证据窗口中" + "尾" * 1000

    value = excerpt(text, "关键目标", max_chars=700)

    assert "关键目标" in value
    assert len(value) <= 700
