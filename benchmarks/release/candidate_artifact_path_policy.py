"""Sanitize and validate Candidate Full retained text and JUnit evidence.

The Candidate Full workflow may retain test output produced under a runner-local
checkout.  This module removes only the exact checkout-root prefix from
non-identity JUnit content and then applies the existing Formal evidence path
and secret-marker policy.  It never scans wheel or sdist bodies.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from benchmarks.release.qualification_artifact_safety import (
    ABSOLUTE_PATH_RE as _ABSOLUTE_PATH_RE,
)
from benchmarks.release.qualification_artifact_safety import (
    FORBIDDEN_FILENAME_RE as _FORBIDDEN_NAME,
)
from benchmarks.release.qualification_artifact_safety import (
    MAX_SOURCE_BYTES,
)
from benchmarks.release.qualification_artifact_safety import (
    SECRET_MARKER_RE as _SECRET_RE,
)

_XML_NAME_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_.:-]*")
_UNSAFE_XML_DECLARATION_RE = re.compile(
    br"<!\s*(?:DOCTYPE|ENTITY)\b",
    re.IGNORECASE,
)
_TEXT_SUFFIXES = frozenset({".txt"})


class CandidateArtifactPathPolicyError(ValueError):
    """Raised when retained Candidate Full evidence is unsafe or malformed."""


@dataclass(frozen=True)
class NormalizationResult:
    """Stable byte and replacement facts from one JUnit normalization."""

    input_bytes: int
    output_bytes: int
    input_sha256: str
    output_sha256: str
    replacements: int


@dataclass(frozen=True)
class ValidationResult:
    """Bounded facts from validating one retained evidence tree."""

    files_scanned: int
    bytes_scanned: int
    requirements_files: int


def _label(path: Path) -> str:
    return path.name or "<unnamed>"


def _error(label: str, message: str) -> None:
    raise CandidateArtifactPathPolicyError(f"{label}: {message}")


def _regular_file(path: Path, *, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CandidateArtifactPathPolicyError(f"{label}: source is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        _error(label, "source is not a regular non-symlink file")
    return path


def _directory(path: Path, *, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CandidateArtifactPathPolicyError(f"{label}: directory is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        _error(label, "directory is not a regular non-symlink directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CandidateArtifactPathPolicyError(f"{label}: directory is unavailable") from exc
    if resolved == Path(resolved.anchor):
        _error(label, "directory is too broad")
    return resolved


def _checkout_root(value: str | os.PathLike[str]) -> tuple[Path, tuple[bytes, ...]]:
    try:
        text = os.fspath(value)
    except TypeError as exc:
        raise CandidateArtifactPathPolicyError("checkout root: value is invalid") from exc
    if not isinstance(text, str) or not text.strip():
        _error("checkout root", "value is missing or empty")
    supplied = Path(text)
    if not supplied.is_absolute():
        _error("checkout root", "value must be absolute")
    root = _directory(supplied, label="checkout root")
    spellings = {text, str(supplied), supplied.as_posix(), str(root), root.as_posix()}
    if os.name == "nt":
        spellings.update(item.replace("\\", "/") for item in tuple(spellings))
        spellings.update(item.replace("/", "\\") for item in tuple(spellings))
    prefixes: set[bytes] = set()
    for spelling in spellings:
        trimmed = spelling.rstrip("/\\")
        if not trimmed:
            continue
        for separator in ("/", "\\"):
            prefixes.add(os.fsencode(trimmed + separator))
        prefixes.add(os.fsencode(trimmed))
    if not prefixes:
        _error("checkout root", "value is invalid")
    return root, tuple(sorted(prefixes, key=len, reverse=True))


def _read(path: Path, *, label: str) -> bytes:
    _regular_file(path, label=label)
    try:
        size = path.stat().st_size
        if size <= 0:
            _error(label, "file is empty")
        if size > MAX_SOURCE_BYTES:
            _error(label, "file is too large")
        raw = path.read_bytes()
    except CandidateArtifactPathPolicyError:
        raise
    except OSError as exc:
        raise CandidateArtifactPathPolicyError(f"{label}: file could not be read") from exc
    if len(raw) != size:
        _error(label, "file size changed while reading")
    return raw


def _strict_text(raw: bytes, *, label: str) -> str:
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise CandidateArtifactPathPolicyError(f"{label}: evidence is not strict UTF-8") from exc


def _validate_text(raw: bytes, *, label: str) -> None:
    text = _strict_text(raw, label=label)
    if _ABSOLUTE_PATH_RE.search(text) or _SECRET_RE.search(text):
        _error(label, "contains a secret marker or local absolute path")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_start_tags(raw: bytes):
    index = 0
    length = len(raw)
    while index < length:
        start = raw.find(b"<", index)
        if start < 0 or start + 1 >= length:
            return
        if raw.startswith(b"<!--", start):
            end = raw.find(b"-->", start + 4)
            if end < 0:
                return
            index = end + 3
            continue
        if raw.startswith(b"<![CDATA[", start):
            end = raw.find(b"]]>", start + 9)
            if end < 0:
                return
            index = end + 3
            continue
        if raw[start + 1 : start + 2] in {b"/", b"?", b"!"}:
            index = start + 1
            continue
        name_match = _XML_NAME_RE.match(raw, start + 1)
        if name_match is None:
            index = start + 1
            continue
        quote: int | None = None
        end = name_match.end()
        while end < length:
            byte = raw[end]
            if quote is None and byte in {ord("'"), ord('"')}:
                quote = byte
            elif quote is not None and byte == quote:
                quote = None
            elif quote is None and byte == ord(">"):
                break
            end += 1
        if end >= length:
            return
        yield start, name_match.group(), end
        index = end + 1


def _iter_attributes(raw: bytes, start: int, name: bytes, end: int):
    index = start + 1 + len(name)
    while index < end:
        while index < end and raw[index] in b" \t\r\n/":
            index += 1
        name_match = _XML_NAME_RE.match(raw, index, end)
        if name_match is None:
            return
        attribute = name_match.group()
        index = name_match.end()
        while index < end and raw[index] in b" \t\r\n":
            index += 1
        if index >= end or raw[index] != ord("="):
            return
        index += 1
        while index < end and raw[index] in b" \t\r\n":
            index += 1
        if index >= end or raw[index] not in {ord("'"), ord('"')}:
            return
        quote = raw[index]
        value_start = index + 1
        value_end = raw.find(bytes((quote,)), value_start, end)
        if value_end < 0:
            return
        yield attribute, value_start, value_end
        index = value_end + 1


def _identity_spans(raw: bytes) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for start, name, end in _iter_start_tags(raw):
        if name.rsplit(b":", 1)[-1] != b"testcase":
            continue
        for attribute, value_start, value_end in _iter_attributes(raw, start, name, end):
            if attribute in {b"classname", b"name"}:
                spans.append((value_start, value_end))
    return tuple(spans)


def _overlaps_identity(start: int, end: int, spans: tuple[tuple[int, int], ...]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _prefix_boundary(raw: bytes, position: int) -> bool:
    if position == 0:
        return True
    previous = raw[position - 1]
    return not (chr(previous).isalnum() or previous in b"_:/\\")


def _suffix_boundary(raw: bytes, position: int) -> bool:
    if position >= len(raw):
        return True
    following = raw[position]
    return not (chr(following).isalnum() or following in b"_.-")


def _replacement_spans(
    raw: bytes,
    prefixes: tuple[bytes, ...],
    identity_spans: tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    matches: list[tuple[int, int]] = []
    for prefix in prefixes:
        offset = 0
        while True:
            position = raw.find(prefix, offset)
            if position < 0:
                break
            end = position + len(prefix)
            offset = position + 1
            if not _prefix_boundary(raw, position) or _overlaps_identity(
                position, end, identity_spans
            ):
                continue
            if not prefix.endswith((b"/", b"\\")) and not _suffix_boundary(raw, end):
                continue
            matches.append((position, end))
    selected: list[tuple[int, int]] = []
    for start, end in sorted(matches, key=lambda span: (span[0], -(span[1] - span[0]))):
        if selected and start < selected[-1][1]:
            continue
        selected.append((start, end))
    return tuple(selected)


def _replace_spans(raw: bytes, spans: tuple[tuple[int, int], ...]) -> bytes:
    if not spans:
        return raw
    pieces: list[bytes] = []
    previous = 0
    for start, end in spans:
        pieces.append(raw[previous:start])
        previous = end
    pieces.append(raw[previous:])
    return b"".join(pieces)


def _parse_xml(raw: bytes, *, label: str):
    if _UNSAFE_XML_DECLARATION_RE.search(raw):
        _error(label, "XML contains a forbidden DTD or entity declaration")
    try:
        return ET.fromstring(raw)
    except Exception as exc:
        raise CandidateArtifactPathPolicyError(f"{label}: XML is invalid or unsafe") from exc


def _junit_shape(raw: bytes, *, label: str, require_testcases: bool = True):
    root = _parse_xml(raw, label=label)
    testcases = [
        element for element in root.iter() if _local_name(element.tag) == "testcase"
    ]
    if require_testcases and not testcases:
        _error(label, "contains no testcase elements")
    for index, testcase in enumerate(testcases):
        if not testcase.attrib.get("classname") or not testcase.attrib.get("name"):
            _error(label, f"testcase[{index}] lacks identity")
    return root


def _validate_junit_bytes(raw: bytes, *, label: str, require_testcases: bool = True) -> None:
    root = _junit_shape(raw, label=label, require_testcases=require_testcases)
    masked = bytearray(raw)
    for start, end in _identity_spans(raw):
        masked[start:end] = b"x" * (end - start)
    _validate_text(bytes(masked), label=label)
    for element in root.iter():
        element_name = _local_name(element.tag)
        for key, value in element.attrib.items():
            if element_name == "testcase" and key in {"classname", "name"}:
                continue
            _validate_text(value.encode("utf-8"), label=label)
        if element.text:
            _validate_text(element.text.encode("utf-8"), label=label)
        if element.tail:
            _validate_text(element.tail.encode("utf-8"), label=label)


def validate_junit(path: Path, *, label: str | None = None) -> None:
    """Validate one JUnit file using the current Formal path/secret policy."""

    source_label = label or _label(path)
    raw = _read(path, label=source_label)
    _validate_junit_bytes(raw, label=source_label)


def _write_atomic(path: Path, raw: bytes, *, label: str) -> None:
    if path.exists() or path.is_symlink():
        _regular_file(path, label=label)
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        _error(label, "output directory is unavailable")
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise CandidateArtifactPathPolicyError(f"{label}: output could not be written") from exc
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()


def normalize_junit(
    input_path: Path,
    output_path: Path,
    checkout_root: str | os.PathLike[str],
) -> NormalizationResult:
    """Remove only the exact checkout-root prefix from non-identity JUnit bytes."""

    source_label = _label(input_path)
    raw = _read(input_path, label=source_label)
    _, prefixes = _checkout_root(checkout_root)
    _junit_shape(raw, label=source_label)
    spans = _replacement_spans(raw, prefixes, _identity_spans(raw))
    normalized = _replace_spans(raw, spans)
    _validate_junit_bytes(normalized, label=source_label)
    _write_atomic(output_path, normalized, label=_label(output_path))
    return NormalizationResult(
        input_bytes=len(raw),
        output_bytes=len(normalized),
        input_sha256=hashlib.sha256(raw).hexdigest(),
        output_sha256=hashlib.sha256(normalized).hexdigest(),
        replacements=len(spans),
    )


def validate_requirements(path: Path) -> None:
    """Validate one retained hash-pinned requirements text file."""

    _validate_text(_read(path, label=_label(path)), label=_label(path))


def _is_protected(path: Path) -> bool:
    return any(
        component == ".env"
        or component.startswith(".env.")
        or _FORBIDDEN_NAME.search(component)
        for component in path.parts
    )


def validate_candidate_full_raw_evidence(
    root: Path,
    *,
    requirements_path: Path | None = None,
) -> ValidationResult:
    """Validate retained Candidate Full JUnit/XML and explicit text artifacts.

    JSON evidence keeps its own schema-aware validators because frozen node/test
    identities may legitimately contain synthetic absolute-path fixtures.  A raw
    byte scan cannot distinguish those identities from a runner-local leak.
    """

    evidence_root = _directory(root, label="Candidate Full evidence root")
    candidates: list[Path] = []
    requirements: list[Path] = []
    for path in sorted(evidence_root.rglob("*")):
        if path.is_symlink():
            _error("Candidate Full evidence", "contains a symlink")
        if not path.is_file():
            continue
        relative = path.relative_to(evidence_root)
        if _is_protected(relative):
            _error("Candidate Full evidence", "contains a prohibited auth/secret/.env file")
        suffix = path.suffix.lower()
        if suffix != ".xml" and suffix not in _TEXT_SUFFIXES:
            continue
        candidates.append(path)
        if path.name == "candidate-requirements.txt":
            requirements.append(path)
        raw = _read(path, label=relative.as_posix())
        if suffix == ".xml":
            _validate_junit_bytes(
                raw,
                label=relative.as_posix(),
                require_testcases=path.name in {"candidate-tests.xml", "windows-calibration.xml"},
            )
        else:
            _validate_text(raw, label=relative.as_posix())
    if requirements_path is not None:
        requested = _regular_file(
            requirements_path.resolve(strict=True),
            label="candidate-requirements.txt",
        )
        try:
            requested.relative_to(evidence_root)
        except ValueError as exc:
            raise CandidateArtifactPathPolicyError(
                "candidate-requirements.txt: source escapes Candidate Full evidence root"
            ) from exc
        if requested not in requirements:
            _error(
                "candidate-requirements.txt",
                "source is not the retained requirements artifact",
            )
        validate_requirements(requested)
    if len(requirements) != 1:
        _error(
            "Candidate Full evidence",
            "must contain exactly one candidate-requirements.txt",
        )
    return ValidationResult(
        files_scanned=len(candidates),
        bytes_scanned=sum(path.stat().st_size for path in candidates),
        requirements_files=len(requirements),
    )


def validate_candidate_full_artifacts(
    root: Path,
    *,
    requirements_path: Path | None = None,
) -> ValidationResult:
    """Compatibility spelling for the final Candidate Full upload validator."""

    return validate_candidate_full_raw_evidence(
        root,
        requirements_path=requirements_path,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser(
        "normalize-junit", aliases=["sanitize-junit"],
        help="remove the exact checkout-root prefix and validate JUnit bytes",
    )
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)
    normalize.add_argument(
        "--checkout-root", "--repository", dest="checkout_root", required=True
    )
    validate = commands.add_parser(
        "validate", help="validate retained Candidate Full text/XML before upload"
    )
    validate.add_argument("--root", type=Path, required=True)
    validate.add_argument("--requirements", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"normalize-junit", "sanitize-junit"}:
        normalize_junit(args.input, args.output, args.checkout_root)
    else:
        validate_candidate_full_raw_evidence(
            args.root,
            requirements_path=args.requirements,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
