from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .models import ExtractionQuality, ExtractionResult, PageExtractionEvidence, TextBlock
from .util import compact_text, normalize_text, sha256_bytes, sha256_file

PIPELINE_NAME = "deeplaw-vision-consensus"
PIPELINE_VERSION = "deeplaw-vision-consensus/v1"
REVIEWED_PAGES_SCHEMA = "deeplaw.reviewed-pages/v1"
EXTRACTION_EVIDENCE_SCHEMA = "deeplaw.extraction-evidence/v1"

_RENDER_DPI = 300
_OCR_LANGUAGE = "chi_sim+eng"
_OCR_PSM = "3"
_MIN_PAGE_CHARACTERS = 80
_MIN_OCR_CONFIDENCE = 0.75
_MIN_NATIVE_OCR_CONSISTENCY = 0.82
_MAX_REVIEW_FILE_BYTES = 8 * 1024 * 1024
_MAX_REVIEWED_PAGE_CHARACTERS = 2 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HAN_INTERSPACE = re.compile(
    r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])\s(?=[\u3400-\u4dbf\u4e00-\u9fff])"
)


class VisionExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class _OcrPage:
    text: str
    confidence: float | None


@dataclass(frozen=True)
class _ReviewedPage:
    page: int
    image_sha256: str
    text: str
    notes: str


@dataclass(frozen=True)
class _ReviewBundle:
    reviewed_by: str
    reviewed_at: str
    file_sha256: str
    pages: dict[int, _ReviewedPage]


def _canonical_page_text(value: str) -> str:
    lines = (normalize_text(line) for line in value.splitlines())
    return "\n".join(line for line in lines if line)


def _closed_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise VisionExtractionError(f"reviewed-pages contains duplicate key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise VisionExtractionError(f"reviewed-pages contains non-standard JSON value: {value}")


def _exact_keys(value: dict[str, Any], expected: set[str], *, field: str) -> None:
    actual = set(value)
    if actual != expected:
        unknown = sorted(actual - expected)
        missing = sorted(expected - actual)
        details = []
        if unknown:
            details.append(f"unknown={unknown}")
        if missing:
            details.append(f"missing={missing}")
        raise VisionExtractionError(f"{field} must use the closed schema ({', '.join(details)})")


def _bounded_string(value: Any, *, field: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise VisionExtractionError(f"{field} must be a string")
    if not allow_empty and not value.strip():
        raise VisionExtractionError(f"{field} must not be blank")
    if len(value) > maximum:
        raise VisionExtractionError(f"{field} exceeds {maximum} characters")
    return value


def _rfc3339(value: Any, *, field: str) -> str:
    text = _bounded_string(value, field=field, maximum=64)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise VisionExtractionError(f"{field} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise VisionExtractionError(f"{field} must include a timezone")
    return text


def load_reviewed_pages(
    path: Path,
    *,
    source_sha256: str,
    page_count: int,
    image_sha256_by_page: dict[int, str],
) -> _ReviewBundle:
    """Load a human-authored review file without creating or upgrading review claims."""

    if path.is_symlink() or not path.is_file():
        raise VisionExtractionError(f"reviewed-pages file is not a regular file: {path}")
    if path.stat().st_size > _MAX_REVIEW_FILE_BYTES:
        raise VisionExtractionError("reviewed-pages file exceeds the 8 MiB limit")
    raw = path.read_bytes()
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_closed_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VisionExtractionError("reviewed-pages file is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise VisionExtractionError("reviewed-pages root must be an object")
    _exact_keys(
        payload,
        {
            "schemaVersion",
            "sourceSha256",
            "reviewer",
            "reviewedAt",
            "attestation",
            "pages",
        },
        field="reviewed-pages root",
    )
    if payload["schemaVersion"] != REVIEWED_PAGES_SCHEMA:
        raise VisionExtractionError(
            f"unsupported reviewed-pages schema: {payload['schemaVersion']!r}"
        )
    declared_source = payload["sourceSha256"]
    if not isinstance(declared_source, str) or not _SHA256.fullmatch(declared_source):
        raise VisionExtractionError("reviewed-pages sourceSha256 must be lowercase SHA-256")
    if declared_source != source_sha256:
        raise VisionExtractionError("reviewed-pages sourceSha256 does not match the PDF")
    if payload["attestation"] != "visual_page_comparison":
        raise VisionExtractionError(
            "reviewed-pages attestation must be 'visual_page_comparison'"
        )

    reviewer = payload["reviewer"]
    if not isinstance(reviewer, dict):
        raise VisionExtractionError("reviewed-pages reviewer must be an object")
    _exact_keys(
        reviewer,
        {"type", "name", "organization", "role"},
        field="reviewed-pages reviewer",
    )
    if reviewer["type"] != "human":
        raise VisionExtractionError("reviewed-pages reviewer.type must be 'human'")
    reviewer_name = _bounded_string(reviewer["name"], field="reviewer.name", maximum=200)
    reviewer_organization = _bounded_string(
        reviewer["organization"], field="reviewer.organization", maximum=300
    )
    reviewer_role = _bounded_string(reviewer["role"], field="reviewer.role", maximum=200)
    reviewed_at = _rfc3339(payload["reviewedAt"], field="reviewedAt")

    pages = payload["pages"]
    if not isinstance(pages, list) or not pages:
        raise VisionExtractionError("reviewed-pages pages must be a non-empty array")
    if len(pages) > page_count:
        raise VisionExtractionError("reviewed-pages contains more entries than the PDF")
    reviewed: dict[int, _ReviewedPage] = {}
    for index, item in enumerate(pages):
        if not isinstance(item, dict):
            raise VisionExtractionError(f"reviewed-pages pages[{index}] must be an object")
        _exact_keys(
            item,
            {"page", "imageSha256", "text", "notes"},
            field=f"reviewed-pages pages[{index}]",
        )
        page = item["page"]
        if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= page_count:
            raise VisionExtractionError(f"reviewed-pages pages[{index}].page is out of range")
        if page in reviewed:
            raise VisionExtractionError(f"reviewed-pages contains duplicate page {page}")
        image_sha256 = item["imageSha256"]
        if not isinstance(image_sha256, str) or not _SHA256.fullmatch(image_sha256):
            raise VisionExtractionError(
                f"reviewed-pages page {page} imageSha256 must be lowercase SHA-256"
            )
        if image_sha256 != image_sha256_by_page[page]:
            raise VisionExtractionError(
                f"reviewed-pages page {page} imageSha256 does not match the rendered page"
            )
        text = _bounded_string(
            item["text"],
            field=f"reviewed-pages page {page} text",
            maximum=_MAX_REVIEWED_PAGE_CHARACTERS,
            allow_empty=True,
        )
        notes = _bounded_string(
            item["notes"],
            field=f"reviewed-pages page {page} notes",
            maximum=4000,
            allow_empty=True,
        )
        reviewed[page] = _ReviewedPage(
            page=page,
            image_sha256=image_sha256,
            text=_canonical_page_text(text),
            notes=notes,
        )

    identity = f"{reviewer_name} | {reviewer_organization} | {reviewer_role}"
    return _ReviewBundle(
        reviewed_by=identity,
        reviewed_at=reviewed_at,
        file_sha256=sha256_bytes(raw),
        pages=reviewed,
    )


def _page_image_number(path: Path) -> int:
    match = re.search(r"-(\d+)$", path.stem)
    if not match:
        raise VisionExtractionError(f"unexpected pdftoppm page filename: {path.name}")
    return int(match.group(1))


def _executable(name: str, environment_name: str) -> str:
    value = shutil.which(name) or os.environ.get(environment_name)
    if not value:
        raise VisionExtractionError(
            f"{PIPELINE_NAME} requires {name} on PATH or {environment_name}"
        )
    return value


def _native_pages(path: Path) -> tuple[str, ...]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise VisionExtractionError("pypdf is required to inspect PDF text layers") from error
    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as error:
        raise VisionExtractionError(f"invalid PDF: {path.name}") from error
    pages: list[str] = []
    for _page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
        except Exception:
            raw_text = ""
        pages.append(_canonical_page_text(raw_text))
    return tuple(pages)


def _render_pages(path: Path, output: Path, pdftoppm: str) -> tuple[Path, ...]:
    prefix = output / "page"
    try:
        process = subprocess.run(
            [pdftoppm, "-r", str(_RENDER_DPI), "-png", str(path), str(prefix)],
            check=False,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VisionExtractionError("pdftoppm failed to start or timed out") from error
    if process.returncode != 0:
        diagnostic = normalize_text(process.stderr)[-800:]
        raise VisionExtractionError(f"pdftoppm failed: {diagnostic or 'unknown error'}")
    images = tuple(sorted(output.glob("page-*.png"), key=_page_image_number))
    if not images:
        raise VisionExtractionError("pdftoppm produced no page images")
    expected = list(range(1, len(images) + 1))
    actual = [_page_image_number(image) for image in images]
    if actual != expected:
        raise VisionExtractionError("pdftoppm page numbering is incomplete or duplicated")
    return images


def _parse_tesseract_tsv(value: str) -> _OcrPage:
    reader = csv.DictReader(io.StringIO(value), delimiter="\t")
    required = {"level", "block_num", "par_num", "line_num", "conf", "text"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise VisionExtractionError("tesseract TSV output is missing required columns")
    lines: dict[tuple[str, str, str], list[str]] = {}
    confidence_total = 0.0
    confidence_weight = 0
    for row in reader:
        if row.get("level") != "5":
            continue
        word = normalize_text(row.get("text", ""))
        if not word:
            continue
        key = (row.get("block_num", ""), row.get("par_num", ""), row.get("line_num", ""))
        lines.setdefault(key, []).append(word)
        try:
            confidence = float(row.get("conf", "-1"))
        except ValueError:
            confidence = -1.0
        if 0 <= confidence <= 100:
            weight = max(1, len(compact_text(word)))
            confidence_total += confidence * weight
            confidence_weight += weight
    text = "\n".join(normalize_text(" ".join(words)) for words in lines.values())
    confidence = confidence_total / confidence_weight / 100 if confidence_weight else None
    return _OcrPage(text=text, confidence=confidence)


def _ocr_page(image: Path, tesseract: str, language: str) -> _OcrPage:
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
                "tsv",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VisionExtractionError(f"tesseract failed on {image.name}") from error
    if process.returncode != 0:
        diagnostic = normalize_text(process.stderr)[-800:]
        raise VisionExtractionError(
            f"tesseract failed on {image.name}: {diagnostic or 'unknown error'}"
        )
    return _parse_tesseract_tsv(process.stdout)


def _tool_version(command: list[str]) -> str:
    try:
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    lines = (process.stdout or process.stderr).splitlines()
    return normalize_text(lines[0])[:200] if lines else "unknown"


def _text_risks(text: str, *, prefix: str) -> tuple[str, ...]:
    risks: list[str] = []
    character_count = len(compact_text(text))
    if not text:
        return (f"{prefix}_empty",)
    if character_count < _MIN_PAGE_CHARACTERS:
        risks.append(f"{prefix}_low_character_count")
    han_count = len(_HAN_CHARACTER.findall(text))
    if han_count >= _MIN_PAGE_CHARACTERS:
        interspace_ratio = len(_HAN_INTERSPACE.findall(text)) / han_count
        if interspace_ratio >= 0.15:
            risks.append(f"{prefix}_inter_han_whitespace")
    replacement_ratio = text.count("�") / max(1, len(text))
    if replacement_ratio >= 0.002:
        risks.append(f"{prefix}_replacement_characters")
    if character_count >= _MIN_PAGE_CHARACTERS and han_count / character_count < 0.15:
        risks.append(f"{prefix}_low_han_ratio")
    return tuple(risks)


def _consistency(native_text: str, ocr_text: str) -> float | None:
    native = compact_text(native_text)
    ocr = compact_text(ocr_text)
    if not native or not ocr:
        return None
    return SequenceMatcher(None, native[:20_000], ocr[:20_000], autojunk=False).ratio()


def _page_risk_flags(
    native_text: str,
    ocr_text: str,
    *,
    ocr_confidence: float | None,
) -> tuple[str, ...]:
    risks = list(_text_risks(native_text, prefix="native"))
    risks.extend(_text_risks(ocr_text, prefix="ocr"))
    if ocr_confidence is None or ocr_confidence < _MIN_OCR_CONFIDENCE:
        risks.append("ocr_low_confidence")
    consistency = _consistency(native_text, ocr_text)
    if consistency is not None and consistency < _MIN_NATIVE_OCR_CONSISTENCY:
        risks.append("native_ocr_mismatch")
    return tuple(dict.fromkeys(risks))


def extract_pdf_vision_consensus(
    path: Path,
    *,
    reviewed_pages_path: Path | None = None,
    language: str = _OCR_LANGUAGE,
) -> ExtractionResult:
    """Extract a PDF with native-first, page-evidenced, fail-closed consensus.

    OCR text remains review-required unless a separately authored, source-bound
    reviewed-pages file passes the closed schema and rendered-page hash checks.
    This function never creates review files or marks its own output human-reviewed.
    """

    source_sha256 = sha256_file(path)
    native_pages = _native_pages(path)
    if not native_pages:
        raise VisionExtractionError("PDF contains no pages")
    pdftoppm = _executable("pdftoppm", "DEEPLAW_PDFTOPPM")
    tesseract: str | None = None
    with tempfile.TemporaryDirectory(prefix="deeplaw-vision-consensus-") as directory:
        images = _render_pages(path, Path(directory), pdftoppm)
        if len(images) != len(native_pages):
            raise VisionExtractionError("rendered page count does not match the PDF text layer")
        image_hashes = {
            page: sha256_file(image) for page, image in enumerate(images, start=1)
        }
        review = (
            load_reviewed_pages(
                reviewed_pages_path,
                source_sha256=source_sha256,
                page_count=len(images),
                image_sha256_by_page=image_hashes,
            )
            if reviewed_pages_path is not None
            else None
        )

        blocks: list[TextBlock] = []
        evidence: list[PageExtractionEvidence] = []
        warnings: list[str] = []
        page_pairs = zip(native_pages, images, strict=True)
        for page, (native_text, image) in enumerate(page_pairs, start=1):
            native_risks = _text_risks(native_text, prefix="native")
            native_acceptable = not native_risks
            ocr = _OcrPage(text="", confidence=None)
            if not native_acceptable:
                if tesseract is None:
                    tesseract = _executable("tesseract", "DEEPLAW_TESSERACT")
                ocr = _ocr_page(image, tesseract, language)
            risks = (
                _page_risk_flags(
                    native_text,
                    ocr.text,
                    ocr_confidence=ocr.confidence,
                )
                if not native_acceptable
                else ()
            )
            consistency = _consistency(native_text, ocr.text)
            reviewed_page = review.pages.get(page) if review is not None else None
            if reviewed_page is not None:
                selected_text = reviewed_page.text
                selected_source = "reviewed"
                review_status = "human_reviewed"
                review_required = False
                risks = (*risks, "human_reviewed_override")
                reviewed_by = review.reviewed_by
                reviewed_at = review.reviewed_at
                review_notes = reviewed_page.notes
                review_file_sha256 = review.file_sha256
            elif native_acceptable:
                selected_text = native_text
                selected_source = "native"
                review_status = "not_reviewed"
                review_required = False
                reviewed_by = None
                reviewed_at = None
                review_notes = None
                review_file_sha256 = None
            else:
                selected_text = ocr.text or native_text
                selected_source = "ocr" if ocr.text else ("native" if native_text else "none")
                review_status = "not_reviewed"
                review_required = True
                reviewed_by = None
                reviewed_at = None
                review_notes = None
                review_file_sha256 = None

            for raw_line in selected_text.splitlines():
                if line := normalize_text(raw_line):
                    blocks.append(TextBlock(text=line, page=page))
            for risk in risks:
                warnings.append(f"page {page}: {risk}")
            evidence.append(
                PageExtractionEvidence(
                    page=page,
                    image_sha256=image_hashes[page],
                    native_text_sha256=sha256_bytes(native_text.encode("utf-8")),
                    ocr_text_sha256=(
                        sha256_bytes(ocr.text.encode("utf-8")) if not native_acceptable else None
                    ),
                    selected_text_sha256=sha256_bytes(selected_text.encode("utf-8")),
                    native_character_count=len(compact_text(native_text)),
                    ocr_character_count=len(compact_text(ocr.text)),
                    selected_character_count=len(compact_text(selected_text)),
                    selected_source=selected_source,
                    review_status=review_status,
                    review_required=review_required,
                    ocr_confidence=ocr.confidence,
                    native_ocr_consistency=consistency,
                    risk_flags=tuple(dict.fromkeys(risks)),
                    reviewed_by=reviewed_by,
                    reviewed_at=reviewed_at,
                    review_notes=review_notes,
                    review_file_sha256=review_file_sha256,
                )
            )

    if sha256_file(path) != source_sha256:
        raise VisionExtractionError("PDF changed while vision consensus was running")
    character_count = sum(len(block.text) for block in blocks)
    if character_count < 20:
        raise VisionExtractionError("vision consensus produced too little selected text")
    review_required = any(page.review_required for page in evidence)
    reviewed_page_count = sum(page.review_status == "human_reviewed" for page in evidence)
    tesseract_version = (
        _tool_version([tesseract, "--version"]) if tesseract is not None else "not-used"
    )
    renderer_version = _tool_version([pdftoppm, "-v"])
    return ExtractionResult(
        blocks=tuple(blocks),
        quality=ExtractionQuality(
            extractor=PIPELINE_NAME,
            extractor_version=PIPELINE_VERSION,
            block_count=len(blocks),
            page_count=len(evidence),
            character_count=character_count,
            low_text_pages=sum(
                page.selected_character_count < _MIN_PAGE_CHARACTERS for page in evidence
            ),
            needs_ocr=review_required,
            review_required=review_required,
            source_sha256=source_sha256,
            reviewed_page_count=reviewed_page_count,
            page_evidence=tuple(evidence),
            warnings=tuple(warnings),
            configuration=(
                f"dpi={_RENDER_DPI}",
                f"language={language}",
                f"psm={_OCR_PSM}",
                f"tesseract={tesseract_version}",
                f"renderer={renderer_version}",
            ),
        ),
    )
