"""Exact, policy-admitted MCP reads over existing knowledge and evidence services."""

from __future__ import annotations

from typing import Any

from .knowledge_autonomy import bounded_source_reference
from .persistent_read_runtime import PersistentReadSnapshot
from .read_services import SourceReadService, WikiReadService
from .util import assert_provider_output_safe, canonical_json, sha256_bytes, strict_json_loads


def read_exact(arguments: dict[str, Any], snapshot: PersistentReadSnapshot) -> dict[str, Any]:
    # Reuse the same admission seam as the existing knowledge reads. Import lazily
    # because the MCP dispatcher owns the transport, not the domain services.
    from .knowledge_mcp_server import _require_autonomous_admission

    target = arguments["target"]
    scope = arguments["scope"]
    sensitivity = arguments["max_sensitivity"]
    offset = arguments.get("offset", 0)
    max_chars = arguments.get("max_chars", 4000)
    expected_digest = arguments.get("content_sha256")
    source_service = SourceReadService(snapshot.store.root)
    source_refs: list[dict[str, Any]] = []
    locator = None
    if target["kind"] == "source_fragment":
        result = source_service.execute(
            action="fragment",
            fragment_id=target["fragment_id"],
            scope=scope,
            max_sensitivity=sensitivity,
            offset=offset,
            max_chars=max_chars,
            snapshot=snapshot,
        )["fragment"]
        if result["source_revision_id"] != target["source_revision_id"]:
            raise KeyError("Source fragment does not match the exact revision")
        content = result["text"]
        digest = result["text_sha256"]
        locator = result["locator"]
        full_text = snapshot.legacy.get_fragment(result["fragment_id"])["text"]
        assert_provider_output_safe({"content": full_text}, interface="knowledge_support")
        total = len(full_text)
        card = source_service.execute(
            action="get",
            source_id=result["source_id"],
            scope=scope,
            max_sensitivity=sensitivity,
            snapshot=snapshot,
        )["source"]
        governance = {
            "origin": "user_source",
            "authority": "unknown",
            "legal_authority": False,
            "scope": snapshot.legacy.manifest["scope"],
            "sensitivity": card["sensitivity"],
            "lifecycle": card["status"],
            "verification": "integrity_verified",
            "trust": card["trust"],
            "imported_at": card["imported_at"],
            "temporal_state": "unknown",
        }
    else:
        item = snapshot.store.get_current(target["knowledge_id"])
        if item["revision_id"] != target["revision_id"]:
            raise KeyError("Knowledge revision is no longer current")
        _require_autonomous_admission(
            snapshot.store,
            item,
            scope=scope,
            max_sensitivity=sensitivity,
        )
        # Working Checkpoints require task-line admission; exact reads cannot
        # bypass the Context Compiler's task binding with a guessed identity.
        if item["kind"] == "memory" and item["metadata"].get("memory_type") == "working":
            raise PermissionError("Memory content requires task-bound context")
        checked = 0
        visiting = {item["revision_id"]}
        admitted = set()

        def admit_references(current):
            nonlocal checked
            projected = []
            statement_rows = snapshot.store.connection.execute(
                "SELECT statement_json FROM knowledge_statements_v1 "
                "WHERE knowledge_revision_id = ? ORDER BY ordinal LIMIT 4097",
                (current["revision_id"],),
            ).fetchall()
            statements = [strict_json_loads(row[0]) for row in statement_rows]
            if any(value.get("schema_version") == "deeplaw.knowledge-statement/v2"
                   for value in statements):
                from .evidence.support import SupportEvaluator

                evaluator = SupportEvaluator(snapshot.store, scope=scope, sensitivity=sensitivity)
                seen_refs = set()
                for value in statements:
                    support = evaluator.statement(value)
                    if support["freshness"] != "fresh":
                        raise KeyError("Knowledge complete support set is unavailable")
                    for reference in support["support_set"]["source_refs"]:
                        key = canonical_json(reference)
                        if key not in seen_refs:
                            checked += 1
                            if checked > 32:
                                raise ValueError(
                                    "Knowledge lineage admission exceeds the read bound"
                                )
                            projected.append(bounded_source_reference(reference))
                            seen_refs.add(key)
                return projected
            for reference in current["source_refs"]:
                checked += 1
                if checked > 32:
                    raise ValueError("Knowledge lineage admission exceeds the read bound")
                keys = set(reference)
                metadata = {"object_sha256"}
                if "revision_id" in keys and keys <= metadata | {"revision_id"}:
                    revision_id = reference["revision_id"]
                    if revision_id in visiting:
                        raise ValueError("Knowledge lineage contains a cycle")
                    row = snapshot.store.connection.execute(
                        "SELECT knowledge_id FROM knowledge_revisions_v3 WHERE revision_id = ?",
                        (revision_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError("Knowledge lineage revision is unavailable")
                    dependency = snapshot.store.get_current(row["knowledge_id"])
                    if dependency["revision_id"] != revision_id or dependency["kind"] == "memory":
                        raise KeyError("Knowledge lineage revision is unavailable")
                    if revision_id not in admitted:
                        visiting.add(revision_id)
                        admit_references(dependency)
                        _require_autonomous_admission(
                            snapshot.store, dependency, scope=scope, max_sensitivity=sensitivity
                        )
                        visiting.remove(revision_id)
                        admitted.add(revision_id)
                    ref = bounded_source_reference(reference)
                    ref["knowledge_id"] = dependency["knowledge_id"]
                elif "artifact_id" in keys and keys <= metadata | {"artifact_id"}:
                    if not snapshot.store._source_reference_is_bound(
                        reference, scope=scope, max_sensitivity=sensitivity
                    ):
                        raise KeyError("Knowledge artifact is unavailable")
                    ref = bounded_source_reference(reference)
                elif keys <= {
                    "source_id", "source_revision_id", "fragment_id", "locator", "uri",
                    "quote_sha256", "object_sha256",
                } and ("source_revision_id" in keys or "source_id" in keys):
                    source_id = reference.get("source_id")
                    revision_id = reference.get("source_revision_id")
                    if revision_id is not None:
                        row = snapshot.store.connection.execute(
                            "SELECT legacy_source_id FROM source_revision_bindings_v2 "
                            "WHERE source_revision_id = ?", (revision_id,),
                        ).fetchone()
                        if row is None or (source_id is not None and source_id != row[0]):
                            raise KeyError("Knowledge source revision is unavailable")
                        source_id = row[0]
                    card = source_service.execute(
                        action="get", source_id=source_id, scope=scope,
                        max_sensitivity=sensitivity, snapshot=snapshot,
                    )["source"]
                    if revision_id is not None and card["source_revision_id"] != revision_id:
                        raise KeyError("Knowledge source revision is unavailable")
                    if "fragment_id" in reference:
                        fragment = source_service.execute(
                            action="fragment", fragment_id=reference["fragment_id"],
                            scope=scope, max_sensitivity=sensitivity, max_chars=200,
                            snapshot=snapshot,
                        )["fragment"]
                        if fragment["source_id"] != source_id:
                            raise KeyError("Knowledge source fragment is unavailable")
                    ref = bounded_source_reference(reference)
                    ref["source_revision_id"] = card["source_revision_id"]
                else:
                    raise ValueError("Knowledge lineage reference shape is unsupported")
                projected.append(ref)
            return projected

        source_refs = admit_references(item)
        governance = {
            key: item[key]
            for key in (
                "origin",
                "authority",
                "verification",
                "lifecycle",
                "scope",
                "sensitivity",
                "legal_authority",
                "source_free",
                "valid_from",
                "valid_to",
                "expires_at",
            )
        }
        governance["temporal_state"] = "current_admitted"
        governance["epistemic_state"] = item["epistemic_state"]
        governance["directive_mode"] = "data_only"
        if target["kind"] == "wiki":
            cursor = (
                WikiReadService._page_cursor_encode(expected_digest, offset) if offset else None
            )
            page = WikiReadService(snapshot.store.root).execute(
                action="page",
                knowledge_id=item["knowledge_id"],
                scope=scope,
                max_sensitivity=sensitivity,
                cursor=cursor,
                snapshot=snapshot,
            )
            if snapshot.wiki is None:
                raise RuntimeError("Wiki projection is unavailable")
            full_text = snapshot.wiki.read_page(page["wiki_path"]).decode("utf-8")
            assert_provider_output_safe({"content": full_text}, interface="knowledge_support")
            content = page["content"][:max_chars]
            digest = page["content_sha256"]
            total = page["total_count"]
        else:
            body = item["body"]
            if not isinstance(body, str):
                raise KeyError("Registered knowledge content is unavailable")
            # Validate before slicing so pagination cannot split a private path
            # or a credential marker into independently innocuous pieces.
            assert_provider_output_safe({"content": body}, interface="knowledge_support")
            content = body[offset : offset + max_chars]
            digest = sha256_bytes(body.encode("utf-8"))
            total = len(body)
    if offset > total or (expected_digest is not None and expected_digest != digest):
        raise ValueError("Read continuation does not match the exact content")
    result = {
        "target": dict(target),
        "content": content,
        "content_sha256": digest,
        "offset": offset,
        "next_offset": offset + len(content) if offset + len(content) < total else None,
        "total_characters": total,
        "source_refs": source_refs,
        "governance": governance,
        "locator": locator,
        "write_performed": False,
    }
    assert_provider_output_safe(result, interface="knowledge_support")
    return result
