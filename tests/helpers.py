from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_docx(
    path: Path,
    paragraphs: list[str],
    *,
    footnote: tuple[int, str, int] | None = None,
) -> None:
    body: list[str] = []
    for index, paragraph in enumerate(paragraphs):
        text = escape(paragraph)
        run = f"<w:r><w:t>{text}</w:t></w:r>"
        if footnote and footnote[2] == index:
            run += f'<w:r><w:footnoteReference w:id="{footnote[0]}"/></w:r>'
        body.append(f"<w:p>{run}</w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}<w:sectPr/></w:body></w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        if footnote:
            note_id, note_text, _ = footnote
            notes = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                "<w:footnotes "
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:footnote w:id="{note_id}"><w:p><w:r><w:t>{escape(note_text)}</w:t>'
                "</w:r></w:p></w:footnote></w:footnotes>"
            )
            archive.writestr("word/footnotes.xml", notes)


def manifest_document(
    root: Path,
    relative_path: str,
    *,
    title: str,
    effective_date: str | None = "2020-01-01",
    status: str = "verified_current",
) -> dict[str, object]:
    path = root / relative_path
    value: dict[str, object] = {
        "path": relative_path,
        "title": title,
        "format": path.suffix.lstrip(".").upper(),
        "officialSource": f"https://example.gov.cn/{path.name}",
        "byteSize": path.stat().st_size,
        "sha256": sha256(path),
        "status": status,
    }
    if effective_date:
        value["effectiveDate"] = effective_date
    return value


def write_manifest(path: Path, documents: list[dict[str, object]]) -> Path:
    payload = {
        "package": {
            "name": "DeepLaw test package",
            "retrievedOn": "2026-07-15",
            "reviewedOn": "2026-07-15",
            "documentCount": len(documents),
        },
        "documents": documents,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
