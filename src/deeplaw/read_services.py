from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from .knowledge_autonomy import SENSITIVITY_ORDER, AutonomousKnowledgeStore, _validate_contract
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


_RECENT_INDEX_PATH = "wiki/recent-changes/index.md"
_RECENT_PAGE_BYTES = 256 * 1024
_RECENT_INDEX_SHARD_LIMIT = 2_000
_RECENT_SHARD_EVENTS = 200
_RECENT_INDEX_LINK = re.compile(
    r"^- \[\[(wiki/recent-changes/[0-9]{4})\|Recent changes ([0-9]{4})\]\] "
    r"\(([0-9]{1,3}) events\)$"
)
_RECENT_EVENT_LINE = re.compile(
    r"^- `[^`\r\n]+` · `[^`\r\n]+` · .+ · `[^`\r\n]+`$"
)
_RECENT_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _recent_frontmatter(
    payload: bytes,
    *,
    expected_schema: str,
    required: tuple[str, ...],
) -> tuple[dict[str, str], str]:
    """Parse the small generated frontmatter envelope without accepting YAML syntax."""

    if not 1 <= len(payload) <= _RECENT_PAGE_BYTES:
        raise RuntimeError("Recent Changes page exceeds its byte bound")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("Recent Changes page is not valid UTF-8") from error
    if not text.startswith("---\n"):
        raise RuntimeError("Recent Changes page frontmatter is missing")
    marker = text.find("\n---\n", 4)
    if marker < 0 or marker > 16 * 1024:
        raise RuntimeError("Recent Changes page frontmatter is invalid")
    values: dict[str, str] = {}
    for line in text[4:marker].split("\n"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*: [^\r\n]+", line):
            raise RuntimeError("Recent Changes page frontmatter is invalid")
        key, value = line.split(": ", 1)
        if key in values:
            raise RuntimeError("Recent Changes page frontmatter is duplicated")
        values[key] = value
    invariant_fields = (
        "audit_head",
        "derived_view",
        "authority",
        "verification",
        "lifecycle",
    )
    if values.get("schema") != expected_schema or any(
        key not in values for key in (*invariant_fields, *required)
    ):
        raise RuntimeError("Recent Changes page frontmatter is incomplete")
    if values.get("derived_view") != "true":
        raise RuntimeError("Recent Changes page is not a derived projection")
    if values.get("authority") != "none" or values.get("verification") != "projection_only":
        raise RuntimeError("Recent Changes page authority metadata is invalid")
    if values.get("lifecycle") != "active":
        raise RuntimeError("Recent Changes page lifecycle is invalid")
    audit_head = values.get("audit_head")
    if audit_head is not None and not _RECENT_SHA256.fullmatch(audit_head):
        raise RuntimeError("Recent Changes page audit binding is invalid")
    return values, text[marker + len("\n---\n") :]


def _recent_integer(values: Mapping[str, str], key: str, *, maximum: int) -> int:
    value = values.get(key)
    if value is None or not re.fullmatch(r"[0-9]+", value):
        raise RuntimeError(f"Recent Changes frontmatter {key} is invalid")
    parsed = int(value)
    if parsed > maximum:
        raise RuntimeError(f"Recent Changes frontmatter {key} exceeds its bound")
    return parsed


def _recent_boolean(values: Mapping[str, str], key: str) -> bool:
    value = values.get(key)
    if value == "true":
        return True
    if value == "false":
        return False
    raise RuntimeError(f"Recent Changes frontmatter {key} is invalid")


def _recent_shard_links(body: str) -> list[tuple[str, int]]:
    links: list[tuple[str, int]] = []
    seen: set[str] = set()
    for line in body.split("\n"):
        if not line.startswith("- [[wiki/recent-changes/"):
            continue
        match = _RECENT_INDEX_LINK.fullmatch(line)
        if match is None or match.group(1)[-4:] != match.group(2):
            raise RuntimeError("Recent Changes index contains an invalid shard link")
        path = f"{match.group(1)}.md"
        if path in seen:
            raise RuntimeError("Recent Changes index contains a duplicate shard")
        seen.add(path)
        event_count = int(match.group(3))
        if not 1 <= event_count <= _RECENT_SHARD_EVENTS:
            raise RuntimeError("Recent Changes shard event count exceeds its bound")
        links.append((path, event_count))
        if len(links) > _RECENT_INDEX_SHARD_LIMIT:
            raise RuntimeError("Recent Changes shard count exceeds its bound")
    return links


def _recent_event_count(body: str) -> int:
    heading = re.search(r"(?m)^# Recent changes · [0-9]{4}\n", body)
    if heading is None:
        raise RuntimeError("Recent Changes shard heading is invalid")
    lines = [line for line in body[heading.end() :].split("\n") if line.startswith("- ")]
    if any(_RECENT_EVENT_LINE.fullmatch(line) is None for line in lines):
        raise RuntimeError("Recent Changes shard contains an invalid event line")
    if not lines:
        raise RuntimeError("Recent Changes shard has no Ledger events")
    return len(lines)


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

    @staticmethod
    def _projection_page_record(
        bundle: WikiProjectionBundle,
        *,
        path: str,
        candidate: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        records = bundle.page_registry.get("records")
        if not isinstance(records, (list, tuple)):
            raise RuntimeError("Living Wiki page registry is unavailable")
        for record in records:
            if not isinstance(record, Mapping) or record.get("canonical_page_path") != path:
                continue
            if (
                record.get("page_id") != candidate.get("page_id")
                or record.get("namespace") != "aggregate"
                or record.get("lifecycle") != "active"
            ):
                raise RuntimeError("Recent Changes page registry admission is invalid")
            return record
        raise RuntimeError("Recent Changes page is not registered")

    def _read_projection_recent_page(
        self,
        bundle: WikiProjectionBundle,
        *,
        path: str,
        selected_scope: str,
        max_sensitivity: str,
    ) -> tuple[Mapping[str, Any], bytes]:
        candidate = self._resolve_projection_page(
            bundle,
            wiki_path=path,
            knowledge_id=None,
            selected_scope=selected_scope,
            max_sensitivity=max_sensitivity,
        )
        record = self._projection_page_record(bundle, path=path, candidate=candidate)
        payload = bundle.read_page(path)
        expected_size = record.get("byte_size")
        expected_hash = record.get("sha256")
        if (
            not isinstance(expected_size, int)
            or expected_size != len(payload)
            or not isinstance(expected_hash, str)
            or not _RECENT_SHA256.fullmatch(expected_hash)
            or sha256_bytes(payload) != expected_hash
        ):
            raise RuntimeError("Recent Changes page registry hash/size mismatch")
        return record, payload

    def _recent_changes_result(
        self,
        *,
        index_content: str,
        index_payload: bytes,
        index_record: Mapping[str, Any] | None,
        shard_records: list[dict[str, Any]],
        total_shard_count: int,
        history_truncated: bool,
        deprecation: bool = False,
    ) -> dict[str, Any]:
        returned_shard_count = len(shard_records)
        by_limit = returned_shard_count < total_shard_count
        truncated = by_limit or history_truncated
        if by_limit and history_truncated:
            truncation_reason: str | None = "limit_and_history_retention"
        elif by_limit:
            truncation_reason = "limit"
        elif history_truncated:
            truncation_reason = "history_retention"
        else:
            truncation_reason = None
        result: dict[str, Any] = {
            "schema_version": "deeplaw.living-wiki-recent-changes-read/v1",
            "action": "recent_changes",
            "index_path": _RECENT_INDEX_PATH,
            "index_content": index_content,
            "index_content_sha256": sha256_bytes(index_payload),
            "index_byte_size": len(index_payload),
            "shards": shard_records,
            "returned_shard_count": returned_shard_count,
            "total_shard_count": total_shard_count,
            "history_truncated": history_truncated,
            "truncated": truncated,
            "truncation_reason": truncation_reason,
            "write_performed": False,
        }
        if deprecation:
            result["deprecation"] = {
                "deprecated": True,
                "replacement": "living-wiki-v3",
                "removal_version": "0.15.0",
            }
        if index_record is not None and (
            index_record.get("canonical_page_path") != _RECENT_INDEX_PATH
            or not isinstance(index_record.get("byte_size"), int)
            or index_record["byte_size"] != len(index_payload)
        ):
            # A registry record is intentionally not echoed wholesale.  The response binds the
            # exact bytes while keeping local registry internals out of the public payload.
            raise RuntimeError("Recent Changes index registry binding is invalid")
        _validate_contract("living-wiki-recent-changes-read.v1.schema.json", result)
        return result

    def _recent_changes_projection(
        self,
        bundle: WikiProjectionBundle,
        *,
        selected_scope: str,
        max_sensitivity: str,
        limit: int,
    ) -> dict[str, Any]:
        index_record, index_payload = self._read_projection_recent_page(
            bundle,
            path=_RECENT_INDEX_PATH,
            selected_scope=selected_scope,
            max_sensitivity=max_sensitivity,
        )
        index_values, index_body = _recent_frontmatter(
            index_payload,
            expected_schema="deeplaw.living-wiki-recent-changes-index/v1",
            required=("event_count", "history_truncated"),
        )
        expected_events = _recent_integer(index_values, "event_count", maximum=10_000)
        history_truncated = _recent_boolean(index_values, "history_truncated")
        links = _recent_shard_links(index_body)
        if len(links) > _RECENT_INDEX_SHARD_LIMIT:
            raise RuntimeError("Recent Changes index shard count exceeds its bound")
        if sum(event_count for _, event_count in links) != expected_events:
            raise RuntimeError("Recent Changes index/shard event counts are inconsistent")
        shard_records: list[dict[str, Any]] = []
        for path, expected_count in links:
            if len(shard_records) >= limit:
                break
            record, payload = self._read_projection_recent_page(
                bundle,
                path=path,
                selected_scope=selected_scope,
                max_sensitivity=max_sensitivity,
            )
            values, body = _recent_frontmatter(
                payload,
                expected_schema="deeplaw.living-wiki-recent-changes/v1",
                required=("shard", "event_count"),
            )
            if values["audit_head"] != index_values["audit_head"]:
                raise RuntimeError("Recent Changes shard audit binding is inconsistent")
            shard_number = _recent_integer(values, "shard", maximum=_RECENT_INDEX_SHARD_LIMIT)
            if path[-7:-3] != f"{shard_number:04d}":
                raise RuntimeError("Recent Changes shard identity is invalid")
            event_count = _recent_integer(
                values,
                "event_count",
                maximum=_RECENT_SHARD_EVENTS,
            )
            if event_count != expected_count or _recent_event_count(body) != event_count:
                raise RuntimeError("Recent Changes shard event count is inconsistent")
            shard_records.append(
                {
                    "path": path,
                    "event_count": event_count,
                    "content_sha256": sha256_bytes(payload),
                    "byte_size": len(payload),
                    "page_id": record.get("page_id"),
                    "revision_id": record.get("revision_id"),
                }
            )
        return self._recent_changes_result(
            index_content=index_payload.decode("utf-8"),
            index_payload=index_payload,
            index_record=index_record,
            shard_records=shard_records,
            total_shard_count=len(links),
            history_truncated=history_truncated,
        )

    def _recent_changes_legacy(
        self,
        *,
        selected_scope: str,
        max_sensitivity: str,
        limit: int,
    ) -> dict[str, Any]:
        # Legacy projections have no registry admission metadata.  Their aggregate pages are
        # private-only compatibility views; fixed paths and explicit index links are the only
        # permitted discovery mechanism.
        if max_sensitivity != "private":
            raise PermissionError("Recent Changes legacy projection requires private admission")
        index_path = _wiki_path(self.root, _RECENT_INDEX_PATH)
        index_payload = index_path.read_bytes()
        index_values, index_body = _recent_frontmatter(
            index_payload,
            expected_schema="deeplaw.living-wiki-recent-changes-index/v1",
            required=("event_count", "history_truncated"),
        )
        expected_events = _recent_integer(index_values, "event_count", maximum=10_000)
        history_truncated = _recent_boolean(index_values, "history_truncated")
        links = _recent_shard_links(index_body)
        if sum(event_count for _, event_count in links) != expected_events:
            raise RuntimeError("Recent Changes index/shard event counts are inconsistent")
        shard_records: list[dict[str, Any]] = []
        for path, expected_count in links:
            if len(shard_records) >= limit:
                break
            shard_path = _wiki_path(self.root, path)
            payload = shard_path.read_bytes()
            values, body = _recent_frontmatter(
                payload,
                expected_schema="deeplaw.living-wiki-recent-changes/v1",
                required=("shard", "event_count"),
            )
            if values["audit_head"] != index_values["audit_head"]:
                raise RuntimeError("Recent Changes shard audit binding is inconsistent")
            shard_number = _recent_integer(values, "shard", maximum=_RECENT_INDEX_SHARD_LIMIT)
            if path[-7:-3] != f"{shard_number:04d}":
                raise RuntimeError("Recent Changes shard identity is invalid")
            event_count = _recent_integer(
                values,
                "event_count",
                maximum=_RECENT_SHARD_EVENTS,
            )
            if event_count != expected_count or _recent_event_count(body) != event_count:
                raise RuntimeError("Recent Changes shard event count is inconsistent")
            shard_records.append(
                {
                    "path": path,
                    "event_count": event_count,
                    "content_sha256": sha256_bytes(payload),
                    "byte_size": len(payload),
                    "page_id": None,
                    "revision_id": None,
                }
            )
        return self._recent_changes_result(
            index_content=index_payload.decode("utf-8"),
            index_payload=index_payload,
            index_record=None,
            shard_records=shard_records,
            total_shard_count=len(links),
            history_truncated=history_truncated,
            deprecation=True,
        )

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
        if action == "recent_changes":
            return self._recent_changes_projection(
                bundle,
                selected_scope=selected_scope,
                max_sensitivity=max_sensitivity,
                limit=limit,
            )
        if action == "browse_kind":
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
        if action == "recent_changes":
            return self._recent_changes_legacy(
                selected_scope=selected_scope,
                max_sensitivity=max_sensitivity,
                limit=limit,
            )
        if action == "browse_kind":
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
