from __future__ import annotations

import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .context_compiler import verify_capsule_file
from .extract import ExtractionError, extract_document
from .knowledge_models import Sensitivity, SourceKind, TrustLevel
from .knowledge_store import KnowledgeVault
from .models import ExtractionQuality, ExtractionResult, TextBlock
from .util import (
    canonical_json,
    has_instruction_risk,
    normalize_text,
    sha256_bytes,
    sha256_file,
)

KNOWLEDGE_COMPILER_SCHEMA = "deeplaw.knowledge-compiler/v1"

_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".csv",
    ".tsv",
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".sql",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".css",
    ".log",
}
_MARKDOWN_SUFFIXES = {".md", ".markdown"}
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_MAX_SECTION_CHARS = 12_000
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_TEXT_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_TEXT_CHARACTERS = 20 * 1024 * 1024
_MAX_TEXT_LINE_CHARACTERS = 2 * 1024 * 1024
_MAX_TEXT_BLOCKS = 200_000
@dataclass(frozen=True, slots=True)
class _CompiledSection:
    title: str
    text: str
    locator: str
    instruction_risk: bool


def _source_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "PDF"
    if suffix == ".docx":
        return "DOCX"
    if suffix == ".doc":
        return "DOC"
    if suffix in _TEXT_SUFFIXES:
        return "TXT"
    raise ExtractionError(
        "unsupported knowledge source format; use PDF, DOCX, UTF-8 text, Markdown, "
        "JSON, source code, CSV/TSV, YAML, TOML, XML, HTML, SQL, or log files; "
        "legacy DOC additionally requires LibreOffice"
    )


def _libreoffice_executable() -> str | None:
    for name in ("soffice", "libreoffice"):
        executable = shutil.which(name)
        if executable:
            return executable
    macos = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if macos.is_file():
        return str(macos)
    return None


def _bounded_process_text(value: str, *, maximum: int = 2_000) -> str:
    return value.strip()[:maximum]


def _extract_legacy_doc(path: Path) -> ExtractionResult:
    executable = _libreoffice_executable()
    if executable is None:
        raise ExtractionError(
            "legacy DOC ingestion requires LibreOffice (soffice) on the local PATH"
        )
    try:
        version_process = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExtractionError("LibreOffice version check failed") from error
    version = _bounded_process_text(
        version_process.stdout or version_process.stderr or "unknown"
    )
    if version_process.returncode != 0 or not version or version == "unknown":
        raise ExtractionError("LibreOffice version identity is unavailable")
    with tempfile.TemporaryDirectory(prefix="deeplaw-doc-") as temporary:
        output_root = Path(temporary)
        profile = output_root / "profile"
        profile.mkdir(mode=0o700)
        try:
            process = subprocess.run(
                [
                    executable,
                    "--headless",
                    "--safe-mode",
                    "--nologo",
                    "--nodefault",
                    "--norestore",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(output_root),
                    str(path),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "HOME": temporary, "TMPDIR": temporary},
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ExtractionError("legacy DOC conversion failed") from error
        candidates = [
            candidate
            for candidate in output_root.iterdir()
            if candidate.suffix.lower() == ".docx"
        ]
        converted = candidates[0] if len(candidates) == 1 else output_root / "missing.docx"
        if (
            process.returncode != 0
            or converted.is_symlink()
            or not converted.is_file()
            or converted.stat().st_size == 0
        ):
            detail = _bounded_process_text(process.stderr or process.stdout)
            raise ExtractionError(
                f"legacy DOC conversion did not produce a safe DOCX: {detail}"
            )
        converted_sha256 = sha256_file(converted)
        extraction = extract_document(converted, "DOCX")
    return ExtractionResult(
        blocks=extraction.blocks,
        quality=replace(
            extraction.quality,
            extractor=f"libreoffice-doc-to-docx+{extraction.quality.extractor}",
            extractor_version=version,
            warnings=(
                *extraction.quality.warnings,
                "legacy DOC was converted to DOCX before deterministic OOXML extraction",
            ),
            configuration=(
                *extraction.quality.configuration,
                f"converted_docx_sha256={converted_sha256}",
            ),
        ),
    )


def _extract_knowledge_text(path: Path) -> ExtractionResult:
    if path.stat().st_size > _MAX_TEXT_SOURCE_BYTES:
        raise ExtractionError(
            "knowledge text source exceeds the 64 MiB extraction limit"
        )
    blocks: list[TextBlock] = []
    character_count = 0
    try:
        with path.open("r", encoding="utf-8-sig", errors="strict") as source:
            for line_number, raw_line in enumerate(source, start=1):
                character_count += len(raw_line)
                if character_count > _MAX_TEXT_CHARACTERS:
                    raise ExtractionError(
                        "knowledge text source exceeds the 20 MiB character limit"
                    )
                if len(raw_line) > _MAX_TEXT_LINE_CHARACTERS:
                    raise ExtractionError(
                        "knowledge text source contains an oversized line"
                    )
                text = raw_line.removesuffix("\n").removesuffix("\r")
                blocks.append(
                    TextBlock(
                        text=text,
                        paragraph=line_number,
                        kind="text_line",
                        source="utf8-preserving",
                    )
                )
                if len(blocks) > _MAX_TEXT_BLOCKS:
                    raise ExtractionError(
                        "knowledge text source exceeds the line-count limit"
                    )
    except UnicodeDecodeError as error:
        raise ExtractionError("knowledge text source must be UTF-8 encoded") from error
    except OSError as error:
        raise ExtractionError("knowledge text source cannot be read") from error
    visible_character_count = sum(len(block.text) for block in blocks)
    if visible_character_count < 20:
        raise ExtractionError("knowledge text source contains too little text")
    return ExtractionResult(
        blocks=tuple(blocks),
        quality=ExtractionQuality(
            extractor="utf8-preserving",
            extractor_version="deeplaw-knowledge-text/v1",
            block_count=len(blocks),
            page_count=None,
            character_count=visible_character_count,
            configuration=("line_structure=preserved", "encoding=utf-8-sig"),
        ),
    )


def _locator(blocks: list[TextBlock], *, ordinal: int) -> str:
    pages = [block.page for block in blocks if block.page is not None]
    paragraphs = [block.paragraph for block in blocks if block.paragraph is not None]
    values = [f"section:{ordinal}"]
    if pages:
        values.append(f"pages:{min(pages)}-{max(pages)}")
    if paragraphs:
        values.append(f"paragraphs:{min(paragraphs)}-{max(paragraphs)}")
    return ";".join(values)


def _split_large_section(
    title: str,
    blocks: list[TextBlock],
    *,
    first_ordinal: int,
) -> list[_CompiledSection]:
    sections: list[_CompiledSection] = []
    current: list[TextBlock] = []
    current_chars = 0
    part = 1

    def flush() -> None:
        nonlocal current, current_chars, part
        if not current:
            return
        text = "\n".join(block.text for block in current).strip()
        section_title = title if part == 1 else f"{title} · part {part}"
        sections.append(
            _CompiledSection(
                title=section_title,
                text=text,
                locator=_locator(current, ordinal=first_ordinal + len(sections)),
                instruction_risk=has_instruction_risk(text),
            )
        )
        current = []
        current_chars = 0
        part += 1

    for block in blocks:
        text = block.text.rstrip("\r\n")
        if not text:
            if current:
                if current_chars + 1 > _MAX_SECTION_CHARS:
                    flush()
                if not current:
                    continue
                current.append(block)
                current_chars += 1
            continue
        start = 0
        while start < len(text):
            remaining = _MAX_SECTION_CHARS - current_chars
            if remaining <= 0:
                flush()
                remaining = _MAX_SECTION_CHARS
            end = min(len(text), start + remaining)
            if end < len(text):
                boundary = max(
                    text.rfind(mark, start + max(1, remaining // 2), end)
                    for mark in ("\n", "。", "；", ".", ";")
                )
                if boundary > start:
                    end = boundary + 1
            fragment = text[start:end]
            if fragment.strip():
                current.append(
                    TextBlock(
                        text=fragment,
                        page=block.page,
                        paragraph=block.paragraph,
                        style=block.style,
                        kind=block.kind,
                        source=block.source,
                    )
                )
                current_chars += len(fragment) + 1
            start = end
            if current_chars >= _MAX_SECTION_CHARS:
                flush()
    flush()
    return sections


def _compile_sections(
    path: Path,
    blocks: tuple[TextBlock, ...],
    *,
    title: str,
) -> tuple[_CompiledSection, ...]:
    if not blocks:
        raise ExtractionError("knowledge source produced no text blocks")
    grouped: list[tuple[str, list[TextBlock]]] = []
    current_title = title
    current: list[TextBlock] = []
    markdown = path.suffix.lower() in _MARKDOWN_SUFFIXES

    def flush() -> None:
        nonlocal current
        if current:
            grouped.append((current_title, current))
            current = []

    for block in blocks:
        heading = _MARKDOWN_HEADING.match(block.text) if markdown else None
        if heading is not None:
            flush()
            current_title = normalize_text(heading.group(2))[:500]
            continue
        current.append(block)
    flush()

    sections: list[_CompiledSection] = []
    for group_title, group_blocks in grouped:
        sections.extend(
            _split_large_section(
                group_title,
                group_blocks,
                first_ordinal=len(sections) + 1,
            )
        )
    if not sections:
        raise ExtractionError("knowledge source produced no compilable sections")
    return tuple(sections)


def compile_source(
    vault: KnowledgeVault,
    source: str | Path,
    *,
    source_kind: SourceKind,
    title: str | None = None,
    origin_uri: str | None = None,
    trust: TrustLevel = "user_provided",
    sensitivity: Sensitivity = "private",
    confirm_no_case_data: bool,
    pdf_fallback: str = "off",
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "knowledge ingestion requires confirmation that the source is not Analytix "
            "case material"
        )
    source_path = Path(source).expanduser().absolute()
    if source_path.is_symlink():
        raise ValueError("knowledge source must be a regular non-symlink file")
    path = source_path.resolve(strict=True)
    if not path.is_file():
        raise ValueError("knowledge source must resolve to a regular non-symlink file")
    source_size = path.stat().st_size
    if not 1 <= source_size <= _MAX_SOURCE_BYTES:
        raise ValueError("knowledge source is empty or exceeds 512 MiB")
    source_content_sha256 = sha256_file(path)
    format_name = _source_format(path)
    extraction = (
        _extract_legacy_doc(path)
        if format_name == "DOC"
        else (
            _extract_knowledge_text(path)
            if format_name == "TXT"
            else extract_document(path, format_name, pdf_fallback=pdf_fallback)
        )
    )
    if format_name == "PDF" and extraction.quality.needs_ocr:
        raise ExtractionError(
            "PDF text quality gate failed; rerun with "
            "--pdf-fallback document-engine or provide a human-reviewed extraction"
        )
    source_title = (title or path.stem).strip()
    if not source_title or len(source_title) > 500:
        raise ValueError("knowledge source title must be between 1 and 500 characters")
    sections = _compile_sections(
        path,
        tuple(
            TextBlock(
                text=block.text,
                page=block.page,
                paragraph=block.paragraph,
                style=block.style,
                kind=block.kind,
                source=block.source,
            )
            for block in extraction.blocks
        ),
        title=source_title,
    )
    if (
        path.stat().st_size != source_size
        or sha256_file(path) != source_content_sha256
    ):
        raise RuntimeError("knowledge source changed while it was being compiled")
    source_risk = any(section.instruction_risk for section in sections)
    warnings = tuple(
        dict.fromkeys(
            (
                *extraction.quality.warnings,
                *(
                    (
                        "instruction-like or invisible control content detected; "
                        "compiled assets remain quarantined until explicit human review",
                    )
                    if source_risk
                    else ()
                ),
            )
        )
    )
    fragments = tuple(
        {
            "text": section.text,
            "locator": section.locator,
            "instruction_risk": section.instruction_risk,
        }
        for section in sections
    )
    memory_tier = (
        "project"
        if source_kind in {"conversation", "tool_result", "code"}
        else "domain"
    )
    asset_specs = tuple(
        {
            "kind": "reference",
            "memory_tier": memory_tier,
            "title": section.title,
            "statement": section.text,
            "tags": (source_kind, path.suffix.lower().lstrip(".") or "text"),
            "warnings": (
                ("section contains instruction-like content",)
                if section.instruction_risk
                else ()
            ),
        }
        for section in sections
    )
    compiled_fragment_sha256 = sha256_bytes(
        canonical_json(
            [
                {
                    "title": section.title,
                    "locator": section.locator,
                    "text": section.text,
                    "instruction_risk": section.instruction_risk,
                }
                for section in sections
            ]
        ).encode("utf-8")
    )
    compiler = {
        "schema_version": KNOWLEDGE_COMPILER_SCHEMA,
        "format": format_name,
        "source_sha256": source_content_sha256,
        "extractor": extraction.quality.extractor,
        "extractor_version": extraction.quality.extractor_version,
        "configuration": list(extraction.quality.configuration),
        "pdf_fallback": pdf_fallback if format_name == "PDF" else None,
        "block_count": extraction.quality.block_count,
        "page_count": extraction.quality.page_count,
        "character_count": extraction.quality.character_count,
        "section_count": len(sections),
        "compiled_fragment_sha256": compiled_fragment_sha256,
        "instruction_risk": source_risk,
        "policy": "source fragments are evidence; compiled assets are review candidates",
    }
    result = vault.add_compiled_source(
        source_path=path,
        expected_byte_size=source_size,
        expected_content_sha256=source_content_sha256,
        source_kind=source_kind,
        title=source_title,
        origin_uri=origin_uri,
        media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        trust=trust,
        sensitivity=sensitivity,
        instruction_risk=source_risk,
        warnings=warnings,
        compiler=compiler,
        fragments=fragments,
        asset_specs=asset_specs,
    )
    result["compiler"] = compiler
    return result


def record_debug_experience(
    vault: KnowledgeVault,
    *,
    question: str,
    cause: str,
    fix: str,
    prevention: str,
    confirm_no_case_data: bool,
    sensitivity: Sensitivity = "private",
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "knowledge debugger records require confirmation that they contain no case data"
        )
    fields = {
        "question": question.strip(),
        "cause": cause.strip(),
        "fix": fix.strip(),
        "prevention": prevention.strip(),
    }
    if any(not value or len(value) > 5_000 for value in fields.values()):
        raise ValueError("knowledge debugger fields must be between 1 and 5000 characters")
    statement = (
        f"Question: {fields['question']}\n"
        f"Cause: {fields['cause']}\n"
        f"Fix: {fields['fix']}\n"
        f"Prevention: {fields['prevention']}"
    )
    asset = vault.propose_asset(
        kind="experience",
        memory_tier="experience",
        title=f"Knowledge Debugger: {fields['question'][:120]}",
        statement=statement,
        semantic_key=None,
        trust="user_provided",
        sensitivity=sensitivity,
        tags=("knowledge-debugger", "failure-learning"),
        quarantined=has_instruction_risk(statement),
    )
    return {
        "schema_version": "deeplaw.knowledge-debugger/v1",
        "asset": asset.to_dict(),
        "activation": "explicit human review and approval required",
    }


def record_capsule_feedback(
    vault: KnowledgeVault,
    *,
    capsule_path: str | Path,
    outcome: str,
    observation: str,
    lesson: str,
    next_action: str | None,
    confirm_no_case_data: bool,
    sensitivity: Sensitivity = "private",
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "capsule feedback requires confirmation that it contains no case data"
        )
    capsule_verification = verify_capsule_file(capsule_path, vault=vault)
    if not capsule_verification["valid"]:
        raise ValueError(
            "capsule feedback requires a valid Capsule bound to the selected vault"
        )
    capsule_id = capsule_verification["capsule_id"]
    if not isinstance(capsule_id, str):
        raise ValueError("capsule feedback verification did not return a Capsule ID")
    if outcome not in {"success", "partial", "failure"}:
        raise ValueError("capsule feedback outcome must be success, partial, or failure")
    observation = observation.strip()
    lesson = lesson.strip()
    next_action = next_action.strip() if next_action else None
    if not observation or len(observation) > 5_000:
        raise ValueError("feedback observation must be between 1 and 5000 characters")
    if not lesson or len(lesson) > 5_000:
        raise ValueError("feedback lesson must be between 1 and 5000 characters")
    if next_action is not None and len(next_action) > 5_000:
        raise ValueError("feedback next_action exceeds 5000 characters")
    statement = (
        f"Outcome: {outcome}\n"
        f"Observation: {observation}\n"
        f"Lesson: {lesson}"
        + (f"\nNext action: {next_action}" if next_action else "")
    )
    asset = vault.propose_asset(
        kind="lesson",
        memory_tier="experience",
        title=f"Capsule feedback: {outcome} · {capsule_id}",
        statement=statement,
        trust="user_provided",
        sensitivity=sensitivity,
        tags=("capsule-feedback", outcome),
        origin_uri=f"deeplaw://{vault.vault_id}/capsules/{capsule_id}",
        quarantined=has_instruction_risk(statement),
    )
    return {
        "schema_version": "deeplaw.knowledge-feedback/v1",
        "capsule_id": capsule_id,
        "asset": asset.to_dict(),
        "activation": "feedback never self-promotes; explicit human review is required",
    }
