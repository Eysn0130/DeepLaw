"""Development compatibility reproductions for the PRD 1.2 Knowledge Sink.

This file records development compatibility evidence only.  It is not Human
Gold, external qualification, or release evidence.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from jsonschema import Draft202012Validator

from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_store import initialize_knowledge_vault

_HISTORICAL_V2_SHA256 = (
    "b3c5c100471cec3a8ecdce115255ae3e4d0d7053800936e5a611fe103527019a"
)
_HISTORICAL_RUN_METADATA_FIELDS = frozenset(
    {"task_kind", "tool_ids", "artifact_ids", "notes_sha256"}
)


def _schema() -> dict[str, object]:
    repository = Path(__file__).resolve().parents[1]
    return json.loads(
        (repository / "contracts/knowledge-sink.input.v2.schema.json").read_text()
    )


def _record_run_request(*, run_metadata: dict[str, object] | None = None) -> dict[str, object]:
    request: dict[str, object] = {
        "operation": "record_run",
        "idempotency_key": "prd12-sink-v2-shape",
        "confirm_no_case_data": True,
        "run_id": "run-prd12-v2-legacy",
        "task": "Reproduce the historical Knowledge Sink v2 run flow.",
        "host_id": "pytest",
        "status": "succeeded",
    }
    if run_metadata is not None:
        request["run_metadata"] = run_metadata
    return request


def _new_vault(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="prd12-sink-compatibility", scope="project")
    initialize_autonomous_core(root)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        grant_id = store.enable_grant(
            writer_id="prd12-sink-compatibility",
            operations=("record_run", "remember"),
        )["grant_id"]
    return root, grant_id


def test_knowledge_sink_v2_file_and_shape_remain_historical() -> None:
    """Freeze the historical v2 contract before the additive binding revision."""

    repository = Path(__file__).resolve().parents[1]
    contract_path = repository / "contracts/knowledge-sink.input.v2.schema.json"
    assert hashlib.sha256(contract_path.read_bytes()).hexdigest() == _HISTORICAL_V2_SHA256

    schema = _schema()
    run_metadata = schema["properties"]["run_metadata"]
    assert isinstance(run_metadata, dict)
    metadata_properties = run_metadata["properties"]
    assert isinstance(metadata_properties, dict)
    assert set(metadata_properties) == _HISTORICAL_RUN_METADATA_FIELDS

    # Validate both historical cases against the frozen field set.  The
    # worktree assertion above ensures this clone cannot hide a v2 mutation.
    historical_schema = deepcopy(schema)
    historical_run_metadata = historical_schema["properties"]["run_metadata"]
    assert isinstance(historical_run_metadata, dict)
    historical_run_metadata["properties"] = {
        key: metadata_properties[key] for key in _HISTORICAL_RUN_METADATA_FIELDS
    }
    validator = Draft202012Validator(historical_schema)
    validator.validate(_record_run_request())
    assert list(
        validator.iter_errors(
            _record_run_request(run_metadata={"task_binding": {}})
        )
    )


def test_historical_unbound_working_checkpoint_is_still_admitted(tmp_path: Path) -> None:
    """Reproduce the old v2 unbound Run -> working checkpoint flow."""

    root, grant_id = _new_vault(tmp_path)
    with AutonomousKnowledgeStore(root, read_only=False) as store:
        run = store.record_run(
            grant_id=grant_id,
            idempotency_key="prd12-sink-v2-run",
            run_id="run-prd12-v2-legacy",
            task="Reproduce the historical Knowledge Sink v2 run flow.",
            host_id="pytest",
            status="succeeded",
            confirm_no_case_data=True,
        )
        checkpoint = store.remember(
            grant_id=grant_id,
            idempotency_key="prd12-sink-v2-working",
            title="Historical working checkpoint",
            body="This checkpoint is retained for the legacy compatibility path.",
            kind="memory",
            memory_type="working",
            expires_at="2099-01-01T00:00:00Z",
            run_id=run["run_id"],
            confirm_no_case_data=True,
        )

        assert checkpoint["revision_id"]
        current = store.get_current(checkpoint["knowledge_id"])
        assert current["revision_id"] == checkpoint["revision_id"]
