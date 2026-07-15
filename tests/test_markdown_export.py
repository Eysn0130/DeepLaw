from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest

from deeplaw.ingest import build_release
from deeplaw.markdown_export import export_markdown

from .helpers import manifest_document, write_docx, write_manifest

_LOCATOR = re.compile(r"<!-- deeplaw-locator\n(?P<payload>\{[^\n]+\})\n-->")


def _locators(markdown: str) -> list[dict[str, object]]:
    return [json.loads(match.group("payload")) for match in _LOCATOR.finditer(markdown)]


def _write_docx_with_table(path: Path) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>测试表格法</w:t></w:r></w:p>"
        "<w:tbl>"
        "<w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>标准</w:t></w:r></w:p></w:tc></w:tr>"
        "<w:tr><w:tc><w:p><w:r><w:t>数额</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>三万元以上</w:t></w:r></w:p></w:tc></w:tr>"
        "</w:tbl>"
        "<w:p><w:r><w:t>第一条 应当保留原件并核对表格。</w:t></w:r></w:p>"
        "<w:sectPr/>"
        "</w:body></w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)


def test_markdown_export_is_deterministic_and_bound_to_release_ir(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "law.docx"
    write_docx(document, ["测试法", "第一章 总则", "第一条 应当保留原件。"])
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="测试法")],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "releases",
    )

    first = export_markdown(release / "deeplaw.sqlite3", tmp_path / "first")
    second = export_markdown(release / "deeplaw.sqlite3", tmp_path / "second")

    assert first == second
    assert first["document_count"] == 1
    relative_path = first["files"][0]["path"]
    first_text = (tmp_path / "first" / relative_path).read_text(encoding="utf-8")
    second_text = (tmp_path / "second" / relative_path).read_text(encoding="utf-8")
    assert first_text == second_text
    assert "deeplaw.markdown-export/v1" in first_text
    assert "deeplaw.markdown-locator/v1" in first_text
    assert "# 测试法" in first_text
    assert "第一条 应当保留原件。" in first_text
    assert str(document.resolve()) not in first_text
    assert str(source.resolve()) not in first_text

    connection = sqlite3.connect(release / "deeplaw.sqlite3")
    connection.row_factory = sqlite3.Row
    try:
        blocks = connection.execute(
            "SELECT * FROM document_blocks ORDER BY ordinal"
        ).fetchall()
        segments = connection.execute("SELECT * FROM segments ORDER BY ordinal").fetchall()
    finally:
        connection.close()

    locators = _locators(first_text)
    block_locators = {
        str(locator["block_id"]): locator
        for locator in locators
        if locator["record_type"] == "block"
    }
    segment_locators = {
        str(locator["segment_id"]): locator
        for locator in locators
        if locator["record_type"] == "segment"
    }
    assert set(block_locators) == {str(block["block_id"]) for block in blocks}
    assert set(segment_locators) == {str(segment["segment_id"]) for segment in segments}
    assert all(locator["release_id"] == first["release_id"] for locator in locators)

    for block in blocks:
        block_id = str(block["block_id"])
        locator = block_locators[block_id]
        assert f'<a id="{block_id}"></a>' in first_text
        assert f"`block_id={block_id}`" in first_text
        assert locator["text_sha256"] == block["text_sha256"]
        assert locator["review_required"] == bool(block["review_required"])
        assert locator["risk_flags"] == json.loads(block["risk_flags_json"])
        assert locator["locator"] == {
            "bbox": json.loads(block["bbox_json"]) if block["bbox_json"] else None,
            "page": block["page"],
            "paragraph": block["paragraph"],
            "table_row": None,
            "table_row_status": "not_applicable",
        }

    for segment in segments:
        segment_id = str(segment["segment_id"])
        locator = segment_locators[segment_id]
        assert f'<a id="{segment_id}"></a>' in first_text
        assert f"`segment_id={segment_id}`" in first_text
        assert locator["source_block_ids"] == json.loads(segment["source_block_ids_json"])
        assert locator["text_sha256"] == segment["text_sha256"]
        assert locator["review_required"] == bool(segment["extraction_review_required"])
        assert locator["risk_flags"] == json.loads(segment["extraction_risk_flags_json"])
    assert json.loads((tmp_path / "first" / "index.json").read_text()) == first


def test_markdown_export_reports_table_row_locator_limit_honestly(tmp_path: Path) -> None:
    source = tmp_path / "source"
    document = source / "table-law.docx"
    _write_docx_with_table(document)
    manifest = write_manifest(
        source / "manifest.json",
        [manifest_document(source, document.name, title="测试表格法")],
    )
    release, _ = build_release(
        source_root=source,
        manifest_path=manifest,
        output_root=tmp_path / "releases",
    )

    exported = export_markdown(release / "deeplaw.sqlite3", tmp_path / "views")
    markdown = (tmp_path / "views" / exported["files"][0]["path"]).read_text(
        encoding="utf-8"
    )
    locators = _locators(markdown)
    table_blocks = [
        locator
        for locator in locators
        if locator["record_type"] == "block" and locator["kind"] == "table_row"
    ]
    assert len(table_blocks) == 2
    for locator in table_blocks:
        assert locator["locator"]["paragraph"] is not None
        assert locator["locator"]["table_row"] is None
        assert (
            locator["locator"]["table_row_status"]
            == "row_index_not_stored_use_block_id_and_paragraph"
        )
    assert "table_row `not-stored-separately (use block_id and paragraph)`" in markdown

    segments_with_table_rows = [
        locator
        for locator in locators
        if locator["record_type"] == "segment"
        and locator["locator"]["table_row_block_ids"]
    ]
    assert segments_with_table_rows
    for locator in segments_with_table_rows:
        assert locator["locator"]["table_row"] is None
        assert (
            locator["locator"]["table_row_status"]
            == "row_index_not_stored_source_block_anchors_provided"
        )


def test_markdown_export_refuses_to_mix_with_stale_output(tmp_path: Path) -> None:
    output = tmp_path / "views"
    output.mkdir()
    (output / "stale.md").write_text("stale", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        export_markdown(tmp_path / "missing.sqlite3", output)
