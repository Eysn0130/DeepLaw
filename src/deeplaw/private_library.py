from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .admin_lock import administration_locked
from .ingest import build_release
from .store import activate_release, default_home, resolve_active_database
from .util import canonical_date, sha256_file, stable_id

PRIVATE_LIBRARY_SCHEMA = "deeplaw.private-library/v1"
PRIVATE_LIBRARY_ID = "local-user"
_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_RELEASE_ID = re.compile(r"^lawrel_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DOCUMENT_TYPES = {
    "law",
    "administrative_regulation",
    "judicial_interpretation",
    "prosecution_standard",
    "departmental_rule",
    "normative_document",
    "case_reference",
}
_STATE_FIELDS = {
    "schema_version",
    "library_id",
    "revision",
    "active_release_id",
    "documents",
    "pdf_fallback",
    "allow_needs_ocr",
}
_DOCUMENT_FIELDS = {
    "document_id",
    "title",
    "format",
    "source_sha256",
    "byte_size",
    "storage_name",
    "document_type",
    "issuer",
    "effective_from",
    "effective_to",
    "added_at",
}


def private_home(home: str | Path | None = None) -> Path:
    root = Path(home).expanduser() if home is not None else default_home()
    root = root.absolute()
    if root.is_symlink():
        raise RuntimeError(f"DeepLaw home must not be a symbolic link: {root}")
    return root / "private"


def _secure_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"private library directory must not be a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise RuntimeError(f"private library path is not a directory: {path}")
    os.chmod(path, 0o700)
    return path


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": PRIVATE_LIBRARY_SCHEMA,
        "library_id": PRIVATE_LIBRARY_ID,
        "revision": 0,
        "active_release_id": None,
        "documents": [],
        "pdf_fallback": "off",
        "allow_needs_ocr": False,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
        raise RuntimeError("private library state does not match its closed contract")
    if value.get("schema_version") != PRIVATE_LIBRARY_SCHEMA:
        raise RuntimeError("unsupported private library state schema")
    if value.get("library_id") != PRIVATE_LIBRARY_ID:
        raise RuntimeError("unsupported private library identity")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise RuntimeError("private library revision is invalid")
    active_release_id = value.get("active_release_id")
    if active_release_id is not None and (
        not isinstance(active_release_id, str) or not _RELEASE_ID.fullmatch(active_release_id)
    ):
        raise RuntimeError("private library active release ID is invalid")
    documents = value.get("documents")
    if not isinstance(documents, list) or len(documents) > 10_000:
        raise RuntimeError("private library documents are invalid")
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for document in documents:
        if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
            raise RuntimeError("private document state does not match its closed contract")
        document_id = document.get("document_id")
        source_sha256 = document.get("source_sha256")
        if (
            not isinstance(document_id, str)
            or not re.fullmatch(r"doc_[0-9a-f]{24}", document_id)
            or document_id in seen_ids
            or not isinstance(source_sha256, str)
            or not _SHA256.fullmatch(source_sha256)
            or source_sha256 in seen_hashes
        ):
            raise RuntimeError("private document identity is invalid or duplicated")
        seen_ids.add(document_id)
        seen_hashes.add(source_sha256)
        title = document.get("title")
        format_name = document.get("format")
        storage_name = document.get("storage_name")
        byte_size = document.get("byte_size")
        if not isinstance(title, str) or not title or len(title) > 500:
            raise RuntimeError("private document title is invalid")
        suffix = {"DOCX": ".docx", "PDF": ".pdf", "TXT": ".txt"}.get(format_name)
        if suffix is None or storage_name != f"{source_sha256}{suffix}":
            raise RuntimeError("private document storage identity is invalid")
        if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 1:
            raise RuntimeError("private document byte size is invalid")
        if document.get("document_type") not in _DOCUMENT_TYPES:
            raise RuntimeError("private document type is invalid")
        issuer = document.get("issuer")
        if not isinstance(issuer, str) or not issuer or len(issuer) > 200:
            raise RuntimeError("private document issuer is invalid")
        for field_name in ("effective_from", "effective_to"):
            date_value = document.get(field_name)
            if date_value is not None:
                if not isinstance(date_value, str):
                    raise RuntimeError(f"private document {field_name} is invalid")
                canonical_date(date_value, field=field_name)
        if (
            document.get("effective_from")
            and document.get("effective_to")
            and document["effective_to"] <= document["effective_from"]
        ):
            raise RuntimeError("private document effective interval is invalid")
        added_at = document.get("added_at")
        if not isinstance(added_at, str) or len(added_at) > 40:
            raise RuntimeError("private document added_at is invalid")
    if not documents and active_release_id is not None:
        raise RuntimeError("empty private library cannot have an active release")
    if value.get("pdf_fallback") not in {
        "off",
        "vision-consensus",
        "document-engine",
    }:
        raise RuntimeError("private library PDF fallback is invalid")
    if not isinstance(value.get("allow_needs_ocr"), bool):
        raise RuntimeError("private library OCR policy is invalid")
    return value


def _state_path(root: Path) -> Path:
    return root / "library.json"


def _load_state(root: Path) -> dict[str, Any]:
    if root.is_symlink():
        raise RuntimeError(f"private library directory must not be a symbolic link: {root}")
    path = _state_path(root)
    if path.is_symlink():
        raise RuntimeError(f"private library state must not be a symbolic link: {path}")
    if not path.exists():
        return _empty_state()
    if not path.is_file() or path.stat().st_size > _MAX_STATE_BYTES:
        raise RuntimeError("private library state is missing, unsafe, or too large")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("private library state cannot be read") from error
    return _validate_state(value)


def _write_state(root: Path, state: dict[str, Any]) -> None:
    _validate_state(state)
    path = _state_path(root)
    if path.is_symlink():
        raise RuntimeError(f"private library state must not be a symbolic link: {path}")
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(".tmp")
    if temporary.is_symlink():
        raise RuntimeError(f"private state temporary must not be a symbolic link: {temporary}")
    temporary.unlink(missing_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _materialize_source(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def _manifest_document(document: dict[str, Any]) -> dict[str, Any]:
    value: dict[str, Any] = {
        "path": f"documents/{document['storage_name']}",
        "title": document["title"],
        "format": document["format"],
        "officialSource": f"private://source/{document['source_sha256']}",
        "byteSize": document["byte_size"],
        "sha256": document["source_sha256"],
        "documentType": document["document_type"],
        "issuer": document["issuer"],
        "authorityRank": 0,
        "status": "unknown",
        "note": (
            "用户私有法律资料；未经 DeepLaw 官方团队审核，"
            "不得标记或引用为 DeepLaw 官方法源。"
        ),
    }
    if document["effective_from"] is not None:
        value["effectiveDate"] = document["effective_from"]
    if document["effective_to"] is not None:
        value["effectiveTo"] = document["effective_to"]
    return value


def _remove_active(root: Path) -> None:
    active = root / "ACTIVE"
    if active.is_symlink():
        raise RuntimeError(f"private ACTIVE pointer must not be a symbolic link: {active}")
    active.unlink(missing_ok=True)


def _read_active(root: Path) -> str | None:
    active = root / "ACTIVE"
    if active.is_symlink():
        raise RuntimeError(f"private ACTIVE pointer must not be a symbolic link: {active}")
    if not active.exists():
        return None
    if not active.is_file() or active.stat().st_size > 128:
        raise RuntimeError("private ACTIVE pointer is unsafe")
    release_id = active.read_text(encoding="utf-8").strip()
    if not _RELEASE_ID.fullmatch(release_id):
        raise RuntimeError("private ACTIVE pointer is invalid")
    return release_id


def _restore_active(root: Path, release_id: str | None) -> None:
    if release_id is None:
        _remove_active(root)
        return
    activate_release(root / "releases", release_id)
    os.chmod(root / "ACTIVE", 0o600)


def _cleanup_releases(root: Path, *, keep: str | None) -> None:
    releases = root / "releases"
    if not releases.exists():
        return
    if releases.is_symlink() or not releases.is_dir():
        raise RuntimeError("private releases directory is unsafe")
    for child in releases.iterdir():
        if child.name == keep:
            continue
        if child.is_symlink():
            raise RuntimeError(f"private release must not be a symbolic link: {child}")
        if child.is_dir():
            shutil.rmtree(child)


def _cleanup_sources(root: Path, documents: list[dict[str, Any]]) -> None:
    sources = root / "sources"
    if not sources.exists():
        return
    if sources.is_symlink() or not sources.is_dir():
        raise RuntimeError("private sources directory is unsafe")
    retained = {document["storage_name"] for document in documents}
    for child in sources.iterdir():
        if child.name not in retained:
            if child.is_symlink() or not child.is_file():
                raise RuntimeError(f"private source cache contains an unsafe entry: {child}")
            child.unlink()


def _publish_snapshot(
    root: Path,
    state: dict[str, Any],
    documents: list[dict[str, Any]],
    *,
    pdf_fallback: str,
    allow_needs_ocr: bool,
) -> dict[str, Any]:
    previous_active = _read_active(root)
    requested_fallbacks = {state["pdf_fallback"], pdf_fallback}
    if "document-engine" in requested_fallbacks:
        effective_pdf_fallback = "document-engine"
    elif "vision-consensus" in requested_fallbacks:
        effective_pdf_fallback = "vision-consensus"
    else:
        effective_pdf_fallback = "off"
    effective_allow_needs_ocr = state["allow_needs_ocr"] or allow_needs_ocr
    next_state = {
        **state,
        "revision": state["revision"] + 1,
        "documents": documents,
        "pdf_fallback": effective_pdf_fallback,
        "allow_needs_ocr": effective_allow_needs_ocr,
    }
    if not documents:
        next_state["active_release_id"] = None
        try:
            _remove_active(root)
            _write_state(root, next_state)
        except BaseException:
            _restore_active(root, previous_active)
            raise
        _cleanup_releases(root, keep=None)
        _cleanup_sources(root, documents)
        return next_state

    workspace = Path(tempfile.mkdtemp(prefix=".private-build-", dir=root))
    try:
        source_root = workspace / "source"
        for document in documents:
            source = root / "sources" / document["storage_name"]
            if source.is_symlink() or not source.is_file():
                raise RuntimeError(
                    f"private source is missing or unsafe: {document['document_id']}"
                )
            if source.stat().st_size != document["byte_size"] or sha256_file(source) != document[
                "source_sha256"
            ]:
                raise RuntimeError(
                    f"private source failed integrity check: {document['document_id']}"
                )
            _materialize_source(source, source_root / "documents" / document["storage_name"])
        manifest = {
            "package": {
                "name": "DeepLaw user private legal library",
                "documentCount": len(documents),
            },
            "documents": [_manifest_document(document) for document in documents],
        }
        manifest_path = workspace / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        release_dir, _ = build_release(
            source_root=source_root,
            manifest_path=manifest_path,
            output_root=root / "releases",
            pdf_fallback=effective_pdf_fallback,
            allow_needs_ocr=effective_allow_needs_ocr,
            source_scope="user_private",
            library_id=PRIVATE_LIBRARY_ID,
            artifact_mode=0o400,
        )
        try:
            activate_release(root / "releases", release_dir.name)
            os.chmod(root / "ACTIVE", 0o600)
            next_state["active_release_id"] = release_dir.name
            _write_state(root, next_state)
        except BaseException:
            _restore_active(root, previous_active)
            raise
        _cleanup_releases(root, keep=release_dir.name)
        _cleanup_sources(root, documents)
        return next_state
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@administration_locked(".private-admin.lock")
def add_private_document(
    source: str | Path,
    *,
    title: str | None = None,
    document_type: str = "normative_document",
    issuer: str = "用户提供（未经 DeepLaw 官方审核）",
    effective_from: str | None = None,
    effective_to: str | None = None,
    confirm_no_case_data: bool = False,
    pdf_fallback: str = "off",
    allow_needs_ocr: bool = False,
    home: str | Path | None = None,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "private add requires explicit confirmation that the file is a legal reference, "
            "not Analytix case material"
        )
    declared_source = Path(source).expanduser()
    if declared_source.is_symlink():
        raise ValueError("private source must not be a symbolic link")
    resolved_source = declared_source.resolve(strict=True)
    if not stat.S_ISREG(resolved_source.stat().st_mode):
        raise ValueError("private source must be a regular file")
    if resolved_source.stat().st_size > _MAX_SOURCE_BYTES:
        raise ValueError("private source exceeds the 512 MiB limit")
    suffix = resolved_source.suffix.lower()
    format_name = {".docx": "DOCX", ".pdf": "PDF", ".txt": "TXT"}.get(suffix)
    if format_name is None:
        if suffix == ".doc":
            raise ValueError("legacy DOC is unsupported; convert it to DOCX, PDF, or UTF-8 TXT")
        raise ValueError("private source format must be DOCX, PDF, or UTF-8 TXT")
    normalized_title = (title or resolved_source.stem).strip()
    if not normalized_title or len(normalized_title) > 500:
        raise ValueError("private document title must contain 1 to 500 characters")
    if document_type not in _DOCUMENT_TYPES:
        raise ValueError(f"unsupported private document type: {document_type}")
    normalized_issuer = issuer.strip()
    if not normalized_issuer or len(normalized_issuer) > 200:
        raise ValueError("private document issuer must contain 1 to 200 characters")
    for field_name, value in (("effective_from", effective_from), ("effective_to", effective_to)):
        if value is not None:
            canonical_date(value, field=field_name)
    if effective_from and effective_to and effective_to <= effective_from:
        raise ValueError("effective_to must be after effective_from")

    root = _secure_directory(private_home(home))
    sources = _secure_directory(root / "sources")
    _secure_directory(root / "releases")
    state = _load_state(root)
    source_sha256 = sha256_file(resolved_source)
    document_id = stable_id("doc", source_sha256, normalized_title)
    if any(
        item["document_id"] == document_id or item["source_sha256"] == source_sha256
        for item in state["documents"]
    ):
        raise ValueError("private library already contains this source")
    storage_name = f"{source_sha256}{suffix}"
    stored_source = sources / storage_name
    created_source = False
    if stored_source.exists():
        if stored_source.is_symlink() or not stored_source.is_file():
            raise RuntimeError("private source cache entry is unsafe")
        if (
            stored_source.stat().st_size != resolved_source.stat().st_size
            or sha256_file(stored_source) != source_sha256
        ):
            raise RuntimeError("private source cache entry failed integrity validation")
    else:
        temporary = sources / f".{storage_name}.tmp"
        temporary.unlink(missing_ok=True)
        shutil.copyfile(resolved_source, temporary)
        if sha256_file(temporary) != source_sha256:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("private source changed while it was copied")
        os.chmod(temporary, 0o600)
        os.replace(temporary, stored_source)
        created_source = True

    document = {
        "document_id": document_id,
        "title": normalized_title,
        "format": format_name,
        "source_sha256": source_sha256,
        "byte_size": resolved_source.stat().st_size,
        "storage_name": storage_name,
        "document_type": document_type,
        "issuer": normalized_issuer,
        "effective_from": effective_from,
        "effective_to": effective_to,
        "added_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    try:
        next_state = _publish_snapshot(
            root,
            state,
            [*state["documents"], document],
            pdf_fallback=pdf_fallback,
            allow_needs_ocr=allow_needs_ocr,
        )
    except BaseException:
        if created_source:
            stored_source.unlink(missing_ok=True)
        raise
    return {
        "document": document,
        "active_release_id": next_state["active_release_id"],
        "document_count": len(next_state["documents"]),
    }


@administration_locked(".private-admin.lock")
def delete_private_document(
    document_id: str,
    *,
    pdf_fallback: str = "off",
    allow_needs_ocr: bool = False,
    home: str | Path | None = None,
) -> dict[str, Any]:
    root = _secure_directory(private_home(home))
    state = _load_state(root)
    documents = [item for item in state["documents"] if item["document_id"] != document_id]
    if len(documents) == len(state["documents"]):
        raise KeyError(f"unknown private document: {document_id}")
    next_state = _publish_snapshot(
        root,
        state,
        documents,
        pdf_fallback=pdf_fallback,
        allow_needs_ocr=allow_needs_ocr,
    )
    return {
        "deleted_document_id": document_id,
        "active_release_id": next_state["active_release_id"],
        "document_count": len(documents),
        "restart_required": True,
    }


def list_private_documents(*, home: str | Path | None = None) -> dict[str, Any]:
    root = private_home(home)
    state = _load_state(root) if root.exists() else _empty_state()
    documents = [
        {key: value for key, value in document.items() if key != "storage_name"}
        for document in state["documents"]
    ]
    return {
        "schema_version": PRIVATE_LIBRARY_SCHEMA,
        "library_id": PRIVATE_LIBRARY_ID,
        "revision": state["revision"],
        "active_release_id": state["active_release_id"],
        "document_count": len(documents),
        "documents": documents,
        "access_model": "local_os_user",
        "pdf_fallback": state["pdf_fallback"],
        "allow_needs_ocr": state["allow_needs_ocr"],
    }


def resolve_private_database(
    *,
    explicit_db: str | Path | None = None,
    home: str | Path | None = None,
) -> Path:
    if explicit_db is None:
        explicit_db = os.environ.get("DEEPLAW_PRIVATE_DB")
    return resolve_active_database(
        explicit_db=explicit_db,
        home=private_home(home),
        use_env_db=False,
    )


def active_private_release_id(*, home: str | Path | None = None) -> str | None:
    return _read_active(private_home(home))
