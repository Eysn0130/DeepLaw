from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import math
import os
import queue
import re
import secrets
import shutil
import socket
import ssl
import stat
import threading
import time
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote_to_bytes, urljoin, urlsplit, urlunsplit

from .bounded_subprocess import BoundedSubprocessError, run_bounded_subprocess
from .knowledge_identity import (
    canonical_origin_commitment,
    make_collection_id,
    normalize_logical_path,
)
from .knowledge_models import canonical_timestamp, utc_now
from .knowledge_store import KnowledgeVault
from .util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads

SOURCE_SNAPSHOT_SCHEMA = "deeplaw.source-snapshot/v1"
MAX_SOURCE_SNAPSHOT_BYTES = 64 * 1024 * 1024

_MAX_SNAPSHOT_BYTES = MAX_SOURCE_SNAPSHOT_BYTES
_MAX_GIT_TOTAL_BYTES = 512 * 1024 * 1024
_MAX_GIT_FILES = 10_000
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_REDIRECTS = 5
_MAX_DNS_ADDRESSES = 32
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SNAPSHOT_ID = re.compile(r"^sourcesnapshot_[0-9a-f]{24}$")
_GIT_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REPOSITORY_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,61}[a-z0-9])?$")
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_URL_PATH = re.compile(r"^(?:[A-Za-z0-9._~!$&'()*+,;=:@/-]|%[0-9A-Fa-f]{2})*$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_GIT_TREE_ROW = re.compile(
    rb"^(?P<mode>[0-7]{6}) (?P<kind>[a-z]+) "
    rb"(?P<object>[0-9a-f]{40}|[0-9a-f]{64}) +(?P<size>-|[0-9]+)\t(?P<path>.+)$"
)
_SUPPORTED_SUFFIXES = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".pptx",
        ".xlsx",
        ".epub",
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
        ".htm",
        ".css",
        ".log",
    }
)
_MEDIA_SUFFIXES = {
    "application/epub+zip": ".epub",
    "application/json": ".json",
    "application/msword": ".doc",
    "application/pdf": ".pdf",
    "application/sql": ".sql",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/xml": ".xml",
    "text/csv": ".csv",
    "text/html": ".html",
    "text/markdown": ".md",
    "text/plain": ".txt",
    "text/tab-separated-values": ".tsv",
    "text/x-python": ".py",
    "text/yaml": ".yaml",
}
_TEXTUAL_SUFFIXES = _SUPPORTED_SUFFIXES - {
    ".doc",
    ".docx",
    ".epub",
    ".pdf",
    ".pptx",
    ".xlsx",
}


def _owner_directory(path: Path) -> Path:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise RuntimeError("source snapshot directory is unsafe")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(path, 0o700)
    return path


def _snapshot_root(vault: KnowledgeVault, *, create: bool) -> Path:
    operations = vault.root / "operations"
    root = operations / "source-snapshots"
    if create:
        _owner_directory(operations)
        return _owner_directory(root)
    for path in (operations, root):
        if path.is_symlink() or not path.is_dir():
            raise RuntimeError("source snapshot directory is missing or unsafe")
    return root


def _owner_only(path: Path) -> bool:
    if os.name == "nt":
        return True
    metadata = path.stat()
    return (
        not stat.S_IMODE(metadata.st_mode) & 0o077
        and (not hasattr(os, "geteuid") or metadata.st_uid == os.geteuid())
    )


def _record_digest(record: dict[str, Any]) -> str:
    return sha256_bytes(
        canonical_json(
            {key: value for key, value in record.items() if key != "record_sha256"}
        ).encode("utf-8")
    )


def _canonical_git_origin(value: str) -> tuple[str, str, str]:
    if not isinstance(value, str) or not value or len(value) > 2_000:
        raise ValueError("local Git source origin is invalid")
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError("local Git source origin port is invalid") from error
    repository_id = parts.hostname or ""
    if (
        parts.scheme != "deeplaw-git"
        or parts.netloc != repository_id
        or parts.username is not None
        or parts.password is not None
        or port is not None
        or parts.query
        or parts.fragment
        or not _REPOSITORY_ID.fullmatch(repository_id)
        or not parts.path.startswith("/")
    ):
        raise ValueError("local Git source origin is not canonical")
    revision, separator, encoded_path = parts.path[1:].partition("/")
    if not separator or not _GIT_REVISION.fullmatch(revision):
        raise ValueError("local Git source origin requires an exact commit and path")
    try:
        logical_path = unquote_to_bytes(encoded_path).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("local Git source origin path is not canonical UTF-8") from error
    logical_path = normalize_logical_path(logical_path)
    canonical_path = quote(logical_path, safe="/-._~")
    canonical = f"deeplaw-git://{repository_id}/{revision}/{canonical_path}"
    if canonical != value:
        raise ValueError("local Git source origin path is not canonically encoded")
    return repository_id, revision, logical_path


def _https_collection_name(host: str) -> str:
    readable = f"https:{host}"
    if len(readable) <= 200:
        return readable
    return f"https-host:{sha256_bytes(host.encode('ascii'))}"


def source_snapshot_collection_name(value: dict[str, Any]) -> str:
    connector = value.get("connector")
    if connector == "https":
        requested = canonical_https_url(value.get("requested_locator"))
        host = urlsplit(requested).hostname
        assert host is not None
        return _https_collection_name(host)
    if connector == "git-local-exact":
        repository_id, _, _ = _canonical_git_origin(value.get("canonical_origin_uri"))
        return f"git:{repository_id}"
    raise ValueError("source snapshot connector is invalid")


def _validate_connector_identity(vault: KnowledgeVault, value: dict[str, Any]) -> None:
    if value["connector"] == "https":
        requested = canonical_https_url(value["requested_locator"])
        resolved = canonical_https_url(value["resolved_locator"])
        if (
            requested != value["requested_locator"]
            or resolved != value["resolved_locator"]
            or value["canonical_origin_uri"] != resolved
            or value["network_used"] is not True
            or value["collection_id"]
            != make_collection_id(
                vault_id=vault.vault_id,
                name=source_snapshot_collection_name(value),
            )
            or value["logical_path"]
            != f"urls/{sha256_bytes(requested.encode('utf-8'))}"
        ):
            raise RuntimeError("HTTPS source snapshot identity is inconsistent")
        return
    _, _, logical_path = _canonical_git_origin(
        value["canonical_origin_uri"]
    )
    if (
        value["requested_locator"] != value["canonical_origin_uri"]
        or value["resolved_locator"] != value["canonical_origin_uri"]
        or value["network_used"] is not False
        or value["collection_id"]
        != make_collection_id(
            vault_id=vault.vault_id,
            name=source_snapshot_collection_name(value),
        )
        or value["logical_path"] != logical_path
    ):
        raise RuntimeError("local Git source snapshot identity is inconsistent")


def _safe_snapshot_filename(value: str, *, suffix: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")[:120] or "source"
    current = PurePosixPath(name).suffix.lower()
    if current not in _SUPPORTED_SUFFIXES:
        name = f"{name}{suffix}"
    if (
        name.casefold() == "snapshot.json"
        or name.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
    ):
        name = f"source-{name}"
    return name


def _persist_snapshot(
    vault: KnowledgeVault,
    *,
    connector: str,
    requested_locator: str,
    resolved_locator: str,
    canonical_origin_uri: str,
    collection_id: str,
    logical_path: str,
    filename: str,
    media_type: str,
    network_used: bool,
    metadata: dict[str, Any],
    content: bytes,
) -> dict[str, Any]:
    if vault.read_only:
        raise RuntimeError("capturing a source snapshot requires a writable vault")
    if not 1 <= len(content) <= _MAX_SNAPSHOT_BYTES:
        raise ValueError("source snapshot is empty or exceeds 64 MiB")
    if connector not in {"https", "git-local-exact"}:
        raise ValueError("source snapshot connector is invalid")
    origin = canonical_origin_commitment(canonical_origin_uri)
    logical = normalize_logical_path(logical_path)
    if (
        origin != canonical_origin_uri
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", filename)
        or filename.casefold() == "snapshot.json"
        or filename.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        or PurePosixPath(filename).suffix.lower() not in _SUPPORTED_SUFFIXES
        or len(media_type) > 200
        or not _MEDIA_TYPE.fullmatch(media_type)
        or not isinstance(network_used, bool)
        or not isinstance(metadata, dict)
        or len(metadata) > 20
        or not re.fullmatch(r"collection_[0-9a-f]{24}", collection_id)
    ):
        raise ValueError("source snapshot metadata is invalid")
    identity = {
        "connector": connector,
        "requested_locator": requested_locator,
        "resolved_locator": resolved_locator,
        "canonical_origin_uri": origin,
        "collection_id": collection_id,
        "logical_path": logical,
        "network_used": network_used,
    }
    _validate_connector_identity(vault, identity)
    digest = sha256_bytes(content)
    snapshot_id = stable_id(
        "sourcesnapshot",
        vault.vault_id,
        connector,
        origin,
        logical,
        digest,
    )
    root = _snapshot_root(vault, create=True)
    destination = root / snapshot_id
    if destination.exists() or destination.is_symlink():
        existing = verify_source_snapshot(vault, snapshot_id)
        if (
            existing["content_sha256"] != digest
            or existing["canonical_origin_uri"] != origin
            or existing["logical_path"] != logical
        ):
            raise RuntimeError("source snapshot identity collision")
        return existing
    temporary = root / f".{snapshot_id}.{secrets.token_hex(8)}.tmp"
    temporary.mkdir(mode=0o700)
    final_file = destination / filename
    temporary_file = temporary / filename
    try:
        descriptor = os.open(temporary_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        captured_at = utc_now()
        body = {
            "schema_version": SOURCE_SNAPSHOT_SCHEMA,
            "snapshot_id": snapshot_id,
            "vault_id": vault.vault_id,
            "connector": connector,
            "captured_at": captured_at,
            "requested_locator": requested_locator,
            "resolved_locator": resolved_locator,
            "canonical_origin_uri": origin,
            "collection_id": collection_id,
            "logical_path": logical,
            "path_hint": str(final_file),
            "content_sha256": digest,
            "byte_size": len(content),
            "media_type": media_type,
            "network_used": network_used,
            "metadata": metadata,
        }
        record = {**body, "record_sha256": sha256_bytes(canonical_json(body).encode())}
        payload = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        if len(payload) > _MAX_MANIFEST_BYTES:
            raise ValueError("source snapshot manifest exceeds its size bound")
        manifest = temporary / "snapshot.json"
        descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return verify_source_snapshot(vault, snapshot_id)


def verify_source_snapshot(vault: KnowledgeVault, snapshot_id: str) -> dict[str, Any]:
    if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.fullmatch(snapshot_id):
        raise ValueError("source snapshot ID is invalid")
    root = _snapshot_root(vault, create=False)
    directory = root / snapshot_id
    manifest = directory / "snapshot.json"
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or manifest.is_symlink()
        or not manifest.is_file()
        or not 1 <= manifest.stat().st_size <= _MAX_MANIFEST_BYTES
        or not all(_owner_only(path) for path in (root, directory, manifest))
    ):
        raise RuntimeError("source snapshot is missing or unsafe")
    try:
        value = strict_json_loads(manifest.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("source snapshot manifest is invalid") from error
    expected = {
        "schema_version",
        "snapshot_id",
        "vault_id",
        "connector",
        "captured_at",
        "requested_locator",
        "resolved_locator",
        "canonical_origin_uri",
        "collection_id",
        "logical_path",
        "path_hint",
        "content_sha256",
        "byte_size",
        "media_type",
        "network_used",
        "metadata",
        "record_sha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value.get("schema_version") != SOURCE_SNAPSHOT_SCHEMA
        or value.get("snapshot_id") != snapshot_id
        or value.get("vault_id") != vault.vault_id
        or value.get("connector") not in {"https", "git-local-exact"}
        or value.get("record_sha256") != _record_digest(value)
        or not isinstance(value.get("captured_at"), str)
        or not isinstance(value.get("requested_locator"), str)
        or not 1 <= len(value["requested_locator"]) <= 4_096
        or not isinstance(value.get("resolved_locator"), str)
        or not 1 <= len(value["resolved_locator"]) <= 4_096
        or not isinstance(value.get("canonical_origin_uri"), str)
        or not isinstance(value.get("collection_id"), str)
        or not re.fullmatch(r"collection_[0-9a-f]{24}", value["collection_id"])
        or not isinstance(value.get("logical_path"), str)
        or not isinstance(value.get("path_hint"), str)
        or not 1 <= len(value["path_hint"]) <= 4_096
        or not isinstance(value.get("media_type"), str)
        or len(value["media_type"]) > 200
        or not _MEDIA_TYPE.fullmatch(value["media_type"])
        or not isinstance(value.get("network_used"), bool)
        or not isinstance(value.get("metadata"), dict)
        or len(value["metadata"]) > 20
        or not isinstance(value.get("byte_size"), int)
        or isinstance(value.get("byte_size"), bool)
        or not 1 <= value["byte_size"] <= _MAX_SNAPSHOT_BYTES
        or not isinstance(value.get("content_sha256"), str)
        or not _SHA256.fullmatch(value["content_sha256"])
    ):
        raise RuntimeError("source snapshot does not match its closed contract")
    try:
        canonical_values = (
            canonical_timestamp(value["captured_at"], field="snapshot capture time")
            == value["captured_at"]
            and canonical_origin_commitment(value["canonical_origin_uri"])
            == value["canonical_origin_uri"]
            and normalize_logical_path(value["logical_path"]) == value["logical_path"]
        )
        _validate_connector_identity(vault, value)
    except (RuntimeError, ValueError) as error:
        raise RuntimeError("source snapshot connector identity is invalid") from error
    expected_snapshot_id = stable_id(
        "sourcesnapshot",
        vault.vault_id,
        value["connector"],
        value["canonical_origin_uri"],
        value["logical_path"],
        value["content_sha256"],
    )
    if not canonical_values or expected_snapshot_id != snapshot_id:
        raise RuntimeError("source snapshot immutable identity is invalid")
    path = Path(value["path_hint"])
    try:
        resolved_parent = path.parent.resolve(strict=True)
        resolved_directory = directory.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("source snapshot path is unavailable") from error
    if (
        resolved_parent != resolved_directory
        or not path.is_absolute()
        or path.name.casefold() == "snapshot.json"
        or path.name.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,160}", path.name)
        or PurePosixPath(path.name).suffix.lower() not in _SUPPORTED_SUFFIXES
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != value["byte_size"]
        or sha256_file(path) != value["content_sha256"]
        or not _owner_only(path)
    ):
        raise RuntimeError("source snapshot bytes failed verification")
    inventory = {item.name for item in directory.iterdir()}
    if inventory != {"snapshot.json", path.name}:
        raise RuntimeError("source snapshot file inventory is invalid")
    return {**value, "valid": True}


def canonical_https_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2_000:
        raise ValueError("HTTPS source URL is missing or exceeds its bound")
    parts = urlsplit(value.strip())
    try:
        port = parts.port
    except ValueError as error:
        raise ValueError("HTTPS source URL port is invalid") from error
    if (
        parts.scheme.lower() != "https"
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or port not in {None, 443}
        or not parts.hostname
    ):
        raise ValueError(
            "HTTPS source URL requires https, port 443, and no credentials, query, or fragment"
        )
    try:
        host = parts.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise ValueError("HTTPS source hostname is invalid") from error
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("HTTPS source must use a DNS hostname, not an IP literal")
    labels = host.split(".")
    if (
        host.endswith(".")
        or len(host) > 253
        or any(not _DNS_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValueError("HTTPS source hostname is not a canonical DNS name")
    path = parts.path or "/"
    encoded_unsafe = re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|5c|7f)", path, re.IGNORECASE)
    if (
        not path.startswith("/")
        or not _URL_PATH.fullmatch(path)
        or encoded_unsafe is not None
    ):
        raise ValueError("HTTPS source path is invalid")
    path = re.sub(
        r"%[0-9A-Fa-f]{2}",
        lambda match: match.group(0).upper(),
        path,
    )
    return canonical_origin_commitment(urlunsplit(("https", host, path, "", "")))


def _resolve_public_addresses(
    host: str,
    port: int,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[tuple[int, str], ...]:
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("HTTPS source DNS timeout is invalid")
    result: queue.SimpleQueue[tuple[bool, Any]] = queue.SimpleQueue()

    def resolve() -> None:
        try:
            rows = socket.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except Exception as error:
            result.put((False, error))
        else:
            result.put((True, rows))

    worker = threading.Thread(target=resolve, daemon=True, name="deeplaw-https-dns")
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise RuntimeError("HTTPS source hostname resolution timed out")
    succeeded, value = result.get_nowait()
    if not succeeded:
        raise RuntimeError("HTTPS source hostname resolution failed") from value
    rows = value
    if not rows or len(rows) > _MAX_DNS_ADDRESSES:
        raise RuntimeError("HTTPS source DNS result is empty or exceeds its bound")
    addresses: set[tuple[int, str]] = set()
    for family, _, _, _, sockaddr in rows:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            raise RuntimeError("HTTPS source DNS returned an unsupported address family")
        raw = str(sockaddr[0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as error:
            raise RuntimeError("HTTPS source DNS returned an invalid address") from error
        if not address.is_global:
            raise RuntimeError("HTTPS source DNS resolved to a non-public address")
        addresses.add((family, address.compressed))
    return tuple(sorted(addresses, key=lambda item: (item[0], item[1])))


def _remaining_timeout(deadline: float, *, operation: str, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError(f"{operation} timed out")
    return min(maximum, remaining)


def _request_https_once(
    url: str,
    *,
    maximum_bytes: int,
    timeout_seconds: float,
) -> tuple[int, dict[str, str], bytes, str]:
    canonical = canonical_https_url(url)
    parts = urlsplit(canonical)
    assert parts.hostname is not None
    deadline = time.monotonic() + timeout_seconds
    addresses = _resolve_public_addresses(
        parts.hostname,
        443,
        timeout_seconds=_remaining_timeout(
            deadline,
            operation="HTTPS source request",
            maximum=timeout_seconds,
        ),
    )
    last_error: BaseException | None = None
    for family, address in addresses:
        raw_socket: socket.socket | None = None
        tls_socket: Any | None = None
        connection: http.client.HTTPSConnection | None = None
        try:
            remaining = _remaining_timeout(
                deadline,
                operation="HTTPS source request",
                maximum=timeout_seconds,
            )
            raw_socket = socket.socket(family, socket.SOCK_STREAM)
            raw_socket.settimeout(remaining)
            endpoint: tuple[Any, ...] = (
                (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
            )
            raw_socket.connect(endpoint)
            context = ssl.create_default_context()
            tls_socket = context.wrap_socket(raw_socket, server_hostname=parts.hostname)
            raw_socket = None
            remaining = _remaining_timeout(
                deadline,
                operation="HTTPS source request",
                maximum=timeout_seconds,
            )
            tls_socket.settimeout(remaining)
            connection = http.client.HTTPSConnection(
                parts.hostname,
                port=443,
                timeout=remaining,
                context=context,
            )
            connection.sock = tls_socket
            tls_socket = None
            connection.sock.settimeout(
                _remaining_timeout(
                    deadline,
                    operation="HTTPS source request",
                    maximum=timeout_seconds,
                )
            )
            connection.request(
                "GET",
                parts.path or "/",
                headers={
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "User-Agent": "DeepLaw-Source-Snapshot/1",
                },
            )
            response = connection.getresponse()
            _remaining_timeout(
                deadline,
                operation="HTTPS source request",
                maximum=timeout_seconds,
            )
            header_rows = [
                (key.lower(), value.strip()) for key, value in response.getheaders()
            ]
            for field in (
                "content-encoding",
                "content-length",
                "content-type",
                "location",
                "transfer-encoding",
            ):
                if sum(key == field for key, _ in header_rows) > 1:
                    raise RuntimeError(f"HTTPS source returned duplicate {field} headers")
            headers = dict(header_rows)
            if response.status in {301, 302, 303, 307, 308}:
                return response.status, headers, b"", address
            if response.status != 200:
                raise RuntimeError(f"HTTPS source returned HTTP {response.status}")
            if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                raise RuntimeError("HTTPS source returned an unsupported content encoding")
            content_length = headers.get("content-length")
            transfer_encoding = headers.get("transfer-encoding")
            declared: int | None = None
            if content_length is not None:
                if not re.fullmatch(r"[0-9]+", content_length):
                    raise RuntimeError("HTTPS source Content-Length is invalid")
                declared = int(content_length)
                if not 1 <= declared <= maximum_bytes:
                    raise RuntimeError("HTTPS source Content-Length exceeds its bound")
            if content_length is not None and transfer_encoding:
                raise RuntimeError(
                    "HTTPS source returned ambiguous length and transfer encoding"
                )
            if transfer_encoding is not None and transfer_encoding.lower() != "chunked":
                raise RuntimeError(
                    "HTTPS source returned an unsupported transfer encoding"
                )
            body = bytearray()
            while True:
                connection.sock.settimeout(
                    _remaining_timeout(
                        deadline,
                        operation="HTTPS source request",
                        maximum=timeout_seconds,
                    )
                )
                chunk = response.read(min(64 * 1024, maximum_bytes + 1 - len(body)))
                _remaining_timeout(
                    deadline,
                    operation="HTTPS source request",
                    maximum=timeout_seconds,
                )
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > maximum_bytes:
                    raise RuntimeError("HTTPS source response exceeds its byte bound")
            if not body:
                raise RuntimeError("HTTPS source response is empty")
            if declared is not None and len(body) != declared:
                raise RuntimeError("HTTPS source response length does not match its header")
            return response.status, headers, bytes(body), address
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            last_error = error
        finally:
            if connection is not None:
                connection.close()
            if raw_socket is not None:
                raw_socket.close()
            if tls_socket is not None:
                tls_socket.close()
    if time.monotonic() >= deadline:
        raise RuntimeError("HTTPS source request timed out") from last_error
    raise RuntimeError("HTTPS source connection failed") from last_error


def _download_https(
    url: str,
    *,
    maximum_bytes: int,
    timeout_seconds: float,
) -> tuple[str, bytes, str, list[str], list[str]]:
    current = canonical_https_url(url)
    redirect_chain = [current]
    endpoint_addresses: list[str] = []
    deadline = time.monotonic() + timeout_seconds
    for _ in range(_MAX_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("HTTPS source capture timed out")
        status, headers, content, endpoint = _request_https_once(
            current,
            maximum_bytes=maximum_bytes,
            timeout_seconds=remaining,
        )
        endpoint_addresses.append(endpoint)
        if status == 200:
            media_type = headers.get("content-type", "application/octet-stream")
            media_type = media_type.split(";", 1)[0].strip().lower()
            return current, content, media_type, redirect_chain, endpoint_addresses
        location = headers.get("location")
        if not location:
            raise RuntimeError("HTTPS source redirect has no Location")
        target = canonical_https_url(urljoin(current, location))
        if target in redirect_chain:
            raise RuntimeError("HTTPS source redirect loop detected")
        redirect_chain.append(target)
        current = target
    raise RuntimeError("HTTPS source exceeds the redirect bound")


def _validate_https_bounds(maximum_bytes: int, timeout_seconds: float) -> None:
    if (
        isinstance(maximum_bytes, bool)
        or not isinstance(maximum_bytes, int)
        or not 1 <= maximum_bytes <= _MAX_SNAPSHOT_BYTES
        or isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 1 <= timeout_seconds <= 120
    ):
        raise ValueError("HTTPS source capture bounds are invalid")


def _normalize_expected_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expected HTTPS source SHA-256 is invalid")
    normalized = value.strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError("expected HTTPS source SHA-256 is invalid")
    return normalized


def plan_https_source(
    url: str,
    *,
    expected_sha256: str | None = None,
    maximum_bytes: int = _MAX_SNAPSHOT_BYTES,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    _validate_https_bounds(maximum_bytes, timeout_seconds)
    expected = _normalize_expected_sha256(expected_sha256)
    canonical = canonical_https_url(url)
    return {
        "schema_version": "deeplaw.https-source-preflight/v1",
        "canonical_requested_url": canonical,
        "expected_sha256": expected,
        "network_performed": False,
        "confirmation_required": True,
        "constraints": {
            "https_only": True,
            "public_dns_only": True,
            "port": 443,
            "credentials_query_fragment_allowed": False,
            "redirect_limit": _MAX_REDIRECTS,
            "maximum_bytes": maximum_bytes,
            "timeout_seconds": timeout_seconds,
        },
    }


def capture_https_source(
    vault: KnowledgeVault,
    url: str,
    *,
    confirm_network: bool,
    expected_sha256: str | None = None,
    maximum_bytes: int = _MAX_SNAPSHOT_BYTES,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    if confirm_network is not True:
        raise ValueError("HTTPS source capture requires explicit --confirm-network")
    _validate_https_bounds(maximum_bytes, timeout_seconds)
    normalized_expected = _normalize_expected_sha256(expected_sha256)
    requested = canonical_https_url(url)
    final, content, media_type, redirects, endpoints = _download_https(
        requested,
        maximum_bytes=maximum_bytes,
        timeout_seconds=timeout_seconds,
    )
    digest = sha256_bytes(content)
    if normalized_expected is not None and digest != normalized_expected:
        raise RuntimeError("HTTPS source bytes do not match --expected-sha256")
    suffix = _suffix_for_source(final, media_type)
    basename = PurePosixPath(urlsplit(final).path).name or "index"
    filename = _safe_snapshot_filename(basename, suffix=suffix)
    host = urlsplit(requested).hostname
    assert host is not None
    return _persist_snapshot(
        vault,
        connector="https",
        requested_locator=requested,
        resolved_locator=final,
        canonical_origin_uri=final,
        collection_id=make_collection_id(
            vault_id=vault.vault_id,
            name=_https_collection_name(host),
        ),
        logical_path=f"urls/{sha256_bytes(requested.encode('utf-8'))}",
        filename=filename,
        media_type=media_type,
        network_used=True,
        metadata={
            "redirect_chain": redirects,
            "endpoint_addresses": endpoints,
            "expected_sha256_supplied": expected_sha256 is not None,
            "expected_sha256": normalized_expected,
            "response_bytes": len(content),
            "authority_effect": "none; snapshot remains untrusted until review",
        },
        content=content,
    )


def _suffix_for_source(url: str, media_type: str) -> str:
    suffix = PurePosixPath(urlsplit(url).path).suffix.lower()
    selected = _MEDIA_SUFFIXES.get(media_type)
    if suffix in _SUPPORTED_SUFFIXES:
        equivalent = (
            {".htm", ".html"},
            {".md", ".markdown"},
            {".yaml", ".yml"},
        )
        if selected is not None and suffix != selected and not any(
            {suffix, selected} <= group for group in equivalent
        ) and not (selected == ".txt" and suffix in _TEXTUAL_SUFFIXES):
            raise ValueError("HTTPS source path suffix conflicts with Content-Type")
        return suffix
    if selected is None:
        raise ValueError(
            "HTTPS source has no supported path suffix or recognized Content-Type"
        )
    return selected


def _git_environment() -> dict[str, str]:
    return {
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_ASKPASS": "",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }


def _git_run(
    repository: Path,
    arguments: list[str],
    *,
    maximum_stdout: int,
    timeout_seconds: float,
) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("local Git source capture requires the git executable")
    try:
        result = run_bounded_subprocess(
            [executable, "--no-replace-objects", "-C", str(repository), *arguments],
            environment=_git_environment(),
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=maximum_stdout,
            max_stderr_bytes=64 * 1024,
        )
    except BoundedSubprocessError as error:
        raise RuntimeError("bounded local Git source operation failed") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:1_000]
        raise RuntimeError(f"local Git source operation failed: {detail}")
    return result.stdout


def _validate_git_inputs(
    repository: str | Path,
    revision: str,
    repository_id: str,
    *,
    deadline: float | None = None,
) -> tuple[Path, str, str]:
    if not isinstance(revision, str) or not isinstance(repository_id, str):
        raise ValueError("local Git revision and repository ID must be strings")
    candidate = Path(repository).expanduser().absolute()
    if candidate.is_symlink():
        raise ValueError("local Git repository must not be a symbolic link")
    root = candidate.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("local Git repository must be a directory")
    selected_revision = revision.strip().lower()
    selected_id = repository_id.strip().lower()
    if not _GIT_REVISION.fullmatch(selected_revision):
        raise ValueError("local Git source requires an exact full commit object ID")
    if not _REPOSITORY_ID.fullmatch(selected_id):
        raise ValueError("local Git repository ID is invalid")
    resolved = _git_run(
        root,
        ["rev-parse", "--verify", "--end-of-options", f"{selected_revision}^{{commit}}"],
        maximum_stdout=256,
        timeout_seconds=(
            15
            if deadline is None
            else _remaining_timeout(
                deadline,
                operation="local Git source capture",
                maximum=15,
            )
        ),
    ).decode("ascii", errors="strict").strip().lower()
    if resolved != selected_revision:
        raise RuntimeError("local Git revision did not resolve to the exact requested commit")
    return root, selected_revision, selected_id


def _validate_patterns(include: tuple[str, ...], exclude: tuple[str, ...]) -> None:
    if len(include) > 32 or len(exclude) > 32:
        raise ValueError("local Git include/exclude patterns exceed their bound")
    if any(
        not isinstance(value, str) or not value or len(value) > 500
        for value in (*include, *exclude)
    ):
        raise ValueError("local Git include/exclude pattern is invalid")


def _git_inventory(
    repository: str | Path,
    revision: str,
    repository_id: str,
    *,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    deadline: float | None = None,
) -> tuple[Path, str, str, list[dict[str, Any]], int]:
    root, commit, selected_id = _validate_git_inputs(
        repository,
        revision,
        repository_id,
        deadline=deadline,
    )
    _validate_patterns(include, exclude)
    raw = _git_run(
        root,
        ["ls-tree", "-r", "-z", "-l", "--full-tree", commit],
        maximum_stdout=32 * 1024 * 1024,
        timeout_seconds=(
            60
            if deadline is None
            else _remaining_timeout(
                deadline,
                operation="local Git source capture",
                maximum=60,
            )
        ),
    )
    files: list[dict[str, Any]] = []
    skipped = 0
    total_bytes = 0
    seen_paths: set[str] = set()
    for row_number, encoded in enumerate(raw.split(b"\0"), start=1):
        if not encoded:
            continue
        if deadline is not None and row_number % 256 == 0:
            _remaining_timeout(
                deadline,
                operation="local Git source capture",
                maximum=60,
            )
        match = _GIT_TREE_ROW.fullmatch(encoded)
        if match is None:
            raise RuntimeError("local Git tree output does not match its closed grammar")
        if match.group("kind") != b"blob" or match.group("mode") not in {b"100644", b"100755"}:
            skipped += 1
            continue
        try:
            path_value = match.group("path").decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise RuntimeError("local Git tree contains a non-UTF-8 path") from error
        if any(ord(character) < 32 or ord(character) == 127 for character in path_value):
            raise RuntimeError("local Git tree contains a path with control characters")
        logical_path = normalize_logical_path(path_value)
        if logical_path != path_value or logical_path in seen_paths:
            raise RuntimeError("local Git tree contains a non-canonical or duplicate path")
        seen_paths.add(logical_path)
        if PurePosixPath(logical_path).suffix.lower() not in _SUPPORTED_SUFFIXES:
            skipped += 1
            continue
        if include and not any(fnmatch(logical_path, pattern) for pattern in include):
            skipped += 1
            continue
        if any(fnmatch(logical_path, pattern) for pattern in exclude):
            skipped += 1
            continue
        try:
            size = int(match.group("size"))
        except ValueError as error:
            raise RuntimeError("local Git blob size is invalid") from error
        if not 1 <= size <= _MAX_SNAPSHOT_BYTES:
            raise RuntimeError("local Git source blob is empty or exceeds 64 MiB")
        total_bytes += size
        if total_bytes > _MAX_GIT_TOTAL_BYTES:
            raise RuntimeError("local Git source selection exceeds 512 MiB")
        files.append(
            {
                "logical_path": logical_path,
                "git_object_id": match.group("object").decode("ascii"),
                "git_mode": match.group("mode").decode("ascii"),
                "byte_size": size,
            }
        )
        if len(files) > _MAX_GIT_FILES:
            raise RuntimeError("local Git source selection exceeds 10000 files")
    if not files:
        raise ValueError("local Git source selection contains no supported files")
    return root, commit, selected_id, files, skipped


def plan_git_source(
    repository: str | Path,
    revision: str,
    repository_id: str,
    *,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> dict[str, Any]:
    root, commit, selected_id, files, skipped = _git_inventory(
        repository,
        revision,
        repository_id,
        include=include,
        exclude=exclude,
    )
    return {
        "schema_version": "deeplaw.git-source-preflight/v1",
        "repository_id": selected_id,
        "repository_path_hint": str(root),
        "commit": commit,
        "network_performed": False,
        "checkout_performed": False,
        "file_count": len(files),
        "skipped_count": skipped,
        "total_bytes": sum(item["byte_size"] for item in files),
        "files": files[:100],
        "files_truncated": len(files) > 100,
    }


def capture_git_sources(
    vault: KnowledgeVault,
    repository: str | Path,
    revision: str,
    repository_id: str,
    *,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    confirm_local_repository: bool,
    timeout_seconds: float = 300.0,
) -> tuple[dict[str, Any], ...]:
    if confirm_local_repository is not True:
        raise ValueError(
            "local Git source capture requires explicit --confirm-local-repository"
        )
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds)
        or not 1 <= timeout_seconds <= 900
    ):
        raise ValueError("local Git source capture timeout is invalid")
    deadline = time.monotonic() + timeout_seconds
    root, commit, selected_id, files, _ = _git_inventory(
        repository,
        revision,
        repository_id,
        include=include,
        exclude=exclude,
        deadline=deadline,
    )
    collection_id = make_collection_id(
        vault_id=vault.vault_id,
        name=f"git:{selected_id}",
    )
    snapshots: list[dict[str, Any]] = []
    for item in files:
        remaining = _remaining_timeout(
            deadline,
            operation="local Git source capture",
            maximum=60,
        )
        content = _git_run(
            root,
            ["cat-file", "blob", item["git_object_id"]],
            maximum_stdout=item["byte_size"],
            timeout_seconds=remaining,
        )
        if len(content) != item["byte_size"]:
            raise RuntimeError("local Git blob size changed during snapshot capture")
        object_payload = f"blob {len(content)}\0".encode("ascii") + content
        object_digest = (
            hashlib.sha1(object_payload, usedforsecurity=False).hexdigest()
            if len(item["git_object_id"]) == 40
            else hashlib.sha256(object_payload).hexdigest()
        )
        if object_digest != item["git_object_id"]:
            raise RuntimeError("local Git blob bytes do not match their object ID")
        encoded_path = quote(item["logical_path"], safe="/-._~")
        origin = f"deeplaw-git://{selected_id}/{commit}/{encoded_path}"
        basename = PurePosixPath(item["logical_path"]).name
        suffix = PurePosixPath(basename).suffix.lower()
        snapshot = _persist_snapshot(
            vault,
            connector="git-local-exact",
            requested_locator=origin,
            resolved_locator=origin,
            canonical_origin_uri=origin,
            collection_id=collection_id,
            logical_path=item["logical_path"],
            filename=_safe_snapshot_filename(basename, suffix=suffix),
            media_type="application/octet-stream",
            network_used=False,
            metadata={
                "repository_id": selected_id,
                "repository_path_hint": str(root),
                "commit": commit,
                "git_object_id": item["git_object_id"],
                "git_mode": item["git_mode"],
                "checkout_performed": False,
                "network_performed": False,
                "authority_effect": "none; snapshot remains review-gated",
            },
            content=content,
        )
        _remaining_timeout(
            deadline,
            operation="local Git source capture",
            maximum=60,
        )
        snapshots.append(snapshot)
    return tuple(snapshots)
