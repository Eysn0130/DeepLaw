from __future__ import annotations

import sqlite3

import pytest

from deeplaw.compilation.store import (
    compilation_tables_sql,
    install_compilation_schema,
)

_SOURCE_REVISION_ID = "source-revision"
_SOURCE_KEY = "source-key"
_COMPILATION_ID = "source-compilation"
_GRANT_ID = "compilation-grant"
_RUN_ID = "compilation-run"
_RECORDED_AT = "2026-01-01T00:00:00Z"


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE source_revisions_v2 (
            source_revision_id TEXT PRIMARY KEY
        ) STRICT;
        CREATE TABLE source_identities_v2 (
            source_key TEXT PRIMARY KEY
        ) STRICT;
        CREATE TABLE compilations_v2 (
            compilation_id TEXT PRIMARY KEY
        ) STRICT;
        CREATE TABLE knowledge_sink_grants_v3 (
            grant_id TEXT PRIMARY KEY
        ) STRICT;
        CREATE TABLE knowledge_objects_v3 (
            knowledge_id TEXT PRIMARY KEY
        ) STRICT;
        CREATE TABLE knowledge_revisions_v3 (
            revision_id TEXT PRIMARY KEY
        ) STRICT;
        CREATE TABLE knowledge_relation_revisions_v3 (
            relation_revision_id TEXT PRIMARY KEY
        ) STRICT;
        CREATE TABLE knowledge_statements_v1 (
            statement_id TEXT PRIMARY KEY,
            knowledge_revision_id TEXT,
            ordinal INTEGER
        ) STRICT;
        """
    )
    connection.executescript(compilation_tables_sql())
    connection.executemany(
        "INSERT INTO {} VALUES (?)".format("source_revisions_v2"),
        [(_SOURCE_REVISION_ID,)],
    )
    connection.execute("INSERT INTO source_identities_v2 VALUES (?)", (_SOURCE_KEY,))
    connection.execute("INSERT INTO compilations_v2 VALUES (?)", (_COMPILATION_ID,))
    connection.execute("INSERT INTO knowledge_sink_grants_v3 VALUES (?)", (_GRANT_ID,))
    connection.execute("INSERT INTO knowledge_revisions_v3 VALUES (?)", ("knowledge-revision",))
    connection.execute(
        "INSERT INTO knowledge_relation_revisions_v3 VALUES (?)",
        ("relation-revision",),
    )
    connection.execute(
        """
        INSERT INTO source_compilation_runs_v1(
            compilation_run_id, source_revision_id, source_key,
            source_ir_compilation_id, grant_id, compiler_profile,
            compiler_profile_version, host_identity, model_identity,
            prompt_template_id, prompt_config_sha256, plan_configuration_sha256,
            request_sha256, input_audit_head, input_legacy_audit_head,
            packet_max_fragments, packet_count, status, resumable,
            output_set_sha256, receipt_sha256, failure_stage, failure_sha256,
            created_at, updated_at, committed_at, completed_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?,
            1, 1, 'planned', 0, NULL, NULL, NULL, NULL, ?, ?, NULL, NULL
        )
        """,
        (
            _RUN_ID,
            _SOURCE_REVISION_ID,
            _SOURCE_KEY,
            _COMPILATION_ID,
            _GRANT_ID,
            "compiler",
            "1",
            "test-host",
            "deeplaw.test/v1",
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            _RECORDED_AT,
            _RECORDED_AT,
        ),
    )
    return connection


def _insert_dependency(
    connection: sqlite3.Connection,
    *,
    dependency_id: str,
    compilation_run_id: str | None,
    consumer_kind: str,
    consumer_object_id: str,
    consumer_revision_id: str,
    dependency_kind: str,
) -> None:
    connection.execute(
        """
        INSERT INTO knowledge_dependencies_v1(
            dependency_id, compilation_run_id, consumer_kind,
            consumer_object_id, consumer_revision_id, source_revision_id,
            fragment_id, dependency_kind, freshness, reason, recorded_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'fresh', 'test', ?, ?)
        """,
        (
            dependency_id,
            compilation_run_id,
            consumer_kind,
            consumer_object_id,
            consumer_revision_id,
            _SOURCE_REVISION_ID,
            dependency_kind,
            _RECORDED_AT,
            _RECORDED_AT,
        ),
    )


def _restore_legacy_dependency_table(connection: sqlite3.Connection) -> None:
    """Recreate the pre-migration table so install_compilation_schema must upgrade it."""

    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE _knowledge_dependencies_v1_legacy (
                dependency_id TEXT PRIMARY KEY,
                compilation_run_id TEXT NOT NULL
                    REFERENCES source_compilation_runs_v1(compilation_run_id),
                consumer_kind TEXT NOT NULL CHECK(consumer_kind IN (
                    'knowledge_revision', 'relation_revision'
                )),
                consumer_object_id TEXT NOT NULL,
                consumer_revision_id TEXT NOT NULL,
                source_revision_id TEXT NOT NULL
                    REFERENCES source_revisions_v2(source_revision_id),
                fragment_id TEXT,
                dependency_kind TEXT NOT NULL CHECK(dependency_kind IN ('direct', 'transitive')),
                freshness TEXT NOT NULL CHECK(freshness IN (
                    'fresh', 'stale', 'invalidated', 'unknown'
                )),
                reason TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(
                    consumer_kind, consumer_revision_id, source_revision_id,
                    fragment_id, dependency_kind
                )
            ) STRICT
            """
        )
        columns = (
            "dependency_id, compilation_run_id, consumer_kind, consumer_object_id, "
            "consumer_revision_id, source_revision_id, fragment_id, dependency_kind, "
            "freshness, reason, recorded_at, updated_at"
        )
        connection.execute(
            f"INSERT INTO _knowledge_dependencies_v1_legacy({columns}) "
            f"SELECT {columns} FROM knowledge_dependencies_v1"
        )
        connection.execute("DROP TABLE knowledge_dependencies_v1")
        connection.execute(
            "ALTER TABLE _knowledge_dependencies_v1_legacy "
            "RENAME TO knowledge_dependencies_v1"
        )
        connection.execute(
            "CREATE INDEX knowledge_dependencies_v1_source "
            "ON knowledge_dependencies_v1(source_revision_id, freshness)"
        )
        connection.execute(
            "CREATE INDEX knowledge_dependencies_v1_consumer "
            "ON knowledge_dependencies_v1(consumer_object_id, freshness)"
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def test_dependency_migration_preserves_rows_and_allows_only_direct_relation_null_run() -> None:
    connection = _connection()
    try:
        _insert_dependency(
            connection,
            dependency_id="legacy-knowledge-dependency",
            compilation_run_id=_RUN_ID,
            consumer_kind="knowledge_revision",
            consumer_object_id="knowledge-object",
            consumer_revision_id="knowledge-revision",
            dependency_kind="direct",
        )
        _insert_dependency(
            connection,
            dependency_id="legacy-relation-dependency",
            compilation_run_id=_RUN_ID,
            consumer_kind="relation_revision",
            consumer_object_id="relation-key",
            consumer_revision_id="relation-revision",
            dependency_kind="direct",
        )
        connection.execute(
            """
            INSERT INTO revision_dependencies_v1(
                dependency_id, consumer_kind, consumer_object_id,
                consumer_revision_id, input_kind, input_id, input_set_sha256,
                freshness, reason, recorded_at, updated_at
            ) VALUES (?, 'relation_revision', ?, ?, 'knowledge_revision', ?, ?,
                      'fresh', 'legacy-digest', ?, ?)
            """,
            (
                "legacy-revision-dependency",
                "relation-key",
                "relation-revision",
                "knowledge-revision",
                "e" * 64,
                _RECORDED_AT,
                _RECORDED_AT,
            ),
        )
        legacy_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM knowledge_dependencies_v1 ORDER BY dependency_id"
            )
        ]
        legacy_digest = connection.execute(
            "SELECT input_set_sha256 FROM revision_dependencies_v1 "
            "WHERE dependency_id = 'legacy-revision-dependency'"
        ).fetchone()[0]
        _restore_legacy_dependency_table(connection)

        install_compilation_schema(
            connection,
            installed_at=_RECORDED_AT,
            migration_source="direct-relation-dependency-test",
        )

        columns = {
            row["name"]: row
            for row in connection.execute("PRAGMA table_info(knowledge_dependencies_v1)")
        }
        assert columns["compilation_run_id"]["notnull"] == 0
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'knowledge_dependencies_v1'"
        ).fetchone()[0]
        assert "CHECK(compilation_run_id IS NOT NULL OR (" in table_sql
        assert [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM knowledge_dependencies_v1 ORDER BY dependency_id"
            )
        ] == legacy_rows
        assert connection.execute(
            "SELECT input_set_sha256 FROM revision_dependencies_v1 "
            "WHERE dependency_id = 'legacy-revision-dependency'"
        ).fetchone()[0] == legacy_digest
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None

        _insert_dependency(
            connection,
            dependency_id="new-null-relation-dependency",
            compilation_run_id=None,
            consumer_kind="relation_revision",
            consumer_object_id="relation-key",
            consumer_revision_id="relation-revision",
            dependency_kind="direct",
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert_dependency(
                connection,
                dependency_id="null-knowledge-dependency",
                compilation_run_id=None,
                consumer_kind="knowledge_revision",
                consumer_object_id="knowledge-object",
                consumer_revision_id="knowledge-revision",
                dependency_kind="direct",
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            _insert_dependency(
                connection,
                dependency_id="null-transitive-dependency",
                compilation_run_id=None,
                consumer_kind="relation_revision",
                consumer_object_id="relation-key",
                consumer_revision_id="relation-revision",
                dependency_kind="transitive",
            )
    finally:
        connection.close()


def test_dependency_migration_foreign_key_failure_rolls_back_before_commit() -> None:
    connection = _connection()
    try:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            """
            INSERT INTO knowledge_dependencies_v1(
                dependency_id, compilation_run_id, consumer_kind,
                consumer_object_id, consumer_revision_id, source_revision_id,
                fragment_id, dependency_kind, freshness, reason, recorded_at, updated_at
            ) VALUES (
                'invalid-legacy-dependency', 'missing-run', 'knowledge_revision',
                'knowledge-object', 'knowledge-revision', 'missing-source', NULL,
                'direct', 'fresh', 'invalid-fixture', ?, ?
            )
            """,
            (_RECORDED_AT, _RECORDED_AT),
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        _restore_legacy_dependency_table(connection)

        with pytest.raises(RuntimeError, match="foreign key"):
            install_compilation_schema(
                connection,
                installed_at=_RECORDED_AT,
                migration_source="direct-relation-dependency-fk-test",
            )

        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'knowledge_dependencies_v1'"
        ).fetchone()[0]
        assert "compilation_run_id TEXT NOT NULL" in table_sql
        assert connection.execute(
            "SELECT dependency_id FROM knowledge_dependencies_v1"
        ).fetchone()[0] == "invalid-legacy-dependency"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()
