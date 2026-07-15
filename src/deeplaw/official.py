from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__
from .admin_lock import administration_locked
from .ingest import build_release
from .models import BuildReport
from .store import activate_release, default_home, verify_release_artifact
from .util import canonical_date, sha256_bytes, sha256_file

OFFICIAL_CATALOG_SCHEMA = "deeplaw.official-catalog/v1"
OFFICIAL_STATE_SCHEMA = "deeplaw.official-state/v1"
DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/Eysn0130/DeepLaw/main/"
    "catalogs/deeplaw-official-cn.json"
)
_MAX_CATALOG_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_BYTES = 512 * 1024 * 1024
_MAX_DOWNLOAD_ENVELOPE_BYTES = 64 * 1024
_RELEASE_ID = re.compile(r"^lawrel_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CATALOG_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CATALOG_REQUIRED_FIELDS = {
    "schemaVersion",
    "catalogId",
    "sequence",
    "version",
    "publishedOn",
    "package",
    "documents",
}
_CATALOG_OPTIONAL_FIELDS = {"reviewOverlay", "buildPolicy"}
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
_STATE_FIELDS = {
    "schema_version",
    "enabled",
    "active_release_id",
    "installed_release_ids",
    "catalog",
}
_STATE_CATALOG_FIELDS = {
    "catalog_id",
    "sequence",
    "version",
    "published_on",
    "sha256",
    "source",
    "synced_at",
}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def official_home(home: str | Path | None = None) -> Path:
    root = Path(home).expanduser() if home is not None else default_home()
    root = root.absolute()
    if root.is_symlink():
        raise RuntimeError(f"DeepLaw home must not be a symbolic link: {root}")
    return root / "official"


def bundled_catalog_path() -> Path:
    packaged = Path(__file__).resolve().parent / "catalogs" / "deeplaw-official-cn.json"
    if packaged.is_file():
        return packaged
    repository = Path(__file__).resolve().parents[2] / "catalogs" / "deeplaw-official-cn.json"
    if repository.is_file():
        return repository
    raise RuntimeError("bundled DeepLaw official catalog is missing")


def _bundled_governance_path(resource: str) -> Path | None:
    packaged = Path(__file__).resolve().parent / "governance" / resource
    if packaged.is_file():
        return packaged
    repository = Path(__file__).resolve().parents[2] / "governance" / resource
    if repository.is_file():
        return repository
    return None


def _secure_directory(path: Path, *, mode: int = 0o700) -> Path:
    if path.is_symlink():
        raise RuntimeError(f"official library directory must not be a symbolic link: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if not path.is_dir():
        raise RuntimeError(f"official library path is not a directory: {path}")
    return path


def _state_path(home: str | Path | None = None) -> Path:
    return official_home(home) / "state.json"


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": OFFICIAL_STATE_SCHEMA,
        "enabled": False,
        "active_release_id": None,
        "installed_release_ids": [],
        "catalog": None,
    }


def _validate_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
        raise RuntimeError("official state does not match its closed contract")
    if value.get("schema_version") != OFFICIAL_STATE_SCHEMA:
        raise RuntimeError("unsupported official state schema")
    if not isinstance(value.get("enabled"), bool):
        raise RuntimeError("official enabled state is invalid")
    active = value.get("active_release_id")
    if active is not None and (not isinstance(active, str) or not _RELEASE_ID.fullmatch(active)):
        raise RuntimeError("official active release ID is invalid")
    installed = value.get("installed_release_ids")
    if (
        not isinstance(installed, list)
        or len(installed) > 10_000
        or len(set(installed)) != len(installed)
        or any(not isinstance(item, str) or not _RELEASE_ID.fullmatch(item) for item in installed)
    ):
        raise RuntimeError("official installed release IDs are invalid")
    if active is not None and active not in installed:
        raise RuntimeError("official active release is not registered as installed")
    catalog = value.get("catalog")
    if catalog is not None:
        if not isinstance(catalog, dict) or set(catalog) != _STATE_CATALOG_FIELDS:
            raise RuntimeError("official catalog state does not match its closed contract")
        if not _CATALOG_ID.fullmatch(str(catalog.get("catalog_id", ""))):
            raise RuntimeError("official catalog identity is invalid")
        sequence = catalog.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise RuntimeError("official catalog sequence is invalid")
        if not isinstance(catalog.get("version"), str) or not catalog["version"]:
            raise RuntimeError("official catalog version is invalid")
        canonical_date(str(catalog.get("published_on", "")), field="published_on")
        if not _SHA256.fullmatch(str(catalog.get("sha256", ""))):
            raise RuntimeError("official catalog SHA-256 is invalid")
        for field_name in ("source", "synced_at"):
            if not isinstance(catalog.get(field_name), str) or not catalog[field_name]:
                raise RuntimeError(f"official catalog {field_name} is invalid")
    if value["enabled"] and active is None:
        raise RuntimeError("enabled official state requires an active release")
    return value


def _load_state(home: str | Path | None = None) -> dict[str, Any]:
    root = official_home(home)
    if root.is_symlink():
        raise RuntimeError(f"official library directory must not be a symbolic link: {root}")
    path = root / "state.json"
    if path.is_symlink():
        raise RuntimeError(f"official state must not be a symbolic link: {path}")
    if not path.exists():
        return _empty_state()
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise RuntimeError("official state is missing, unsafe, or too large")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("official state cannot be read") from error
    return _validate_state(value)


def _write_state(state: dict[str, Any], *, home: str | Path | None = None) -> None:
    _validate_state(state)
    root = _secure_directory(official_home(home))
    path = root / "state.json"
    if path.is_symlink():
        raise RuntimeError(f"official state must not be a symbolic link: {path}")
    temporary = root / ".state.json.tmp"
    if temporary.is_symlink():
        raise RuntimeError(f"official state temporary must not be a symbolic link: {temporary}")
    temporary.unlink(missing_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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


def _download_bytes(url: str, *, maximum: int, timeout: float = 60.0) -> bytes:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("official network sources must use credential-free HTTPS URLs")
    request = Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": f"DeepLaw/{__version__} official-catalog-client",
        },
    )
    with _urlopen_with_retry(request, timeout=timeout) as response:
        final = urlparse(response.geturl())
        if (
            final.scheme != "https"
            or not final.hostname
            or final.username is not None
            or final.password is not None
        ):
            raise RuntimeError("official download redirected to an unsafe URL")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = response.read(min(1024 * 1024, maximum + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                raise RuntimeError(f"official download exceeds {maximum} bytes")
            chunks.append(chunk)
        return b"".join(chunks)


def _urlopen_with_retry(request: Request, *, timeout: float) -> Any:
    attempts = 5
    for attempt in range(attempts):
        try:
            return urlopen(request, timeout=timeout)
        except HTTPError as error:
            if error.code not in _RETRYABLE_HTTP_STATUS or attempt == attempts - 1:
                raise
            error.close()
        except (TimeoutError, URLError):
            if attempt == attempts - 1:
                raise
        time.sleep(0.5 * (2**attempt))
    raise RuntimeError("official download retry loop ended unexpectedly")


def _read_catalog(source: str | Path | None) -> tuple[dict[str, Any], bytes, str]:
    if source is None:
        path = bundled_catalog_path()
        payload = path.read_bytes()
        label = "bundled"
    else:
        raw_source = str(source)
        if raw_source.startswith("https://"):
            payload = _download_bytes(raw_source, maximum=_MAX_CATALOG_BYTES)
            label = raw_source
        else:
            path = Path(source).expanduser()
            if path.is_symlink():
                raise ValueError("official catalog must not be a symbolic link")
            path = path.resolve(strict=True)
            if not path.is_file():
                raise ValueError("official catalog must be a regular file")
            payload = path.read_bytes()
            label = "local"
    if len(payload) > _MAX_CATALOG_BYTES:
        raise ValueError("official catalog exceeds the 64 MiB limit")
    try:
        catalog = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("official catalog is not valid UTF-8 JSON") from error
    return _validate_catalog(catalog), payload, label


def _validate_catalog(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or not set(value) >= _CATALOG_REQUIRED_FIELDS
        or set(value) - _CATALOG_REQUIRED_FIELDS - _CATALOG_OPTIONAL_FIELDS
    ):
        raise ValueError("official catalog does not match its closed contract")
    if value.get("schemaVersion") != OFFICIAL_CATALOG_SCHEMA:
        raise ValueError("unsupported official catalog schema")
    catalog_id = value.get("catalogId")
    if not isinstance(catalog_id, str) or not _CATALOG_ID.fullmatch(catalog_id):
        raise ValueError("official catalog ID is invalid")
    sequence = value.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise ValueError("official catalog sequence must be a positive integer")
    version = value.get("version")
    if not isinstance(version, str) or not version or len(version) > 100:
        raise ValueError("official catalog version is invalid")
    published_on = value.get("publishedOn")
    if not isinstance(published_on, str):
        raise ValueError("official catalog publishedOn is invalid")
    canonical_date(published_on, field="publishedOn")
    review_overlay = value.get("reviewOverlay")
    if review_overlay is not None:
        if not isinstance(review_overlay, dict) or set(review_overlay) != {
            "resource",
            "url",
            "sha256",
        }:
            raise ValueError("official catalog reviewOverlay is invalid")
        resource = review_overlay.get("resource")
        if (
            not isinstance(resource, str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\.json", resource)
        ):
            raise ValueError("official catalog reviewOverlay resource is invalid")
        review_url = urlparse(str(review_overlay.get("url", "")))
        if (
            review_url.scheme != "https"
            or not review_url.hostname
            or review_url.username is not None
            or review_url.password is not None
        ):
            raise ValueError("official catalog reviewOverlay URL is invalid")
        if not _SHA256.fullmatch(str(review_overlay.get("sha256", ""))):
            raise ValueError("official catalog reviewOverlay SHA-256 is invalid")
    build_policy = value.get("buildPolicy")
    if build_policy is not None and (
        not isinstance(build_policy, dict)
        or set(build_policy) != {"pdfFallback", "allowNeedsOcr"}
        or build_policy.get("pdfFallback") not in {"off", "vision-consensus"}
        or not isinstance(build_policy.get("allowNeedsOcr"), bool)
    ):
        raise ValueError("official catalog buildPolicy is invalid")
    package = value.get("package")
    documents = value.get("documents")
    if not isinstance(package, dict):
        raise ValueError("official catalog package must be an object")
    if not isinstance(documents, list) or not documents or len(documents) > 10_000:
        raise ValueError("official catalog documents are invalid")
    if package.get("documentCount") != len(documents):
        raise ValueError("official catalog documentCount does not match its documents")
    seen_paths: set[str] = set()
    seen_hashes: set[str] = set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("official catalog document must be an object")
        relative_path = document.get("path")
        title = document.get("title")
        format_name = document.get("format")
        source_url = document.get("officialSource")
        byte_size = document.get("byteSize")
        source_hash = document.get("sha256")
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path in seen_paths
            or Path(relative_path).is_absolute()
            or any(part in {".", ".."} for part in Path(relative_path).parts)
        ):
            raise ValueError("official catalog contains an invalid or duplicate path")
        seen_paths.add(relative_path)
        if not isinstance(title, str) or not title or len(title) > 500:
            raise ValueError(f"official catalog title is invalid: {relative_path}")
        suffix = {"DOCX": ".docx", "PDF": ".pdf"}.get(format_name)
        if suffix is None or Path(relative_path).suffix.lower() != suffix:
            raise ValueError(f"official catalog format is invalid: {relative_path}")
        parsed_source = urlparse(str(source_url))
        if (
            not isinstance(source_url, str)
            or parsed_source.scheme != "https"
            or not parsed_source.hostname
            or parsed_source.username is not None
            or parsed_source.password is not None
        ):
            raise ValueError(f"official catalog source URL is invalid: {relative_path}")
        if (
            isinstance(byte_size, bool)
            or not isinstance(byte_size, int)
            or not 1 <= byte_size <= _MAX_SOURCE_BYTES
        ):
            raise ValueError(f"official catalog byte size is invalid: {relative_path}")
        if (
            not isinstance(source_hash, str)
            or not _SHA256.fullmatch(source_hash)
            or source_hash in seen_hashes
        ):
            raise ValueError(f"official catalog SHA-256 is invalid or duplicated: {relative_path}")
        seen_hashes.add(source_hash)
    return value


def _save_catalog(
    catalog: dict[str, Any],
    payload: bytes,
    *,
    home: str | Path | None,
) -> Path:
    root = _secure_directory(official_home(home) / "catalogs")
    digest = sha256_bytes(payload)
    path = root / f"{catalog['sequence']:08d}-{digest}.json"
    if path.exists():
        if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            raise RuntimeError("stored official catalog failed integrity validation")
        return path
    temporary = root / f".{path.name}.tmp"
    if temporary.is_symlink():
        raise RuntimeError(f"official catalog temporary must not be a symbolic link: {temporary}")
    temporary.unlink(missing_ok=True)
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o444)
    os.replace(temporary, path)
    return path


def _resolve_review_overlay(
    catalog: dict[str, Any],
    *,
    source_label: str,
    home: str | Path | None,
) -> Path | None:
    declaration = catalog.get("reviewOverlay")
    if declaration is None:
        return None
    expected_hash = declaration["sha256"]
    payload: bytes | None = None
    bundled = _bundled_governance_path(declaration["resource"])
    if bundled is not None and sha256_file(bundled) == expected_hash:
        payload = bundled.read_bytes()
    elif source_label == "bundled":
        raise RuntimeError("bundled official review overlay is missing or has the wrong hash")
    if payload is None:
        payload = _download_bytes(declaration["url"], maximum=_MAX_CATALOG_BYTES)
    if sha256_bytes(payload) != expected_hash:
        raise RuntimeError("official review overlay SHA-256 changed")
    root = _secure_directory(official_home(home) / "reviews")
    path = root / f"{expected_hash}.json"
    if path.exists():
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected_hash:
            raise RuntimeError("stored official review overlay failed integrity validation")
        return path
    temporary = root / f".{path.name}.tmp"
    if temporary.is_symlink():
        raise RuntimeError(
            f"official review overlay temporary must not be a symbolic link: {temporary}"
        )
    temporary.unlink(missing_ok=True)
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o444)
    os.replace(temporary, path)
    return path


def _download_source(document: dict[str, Any], destination: Path) -> None:
    source_url = _resolve_source_download_url(document)
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("official network sources must use credential-free HTTPS URLs")
    request = Request(
        source_url,
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": f"DeepLaw/{__version__} official-source-client",
        },
    )
    temporary = destination.with_name(f".{destination.name}.tmp")
    if temporary.is_symlink():
        raise RuntimeError(f"official source temporary must not be a symbolic link: {temporary}")
    temporary.unlink(missing_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with (
            _urlopen_with_retry(request, timeout=60.0) as response,
            temporary.open("xb") as stream,
        ):
            final = urlparse(response.geturl())
            if (
                final.scheme != "https"
                or not final.hostname
                or final.username is not None
                or final.password is not None
            ):
                raise RuntimeError("official download redirected to an unsafe URL")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > document["byteSize"] or size > _MAX_SOURCE_BYTES:
                    raise RuntimeError(f"official source byte size changed: {document['path']}")
                digest.update(chunk)
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if size != document["byteSize"]:
            raise RuntimeError(f"official source byte size changed: {document['path']}")
        if digest.hexdigest() != document["sha256"]:
            raise RuntimeError(f"official source SHA-256 changed: {document['path']}")
        os.chmod(temporary, 0o444)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _resolve_source_download_url(document: dict[str, Any]) -> str:
    source_url = str(document["officialSource"])
    parsed = urlparse(source_url)
    if parsed.hostname != "flk.npc.gov.cn" or parsed.path != "/law-search/download/pc":
        return source_url

    payload = _download_bytes(source_url, maximum=_MAX_DOWNLOAD_ENVELOPE_BYTES)
    try:
        envelope = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("National Laws Database download envelope is invalid") from error
    if (
        not isinstance(envelope, dict)
        or envelope.get("code") != 200
        or not isinstance(envelope.get("data"), dict)
    ):
        raise RuntimeError("National Laws Database download envelope was rejected")
    resolved_url = envelope["data"].get("url")
    if not isinstance(resolved_url, str):
        raise RuntimeError("National Laws Database download URL is missing")
    resolved = urlparse(resolved_url)
    if (
        resolved.scheme != "https"
        or resolved.hostname != "flkoss.obs-bj2.cucloud.cn"
        or resolved.username is not None
        or resolved.password is not None
        or Path(resolved.path).suffix.lower() != Path(document["path"]).suffix.lower()
    ):
        raise RuntimeError("National Laws Database download URL is unsafe")
    return resolved_url


def _materialize_downloaded_sources(
    catalog: dict[str, Any],
    *,
    home: str | Path | None,
) -> Path:
    root = _secure_directory(official_home(home))
    cache = _secure_directory(root / "sources")
    workspace = Path(tempfile.mkdtemp(prefix=".official-sources-", dir=root))
    try:
        for document in catalog["documents"]:
            suffix = Path(document["path"]).suffix.lower()
            cached = cache / f"{document['sha256']}{suffix}"
            if cached.exists():
                if (
                    cached.is_symlink()
                    or not cached.is_file()
                    or cached.stat().st_size != document["byteSize"]
                    or sha256_file(cached) != document["sha256"]
                ):
                    raise RuntimeError(
                        f"official source cache failed validation: {document['path']}"
                    )
            else:
                try:
                    _download_source(document, cached)
                except Exception as error:
                    raise RuntimeError(
                        f"official source download failed: {document['path']}: {error}"
                    ) from error
            destination = workspace / document["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(cached, destination)
            except OSError:
                shutil.copyfile(cached, destination)
        return workspace
    except BaseException:
        shutil.rmtree(workspace, ignore_errors=True)
        raise


def _active_pointer(home: str | Path | None = None) -> Path:
    root = Path(home).expanduser().absolute() if home is not None else default_home().absolute()
    return root / "ACTIVE"


def _snapshot_active(home: str | Path | None = None) -> str | None:
    active = _active_pointer(home)
    if active.is_symlink():
        raise RuntimeError(f"official ACTIVE pointer must not be a symbolic link: {active}")
    if not active.exists():
        return None
    if not active.is_file() or active.stat().st_size > 128:
        raise RuntimeError("official ACTIVE pointer is unsafe")
    release_id = active.read_text(encoding="utf-8").strip()
    if not _RELEASE_ID.fullmatch(release_id):
        raise RuntimeError("official ACTIVE pointer is invalid")
    return release_id


def _restore_active(
    release_id: str | None,
    *,
    home: str | Path | None = None,
) -> None:
    active = _active_pointer(home)
    if release_id is None:
        active.unlink(missing_ok=True)
        return
    base_home = (
        Path(home).expanduser().absolute() if home is not None else default_home().absolute()
    )
    activate_release(base_home / "releases", release_id)


def _remove_active_if_registered(state: dict[str, Any], *, home: str | Path | None = None) -> None:
    active = _active_pointer(home)
    if active.is_symlink():
        raise RuntimeError(f"official ACTIVE pointer must not be a symbolic link: {active}")
    if not active.exists():
        return
    if not active.is_file() or active.stat().st_size > 128:
        raise RuntimeError("official ACTIVE pointer is unsafe")
    release_id = active.read_text(encoding="utf-8").strip()
    if release_id in state["installed_release_ids"]:
        active.unlink()


def _build_report_summary(report: BuildReport, release_dir: Path) -> dict[str, Any]:
    review_required_documents = sum(
        bool(document.get("review_required")) for document in report.documents
    )
    review_required_pages = sum(
        bool(page.get("review_required"))
        for document in report.documents
        for page in document.get("page_evidence", [])
    )
    return {
        "schema_version": report.schema_version,
        "release_id": report.release_id,
        "document_count": report.document_count,
        "segment_count": report.segment_count,
        "relation_count": report.relation_count,
        "source_bytes": report.source_bytes,
        "extractors": dict(report.extractors),
        "warning_count": len(report.warnings),
        "review_required_document_count": review_required_documents,
        "review_required_page_count": review_required_pages,
        "build_report": str(release_dir / "build-report.json"),
    }


def _reuse_unchanged_release(
    state: dict[str, Any],
    *,
    update: bool,
    home: str | Path | None,
) -> dict[str, Any] | None:
    release_id = state["active_release_id"]
    if release_id is None:
        return None
    base_home = (
        Path(home).expanduser().absolute() if home is not None else default_home().absolute()
    )
    release_dir = base_home / "releases" / release_id
    database = release_dir / "deeplaw.sqlite3"
    if not database.exists():
        return None
    artifact = verify_release_artifact(database)
    catalog = state["catalog"]
    if catalog is None or artifact.get("source_manifest_sha256") != catalog["sha256"]:
        raise RuntimeError("installed official release does not match its catalog state")

    should_enable = state["enabled"] if update else True
    previous_active = _snapshot_active(home)
    active_changed = should_enable and previous_active != release_id
    state_changed = should_enable != state["enabled"]
    if active_changed or state_changed:
        try:
            if should_enable:
                activate_release(base_home / "releases", release_id)
            if state_changed:
                _write_state({**state, "enabled": should_enable}, home=home)
        except BaseException:
            _restore_active(previous_active, home=home)
            raise
    return {
        "changed": False,
        "enabled": should_enable,
        "active_release_id": release_id,
        "catalog": catalog,
        "report": {
            "release_id": release_id,
            "document_count": artifact["document_count"],
            "segment_count": artifact["segment_count"],
            "build_report": str(release_dir / "build-report.json"),
            "cached": True,
        },
        "restart_required": active_changed or state_changed,
    }


@administration_locked(".official-admin.lock")
def sync_official(
    *,
    catalog_source: str | Path | None = None,
    source_root: str | Path | None = None,
    update: bool = False,
    pdf_fallback: str | None = None,
    home: str | Path | None = None,
) -> dict[str, Any]:
    if update and catalog_source is None:
        catalog_source = DEFAULT_CATALOG_URL
    catalog, payload, source_label = _read_catalog(catalog_source)
    declared_build_policy = catalog.get("buildPolicy", {})
    effective_pdf_fallback = pdf_fallback or declared_build_policy.get("pdfFallback", "off")
    effective_allow_needs_ocr = bool(declared_build_policy.get("allowNeedsOcr", False))
    state = _load_state(home)
    previous = state["catalog"]
    digest = sha256_bytes(payload)
    if previous is not None:
        if catalog["catalogId"] != previous["catalog_id"]:
            raise ValueError("official catalog ID cannot change during an update")
        if catalog["sequence"] < previous["sequence"]:
            raise ValueError("official catalog rollback is not allowed")
        if catalog["sequence"] == previous["sequence"] and digest != previous["sha256"]:
            raise ValueError("official catalog sequence was rewritten with different content")
        if digest == previous["sha256"]:
            reused = _reuse_unchanged_release(state, update=update, home=home)
            if reused is not None:
                return reused

    catalog_path = _save_catalog(catalog, payload, home=home)
    review_overlay_path = _resolve_review_overlay(
        catalog,
        source_label=source_label,
        home=home,
    )
    base_home = (
        Path(home).expanduser().absolute() if home is not None else default_home().absolute()
    )
    releases = _secure_directory(base_home / "releases")
    temporary_sources: Path | None = None
    if source_root is None:
        temporary_sources = _materialize_downloaded_sources(catalog, home=home)
        build_source_root = temporary_sources
    else:
        declared_root = Path(source_root).expanduser()
        if declared_root.is_symlink():
            raise ValueError("official source root must not be a symbolic link")
        build_source_root = declared_root.resolve(strict=True)
        if not build_source_root.is_dir():
            raise ValueError("official source root must be a directory")
    try:
        release_dir, report = build_release(
            source_root=build_source_root,
            manifest_path=catalog_path,
            output_root=releases,
            pdf_fallback=effective_pdf_fallback,
            allow_needs_ocr=effective_allow_needs_ocr,
            review_overlay_path=review_overlay_path,
        )
    finally:
        if temporary_sources is not None:
            shutil.rmtree(temporary_sources, ignore_errors=True)

    installed = list(state["installed_release_ids"])
    if release_dir.name not in installed:
        installed.append(release_dir.name)
    should_enable = not update or state["enabled"] or state["catalog"] is None
    next_state = {
        "schema_version": OFFICIAL_STATE_SCHEMA,
        "enabled": should_enable,
        "active_release_id": release_dir.name,
        "installed_release_ids": installed,
        "catalog": {
            "catalog_id": catalog["catalogId"],
            "sequence": catalog["sequence"],
            "version": catalog["version"],
            "published_on": catalog["publishedOn"],
            "sha256": digest,
            "source": source_label,
            "synced_at": _now(),
        },
    }
    previous_active = _snapshot_active(home)
    try:
        if should_enable:
            activate_release(releases, release_dir.name)
        _write_state(next_state, home=home)
    except BaseException:
        _restore_active(previous_active, home=home)
        raise
    return {
        "changed": previous is None or previous["sha256"] != digest,
        "enabled": should_enable,
        "active_release_id": release_dir.name,
        "catalog": next_state["catalog"],
        "report": _build_report_summary(report, release_dir),
        "restart_required": True,
    }


@administration_locked(".official-admin.lock")
def disable_official(*, home: str | Path | None = None) -> dict[str, Any]:
    state = _load_state(home)
    if not state["installed_release_ids"]:
        raise FileNotFoundError("DeepLaw official library is not installed")
    previous_active = _snapshot_active(home)
    try:
        _remove_active_if_registered(state, home=home)
        state = {**state, "enabled": False}
        _write_state(state, home=home)
    except BaseException:
        _restore_active(previous_active, home=home)
        raise
    return {"enabled": False, "restart_required": True}


@administration_locked(".official-admin.lock")
def enable_official(*, home: str | Path | None = None) -> dict[str, Any]:
    state = _load_state(home)
    release_id = state["active_release_id"]
    if release_id is None:
        raise FileNotFoundError("DeepLaw official library is not installed")
    base_home = (
        Path(home).expanduser().absolute() if home is not None else default_home().absolute()
    )
    database = base_home / "releases" / release_id / "deeplaw.sqlite3"
    verify_release_artifact(database)
    previous_active = _snapshot_active(home)
    try:
        activate_release(base_home / "releases", release_id)
        state = {**state, "enabled": True}
        _write_state(state, home=home)
    except BaseException:
        _restore_active(previous_active, home=home)
        raise
    return {"enabled": True, "active_release_id": release_id, "restart_required": True}


@administration_locked(".official-admin.lock")
def uninstall_official(*, home: str | Path | None = None) -> dict[str, Any]:
    state = _load_state(home)
    _remove_active_if_registered(state, home=home)
    base_home = (
        Path(home).expanduser().absolute() if home is not None else default_home().absolute()
    )
    releases = base_home / "releases"
    removed: list[str] = []
    if releases.exists():
        if releases.is_symlink() or not releases.is_dir():
            raise RuntimeError("DeepLaw releases directory is unsafe")
        for release_id in state["installed_release_ids"]:
            release_dir = releases / release_id
            if release_dir.is_symlink():
                raise RuntimeError(f"official release must not be a symbolic link: {release_dir}")
            if release_dir.exists():
                shutil.rmtree(release_dir)
                removed.append(release_id)
    root = official_home(home)
    if root.is_symlink():
        raise RuntimeError(f"official library directory must not be a symbolic link: {root}")
    if root.exists():
        shutil.rmtree(root)
    return {"installed": False, "removed_release_ids": removed, "restart_required": True}


def official_status(*, home: str | Path | None = None) -> dict[str, Any]:
    state = _load_state(home)
    return {
        **state,
        "installed": bool(state["installed_release_ids"]),
        "update_catalog_url": DEFAULT_CATALOG_URL,
        "trust_model": (
            "HTTPS team catalog, monotonic sequence, catalog SHA-256, "
            "and per-source byte size/SHA-256"
        ),
    }


def active_official_release_id(*, home: str | Path | None = None) -> str | None:
    state = _load_state(home)
    release_id = state["active_release_id"]
    if not state["enabled"] or release_id is None:
        return None
    return release_id if _snapshot_active(home) == release_id else None
