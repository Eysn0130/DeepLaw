from __future__ import annotations

import json
import re
import sqlite3
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from .knowledge_autonomy import AutonomousKnowledgeStore
from .knowledge_store import KnowledgeVault, _database_path
from .util import canonical_json, sha256_bytes, strict_json_loads

_MAX_LEGACY_DERIVED_MANIFEST_BYTES = 4 * 1024 * 1024
_MAX_V2_DERIVED_MANIFEST_BYTES = 1 * 1024 * 1024
_MAX_LIVING_WIKI_V2_MANIFEST_BYTES = 64 * 1024 * 1024
_MAX_ROOT_MANIFEST_BYTES = 256 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_WIKI_PAGE_BYTES = 256 * 1024


def _immutable(value: Any) -> Any:
    """Detach projection metadata from mutable loader dictionaries."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_immutable(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_immutable(item) for item in value)
    return value


def _safe_relative(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RuntimeError(f"{field} path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError(f"{field} path is invalid")
    return path.as_posix()


@dataclass(frozen=True, slots=True)
class WikiProjectionBundle:
    """One fully verified active v2/v3 projection and its in-memory indexes."""

    root: Path
    v2_manifest: Mapping[str, Any]
    v3_manifest: Mapping[str, Any]
    v2_files: tuple[Mapping[str, Any], ...]
    v3_files: tuple[Mapping[str, Any], ...]
    page_registry: Mapping[str, Any]
    link_index: Mapping[str, Any]
    resolver: Any

    @property
    def v2_file_map(self) -> Mapping[str, Mapping[str, Any]]:
        return MappingProxyType({item["path"]: item for item in self.v2_files})

    def read_page(self, relative: str) -> bytes:
        """Read one registry-declared page without following symlinks or guessing paths."""

        normalized = _safe_relative(relative, field="Wiki page")
        descriptor = self.v2_file_map.get(normalized)
        if descriptor is None:
            raise KeyError("Living Wiki page is unavailable")
        expected_size = descriptor.get("byte_size")
        expected_hash = descriptor.get("sha256")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size > _MAX_WIKI_PAGE_BYTES
            or not isinstance(expected_hash, str)
            or not _SHA256.fullmatch(expected_hash)
        ):
            raise RuntimeError("Living Wiki page descriptor is invalid")
        from .wiki.registry import _safe_read_file

        try:
            payload = _safe_read_file(
                self.root,
                normalized,
                max_bytes=_MAX_WIKI_PAGE_BYTES,
                field="Living Wiki page",
            )
        except Exception as error:
            raise RuntimeError("Living Wiki page is unavailable") from error
        if len(payload) != expected_size or sha256_bytes(payload) != expected_hash:
            raise RuntimeError("Living Wiki page hash/size mismatch")
        return payload


@dataclass(frozen=True)
class ReadIdentity:
    """The small live identity used to guard a pinned read snapshot."""

    database: tuple[int, int, int, int, int]
    data_version: int
    legacy_audit_head: str
    autonomous_audit_head: str
    manifest: tuple[Any, ...]
    source: tuple[Any, ...] = ()
    wiki: tuple[Any, ...] = ()
    root_manifest: tuple[Any, ...] = ()

    @property
    def source_identity(self) -> tuple[Any, ...]:
        return self.source

    @property
    def wiki_identity(self) -> tuple[Any, ...]:
        return self.wiki

    @property
    def ledger_identity(self) -> tuple[int, int, int, int, int]:
        return self.database


@dataclass
class PersistentReadSnapshot:
    """A verified pair of read-only planes; provider payloads are never stored here."""

    legacy: KnowledgeVault
    store: AutonomousKnowledgeStore
    legacy_integrity: dict[str, Any]
    autonomous_integrity: dict[str, Any]
    identity: ReadIdentity
    closed: bool = False
    wiki: WikiProjectionBundle | None = None
    source_integrity: dict[str, Any] | None = None

    @property
    def wiki_projection(self) -> WikiProjectionBundle | None:
        return self.wiki

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # Close the autonomous connection first.  The legacy transaction is the
        # consistency anchor passed to its constructor.
        with suppress(sqlite3.ProgrammingError):
            self.store.close()
        with suppress(sqlite3.ProgrammingError):
            self.legacy.close()


class _LiveObserver:
    """Independent autocommit observer; it never participates in a read snapshot."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.database = _database_path(root)
        self.connection: sqlite3.Connection | None = None
        self._database_identity = self._regular_file_identity(self.database, "database")
        try:
            uri = f"{self.database.as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            self.connection = connection
            # Do not issue BEGIN here: data_version must describe live state,
            # not a transaction pinned at observer creation time.
            self.identity()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _regular_file_identity(path: Path, label: str) -> tuple[int, int, int, int, int]:
        try:
            info = path.lstat()
        except OSError as error:
            raise RuntimeError(f"{label} is unavailable") from error
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise RuntimeError(f"{label} is not a safe regular file")
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(info.st_ctime_ns),
        )

    def _manifest_identity(self) -> tuple[Any, ...]:
        path = self.root / ".deeplaw" / "derived" / "manifest.json"
        for parent in (path.parent, path.parent.parent):
            try:
                parent_info = parent.lstat()
            except OSError as error:
                raise RuntimeError("derived manifest parent is unavailable") from error
            if not stat.S_ISDIR(parent_info.st_mode) or parent.is_symlink():
                raise RuntimeError("derived manifest parent is not a safe directory")
        try:
            identity = self._regular_file_identity(path, "derived manifest")
        except RuntimeError as error:
            if not path.exists() and not path.is_symlink():
                # A newly initialized autonomous Vault legitimately has no
                # projection manifest yet; represent that state explicitly so
                # a later creation still invalidates the pinned snapshot.
                return ("missing",)
            raise error
        size = identity[2]
        if size > _MAX_LEGACY_DERIVED_MANIFEST_BYTES:
            raise RuntimeError("derived manifest exceeds its bounded observer size")
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RuntimeError("derived manifest cannot be read") from error
        after = self._regular_file_identity(path, "derived manifest")
        if after != identity or len(payload) != size:
            raise RuntimeError("derived manifest changed while it was observed")
        try:
            import json

            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("derived manifest is not valid JSON") from error
        if not isinstance(parsed, dict):
            raise RuntimeError("derived manifest is not an object")
        schema_version = parsed.get("schema_version")
        if schema_version not in {
            "deeplaw.derived-manifest/v1",
            "deeplaw.derived-manifest/v2",
        }:
            raise RuntimeError("derived manifest schema is unsupported")
        if (
            schema_version == "deeplaw.derived-manifest/v2"
            and size > _MAX_V2_DERIVED_MANIFEST_BYTES
        ):
            raise RuntimeError("derived v2 manifest exceeds its bounded observer size")
        manifest_digest = parsed.get("manifest_sha256")
        manifest_body = {key: value for key, value in parsed.items() if key != "manifest_sha256"}
        if not isinstance(manifest_digest, str) or not _SHA256.fullmatch(manifest_digest):
            raise RuntimeError("derived manifest digest is invalid")
        if manifest_digest != sha256_bytes(canonical_json(manifest_body).encode("utf-8")):
            raise RuntimeError("derived manifest hash is invalid")
        digest = sha256_bytes(payload)
        return (*identity, str(schema_version or ""), digest)

    def _optional_json_manifest(
        self,
        relative: str,
        *,
        label: str,
        max_bytes: int,
    ) -> tuple[tuple[Any, ...], dict[str, Any], bytes] | None:
        path = self.root.joinpath(*relative.split("/"))
        if not path.exists() and not path.is_symlink():
            return None
        identity = self._regular_file_identity(path, label)
        if identity[2] > max_bytes:
            raise RuntimeError(f"{label} exceeds its bounded observer size")
        try:
            payload = path.read_bytes()
            parsed = strict_json_loads(payload)
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"{label} is not valid JSON") from error
        after = self._regular_file_identity(path, label)
        if after != identity or len(payload) != identity[2]:
            raise RuntimeError(f"{label} changed while it was observed")
        if not isinstance(parsed, dict):
            raise RuntimeError(f"{label} is not an object")
        return identity, parsed, payload

    def _root_manifest_identity(self) -> tuple[Any, ...]:
        """Return a bounded byte identity for the authoritative Vault manifest.

        Warm checks deliberately avoid parsing this manifest.  The Vault and
        autonomous-store constructors perform full startup verification; this
        observer only makes byte-level rewrites observable before reusing a
        pinned snapshot.
        """

        path = self.root / ".deeplaw" / "manifest.json"
        for parent in (path.parent, self.root):
            try:
                parent_info = parent.lstat()
            except OSError as error:
                raise RuntimeError("Vault manifest parent is unavailable") from error
            if not stat.S_ISDIR(parent_info.st_mode) or parent.is_symlink():
                raise RuntimeError("Vault manifest parent is not a safe directory")
        identity = self._regular_file_identity(path, "Vault manifest")
        if identity[2] > _MAX_ROOT_MANIFEST_BYTES:
            raise RuntimeError("Vault manifest exceeds its bounded observer size")
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RuntimeError("Vault manifest cannot be read") from error
        after = self._regular_file_identity(path, "Vault manifest")
        if after != identity or len(payload) != identity[2]:
            raise RuntimeError("Vault manifest changed while it was observed")
        # Hash the exact payload, not a parsed/canonicalized form, so a same-size
        # rewrite cannot bypass invalidation.
        return (*identity, sha256_bytes(payload))

    def _wiki_identity(self) -> tuple[Any, ...]:
        v2_relative = ".deeplaw/derived/tree/living-wiki-manifest.json"
        v3_relative = ".deeplaw/derived/wiki/v3/manifest.json"
        v2_path = self.root.joinpath(*v2_relative.split("/"))
        if not v2_path.exists() and not v2_path.is_symlink():
            v2_identity = ("missing",)
        else:
            identity = self._regular_file_identity(v2_path, "Living Wiki v2 manifest")
            if identity[2] > _MAX_LIVING_WIKI_V2_MANIFEST_BYTES:
                raise RuntimeError("Living Wiki v2 manifest exceeds its bounded observer size")
            # The aggregate derived manifest is hash-checked by ``_manifest_identity`` and
            # binds this v2 manifest digest.  The active snapshot fully validates v2 once.
            # A warm request therefore needs only the filesystem identity here; reparsing a
            # file inventory proportional to Wiki size would defeat the persistent runtime.
            v2_identity = ("present", identity)

        v3 = self._optional_json_manifest(
            v3_relative,
            label="Living Wiki v3 manifest",
            max_bytes=_MAX_V2_DERIVED_MANIFEST_BYTES,
        )
        if v3 is None:
            v3_identity: tuple[Any, ...] = ("missing",)
        else:
            identity, parsed, payload = v3
            try:
                from .wiki.registry import validate_living_wiki_manifest_v3

                validate_living_wiki_manifest_v3(parsed)
            except Exception as error:
                raise RuntimeError("Living Wiki v3 manifest is invalid") from error
            manifest_digest = parsed.get("manifest_sha256")
            if not isinstance(manifest_digest, str) or not _SHA256.fullmatch(manifest_digest):
                raise RuntimeError("Living Wiki v3 manifest hash is invalid")
            descriptors: list[tuple[Any, ...]] = [
                (v3_relative, identity, sha256_bytes(payload), str(manifest_digest))
            ]
            for component in parsed["components"]:
                manifest_relative = _safe_relative(
                    component.get("manifest_path"), field="Living Wiki v3 component"
                )
                loaded = self._optional_json_manifest(
                    manifest_relative,
                    label="Living Wiki v3 component manifest",
                    max_bytes=_MAX_V2_DERIVED_MANIFEST_BYTES,
                )
                if loaded is None:
                    raise RuntimeError("Living Wiki v3 component manifest is missing")
                component_identity, _, component_payload = loaded
                if (
                    component.get("manifest_byte_size") != component_identity[2]
                    or component.get("manifest_sha256") != sha256_bytes(component_payload)
                ):
                    raise RuntimeError("Living Wiki v3 component binding is invalid")
                descriptors.append(
                    (
                        manifest_relative,
                        component_identity,
                        sha256_bytes(component_payload),
                        component.get("shard_count"),
                        component.get("record_count"),
                        component.get("registry_or_index_sha256"),
                    )
                )
            v3_identity = ("present", tuple(descriptors))
        return v2_identity, v3_identity

    def _source_identity(self) -> tuple[Any, ...]:
        # The ledger is the single local database for both source and autonomous planes.
        # ``data_version`` and the audit heads are collected by ``identity`` itself; keep
        # this explicit source identity O(1) rather than scanning source rows/files per request.
        return ("ledger", self._database_identity)

    def identity(self) -> ReadIdentity:
        connection = self.connection
        if connection is None:
            raise RuntimeError("live observer is closed")
        current_database_identity = self._regular_file_identity(self.database, "database")
        if current_database_identity != self._database_identity:
            raise RuntimeError("knowledge database identity changed")
        try:
            data_version = int(connection.execute("PRAGMA data_version").fetchone()[0])
            legacy_row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'audit_head'"
            ).fetchone()
            autonomous_row = connection.execute(
                "SELECT value FROM autonomous_metadata_v3 WHERE key = 'audit_head'"
            ).fetchone()
        except (sqlite3.DatabaseError, TypeError, ValueError) as error:
            raise RuntimeError("knowledge database live identity is unavailable") from error
        if legacy_row is None or autonomous_row is None:
            raise RuntimeError("knowledge audit heads are unavailable")
        legacy_head = legacy_row[0]
        autonomous_head = autonomous_row[0]
        if (
            not isinstance(legacy_head, str)
            or not isinstance(autonomous_head, str)
            or not _SHA256.fullmatch(legacy_head)
            or not _SHA256.fullmatch(autonomous_head)
        ):
            raise RuntimeError("knowledge audit heads are invalid")
        if self._regular_file_identity(self.database, "database") != current_database_identity:
            raise RuntimeError("knowledge database changed while it was observed")
        return ReadIdentity(
            database=current_database_identity,
            data_version=data_version,
            legacy_audit_head=legacy_head,
            autonomous_audit_head=autonomous_head,
            manifest=self._manifest_identity(),
            source=self._source_identity(),
            wiki=self._wiki_identity(),
            root_manifest=self._root_manifest_identity(),
        )

    def close(self) -> None:
        connection = self.connection
        self.connection = None
        if connection is not None:
            connection.close()


def _force_legacy_integrity(vault: KnowledgeVault) -> dict[str, Any]:
    """Run the two legacy verifiers directly, bypassing instance/global caches."""

    fingerprint = vault._database_file_fingerprint()
    audit = vault.verify_audit_chain()
    state = (
        vault.verify_state_integrity()
        if audit["valid"]
        else {
            "valid": False,
            "reason": "audit_chain_invalid",
            "object_id": None,
        }
    )
    if vault._database_file_fingerprint() != fingerprint:
        raise RuntimeError("knowledge vault database changed during integrity verification")
    return {
        "valid": bool(audit["valid"] and state["valid"]),
        "audit": audit,
        "state": state,
    }


class PersistentReadRuntime:
    """Integrity-bound lifespan runtime for the read-only knowledge MCP."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()
        self._observer: _LiveObserver | None = None
        self._snapshot: PersistentReadSnapshot | None = None
        self._closed = False
        try:
            self._open_and_verify()
        except BaseException:
            self.close()
            raise

    @property
    def snapshot(self) -> PersistentReadSnapshot:
        snapshot = self._snapshot
        if self._closed or snapshot is None or snapshot.closed:
            raise RuntimeError("persistent knowledge read runtime is closed")
        return snapshot

    def _load_wiki_bundle(
        self,
        *,
        legacy_audit_head: str,
        autonomous_audit_head: str,
    ) -> WikiProjectionBundle | None:
        """Load and validate the active v2/v3 pair exactly once for a lifespan."""

        from .projection.incremental import read_previous_manifest, read_previous_v3

        v3_snapshot = read_previous_v3(self.root)
        if v3_snapshot is None:
            return None
        v2_manifest = read_previous_manifest(self.root)
        if v2_manifest is None:
            raise RuntimeError("Living Wiki v3 projection has no active v2 pair")
        v3_manifest = v3_snapshot.get("manifest")
        if not isinstance(v3_manifest, dict):
            raise RuntimeError("Living Wiki v3 projection manifest is invalid")
        expected_v2 = v3_manifest.get("v2_manifest_sha256")
        actual_v2 = v2_manifest.get("manifest_sha256")
        if expected_v2 != actual_v2:
            raise RuntimeError("Living Wiki v2/v3 projection pair is inconsistent")
        if (
            v3_manifest.get("input_audit_head") != autonomous_audit_head
            or v3_manifest.get("legacy_audit_head") != legacy_audit_head
            or v2_manifest.get("input_audit_head") != autonomous_audit_head
            or v2_manifest.get("legacy_audit_head") != legacy_audit_head
        ):
            return None
        components = v3_snapshot.get("components")
        if not isinstance(components, dict):
            raise RuntimeError("Living Wiki v3 projection components are unavailable")
        registry = components.get("page_registry")
        links = components.get("link_index")
        resolver = components.get("resolver")
        if not isinstance(registry, dict) or not isinstance(links, dict) or resolver is None:
            raise RuntimeError("Living Wiki v3 projection indexes are unavailable")
        v2_files = v2_manifest.get("files")
        v3_files = v3_snapshot.get("files")
        if not isinstance(v2_files, list) or not isinstance(v3_files, list):
            raise RuntimeError("Living Wiki projection inventories are invalid")
        # The incremental loader has already read and hash-verified every v3 component,
        # shard, and coverage file.  Detach only the descriptors and metadata; page bodies
        # remain on disk and are read through the registry-declared path on demand.
        return WikiProjectionBundle(
            root=self.root,
            v2_manifest=_immutable(v2_manifest),
            v3_manifest=_immutable(v3_manifest),
            v2_files=tuple(_immutable(item) for item in v2_files),
            v3_files=tuple(_immutable(item) for item in v3_files),
            page_registry=_immutable(registry),
            link_index=links,
            resolver=resolver,
        )

    def _open_and_verify(self) -> None:
        if self._closed:
            raise RuntimeError("persistent knowledge read runtime is closed")
        observer = _LiveObserver(self.root)
        before = observer.identity()
        legacy: KnowledgeVault | None = None
        store: AutonomousKnowledgeStore | None = None
        try:
            legacy = KnowledgeVault(self.root, read_only=True)
            store = AutonomousKnowledgeStore(
                self.root,
                read_only=True,
                legacy_snapshot=legacy,
            )
            legacy_integrity = legacy.verify_integrity()
            if not legacy_integrity.get("valid"):
                raise RuntimeError("knowledge vault integrity is invalid; Agent reads stopped")
            legacy_audit_head = legacy.audit_head
            autonomous_integrity = store.verify(
                preverified_legacy_integrity=legacy_integrity,
                preverified_legacy_audit_head=legacy_audit_head,
            )
            if not autonomous_integrity.get("valid"):
                raise RuntimeError("autonomous knowledge integrity is invalid; Agent reads stopped")
            source_rows = legacy.connection.execute(
                "SELECT source_id FROM sources ORDER BY source_id"
            )
            source_ids = tuple(row[0] for row in source_rows)
            source_integrity = legacy.verify_source_files(source_ids)
            if not source_integrity.get("valid"):
                raise RuntimeError("knowledge source integrity is invalid; Agent reads stopped")
            wiki = self._load_wiki_bundle(
                legacy_audit_head=legacy_audit_head,
                autonomous_audit_head=store.audit_head,
            )
            after = observer.identity()
            if after != before:
                raise RuntimeError("knowledge state changed while opening its read snapshot")
            self._observer = observer
            self._snapshot = PersistentReadSnapshot(
                legacy=legacy,
                store=store,
                legacy_integrity=legacy_integrity,
                autonomous_integrity=autonomous_integrity,
                identity=after,
                wiki=wiki,
                source_integrity=source_integrity,
            )
            legacy = None
            store = None
        finally:
            if store is not None:
                store.close()
            if legacy is not None:
                legacy.close()
            if self._observer is not observer:
                observer.close()

    def _reopen(self) -> None:
        old = self._snapshot
        self._snapshot = None
        if old is not None:
            old.close()
        observer = self._observer
        self._observer = None
        if observer is not None:
            observer.close()
        # No old snapshot is usable after this point.  If opening fails the
        # exception is propagated and the caller receives a fail-closed error.
        self._open_and_verify()

    def _invalidate(self) -> None:
        snapshot = self._snapshot
        self._snapshot = None
        if snapshot is not None:
            snapshot.close()
        observer = self._observer
        self._observer = None
        if observer is not None:
            observer.close()

    def get_snapshot(self, *, operation: str = "read") -> PersistentReadSnapshot:
        if self._closed:
            raise RuntimeError("persistent knowledge read runtime is closed")
        observer = self._observer
        snapshot = self._snapshot
        if observer is None or snapshot is None:
            self._reopen()
            snapshot = self.snapshot
        else:
            try:
                current = observer.identity()
            except BaseException:
                self._invalidate()
                self._reopen()
                snapshot = self.snapshot
            else:
                if current != snapshot.identity:
                    self._invalidate()
                    self._reopen()
                    snapshot = self.snapshot
        if operation == "verify":
            # Explicit verification is never served from cached integrity.
            try:
                legacy_integrity = _force_legacy_integrity(snapshot.legacy)
                autonomous_integrity = snapshot.store.verify(
                    preverified_legacy_integrity=legacy_integrity,
                    preverified_legacy_audit_head=snapshot.legacy.audit_head,
                )
                if not legacy_integrity.get("valid") or not autonomous_integrity.get("valid"):
                    raise RuntimeError("knowledge vault integrity is invalid; Agent reads stopped")
            except BaseException:
                self._invalidate()
                raise
            identity = self._observer.identity() if self._observer is not None else None
            if identity != snapshot.identity:
                self._invalidate()
                self._reopen()
                snapshot = self.snapshot
            else:
                snapshot.legacy_integrity = legacy_integrity
                snapshot.autonomous_integrity = autonomous_integrity
        return snapshot

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        snapshot = self._snapshot
        self._snapshot = None
        if snapshot is not None:
            snapshot.close()
        observer = self._observer
        self._observer = None
        if observer is not None:
            observer.close()
