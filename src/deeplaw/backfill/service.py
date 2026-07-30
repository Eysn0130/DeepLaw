from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ..knowledge_autonomy import (
    KNOWLEDGE_KINDS,
    SENSITIVITY_ORDER,
    AutonomousKnowledgeStore,
    _atomic_owner_write,
    _read_object,
    _validate_contract,
    _write_object,
)
from ..knowledge_intelligence import normalize_identity_text
from ..util import (
    canonical_json,
    has_instruction_risk,
    sha256_bytes,
    sha256_file,
    stable_id,
    strict_json_loads,
)

_BACKFILL_SCHEMA = "deeplaw.knowledge-backfill-draft/v1"
_BACKFILL_RECEIPT_SCHEMA = "deeplaw.knowledge-backfill-receipt/v1"


class BackfillService:
    """Two-phase, explicitly granted query-synthesis backfill."""

    def __init__(self, path: str | Path) -> None:
        self.root = Path(path).expanduser().absolute()

    def propose(
        self,
        *,
        grant_id: str,
        idempotency_key: str,
        query: str,
        title: str,
        body: str,
        kind: str,
        durable: bool,
        reusable: bool,
        novel: bool,
        non_duplicate: bool,
        contains_case_data: bool,
        source_refs: list[dict[str, Any]] | None,
        source_free: bool,
        scope: str,
        sensitivity: str,
        semantic_key: str | None = None,
        knowledge_id: str | None = None,
        expected_revision_id: str | None = None,
        tags: list[str] | None = None,
        run_id: str | None = None,
        model_id: str | None = None,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data or contains_case_data:
            raise ValueError("query backfill rejects case data")
        if not all((durable, reusable, novel, non_duplicate)):
            raise ValueError(
                "query backfill draft requires durable, reusable, novel, non-duplicate content"
            )
        selected_query = self._text(query, field="backfill query", maximum=5_000)
        selected_title = self._text(title, field="backfill title", maximum=500)
        selected_body = self._text(body, field="backfill body", maximum=100_000)
        selected_key = (
            self._text(semantic_key, field="backfill semantic key", maximum=300)
            if semantic_key is not None
            else None
        )
        selected_idempotency = self._text(
            idempotency_key,
            field="backfill idempotency key",
            maximum=200,
        )
        if kind not in KNOWLEDGE_KINDS or kind == "skill":
            raise ValueError("query backfill kind is invalid")
        if scope not in {"personal", "project", "domain"}:
            raise ValueError("query backfill scope is invalid")
        if sensitivity not in {"public", "internal", "private"}:
            raise ValueError("query backfill sensitivity is invalid")
        if has_instruction_risk(selected_body):
            raise ValueError("query backfill contains persistent instruction risk")
        if bool(source_refs) == source_free:
            raise ValueError(
                "query backfill requires source bindings or an explicit source_free marker"
            )
        selected_tags = list(dict.fromkeys(tags or []))
        if (
            len(selected_tags) > 31
            or any(
                not isinstance(tag, str)
                or tag != tag.strip()
                or not 1 <= len(tag) <= 100
                for tag in selected_tags
            )
        ):
            raise ValueError("query backfill tags are invalid")
        request = {
            "operation": "propose_knowledge_backfill",
            "idempotency_key": selected_idempotency,
            "query": selected_query,
            "title": selected_title,
            "body": selected_body,
            "kind": kind,
            "durable": durable,
            "reusable": reusable,
            "novel": novel,
            "non_duplicate": non_duplicate,
            "contains_case_data": False,
            "source_refs": source_refs or [],
            "source_free": source_free,
            "scope": scope,
            "sensitivity": sensitivity,
            "semantic_key": selected_key,
            "knowledge_id": knowledge_id,
            "expected_revision_id": expected_revision_id,
            "tags": selected_tags,
            "run_id": run_id,
            "model_id": model_id,
        }
        request_bytes = canonical_json(request).encode("utf-8")
        request_sha256 = sha256_bytes(request_bytes)
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            grant = store._grant(
                grant_id,
                operation="propose_knowledge_backfill",
                request_bytes=len(request_bytes),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            if scope != grant["allowed_scope"] or SENSITIVITY_ORDER.index(
                sensitivity
            ) > SENSITIVITY_ORDER.index(grant["max_sensitivity"]):
                raise PermissionError("query backfill exceeds its granted boundary")
            pinned_refs = store._pin_source_references(source_refs or [])
            request["source_refs"] = pinned_refs
            self._preflight_identity(
                store,
                title=selected_title,
                kind=kind,
                scope=scope,
                sensitivity=sensitivity,
                semantic_key=selected_key,
                knowledge_id=knowledge_id,
                expected_revision_id=expected_revision_id,
            )
            draft_id = stable_id(
                "backfilldraft",
                store.vault_id,
                grant_id,
                selected_idempotency,
            )
            existing = store.connection.execute(
                """
                SELECT * FROM query_backfill_drafts_v1
                WHERE grant_id = ? AND idempotency_key = ?
                """,
                (grant_id, selected_idempotency),
            ).fetchone()
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise RuntimeError(
                        "query backfill idempotency key was reused with another request"
                    )
                return self._status(store, existing, idempotent_replay=True)
            created_at = store._next_transaction_time(strictly_after_event=True)
            draft = {
                "schema_version": _BACKFILL_SCHEMA,
                "draft_id": draft_id,
                "query_sha256": sha256_bytes(selected_query.encode("utf-8")),
                "title": selected_title,
                "body": selected_body,
                "kind": kind,
                "durable": True,
                "reusable": True,
                "novel": True,
                "non_duplicate": True,
                "contains_case_data": False,
                "source_refs": pinned_refs,
                "source_free": source_free,
                "scope": scope,
                "sensitivity": sensitivity,
                "semantic_key": selected_key,
                "knowledge_id": knowledge_id,
                "expected_revision_id": expected_revision_id,
                "tags": selected_tags,
                "generation": {
                    "run_id": run_id,
                    "model_id": model_id,
                    "tool_id": "purpose-aware-query-backfill",
                },
                "origin": "agent_derived",
                "authority": "agent_derived",
                "legal_authority": False,
                "status": "proposed",
                "created_at": created_at,
            }
            _validate_contract("knowledge-backfill-draft.v1.schema.json", draft)
            draft_bytes = canonical_json(draft).encode("utf-8")
            draft_sha256, _ = _write_object(store.root, draft_bytes)
            workspace_path = f"drafts/{draft_id}.md"
            workspace_bytes = self._workspace_markdown(draft)
            workspace_sha256 = sha256_bytes(workspace_bytes)
            _atomic_owner_write(store.root / workspace_path, workspace_bytes)
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked_grant = store._grant(
                    grant_id,
                    operation="propose_knowledge_backfill",
                    request_bytes=len(request_bytes),
                )
                store.connection.execute(
                    """
                    INSERT INTO source_compilation_artifacts_v1(
                        artifact_sha256, artifact_role, byte_size,
                        media_type, created_at
                    ) VALUES (?, 'query_backfill', ?, 'application/json', ?)
                    """,
                    (draft_sha256, len(draft_bytes), created_at),
                )
                store.connection.execute(
                    """
                    INSERT INTO query_backfill_drafts_v1(
                        draft_id, grant_id, idempotency_key, request_sha256,
                        query_sha256, draft_sha256, workspace_path,
                        workspace_sha256, status, validation_sha256,
                        promoted_revision_id, promotion_receipt_sha256,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'proposed', NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        draft_id,
                        grant_id,
                        selected_idempotency,
                        request_sha256,
                        draft["query_sha256"],
                        draft_sha256,
                        workspace_path,
                        workspace_sha256,
                        created_at,
                        created_at,
                    ),
                )
                store._append_event(
                    event_type="knowledge_backfill_proposed",
                    object_id=draft_id,
                    payload={
                        "draft_sha256": draft_sha256,
                        "query_sha256": draft["query_sha256"],
                        "grant_id": grant_id,
                        "writer_id": locked_grant["writer_id"],
                        "origin": "agent_derived",
                        "authority": "agent_derived",
                        "legal_authority": False,
                    },
                    recorded_at=created_at,
                )
                self._record_usage(
                    store,
                    grant_id=grant_id,
                    request_sha256=request_sha256,
                    draft_id=draft_id,
                    recorded_at=created_at,
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                (store.root / workspace_path).unlink(missing_ok=True)
                raise
            row = store.connection.execute(
                "SELECT * FROM query_backfill_drafts_v1 WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            return self._status(store, row, idempotent_replay=False)

    def validate(
        self,
        *,
        grant_id: str,
        draft_id: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError("query backfill validation requires no-case-data confirmation")
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            grant = store._grant(
                grant_id,
                operation="propose_knowledge_backfill",
                request_bytes=len(draft_id.encode("utf-8")),
            )
            store._enforce_grant_limits(grant, enforce_object_capacity=False)
            row = self._draft_row(store, draft_id, grant_id=grant_id)
            if row["status"] in {"validated", "promoted"}:
                return self._validation_result(
                    store,
                    row,
                    idempotent_replay=True,
                )
            if row["status"] != "proposed":
                raise RuntimeError("query backfill draft cannot be validated from its state")
            draft = self._load_draft(store, row)
            self._verify_workspace(store, row)
            self._preflight_identity(
                store,
                title=draft["title"],
                kind=draft["kind"],
                scope=draft["scope"],
                sensitivity=draft["sensitivity"],
                semantic_key=draft["semantic_key"],
                knowledge_id=draft["knowledge_id"],
                expected_revision_id=draft["expected_revision_id"],
            )
            if has_instruction_risk(draft["body"]):
                raise ValueError("query backfill contains persistent instruction risk")
            validation = {
                "schema_version": "deeplaw.knowledge-backfill-validation/v1",
                "draft_id": draft_id,
                "valid": True,
                "checks": [
                    "durable_reusable_novel",
                    "no_case_data",
                    "source_binding",
                    "scope_sensitivity",
                    "identity_expected_revision",
                    "instruction_risk",
                ],
                "draft_sha256": row["draft_sha256"],
                "validated_at": store._next_transaction_time(strictly_after_event=True),
            }
            validation_bytes = canonical_json(validation).encode("utf-8")
            expected_validation_sha256 = sha256_bytes(validation_bytes)
            _validate_contract(
                "knowledge-backfill-validation.v1.schema.json",
                {
                    **validation,
                    "validation_sha256": expected_validation_sha256,
                    "idempotent_replay": False,
                },
            )
            validation_sha256, _ = _write_object(store.root, validation_bytes)
            if validation_sha256 != expected_validation_sha256:
                raise RuntimeError("query backfill validation artifact digest changed")
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = self._draft_row(store, draft_id, grant_id=grant_id)
                if locked["status"] != "proposed":
                    raise RuntimeError("query backfill draft changed during validation")
                store.connection.execute(
                    """
                    INSERT INTO source_compilation_artifacts_v1(
                        artifact_sha256, artifact_role, byte_size,
                        media_type, created_at
                    ) VALUES (?, 'query_backfill', ?, 'application/json', ?)
                    """,
                    (
                        validation_sha256,
                        len(validation_bytes),
                        validation["validated_at"],
                    ),
                )
                store.connection.execute(
                    """
                    UPDATE query_backfill_drafts_v1
                    SET status = 'validated', validation_sha256 = ?, updated_at = ?
                    WHERE draft_id = ?
                    """,
                    (validation_sha256, validation["validated_at"], draft_id),
                )
                store._append_event(
                    event_type="knowledge_backfill_validated",
                    object_id=draft_id,
                    payload={
                        "draft_sha256": row["draft_sha256"],
                        "validation_sha256": validation_sha256,
                        "grant_id": grant_id,
                    },
                    recorded_at=validation["validated_at"],
                )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            validation["validation_sha256"] = validation_sha256
            validation["idempotent_replay"] = False
            return validation

    def promote(
        self,
        *,
        grant_id: str,
        draft_id: str,
        idempotency_key: str,
        evaluator_type: str,
        evaluator_id: str,
        evaluation_reason: str,
        confirm_no_case_data: bool,
    ) -> dict[str, Any]:
        if not confirm_no_case_data:
            raise ValueError("query backfill promotion requires no-case-data confirmation")
        if evaluator_type not in {"user", "external_check", "owner_policy"}:
            raise ValueError("query backfill evaluator type is invalid")
        selected_evaluator = self._text(
            evaluator_id, field="backfill evaluator ID", maximum=200
        )
        selected_reason = self._text(
            evaluation_reason,
            field="backfill evaluation reason",
            maximum=2_000,
        )
        selected_idempotency = self._text(
            idempotency_key,
            field="backfill promotion idempotency key",
            maximum=200,
        )
        if has_instruction_risk(selected_reason):
            raise ValueError("query backfill evaluation reason contains instruction risk")
        with AutonomousKnowledgeStore(self.root, read_only=False) as store:
            grant = store._grant(
                grant_id,
                operation="promote_knowledge_draft",
                request_bytes=len(
                    canonical_json(
                        {
                            "draft_id": draft_id,
                            "idempotency_key": selected_idempotency,
                            "evaluator_type": evaluator_type,
                            "evaluator_id": selected_evaluator,
                            "evaluation_reason": selected_reason,
                        }
                    ).encode("utf-8")
                ),
            )
            allowed_evaluators = store._grant_evaluator_types(grant)
            if evaluator_type in {"user", "external_check"}:
                if evaluator_type not in allowed_evaluators:
                    raise PermissionError("query backfill evaluator is not granted")
            elif grant["writer_id"] != "owner":
                raise PermissionError("owner_policy promotion requires writer_id=owner")
            row = self._draft_row(store, draft_id, grant_id=grant_id)
            if row["status"] == "promoted":
                return self._promotion_result(store, row, idempotent_replay=True)
            if row["status"] != "validated":
                raise RuntimeError("query backfill must be validated before promotion")
            draft = self._load_draft(store, row)
            self._verify_workspace(store, row)
            self._preflight_identity(
                store,
                title=draft["title"],
                kind=draft["kind"],
                scope=draft["scope"],
                sensitivity=draft["sensitivity"],
                semantic_key=draft["semantic_key"],
                knowledge_id=draft["knowledge_id"],
                expected_revision_id=draft["expected_revision_id"],
            )
            revision = store.remember(
                grant_id=grant_id,
                idempotency_key=selected_idempotency,
                title=draft["title"],
                body=draft["body"],
                kind=draft["kind"],
                knowledge_id=draft["knowledge_id"],
                expected_revision_id=draft["expected_revision_id"],
                scope=draft["scope"],
                sensitivity=draft["sensitivity"],
                source_refs=draft["source_refs"],
                run_id=draft["generation"]["run_id"],
                model_id=draft["generation"]["model_id"],
                tool_id="purpose-aware-query-backfill",
                tags=[*draft["tags"], "query-backfill"],
                semantic_key=draft["semantic_key"],
                requested_origin="agent_derived",
                requested_authority="agent_derived",
                confirm_no_case_data=True,
                operation="promote_knowledge_draft",
            )
            promoted_at = store._next_transaction_time(strictly_after_event=True)
            receipt = {
                "schema_version": _BACKFILL_RECEIPT_SCHEMA,
                "draft_id": draft_id,
                "revision_id": revision["revision_id"],
                "knowledge_id": revision["knowledge_id"],
                "draft_sha256": row["draft_sha256"],
                "validation_sha256": row["validation_sha256"],
                "evaluator_type": evaluator_type,
                "evaluator_id": selected_evaluator,
                "evaluation_reason_sha256": sha256_bytes(
                    selected_reason.encode("utf-8")
                ),
                "origin": "agent_derived",
                "authority": "agent_derived",
                "legal_authority": False,
                "promoted_at": promoted_at,
                "audit_head": store.audit_head,
            }
            _validate_contract("knowledge-backfill-receipt.v1.schema.json", receipt)
            receipt_bytes = canonical_json(receipt).encode("utf-8")
            receipt_sha256, _ = _write_object(store.root, receipt_bytes)
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                locked = self._draft_row(store, draft_id, grant_id=grant_id)
                if locked["status"] not in {"validated", "promoted"}:
                    raise RuntimeError("query backfill draft changed during promotion")
                store.connection.execute(
                    """
                    INSERT OR IGNORE INTO source_compilation_artifacts_v1(
                        artifact_sha256, artifact_role, byte_size,
                        media_type, created_at
                    ) VALUES (?, 'query_backfill', ?, 'application/json', ?)
                    """,
                    (receipt_sha256, len(receipt_bytes), promoted_at),
                )
                store.connection.execute(
                    """
                    UPDATE query_backfill_drafts_v1
                    SET status = 'promoted', promoted_revision_id = ?,
                        promotion_receipt_sha256 = ?, updated_at = ?
                    WHERE draft_id = ?
                    """,
                    (
                        revision["revision_id"],
                        receipt_sha256,
                        promoted_at,
                        draft_id,
                    ),
                )
                existing_event = store.connection.execute(
                    """
                    SELECT 1 FROM autonomous_events_v3
                    WHERE event_type = 'knowledge_backfill_promoted'
                      AND object_id = ?
                    """,
                    (draft_id,),
                ).fetchone()
                if existing_event is None:
                    store._append_event(
                        event_type="knowledge_backfill_promoted",
                        object_id=draft_id,
                        payload={
                            "revision_id": revision["revision_id"],
                            "knowledge_id": revision["knowledge_id"],
                            "receipt_sha256": receipt_sha256,
                            "grant_id": grant_id,
                            "evaluator_type": evaluator_type,
                            "evaluator_id": selected_evaluator,
                            "origin": "agent_derived",
                            "authority": "agent_derived",
                            "legal_authority": False,
                        },
                        recorded_at=promoted_at,
                    )
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise
            receipt["receipt_sha256"] = receipt_sha256
            receipt["idempotent_replay"] = False
            receipt["audit_head"] = store.audit_head
            return receipt

    def status(self, draft_id: str) -> dict[str, Any]:
        with AutonomousKnowledgeStore(self.root, read_only=True) as store:
            row = self._draft_row(store, draft_id, grant_id=None)
            return self._status(store, row, idempotent_replay=False)

    @staticmethod
    def _text(value: str, *, field: str, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or value != value.strip()
            or not value
            or len(value) > maximum
        ):
            raise ValueError(f"{field} must be bounded canonical text")
        return value

    @staticmethod
    def _preflight_identity(
        store: AutonomousKnowledgeStore,
        *,
        title: str,
        kind: str,
        scope: str,
        sensitivity: str,
        semantic_key: str | None,
        knowledge_id: str | None,
        expected_revision_id: str | None,
    ) -> None:
        if knowledge_id is None and expected_revision_id is not None:
            raise ValueError("new query backfill cannot declare expected_revision_id")
        if knowledge_id is not None:
            current = store.get_current(knowledge_id, include_inactive=True)
            if current["revision_id"] != expected_revision_id:
                raise RuntimeError("query backfill expected revision is stale")
            if (
                current["kind"] != kind
                or current["scope"] != scope
                or current["sensitivity"] != sensitivity
            ):
                raise ValueError("query backfill cannot change identity policy dimensions")
        identity_query = semantic_key or title
        identity = store.lookup_identity(
            normalize_identity_text(identity_query),
            scope=cast(Any, scope),
            max_sensitivity=cast(Any, sensitivity),
            limit=2,
        )
        if identity["status"] == "ambiguous":
            raise RuntimeError("query backfill semantic identity is ambiguous")
        if identity["status"] == "resolved":
            resolved_id = identity["candidates"][0]["knowledge_id"]
            if knowledge_id is None or resolved_id != knowledge_id:
                raise ValueError(
                    "query backfill duplicates an existing identity; revise it explicitly"
                )

    @staticmethod
    def _workspace_markdown(draft: dict[str, Any]) -> bytes:
        frontmatter = {
            "schema_version": draft["schema_version"],
            "draft_id": draft["draft_id"],
            "status": "proposed",
            "origin": "agent_derived",
            "authority": "agent_derived",
            "legal_authority": False,
            "source_free": draft["source_free"],
        }
        lines = [
            "---",
            *(
                f"{key}: {canonical_json(value)}"
                for key, value in frontmatter.items()
            ),
            "---",
            "",
            f"# {draft['title']}",
            "",
            draft["body"],
            "",
            "## Promotion gate",
            "",
            (
                "This is a non-canonical draft. Explicit validation and a promotion "
                "grant are required."
            ),
            "",
        ]
        return "\n".join(lines).encode("utf-8")

    @staticmethod
    def _record_usage(
        store: AutonomousKnowledgeStore,
        *,
        grant_id: str,
        request_sha256: str,
        draft_id: str,
        recorded_at: str,
    ) -> None:
        operation_id = stable_id(
            "compilationop",
            grant_id,
            "propose_knowledge_backfill",
            draft_id,
            request_sha256,
        )
        store.connection.execute(
            """
            INSERT INTO source_compilation_usage_v1(
                operation_id, grant_id, operation, request_sha256, recorded_at
            ) VALUES (?, ?, 'propose_knowledge_backfill', ?, ?)
            """,
            (operation_id, grant_id, request_sha256, recorded_at),
        )

    @staticmethod
    def _draft_row(
        store: AutonomousKnowledgeStore,
        draft_id: str,
        *,
        grant_id: str | None,
    ) -> Any:
        row = store.connection.execute(
            "SELECT * FROM query_backfill_drafts_v1 WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()
        if row is None or (grant_id is not None and row["grant_id"] != grant_id):
            raise KeyError("query backfill draft is unavailable")
        return row

    @staticmethod
    def _load_draft(
        store: AutonomousKnowledgeStore,
        row: Any,
    ) -> dict[str, Any]:
        value = strict_json_loads(_read_object(store.root, row["draft_sha256"]))
        if not isinstance(value, dict):
            raise RuntimeError("query backfill draft artifact is invalid")
        _validate_contract("knowledge-backfill-draft.v1.schema.json", value)
        return value

    @staticmethod
    def _verify_workspace(store: AutonomousKnowledgeStore, row: Any) -> None:
        path = store.root / row["workspace_path"]
        if (
            path.is_symlink()
            or not path.is_file()
            or sha256_file(path) != row["workspace_sha256"]
        ):
            raise RuntimeError(
                "query backfill workspace draft changed; re-propose before validation"
            )

    @staticmethod
    def _status(
        store: AutonomousKnowledgeStore,
        row: Any,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        value = {
            "schema_version": "deeplaw.knowledge-backfill-status/v1",
            "draft_id": row["draft_id"],
            "status": row["status"],
            "draft_sha256": row["draft_sha256"],
            "validation_sha256": row["validation_sha256"],
            "promoted_revision_id": row["promoted_revision_id"],
            "promotion_receipt_sha256": row["promotion_receipt_sha256"],
            "idempotent_replay": idempotent_replay,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "audit_head": store.audit_head,
        }
        _validate_contract("knowledge-backfill-status.v1.schema.json", value)
        return value

    @staticmethod
    def _validation_result(
        store: AutonomousKnowledgeStore,
        row: Any,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        if row["validation_sha256"] is None:
            raise RuntimeError("validated query backfill has no validation artifact")
        value = strict_json_loads(_read_object(store.root, row["validation_sha256"]))
        if not isinstance(value, dict):
            raise RuntimeError("query backfill validation artifact is invalid")
        value["validation_sha256"] = row["validation_sha256"]
        value["idempotent_replay"] = idempotent_replay
        _validate_contract("knowledge-backfill-validation.v1.schema.json", value)
        return value

    @staticmethod
    def _promotion_result(
        store: AutonomousKnowledgeStore,
        row: Any,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        if row["promotion_receipt_sha256"] is None:
            raise RuntimeError("promoted query backfill has no receipt")
        value = strict_json_loads(
            _read_object(store.root, row["promotion_receipt_sha256"])
        )
        if not isinstance(value, dict):
            raise RuntimeError("query backfill promotion receipt is invalid")
        value["receipt_sha256"] = row["promotion_receipt_sha256"]
        value["idempotent_replay"] = idempotent_replay
        value["audit_head"] = store.audit_head
        return value
