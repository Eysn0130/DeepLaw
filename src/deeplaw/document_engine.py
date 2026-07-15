from __future__ import annotations

import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

try:
    import resource as _resource
except ImportError:  # pragma: no cover - resource is unavailable on Windows.
    _resource = None

_ENGINE_ENVIRONMENT = "DEEPLAW_DOCUMENT_ENGINE"
_DEFAULT_EXECUTABLE = "deeplaw-document-engine"
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_OUTPUT_FILES = 20_000
_MAX_JSON_NODES = 500_000
_MAX_JSON_DEPTH = 24
_MAX_STRING_CHARACTERS = 2 * 1024 * 1024
_MAX_BLOCKS = 50_000
_MAX_BLOCK_TEXT_CHARACTERS = 2 * 1024 * 1024
_MAX_TOTAL_TEXT_CHARACTERS = 64 * 1024 * 1024
_MAX_PAGE_RANGE = 5_000
_MAX_PROCESS_CPU_SECONDS = 1800
_MAX_PROCESS_ADDRESS_SPACE_BYTES = 16 * 1024 * 1024 * 1024
_MAX_PROCESS_OPEN_FILES = 256
_OUTPUT_MONITOR_INTERVAL_SECONDS = 0.1


class DocumentEngineError(RuntimeError):
    """Raised when the external document engine violates the adapter contract."""


@dataclass(frozen=True)
class DocumentEngineBlock:
    type: str
    text: str
    page: int
    order: int
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class DocumentEnginePage:
    page: int
    blocks: tuple[DocumentEngineBlock, ...]


@dataclass(frozen=True)
class DocumentEngineResult:
    pages: tuple[DocumentEnginePage, ...]
    engine: str
    engine_version: str
    output_schema: str
    configuration: tuple[str, ...]

    @property
    def blocks(self) -> tuple[DocumentEngineBlock, ...]:
        return tuple(block for page in self.pages for block in page.blocks)


class _BoundedCapture:
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


class _StructuredText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"br", "p", "tr", "li"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"p", "tr", "li"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        return


def _set_posix_resource_limit(kind: int, requested: int) -> None:
    if _resource is None:
        return
    try:
        _current_soft, current_hard = _resource.getrlimit(kind)
        infinity = _resource.RLIM_INFINITY
        effective = requested if current_hard == infinity else min(requested, current_hard)
        if effective > 0:
            _resource.setrlimit(kind, (effective, effective))
    except (OSError, ValueError):
        # Some POSIX platforms expose a limit constant but do not implement it.
        # Wall-clock, pipe, and live-directory limits remain enforced by the parent.
        return


def _posix_resource_limiter(timeout_seconds: float) -> Any | None:
    if os.name != "posix" or _resource is None:
        return None

    cpu_seconds = max(
        1,
        min(math.ceil(timeout_seconds) + 1, _MAX_PROCESS_CPU_SECONDS),
    )

    def apply_limits() -> None:
        limits = (
            (getattr(_resource, "RLIMIT_CPU", None), cpu_seconds),
            (
                getattr(_resource, "RLIMIT_AS", None),
                _MAX_PROCESS_ADDRESS_SPACE_BYTES,
            ),
            (getattr(_resource, "RLIMIT_FSIZE", None), _MAX_OUTPUT_BYTES),
            (getattr(_resource, "RLIMIT_NOFILE", None), _MAX_PROCESS_OPEN_FILES),
        )
        for kind, requested in limits:
            if kind is not None:
                _set_posix_resource_limit(kind, requested)

    return apply_limits


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float,
    capture_limit: int = _MAX_CAPTURE_BYTES,
    output_root: Path | None = None,
) -> tuple[int, bytes]:
    popen_options: dict[str, Any] = {}
    resource_limiter = _posix_resource_limiter(timeout_seconds)
    if resource_limiter is not None:
        popen_options["preexec_fn"] = resource_limiter
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=os.name == "posix",
            **popen_options,
        )
    except OSError as error:
        raise DocumentEngineError(f"document engine failed to start: {error}") from error

    assert process.stdout is not None
    assert process.stderr is not None
    capture = _BoundedCapture(capture_limit)
    readers = [
        threading.Thread(target=capture.drain, args=(process.stdout,), daemon=True),
        threading.Thread(target=capture.drain, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + timeout_seconds
    next_output_check = 0.0
    failure: str | None = None
    while process.poll() is None:
        if capture.exceeded.is_set():
            failure = f"document engine output exceeded {capture_limit} bytes"
            break
        now = time.monotonic()
        if now >= deadline:
            failure = f"document engine timed out after {timeout_seconds:g} seconds"
            break
        if output_root is not None and now >= next_output_check:
            try:
                _check_output_tree(output_root)
            except DocumentEngineError as error:
                failure = str(error)
                break
            next_output_check = now + _OUTPUT_MONITOR_INTERVAL_SECONDS
        time.sleep(0.02)
    if failure is not None:
        _kill_process(process)
    process.wait()
    for reader in readers:
        reader.join(timeout=2)
    if failure is not None:
        raise DocumentEngineError(failure)
    if capture.exceeded.is_set():
        raise DocumentEngineError(f"document engine output exceeded {capture_limit} bytes")
    if output_root is not None:
        _check_output_tree(output_root)
    return process.returncode, bytes(capture.value)


def _discover_engine() -> Path:
    configured = os.environ.get(_ENGINE_ENVIRONMENT)
    if configured is None:
        sibling = Path(sys.executable).with_name(_DEFAULT_EXECUTABLE)
        candidate = str(sibling) if sibling.is_file() else _DEFAULT_EXECUTABLE
    else:
        candidate = configured
    if not candidate.strip():
        raise DocumentEngineError(f"{_ENGINE_ENVIRONMENT} must not be blank")
    executable = candidate if Path(candidate).is_absolute() else shutil.which(candidate)
    if executable is None:
        raise DocumentEngineError(
            f"document engine is not installed or not found; set {_ENGINE_ENVIRONMENT} or install "
            "DeepLaw with the document-engine extra"
        )
    path = Path(executable).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise DocumentEngineError(f"document engine is not an executable file: {path}")
    return path


@lru_cache(maxsize=8)
def _engine_version(executable: Path) -> str:
    return_code, output = _run_bounded(
        [str(executable), "--version"],
        cwd=executable.parent,
        timeout_seconds=90,
        capture_limit=64 * 1024,
    )
    diagnostic = output.decode("utf-8", errors="replace").strip()
    if return_code != 0:
        raise DocumentEngineError(
            f"document engine version check failed with exit code {return_code}: "
            f"{diagnostic[:500] or 'no diagnostic'}"
        )
    first_line = diagnostic.splitlines()[0].strip() if diagnostic else ""
    if not first_line:
        raise DocumentEngineError("document engine returned an invalid version string")
    if len(first_line) <= 160:
        return first_line
    digest = sha256(first_line.encode("utf-8")).hexdigest()
    return f"{first_line[:80]};sha256={digest}"


def _check_output_tree(root: Path) -> None:
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
                raise DocumentEngineError("document engine output must not contain symlinks")
        for name in files:
            path = current / name
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise DocumentEngineError("document engine output must contain regular files only")
            file_count += 1
            byte_count += metadata.st_size
            if file_count > _MAX_OUTPUT_FILES:
                raise DocumentEngineError("document engine produced too many output files")
            if byte_count > _MAX_OUTPUT_BYTES:
                raise DocumentEngineError("document engine output exceeds the size limit")


def _select_content_list(root: Path) -> tuple[Path, str]:
    v2 = sorted(root.rglob("*_content_list_v2.json"))
    legacy = sorted(root.rglob("*_content_list.json"))
    candidates = v2 if v2 else legacy
    schema = "content_list_v2" if v2 else "content_list"
    if not candidates:
        raise DocumentEngineError("document engine produced no structured content list")
    if len(candidates) != 1:
        raise DocumentEngineError(
            f"document engine produced {len(candidates)} {schema} files for one PDF"
        )
    return candidates[0], schema


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DocumentEngineError(f"document engine JSON contains duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise DocumentEngineError(f"document engine JSON contains non-standard number: {value}")


def _validate_json_bounds(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            raise DocumentEngineError("document engine JSON exceeds the node limit")
        if depth > _MAX_JSON_DEPTH:
            raise DocumentEngineError("document engine JSON exceeds the nesting limit")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str) and len(item) > _MAX_STRING_CHARACTERS:
            raise DocumentEngineError("document engine JSON contains an oversized string")


def _load_json(path: Path) -> Any:
    size = path.stat().st_size
    if size <= 0:
        raise DocumentEngineError("document engine content list is empty")
    if size > _MAX_JSON_BYTES:
        raise DocumentEngineError("document engine content list exceeds the JSON size limit")
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_closed_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DocumentEngineError("document engine content list is not valid UTF-8 JSON") from error
    _validate_json_bounds(value)
    return value


def _bounded_type(value: Any, *, block_index: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise DocumentEngineError(f"document engine block {block_index} has an invalid type")
    return value.strip()


def _bbox(value: Any, *, block_index: int) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 4:
        raise DocumentEngineError(f"document engine block {block_index} has an invalid bbox")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise DocumentEngineError(f"document engine block {block_index} has an invalid bbox")
    coordinates = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in coordinates):
        raise DocumentEngineError(f"document engine block {block_index} has an invalid bbox")
    left, top, right, bottom = coordinates
    if right < left or bottom < top:
        raise DocumentEngineError(f"document engine block {block_index} has an invalid bbox")
    return coordinates


def _confidence(item: dict[str, Any], *, block_index: int) -> float | None:
    value = next(
        (item[key] for key in ("confidence", "score", "confidence_score") if key in item),
        None,
    )
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentEngineError(f"document engine block {block_index} has invalid confidence")
    confidence = float(value)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise DocumentEngineError(f"document engine block {block_index} has invalid confidence")
    return confidence


def _plain_html(value: str) -> str:
    parser = _StructuredText()
    try:
        parser.feed(value)
        parser.close()
    except Exception as error:
        raise DocumentEngineError("document engine returned invalid table HTML") from error
    return "".join(parser.parts)


_TEXT_CONTENT_KEYS = frozenset(
    {
        "content",
        "text",
        "text_content",
        "title_content",
        "list_content",
        "list_items",
        "table_caption",
        "table_body",
        "table_footnote",
        "html",
        "code_content",
        "equation",
        "latex",
    }
)
_HTML_CONTENT_KEYS = frozenset({"html", "table_body"})
_GENERATED_VISUAL_BLOCK_TYPES = frozenset(
    {"image", "figure", "image_caption", "image_body", "figure_caption"}
)


def _text_fragments(value: Any, *, field: str | None = None) -> list[str]:
    if isinstance(value, str):
        if field not in _TEXT_CONTENT_KEYS:
            return []
        return [_plain_html(value) if field in _HTML_CONTENT_KEYS and "<" in value else value]
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_text_fragments(item, field=field))
        return values
    if isinstance(value, dict):
        values = []
        for key, item in value.items():
            if key not in _TEXT_CONTENT_KEYS:
                continue
            values.extend(_text_fragments(item, field=key))
        return values
    return []


def _block_text(item: dict[str, Any], *, block_index: int, block_type: str) -> str:
    # Visual-description fields may be generated by a model and are not source
    # text. Keep the block/provenance, but never admit those descriptions into
    # legal evidence as if they came from the document.
    if block_type in _GENERATED_VISUAL_BLOCK_TYPES:
        return ""
    fragments = _text_fragments(item)
    normalized: list[str] = []
    for fragment in fragments:
        text = unicodedata.normalize("NFKC", fragment).replace("\x00", " ")
        text = " ".join(text.split())
        if text:
            normalized.append(text)
    result = "\n".join(normalized)
    if len(result) > _MAX_BLOCK_TEXT_CHARACTERS:
        raise DocumentEngineError(f"document engine block {block_index} text exceeds the limit")
    return result


def _parse_v2(
    value: Any,
    *,
    start_page: int,
    end_page: int,
) -> tuple[DocumentEnginePage, ...]:
    expected_pages = end_page - start_page + 1
    if not isinstance(value, list) or len(value) != expected_pages:
        raise DocumentEngineError(
            "document engine content_list_v2 must contain exactly one array per requested page"
        )
    pages: list[DocumentEnginePage] = []
    block_count = 0
    total_text = 0
    for page_offset, raw_blocks in enumerate(value):
        page = start_page + page_offset
        if not isinstance(raw_blocks, list):
            raise DocumentEngineError(f"document engine page {page} must be an array")
        blocks: list[DocumentEngineBlock] = []
        for order, item in enumerate(raw_blocks, start=1):
            block_count += 1
            if block_count > _MAX_BLOCKS:
                raise DocumentEngineError("document engine content list has too many blocks")
            if not isinstance(item, dict):
                raise DocumentEngineError(f"document engine block {block_count} must be an object")
            block_type = _bounded_type(item.get("type"), block_index=block_count)
            text = _block_text(
                item,
                block_index=block_count,
                block_type=block_type,
            )
            total_text += len(text)
            if total_text > _MAX_TOTAL_TEXT_CHARACTERS:
                raise DocumentEngineError("document engine extracted text exceeds the limit")
            blocks.append(
                DocumentEngineBlock(
                    type=block_type,
                    text=text,
                    page=page,
                    order=order,
                    bbox=_bbox(item.get("bbox"), block_index=block_count),
                    confidence=_confidence(item, block_index=block_count),
                )
            )
        pages.append(DocumentEnginePage(page=page, blocks=tuple(blocks)))
    return tuple(pages)


def _parse_legacy(
    value: Any,
    *,
    start_page: int,
    end_page: int,
) -> tuple[DocumentEnginePage, ...]:
    if not isinstance(value, list):
        raise DocumentEngineError("document engine content_list root must be an array")
    expected_pages = end_page - start_page + 1
    by_page: dict[int, list[DocumentEngineBlock]] = {
        page: [] for page in range(start_page, end_page + 1)
    }
    total_text = 0
    if len(value) > _MAX_BLOCKS:
        raise DocumentEngineError("document engine content list has too many blocks")
    for block_index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise DocumentEngineError(f"document engine block {block_index} must be an object")
        page_index = item.get("page_idx")
        if (
            isinstance(page_index, bool)
            or not isinstance(page_index, int)
            or not 0 <= page_index < expected_pages
        ):
            raise DocumentEngineError(
                f"document engine block {block_index} is outside the requested page range"
            )
        page = start_page + page_index
        block_type = _bounded_type(item.get("type"), block_index=block_index)
        text = _block_text(
            item,
            block_index=block_index,
            block_type=block_type,
        )
        total_text += len(text)
        if total_text > _MAX_TOTAL_TEXT_CHARACTERS:
            raise DocumentEngineError("document engine extracted text exceeds the limit")
        by_page[page].append(
            DocumentEngineBlock(
                type=block_type,
                text=text,
                page=page,
                order=len(by_page[page]) + 1,
                bbox=_bbox(item.get("bbox"), block_index=block_index),
                confidence=_confidence(item, block_index=block_index),
            )
        )
    return tuple(
        DocumentEnginePage(page=page, blocks=tuple(by_page[page]))
        for page in range(start_page, end_page + 1)
    )


def extract_pdf_page_range(
    path: Path,
    *,
    start_page: int,
    end_page: int,
    timeout_seconds: float = 1800,
    method: str = "auto",
    backend: str = "pipeline",
    language: str = "ch",
) -> DocumentEngineResult:
    """Extract a bounded 1-based PDF page range into a structured, non-Markdown IR."""

    if isinstance(start_page, bool) or not isinstance(start_page, int) or start_page < 1:
        raise DocumentEngineError("start_page must be a positive 1-based integer")
    if isinstance(end_page, bool) or not isinstance(end_page, int) or end_page < start_page:
        raise DocumentEngineError("end_page must be an integer not smaller than start_page")
    if end_page - start_page + 1 > _MAX_PAGE_RANGE:
        raise DocumentEngineError(f"requested page range exceeds {_MAX_PAGE_RANGE} pages")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise DocumentEngineError("timeout_seconds must be a positive number")
    if not math.isfinite(float(timeout_seconds)) or timeout_seconds <= 0:
        raise DocumentEngineError("timeout_seconds must be a positive number")
    for field, value, allowed in (
        ("method", method, {"auto", "txt", "ocr"}),
        ("backend", backend, {"pipeline", "vlm-engine", "hybrid-engine"}),
    ):
        if value not in allowed:
            raise DocumentEngineError(f"unsupported {field}: {value}")
    if not language or len(language) > 32 or not language.replace("-", "").isalnum():
        raise DocumentEngineError("language must be a short language identifier")

    source = path.resolve()
    if path.suffix.lower() != ".pdf" or not source.is_file():
        raise DocumentEngineError(f"PDF is not a regular .pdf file: {path}")
    executable = _discover_engine()

    with TemporaryDirectory(prefix="deeplaw-document-engine-") as temporary:
        temporary_root = Path(temporary)
        output_root = temporary_root / "output"
        output_root.mkdir(mode=0o700)
        version = _engine_version(executable)
        command = [
            str(executable),
            "-p",
            str(source),
            "-o",
            str(output_root),
            "-m",
            method,
            "-b",
            backend,
            "-l",
            language,
            "-s",
            str(start_page - 1),
            "-e",
            str(end_page - 1),
        ]
        return_code, output = _run_bounded(
            command,
            cwd=temporary_root,
            timeout_seconds=float(timeout_seconds),
            output_root=output_root,
        )
        if return_code != 0:
            diagnostic = output.decode("utf-8", errors="replace").strip()
            raise DocumentEngineError(
                f"document engine failed with exit code {return_code}: "
                f"{diagnostic[:1000] or 'no diagnostic'}"
            )
        _check_output_tree(output_root)
        content_path, output_schema = _select_content_list(output_root)
        value = _load_json(content_path)
        if output_schema == "content_list_v2":
            pages = _parse_v2(value, start_page=start_page, end_page=end_page)
        else:
            pages = _parse_legacy(value, start_page=start_page, end_page=end_page)

    return DocumentEngineResult(
        pages=pages,
        engine="mineru-compatible-cli",
        engine_version=version,
        output_schema=output_schema,
        configuration=(
            f"method={method}",
            f"backend={backend}",
            f"language={language}",
            f"pages={start_page}-{end_page}",
        ),
    )
