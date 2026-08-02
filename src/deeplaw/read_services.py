from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .knowledge_autonomy import SENSITIVITY_ORDER, AutonomousKnowledgeStore
from .knowledge_store import KnowledgeVault
from .util import sha256_bytes


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
    ) -> dict[str, Any]:
        if action not in {"list", "get", "fragment", "diff"}:
            raise ValueError("source support action is invalid")
        if max_sensitivity not in {"public", "internal", "private"} or not 1 <= limit <= 20:
            raise ValueError("source support policy is invalid")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("source fragment offset is invalid")
        if not 200 <= max_chars <= 12_000:
            raise ValueError("source fragment character budget is invalid")
        with KnowledgeVault(self.root, read_only=True) as vault:
            if not vault.verify_integrity()["valid"]:
                raise RuntimeError("knowledge vault integrity is invalid; source read stopped")
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
                self._require_admitted(vault, source, scope, max_sensitivity)
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
                self._require_admitted(vault, source, scope, max_sensitivity)
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
            self._require_admitted(vault, old, scope, max_sensitivity)
            self._require_admitted(vault, new, scope, max_sensitivity)
            result = vault.source_diff(old_source_id, new_source_id)
            result["write_performed"] = False
            return result

    @staticmethod
    def _require_admitted(
        vault: KnowledgeVault,
        source: dict[str, Any],
        scope: str | None,
        max_sensitivity: str,
    ) -> None:
        if not _source_admitted(
            source,
            vault_scope=vault.manifest["scope"],
            scope=scope,
            max_sensitivity=max_sensitivity,
        ):
            raise PermissionError("source is not admitted")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not vault.verify_source_files(
            (source_id,)
        )["valid"]:
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
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
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
                content = text[:20_000]
                return {
                    "schema_version": "deeplaw.living-wiki-page-read/v1",
                    "wiki_path": wiki_path,
                    "content": content,
                    "content_sha256": sha256_bytes(text.encode("utf-8")),
                    "content_truncated": len(content) != len(text),
                    "write_performed": False,
                }
            if action == "outlinks":
                return {
                    "schema_version": "deeplaw.living-wiki-links/v1",
                    "wiki_path": wiki_path,
                    "direction": "out",
                    "links": links[:limit],
                    "truncated": len(links) > limit,
                    "write_performed": False,
                }
            target = PurePosixPath(wiki_path).with_suffix("").as_posix()
            backlinks = []
            pages = sorted((self.root / "wiki").rglob("*.md"))[:1000]
            for candidate in pages:
                if candidate.is_symlink() or candidate.stat().st_size > 256 * 1024:
                    continue
                candidate_text = candidate.read_text(encoding="utf-8")
                if target in _WIKILINK_PATTERN.findall(candidate_text):
                    backlinks.append(candidate.relative_to(self.root).as_posix())
                    if len(backlinks) >= limit:
                        break
            return {
                "schema_version": "deeplaw.living-wiki-links/v1",
                "wiki_path": wiki_path,
                "direction": "in",
                "links": backlinks,
                "truncated": len(backlinks) == limit,
                "write_performed": False,
            }
