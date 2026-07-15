from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .models import ExtractionQuality, ExtractionResult, TextBlock
from .util import normalize_text

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MAX_OOXML_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_DERIVATIVE_BYTES = 64 * 1024 * 1024
_OCR_DPI = 300
_OCR_LANGUAGE = "chi_sim+eng"
_OCR_PSM = "3"
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
                        TextBlock(text=text, paragraph=paragraph_index, style="table-row")
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

    blocks: list[TextBlock] = []
    low_text_pages = 0
    warnings: list[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
        except Exception as error:  # Keep a per-page failure visible in the build report.
            warnings.append(f"page {page_number}: {type(error).__name__}")
            raw_text = ""
        text = normalize_text(raw_text)
        if len(text) < 40:
            low_text_pages += 1
        if not text:
            continue
        lines = [normalize_text(line) for line in raw_text.splitlines()]
        page_lines = [line for line in lines if line]
        if page_lines:
            blocks.extend(TextBlock(text=line, page=page_number) for line in page_lines)
        else:
            blocks.append(TextBlock(text=text, page=page_number))

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
        warnings.append("PDF text layer is incomplete; OCR or MinerU review is required")
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


def extract_mineru_markdown(
    markdown_path: Path, *, extractor_version: str | None = None
) -> ExtractionResult:
    if markdown_path.is_symlink() or markdown_path.stat().st_size > _MAX_DERIVATIVE_BYTES:
        raise ExtractionError(f"unsafe MinerU Markdown output: {markdown_path}")
    try:
        raw_text = markdown_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ExtractionError(f"cannot read MinerU output: {markdown_path}") from error
    blocks = tuple(
        TextBlock(text=line, paragraph=index)
        for index, raw_line in enumerate(raw_text.splitlines(), start=1)
        if (line := normalize_text(raw_line.lstrip("#>-* ")))
    )
    character_count = sum(len(block.text) for block in blocks)
    if character_count < 20:
        raise ExtractionError(f"MinerU output contains too little text: {markdown_path}")
    return ExtractionResult(
        blocks=blocks,
        quality=ExtractionQuality(
            extractor="mineru-markdown",
            extractor_version=extractor_version,
            block_count=len(blocks),
            page_count=None,
            character_count=character_count,
        ),
    )


def _mineru_version() -> str | None:
    try:
        process = subprocess.run(
            ["mineru", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = normalize_text(process.stdout or process.stderr)
    return output[:200] or None


def extract_mineru_content_list(
    content_list_path: Path, *, extractor_version: str | None = None
) -> ExtractionResult:
    if content_list_path.is_symlink() or content_list_path.stat().st_size > _MAX_DERIVATIVE_BYTES:
        raise ExtractionError(f"unsafe MinerU content list output: {content_list_path}")
    try:
        payload = json.loads(content_list_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExtractionError(f"cannot read MinerU content list: {content_list_path}") from error
    if not isinstance(payload, list):
        raise ExtractionError("MinerU content list must be an array")
    blocks: list[TextBlock] = []
    pages: list[int] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        page_index = item.get("page_idx")
        page = page_index + 1 if isinstance(page_index, int) and page_index >= 0 else None
        candidates: list[str] = []
        for key in ("text", "table_body"):
            value = item.get(key)
            if isinstance(value, str):
                candidates.append(value)
        for key in ("image_caption", "image_footnote", "table_caption", "table_footnote"):
            value = item.get(key)
            if isinstance(value, list):
                candidates.extend(part for part in value if isinstance(part, str))
        text = normalize_text("\n".join(candidates))
        if not text:
            continue
        if page is not None:
            pages.append(page)
        blocks.append(TextBlock(text=text, page=page, paragraph=index))
    character_count = sum(len(block.text) for block in blocks)
    if character_count < 20:
        raise ExtractionError(f"MinerU output contains too little text: {content_list_path}")
    return ExtractionResult(
        blocks=tuple(blocks),
        quality=ExtractionQuality(
            extractor="mineru-content-list",
            extractor_version=extractor_version,
            block_count=len(blocks),
            page_count=max(pages) if pages else None,
            character_count=character_count,
        ),
    )


def run_mineru(path: Path, *, backend: str = "pipeline") -> ExtractionResult:
    """Run an installed MinerU CLI in an isolated temporary directory.

    Calling this function is an explicit operator choice after the native PDF
    quality gate fails. DeepLaw requires MinerU's local model-source mode, but an
    OS-level network sandbox remains the operator's responsibility.
    """

    if os.environ.get("MINERU_MODEL_SOURCE", "").lower() != "local":
        raise ExtractionError(
            "MinerU fallback requires MINERU_MODEL_SOURCE=local and preinstalled models"
        )
    with tempfile.TemporaryDirectory(prefix="deeplaw-mineru-") as directory:
        output = Path(directory)
        command = ["mineru", "-p", str(path), "-o", str(output), "-b", backend]
        environment = os.environ.copy()
        environment["MINERU_MODEL_SOURCE"] = "local"
        try:
            process = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=1800,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExtractionError("MinerU CLI is unavailable or timed out") from error
        if process.returncode != 0:
            diagnostic = normalize_text(process.stderr)[-800:]
            raise ExtractionError(f"MinerU failed: {diagnostic or 'unknown error'}")
        extractor_version = _mineru_version()
        content_lists = sorted(
            (
                value
                for value in output.rglob("*content_list.json")
                if value.is_file() and not value.is_symlink()
            ),
            key=lambda value: value.stat().st_size,
            reverse=True,
        )
        if content_lists:
            return extract_mineru_content_list(
                content_lists[0], extractor_version=extractor_version
            )
        candidates = sorted(
            (
                value
                for value in output.rglob("*.md")
                if value.is_file() and not value.is_symlink()
            ),
            key=lambda value: value.stat().st_size,
            reverse=True,
        )
        if not candidates:
            inventory = [
                str(item.relative_to(output)) for item in output.rglob("*") if item.is_file()
            ]
            raise ExtractionError(
                "MinerU produced no Markdown output: "
                + json.dumps(inventory[:20], ensure_ascii=False)
            )
        return extract_mineru_markdown(candidates[0], extractor_version=extractor_version)


def _page_image_number(path: Path) -> int:
    match = re.search(r"-(\d+)$", path.stem)
    if not match:
        raise ExtractionError(f"unexpected pdftoppm page filename: {path.name}")
    return int(match.group(1))


def run_tesseract(path: Path, *, language: str = _OCR_LANGUAGE) -> ExtractionResult:
    """OCR a scanned PDF locally without changing the source file."""

    pdftoppm = shutil.which("pdftoppm") or os.environ.get("DEEPLAW_PDFTOPPM")
    tesseract = shutil.which("tesseract") or os.environ.get("DEEPLAW_TESSERACT")
    if not pdftoppm or not tesseract:
        raise ExtractionError("Tesseract fallback requires pdftoppm and tesseract on PATH")
    with tempfile.TemporaryDirectory(prefix="deeplaw-tesseract-") as directory:
        output = Path(directory)
        prefix = output / "page"
        try:
            render = subprocess.run(
                [pdftoppm, "-r", str(_OCR_DPI), "-png", str(path), str(prefix)],
                check=False,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExtractionError("pdftoppm failed to start or timed out") from error
        if render.returncode != 0:
            raise ExtractionError(f"pdftoppm failed: {normalize_text(render.stderr)[-800:]}")
        images = sorted(output.glob("page-*.png"), key=_page_image_number)
        if not images:
            raise ExtractionError("pdftoppm produced no page images")
        blocks: list[TextBlock] = []
        for page_number, image in enumerate(images, start=1):
            try:
                process = subprocess.run(
                    [
                        tesseract,
                        str(image),
                        "stdout",
                        "-l",
                        language,
                        "--psm",
                        _OCR_PSM,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ExtractionError(
                    f"tesseract failed to start or timed out on page {page_number}"
                ) from error
            if process.returncode != 0:
                raise ExtractionError(
                    f"tesseract failed on page {page_number}: "
                    f"{normalize_text(process.stderr)[-800:]}"
                )
            for raw_line in process.stdout.splitlines():
                if line := normalize_text(raw_line):
                    blocks.append(TextBlock(text=line, page=page_number))
        character_count = sum(len(block.text) for block in blocks)
        if character_count < 80:
            raise ExtractionError("Tesseract OCR produced too little text")
        version_process = subprocess.run(
            [tesseract, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        version_lines = (version_process.stdout or version_process.stderr).splitlines()
        tesseract_version = normalize_text(version_lines[0]) if version_lines else "unknown"
        renderer_process = subprocess.run(
            [pdftoppm, "-v"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        renderer_lines = (renderer_process.stdout or renderer_process.stderr).splitlines()
        renderer_version = normalize_text(renderer_lines[0]) if renderer_lines else "unknown"
        extractor_version = f"{tesseract_version}; {renderer_version}"
        return ExtractionResult(
            blocks=tuple(blocks),
            quality=ExtractionQuality(
                extractor="tesseract-ocr",
                extractor_version=extractor_version,
                block_count=len(blocks),
                page_count=len(images),
                character_count=character_count,
                configuration=(
                    f"dpi={_OCR_DPI}",
                    f"language={language}",
                    f"psm={_OCR_PSM}",
                ),
                warnings=(
                    "OCR derivative was generated locally; page-level manual review is required",
                ),
            ),
        )


def extract_document(
    path: Path,
    format_name: str,
    *,
    pdf_fallback: str = "off",
) -> ExtractionResult:
    format_name = format_name.upper()
    if format_name == "DOCX":
        return extract_docx(path)
    if format_name != "PDF":
        raise ExtractionError(f"unsupported source format: {format_name}")

    result = extract_pdf(path)
    if result.quality.needs_ocr and pdf_fallback == "mineru":
        fallback = run_mineru(path, backend="pipeline")
        return replace(
            fallback,
            quality=replace(
                fallback.quality,
                warnings=(*result.quality.warnings, *fallback.quality.warnings),
            ),
        )
    if result.quality.needs_ocr and pdf_fallback == "tesseract":
        fallback = run_tesseract(path)
        return replace(
            fallback,
            quality=replace(
                fallback.quality,
                warnings=(*result.quality.warnings, *fallback.quality.warnings),
            ),
        )
    return result
