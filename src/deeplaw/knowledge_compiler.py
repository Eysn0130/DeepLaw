from __future__ import annotations

import mimetypes
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any

from .bounded_subprocess import BoundedSubprocessError, run_bounded_subprocess
from .context_compiler import verify_capsule_file
from .extract import ExtractionError, extract_document
from .knowledge_identity import (
    canonical_collection_name,
    make_collection_id,
    make_knowledge_key,
    normalize_logical_path,
)
from .knowledge_models import Sensitivity, SourceKind, TrustLevel
from .knowledge_store import KnowledgeVault, knowledge_source_key
from .models import ExtractionQuality, ExtractionResult, TextBlock
from .source_adapters import build_source_ir, extract_extended_source
from .typed_extractor import run_typed_extractor
from .util import (
    canonical_json,
    has_instruction_risk,
    normalize_text,
    sha256_bytes,
    sha256_file,
    stable_id,
)

KNOWLEDGE_COMPILER_SCHEMA = "deeplaw.knowledge-compiler/v1"
TYPED_EXTRACTION_MODES = frozenset(
    {
        "off",
        "deterministic-v1",
        "deterministic-v2",
        "local-model-v1",
        "external-model-explicit",
    }
)

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
_COMPILED_PART_SUFFIX = re.compile(r"^(?P<title>.+) · part [2-9][0-9]*$")
_MAX_SECTION_CHARS = 12_000
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_TEXT_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_TEXT_CHARACTERS = 20 * 1024 * 1024
_MAX_TEXT_LINE_CHARACTERS = 2 * 1024 * 1024
_MAX_TEXT_BLOCKS = 200_000
_MAX_DIRECTORY_ENTRIES = 100_000


@dataclass(frozen=True, slots=True)
class _CompiledSection:
    title: str
    text: str
    locator: str
    instruction_risk: bool


def _typed_section_kind(section: _CompiledSection, *, extractor: str) -> str:
    if extractor == "off":
        return "reference"
    if extractor not in {"deterministic-v1", "deterministic-v2"}:
        raise ValueError("typed extraction must be off, deterministic-v1, or deterministic-v2")
    title = normalize_text(section.title).casefold()
    statement = normalize_text(section.text).casefold()
    if title.startswith(("decision", "decision record", "决策", "决定")):
        return "decision"
    if title.startswith(("constraint", "policy", "约束", "限制", "边界")):
        return "constraint"
    if title.startswith(("procedure", "workflow", "steps", "流程", "步骤")):
        return "procedure"
    if title.startswith(("lesson", "postmortem", "教训", "经验", "复盘")):
        return "lesson"
    if title.startswith(("experience", "经历", "实践")):
        return "experience"
    if title.startswith(("exception", "例外", "除外")):
        return "exception"
    if title.startswith(("definition", "glossary", "定义", "术语")):
        return "definition"
    if title.startswith(("requirement", "must", "要求", "需求", "必须")):
        return "requirement"
    if title.startswith(("risk", "warning", "风险", "警告")):
        return "risk"
    if title.startswith(("assumption", "假设", "前提")):
        return "assumption"
    if title.startswith(("question", "open question", "问题", "待确认")) or statement.endswith(
        ("?", "\uff1f")
    ):
        return "question"
    if title.startswith(("rule", "规则")):
        return "rule"
    if title.startswith(("fact", "事实")):
        return "fact"
    return "reference"


def _deterministic_extractor_revision(mode: str) -> str:
    if mode == "deterministic-v2":
        return "deeplaw-deterministic/v2"
    if mode in {"off", "deterministic-v1"}:
        return "deeplaw-deterministic/v1"
    raise ValueError("model-backed typed extraction has a manifest-defined revision")


def _source_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "PDF"
    if suffix == ".docx":
        return "DOCX"
    if suffix == ".doc":
        return "DOC"
    if suffix == ".pptx":
        return "PPTX"
    if suffix == ".xlsx":
        return "XLSX"
    if suffix == ".epub":
        return "EPUB"
    if suffix in _TEXT_SUFFIXES:
        return "TXT"
    raise ExtractionError(
        "unsupported knowledge source format; use PDF, DOCX, PPTX, XLSX, EPUB, "
        "UTF-8 text, Markdown, "
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
        version_process = run_bounded_subprocess(
            [executable, "--version"],
            timeout_seconds=15,
            max_stdout_bytes=8 * 1024,
            max_stderr_bytes=8 * 1024,
        )
    except BoundedSubprocessError as error:
        raise ExtractionError("LibreOffice version check failed") from error
    version = _bounded_process_text(
        (version_process.stdout or version_process.stderr).decode(
            "utf-8", errors="replace"
        )
        or "unknown"
    )
    if version_process.returncode != 0 or not version or version == "unknown":
        raise ExtractionError("LibreOffice version identity is unavailable")
    with tempfile.TemporaryDirectory(prefix="deeplaw-doc-") as temporary:
        output_root = Path(temporary)
        profile = output_root / "profile"
        profile.mkdir(mode=0o700)
        try:
            process = run_bounded_subprocess(
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
                timeout_seconds=120,
                max_stdout_bytes=64 * 1024,
                max_stderr_bytes=64 * 1024,
                environment={**os.environ, "HOME": temporary, "TMPDIR": temporary},
            )
        except BoundedSubprocessError as error:
            raise ExtractionError("legacy DOC conversion failed") from error
        candidates = [
            candidate for candidate in output_root.iterdir() if candidate.suffix.lower() == ".docx"
        ]
        converted = candidates[0] if len(candidates) == 1 else output_root / "missing.docx"
        if (
            process.returncode != 0
            or converted.is_symlink()
            or not converted.is_file()
            or converted.stat().st_size == 0
        ):
            detail = _bounded_process_text(
                (process.stderr or process.stdout).decode("utf-8", errors="replace")
            )
            raise ExtractionError(f"legacy DOC conversion did not produce a safe DOCX: {detail}")
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
        raise ExtractionError("knowledge text source exceeds the 64 MiB extraction limit")
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
                    raise ExtractionError("knowledge text source contains an oversized line")
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
                    raise ExtractionError("knowledge text source exceeds the line-count limit")
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
        structured_heading = (
            not markdown
            and block.style is not None
            and re.match(r"^(?:Heading|Title)\s*[1-6]?$", block.style, re.IGNORECASE)
        )
        if heading is not None:
            flush()
            current_title = normalize_text(heading.group(2))[:500]
            continue
        if structured_heading:
            flush()
            current_title = normalize_text(block.text)[:500]
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
    source_key: str | None = None,
    typed_extraction: str = "off",
    typed_extractor_manifest: str | Path | None = None,
    confirm_external_disclosure: bool = False,
    reference_proposals: bool = True,
    collection_id: str | None = None,
    collection_name: str = "project",
    logical_path: str | None = None,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "knowledge ingestion requires confirmation that the source is not Analytix "
            "case material"
        )
    if typed_extraction not in TYPED_EXTRACTION_MODES:
        raise ValueError("unsupported typed extraction mode")
    model_backed = typed_extraction in {
        "local-model-v1",
        "external-model-explicit",
    }
    if model_backed != (typed_extractor_manifest is not None):
        raise ValueError(
            "model-backed typed extraction requires exactly one --typed-extractor-manifest"
        )
    if typed_extraction != "external-model-explicit" and confirm_external_disclosure:
        raise ValueError(
            "external disclosure confirmation is only valid for external-model-explicit"
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
    selected_collection_name = canonical_collection_name(collection_name)
    expected_collection_id = make_collection_id(
        vault_id=vault.vault_id,
        name=selected_collection_name,
    )
    selected_collection_id = collection_id or expected_collection_id
    if selected_collection_id != expected_collection_id:
        raise ValueError("collection identity does not match its canonical name")
    selected_logical_path = normalize_logical_path(logical_path or path.name)
    logical_source_key = source_key or knowledge_source_key(
        vault_id=vault.vault_id,
        source_kind=source_kind,
        source_path=path,
        origin_uri=origin_uri,
        collection_id=selected_collection_id,
        logical_path=selected_logical_path,
    )
    prior_source = (
        vault.active_source_for_key(logical_source_key)
        if vault.control_enabled
        else None
    )
    prior_logical_path = (
        prior_source.get("logical_path") if prior_source is not None else None
    )
    relocation_status: str | None = None
    if prior_logical_path is not None and prior_logical_path != selected_logical_path:
        old_path = PurePosixPath(prior_logical_path)
        new_path = PurePosixPath(selected_logical_path)
        relocation_status = (
            "renamed" if old_path.parent == new_path.parent else "moved"
        )
    format_name = _source_format(path)
    source_media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    extraction = (
        _extract_legacy_doc(path)
        if format_name == "DOC"
        else (
            extract_extended_source(path, format_name)
            if format_name in {"PPTX", "XLSX", "EPUB"}
            else (
                _extract_knowledge_text(path)
                if format_name == "TXT"
                else extract_document(path, format_name, pdf_fallback=pdf_fallback)
            )
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
    if path.stat().st_size != source_size or sha256_file(path) != source_content_sha256:
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
    memory_tier = "project" if source_kind in {"conversation", "tool_result", "code"} else "domain"
    section_occurrences: dict[str, int] = {}
    current_section_groups: dict[str, str] = {}
    fragments_list: list[dict[str, Any]] = []
    asset_specs_list: list[dict[str, Any]] = []
    for fragment_index, section in enumerate(sections):
        match = _COMPILED_PART_SUFFIX.fullmatch(section.title)
        section_title = match.group("title") if match else section.title
        if match is None:
            occurrence = section_occurrences.get(section_title, 0) + 1
            section_occurrences[section_title] = occurrence
            section_group_id = stable_id(
                "sectiongroup",
                logical_source_key,
                section_title,
                str(occurrence),
            )
            current_section_groups[section_title] = section_group_id
        else:
            occurrence = section_occurrences.get(section_title, 1)
            section_group_id = current_section_groups.get(section_title)
            if section_group_id is None:
                section_group_id = stable_id(
                    "sectiongroup",
                    logical_source_key,
                    section_title,
                    str(occurrence),
                )
                current_section_groups[section_title] = section_group_id
        section_id = stable_id(
            "section",
            logical_source_key,
            section_group_id,
            section.title,
        )
        logical_node_key = f"section:{section_id}"
        fragments_list.append(
            {
                "text": section.text,
                "locator": section.locator,
                "instruction_risk": section.instruction_risk,
                "logical_node_key": logical_node_key,
                "parent_logical_node_key": None,
                "node_type": "section",
                "title": section.title,
                "source_span": {
                    "locator": section.locator,
                    "fragment_ordinal": fragment_index + 1,
                },
                "quality_flags": [],
            }
        )
        deterministic_mode = "off" if model_backed else typed_extraction
        kind = _typed_section_kind(section, extractor=deterministic_mode)
        proposal_kinds = (
            ("reference", kind)
            if typed_extraction == "deterministic-v2" and kind != "reference"
            else (kind,)
        )
        if not reference_proposals:
            proposal_kinds = tuple(
                proposal_kind
                for proposal_kind in proposal_kinds
                if proposal_kind != "reference"
            )
        for proposal_kind in proposal_kinds:
            proposal_role = (
                "reference" if proposal_kind == "reference" else f"typed:{proposal_kind}"
            )
            knowledge_key = make_knowledge_key(
                vault_id=vault.vault_id,
                source_key=logical_source_key,
                logical_node_key=logical_node_key,
                proposal_role=proposal_role,
            )
            active = vault.active_asset_for_semantic_key(knowledge_key)
            asset_specs_list.append(
                {
                    "kind": proposal_kind,
                    "memory_tier": memory_tier,
                    "title": section.title,
                    "statement": section.text,
                    "semantic_key": knowledge_key,
                    "knowledge_key": knowledge_key,
                    "proposal_role": proposal_role,
                    "logical_node_keys": (logical_node_key,),
                    "source_ref_indexes": (fragment_index,),
                    "applicability": {
                        "source_kind": source_kind,
                        "logical_path": selected_logical_path,
                    },
                    "project_scope": selected_collection_id,
                    "repository_scope": str(
                        PurePosixPath(selected_logical_path).parent
                    ),
                    "branch_scope": None,
                    "version_scope": None,
                    "environment_scope": None,
                    "valid_from": None,
                    "valid_to": None,
                    "supersedes_asset_id": active.asset_id if active is not None else None,
                    "lineage_status_hint": relocation_status,
                    "previous_logical_path": prior_logical_path,
                    "current_logical_path": selected_logical_path,
                    "tags": tuple(
                        dict.fromkeys(
                            (
                                source_kind,
                                path.suffix.lower().lstrip(".") or "text",
                                f"section-group:{section_group_id}",
                                *(
                                    (f"typed-extractor:{typed_extraction}",)
                                    if typed_extraction != "off"
                                    else ()
                                ),
                            )
                        )
                    ),
                    "warnings": (
                        ("section contains instruction-like content",)
                        if section.instruction_risk
                        else ()
                    ),
                }
            )
    source_ir = build_source_ir(
        path,
        source_key=logical_source_key,
        format_name=format_name,
        extraction=extraction,
        fragments=fragments_list,
    )
    for index, logical_node_keys in enumerate(source_ir.fragment_logical_node_keys):
        fragments_list[index]["logical_node_keys"] = logical_node_keys
    typed_extractor_metadata: dict[str, Any] | None = None
    if model_backed:
        assert typed_extractor_manifest is not None
        extracted = run_typed_extractor(
            manifest_path=typed_extractor_manifest,
            mode=typed_extraction,
            source_revision_hint={
                "source_key": logical_source_key,
                "content_sha256": source_content_sha256,
                "media_identity": source_media_type,
                "logical_path": selected_logical_path,
            },
            sections=[
                {
                    "index": index,
                    "title": section.title,
                    "text": section.text,
                    "locator": section.locator,
                    "logical_node_keys": list(
                        fragments_list[index]["logical_node_keys"]
                    ),
                }
                for index, section in enumerate(sections)
            ],
            confirm_external_disclosure=confirm_external_disclosure,
        )
        for ordinal, proposal in enumerate(extracted["proposals"], start=1):
            reference_indexes = tuple(proposal["source_ref_indexes"])
            logical_node_keys = tuple(
                dict.fromkeys(
                    key
                    for index in reference_indexes
                    for key in fragments_list[index]["logical_node_keys"]
                )
            )
            proposal_fingerprint = sha256_bytes(
                canonical_json(
                    {
                        "kind": proposal["kind"],
                        "statement": normalize_text(proposal["statement"]),
                        "semantic_key_hint": proposal["semantic_key_hint"],
                        "logical_node_keys": list(logical_node_keys),
                    }
                ).encode("utf-8")
            )
            proposal_role = f"typed:{proposal['kind']}:{proposal_fingerprint[:24]}"
            knowledge_key = make_knowledge_key(
                vault_id=vault.vault_id,
                source_key=logical_source_key,
                logical_node_key="+".join(logical_node_keys),
                proposal_role=proposal_role,
            )
            active = vault.active_asset_for_semantic_key(knowledge_key)
            proposal_risk = has_instruction_risk(
                f"{proposal['title']}\n{proposal['statement']}"
            )
            proposal_spec: dict[str, Any] = {
                "kind": proposal["kind"],
                "memory_tier": memory_tier,
                "title": proposal["title"],
                "statement": proposal["statement"],
                "semantic_key": knowledge_key,
                "knowledge_key": knowledge_key,
                "proposal_role": proposal_role,
                "logical_node_keys": logical_node_keys,
                "source_ref_indexes": reference_indexes,
                "applicability": {
                    "source_kind": source_kind,
                    "logical_path": selected_logical_path,
                    **proposal["applicability"],
                },
                "project_scope": proposal["project_scope"] or selected_collection_id,
                "repository_scope": proposal["repository_scope"]
                or str(PurePosixPath(selected_logical_path).parent),
                "branch_scope": proposal["branch_scope"],
                "version_scope": proposal["version_scope"],
                "environment_scope": proposal["environment_scope"],
                "valid_from": proposal["valid_from"],
                "valid_to": proposal["valid_to"],
                "expires_at": proposal["expires_at"],
                "supersedes_asset_id": (
                    active.asset_id if active is not None else None
                ),
                "lineage_status_hint": relocation_status,
                "previous_logical_path": prior_logical_path,
                "current_logical_path": selected_logical_path,
                # Model output never inherits source trust.  Human approval is
                # still required before it can become active.
                "trust": "untrusted",
                "quarantined": proposal_risk,
                "tags": (
                    source_kind,
                    path.suffix.lower().lstrip(".") or "text",
                    f"typed-extractor:{typed_extraction}",
                    f"model-proposal:{ordinal}",
                ),
                "warnings": tuple(
                    dict.fromkeys(
                        (
                            *proposal["warnings"],
                            "automated model output requires human review",
                            *(("instruction-like model output",) if proposal_risk else ()),
                        )
                    )
                ),
            }
            if proposal["observed_at"] is not None:
                proposal_spec["observed_at"] = proposal["observed_at"]
            asset_specs_list.append(proposal_spec)
        typed_extractor_metadata = {
            key: extracted[key]
            for key in (
                "extractor",
                "extractor_revision",
                "model_identity",
                "prompt_config_sha256",
                "manifest_sha256",
                "network_policy",
                "disclosure",
                "output_sha256",
            )
        }
    fragments = tuple(fragments_list)
    asset_specs = tuple(asset_specs_list)
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
        "source_key": logical_source_key,
        "collection_id": selected_collection_id,
        "collection_name": selected_collection_name,
        "logical_path": selected_logical_path,
        "format": format_name,
        "source_sha256": source_content_sha256,
        "extractor": extraction.quality.extractor,
        "extractor_version": extraction.quality.extractor_version,
        "source_adapter": source_ir.adapter,
        "source_adapter_version": source_ir.adapter_version,
        "source_ir_schema": "deeplaw.source-ir/v1",
        "source_ir_node_count": len(source_ir.nodes),
        "source_ir_quality_flags": list(source_ir.quality_flags),
        "configuration": list(extraction.quality.configuration),
        "pdf_fallback": pdf_fallback if format_name == "PDF" else None,
        "block_count": extraction.quality.block_count,
        "page_count": extraction.quality.page_count,
        "character_count": extraction.quality.character_count,
        "section_count": len(sections),
        "compiled_fragment_sha256": compiled_fragment_sha256,
        "instruction_risk": source_risk,
        "typed_extraction": typed_extraction,
        "reference_proposals": reference_proposals,
        "typed_extractor": typed_extractor_metadata,
        "policy": "source fragments are evidence; compiled assets are review candidates",
    }
    result = vault.add_compiled_source(
        source_path=path,
        source_key=logical_source_key,
        expected_byte_size=source_size,
        expected_content_sha256=source_content_sha256,
        source_kind=source_kind,
        title=source_title,
        origin_uri=origin_uri,
        media_type=source_media_type,
        trust=trust,
        sensitivity=sensitivity,
        instruction_risk=source_risk,
        warnings=warnings,
        compiler=compiler,
        fragments=fragments,
        asset_specs=asset_specs,
        source_ir_nodes=source_ir.nodes,
    )
    result["compiler"] = compiler
    return result


def compile_directory(
    vault: KnowledgeVault,
    directory: str | Path,
    *,
    recursive: bool,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    source_kind: SourceKind = "document",
    trust: TrustLevel = "user_provided",
    sensitivity: Sensitivity = "private",
    confirm_no_case_data: bool,
    pdf_fallback: str = "off",
    typed_extraction: str = "off",
    typed_extractor_manifest: str | Path | None = None,
    confirm_external_disclosure: bool = False,
    reference_proposals: bool = True,
    dry_run: bool = False,
    collection_id: str | None = None,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "directory ingestion requires confirmation that sources contain no case data"
        )
    root_input = Path(directory).expanduser().absolute()
    if root_input.is_symlink():
        raise ValueError("knowledge source directory must not be a symbolic link")
    root = root_input.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("knowledge source directory must be a directory")
    selected_collection_id = collection_id or make_collection_id(
        vault_id=vault.vault_id,
        name="project",
    )
    if len(include) > 32 or len(exclude) > 32:
        raise ValueError("directory include/exclude patterns exceed the bound")
    patterns = (*include, *exclude)
    if any(not pattern or len(pattern) > 500 for pattern in patterns):
        raise ValueError("directory include/exclude pattern is invalid")
    iterator = root.rglob("*") if recursive else root.glob("*")
    candidates: list[tuple[str, Path, int, str]] = []
    skipped = 0
    entries_seen = 0
    for path in iterator:
        entries_seen += 1
        if entries_seen > _MAX_DIRECTORY_ENTRIES:
            raise ValueError("directory ingestion exceeds the 100000-entry scan bound")
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.is_file():
            skipped += 1
            continue
        if any(part in {".git", "node_modules", "__pycache__"} for part in path.parts):
            skipped += 1
            continue
        if include and not any(fnmatch(relative, pattern) for pattern in include):
            skipped += 1
            continue
        if any(fnmatch(relative, pattern) for pattern in exclude):
            skipped += 1
            continue
        try:
            _source_format(path)
        except ExtractionError:
            skipped += 1
            continue
        size = path.stat().st_size
        if not 1 <= size <= _MAX_SOURCE_BYTES:
            skipped += 1
            continue
        candidates.append((relative, path, size, sha256_file(path)))
        if len(candidates) > 10_000:
            raise ValueError("directory ingestion exceeds the 10000-file job bound")
    candidates.sort(key=lambda item: item[0])
    manifest_entries = [
        {"path": relative, "byte_size": size, "content_sha256": digest}
        for relative, _, size, digest in candidates
    ]
    manifest_body = {
        "schema_version": "deeplaw.knowledge-directory-manifest/v1",
        "vault_id": vault.vault_id,
        "collection_id": selected_collection_id,
        "recursive": recursive,
        "include": list(include),
        "exclude": list(exclude),
        "source_kind": source_kind,
        "trust": trust,
        "sensitivity": sensitivity,
        "typed_extraction": typed_extraction,
        "reference_proposals": reference_proposals,
        "typed_extractor_manifest_sha256": (
            sha256_file(Path(typed_extractor_manifest).expanduser().absolute())
            if typed_extractor_manifest is not None
            else None
        ),
        "external_disclosure_confirmed": confirm_external_disclosure,
        "files": manifest_entries,
    }
    manifest_sha256 = sha256_bytes(canonical_json(manifest_body).encode("utf-8"))
    job_id = stable_id("job", vault.vault_id, manifest_sha256)
    if dry_run:
        visible = manifest_entries[:100]
        return {
            "schema_version": "deeplaw.knowledge-directory-job/v1",
            "job_id": job_id,
            "manifest_sha256": manifest_sha256,
            "dry_run": True,
            "files_seen": entries_seen,
            "files_admitted": len(candidates),
            "files_skipped": skipped,
            "files_failed": 0,
            "total_bytes": sum(item[2] for item in candidates),
            "files": visible,
            "files_truncated": len(visible) < len(manifest_entries),
            "write_performed": False,
        }

    started = perf_counter()
    compiled: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for relative, path, _, expected_hash in candidates:
        try:
            if sha256_file(path) != expected_hash:
                raise RuntimeError("file changed after directory manifest creation")
            compiled.append(
                compile_source(
                    vault,
                    path,
                    source_kind=source_kind,
                    trust=trust,
                    sensitivity=sensitivity,
                    confirm_no_case_data=True,
                    pdf_fallback=pdf_fallback,
                    typed_extraction=typed_extraction,
                    typed_extractor_manifest=typed_extractor_manifest,
                    confirm_external_disclosure=confirm_external_disclosure,
                    reference_proposals=reference_proposals,
                    collection_id=selected_collection_id,
                    logical_path=relative,
                )
            )
        except (OSError, RuntimeError, ValueError, ExtractionError) as error:
            failures.append({"path": relative, "error": f"{type(error).__name__}: {error}"[:1_000]})
    source_ids = [result["source"]["source_id"] for result in compiled]
    source_keys = [result["source"]["source_key"] for result in compiled]
    proposal_count = sum(len(result["asset_ids"]) for result in compiled)
    quarantine_count = sum(
        len(result["asset_ids"]) for result in compiled if result["source"]["instruction_risk"]
    )
    return {
        "schema_version": "deeplaw.knowledge-directory-job/v1",
        "job_id": job_id,
        "manifest_sha256": manifest_sha256,
        "dry_run": False,
        "partial_success": bool(compiled and failures),
        "atomic": False,
        "failure_semantics": (
            "each file is an independent atomic source transaction; failed files "
            "do not roll back successful files"
        ),
        "files_seen": entries_seen,
        "files_admitted": len(compiled),
        "files_skipped": skipped,
        "files_failed": len(failures),
        "total_bytes": sum(item[2] for item in candidates),
        "source_keys": source_keys[:100],
        "source_ids": source_ids[:100],
        "sources_truncated": len(source_ids) > 100,
        "proposal_count": proposal_count,
        "quarantine_count": quarantine_count,
        "failures": failures[:100],
        "failures_truncated": len(failures) > 100,
        "elapsed_seconds": round(perf_counter() - started, 6),
    }


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
        raise ValueError("capsule feedback requires confirmation that it contains no case data")
    capsule_verification = verify_capsule_file(capsule_path, vault=vault)
    if not capsule_verification["valid"]:
        raise ValueError("capsule feedback requires a valid Capsule bound to the selected vault")
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
    statement = f"Outcome: {outcome}\nObservation: {observation}\nLesson: {lesson}" + (
        f"\nNext action: {next_action}" if next_action else ""
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
