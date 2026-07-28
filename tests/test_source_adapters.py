from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from deeplaw.extract import ExtractionError
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.source_adapters import extract_extended_source
from deeplaw.util import strict_json_loads


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="source adapters", scope="project")
    return root


def test_markdown_source_ir_preserves_hierarchy_and_structures(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "architecture.md"
    source.write_text(
        "---\nowner: local-operator\n---\n"
        "# Architecture\nThe system keeps evidence on the local machine.\n"
        "## Rules\n- Preserve source hashes.\n"
        "| Field | Value |\n| mode | local |\n"
        "```python\nprint('bounded')\n```\n",
        encoding="utf-8",
    )

    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        rows = vault.connection.execute(
            """
            SELECT node_type, parent_node_id, source_span_json
            FROM source_ir_nodes_v2
            WHERE compilation_id = ? ORDER BY ordinal
            """,
            (result["identity"]["compilation_id"],),
        ).fetchall()
        integrity = vault.verify_integrity()

    node_types = {row["node_type"] for row in rows}
    assert {"section", "property", "heading", "list_item", "table", "code_block"} <= (
        node_types
    )
    assert any(row["parent_node_id"] is not None for row in rows)
    assert all(row["source_span_json"] for row in rows)
    assert integrity["valid"] is True


def test_structured_and_code_adapters_emit_typed_source_ir(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    fixtures = {
        "data.json": '{"service":{"name":"Mercury","ports":[443,8443]}}',
        "events.jsonl": '{"event":"start"}\n{"event":"ready"}\n',
        "data.yaml": "service:\n  name: Mercury\n  enabled: true\n",
        "data.toml": '[service]\nname = "Mercury"\nenabled = true\n',
        "records.csv": "name,value\nMercury,enabled\nVenus,disabled\n",
        "records.tsv": "name\tvalue\nMercury\tenabled\nVenus\tdisabled\n",
        "query.sql": "WITH active AS (SELECT id FROM systems) SELECT id FROM active;",
        "module.py": (
            "import os\n\nclass Engine:\n"
            "    def run(self):\n"
            "        \"\"\"Return the operating-system name.\"\"\"\n"
            "        return str(os.name)\n"
        ),
        "module.ts": (
            "export interface Engine { run(): string }\n"
            "export function runEngine() { return 'ok' }\n"
        ),
        "Main.java": "public class Main { public static void run() {} }\n",
        "main.go": "package main\nfunc RunEngine() string { return \"ok\" }\n",
        "lib.rs": "pub struct Engine;\npub fn run_engine() -> &'static str { \"ok\" }\n",
        "page.html": "<h1>Mercury</h1><p>The local engine preserves source evidence.</p>",
    }
    observed_types: set[str] = set()
    with KnowledgeVault(root, read_only=False) as vault:
        for name, content in fixtures.items():
            source = tmp_path / name
            source.write_text(content, encoding="utf-8")
            result = compile_source(
                vault,
                source,
                source_kind=(
                    "code"
                    if source.suffix in {".py", ".ts", ".java", ".go", ".rs"}
                    else "document"
                ),
                confirm_no_case_data=True,
            )
            observed_types.update(
                row["node_type"]
                for row in vault.connection.execute(
                    "SELECT node_type FROM source_ir_nodes_v2 WHERE compilation_id = ?",
                    (result["identity"]["compilation_id"],),
                )
            )
        integrity = vault.verify_integrity()

    assert {
        "object",
        "array",
        "scalar",
        "row",
        "statement",
        "cte",
        "table",
        "column",
        "module",
        "class",
        "function",
        "method",
        "reference",
    } <= observed_types
    assert "heading" in observed_types
    assert integrity["valid"] is True


@pytest.mark.parametrize(
    ("name", "content", "adapter", "grammar", "expected_types"),
    (
        (
            "engine.js",
            "import helper from './helper.js';\n"
            "class Engine {\n  /** Run docs. */\n  run() { return helper(); }\n}\n",
            "tree-sitter-js",
            "tree-sitter-javascript/0.25.0",
            {"class", "method"},
        ),
        (
            "engine.ts",
            "import { helper } from './helper';\n"
            "interface Runner { run(): string }\n"
            "export function runEngine(): string { return helper(); }\n",
            "tree-sitter-ts",
            "tree-sitter-typescript/0.23.2",
            {"interface", "method", "function"},
        ),
        (
            "view.tsx",
            "import { helper } from './helper';\n"
            "export function View() { helper(); return <div />; }\n",
            "tree-sitter-tsx",
            "tree-sitter-typescript/0.23.2",
            {"function"},
        ),
        (
            "Engine.java",
            "import java.util.List;\n"
            "class Engine {\n  /** Run docs. */\n  void run() { helper(); }\n"
            "  void helper() {}\n}\n",
            "tree-sitter-java",
            "tree-sitter-java/0.23.5",
            {"class", "method"},
        ),
        (
            "engine.go",
            "package engine\nimport \"fmt\"\ntype Engine struct{}\n"
            "// Run docs.\nfunc (e Engine) Run() { helper(); fmt.Println(\"ok\") }\n"
            "func helper() {}\n",
            "tree-sitter-go",
            "tree-sitter-go/0.25.0",
            {"type", "method", "function"},
        ),
        (
            "engine.rs",
            "use std::io;\nstruct Engine {}\nimpl Engine {\n"
            "    /// Run docs.\n    fn run(&self) { helper(); }\n}\nfn helper() {}\n",
            "tree-sitter-rs",
            "tree-sitter-rust/0.24.2",
            {"class", "implementation", "method", "function"},
        ),
    ),
)
def test_tree_sitter_code_adapters_preserve_compiler_grade_symbols(
    tmp_path: Path,
    name: str,
    content: str,
    adapter: str,
    grammar: str,
    expected_types: set[str],
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / name
    source.write_text(content, encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="code",
            confirm_no_case_data=True,
        )
        rows = vault.connection.execute(
            """
            SELECT node_type, title, text, parent_node_id, source_span_json,
                   adapter, adapter_version
            FROM source_ir_nodes_v2
            WHERE compilation_id = ?
            ORDER BY ordinal
            """,
            (result["identity"]["compilation_id"],),
        ).fetchall()
        assert vault.verify_integrity()["valid"] is True

    node_types = {row["node_type"] for row in rows}
    assert {"module", "import", "reference", *expected_types} <= node_types
    assert all(row["adapter"] == adapter for row in rows)
    assert all("tree-sitter/0.26.0" in row["adapter_version"] for row in rows)
    assert all(grammar in row["adapter_version"] for row in rows)
    assert result["compiler"]["source_ir_quality_flags"] == [
        "compiler-grade-tree-sitter-ast"
    ]
    structural = [row for row in rows if row["node_type"] in expected_types]
    assert structural
    for row in structural:
        span = strict_json_loads(row["source_span_json"])
        assert 1 <= span["line_start"] <= span["line_end"]
        assert span["symbol_path"]
    nested = [
        row
        for row in structural
        if row["node_type"] in {"method", "implementation"}
    ]
    if nested:
        assert all(row["parent_node_id"] is not None for row in nested)
    assert any(
        row["node_type"] == "reference" and "helper" in row["text"]
        for row in rows
    )


def test_tree_sitter_syntax_recovery_is_explicit_quality_data(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "broken.ts"
    source.write_text(
        "export function broken(value: string { return value;\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="code",
            confirm_no_case_data=True,
        )
        assert vault.verify_integrity()["valid"] is True

    assert result["compiler"]["source_ir_quality_flags"] == [
        "compiler-grade-tree-sitter-ast",
        "tree-sitter-recovered-syntax-errors",
    ]


def test_sqlglot_adapter_preserves_statements_ctes_tables_columns_and_spans(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "report.sql"
    source.write_text(
        "WITH active AS (\n"
        "  SELECT event.id, event.note\n"
        "  FROM audit.events AS event\n"
        "  WHERE event.note = 'alpha;beta'\n"
        ")\n"
        "SELECT active.id, active.note FROM active;\n\n"
        "INSERT INTO archive.events (id)\n"
        "SELECT id FROM audit.events;\n",
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        rows = vault.connection.execute(
            """
            SELECT node_type, title, text, source_span_json, adapter, adapter_version
            FROM source_ir_nodes_v2
            WHERE compilation_id = ? AND node_type != 'section'
            ORDER BY ordinal
            """,
            (result["identity"]["compilation_id"],),
        ).fetchall()
        assert vault.verify_integrity()["valid"] is True

    assert result["compiler"]["source_adapter"] == "sqlglot-sql"
    assert result["compiler"]["source_adapter_version"].endswith(
        "sqlglot/30.13.0;dialect=generic"
    )
    assert result["compiler"]["source_ir_quality_flags"] == [
        "compiler-grade-sqlglot-ast"
    ]
    assert all(row["adapter"] == "sqlglot-sql" for row in rows)
    assert {"statement", "cte", "table", "column"} <= {
        row["node_type"] for row in rows
    }
    statements = [row for row in rows if row["node_type"] == "statement"]
    assert len(statements) == 2
    assert "'alpha;beta'" in statements[0]["text"]
    assert {row["title"] for row in statements} == {"SELECT", "INSERT"}
    first_span = strict_json_loads(statements[0]["source_span_json"])
    second_span = strict_json_loads(statements[1]["source_span_json"])
    assert (first_span["line_start"], first_span["line_end"]) == (1, 6)
    assert (second_span["line_start"], second_span["line_end"]) == (8, 9)
    assert any(
        row["node_type"] == "table" and "audit.events" in row["title"]
        for row in rows
    )
    assert any(
        row["node_type"] == "column" and "event.note" in row["title"]
        for row in rows
    )


def test_sqlglot_parse_failure_uses_explicit_bounded_lexical_fallback(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "broken.sql"
    source.write_text("SELECT ( FROM broken;\n", encoding="utf-8")
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        assert vault.verify_integrity()["valid"] is True

    assert result["compiler"]["source_adapter"] == "sqlglot-sql"
    assert result["compiler"]["source_ir_quality_flags"] == [
        "sqlglot-parse-failed:lexical-fallback"
    ]


def test_sqlglot_recursion_failure_uses_explicit_bounded_lexical_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "deep.sql"
    source.write_text("SELECT id FROM bounded_table;\n", encoding="utf-8")

    def fail_closed(*_args: object, **_kwargs: object) -> object:
        raise RecursionError("synthetic parser depth exhaustion")

    monkeypatch.setattr("deeplaw.source_adapters.sqlglot.parse", fail_closed)
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )

    assert result["compiler"]["source_ir_quality_flags"] == [
        "sqlglot-parse-failed:lexical-fallback"
    ]


def test_structured_source_rejects_ambiguous_keys_aliases_and_cycles(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    fixtures = {
        "duplicate.json": '{"mode":"local","mode":"remote"}',
        "duplicate.yaml": "mode: local\nmode: remote\n",
        "alias.yaml": "root: &root\n  child: *root\n",
    }
    with KnowledgeVault(root, read_only=False) as vault:
        for name, content in fixtures.items():
            source = tmp_path / name
            source.write_text(content, encoding="utf-8")
            with pytest.raises(ExtractionError, match="invalid structured source"):
                compile_source(
                    vault,
                    source,
                    source_kind="document",
                    confirm_no_case_data=True,
                )


def test_structured_source_uses_unambiguous_json_pointer_paths(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "pointer.json"
    source.write_text(
        '{"a/b":"flat","a":{"b":"nested"},"t~x":"tilde"}',
        encoding="utf-8",
    )
    with KnowledgeVault(root, read_only=False) as vault:
        result = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        locators = {
            row["locator"]
            for row in vault.connection.execute(
                "SELECT locator FROM source_ir_nodes_v2 WHERE compilation_id = ?",
                (result["identity"]["compilation_id"],),
            )
        }

    assert {"path:$/a~1b", "path:$/a/b", "path:$/t~0x"} <= locators


def test_office_archive_rejects_unused_unsafe_members(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    source = tmp_path / "unsafe.pptx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            "<p:sld xmlns:p='urn:p' xmlns:a='urn:a'><a:t>Safe text long enough.</a:t></p:sld>",
        )
        archive.writestr("../outside.xml", "unused but unsafe")

    with (
        KnowledgeVault(root, read_only=False) as vault,
        pytest.raises(ExtractionError, match="unsafe or duplicate member"),
    ):
        compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )


def test_pptx_uses_relationship_order_object_order_and_exact_notes_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ordered.pptx"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """
            <p:presentation
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <p:sldIdLst>
                <p:sldId id="256" r:id="rIdSecondFile"/>
                <p:sldId id="257" r:id="rIdFirstFile"/>
              </p:sldIdLst>
            </p:presentation>
            """,
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rIdFirstFile" Target="slides/slide1.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/>
              <Relationship Id="rIdSecondFile" Target="slides/slide2.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/>
            </Relationships>
            """,
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree>
                <p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
                  <p:txBody><a:p><a:r><a:t>First deck slide</a:t></a:r></a:p></p:txBody>
                </p:sp>
                <p:graphicFrame><a:graphic><a:graphicData><a:tbl>
                  <a:tr><a:tc><a:txBody><a:p><a:r><a:t>A</a:t></a:r></a:p></a:txBody></a:tc>
                    <a:tc><a:txBody><a:p><a:r><a:t>B</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
                  <a:tr><a:tc><a:txBody><a:p><a:r><a:t>1</a:t></a:r></a:p></a:txBody></a:tc>
                    <a:tc><a:txBody><a:p><a:r><a:t>2</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
                </a:tbl></a:graphicData></a:graphic></p:graphicFrame>
                <p:sp><p:txBody><a:p><a:r><a:t>Body after table.</a:t></a:r></a:p>
                </p:txBody></p:sp>
              </p:spTree></p:cSld>
            </p:sld>
            """,
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp>
                <p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
                <p:txBody><a:p><a:r><a:t>Second deck slide</a:t></a:r></a:p></p:txBody>
              </p:sp></p:spTree></p:cSld>
            </p:sld>
            """,
        )
        archive.writestr(
            "ppt/slides/_rels/slide2.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="notes" Target="../notesSlides/notesSlide9.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"/>
            </Relationships>
            """,
        )
        archive.writestr(
            "ppt/notesSlides/notesSlide9.xml",
            """
            <p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                     xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/>
                </p:nvPr></p:nvSpPr><p:txBody><a:p><a:r>
                <a:t>Notes belong to the first deck slide.</a:t>
                </a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
            </p:notes>
            """,
        )

    result = extract_extended_source(source, "PPTX")
    assert result.quality.page_count == 2
    assert [(block.kind, block.page, block.text) for block in result.blocks] == [
        ("slide_title", 1, "First deck slide"),
        ("table", 1, "A | B\n1 | 2"),
        ("slide_text", 1, "Body after table."),
        ("speaker_notes", 1, "Notes belong to the first deck slide."),
        ("slide_title", 2, "Second deck slide"),
    ]


def test_office_and_epub_relationship_targets_fail_closed(tmp_path: Path) -> None:
    pptx = tmp_path / "external-slide.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """
            <p:presentation
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <p:sldIdLst><p:sldId id="256" r:id="external"/></p:sldIdLst>
            </p:presentation>
            """,
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="external" Target="https://example.invalid/slide.xml"
                TargetMode="External"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/>
            </Relationships>
            """,
        )
    with pytest.raises(ExtractionError, match="slide relationship"):
        extract_extended_source(pptx, "PPTX")

    xlsx = tmp_path / "escape-sheet.xlsx"
    with zipfile.ZipFile(xlsx, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Unsafe" sheetId="1" r:id="escape"/></sheets>
            </workbook>
            """,
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="escape" Target="%2e%2e/%2e%2e/outside.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
            </Relationships>
            """,
        )
    with pytest.raises(ExtractionError, match="escapes the document package"):
        extract_extended_source(xlsx, "XLSX")

    epub = tmp_path / "remote-spine.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles>
            </container>
            """,
        )
        archive.writestr(
            "OEBPS/package.opf",
            """
            <package xmlns="http://www.idpf.org/2007/opf">
              <manifest><item id="remote" href="https://example.invalid/chapter.xhtml"/>
              </manifest><spine><itemref idref="remote"/></spine>
            </package>
            """,
        )
    with pytest.raises(ExtractionError, match="non-package target"):
        extract_extended_source(epub, "EPUB")


def test_xlsx_rejects_unavailable_shared_strings_and_ambiguous_cells(
    tmp_path: Path,
) -> None:
    for name, cells, message in (
        (
            "missing-shared-string.xlsx",
            '<c r="A1" t="s"><v>9</v></c>',
            "shared-string index is unavailable",
        ),
        (
            "duplicate-cell.xlsx",
            '<c r="A1"><v>1</v></c><c r="A1"><v>2</v></c>',
            "cell coordinate is duplicated",
        ),
    ):
        source = tmp_path / name
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr(
                "xl/workbook.xml",
                """
                <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
                  <sheets><sheet name="Sheet" sheetId="1" r:id="sheet"/></sheets>
                </workbook>
                """,
            )
            archive.writestr(
                "xl/_rels/workbook.xml.rels",
                """
                <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
                  <Relationship Id="sheet" Target="worksheets/sheet1.xml"
                    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
                </Relationships>
                """,
            )
            archive.writestr(
                "xl/sharedStrings.xml",
                """
                <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                  <si><t>only value</t></si>
                </sst>
                """,
            )
            archive.writestr(
                "xl/worksheets/sheet1.xml",
                "<worksheet xmlns=\"http://schemas.openxmlformats.org/"
                "spreadsheetml/2006/main\"><sheetData><row r=\"1\">"
                f"{cells}</row></sheetData></worksheet>",
            )

        with pytest.raises(ExtractionError, match=message):
            extract_extended_source(source, "XLSX")


def test_office_xml_depth_budget_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "deep.pptx"
    nested = "<x>" * 257 + "text" + "</x>" * 257
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("ppt/presentation.xml", nested)

    with pytest.raises(ExtractionError, match="XML exceeds the depth bound"):
        extract_extended_source(source, "PPTX")


def test_pptx_xlsx_and_epub_closed_adapters_compile(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    pptx = tmp_path / "slides.pptx"
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            """
            <p:presentation
              xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
            </p:presentation>
            """,
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="slides/slide1.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/>
            </Relationships>
            """,
        )
        archive.writestr(
            "ppt/slides/slide1.xml",
            """
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody>
                <a:p><a:r><a:t>Mercury release</a:t></a:r></a:p>
                <a:p><a:r><a:t>The release retains exact source evidence locally.</a:t></a:r></a:p>
              </p:txBody></p:sp></p:spTree></p:cSld>
            </p:sld>
            """,
        )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rIdNotes" Target="../notesSlides/notesSlide1.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"/>
            </Relationships>
            """,
        )
        archive.writestr(
            "ppt/notesSlides/notesSlide1.xml",
            """
            <p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                     xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <p:cSld><p:spTree><p:sp><p:txBody>
                <a:p><a:r><a:t>Operator speaker notes remain source evidence.</a:t></a:r></a:p>
              </p:txBody></p:sp></p:spTree></p:cSld>
            </p:notes>
            """,
        )
    xlsx = tmp_path / "workbook.xlsx"
    with zipfile.ZipFile(xlsx, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            """
            <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
                      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
              <sheets><sheet name="Release Matrix" sheetId="1" r:id="rId1"/></sheets>
            </workbook>
            """,
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Target="worksheets/sheet1.xml"
                Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>
            </Relationships>
            """,
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            """
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Mercury</t></si><si><t>source evidence retained</t></si>
            </sst>
            """,
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>
              <c r="B1" t="s"><v>1</v></c></row>
              <row r="2"><c r="A2"><f>1+1</f><v>2</v></c></row></sheetData>
              <mergeCells><mergeCell ref="A3:B3"/></mergeCells>
            </worksheet>
            """,
        )
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """
            <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
              <rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles>
            </container>
            """,
        )
        archive.writestr(
            "OEBPS/package.opf",
            """
            <package xmlns="http://www.idpf.org/2007/opf">
              <manifest><item id="chapter" href="chapter.xhtml"/></manifest>
              <spine><itemref idref="chapter"/></spine>
            </package>
            """,
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            "<html><body><h1>Local knowledge</h1>"
            "<p>Every chapter preserves verifiable source evidence.</p></body></html>",
        )

    with KnowledgeVault(root, read_only=False) as vault:
        results = [
            compile_source(
                vault,
                source,
                source_kind="document",
                confirm_no_case_data=True,
            )
            for source in (pptx, xlsx, epub)
        ]
        adapters = {
            result["compiler"]["source_adapter"] for result in results
        }
        node_rows = vault.connection.execute(
            "SELECT node_type, text FROM source_ir_nodes_v2"
        ).fetchall()
        node_types = {row["node_type"] for row in node_rows}
        integrity = vault.verify_integrity()

    assert adapters == {"ooxml-presentation", "ooxml-workbook", "epub-spine"}
    assert {
        "slide_text",
        "speaker_notes",
        "sheet",
        "cell_range",
        "merged_cells",
        "p",
    } <= node_types
    assert any("Release Matrix" in row["text"] for row in node_rows)
    assert integrity["valid"] is True
