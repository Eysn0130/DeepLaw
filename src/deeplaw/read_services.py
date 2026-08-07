from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from .knowledge_autonomy import SENSITIVITY_ORDER, AutonomousKnowledgeStore
from .knowledge_store import KnowledgeVault
from .util import sha256_bytes

if TYPE_CHECKING:
    from .persistent_read_runtime import PersistentReadSnapshot, WikiProjectionBundle


def _source_admitted(
    source: dict[str, Any],
    *,
    vault_scope: str,
    scope: str | None,
    max_sensitivity: str,
) -> bool:
    return (
        (scope is None or scope == vault_scope)
        and source.get("status") == "active"
        and source.get("sensitivity") in SENSITIVITY_ORDER
        and SENSITIVITY_ORDER.index(source["sensitivity"])
        <= SENSITIVITY_ORDER.index(max_sensitivity)
    )


def _source_card(source: dict[str, Any], *, fragment_count: int) -> dict[str, Any]:
    return {
        key: source.get(key)
        for key in (
            "source_id",
            "source_revision_id",
            "canonical_source_key",
            "previous_source_id",
            "kind",
            "title",
            "media_type",
            "byte_size",
            "content_sha256",
            "trust",
            "sensitivity",
            "status",
            "imported_at",
            "compiler",
            "warnings",
        )
    } | {"fragment_count": fragment_count}


class SourceReadService:
    """Policy-admitted reads over immutable Source Revisions and fragments."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()

    def execute(
        self,
        *,
        action: str,
        source_id: str | None = None,
        old_source_id: str | None = None,
        new_source_id: str | None = None,
        fragment_id: str | None = None,
        scope: str | None = None,
        max_sensitivity: str = "private",
        limit: int = 20,
        offset: int = 0,
        max_chars: int = 12_000,
        snapshot: PersistentReadSnapshot | None = None,
    ) -> dict[str, Any]:
        if action not in {"list", "get", "fragment", "diff"}:
            raise ValueError("source support action is invalid")
        if max_sensitivity not in {"public", "internal", "private"} or not 1 <= limit <= 20:
            raise ValueError("source support policy is invalid")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("source fragment offset is invalid")
        if not 200 <= max_chars <= 12_000:
            raise ValueError("source fragment character budget is invalid")
        if snapshot is not None:
            if (
                snapshot.closed
                or snapshot.legacy.root != self.root
                or not snapshot.legacy.read_only
            ):
                raise RuntimeError("persistent knowledge read snapshot belongs to another Vault")
            return self._execute_with_vault(
                snapshot.legacy,
                source_integrity=snapshot.source_integrity,
                action=action,
                source_id=source_id,
                old_source_id=old_source_id,
                new_source_id=new_source_id,
                fragment_id=fragment_id,
                scope=scope,
                max_sensitivity=max_sensitivity,
                limit=limit,
                offset=offset,
                max_chars=max_chars,
            )
        with KnowledgeVault(self.root, read_only=True) as vault:
            if not vault.verify_integrity()["valid"]:
                raise RuntimeError("knowledge vault integrity is invalid; source read stopped")
            return self._execute_with_vault(
                vault,
                source_integrity=None,
                action=action,
                source_id=source_id,
                old_source_id=old_source_id,
                new_source_id=new_source_id,
                fragment_id=fragment_id,
                scope=scope,
                max_sensitivity=max_sensitivity,
                limit=limit,
                offset=offset,
                max_chars=max_chars,
            )

    def _execute_with_vault(
        self,
        vault: KnowledgeVault,
        *,
        source_integrity: dict[str, Any] | None,
        action: str,
        source_id: str | None,
        old_source_id: str | None,
        new_source_id: str | None,
        fragment_id: str | None,
        scope: str | None,
        max_sensitivity: str,
        limit: int,
        offset: int,
        max_chars: int,
    ) -> dict[str, Any]:
        if action == "list":
            cards = []
            for source in vault.all_sources():
                if not _source_admitted(
                    source,
                    vault_scope=vault.manifest["scope"],
                    scope=scope,
                    max_sensitivity=max_sensitivity,
                ):
                    continue
                count = vault.connection.execute(
                    "SELECT COUNT(*) FROM source_fragments WHERE source_id = ?",
                    (source["source_id"],),
                ).fetchone()[0]
                cards.append(_source_card(source, fragment_count=count))
                if len(cards) >= limit:
                    break
            return {
                "schema_version": "deeplaw.knowledge-source-list/v2",
                "sources": cards,
                "source_count": len(cards),
                "truncated": len(cards) == limit,
                "write_performed": False,
            }
        if action == "get":
            if source_id is None:
                raise ValueError("source ID is required")
            source = vault.source_info(source_id)
            self._require_admitted(
                vault,
                source,
                scope,
                max_sensitivity,
                source_integrity=source_integrity,
            )
            count = vault.connection.execute(
                "SELECT COUNT(*) FROM source_fragments WHERE source_id = ?",
                (source_id,),
            ).fetchone()[0]
            return {
                "schema_version": "deeplaw.knowledge-source-card/v1",
                "source": _source_card(source, fragment_count=count),
                "write_performed": False,
            }
        if action == "fragment":
            if fragment_id is None:
                raise ValueError("fragment ID is required")
            binding = vault.connection.execute(
                """
                SELECT fragment_revision_id, fragment_id
                FROM legacy_fragment_bindings_v2
                WHERE fragment_revision_id = ? OR fragment_id = ?
                """,
                (fragment_id, fragment_id),
            ).fetchone()
            if binding is None:
                raise KeyError(f"unknown fragment identity: {fragment_id}")
            fragment = vault.get_fragment(binding["fragment_id"])
            source = vault.source_info(fragment["source_id"])
            self._require_admitted(
                vault,
                source,
                scope,
                max_sensitivity,
                source_integrity=source_integrity,
            )
            text = fragment["text"]
            selected = text[offset : offset + max_chars]
            next_offset = offset + len(selected)
            truncated = next_offset < len(text)
            return {
                "schema_version": "deeplaw.knowledge-source-fragment/v1",
                "fragment": {
                    key: fragment[key]
                    for key in (
                        "fragment_id",
                        "source_id",
                        "ordinal",
                        "locator",
                        "text_sha256",
                        "instruction_risk",
                    )
                }
                | {
                    "fragment_revision_id": binding["fragment_revision_id"],
                    "source_revision_id": source.get("source_revision_id"),
                    "text": selected,
                    "content_offset": offset,
                    "content_characters": len(selected),
                    "content_truncated": truncated,
                    "next_offset": next_offset if truncated else None,
                    "continuation": (
                        {
                            "action": "fragment",
                            "fragment_id": binding["fragment_revision_id"],
                            "offset": next_offset,
                            "max_chars": max_chars,
                        }
                        if truncated
                        else None
                    ),
                },
                "write_performed": False,
            }
        if old_source_id is None or new_source_id is None:
            raise ValueError("source diff requires exact source IDs")
        old = vault.source_info(old_source_id)
        new = vault.source_info(new_source_id)
        self._require_admitted(
            vault,
            old,
            scope,
            max_sensitivity,
            source_integrity=source_integrity,
        )
        self._require_admitted(
            vault,
            new,
            scope,
            max_sensitivity,
            source_integrity=source_integrity,
        )
        result = vault.source_diff(old_source_id, new_source_id)
        result["write_performed"] = False
        return result

    @staticmethod
    def _require_admitted(
        vault: KnowledgeVault,
        source: dict[str, Any],
        scope: str | None,
        max_sensitivity: str,
        *,
        source_integrity: dict[str, Any] | None = None,
    ) -> None:
        if not _source_admitted(
            source,
            vault_scope=vault.manifest["scope"],
            scope=scope,
            max_sensitivity=max_sensitivity,
        ):
            raise PermissionError("source is not admitted")
        source_id = source.get("source_id")
        if not isinstance(source_id, str):
            raise RuntimeError("source bytes failed current integrity verification")
        if source_integrity is None:
            if not vault.verify_source_files((source_id,))["valid"]:
                raise RuntimeError("source bytes failed current integrity verification")
            return
        checks = source_integrity.get("checks")
        if source_integrity.get("valid") is not True or not isinstance(checks, list):
            raise RuntimeError("source bytes failed current integrity verification")
        check = next(
            (
                item
                for item in checks
                if isinstance(item, dict) and item.get("source_id") == source_id
            ),
            None,
        )
        if not isinstance(check, dict) or check.get("valid") is not True:
            raise RuntimeError("source bytes failed current integrity verification")
        # The lifespan-wide check proves the snapshot started from valid evidence.  Source
        # bytes live outside SQLite, so an exact source/fragment read also rechecks only the
        # requested CAS file.  The store's stat-keyed digest cache keeps unchanged reads cheap
        # while preventing a later file mutation from being served through an old snapshot.
        if not vault.verify_source_files((source_id,))["valid"]:
            raise RuntimeError("source bytes failed current integrity verification")


_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+)")


def _wiki_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Wiki path is invalid")
    selected = PurePosixPath(relative)
    if (
        selected.is_absolute()
        or not selected.parts
        or selected.parts[0] != "wiki"
        or "." in selected.parts
        or ".." in selected.parts
        or selected.suffix != ".md"
    ):
        raise ValueError("Wiki path must be a canonical relative Markdown path")
    path = root / selected
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise KeyError("Living Wiki page is unavailable")
    return path


class WikiReadService:
    """Bounded admitted reads over rebuildable Living Wiki projections."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()

    def execute(
        self,
        *,
        action: str,
        wiki_path: str | None = None,
        knowledge_id: str | None = None,
        kind: str | None = None,
        scope: str | None = None,
        max_sensitivity: str = "private",
        limit: int = 20,
        cursor: str | None = None,
        snapshot: PersistentReadSnapshot | None = None,
    ) -> dict[str, Any]:
        if action not in {
            "page",
            "backlinks",
            "outlinks",
            "local_graph",
            "browse_kind",
            "recent_changes",
        }:
            raise ValueError("Wiki support action is invalid")
        if not 1 <= limit <= 20 or max_sensitivity not in {
            "public",
            "internal",
            "private",
        }:
            raise ValueError("Wiki support policy is invalid")
        temporary_runtime = None
        try:
            if snapshot is None:
                v3_path = self.root / ".deeplaw" / "derived" / "wiki" / "v3" / "manifest.json"
                if v3_path.exists() or v3_path.is_symlink():
                    from .persistent_read_runtime import PersistentReadRuntime

                    temporary_runtime = PersistentReadRuntime(self.root)
                    snapshot = temporary_runtime.snapshot
                    if snapshot.wiki is None:
                        raise RuntimeError("Living Wiki projection is unavailable")
            if snapshot is not None:
                if (
                    snapshot.closed
                    or snapshot.store.root != self.root
                    or not snapshot.store.read_only
                ):
                    raise RuntimeError(
                        "persistent knowledge read snapshot belongs to another Vault"
                    )
                bundle = snapshot.wiki
                if bundle is None:
                    raise RuntimeError("Living Wiki projection is unavailable")
                return self._execute_projection(
                    snapshot.store,
                    bundle,
                    action=action,
                    wiki_path=wiki_path,
                    knowledge_id=knowledge_id,
                    kind=kind,
                    scope=scope,
                    max_sensitivity=max_sensitivity,
                    limit=limit,
                    cursor=cursor,
                )
            with AutonomousKnowledgeStore(self.root, read_only=True) as store:
                return self._execute_legacy(
                    store,
                    action=action,
                    wiki_path=wiki_path,
                    knowledge_id=knowledge_id,
                    kind=kind,
                    scope=scope,
                    max_sensitivity=max_sensitivity,
                    limit=limit,
                    cursor=cursor,
                )
        finally:
            if temporary_runtime is not None:
                temporary_runtime.close()

    @staticmethod
    def _legacy_deprecation(result: dict[str, Any]) -> dict[str, Any]:
        result["deprecation"] = {
            "deprecated": True,
            "replacement": "wiki",
            "removal_version": "0.15.0",
        }
        return result

    @staticmethod
    def _browse(
        store: AutonomousKnowledgeStore,
        *,
        action: str,
        kind: str | None,
        selected_scope: str,
        max_sensitivity: str,
        limit: int,
    ) -> dict[str, Any]:
        filters = ["revisions.lifecycle = 'active'", "revisions.scope = ?"]
        parameters: list[Any] = [selected_scope]
        admitted = SENSITIVITY_ORDER[: SENSITIVITY_ORDER.index(max_sensitivity) + 1]
        filters.append(f"revisions.sensitivity IN ({','.join('?' for _ in admitted)})")
        parameters.extend(admitted)
        if action == "browse_kind":
            if kind is None:
                raise ValueError("Wiki kind is required")
            filters.append("revisions.kind = ?")
            parameters.append(kind)
        parameters.append(limit)
        rows = store.connection.execute(
            f"""
            SELECT revisions.knowledge_id, revisions.revision_id,
                   revisions.title, revisions.kind, revisions.recorded_at,
                   objects.workspace_path
            FROM knowledge_objects_v3 AS objects
            JOIN knowledge_revisions_v3 AS revisions
              ON revisions.revision_id = objects.current_revision_id
            WHERE {" AND ".join(filters)}
            ORDER BY revisions.recorded_at DESC, revisions.revision_id DESC
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        return {
            "schema_version": "deeplaw.living-wiki-browse/v1",
            "action": action,
            "items": [dict(row) for row in rows],
            "write_performed": False,
        }

    @staticmethod
    def _page_cursor_encode(digest: str, offset: int) -> str:
        body = json.dumps(
            {"sha256": digest, "offset": offset},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")

    @staticmethod
    def _page_cursor_decode(cursor: str) -> tuple[str, int]:
        if not isinstance(cursor, str) or len(cursor) > 256:
            raise ValueError("Wiki page cursor is invalid")
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (ValueError, TypeError, UnicodeDecodeError) as error:
            raise ValueError("Wiki page cursor is invalid") from error
        if (
            not isinstance(value, dict)
            or set(value) != {"sha256", "offset"}
            or not isinstance(value.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or not isinstance(value.get("offset"), int)
            or isinstance(value.get("offset"), bool)
            or value["offset"] < 0
        ):
            raise ValueError("Wiki page cursor is invalid")
        return value["sha256"], value["offset"]

    def _resolve_projection_page(
        self,
        bundle: WikiProjectionBundle,
        *,
        wiki_path: str | None,
        knowledge_id: str | None,
        selected_scope: str,
        max_sensitivity: str,
    ) -> dict[str, Any]:
        if (wiki_path is None) == (knowledge_id is None):
            raise ValueError("exactly one Wiki identity is required")
        identity = (
            {"wiki_path": wiki_path}
            if wiki_path is not None
            else {"knowledge_id": knowledge_id}
        )
        result = bundle.resolver.resolve(
            identity,
            scope=selected_scope,
            max_sensitivity=max_sensitivity,
            allowed_freshness=["fresh", "unknown"],
        )
        if (
            result.get("status") != "resolved"
            or result.get("admission", {}).get("admitted") is not True
        ):
            reason = result.get("admission", {}).get("reason", "not_admitted")
            if reason in {"scope_denied", "sensitivity_denied", "not_admitted"}:
                raise PermissionError("Wiki page is not admitted")
            if result.get("status") == "not_found":
                raise KeyError("Living Wiki page is unavailable")
            raise RuntimeError("Living Wiki resolver could not admit the page")
        candidates = result.get("candidates")
        if (
            not isinstance(candidates, list)
            or len(candidates) != 1
            or not isinstance(candidates[0], dict)
        ):
            raise RuntimeError("Living Wiki resolver returned an invalid page")
        return candidates[0]

    def _execute_projection(
        self,
        store: AutonomousKnowledgeStore,
        bundle: WikiProjectionBundle,
        *,
        action: str,
        wiki_path: str | None,
        knowledge_id: str | None,
        kind: str | None,
        scope: str | None,
        max_sensitivity: str,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        selected_scope = scope or store.vault_scope
        if selected_scope != store.vault_scope:
            raise PermissionError("Wiki request exceeds the Vault scope")
        if action == "local_graph":
            return self._legacy_deprecation(
                store.graph(
                knowledge_id=knowledge_id,
                scope=cast(Any, selected_scope),
                max_sensitivity=cast(Any, max_sensitivity),
                limit=limit,
                )
            )
        if action in {"browse_kind", "recent_changes"}:
            return self._legacy_deprecation(
                self._browse(
                    store,
                    action=action,
                    kind=kind,
                    selected_scope=selected_scope,
                    max_sensitivity=max_sensitivity,
                    limit=limit,
                )
            )
        if wiki_path is None and knowledge_id is None:
            raise ValueError("Wiki identity is required")
        candidate = self._resolve_projection_page(
            bundle,
            wiki_path=wiki_path,
            knowledge_id=knowledge_id,
            selected_scope=selected_scope,
            max_sensitivity=max_sensitivity,
        )
        canonical_path = candidate["canonical_page_path"]
        if action == "page":
            try:
                text = bundle.read_page(canonical_path).decode("utf-8")
            except UnicodeDecodeError as error:
                raise RuntimeError("Living Wiki page is not valid UTF-8") from error
            digest = sha256_bytes(text.encode("utf-8"))
            start = 0
            if cursor is not None:
                cursor_digest, start = self._page_cursor_decode(cursor)
                if cursor_digest != digest:
                    raise ValueError("Wiki page cursor is bound to another page")
                if start > len(text):
                    raise ValueError("Wiki page cursor is outside the page")
            content = text[start : start + 20_000]
            next_offset = start + len(content)
            truncated = next_offset < len(text)
            return {
                "schema_version": "deeplaw.living-wiki-page-read/v1",
                "wiki_path": canonical_path,
                "content": content,
                "content_sha256": digest,
                "content_offset": start,
                "content_characters": len(content),
                "content_truncated": truncated,
                "total_count": len(text),
                "cursor": self._page_cursor_encode(digest, next_offset) if truncated else None,
                "truncation_reason": "page_limit" if truncated else None,
                "write_performed": False,
            }
        from .wiki.link_index import query_links

        direction = "outlinks" if action == "outlinks" else "backlinks"
        indexed = query_links(
            bundle.link_index,
            candidate["page_id"],
            direction=direction,
            limit=limit,
            cursor=cursor,
        )
        if indexed.get("status") != "ok":
            raise RuntimeError(str(indexed.get("gap", "Living Wiki link index is unavailable")))
        path_by_id = {
            row["page_id"]: row["canonical_page_path"]
            for row in bundle.page_registry.get("records", ())
            if isinstance(row, Mapping)
        }
        links: list[str] = []
        for edge in indexed.get("links", ()):
            if not isinstance(edge, Mapping):
                continue
            if direction == "outlinks":
                targets = edge.get("target_page_ids", ())
                if isinstance(targets, (list, tuple)) and targets:
                    links.extend(path_by_id.get(target, target) for target in targets)
                elif isinstance(edge.get("target_raw"), str):
                    links.append(edge["target_raw"])
            else:
                links.append(
                    path_by_id.get(edge.get("source_page_id"), edge.get("source_page_id"))
                )
        return {
            "schema_version": "deeplaw.living-wiki-links/v1",
            "wiki_path": canonical_path,
            "direction": "out" if direction == "outlinks" else "in",
            "links": links[:limit],
            "total_count": indexed["total_count"],
            "cursor": indexed["cursor"],
            "truncated": indexed["truncated"],
            "truncation_reason": indexed["truncation_reason"],
            "index_used": True,
            "write_performed": False,
        }

    def _execute_legacy(
        self,
        store: AutonomousKnowledgeStore,
        *,
        action: str,
        wiki_path: str | None,
        knowledge_id: str | None,
        kind: str | None,
        scope: str | None,
        max_sensitivity: str,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        selected_scope = scope or store.vault_scope
        if selected_scope != store.vault_scope:
            raise PermissionError("Wiki request exceeds the Vault scope")
        if action == "local_graph":
            return store.graph(
                knowledge_id=knowledge_id,
                scope=cast(Any, selected_scope),
                max_sensitivity=cast(Any, max_sensitivity),
                limit=limit,
            )
        if action in {"browse_kind", "recent_changes"}:
            return self._browse(
                store,
                action=action,
                kind=kind,
                selected_scope=selected_scope,
                max_sensitivity=max_sensitivity,
                limit=limit,
            )
        if wiki_path is None:
            raise ValueError("Wiki path is required")
        page = _wiki_path(self.root, wiki_path)
        text = page.read_text(encoding="utf-8")
        if max_sensitivity != "private":
            match = re.search(r"(?m)^knowledge_id:\s*(knowledge_[0-9a-f]{24})$", text)
            if match is None:
                raise PermissionError("aggregate Wiki pages require private admission")
            current = store.get_current(match.group(1))
            if SENSITIVITY_ORDER.index(current["sensitivity"]) > SENSITIVITY_ORDER.index(
                max_sensitivity
            ):
                raise PermissionError("Wiki page is not admitted")
        links = sorted(set(_WIKILINK_PATTERN.findall(text)))
        if action == "page":
            digest = sha256_bytes(text.encode("utf-8"))
            start = 0
            if cursor is not None:
                cursor_digest, start = self._page_cursor_decode(cursor)
                if cursor_digest != digest:
                    raise ValueError("Wiki page cursor is bound to another page")
                if start > len(text):
                    raise ValueError("Wiki page cursor is outside the page")
            content = text[start : start + 20_000]
            next_offset = start + len(content)
            truncated = next_offset < len(text)
            return self._legacy_deprecation({
                "schema_version": "deeplaw.living-wiki-page-read/v1",
                "wiki_path": wiki_path,
                "content": content,
                "content_sha256": digest,
                "content_offset": start,
                "content_characters": len(content),
                "content_truncated": truncated,
                "total_count": len(text),
                "cursor": self._page_cursor_encode(digest, next_offset) if truncated else None,
                "truncation_reason": "page_limit" if truncated else None,
                "write_performed": False,
            })
        if action == "outlinks":
            truncated = len(links) > limit
            return {
                "schema_version": "deeplaw.living-wiki-links/v1",
                "wiki_path": wiki_path,
                "direction": "out",
                "links": links[:limit],
                "total_count": len(links),
                "cursor": None,
                "truncated": truncated,
                "truncation_reason": "page_limit" if truncated else None,
                "index_used": False,
                "write_performed": False,
            }
        target = PurePosixPath(wiki_path).with_suffix("").as_posix()
        backlinks: list[str] = []
        wiki_root = self.root / "wiki"
        if wiki_root.is_symlink() or not wiki_root.is_dir():
            raise KeyError("Living Wiki pages are unavailable")
        pages = sorted(wiki_root.rglob("*.md"))
        for candidate in pages:
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or candidate.stat().st_size > 256 * 1024
            ):
                continue
            candidate_text = candidate.read_text(encoding="utf-8")
            if target in _WIKILINK_PATTERN.findall(candidate_text):
                backlinks.append(candidate.relative_to(self.root).as_posix())
        truncated = len(backlinks) > limit
        return self._legacy_deprecation({
            "schema_version": "deeplaw.living-wiki-links/v1",
            "wiki_path": wiki_path,
            "direction": "in",
            "links": backlinks[:limit],
            "total_count": len(backlinks),
            "cursor": None,
            "truncated": truncated,
            "truncation_reason": "page_limit" if truncated else None,
            "index_used": False,
            "write_performed": False,
        })
