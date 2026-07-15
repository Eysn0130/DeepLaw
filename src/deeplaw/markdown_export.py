from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .search import DeepLaw
from .util import canonical_json, sha256_bytes

MARKDOWN_EXPORT_SCHEMA = "deeplaw.markdown-export/v1"
MARKDOWN_LOCATOR_SCHEMA = "deeplaw.markdown-locator/v1"


def _front_matter(document: Any, *, release_id: str) -> str:
    metadata = {
        "schema_version": MARKDOWN_EXPORT_SCHEMA,
        "release_id": release_id,
        "document_id": document["document_id"],
        "title": document["title"],
        "source_sha256": document["source_sha256"],
        "official_source": document["official_source"],
        "status": document["status"],
        "effective_from": document["effective_from"],
        "effective_to": document["effective_to"],
    }
    return f"<!-- deeplaw-derived-view\n{canonical_json(metadata)}\n-->"


def _string_list(value: str, *, field: str) -> tuple[str, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid {field} in release IR") from error
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise RuntimeError(f"invalid {field} in release IR")
    return tuple(decoded)


def _span(start: int | None, end: int | None) -> str:
    if start is None and end is None:
        return "not-stored"
    if start == end or end is None:
        return str(start)
    if start is None:
        return str(end)
    return f"{start}-{end}"


def _risk_label(risk_flags: tuple[str, ...]) -> str:
    return ",".join(risk_flags) if risk_flags else "none"


def _review_label(review_required: bool) -> str:
    return "required" if review_required else "not-required"


def _locator_comment(metadata: dict[str, Any]) -> str:
    return f"<!-- deeplaw-locator\n{canonical_json(metadata)}\n-->"


def _anchor(record_id: str) -> str:
    return f'<a id="{record_id}"></a>'


def _block_links(block_ids: tuple[str, ...]) -> str:
    if not block_ids:
        return "not-stored"
    return ", ".join(f"[`{block_id}`](#{block_id})" for block_id in block_ids)


def _segment_links(segment_ids: tuple[str, ...]) -> str:
    if not segment_ids:
        return "not-stored"
    return ", ".join(f"[`{segment_id}`](#{segment_id})" for segment_id in segment_ids)


def _segment_locator(
    segment: Any,
    *,
    block_by_id: dict[str, Any],
    release_id: str,
) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...]]:
    source_block_ids = _string_list(
        str(segment["source_block_ids_json"]),
        field="segments.source_block_ids_json",
    )
    risk_flags = _string_list(
        str(segment["extraction_risk_flags_json"]),
        field="segments.extraction_risk_flags_json",
    )
    table_block_ids = tuple(
        block_id
        for block_id in source_block_ids
        if block_id in block_by_id
        and str(block_by_id[block_id]["kind"]) in {"table", "table_row", "table_cell"}
    )
    table_row_status = (
        "row_index_not_stored_source_block_anchors_provided"
        if table_block_ids
        else "not_applicable"
    )
    segment_id = str(segment["segment_id"])
    metadata = {
        "anchor": f"#{segment_id}",
        "document_id": segment["document_id"],
        "kind": segment["kind"],
        "locator": {
            "page_end": segment["page_end"],
            "page_start": segment["page_start"],
            "paragraph_end": segment["paragraph_end"],
            "paragraph_start": segment["paragraph_start"],
            "table_row": None,
            "table_row_block_ids": list(table_block_ids),
            "table_row_status": table_row_status,
        },
        "ordinal": segment["ordinal"],
        "record_type": "segment",
        "release_id": release_id,
        "review_required": bool(segment["extraction_review_required"]),
        "risk_flags": list(risk_flags),
        "schema_version": MARKDOWN_LOCATOR_SCHEMA,
        "segment_id": segment_id,
        "source_block_ids": list(source_block_ids),
        "text_sha256": segment["text_sha256"],
    }
    return metadata, source_block_ids, risk_flags


def _block_locator(
    block: Any,
    *,
    release_id: str,
    segment_ids: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...], str]:
    risk_flags = _string_list(
        str(block["risk_flags_json"]),
        field="document_blocks.risk_flags_json",
    )
    kind = str(block["kind"])
    table_row_status = (
        "row_index_not_stored_use_block_id_and_paragraph"
        if kind in {"table", "table_row", "table_cell"}
        else "not_applicable"
    )
    block_id = str(block["block_id"])
    metadata = {
        "anchor": f"#{block_id}",
        "block_id": block_id,
        "document_id": block["document_id"],
        "kind": kind,
        "locator": {
            "bbox": json.loads(block["bbox_json"]) if block["bbox_json"] is not None else None,
            "page": block["page"],
            "paragraph": block["paragraph"],
            "table_row": None,
            "table_row_status": table_row_status,
        },
        "ordinal": block["ordinal"],
        "record_type": "block",
        "release_id": release_id,
        "review_required": bool(block["review_required"]),
        "risk_flags": list(risk_flags),
        "schema_version": MARKDOWN_LOCATOR_SCHEMA,
        "segment_ids": list(segment_ids),
        "text_sha256": block["text_sha256"],
    }
    return metadata, risk_flags, table_row_status


def _render_segment_locator(
    segment: Any,
    *,
    block_by_id: dict[str, Any],
    release_id: str,
) -> str:
    metadata, source_block_ids, risk_flags = _segment_locator(
        segment,
        block_by_id=block_by_id,
        release_id=release_id,
    )
    locator = metadata["locator"]
    segment_id = str(segment["segment_id"])
    table_row = (
        "source-block-anchors (row index not stored separately)"
        if locator["table_row_block_ids"]
        else "not-applicable"
    )
    visible = (
        "> DeepLaw segment locator"
        f" · `segment_id={segment_id}`"
        f" · page `{_span(segment['page_start'], segment['page_end'])}`"
        f" · paragraph `{_span(segment['paragraph_start'], segment['paragraph_end'])}`"
        f" · table_row `{table_row}`"
        f" · source blocks {_block_links(source_block_ids)}"
        f" · `text_sha256={segment['text_sha256']}`"
        f" · review `{_review_label(bool(segment['extraction_review_required']))}`"
        f" · risk `{_risk_label(risk_flags)}`"
    )
    return "\n".join((_anchor(segment_id), _locator_comment(metadata), visible))


def _render_block_locator(
    block: Any,
    *,
    release_id: str,
    segment_ids: tuple[str, ...],
) -> str:
    metadata, risk_flags, table_row_status = _block_locator(
        block,
        release_id=release_id,
        segment_ids=segment_ids,
    )
    block_id = str(block["block_id"])
    table_row = (
        "not-stored-separately (use block_id and paragraph)"
        if table_row_status != "not_applicable"
        else "not-applicable"
    )
    visible = (
        "> DeepLaw block locator"
        f" · `block_id={block_id}`"
        f" · segments {_segment_links(segment_ids)}"
        f" · page `{block['page'] if block['page'] is not None else 'not-stored'}`"
        f" · paragraph `{block['paragraph'] if block['paragraph'] is not None else 'not-stored'}`"
        f" · table_row `{table_row}`"
        f" · `text_sha256={block['text_sha256']}`"
        f" · review `{_review_label(bool(block['review_required']))}`"
        f" · risk `{_risk_label(risk_flags)}`"
    )
    return "\n".join((_anchor(block_id), _locator_comment(metadata), visible))


def _render_document(
    document: Any,
    blocks: list[Any],
    segments: list[Any],
    *,
    release_id: str,
) -> str:
    parts = [_front_matter(document, release_id=release_id), "", f"# {document['title']}"]
    block_by_id = {str(block["block_id"]): block for block in blocks}
    segment_ids_by_block: dict[str, list[str]] = {}
    segments_by_first_block: dict[str, list[Any]] = {}
    unbound_segments: list[Any] = []
    for segment in segments:
        source_block_ids = _string_list(
            str(segment["source_block_ids_json"]),
            field="segments.source_block_ids_json",
        )
        if any(block_id not in block_by_id for block_id in source_block_ids):
            raise RuntimeError("segment references a missing document block in release IR")
        for block_id in source_block_ids:
            segment_ids_by_block.setdefault(block_id, []).append(str(segment["segment_id"]))
        if source_block_ids:
            segments_by_first_block.setdefault(source_block_ids[0], []).append(segment)
        else:
            unbound_segments.append(segment)

    for block in blocks:
        block_id = str(block["block_id"])
        for segment in segments_by_first_block.get(block_id, []):
            parts.extend(
                (
                    "",
                    _render_segment_locator(
                        segment,
                        block_by_id=block_by_id,
                        release_id=release_id,
                    ),
                )
            )
        parts.extend(
            (
                "",
                _render_block_locator(
                    block,
                    release_id=release_id,
                    segment_ids=tuple(segment_ids_by_block.get(block_id, [])),
                ),
            )
        )
        text = str(block["text"]).strip()
        if not text or text == document["title"]:
            continue
        parts.append("")
        if bool(block["review_required"]):
            parts.extend(
                (
                    "> [!WARNING]",
                    "> 此段抽取结果尚需按定位信息对照原件，不得作为已核验主证据。",
                    "",
                )
            )
        kind = str(block["kind"])
        if kind in {"heading", "title"}:
            parts.append(f"## {text}")
        elif kind == "table":
            parts.extend(("```text", text, "```"))
        else:
            parts.append(text)
    for segment in unbound_segments:
        parts.extend(
            (
                "",
                _render_segment_locator(
                    segment,
                    block_by_id=block_by_id,
                    release_id=release_id,
                ),
            )
        )
    return "\n".join(parts).rstrip() + "\n"


def export_markdown(database: Path, output_root: Path) -> dict[str, Any]:
    """Export deterministic human-readable views from one verified release IR."""

    output_root = output_root.expanduser().resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("Markdown output directory must be empty")
    output_root.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    with DeepLaw(database) as law:
        documents = law.connection.execute(
            """
            SELECT document_id, title, relative_path, source_sha256, official_source,
                   status, effective_from, effective_to
            FROM documents
            ORDER BY relative_path, document_id
            """
        ).fetchall()
        for index, document in enumerate(documents, start=1):
            blocks = law.connection.execute(
                """
                SELECT block_id, document_id, ordinal, kind, page, paragraph,
                       bbox_json, review_required, risk_flags_json, text, text_sha256
                FROM document_blocks
                WHERE document_id = ?
                ORDER BY ordinal
                """,
                (document["document_id"],),
            ).fetchall()
            segments = law.connection.execute(
                """
                SELECT segment_id, document_id, ordinal, kind, page_start, page_end,
                       paragraph_start, paragraph_end, text_sha256,
                       source_block_ids_json, extraction_review_required,
                       extraction_risk_flags_json
                FROM segments
                WHERE document_id = ?
                ORDER BY ordinal
                """,
                (document["document_id"],),
            ).fetchall()
            rendered = _render_document(
                document,
                blocks,
                segments,
                release_id=law.release_id,
            )
            relative_path = f"{index:04d}-{document['document_id']}.md"
            payload = rendered.encode("utf-8")
            (output_root / relative_path).write_bytes(payload)
            files.append(
                {
                    "document_id": document["document_id"],
                    "path": relative_path,
                    "sha256": sha256_bytes(payload),
                }
            )
        manifest = {
            "schema_version": MARKDOWN_EXPORT_SCHEMA,
            "release_id": law.release_id,
            "database_sha256": law.artifact["database_sha256"],
            "document_count": len(files),
            "files": files,
        }
    manifest_payload = (canonical_json(manifest) + "\n").encode("utf-8")
    (output_root / "index.json").write_bytes(manifest_payload)
    return manifest
