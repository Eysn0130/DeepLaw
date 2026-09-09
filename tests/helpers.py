from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_private_broker_interpreter(root: Path) -> tuple[Path, str]:
    """Build one real, owner-only interpreter for broker fixture execution."""

    if os.name != "posix":
        raise RuntimeError("private broker interpreter construction is POSIX-only")
    if not hasattr(os, "geteuid"):
        raise RuntimeError("private broker interpreter owner identity is unavailable")
    source = Path(sys.executable).resolve(strict=True)
    try:
        source_before = source.lstat()
        source_hash = sha256(source)
        source_mode = stat.S_IMODE(source_before.st_mode)
        source_uid = source_before.st_uid
        source_links = source_before.st_nlink
    except OSError:
        raise RuntimeError("private broker interpreter source is unavailable") from None
    if not stat.S_ISREG(source_before.st_mode):
        raise RuntimeError("private broker interpreter source metadata is unsafe")

    root = root.resolve(strict=True) / "private-broker-interpreter"
    try:
        root.mkdir(mode=0o700)
        binary_root = root / "bin"
        library_root = root / "lib"
        binary_root.mkdir(mode=0o700)
        library_root.mkdir(mode=0o700)
        candidate = binary_root / "python"
        shutil.copyfile(source, candidate)
        candidate.chmod(0o500)
        _copy_matching_python_library(library_root)
        stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
        stdlib_link = library_root / (
            f"python{sys.version_info.major}.{sys.version_info.minor}"
        )
        stdlib_link.symlink_to(stdlib, target_is_directory=True)
    except (OSError, TypeError, ValueError):
        raise RuntimeError("private broker interpreter construction failed") from None

    _assert_private_interpreter(
        candidate,
        source_hash=source_hash,
        source_mode=source_mode,
        source_uid=source_uid,
        source_links=source_links,
        source=source,
    )
    version = _probe_private_interpreter(candidate, isolated=False)
    isolated_version = _probe_private_interpreter(candidate, isolated=True)
    if version is None or isolated_version != version.removeprefix("Python "):
        raise RuntimeError("private broker interpreter probe failed")
    return candidate, version


def _probe_private_interpreter(path: Path, *, isolated: bool) -> str | None:
    arguments = (
        [
            "-I",
            "-S",
            "-B",
            "-c",
            "import sys,json,pathlib,encodings; print(sys.version.split()[0])",
        ]
        if isolated
        else ["-B", "--version"]
    )
    try:
        completed = subprocess.run(
            [str(path), *arguments],
            capture_output=True,
            check=False,
            timeout=5,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    try:
        observed = (completed.stdout + completed.stderr).decode("utf-8", errors="strict").strip()
    except UnicodeError:
        return None
    if isolated:
        return observed if observed.startswith("3.") else None
    return observed if observed.startswith("Python ") else None


def _copy_matching_python_library(library_root: Path) -> None:
    if sys.platform != "darwin":
        return
    library_directory = sysconfig.get_config_var("LIBDIR")
    library_name = sysconfig.get_config_var("LDLIBRARY")
    if (
        not isinstance(library_directory, str)
        or not isinstance(library_name, str)
        or not library_name.endswith(".dylib")
    ):
        return
    source = Path(library_directory) / library_name
    if not source.is_file() or source.is_symlink():
        return
    target = library_root / source.name
    shutil.copyfile(source, target)
    target.chmod(0o500)


def _assert_private_interpreter(
    path: Path,
    *,
    source_hash: str,
    source_mode: int,
    source_uid: int,
    source_links: int,
    source: Path,
) -> None:
    try:
        details = path.lstat()
        candidate_hash = sha256(path)
    except OSError:
        raise RuntimeError("private broker interpreter metadata is unavailable") from None
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or stat.S_IMODE(details.st_mode) != 0o500
        or details.st_uid != os.geteuid()
        or candidate_hash != source_hash
    ):
        raise RuntimeError("private broker interpreter metadata is unsafe")
    try:
        source_after = source.lstat()
        source_after_hash = sha256(source)
    except OSError:
        raise RuntimeError("private broker interpreter source changed") from None
    if (
        stat.S_IMODE(source_after.st_mode) != source_mode
        or source_after.st_uid != source_uid
        or source_after.st_nlink != source_links
        or source_after_hash != source_hash
    ):
        raise RuntimeError("private broker interpreter source changed")


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
