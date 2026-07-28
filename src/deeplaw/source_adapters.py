from __future__ import annotations

import ast
import bisect
import csv
import math
import re
import sqlite3
import stat
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import sqlglot
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_rust
import tree_sitter_typescript
from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError, TokenError
from sqlglot.tokens import TokenType
from tree_sitter import Language, Node, Parser

from .extract import ExtractionError
from .models import ExtractionQuality, ExtractionResult, TextBlock
from .util import (
    canonical_json,
    has_instruction_risk,
    normalize_text,
    sha256_bytes,
    stable_id,
    strict_json_loads,
)

SOURCE_ADAPTER_SCHEMA = "deeplaw.source-adapter/v1"
SOURCE_IR_ADAPTER_VERSION = "deeplaw-source-adapters/4"
_SQLGLOT_VERSION = "30.13.0"

_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
_MAX_BLOCKS = 200_000
_MAX_CHARACTERS = 20 * 1024 * 1024
_MAX_CODE_PARSER_BYTES = 8 * 1024 * 1024
_MAX_CODE_TREE_NODES = 500_000
_MAX_CODE_SYMBOLS = 50_000
_MAX_SQL_PARSER_BYTES = 8 * 1024 * 1024
_MAX_SQL_TREE_NODES = 500_000
_MAX_SQL_SYMBOLS = 50_000
_MAX_XML_NODES = 500_000
_MAX_XML_DEPTH = 256
_MAX_STRUCTURE_DEPTH = 128
_XLSX_CELL_REFERENCE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")
_HEADING_STYLE = re.compile(r"^(?:heading|title)\s*([1-6])?$", re.IGNORECASE)
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CODE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs"}
_SYMBOL_PATTERNS = {
    ".js": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([\w$]+)"),
    ".jsx": re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([\w$]+)"),
    ".ts": re.compile(
        r"^\s*(?:export\s+)?(?:declare\s+)?(?:async\s+)?"
        r"(?:function|class|interface|type|enum|namespace)\s+([\w$]+)"
    ),
    ".tsx": re.compile(
        r"^\s*(?:export\s+)?(?:declare\s+)?(?:async\s+)?"
        r"(?:function|class|interface|type|enum|namespace)\s+([\w$]+)"
    ),
    ".java": re.compile(
        r"^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+)*"
        r"(?:class|interface|enum|record)\s+(\w+)"
    ),
    ".go": re.compile(r"^\s*(?:func|type)\s+(?:\([^)]*\)\s*)?(\w+)"),
    ".rs": re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?"
        r"(?:fn|struct|enum|trait|impl|mod|type)\s+([A-Za-z_]\w*)"
    ),
}
_TREE_SITTER_GRAMMAR_VERSIONS = {
    ".js": "tree-sitter-javascript/0.25.0",
    ".jsx": "tree-sitter-javascript/0.25.0",
    ".ts": "tree-sitter-typescript/0.23.2",
    ".tsx": "tree-sitter-typescript/0.23.2",
    ".java": "tree-sitter-java/0.23.5",
    ".go": "tree-sitter-go/0.25.0",
    ".rs": "tree-sitter-rust/0.24.2",
}
_TREE_SITTER_DECLARATIONS = {
    ".js": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "variable_declarator": "function",
    },
    ".jsx": {
        "class_declaration": "class",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "method_definition": "method",
        "variable_declarator": "function",
    },
    ".ts": {
        "abstract_class_declaration": "class",
        "class_declaration": "class",
        "enum_declaration": "enum",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "interface_declaration": "interface",
        "method_definition": "method",
        "method_signature": "method",
        "namespace_declaration": "namespace",
        "type_alias_declaration": "type",
        "variable_declarator": "function",
    },
    ".tsx": {
        "abstract_class_declaration": "class",
        "class_declaration": "class",
        "enum_declaration": "enum",
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "interface_declaration": "interface",
        "method_definition": "method",
        "method_signature": "method",
        "namespace_declaration": "namespace",
        "type_alias_declaration": "type",
        "variable_declarator": "function",
    },
    ".java": {
        "annotation_type_declaration": "annotation",
        "class_declaration": "class",
        "constructor_declaration": "method",
        "enum_declaration": "enum",
        "interface_declaration": "interface",
        "method_declaration": "method",
        "record_declaration": "record",
    },
    ".go": {
        "function_declaration": "function",
        "method_declaration": "method",
        "type_spec": "type",
    },
    ".rs": {
        "enum_item": "enum",
        "function_item": "function",
        "impl_item": "implementation",
        "mod_item": "module",
        "struct_item": "class",
        "trait_item": "trait",
        "type_item": "type",
        "union_item": "union",
    },
}
_TREE_SITTER_IMPORTS = {
    ".js": {"import_statement"},
    ".jsx": {"import_statement"},
    ".ts": {"import_statement"},
    ".tsx": {"import_statement"},
    ".java": {"import_declaration"},
    ".go": {"import_declaration"},
    ".rs": {"extern_crate_declaration", "use_declaration"},
}
_TREE_SITTER_CALLS = {
    ".js": {"call_expression", "new_expression"},
    ".jsx": {"call_expression", "new_expression"},
    ".ts": {"call_expression", "new_expression"},
    ".tsx": {"call_expression", "new_expression"},
    ".java": {"method_invocation", "object_creation_expression"},
    ".go": {"call_expression"},
    ".rs": {"call_expression", "macro_invocation"},
}
_TREE_SITTER_COMMENTS = {"comment", "block_comment", "line_comment"}


@dataclass(frozen=True, slots=True)
class SourceIRBuild:
    schema_version: str
    adapter: str
    adapter_version: str
    nodes: tuple[dict[str, Any], ...]
    fragment_logical_node_keys: tuple[tuple[str, ...], ...]
    quality_flags: tuple[str, ...]


class _RootLocatorIndex:
    def __init__(self, roots: list[dict[str, Any]]) -> None:
        self.roots = roots
        paragraph_intervals: list[tuple[int, int, dict[str, Any]]] = []
        page_intervals: list[tuple[int, int, dict[str, Any]]] = []
        for root in roots:
            locator = root["locator"]
            paragraph_match = re.search(r"paragraphs:(\d+)-(\d+)", locator)
            page_match = re.search(r"pages:(\d+)-(\d+)", locator)
            if paragraph_match:
                paragraph_intervals.append(
                    (int(paragraph_match.group(1)), int(paragraph_match.group(2)), root)
                )
            if page_match:
                page_intervals.append(
                    (int(page_match.group(1)), int(page_match.group(2)), root)
                )
        self.paragraph_intervals = sorted(paragraph_intervals, key=lambda item: item[0])
        self.paragraph_starts = [item[0] for item in self.paragraph_intervals]
        self.page_intervals = sorted(page_intervals, key=lambda item: item[0])
        self.page_starts = [item[0] for item in self.page_intervals]

    def resolve(self, block: TextBlock) -> dict[str, Any]:
        if len(self.roots) == 1:
            return self.roots[0]
        for value, starts, intervals in (
            (block.paragraph, self.paragraph_starts, self.paragraph_intervals),
            (block.page, self.page_starts, self.page_intervals),
        ):
            if value is None or not starts:
                continue
            index = bisect.bisect_right(starts, value) - 1
            if index >= 0:
                start, end, root = intervals[index]
                if start <= value <= end:
                    return root
        fallback = block.paragraph or block.page or 1
        return self.roots[min(len(self.roots) - 1, max(0, fallback - 1))]


class _StructuredHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._tag: str | None = None
        self._parts: list[str] = []
        self._source_characters = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        selected = tag.lower()
        if selected in {
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "li",
            "pre",
            "code",
            "td",
            "th",
            "figcaption",
        }:
            self._flush()
            self._tag = selected

    def handle_endtag(self, tag: str) -> None:
        if self._tag == tag.lower():
            self._flush()

    def handle_data(self, data: str) -> None:
        self._source_characters += len(data)
        if self._source_characters > _MAX_CHARACTERS:
            raise ExtractionError("HTML source exceeds the character bound")
        if self._tag is not None:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self._tag is not None:
            text = normalize_text(" ".join(self._parts))
            if text:
                if len(self.blocks) >= _MAX_BLOCKS:
                    raise ExtractionError("HTML source exceeds the structural block bound")
                self.blocks.append((self._tag, text))
        self._tag = None
        self._parts = []


def extract_extended_source(path: Path, format_name: str) -> ExtractionResult:
    if format_name == "PPTX":
        return _extract_pptx(path)
    if format_name == "XLSX":
        return _extract_xlsx(path)
    if format_name == "EPUB":
        return _extract_epub(path)
    raise ExtractionError(f"no extended source adapter for {format_name}")


def build_source_ir(
    path: Path,
    *,
    source_key: str,
    format_name: str,
    extraction: ExtractionResult,
    fragments: list[dict[str, Any]],
) -> SourceIRBuild:
    if len(fragments) > _MAX_BLOCKS:
        raise ExtractionError("Source IR fragment roots exceed the adapter bound")
    adapter = _adapter_name(path, format_name)
    adapter_version = _adapter_version(path)
    root_nodes: list[dict[str, Any]] = []
    fragment_keys: list[tuple[str, ...]] = []
    heading_levels = _fragment_heading_levels(path, fragments)
    heading_stack: list[tuple[int, str]] = []
    for ordinal, fragment in enumerate(fragments, start=1):
        logical_key = fragment["logical_node_key"]
        level = heading_levels[ordinal - 1]
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        parent_key = heading_stack[-1][1] if heading_stack else None
        heading_stack.append((level, logical_key))
        root_nodes.append(
            _node(
                logical_node_key=logical_key,
                parent_logical_node_key=parent_key,
                ordinal=ordinal,
                node_type="section",
                title=fragment.get("title"),
                text=fragment["text"],
                locator=fragment["locator"],
                source_span=fragment.get("source_span", {"locator": fragment["locator"]}),
                adapter=adapter,
                adapter_version=adapter_version,
                quality_flags=fragment.get("quality_flags", []),
            )
        )
        fragment_keys.append((logical_key,))
    child_specs, quality_flags = _rich_node_specs(
        path,
        format_name=format_name,
        extraction=extraction,
        fragment_roots=root_nodes,
        source_key=source_key,
    )
    nodes = [*root_nodes]
    for spec in child_specs:
        nodes.append(
            _node(
                **spec,
                ordinal=len(nodes) + 1,
                adapter=adapter,
                adapter_version=adapter_version,
            )
        )
    return SourceIRBuild(
        schema_version=SOURCE_ADAPTER_SCHEMA,
        adapter=adapter,
        adapter_version=adapter_version,
        nodes=tuple(nodes),
        fragment_logical_node_keys=tuple(fragment_keys),
        quality_flags=tuple(quality_flags),
    )


def _node(
    *,
    logical_node_key: str,
    parent_logical_node_key: str | None,
    ordinal: int,
    node_type: str,
    title: str | None,
    text: str,
    locator: str,
    source_span: dict[str, Any],
    adapter: str,
    adapter_version: str,
    quality_flags: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    normalized_text = normalize_text(text) if text else ""
    return {
        "logical_node_key": logical_node_key,
        "parent_logical_node_key": parent_logical_node_key,
        "ordinal": ordinal,
        "node_type": node_type,
        "title": normalize_text(title) if title else None,
        "text": normalized_text,
        "locator": locator,
        "source_span": source_span,
        "content_sha256": sha256_bytes(normalized_text.encode("utf-8")),
        "adapter": adapter,
        "adapter_version": adapter_version,
        "quality_flags": list(quality_flags),
        "instruction_risk": has_instruction_risk(normalized_text),
    }


def _adapter_name(path: Path, format_name: str) -> str:
    suffix = path.suffix.lower()
    if suffix in _CODE_SUFFIXES:
        return (
            "python-ast"
            if suffix in {".py", ".pyi"}
            else f"tree-sitter-{suffix.lstrip('.')}"
        )
    if suffix == ".sql":
        return "sqlglot-sql"
    return {
        "PPTX": "ooxml-presentation",
        "XLSX": "ooxml-workbook",
        "EPUB": "epub-spine",
        "PDF": "pdf-layout",
        "DOCX": "ooxml-document",
    }.get(format_name, f"structured-{suffix.lstrip('.') or 'text'}")


def _adapter_version(path: Path) -> str:
    if path.suffix.lower() == ".sql":
        return f"{SOURCE_IR_ADAPTER_VERSION};sqlglot/{_SQLGLOT_VERSION};dialect=generic"
    grammar = _TREE_SITTER_GRAMMAR_VERSIONS.get(path.suffix.lower())
    if grammar is None:
        return SOURCE_IR_ADAPTER_VERSION
    return f"{SOURCE_IR_ADAPTER_VERSION};tree-sitter/0.26.0;{grammar}"


def _fragment_heading_levels(path: Path, fragments: list[dict[str, Any]]) -> list[int]:
    if path.suffix.lower() not in {".md", ".markdown"}:
        return [1] * len(fragments)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return [1] * len(fragments)
    levels = [len(match.group(1)) for line in lines if (match := _MARKDOWN_HEADING.match(line))]
    if len(levels) < len(fragments):
        levels = [1, *levels]
    return (levels + [1] * len(fragments))[: len(fragments)]


def _rich_node_specs(
    path: Path,
    *,
    format_name: str,
    extraction: ExtractionResult,
    fragment_roots: list[dict[str, Any]],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return _markdown_specs(path, fragment_roots, source_key)
    if suffix in {".json", ".jsonl", ".yaml", ".yml", ".toml"}:
        return _structured_data_specs(path, fragment_roots[0], source_key)
    if suffix in _CODE_SUFFIXES:
        return _code_specs(path, fragment_roots[0], source_key)
    if suffix in {".csv", ".tsv"}:
        return _delimited_specs(path, fragment_roots[0], source_key)
    if suffix == ".sql":
        return _sql_specs(path, fragment_roots[0], source_key)
    if suffix in {".html", ".htm"}:
        return _html_specs(path, fragment_roots[0], source_key)
    return _block_specs(extraction, fragment_roots, source_key, format_name)


def _markdown_specs(
    path: Path,
    roots: list[dict[str, Any]],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    specs: list[dict[str, Any]] = []
    root_index = _RootLocatorIndex(roots)
    in_frontmatter = bool(lines and lines[0].strip() == "---")
    in_code = False
    code_start = 0
    code_lines: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        block = TextBlock(text=stripped or " ", paragraph=line_number)
        parent = root_index.resolve(block)["logical_node_key"]
        if line_number == 1 and in_frontmatter:
            continue
        if in_frontmatter:
            if stripped == "---":
                in_frontmatter = False
                continue
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                locator = f"frontmatter:{key.strip()};line:{line_number}"
                specs.append(
                    {
                        "logical_node_key": _logical_key(source_key, "property", locator),
                        "parent_logical_node_key": parent,
                        "node_type": "property",
                        "title": key.strip(),
                        "text": value.strip(),
                        "locator": locator,
                        "source_span": {
                            "line_start": line_number,
                            "line_end": line_number,
                        },
                        "quality_flags": [],
                    }
                )
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            if in_code:
                locator = f"lines:{code_start}-{line_number}"
                specs.append(
                    {
                        "logical_node_key": _logical_key(
                            source_key, "code_block", locator
                        ),
                        "parent_logical_node_key": parent,
                        "node_type": "code_block",
                        "title": None,
                        "text": "\n".join(code_lines),
                        "locator": locator,
                        "source_span": {
                            "line_start": code_start,
                            "line_end": line_number,
                        },
                        "quality_flags": [],
                    }
                )
                code_lines = []
                in_code = False
            else:
                in_code = True
                code_start = line_number
            continue
        if in_code:
            code_lines.append(line)
            continue
        heading = _MARKDOWN_HEADING.match(line)
        if heading:
            node_type = "heading"
            title = heading.group(2)
        elif re.match(r"^\s*(?:[-*+] |\d+[.)] )", line):
            node_type = "list_item"
            title = None
        elif "|" in stripped and stripped.count("|") >= 2:
            node_type = "table"
            title = None
        else:
            continue
        locator = f"line:{line_number}"
        specs.append(
            {
                "logical_node_key": _logical_key(
                    source_key, node_type, f"{locator}:{stripped[:80]}"
                ),
                "parent_logical_node_key": parent,
                "node_type": node_type,
                "title": title,
                "text": stripped,
                "locator": locator,
                "source_span": {
                    "line_start": line_number,
                    "line_end": line_number,
                },
                "quality_flags": [],
            }
        )
    if in_code:
        raise ExtractionError("Markdown contains an unterminated fenced code block")
    return specs, []


def _block_specs(
    extraction: ExtractionResult,
    roots: list[dict[str, Any]],
    source_key: str,
    format_name: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    specs: list[dict[str, Any]] = []
    root_index = _RootLocatorIndex(roots)
    for index, block in enumerate(extraction.blocks, start=1):
        parent = root_index.resolve(block)
        style = (block.style or "").casefold()
        node_type = block.kind or "paragraph"
        if node_type in {
            "sheet",
            "slide_title",
            "slide_text",
            "speaker_notes",
            "merged_cells",
            "figure",
        }:
            pass
        elif "table" in style or "table" in node_type:
            node_type = "table"
        elif _HEADING_STYLE.match(block.style or ""):
            node_type = "heading"
        elif format_name == "PDF":
            node_type = "layout_block"
        locator = _block_locator(block, index)
        specs.append(
            {
                "logical_node_key": _logical_key(source_key, node_type, locator),
                "parent_logical_node_key": parent["logical_node_key"],
                "node_type": node_type,
                "title": (
                    block.text
                    if node_type in {"heading", "sheet", "slide_title"}
                    else None
                ),
                "text": block.text,
                "locator": locator,
                "source_span": {
                    "page": block.page,
                    "paragraph": block.paragraph,
                    "style": block.style,
                    "kind": block.kind,
                    "source": block.source,
                },
                "quality_flags": [],
            }
        )
    flags = list(extraction.quality.warnings)
    if format_name == "PDF" and extraction.quality.needs_ocr:
        flags.append("ocr-required")
    return specs, flags


def _block_locator(block: TextBlock, index: int) -> str:
    if block.page is not None:
        return f"page:{block.page};block:{index}"
    if block.paragraph is not None:
        return f"paragraph:{block.paragraph}"
    return f"block:{index}"


def _structured_data_specs(
    path: Path,
    root: dict[str, Any],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    flags: list[str] = []
    try:
        if suffix == ".json":
            payload = strict_json_loads(text)
        elif suffix == ".jsonl":
            payload = [strict_json_loads(line) for line in text.splitlines() if line.strip()]
        elif suffix == ".toml":
            payload = tomllib.loads(text)
        else:
            try:
                import yaml
            except ImportError:
                return [], ["yaml-parser-unavailable:text-fallback"]
            payload = _closed_yaml_load(text, yaml)
    except (RecursionError, ValueError, TypeError) as error:
        raise ExtractionError(f"invalid structured source: {path.name}") from error
    specs: list[dict[str, Any]] = []
    seen_containers: set[int] = set()

    def walk(value: Any, pointer: str, parent_key: str, depth: int) -> None:
        if len(specs) >= _MAX_BLOCKS:
            raise ExtractionError("structured source exceeds the Source IR node bound")
        if depth > _MAX_STRUCTURE_DEPTH:
            raise ExtractionError("structured source exceeds the nesting-depth bound")
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in seen_containers:
                raise ExtractionError("structured source contains an alias or cycle")
            seen_containers.add(identity)
        if isinstance(value, dict) and any(
            not isinstance(key, str) or not 1 <= len(key) <= 2_000 for key in value
        ):
            raise ExtractionError("structured source object keys must be bounded strings")
        if isinstance(value, float) and not math.isfinite(value):
            raise ExtractionError("structured source contains a non-finite number")
        if not isinstance(value, (dict, list, str, int, float, bool, type(None))):
            raise ExtractionError("structured source contains a non-JSON scalar type")
        node_type = (
            "object"
            if isinstance(value, dict)
            else "array"
            if isinstance(value, list)
            else "scalar"
        )
        rendered = _structured_node_text(value)
        logical_key = _logical_key(source_key, node_type, pointer)
        specs.append(
            {
                "logical_node_key": logical_key,
                "parent_logical_node_key": parent_key,
                "node_type": node_type,
                "title": pointer,
                "text": rendered,
                "locator": f"path:{pointer}",
                "source_span": {"path": pointer},
                "quality_flags": [],
            }
        )
        if isinstance(value, dict):
            for key, child in value.items():
                escaped = key.replace("~", "~0").replace("/", "~1")
                walk(child, f"{pointer}/{escaped}", logical_key, depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{pointer}/{index}", logical_key, depth + 1)

    walk(payload, "$", root["logical_node_key"], 0)
    return specs, flags


def _closed_yaml_load(text: str, yaml: Any) -> Any:
    class ClosedSafeLoader(yaml.SafeLoader):
        def compose_node(self, parent: Any, index: Any) -> Any:
            if self.check_event(yaml.events.AliasEvent):
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    "YAML aliases are not accepted by the closed Source Adapter",
                    self.peek_event().start_mark,
                )
            return super().compose_node(parent, index)

    def unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict[str, Any]:
        loader.flatten_mapping(node)
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "YAML object keys must be strings",
                    key_node.start_mark,
                )
            if key in result:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate YAML object key: {key}",
                    key_node.start_mark,
                )
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    ClosedSafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        unique_mapping,
    )
    try:
        return yaml.load(text, Loader=ClosedSafeLoader)
    except yaml.YAMLError as error:
        raise ValueError("YAML does not match the closed Source Adapter grammar") from error


def _structured_node_text(value: Any) -> str:
    if isinstance(value, dict):
        keys = list(value)
        return canonical_json(
            {
                "item_count": len(keys),
                "keys": keys[:100],
                "keys_truncated": len(keys) > 100,
            }
        )
    if isinstance(value, list):
        return canonical_json(
            {
                "item_count": len(value),
                "item_types": sorted({type(item).__name__ for item in value[:100]}),
                "items_sampled": min(len(value), 100),
            }
        )
    rendered = canonical_json(value)
    return rendered if len(rendered) <= 20_000 else rendered[:20_000]


def _code_specs(
    path: Path,
    root: dict[str, Any],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    module_name = path.stem
    module_key = _logical_key(source_key, "module", module_name)
    specs: list[dict[str, Any]] = [
        {
            "logical_node_key": module_key,
            "parent_logical_node_key": root["logical_node_key"],
            "node_type": "module",
            "title": module_name,
            "text": text[:20_000],
            "locator": f"module:{module_name}",
            "source_span": {
                "line_start": 1,
                "line_end": max(1, len(lines)),
                "symbol_path": module_name,
            },
            "quality_flags": [],
        }
    ]
    if path.suffix.lower() in {".py", ".pyi"}:
        try:
            tree = ast.parse(text, filename=path.name)
        except SyntaxError as error:
            raise ExtractionError(f"invalid Python syntax: {path.name}:{error.lineno}") from error
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        symbols = sorted(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
        symbol_paths: dict[ast.AST, str] = {}
        symbol_keys: dict[ast.AST, str] = {}
        for node in symbols:
            parent_symbol = parents.get(node)
            while parent_symbol is not None and parent_symbol not in symbol_paths:
                parent_symbol = parents.get(parent_symbol)
            symbol_path = f"{symbol_paths.get(parent_symbol, module_name)}.{node.name}"
            symbol_paths[node] = symbol_path
            symbol_keys[node] = _logical_key(source_key, "symbol", symbol_path)
        for node in symbols:
            locator = f"lines:{node.lineno}-{getattr(node, 'end_lineno', node.lineno)}"
            parent_symbol = parents.get(node)
            while parent_symbol is not None and parent_symbol not in symbol_paths:
                parent_symbol = parents.get(parent_symbol)
            node_type = (
                "class"
                if isinstance(node, ast.ClassDef)
                else "method"
                if isinstance(parent_symbol, ast.ClassDef)
                else "function"
            )
            symbol_path = symbol_paths[node]
            specs.append(
                {
                    "logical_node_key": symbol_keys[node],
                    "parent_logical_node_key": symbol_keys.get(parent_symbol, module_key),
                    "node_type": node_type,
                    "title": symbol_path,
                    "text": "\n".join(
                        lines[node.lineno - 1 : getattr(node, "end_lineno", node.lineno)]
                    )[:20_000],
                    "locator": locator,
                    "source_span": {
                        "line_start": node.lineno,
                        "line_end": getattr(node, "end_lineno", node.lineno),
                        "symbol": node.name,
                        "symbol_path": symbol_path,
                        "docstring": ast.get_docstring(node, clean=False),
                    },
                    "quality_flags": [],
                }
            )
            references = sorted(
                {
                    reference
                    for call in ast.walk(node)
                    if isinstance(call, ast.Call)
                    if (reference := _python_reference_name(call.func)) is not None
                }
            )
            if references:
                specs.append(
                    {
                        "logical_node_key": _logical_key(
                            source_key,
                            "reference",
                            symbol_path,
                        ),
                        "parent_logical_node_key": symbol_keys[node],
                        "node_type": "reference",
                        "title": f"{symbol_path} references",
                        "text": ", ".join(references)[:20_000],
                        "locator": f"{locator};references",
                        "source_span": {
                            "line_start": node.lineno,
                            "line_end": getattr(node, "end_lineno", node.lineno),
                            "symbol_path": symbol_path,
                            "references": references[:500],
                        },
                        "quality_flags": [],
                    }
                )
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                locator = f"line:{node.lineno}"
                specs.append(
                    {
                        "logical_node_key": _logical_key(source_key, "import", locator),
                        "parent_logical_node_key": module_key,
                        "node_type": "import",
                        "title": None,
                        "text": lines[node.lineno - 1],
                        "locator": locator,
                        "source_span": {"line_start": node.lineno, "line_end": node.lineno},
                        "quality_flags": [],
                    }
                )
        return specs, []
    return _tree_sitter_code_specs(
        path,
        root,
        source_key,
        text=text,
        lines=lines,
        module_spec=specs[0],
    )


def _lexical_code_specs(
    path: Path,
    root: dict[str, Any],
    source_key: str,
    *,
    text: str,
    lines: list[str],
    module_spec: dict[str, Any],
    quality_flag: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    specs = [module_spec]
    module_name = path.stem
    module_key = module_spec["logical_node_key"]
    pattern = _SYMBOL_PATTERNS[path.suffix.lower()]
    for line_number, line in enumerate(lines, start=1):
        match = pattern.match(line)
        if match:
            symbol = match.group(1)
            locator = f"line:{line_number}"
            symbol_path = f"{module_name}.{symbol}"
            specs.append(
                {
                    "logical_node_key": _logical_key(source_key, "symbol", symbol_path),
                    "parent_logical_node_key": module_key,
                    "node_type": "symbol",
                    "title": symbol_path,
                    "text": line.strip(),
                    "locator": locator,
                    "source_span": {
                        "line_start": line_number,
                        "line_end": line_number,
                        "symbol_path": symbol_path,
                    },
                    "quality_flags": ["regex-symbol-boundary"],
                }
            )
    del text, root
    return specs, [quality_flag]


def _tree_sitter_language(suffix: str) -> Language:
    if suffix in {".js", ".jsx"}:
        capsule = tree_sitter_javascript.language()
    elif suffix == ".ts":
        capsule = tree_sitter_typescript.language_typescript()
    elif suffix == ".tsx":
        capsule = tree_sitter_typescript.language_tsx()
    elif suffix == ".java":
        capsule = tree_sitter_java.language()
    elif suffix == ".go":
        capsule = tree_sitter_go.language()
    elif suffix == ".rs":
        capsule = tree_sitter_rust.language()
    else:  # pragma: no cover - guarded by the closed suffix table
        raise ValueError("unsupported Tree-sitter language")
    return Language(capsule)


def _tree_node_text(node: Node, source: bytes, *, maximum: int = 20_000) -> str:
    value = source[node.start_byte : node.end_byte].decode("utf-8", errors="strict")
    return value[:maximum]


def _tree_symbol_name(node: Node, source: bytes) -> str | None:
    selected = node.child_by_field_name("name")
    if selected is None and node.type == "impl_item":
        selected = node.child_by_field_name("type")
    if selected is None:
        return None
    name = normalize_text(_tree_node_text(selected, source, maximum=500))
    return name if name else None


def _tree_callable(node: Node) -> bool:
    if node.type != "variable_declarator":
        return True
    value = node.child_by_field_name("value")
    return value is not None and value.type in {
        "arrow_function",
        "function_expression",
        "generator_function",
    }


def _tree_signature(node: Node, source: bytes) -> str:
    parameters = node.child_by_field_name("parameters")
    if parameters is None:
        return ""
    return normalize_text(_tree_node_text(parameters, source, maximum=500))


def _tree_preceding_doc(node: Node, source: bytes) -> str | None:
    current = node
    comments: list[str] = []
    total = 0
    for _ in range(8):
        sibling = current.prev_named_sibling
        if sibling is None and current.parent is not None and current.parent.type in {
            "decorated_definition",
            "export_statement",
        }:
            current = current.parent
            sibling = current.prev_named_sibling
        if sibling is None or sibling.type not in _TREE_SITTER_COMMENTS:
            break
        value = normalize_text(_tree_node_text(sibling, source, maximum=4_000))
        total += len(value)
        if total > 4_000:
            break
        comments.append(value)
        current = sibling
    if not comments:
        return None
    return "\n".join(reversed(comments))


def _tree_reference_name(node: Node, source: bytes) -> str | None:
    selected = (
        node.child_by_field_name("function")
        or node.child_by_field_name("name")
        or node.child_by_field_name("type")
        or node.child_by_field_name("macro")
    )
    if selected is None:
        return None
    value = normalize_text(_tree_node_text(selected, source, maximum=300))
    if not value or len(value) > 200 or "\n" in value:
        return None
    return value


def _tree_references(
    declaration: Node,
    source: bytes,
    *,
    declaration_ids: set[int],
    call_types: set[str],
) -> list[str]:
    references: set[str] = set()
    stack = list(reversed(declaration.named_children))
    visited = 0
    while stack and len(references) < 500:
        node = stack.pop()
        visited += 1
        if visited > _MAX_CODE_TREE_NODES:
            break
        if node.id in declaration_ids:
            continue
        if node.type in call_types:
            reference = _tree_reference_name(node, source)
            if reference is not None:
                references.add(reference)
        stack.extend(reversed(node.named_children))
    return sorted(references)


def _tree_sitter_code_specs(
    path: Path,
    root: dict[str, Any],
    source_key: str,
    *,
    text: str,
    lines: list[str],
    module_spec: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    suffix = path.suffix.lower()
    source = text.encode("utf-8")
    if len(source) > _MAX_CODE_PARSER_BYTES:
        return _lexical_code_specs(
            path,
            root,
            source_key,
            text=text,
            lines=lines,
            module_spec=module_spec,
            quality_flag="code-exceeds-tree-sitter-bound-uses-lexical-fallback",
        )
    tree = Parser(_tree_sitter_language(suffix)).parse(source)
    if tree is None:
        raise ExtractionError("Tree-sitter did not return a syntax tree")
    if tree.root_node.descendant_count > _MAX_CODE_TREE_NODES:
        return _lexical_code_specs(
            path,
            root,
            source_key,
            text=text,
            lines=lines,
            module_spec=module_spec,
            quality_flag="code-tree-exceeds-node-bound-uses-lexical-fallback",
        )

    declaration_types = _TREE_SITTER_DECLARATIONS[suffix]
    import_types = _TREE_SITTER_IMPORTS[suffix]
    declarations: list[tuple[Node, str]] = []
    imports: list[Node] = []
    stack = [tree.root_node]
    visited = 0
    while stack:
        node = stack.pop()
        visited += 1
        if visited > _MAX_CODE_TREE_NODES:
            raise ExtractionError("Tree-sitter traversal exceeded its node bound")
        node_kind = declaration_types.get(node.type)
        if node_kind is not None and _tree_callable(node):
            if suffix == ".rs" and node.type == "function_item":
                parent = node.parent
                while parent is not None and parent.type in {"declaration_list"}:
                    parent = parent.parent
                if parent is not None and parent.type in {"impl_item", "trait_item"}:
                    node_kind = "method"
            declarations.append((node, node_kind))
        if node.type in import_types:
            imports.append(node)
        stack.extend(reversed(node.named_children))
    if len(declarations) + len(imports) > _MAX_CODE_SYMBOLS:
        return _lexical_code_specs(
            path,
            root,
            source_key,
            text=text,
            lines=lines,
            module_spec=module_spec,
            quality_flag="code-symbol-inventory-exceeds-bound-uses-lexical-fallback",
        )
    declarations.sort(key=lambda item: (item[0].start_byte, item[0].end_byte, item[0].type))
    imports.sort(key=lambda node: (node.start_byte, node.end_byte, node.type))

    module_name = path.stem
    module_key = module_spec["logical_node_key"]
    declaration_by_id = {node.id: (node, kind) for node, kind in declarations}
    symbol_paths: dict[int, str] = {}
    symbol_keys: dict[int, str] = {}
    seen_symbol_keys: set[str] = set()
    duplicate_keys: set[str] = set()
    quality_flags = ["compiler-grade-tree-sitter-ast"]
    if tree.root_node.has_error:
        quality_flags.append("tree-sitter-recovered-syntax-errors")
    for node, kind in declarations:
        name = _tree_symbol_name(node, source)
        if name is None:
            continue
        parent = node.parent
        while parent is not None and parent.id not in declaration_by_id:
            parent = parent.parent
        parent_path = symbol_paths.get(parent.id if parent is not None else -1, module_name)
        signature = _tree_signature(node, source)
        if node.type == "impl_item":
            header = normalize_text(
                _tree_node_text(node, source, maximum=1_000).split("{", 1)[0]
            )
            segment = f"impl[{header}]"
        else:
            segment = f"{name}{signature}" if signature else name
        symbol_path = f"{parent_path}.{segment}"
        identity = f"{suffix}:{kind}:{symbol_path}"
        logical_key = _logical_key(source_key, "symbol", identity)
        if logical_key in seen_symbol_keys:
            duplicate_keys.add(logical_key)
            logical_key = _logical_key(
                source_key,
                "symbol",
                f"{identity}:line:{node.start_point.row + 1}",
            )
        symbol_paths[node.id] = symbol_path
        symbol_keys[node.id] = logical_key
        seen_symbol_keys.add(logical_key)
    if duplicate_keys:
        quality_flags.append("duplicate-symbol-required-line-disambiguation")

    records: list[tuple[int, dict[str, Any]]] = []
    seen_imports: set[str] = set()
    for node in imports:
        import_text = normalize_text(_tree_node_text(node, source))
        if not import_text or import_text in seen_imports:
            continue
        seen_imports.add(import_text)
        line_start = node.start_point.row + 1
        line_end = max(line_start, node.end_point.row + 1)
        records.append(
            (
                node.start_byte,
                {
                    "logical_node_key": _logical_key(
                        source_key,
                        "import",
                        sha256_bytes(import_text.encode("utf-8")),
                    ),
                    "parent_logical_node_key": module_key,
                    "node_type": "import",
                    "title": import_text[:500],
                    "text": _tree_node_text(node, source),
                    "locator": f"lines:{line_start}-{line_end}",
                    "source_span": {
                        "line_start": line_start,
                        "line_end": line_end,
                        "symbol_path": module_name,
                        "import": import_text[:2_000],
                    },
                    "quality_flags": [],
                },
            )
        )

    declaration_ids = set(declaration_by_id)
    for node, kind in declarations:
        logical_key = symbol_keys.get(node.id)
        symbol_path = symbol_paths.get(node.id)
        if logical_key is None or symbol_path is None:
            continue
        parent = node.parent
        while parent is not None and parent.id not in declaration_by_id:
            parent = parent.parent
        line_start = node.start_point.row + 1
        line_end = max(line_start, node.end_point.row + 1)
        references = _tree_references(
            node,
            source,
            declaration_ids=declaration_ids,
            call_types=_TREE_SITTER_CALLS[suffix],
        )
        records.append(
            (
                node.start_byte,
                {
                    "logical_node_key": logical_key,
                    "parent_logical_node_key": symbol_keys.get(
                        parent.id if parent is not None else -1,
                        module_key,
                    ),
                    "node_type": kind,
                    "title": symbol_path[:500],
                    "text": _tree_node_text(node, source),
                    "locator": f"lines:{line_start}-{line_end}",
                    "source_span": {
                        "line_start": line_start,
                        "line_end": line_end,
                        "symbol": _tree_symbol_name(node, source),
                        "symbol_path": symbol_path[:2_000],
                        "signature": _tree_signature(node, source),
                        "docstring": _tree_preceding_doc(node, source),
                    },
                    "quality_flags": [],
                },
            )
        )
        if references:
            records.append(
                (
                    node.start_byte + 1,
                    {
                        "logical_node_key": _logical_key(
                            source_key,
                            "reference",
                            logical_key,
                        ),
                        "parent_logical_node_key": logical_key,
                        "node_type": "reference",
                        "title": f"{symbol_path} references"[:500],
                        "text": ", ".join(references)[:20_000],
                        "locator": f"lines:{line_start}-{line_end};references",
                        "source_span": {
                            "line_start": line_start,
                            "line_end": line_end,
                            "symbol_path": symbol_path[:2_000],
                            "references": references,
                        },
                        "quality_flags": [],
                    },
                )
            )
    records.sort(key=lambda item: (item[0], item[1]["logical_node_key"]))
    return [module_spec, *(record for _, record in records)], quality_flags


def _python_reference_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _python_reference_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _delimited_specs(
    path: Path,
    root: dict[str, Any],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    specs: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row_number, row in enumerate(csv.reader(stream, delimiter=delimiter), start=1):
            locator = f"row:{row_number}"
            text = " | ".join(row)
            specs.append(
                {
                    "logical_node_key": _logical_key(source_key, "row", locator),
                    "parent_logical_node_key": root["logical_node_key"],
                    "node_type": "header" if row_number == 1 else "row",
                    "title": None,
                    "text": text,
                    "locator": locator,
                    "source_span": {"row": row_number, "column_count": len(row)},
                    "quality_flags": [],
                }
            )
            if len(specs) > _MAX_BLOCKS:
                raise ExtractionError("delimited source exceeds the row bound")
    return specs, []


def _sql_specs(
    path: Path,
    root: dict[str, Any],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    if sqlglot.__version__ != _SQLGLOT_VERSION:
        raise ExtractionError("SQLGlot runtime does not match the Source Adapter identity")
    if len(text.encode("utf-8")) > _MAX_SQL_PARSER_BYTES:
        specs, _ = _lexical_sql_specs(text, root, source_key)
        return specs, ["sql-exceeds-parser-bound:lexical-fallback"]
    try:
        tokens = sqlglot.tokenize(text)
        expressions = sqlglot.parse(text, error_level=ErrorLevel.RAISE)
    except (ParseError, RecursionError, TokenError):
        specs, _ = _lexical_sql_specs(text, root, source_key)
        return specs, ["sqlglot-parse-failed:lexical-fallback"]

    chunks: list[list[Any]] = [[]]
    for index, token in enumerate(tokens):
        if token.token_type == TokenType.SEMICOLON:
            if token.comments:
                chunks.append([token])
            if index < len(tokens) - 1:
                chunks.append([])
        else:
            chunks[-1].append(token)
    if len(chunks) != len(expressions):
        raise ExtractionError("SQLGlot statement inventory does not match its token stream")
    parsed = [
        (expression, chunk)
        for expression, chunk in zip(expressions, chunks, strict=True)
        if expression is not None and not isinstance(expression, exp.Semicolon)
    ]
    if any(isinstance(expression, exp.Command) for expression, _ in parsed):
        specs, _ = _lexical_sql_specs(text, root, source_key)
        return specs, ["sqlglot-unsupported-command:lexical-fallback"]
    tree_node_count = sum(
        1 for expression, _ in parsed for _node_value in expression.walk()
    )
    if tree_node_count > _MAX_SQL_TREE_NODES:
        specs, _ = _lexical_sql_specs(text, root, source_key)
        return specs, ["sql-tree-exceeds-bound:lexical-fallback"]

    specs: list[dict[str, Any]] = []
    symbol_count = 0
    for statement_index, (expression, chunk) in enumerate(parsed, start=1):
        if not chunk:
            raise ExtractionError("SQLGlot returned a statement without source tokens")
        start = chunk[0].start
        end = chunk[-1].end
        line_start = chunk[0].line
        line_end = chunk[-1].line
        locator = f"statement:{statement_index};lines:{line_start}-{line_end}"
        statement_key = _logical_key(source_key, "statement", str(statement_index))
        statement_text = text[start : end + 1].strip()
        specs.append(
            {
                "logical_node_key": statement_key,
                "parent_logical_node_key": root["logical_node_key"],
                "node_type": "statement",
                "title": expression.key.upper(),
                "text": statement_text[:20_000],
                "locator": locator,
                "source_span": {
                    "line_start": line_start,
                    "line_end": line_end,
                    "character_start": start,
                    "character_end": end + 1,
                },
                "quality_flags": ["compiler-grade-sqlglot-ast"],
            }
        )
        entities: list[tuple[str, exp.Expression, str]] = []
        entities.extend(
            ("cte", node, node.alias_or_name or node.sql())
            for node in expression.find_all(exp.CTE)
        )
        entities.extend(
            ("table", node, node.sql()) for node in expression.find_all(exp.Table)
        )
        entities.extend(
            ("column", node, node.sql()) for node in expression.find_all(exp.Column)
        )
        seen_entities: set[tuple[str, str]] = set()
        for node_type, node, rendered in entities:
            identity = (node_type, rendered.casefold())
            if identity in seen_entities:
                continue
            seen_entities.add(identity)
            symbol_count += 1
            if symbol_count > _MAX_SQL_SYMBOLS or len(specs) >= _MAX_BLOCKS:
                raise ExtractionError("SQL Source IR exceeds the structural symbol bound")
            node_text, span = _sql_expression_source(node, text)
            entity_locator = f"statement:{statement_index};{node_type}:{symbol_count}"
            specs.append(
                {
                    "logical_node_key": _logical_key(
                        source_key,
                        node_type,
                        f"{statement_index}:{rendered}",
                    ),
                    "parent_logical_node_key": statement_key,
                    "node_type": node_type,
                    "title": rendered[:500],
                    "text": node_text[:20_000],
                    "locator": entity_locator,
                    "source_span": {
                        **span,
                        "statement": statement_index,
                    },
                    "quality_flags": ["compiler-grade-sqlglot-ast"],
                }
            )
    return specs, ["compiler-grade-sqlglot-ast"]


def _sql_expression_source(
    expression: exp.Expression,
    source: str,
) -> tuple[str, dict[str, int]]:
    positions = [
        (
            value.meta["start"],
            value.meta["end"],
            value.meta["line"],
        )
        for value in expression.walk()
        if all(
            isinstance(value.meta.get(field), int)
            for field in ("start", "end", "line")
        )
    ]
    if not positions:
        rendered = expression.sql()
        return rendered, {"line_start": 1, "line_end": 1}
    start = min(position[0] for position in positions)
    end = max(position[1] for position in positions)
    line_start = min(position[2] for position in positions)
    line_end = max(position[2] for position in positions)
    return source[start : end + 1], {
        "line_start": line_start,
        "line_end": line_end,
        "character_start": start,
        "character_end": end + 1,
    }


def _lexical_sql_specs(
    text: str,
    root: dict[str, Any],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    specs: list[dict[str, Any]] = []
    offset = 0
    for index, statement in enumerate(re.split(r";(?=(?:[^']|'[^']*')*$)", text), start=1):
        statement = statement.strip()
        if not statement:
            continue
        line_start = text.count("\n", 0, offset) + 1
        offset = text.find(statement, offset) + len(statement)
        line_end = line_start + statement.count("\n")
        locator = f"statement:{index};lines:{line_start}-{line_end}"
        statement_key = _logical_key(source_key, "statement", str(index))
        if len(specs) >= _MAX_BLOCKS:
            raise ExtractionError("SQL Source IR exceeds the node bound")
        specs.append(
            {
                "logical_node_key": statement_key,
                "parent_logical_node_key": root["logical_node_key"],
                "node_type": "statement",
                "title": statement.split(None, 1)[0].upper(),
                "text": statement[:20_000],
                "locator": locator,
                "source_span": {"line_start": line_start, "line_end": line_end},
                "quality_flags": ["closed-grammar-sql-boundary"],
            }
        )
        entities: list[tuple[str, str]] = []
        entities.extend(
            ("cte", match.group(1))
            for match in re.finditer(
                r"(?i)\bwith\s+([A-Za-z_][\w$]*)\s+as\s*\(",
                statement,
            )
        )
        entities.extend(
            ("table", match.group(1))
            for match in re.finditer(
                r"(?i)\b(?:from|join|into|update)\s+([A-Za-z_][\w.$]*)",
                statement,
            )
        )
        select = re.search(r"(?is)\bselect\s+(.+?)\s+from\b", statement)
        if select is not None:
            for raw_column in select.group(1).split(",")[:500]:
                column = normalize_text(raw_column)
                if column:
                    entities.append(("column", column))
        seen_entities: set[tuple[str, str]] = set()
        for entity_number, (node_type, value) in enumerate(entities, start=1):
            identity = (node_type, value.casefold())
            if identity in seen_entities:
                continue
            seen_entities.add(identity)
            if len(specs) >= _MAX_BLOCKS:
                raise ExtractionError("SQL Source IR exceeds the node bound")
            entity_locator = f"statement:{index};{node_type}:{entity_number}"
            specs.append(
                {
                    "logical_node_key": _logical_key(
                        source_key,
                        node_type,
                        f"{index}:{value}",
                    ),
                    "parent_logical_node_key": statement_key,
                    "node_type": node_type,
                    "title": value[:500],
                    "text": value[:20_000],
                    "locator": entity_locator,
                    "source_span": {
                        "line_start": line_start,
                        "line_end": line_end,
                        "statement": index,
                    },
                    "quality_flags": ["lexical-sql-symbol"],
                }
            )
    return specs, ["sql-symbol-resolution-is-lexical"]


def _html_specs(
    path: Path,
    root: dict[str, Any],
    source_key: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    parser = _StructuredHTMLParser()
    parser.feed(path.read_text(encoding="utf-8-sig"))
    parser.close()
    specs: list[dict[str, Any]] = []
    for index, (tag, text) in enumerate(parser.blocks, start=1):
        locator = f"html:{tag}:{index}"
        specs.append(
            {
                "logical_node_key": _logical_key(source_key, tag, str(index)),
                "parent_logical_node_key": root["logical_node_key"],
                "node_type": "heading" if tag.startswith("h") else tag,
                "title": text if tag.startswith("h") else None,
                "text": text,
                "locator": locator,
                "source_span": {"tag": tag, "occurrence": index},
                "quality_flags": [],
            }
        )
    return specs, []


def _logical_key(source_key: str, node_type: str, locator: str) -> str:
    return f"node:{stable_id('logicalnode', source_key, node_type, locator)}"


def _safe_member(archive: zipfile.ZipFile, name: str) -> bytes:
    if len(archive.infolist()) > _MAX_ARCHIVE_MEMBERS:
        raise ExtractionError("document archive exceeds the member bound")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        raise ExtractionError("document archive contains an unsafe member path")
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ExtractionError(f"document archive member is missing: {name}") from error
    if info.file_size > _MAX_ARCHIVE_MEMBER_BYTES:
        raise ExtractionError(f"document archive member is too large: {name}")
    if info.compress_size and info.file_size / info.compress_size > 200:
        raise ExtractionError(f"document archive member has an unsafe ratio: {name}")
    return archive.read(info)


def _validate_archive_inventory(archive: zipfile.ZipFile, *, label: str) -> None:
    infos = archive.infolist()
    if not 1 <= len(infos) <= _MAX_ARCHIVE_MEMBERS:
        raise ExtractionError(f"{label} archive member inventory exceeds its bound")
    names: set[str] = set()
    expanded_bytes = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        mode = info.external_attr >> 16
        if (
            not name
            or name in names
            or name.startswith("/")
            or "\\" in name
            or ".." in path.parts
            or path.as_posix() != name.rstrip("/")
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
            or info.flag_bits & 0x1
            or stat.S_ISLNK(mode)
        ):
            raise ExtractionError(f"{label} archive contains an unsafe or duplicate member")
        names.add(name)
        if info.is_dir():
            continue
        if info.file_size > _MAX_ARCHIVE_MEMBER_BYTES:
            raise ExtractionError(f"{label} archive member exceeds its size bound")
        if info.compress_size and info.file_size / info.compress_size > 200:
            raise ExtractionError(f"{label} archive member has an unsafe compression ratio")
        expanded_bytes += info.file_size
        if expanded_bytes > _MAX_ARCHIVE_EXPANDED_BYTES:
            raise ExtractionError(f"{label} archive expanded size exceeds its bound")


def _xml(payload: bytes, *, label: str) -> ET.Element:
    if len(payload) > _MAX_ARCHIVE_MEMBER_BYTES:
        raise ExtractionError(f"{label} XML exceeds the byte bound")
    try:
        root = DefusedET.fromstring(payload)
    except (ET.ParseError, DefusedXmlException) as error:
        raise ExtractionError(f"invalid {label} XML") from error
    node_count = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > _MAX_XML_NODES:
            raise ExtractionError(f"{label} XML exceeds the node bound")
        children = list(node)
        if children and depth >= _MAX_XML_DEPTH:
            raise ExtractionError(f"{label} XML exceeds the depth bound")
        stack.extend((child, depth + 1) for child in children)
    return root


def _package_relationships(
    archive: zipfile.ZipFile,
    owner: str,
    *,
    label: str,
    required: bool,
) -> dict[str, dict[str, str]]:
    owner_path = PurePosixPath(owner)
    relationship_member = (
        owner_path.parent / "_rels" / f"{owner_path.name}.rels"
    ).as_posix()
    if relationship_member not in archive.namelist():
        if required:
            raise ExtractionError(f"{label} relationship inventory is missing")
        return {}
    root = _xml(_safe_member(archive, relationship_member), label=f"{label} relationships")
    relationships: dict[str, dict[str, str]] = {}
    for node in root:
        if not node.tag.endswith("}Relationship"):
            continue
        relationship_id = node.attrib.get("Id", "")
        target = node.attrib.get("Target", "")
        relationship_type = node.attrib.get("Type", "")
        target_mode = node.attrib.get("TargetMode", "Internal")
        if (
            not 1 <= len(relationship_id) <= 500
            or relationship_id in relationships
            or not 1 <= len(target) <= 4_000
            or not 1 <= len(relationship_type) <= 2_000
            or target_mode not in {"Internal", "External"}
        ):
            raise ExtractionError(f"{label} relationship inventory is invalid")
        relationships[relationship_id] = {
            "target": target,
            "type": relationship_type,
            "target_mode": target_mode,
        }
    if len(relationships) > _MAX_ARCHIVE_MEMBERS:
        raise ExtractionError(f"{label} relationship inventory exceeds its bound")
    return relationships


def _resolve_package_target(owner: str, target: str, *, label: str) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ExtractionError(f"{label} contains a non-package target")
    decoded = unquote(parsed.path)
    if (
        not decoded
        or "\\" in decoded
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
    ):
        raise ExtractionError(f"{label} contains an unsafe package target")
    parts = [] if decoded.startswith("/") else list(PurePosixPath(owner).parent.parts)
    for part in decoded.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ExtractionError(f"{label} escapes the document package")
            parts.pop()
            continue
        parts.append(part)
    member = PurePosixPath(*parts).as_posix()
    if not member or PurePosixPath(member).is_absolute() or ".." in PurePosixPath(member).parts:
        raise ExtractionError(f"{label} contains an unsafe package target")
    return member


def _quality(
    blocks: list[TextBlock],
    *,
    extractor: str,
    page_count: int | None = None,
) -> ExtractionResult:
    character_count = sum(len(block.text) for block in blocks)
    if character_count < 20:
        raise ExtractionError(f"{extractor} source contains too little text")
    if len(blocks) > _MAX_BLOCKS or character_count > _MAX_CHARACTERS:
        raise ExtractionError(f"{extractor} source exceeds the extraction budget")
    return ExtractionResult(
        blocks=tuple(blocks),
        quality=ExtractionQuality(
            extractor=extractor,
            extractor_version=SOURCE_IR_ADAPTER_VERSION,
            block_count=len(blocks),
            page_count=page_count,
            character_count=character_count,
        ),
    )


def _pptx_slide_names(archive: zipfile.ZipFile) -> list[str]:
    owner = "ppt/presentation.xml"
    if owner not in archive.namelist():
        raise ExtractionError("PPTX presentation inventory is missing")
    presentation = _xml(_safe_member(archive, owner), label="PPTX presentation")
    relationships = _package_relationships(
        archive,
        owner,
        label="PPTX presentation",
        required=True,
    )
    values: list[str] = []
    seen_relationship_ids: set[str] = set()
    for slide in (node for node in presentation.iter() if node.tag.endswith("}sldId")):
        relationship_id = next(
            (value for key, value in slide.attrib.items() if key.endswith("}id")),
            None,
        )
        relationship = relationships.get(relationship_id or "")
        if (
            not relationship_id
            or relationship_id in seen_relationship_ids
            or relationship is None
            or relationship["target_mode"] != "Internal"
            or not relationship["type"].endswith("/slide")
        ):
            raise ExtractionError("PPTX slide relationship is missing or invalid")
        member = _resolve_package_target(
            owner,
            relationship["target"],
            label="PPTX slide relationship",
        )
        if (
            member not in archive.namelist()
            or not member.startswith("ppt/slides/")
            or not member.endswith(".xml")
            or member in values
        ):
            raise ExtractionError("PPTX slide target is missing or duplicated")
        seen_relationship_ids.add(relationship_id)
        values.append(member)
    if not values:
        raise ExtractionError("PPTX presentation contains no ordered slides")
    return values


def _pptx_paragraphs(node: ET.Element) -> list[str]:
    values: list[str] = []
    for paragraph in (item for item in node.iter() if item.tag.endswith("}p")):
        parts: list[str] = []
        for value in paragraph.iter():
            if value.tag.endswith("}t") and value.text:
                parts.append(value.text)
            elif value.tag.endswith("}tab"):
                parts.append("\t")
            elif value.tag.endswith("}br"):
                parts.append("\n")
        rendered = normalize_text("".join(parts))
        if rendered:
            values.append(rendered)
    return values


def _pptx_placeholder_type(node: ET.Element) -> str | None:
    return next(
        (
            value.attrib.get("type", "body")
            for value in node.iter()
            if value.tag.endswith("}ph")
        ),
        None,
    )


def _pptx_figure_description(node: ET.Element) -> str | None:
    for value in node.iter():
        if not value.tag.endswith("}cNvPr"):
            continue
        description = normalize_text(value.attrib.get("descr", ""))
        name = normalize_text(value.attrib.get("name", ""))
        selected = description or name
        if selected and not selected.casefold().startswith(("picture ", "image ")):
            return selected
    return None


def _pptx_table_text(table: ET.Element) -> str:
    rows: list[str] = []
    for row in (node for node in table if node.tag.endswith("}tr")):
        cells: list[str] = []
        for cell in (node for node in row if node.tag.endswith("}tc")):
            cells.append(" / ".join(_pptx_paragraphs(cell)))
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _pptx_slide_blocks(root: ET.Element, *, slide_number: int) -> list[TextBlock]:
    tree = next((node for node in root.iter() if node.tag.endswith("}spTree")), None)
    if tree is None:
        raise ExtractionError("PPTX slide shape tree is missing")
    has_explicit_title = any(
        _pptx_placeholder_type(node) in {"title", "ctrTitle"}
        for node in tree.iter()
        if node.tag.endswith("}sp")
    )
    blocks: list[TextBlock] = []
    counters = {"object": 0, "paragraph": 0, "table": 0, "figure": 0}
    fallback_title_used = False

    def walk(parent: ET.Element) -> None:
        nonlocal fallback_title_used
        for child in parent:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name == "grpSp":
                walk(child)
                continue
            if local_name not in {"sp", "graphicFrame", "pic"}:
                continue
            counters["object"] += 1
            object_number = counters["object"]
            if local_name == "sp":
                placeholder = _pptx_placeholder_type(child)
                for index, value in enumerate(_pptx_paragraphs(child), start=1):
                    counters["paragraph"] += 1
                    explicit_title = placeholder in {"title", "ctrTitle"} and index == 1
                    fallback_title = (
                        not has_explicit_title and not fallback_title_used and index == 1
                    )
                    is_title = explicit_title or fallback_title
                    fallback_title_used = fallback_title_used or fallback_title
                    blocks.append(
                        TextBlock(
                            text=value,
                            page=slide_number,
                            paragraph=counters["paragraph"],
                            style="Heading 1" if is_title else "slide-body",
                            kind="slide_title" if is_title else "slide_text",
                            source=(
                                f"pptx:slide:{slide_number};object:{object_number};"
                                f"paragraph:{index}"
                            ),
                        )
                    )
                continue
            table = next(
                (node for node in child.iter() if node.tag.endswith("}tbl")),
                None,
            )
            if table is not None:
                rendered = _pptx_table_text(table)
                if rendered:
                    counters["table"] += 1
                    blocks.append(
                        TextBlock(
                            text=rendered,
                            page=slide_number,
                            style="slide-table",
                            kind="table",
                            source=(
                                f"pptx:slide:{slide_number};object:{object_number};"
                                f"table:{counters['table']}"
                            ),
                        )
                    )
                continue
            description = _pptx_figure_description(child)
            if description:
                counters["figure"] += 1
                blocks.append(
                    TextBlock(
                        text=description,
                        page=slide_number,
                        style="slide-figure",
                        kind="figure",
                        source=(
                            f"pptx:slide:{slide_number};object:{object_number};"
                            f"figure:{counters['figure']}"
                        ),
                    )
                )

    walk(tree)
    return blocks


def _pptx_notes_member(archive: zipfile.ZipFile, slide_member: str) -> str | None:
    relationships = _package_relationships(
        archive,
        slide_member,
        label="PPTX slide",
        required=False,
    )
    notes = [
        relationship
        for relationship in relationships.values()
        if relationship["type"].endswith("/notesSlide")
    ]
    if len(notes) > 1:
        raise ExtractionError("PPTX slide has ambiguous speaker-notes relationships")
    if not notes:
        return None
    relationship = notes[0]
    if relationship["target_mode"] != "Internal":
        raise ExtractionError("PPTX speaker notes cannot use an external relationship")
    member = _resolve_package_target(
        slide_member,
        relationship["target"],
        label="PPTX speaker-notes relationship",
    )
    if (
        member not in archive.namelist()
        or not member.startswith("ppt/notesSlides/")
        or not member.endswith(".xml")
    ):
        raise ExtractionError("PPTX speaker-notes target is missing or invalid")
    return member


def _pptx_notes_text(root: ET.Element) -> str:
    values: list[str] = []
    for shape in (node for node in root.iter() if node.tag.endswith("}sp")):
        if _pptx_placeholder_type(shape) in {"dt", "ftr", "hdr", "sldNum"}:
            continue
        values.extend(_pptx_paragraphs(shape))
    return normalize_text("\n".join(values))


def _extract_pptx(path: Path) -> ExtractionResult:
    blocks: list[TextBlock] = []
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_archive_inventory(archive, label="PPTX")
            slide_names = _pptx_slide_names(archive)
            seen_notes: set[str] = set()
            for slide_number, name in enumerate(slide_names, start=1):
                root = _xml(_safe_member(archive, name), label="PPTX slide")
                blocks.extend(_pptx_slide_blocks(root, slide_number=slide_number))
                notes_member = _pptx_notes_member(archive, name)
                if notes_member is None:
                    continue
                if notes_member in seen_notes:
                    raise ExtractionError("PPTX speaker-notes target is duplicated")
                seen_notes.add(notes_member)
                notes_root = _xml(
                    _safe_member(archive, notes_member),
                    label="PPTX notes",
                )
                notes_text = _pptx_notes_text(notes_root)
                if notes_text:
                    blocks.append(
                        TextBlock(
                            text=notes_text,
                            page=slide_number,
                            style="speaker-notes",
                            kind="speaker_notes",
                            source=f"pptx:slide:{slide_number};notes",
                        )
                    )
    except (OSError, zipfile.BadZipFile) as error:
        raise ExtractionError(f"invalid PPTX: {path.name}") from error
    return _quality(
        blocks,
        extractor="ooxml-presentation",
        page_count=len(slide_names),
    )


def _extract_xlsx(path: Path) -> ExtractionResult:
    blocks: list[TextBlock] = []
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_archive_inventory(archive, label="XLSX")
            shared: list[str] = []
            shared_characters = 0
            if "xl/sharedStrings.xml" in archive.namelist():
                root = _xml(
                    _safe_member(archive, "xl/sharedStrings.xml"),
                    label="XLSX shared strings",
                )
                for item in root:
                    rendered = normalize_text(
                        " ".join(
                            node.text or ""
                            for node in item.iter()
                            if node.tag.endswith("}t")
                        )
                    )
                    shared.append(rendered)
                    shared_characters += len(rendered)
                    if (
                        len(shared) > _MAX_BLOCKS
                        or shared_characters > _MAX_CHARACTERS
                    ):
                        raise ExtractionError("XLSX shared-string inventory exceeds its bound")
            sheets = _xlsx_sheets(archive)
            for sheet_number, (sheet_title, name) in enumerate(sheets, start=1):
                blocks.append(
                    TextBlock(
                        text=sheet_title,
                        paragraph=sheet_number,
                        style="Heading 1",
                        kind="sheet",
                        source=f"xlsx:sheet:{sheet_number};name:{sheet_title}",
                    )
                )
                root = _xml(_safe_member(archive, name), label="XLSX worksheet")
                seen_coordinates: set[str] = set()
                previous_row_number = 0
                for row in (node for node in root.iter() if node.tag.endswith("}row")):
                    cells = [node for node in row if node.tag.endswith("}c")]
                    if not cells:
                        continue
                    parsed_coordinates = [
                        _xlsx_cell_reference(cell.attrib.get("r", "")) for cell in cells
                    ]
                    coordinate_rows = {value[2] for value in parsed_coordinates}
                    row_attribute = row.attrib.get("r")
                    if row_attribute is None:
                        if len(coordinate_rows) != 1:
                            raise ExtractionError(
                                "XLSX row has ambiguous cell coordinates"
                            )
                        row_number = next(iter(coordinate_rows))
                    elif not row_attribute.isdigit() or not 1 <= int(row_attribute) <= 1_048_576:
                        raise ExtractionError("XLSX row number is invalid")
                    else:
                        row_number = int(row_attribute)
                    if (
                        coordinate_rows != {row_number}
                        or row_number <= previous_row_number
                    ):
                        raise ExtractionError("XLSX row/cell order is invalid")
                    previous_row_number = row_number
                    values: list[str] = []
                    for cell, (coordinate, _column, _coordinate_row) in zip(
                        cells, parsed_coordinates, strict=True
                    ):
                        if coordinate in seen_coordinates:
                            raise ExtractionError("XLSX cell coordinate is duplicated")
                        seen_coordinates.add(coordinate)
                        value_nodes = [
                            node for node in cell if node.tag.endswith("}v")
                        ]
                        formula_nodes = [
                            node for node in cell if node.tag.endswith("}f")
                        ]
                        if len(value_nodes) > 1 or len(formula_nodes) > 1:
                            raise ExtractionError("XLSX cell value inventory is ambiguous")
                        value_node = value_nodes[0] if value_nodes else None
                        formula_node = formula_nodes[0] if formula_nodes else None
                        value = value_node.text if value_node is not None else ""
                        cell_type = cell.attrib.get("t", "")
                        if cell_type not in {"", "n", "s", "str", "inlineStr", "b", "e", "d"}:
                            raise ExtractionError("XLSX cell type is unsupported")
                        if cell_type == "s":
                            if value is None or not value.isdigit():
                                raise ExtractionError("XLSX shared-string index is invalid")
                            position = int(value)
                            if position >= len(shared):
                                raise ExtractionError("XLSX shared-string index is unavailable")
                            value = shared[position]
                        elif cell_type == "inlineStr":
                            value = normalize_text(
                                " ".join(
                                    node.text or ""
                                    for node in cell.iter()
                                    if node.tag.endswith("}t")
                                )
                            )
                        elif cell_type == "b" and value not in {"0", "1"}:
                            raise ExtractionError("XLSX boolean cell value is invalid")
                        if formula_node is not None and formula_node.text:
                            value = f"={formula_node.text} -> {value}"
                        values.append(f"{coordinate}={value}")
                    if values:
                        blocks.append(
                            TextBlock(
                                text=" | ".join(values),
                                paragraph=row_number,
                                style="sheet-row",
                                kind="cell_range",
                                source=(
                                    f"xlsx:sheet:{sheet_number};name:{sheet_title};"
                                    f"row:{row_number}"
                                ),
                            )
                        )
                merged: list[str] = []
                for node in root.iter():
                    if not node.tag.endswith("}mergeCell"):
                        continue
                    reference = node.attrib.get("ref", "")
                    parts = reference.split(":")
                    if len(parts) != 2:
                        raise ExtractionError("XLSX merged-cell range is invalid")
                    start = _xlsx_cell_reference(parts[0])
                    end = _xlsx_cell_reference(parts[1])
                    if (
                        _xlsx_column_number(start[1]) > _xlsx_column_number(end[1])
                        or start[2] > end[2]
                        or reference in merged
                    ):
                        raise ExtractionError("XLSX merged-cell range is invalid")
                    merged.append(reference)
                if merged:
                    blocks.append(
                        TextBlock(
                            text=f"Merged cells: {', '.join(merged)}",
                            paragraph=sheet_number,
                            style="merged-cells",
                            kind="merged_cells",
                            source=(
                                f"xlsx:sheet:{sheet_number};name:{sheet_title};merged"
                            ),
                        )
                    )
    except (OSError, zipfile.BadZipFile) as error:
        raise ExtractionError(f"invalid XLSX: {path.name}") from error
    return _quality(
        blocks,
        extractor="ooxml-workbook",
        page_count=len(sheets),
    )


def _xlsx_column_number(column: str) -> int:
    value = 0
    for character in column:
        value = value * 26 + ord(character) - ord("A") + 1
    return value


def _xlsx_cell_reference(value: str) -> tuple[str, str, int]:
    match = _XLSX_CELL_REFERENCE.fullmatch(value)
    if match is None:
        raise ExtractionError("XLSX cell coordinate is invalid")
    column, raw_row = match.groups()
    row = int(raw_row)
    if _xlsx_column_number(column) > 16_384 or row > 1_048_576:
        raise ExtractionError("XLSX cell coordinate exceeds the worksheet bound")
    return value, column, row


def _xlsx_sheets(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    members = set(archive.namelist())
    workbook_name = "xl/workbook.xml"
    if workbook_name not in members:
        raise ExtractionError("XLSX workbook inventory is missing")
    workbook = _xml(_safe_member(archive, workbook_name), label="XLSX workbook")
    relationships = _package_relationships(
        archive,
        workbook_name,
        label="XLSX workbook",
        required=True,
    )
    values: list[tuple[str, str]] = []
    relationship_ids: set[str] = set()
    worksheet_members: set[str] = set()
    titles: set[str] = set()
    for position, sheet in enumerate(
        (node for node in workbook.iter() if node.tag.endswith("}sheet")),
        start=1,
    ):
        relationship_id = next(
            (value for key, value in sheet.attrib.items() if key.endswith("}id")),
            None,
        )
        relationship = relationships.get(relationship_id or "")
        if (
            not relationship_id
            or relationship_id in relationship_ids
            or relationship is None
            or relationship["target_mode"] != "Internal"
            or not relationship["type"].endswith("/worksheet")
        ):
            raise ExtractionError("XLSX worksheet relationship is missing or invalid")
        member = _resolve_package_target(
            workbook_name,
            relationship["target"],
            label="XLSX worksheet relationship",
        )
        if (
            member not in members
            or not member.startswith("xl/worksheets/")
            or not member.endswith(".xml")
            or member in worksheet_members
        ):
            raise ExtractionError("XLSX worksheet target is missing or duplicated")
        title = normalize_text(sheet.attrib.get("name", "")) or f"Sheet {position}"
        folded_title = title.casefold()
        if folded_title in titles:
            raise ExtractionError("XLSX worksheet title is duplicated")
        relationship_ids.add(relationship_id)
        worksheet_members.add(member)
        titles.add(folded_title)
        values.append((title, member))
    if not values:
        raise ExtractionError("XLSX workbook contains no ordered worksheets")
    return values


def _extract_epub(path: Path) -> ExtractionResult:
    blocks: list[TextBlock] = []
    try:
        with zipfile.ZipFile(path) as archive:
            _validate_archive_inventory(archive, label="EPUB")
            container = _xml(
                _safe_member(archive, "META-INF/container.xml"),
                label="EPUB container",
            )
            rootfile = next(
                (
                    node.attrib.get("full-path")
                    for node in container.iter()
                    if node.tag.endswith("}rootfile")
                ),
                None,
            )
            if not rootfile:
                raise ExtractionError("EPUB package root is unavailable")
            rootfile = _resolve_package_target(
                "META-INF/container.xml",
                f"/{rootfile}",
                label="EPUB package root relationship",
            )
            package = _xml(_safe_member(archive, rootfile), label="EPUB package")
            manifest: dict[str, str] = {}
            for item in (node for node in package.iter() if node.tag.endswith("}item")):
                identifier = item.attrib.get("id", "")
                href = item.attrib.get("href", "")
                if (
                    not 1 <= len(identifier) <= 500
                    or identifier in manifest
                    or not 1 <= len(href) <= 4_000
                ):
                    raise ExtractionError("EPUB manifest inventory is invalid")
                manifest[identifier] = href
            spine = [
                node.attrib.get("idref")
                for node in package.iter()
                if node.tag.endswith("}itemref")
            ]
            if (
                not spine
                or len(spine) > _MAX_ARCHIVE_MEMBERS
                or any(not identifier or identifier not in manifest for identifier in spine)
                or len(spine) != len(set(spine))
            ):
                raise ExtractionError("EPUB spine inventory is invalid")
            for chapter, identifier in enumerate(spine, start=1):
                href = manifest[identifier]
                member = _resolve_package_target(
                    rootfile,
                    href,
                    label="EPUB spine relationship",
                )
                if (
                    member not in archive.namelist()
                    or PurePosixPath(member).suffix.lower() not in {".html", ".htm", ".xhtml"}
                ):
                    raise ExtractionError("EPUB spine target is missing or unsupported")
                parser = _StructuredHTMLParser()
                parser.feed(_safe_member(archive, member).decode("utf-8-sig"))
                parser.close()
                for index, (tag, text) in enumerate(parser.blocks, start=1):
                    blocks.append(
                        TextBlock(
                            text=text,
                            page=chapter,
                            paragraph=index,
                            style=(f"Heading {tag[1:]}" if tag.startswith("h") else tag),
                            kind="heading" if tag.startswith("h") else tag,
                            source=f"epub:{member}",
                        )
                    )
    except (OSError, UnicodeDecodeError, zipfile.BadZipFile) as error:
        raise ExtractionError(f"invalid EPUB: {path.name}") from error
    return _quality(blocks, extractor="epub-spine", page_count=len(spine))


def validate_source_ir_database(connection: sqlite3.Connection) -> dict[str, Any]:
    orphan_nodes = connection.execute(
        """
        SELECT COUNT(*) FROM source_ir_nodes_v2
        LEFT JOIN compilations_v2 USING(compilation_id)
        WHERE compilations_v2.compilation_id IS NULL
        """
    ).fetchone()[0]
    orphan_fragments = connection.execute(
        """
        SELECT COUNT(*) FROM fragments_v2
        LEFT JOIN compilations_v2 USING(compilation_id)
        WHERE compilations_v2.compilation_id IS NULL
        """
    ).fetchone()[0]
    return {
        "schema_version": "deeplaw.source-ir-validation/v1",
        "orphan_node_count": orphan_nodes,
        "orphan_fragment_count": orphan_fragments,
        "valid": orphan_nodes == orphan_fragments == 0,
    }
