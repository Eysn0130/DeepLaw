from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .document_engine import (
    DocumentEngineBlock,
    DocumentEngineError,
    extract_pdf_page_range,
)
from .models import ExtractionQuality, ExtractionResult, PageExtractionEvidence, TextBlock
from .util import compact_text, normalize_text, sha256_bytes, sha256_file

PIPELINE_NAME = "deeplaw-vision-consensus"
PIPELINE_VERSION = "deeplaw-vision-consensus/v2"
REVIEWED_PAGES_SCHEMA = "deeplaw.reviewed-pages/v1"
EXTRACTION_EVIDENCE_SCHEMA = "deeplaw.extraction-evidence/v1"

_RENDER_DPI = 300
_OCR_LANGUAGE = "chi_sim+eng"
_OCR_PSM = "3"
_MIN_PAGE_CHARACTERS = 80
_MIN_OCR_CONFIDENCE = 0.75
_MIN_NATIVE_OCR_CONSISTENCY = 0.82
_MIN_MACHINE_CONSENSUS = 0.94
_MAX_REVIEW_FILE_BYTES = 8 * 1024 * 1024
_MAX_REVIEWED_PAGE_CHARACTERS = 2 * 1024 * 1024
_MAX_PDF_PAGES = 500
_MAX_RENDER_PAGE_PIXELS = 100_000_000
_MAX_RENDER_TOTAL_PIXELS = 2_000_000_000
_RENDER_BATCH_PAGES = 4
_MAX_RENDER_PAGE_BYTES = 128 * 1024 * 1024
_MAX_RENDER_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_RENDER_STDOUT_BYTES = 64 * 1024
_MAX_RENDER_STDERR_BYTES = 1024 * 1024
_MAX_OCR_STDOUT_BYTES = 64 * 1024 * 1024
_MAX_OCR_STDERR_BYTES = 1024 * 1024
_RENDER_BATCH_TIMEOUT_SECONDS = 180
_RENDER_TOTAL_TIMEOUT_SECONDS = 1800
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HAN_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_HAN_INTERSPACE = re.compile(r"(?<=[\u3400-\u4dbf\u4e00-\u9fff])\s(?=[\u3400-\u4dbf\u4e00-\u9fff])")
_CRITICAL_TOKEN = re.compile(
    r"第[〇零一二两三四五六七八九十百千万亿0-9]+(?:编|章|节|条|款|项)|"
    r"[0-9]+(?:\.[0-9]+)?(?:年|月|日|元|万元|亿元|%|\uFF05)?|"
    r"不得|不予|不应|不能|禁止|应当|必须|可以|有权|无权|"
    r"以上|以下|以内|不满|超过|不足|达到|或者|并且|以及|"
    r"除外|除非|但是|但|未|无"
)
_SEMANTIC_TOKEN = re.compile(r"[0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff]+")
_LEGAL_PUNCTUATION_TOKEN = re.compile(
    r"[\uFF0C\u3002\uFF1B\uFF1A\u3001\uFF01\uFF1F,.;:!?"
    r"\uFF08\uFF09()\u300A\u300B\u3008\u3009\u3010\u3011\[\]"
    r"\u201C\u201D\u2018\u2019\"']"
)
_TABLE_BLOCK_TYPES = frozenset({"table", "table_row", "table_cell"})


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


@dataclass(frozen=True)
class _DocumentEngineCandidate:
    blocks: tuple[DocumentEngineBlock, ...]
    engine: str
    engine_version: str
    output_schema: str
    method: str
    backend: str
    language: str


@dataclass(frozen=True)
class _BoundedProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class _BoundedStreamCapture:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.value = bytearray()
        self.exceeded = threading.Event()
        self._lock = threading.Lock()

    def drain(self, stream: Any) -> None:
        try:
            while chunk := stream.read(64 * 1024):
                with self._lock:
                    remaining = self.maximum - len(self.value)
                    if remaining > 0:
                        self.value.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        self.exceeded.set()
        finally:
            stream.close()


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
        raise VisionExtractionError("reviewed-pages attestation must be 'visual_page_comparison'")

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


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return


def _render_output_usage(root: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    for directory, directories, files in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in directories:
            try:
                mode = (current / name).lstat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(mode):
                raise VisionExtractionError("renderer output must not contain symlinks")
        for name in files:
            path = current / name
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise VisionExtractionError("renderer output must contain regular files only")
            file_count += 1
            byte_count += metadata.st_size
            if metadata.st_size > _MAX_RENDER_PAGE_BYTES:
                raise VisionExtractionError("renderer produced an oversized page image")
    return file_count, byte_count


def _run_bounded_pdf_subprocess(
    command: list[str],
    *,
    timeout_seconds: float,
    stdout_limit: int,
    stderr_limit: int,
    output_root: Path | None = None,
    output_byte_limit: int | None = None,
) -> _BoundedProcessResult:
    """Run a PDF helper with bounded pipes, time, and optional live output budget."""

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name == "posix",
        )
    except OSError as error:
        raise VisionExtractionError(f"PDF helper failed to start: {error}") from error
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = _BoundedStreamCapture(stdout_limit)
    stderr = _BoundedStreamCapture(stderr_limit)
    readers = [
        threading.Thread(target=stdout.drain, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr.drain, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout_seconds
    next_output_check = 0.0
    failure: str | None = None
    while process.poll() is None:
        if stdout.exceeded.is_set() or stderr.exceeded.is_set():
            failure = "PDF helper diagnostic output exceeded the limit"
            break
        now = time.monotonic()
        if now >= deadline:
            failure = f"PDF helper timed out after {timeout_seconds:g} seconds"
            break
        if output_root is not None and now >= next_output_check:
            try:
                _file_count, output_bytes = _render_output_usage(output_root)
            except VisionExtractionError as error:
                failure = str(error)
                break
            if output_byte_limit is not None and output_bytes > output_byte_limit:
                failure = "renderer output exceeded the total byte limit"
                break
            next_output_check = now + 0.05
        time.sleep(0.02)
    if failure is not None:
        _kill_process_tree(process)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        process.wait()
    for reader in readers:
        reader.join(timeout=2)
    if failure is not None:
        raise VisionExtractionError(failure)
    if stdout.exceeded.is_set() or stderr.exceeded.is_set():
        raise VisionExtractionError("PDF helper diagnostic output exceeded the limit")
    if output_root is not None:
        _file_count, output_bytes = _render_output_usage(output_root)
        if output_byte_limit is not None and output_bytes > output_byte_limit:
            raise VisionExtractionError("renderer output exceeded the total byte limit")
    return _BoundedProcessResult(
        returncode=process.returncode,
        stdout=bytes(stdout.value),
        stderr=bytes(stderr.value),
    )


def _executable(name: str, environment_name: str) -> str:
    value = shutil.which(name) or os.environ.get(environment_name)
    if not value:
        raise VisionExtractionError(
            f"{PIPELINE_NAME} requires {name} on PATH or {environment_name}"
        )
    return value


def validate_pdf_render_budget(pages: Any) -> None:
    """Reject PDFs whose declared page geometry would exceed render budgets."""

    try:
        page_count = len(pages)
    except Exception as error:
        raise VisionExtractionError("PDF page count could not be inspected") from error
    if page_count > _MAX_PDF_PAGES:
        raise VisionExtractionError(f"PDF exceeds the {_MAX_PDF_PAGES}-page render limit")
    total_pixels = 0
    for page_number, page in enumerate(pages, start=1):
        try:
            width_points = float(page.mediabox.width)
            height_points = float(page.mediabox.height)
            user_unit = float(page.get("/UserUnit", 1))
        except Exception as error:
            raise VisionExtractionError(
                f"PDF page {page_number} has an invalid MediaBox"
            ) from error
        dimensions = (width_points, height_points, user_unit)
        if not all(math.isfinite(value) and value > 0 for value in dimensions):
            raise VisionExtractionError(f"PDF page {page_number} has an invalid MediaBox")
        width_pixels = math.ceil(width_points * user_unit * _RENDER_DPI / 72)
        height_pixels = math.ceil(height_points * user_unit * _RENDER_DPI / 72)
        page_pixels = width_pixels * height_pixels
        if page_pixels > _MAX_RENDER_PAGE_PIXELS:
            raise VisionExtractionError(f"PDF page {page_number} exceeds the render pixel limit")
        total_pixels += page_pixels
        if total_pixels > _MAX_RENDER_TOTAL_PIXELS:
            raise VisionExtractionError("PDF exceeds the total render pixel limit")


def _native_pages(path: Path) -> tuple[str, ...]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise VisionExtractionError("pypdf is required to inspect PDF text layers") from error
    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as error:
        raise VisionExtractionError(f"invalid PDF: {path.name}") from error
    validate_pdf_render_budget(reader.pages)
    pages: list[str] = []
    for _page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text(extraction_mode="layout") or page.extract_text() or ""
        except Exception:
            raw_text = ""
        pages.append(_canonical_page_text(raw_text))
    return tuple(pages)


def _render_pages(
    path: Path,
    output: Path,
    pdftoppm: str,
    *,
    page_count: int,
) -> tuple[Path, ...]:
    if page_count < 1 or page_count > _MAX_PDF_PAGES:
        raise VisionExtractionError("PDF page count is outside the render limit")
    total_bytes = 0
    deadline = time.monotonic() + _RENDER_TOTAL_TIMEOUT_SECONDS
    images: list[Path] = []
    for start_page in range(1, page_count + 1, _RENDER_BATCH_PAGES):
        end_page = min(page_count, start_page + _RENDER_BATCH_PAGES - 1)
        batch_root = output / f".render-{start_page}-{end_page}"
        batch_root.mkdir(mode=0o700)
        prefix = batch_root / "page"
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            raise VisionExtractionError("pdftoppm exceeded the total render timeout")
        process = _run_bounded_pdf_subprocess(
            [
                pdftoppm,
                "-f",
                str(start_page),
                "-l",
                str(end_page),
                "-r",
                str(_RENDER_DPI),
                "-png",
                str(path),
                str(prefix),
            ],
            timeout_seconds=min(_RENDER_BATCH_TIMEOUT_SECONDS, remaining_seconds),
            stdout_limit=_MAX_RENDER_STDOUT_BYTES,
            stderr_limit=_MAX_RENDER_STDERR_BYTES,
            output_root=batch_root,
            output_byte_limit=_MAX_RENDER_TOTAL_BYTES - total_bytes,
        )
        if process.returncode != 0:
            diagnostic = normalize_text(process.stderr.decode("utf-8", errors="replace"))[-800:]
            raise VisionExtractionError(f"pdftoppm failed: {diagnostic or 'unknown error'}")
        batch_images = tuple(sorted(batch_root.glob("page-*.png"), key=_page_image_number))
        expected_count = end_page - start_page + 1
        if len(batch_images) != expected_count:
            raise VisionExtractionError("pdftoppm produced an incomplete render batch")
        for page_number, image in zip(range(start_page, end_page + 1), batch_images, strict=True):
            metadata = image.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                raise VisionExtractionError("renderer output must contain regular files only")
            if metadata.st_size > _MAX_RENDER_PAGE_BYTES:
                raise VisionExtractionError("renderer produced an oversized page image")
            total_bytes += metadata.st_size
            if total_bytes > _MAX_RENDER_TOTAL_BYTES:
                raise VisionExtractionError("renderer output exceeded the total byte limit")
            destination = output / f"page-{page_number}.png"
            image.replace(destination)
            images.append(destination)
        try:
            batch_root.rmdir()
        except OSError as error:
            raise VisionExtractionError("pdftoppm produced unexpected output files") from error
    return tuple(images)


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
    process = _run_bounded_pdf_subprocess(
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
        timeout_seconds=180,
        stdout_limit=_MAX_OCR_STDOUT_BYTES,
        stderr_limit=_MAX_OCR_STDERR_BYTES,
    )
    if process.returncode != 0:
        diagnostic = normalize_text(process.stderr.decode("utf-8", errors="replace"))[-800:]
        raise VisionExtractionError(
            f"tesseract failed on {image.name}: {diagnostic or 'unknown error'}"
        )
    try:
        tsv = process.stdout.decode("utf-8")
    except UnicodeDecodeError as error:
        raise VisionExtractionError(f"tesseract returned invalid UTF-8 on {image.name}") from error
    return _parse_tesseract_tsv(tsv)


def _tool_version(command: list[str]) -> str:
    try:
        process = _run_bounded_pdf_subprocess(
            command,
            timeout_seconds=30,
            stdout_limit=64 * 1024,
            stderr_limit=64 * 1024,
        )
    except VisionExtractionError:
        return "unknown"
    value = (process.stdout or process.stderr).decode("utf-8", errors="replace")
    lines = value.splitlines()
    if not lines:
        return "unknown"
    version = normalize_text(lines[0])
    if len(version) <= 160:
        return version
    digest = sha256_bytes(version.encode("utf-8"))
    return f"{version[:80]};sha256={digest}"


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


def _critical_tokens(text: str) -> tuple[str, ...]:
    return tuple(_CRITICAL_TOKEN.findall(compact_text(text)))


def _semantic_tokens(text: str) -> tuple[str, ...]:
    """Ignore layout punctuation while preserving every lexical source token."""

    return tuple(_SEMANTIC_TOKEN.findall(unicodedata.normalize("NFKC", text)))


def _legal_punctuation_tokens(text: str) -> tuple[str, ...]:
    """Preserve punctuation that can change legal scope, grouping, or enumeration."""

    return tuple(_LEGAL_PUNCTUATION_TOKEN.findall(unicodedata.normalize("NFKC", text)))


def _contiguous_ranges(pages: list[int]) -> tuple[tuple[int, int], ...]:
    if not pages:
        return ()
    ranges: list[tuple[int, int]] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page != previous + 1:
            ranges.append((start, previous))
            start = page
        previous = page
    ranges.append((start, previous))
    return tuple(ranges)


def _bounded_range_summary(ranges: list[tuple[int, int]]) -> str:
    rendered = ",".join(str(start) if start == end else f"{start}-{end}" for start, end in ranges)
    if len(rendered) <= 700:
        return rendered
    digest = sha256_bytes(rendered.encode("utf-8"))
    return f"{rendered[:700]}...;sha256={digest}"


def _document_engine_candidates(
    path: Path,
    native_pages: tuple[str, ...],
) -> tuple[
    dict[int, _DocumentEngineCandidate],
    tuple[str, ...],
    tuple[str, ...],
]:
    risky_pages = [
        page
        for page, text in enumerate(native_pages, start=1)
        if _text_risks(text, prefix="native")
    ]
    candidates_by_page: dict[int, _DocumentEngineCandidate] = {}
    engines: set[str] = set()
    engine_versions: set[str] = set()
    schemas: set[str] = set()
    methods: set[str] = set()
    backends: set[str] = set()
    languages: set[str] = set()
    successful_range_count = 0
    unavailable_ranges: list[tuple[int, int]] = []
    for start_page, end_page in _contiguous_ranges(risky_pages):
        result = None
        for method in ("auto", "ocr"):
            try:
                result = extract_pdf_page_range(
                    path,
                    start_page=start_page,
                    end_page=end_page,
                    method=method,
                )
                break
            except DocumentEngineError:
                continue
        if result is None:
            unavailable_ranges.append((start_page, end_page))
            continue
        successful_range_count += 1
        engines.add(result.engine)
        engine_versions.add(result.engine_version)
        schemas.add(result.output_schema)
        run_configuration: dict[str, str] = {}
        for item in result.configuration:
            key, separator, value = item.partition("=")
            if not separator:
                continue
            run_configuration[key] = value
            if key == "method":
                methods.add(value)
            elif key == "backend":
                backends.add(value)
            elif key == "language":
                languages.add(value)
        for page in result.pages:
            candidates_by_page[page.page] = _DocumentEngineCandidate(
                blocks=page.blocks,
                engine=result.engine,
                engine_version=result.engine_version,
                output_schema=result.output_schema,
                method=run_configuration["method"],
                backend=run_configuration["backend"],
                language=run_configuration["language"],
            )
    configuration: tuple[str, ...] = ()
    if unavailable_ranges:
        raise VisionExtractionError(
            "document engine failed for one or more requested risk-page ranges: "
            f"pages={_bounded_range_summary(unavailable_ranges)}"
        )
    if successful_range_count:
        engine = ",".join(sorted(engines))
        version = ",".join(sorted(engine_versions))
        run = ";".join(
            (
                f"schemas={','.join(sorted(schemas))}",
                f"methods={','.join(sorted(methods))}",
                f"backends={','.join(sorted(backends))}",
                f"languages={','.join(sorted(languages))}",
                f"ranges={successful_range_count}",
            )
        )
        configuration = (
            f"document_engine={engine}",
            f"document_engine_version={version}",
            f"document_engine_run={run}",
        )
    warnings: tuple[str, ...] = ()
    return candidates_by_page, configuration, warnings


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


def _select_unresolved_candidate(
    *,
    ocr_text: str,
    document_engine_text: str,
    document_engine_risks: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """Choose review-required text without allowing an obvious truncation to win."""

    if not document_engine_text:
        return "ocr", ()
    if not ocr_text:
        return "document_engine", ()
    ocr_count = len(compact_text(ocr_text))
    document_engine_count = len(compact_text(document_engine_text))
    document_engine_is_short = "document_engine_low_character_count" in document_engine_risks
    clearly_more_complete = (
        ocr_count >= _MIN_PAGE_CHARACTERS
        and ocr_count - document_engine_count >= _MIN_PAGE_CHARACTERS // 2
        and ocr_count >= max(document_engine_count * 2, 1)
    )
    severely_truncated = (
        ocr_count - document_engine_count >= _MIN_PAGE_CHARACTERS
        and ocr_count >= max(document_engine_count * 3, 1)
    )
    if (document_engine_is_short and clearly_more_complete) or severely_truncated:
        return "ocr", ("document_engine_candidate_rejected_incomplete",)
    return "document_engine", ()


def extract_pdf_vision_consensus(
    path: Path,
    *,
    reviewed_pages_path: Path | None = None,
    language: str = _OCR_LANGUAGE,
    use_document_engine: bool = False,
) -> ExtractionResult:
    """Extract a PDF with native-first, page-evidenced, fail-closed consensus.

    Risk-page text remains review-required unless two independent candidates pass
    the closed machine-consensus gates or a separately authored, source-bound
    reviewed-pages file passes the schema and rendered-page hash checks. This
    function never creates review files or marks its own output human-reviewed.
    """

    source_sha256 = sha256_file(path)
    native_pages = _native_pages(path)
    if not native_pages:
        raise VisionExtractionError("PDF contains no pages")
    if use_document_engine:
        advanced_candidates_by_page, advanced_configuration, advanced_warnings = (
            _document_engine_candidates(path, native_pages)
        )
    else:
        advanced_candidates_by_page = {}
        advanced_configuration = ()
        advanced_warnings = ()
    pdftoppm = _executable("pdftoppm", "DEEPLAW_PDFTOPPM")
    tesseract: str | None = None
    with tempfile.TemporaryDirectory(prefix="deeplaw-vision-consensus-") as directory:
        images = _render_pages(
            path,
            Path(directory),
            pdftoppm,
            page_count=len(native_pages),
        )
        if len(images) != len(native_pages):
            raise VisionExtractionError("rendered page count does not match the PDF text layer")
        image_hashes = {page: sha256_file(image) for page, image in enumerate(images, start=1)}
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
        warnings: list[str] = list(advanced_warnings)
        page_pairs = zip(native_pages, images, strict=True)
        for page, (native_text, image) in enumerate(page_pairs, start=1):
            native_risks = _text_risks(native_text, prefix="native")
            native_acceptable = not native_risks
            ocr = _OcrPage(text="", confidence=None)
            if not native_acceptable:
                if tesseract is None:
                    tesseract = _executable("tesseract", "DEEPLAW_TESSERACT")
                ocr = _ocr_page(image, tesseract, language)
            advanced_candidate = advanced_candidates_by_page.get(page)
            advanced_blocks = advanced_candidate.blocks if advanced_candidate else ()
            advanced_text = _canonical_page_text("\n".join(block.text for block in advanced_blocks))
            advanced_risks = (
                _text_risks(advanced_text, prefix="document_engine") if advanced_blocks else ()
            )
            if any(block.type in _TABLE_BLOCK_TYPES for block in advanced_blocks):
                advanced_risks = (*advanced_risks, "document_engine_table_requires_review")
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
            ocr_document_engine_consistency = _consistency(ocr.text, advanced_text)
            critical_tokens_match = (
                _critical_tokens(ocr.text) == _critical_tokens(advanced_text)
                and _legal_punctuation_tokens(ocr.text) == _legal_punctuation_tokens(advanced_text)
                if ocr.text and advanced_text
                else None
            )
            semantic_tokens_match = (
                _semantic_tokens(ocr.text) == _semantic_tokens(advanced_text)
                if ocr.text and advanced_text
                else None
            )
            machine_consensus = bool(
                advanced_text
                and ocr.text
                and not advanced_risks
                and ocr.confidence is not None
                and ocr.confidence >= _MIN_OCR_CONFIDENCE
                and ocr_document_engine_consistency is not None
                and ocr_document_engine_consistency >= _MIN_MACHINE_CONSENSUS
                and critical_tokens_match
                and semantic_tokens_match
            )
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
            elif machine_consensus:
                selected_text = advanced_text
                selected_source = "machine_consensus"
                review_status = "not_reviewed"
                review_required = False
                risks = (*risks, *advanced_risks, "machine_consensus_admitted")
                reviewed_by = None
                reviewed_at = None
                review_notes = None
                review_file_sha256 = None
            elif advanced_text:
                selected_source, selection_risks = _select_unresolved_candidate(
                    ocr_text=ocr.text,
                    document_engine_text=advanced_text,
                    document_engine_risks=advanced_risks,
                )
                selected_text = ocr.text if selected_source == "ocr" else advanced_text
                review_status = "not_reviewed"
                review_required = True
                risks = (
                    *risks,
                    *advanced_risks,
                    "machine_consensus_unresolved",
                    *selection_risks,
                )
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

            selected_advanced_blocks = (
                advanced_blocks
                if selected_source in {"document_engine", "machine_consensus"}
                else ()
            )
            selected_lines = (
                tuple(
                    (
                        block.text,
                        block.type,
                        block.bbox,
                        block.confidence,
                    )
                    for block in selected_advanced_blocks
                )
                if selected_advanced_blocks
                else tuple((line, "text_line", None, None) for line in selected_text.splitlines())
            )
            for raw_line, block_kind, bbox, block_confidence in selected_lines:
                if line := normalize_text(raw_line):
                    blocks.append(
                        TextBlock(
                            text=line,
                            page=page,
                            kind=block_kind,
                            bbox=bbox,
                            source=selected_source,
                            confidence=(
                                block_confidence
                                if selected_advanced_blocks
                                else (ocr.confidence if selected_source == "ocr" else None)
                            ),
                            review_required=review_required,
                            risk_flags=tuple(dict.fromkeys(risks)),
                        )
                    )
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
                    document_engine_text_sha256=(
                        sha256_bytes(advanced_text.encode("utf-8")) if advanced_text else None
                    ),
                    document_engine_character_count=len(compact_text(advanced_text)),
                    document_engine_name=(
                        advanced_candidate.engine if advanced_candidate else None
                    ),
                    document_engine_version=(
                        advanced_candidate.engine_version if advanced_candidate else None
                    ),
                    document_engine_schema=(
                        advanced_candidate.output_schema if advanced_candidate else None
                    ),
                    document_engine_method=(
                        advanced_candidate.method if advanced_candidate else None
                    ),
                    document_engine_backend=(
                        advanced_candidate.backend if advanced_candidate else None
                    ),
                    document_engine_language=(
                        advanced_candidate.language if advanced_candidate else None
                    ),
                    ocr_document_engine_consistency=ocr_document_engine_consistency,
                    critical_tokens_match=critical_tokens_match,
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
    risk_pages: dict[str, list[int]] = {}
    for page in evidence:
        for risk in page.risk_flags:
            risk_pages.setdefault(risk, []).append(page.page)
    warnings.extend(
        f"risk={risk};pages={_bounded_range_summary(list(_contiguous_ranges(pages)))}"
        for risk, pages in sorted(risk_pages.items())
    )
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
                *advanced_configuration,
            ),
        ),
    )
