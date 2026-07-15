from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .models import ExtractionQuality, ExtractionResult, TextBlock
from .util import normalize_text
from .vision import (
    VisionExtractionError,
    extract_pdf_vision_consensus,
    validate_pdf_render_budget,
)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_OOXML_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_TEXT_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_TEXT_CHARACTERS = 20 * 1024 * 1024
_MAX_TEXT_LINE_CHARACTERS = 2 * 1024 * 1024
_MAX_TEXT_BLOCKS = 200_000
_MAX_PDF_EXTRACTED_CHARACTERS = 20 * 1024 * 1024
_HAN = r"[\u3400-\u4dbf\u4e00-\u9fff]"
_HAN_CHARACTER = re.compile(_HAN)
_HAN_INTERSPACE = re.compile(rf"(?<={_HAN})\s(?={_HAN})")


class ExtractionError(RuntimeError):
    pass


def _text_layer_suspicious(text: str) -> tuple[bool, str | None]:
    han_count = len(_HAN_CHARACTER.findall(text))
    if han_count < 80:
        return False, None
    interspace_ratio = len(_HAN_INTERSPACE.findall(text)) / han_count
    replacement_ratio = text.count("�") / max(1, len(text))
    if interspace_ratio >= 0.15:
        return True, f"inter-Han whitespace ratio is {interspace_ratio:.3f}"
    if replacement_ratio >= 0.002:
        return True, f"replacement-character ratio is {replacement_ratio:.3f}"
    return False, None


def _package_version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _paragraph_text(
    element: ET.Element,
    *,
    footnotes: dict[str, str] | None = None,
) -> str:
    values: list[str] = []
    for node in element.iter():
        if node.tag == f"{_W}t" and node.text:
            values.append(node.text)
        elif node.tag in {f"{_W}tab", f"{_W}br", f"{_W}cr"}:
            values.append("\t" if node.tag == f"{_W}tab" else "\n")
        elif node.tag == f"{_W}footnoteReference" and footnotes:
            footnote_id = node.attrib.get(f"{_W}id", "")
            if footnote_text := footnotes.get(footnote_id):
                values.append(f" [注{footnote_id}: {footnote_text}] ")
    return normalize_text("".join(values))


def _paragraph_style(element: ET.Element) -> str | None:
    style = element.find(f"./{_W}pPr/{_W}pStyle")
    if style is None:
        return None
    return style.attrib.get(f"{_W}val")


def _read_ooxml_member(archive: zipfile.ZipFile, member: str) -> bytes:
    info = archive.getinfo(member)
    if info.file_size > _MAX_OOXML_MEMBER_BYTES:
        raise ExtractionError(f"OOXML member is too large: {member}")
    if info.compress_size and info.file_size / info.compress_size > 200:
        raise ExtractionError(f"OOXML member has an unsafe compression ratio: {member}")
    return archive.read(member)


def _read_footnotes(archive: zipfile.ZipFile) -> dict[str, str]:
    try:
        payload = _read_ooxml_member(archive, "word/footnotes.xml")
    except KeyError:
        return {}
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ExtractionError("invalid DOCX footnotes XML") from error
    values: dict[str, str] = {}
    for footnote in root.findall(f"./{_W}footnote"):
        footnote_id = footnote.attrib.get(f"{_W}id", "")
        if not footnote_id or footnote_id.startswith("-") or footnote_id == "0":
            continue
        text = _paragraph_text(footnote)
        if text:
            values[footnote_id] = text
    return values


def extract_docx(path: Path) -> ExtractionResult:
    try:
        with zipfile.ZipFile(path) as archive:
            payload = _read_ooxml_member(archive, "word/document.xml")
            footnotes = _read_footnotes(archive)
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise ExtractionError(f"invalid DOCX: {path.name}") from error

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ExtractionError(f"invalid document XML: {path.name}") from error
    body = root.find(f".//{_W}body")
    if body is None:
        raise ExtractionError(f"DOCX has no document body: {path.name}")

    blocks: list[TextBlock] = []
    paragraph_index = 0
    for child in body:
        if child.tag == f"{_W}p":
            text = _paragraph_text(child, footnotes=footnotes)
            if text:
                paragraph_index += 1
                blocks.append(
                    TextBlock(
                        text=text,
                        paragraph=paragraph_index,
                        style=_paragraph_style(child),
                        kind="paragraph",
                        source="ooxml",
                    )
                )
        elif child.tag == f"{_W}tbl":
            for row in child.findall(f"./{_W}tr"):
                cells = [
                    _paragraph_text(cell, footnotes=footnotes) for cell in row.findall(f"./{_W}tc")
                ]
                text = normalize_text(" | ".join(value for value in cells if value))
                if text:
                    paragraph_index += 1
                    blocks.append(
                        TextBlock(
                            text=text,
                            paragraph=paragraph_index,
                            style="table-row",
                            kind="table_row",
                            source="ooxml",
                        )
                    )

    character_count = sum(len(block.text) for block in blocks)
    if character_count < 20:
        raise ExtractionError(f"DOCX contains too little text: {path.name}")
    return ExtractionResult(
        blocks=tuple(blocks),
        quality=ExtractionQuality(
            extractor="ooxml",
            extractor_version="deeplaw-ooxml/v1",
            block_count=len(blocks),
            page_count=None,
            character_count=character_count,
        ),
    )


def extract_pdf(path: Path) -> ExtractionResult:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise ExtractionError("pypdf is required to extract PDF text") from error

    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as error:  # pypdf exposes parser-specific exceptions.
        raise ExtractionError(f"invalid PDF: {path.name}") from error
    try:
        validate_pdf_render_budget(reader.pages)
    except VisionExtractionError as error:
        raise ExtractionError(str(error)) from error

    blocks: list[TextBlock] = []
    low_text_pages = 0
    warnings: list[str] = []
    extracted_character_count = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
        except Exception as error:  # Keep a per-page failure visible in the build report.
            warnings.append(f"page {page_number}: {type(error).__name__}")
            raw_text = ""
        text = normalize_text(raw_text)
        extracted_character_count += len(text)
        if extracted_character_count > _MAX_PDF_EXTRACTED_CHARACTERS:
            raise ExtractionError(
                "PDF extracted text exceeds the "
                f"{_MAX_PDF_EXTRACTED_CHARACTERS} character limit: {path.name}"
            )
        if len(text) < 40:
            low_text_pages += 1
        if not text:
            continue
        lines = [normalize_text(line) for line in raw_text.splitlines()]
        page_lines = [line for line in lines if line]
        if page_lines:
            blocks.extend(
                TextBlock(text=line, page=page_number, source="native")
                for line in page_lines
            )
        else:
            blocks.append(TextBlock(text=text, page=page_number, source="native"))

    page_count = len(reader.pages)
    character_count = sum(len(block.text) for block in blocks)
    extracted_text = "\n".join(block.text for block in blocks)
    suspicious_text, suspicious_reason = _text_layer_suspicious(extracted_text)
    needs_ocr = page_count > 0 and (
        character_count < 80 or low_text_pages / page_count > 0.5 or suspicious_text
    )
    if suspicious_reason:
        warnings.append(f"PDF text layer failed plausibility check: {suspicious_reason}")
    if needs_ocr:
        warnings.append(
            "PDF text layer is incomplete; DeepLaw vision consensus or human review is required"
        )
    return ExtractionResult(
        blocks=tuple(blocks),
        quality=ExtractionQuality(
            extractor="pypdf",
            extractor_version=_package_version("pypdf"),
            block_count=len(blocks),
            page_count=page_count,
            character_count=character_count,
            low_text_pages=low_text_pages,
            needs_ocr=needs_ocr,
            warnings=tuple(warnings),
        ),
    )


def extract_text(path: Path) -> ExtractionResult:
    try:
        source_size = path.stat().st_size
    except OSError as error:
        raise ExtractionError(f"TXT cannot be read: {path.name}") from error
    if source_size > _MAX_TEXT_SOURCE_BYTES:
        raise ExtractionError(
            f"TXT exceeds the {_MAX_TEXT_SOURCE_BYTES // (1024 * 1024)} MiB source limit: "
            f"{path.name}"
        )

    blocks: list[TextBlock] = []
    paragraph = 0
    source_character_count = 0
    try:
        with path.open("r", encoding="utf-8-sig", errors="strict") as source:
            for raw_line in source:
                source_character_count += len(raw_line)
                if source_character_count > _MAX_TEXT_CHARACTERS:
                    raise ExtractionError(
                        f"TXT exceeds the {_MAX_TEXT_CHARACTERS} character limit: {path.name}"
                    )
                if len(raw_line) > _MAX_TEXT_LINE_CHARACTERS:
                    raise ExtractionError(
                        f"TXT line exceeds the {_MAX_TEXT_LINE_CHARACTERS} character limit: "
                        f"{path.name}"
                    )
                text = normalize_text(raw_line)
                if not text:
                    continue
                paragraph += 1
                if paragraph > _MAX_TEXT_BLOCKS:
                    raise ExtractionError(
                        f"TXT exceeds the {_MAX_TEXT_BLOCKS} block limit: {path.name}"
                    )
                blocks.append(
                    TextBlock(
                        text=text,
                        paragraph=paragraph,
                        kind="text_line",
                        source="text",
                    )
                )
    except UnicodeDecodeError as error:
        raise ExtractionError(f"TXT must be UTF-8 encoded: {path.name}") from error
    except OSError as error:
        raise ExtractionError(f"TXT cannot be read: {path.name}") from error
    character_count = sum(len(block.text) for block in blocks)
    if character_count < 20:
        raise ExtractionError(f"TXT contains too little text: {path.name}")
    return ExtractionResult(
        blocks=tuple(blocks),
        quality=ExtractionQuality(
            extractor="utf8-text",
            extractor_version="deeplaw-text/v1",
            block_count=len(blocks),
            page_count=None,
            character_count=character_count,
        ),
    )


def extract_document(
    path: Path,
    format_name: str,
    *,
    pdf_fallback: str = "off",
    reviewed_pages_path: Path | None = None,
) -> ExtractionResult:
    format_name = format_name.upper()
    if reviewed_pages_path is not None and (
        format_name != "PDF"
        or pdf_fallback not in {"vision-consensus", "document-engine"}
    ):
        raise ExtractionError(
            "reviewed-pages requires PDF format and an evidence-preserving PDF fallback"
        )
    if format_name == "DOCX":
        return extract_docx(path)
    if format_name == "TXT":
        return extract_text(path)
    if format_name != "PDF":
        raise ExtractionError(f"unsupported source format: {format_name}")
    if pdf_fallback not in {"off", "vision-consensus", "document-engine"}:
        raise ExtractionError(f"unsupported PDF fallback: {pdf_fallback}")

    if pdf_fallback in {"vision-consensus", "document-engine"}:
        try:
            return extract_pdf_vision_consensus(
                path,
                reviewed_pages_path=reviewed_pages_path,
                use_document_engine=pdf_fallback == "document-engine",
            )
        except VisionExtractionError as error:
            raise ExtractionError(str(error)) from error

    return extract_pdf(path)
