"""Synthetic inventory probes, not Host execution or qualification evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts import run_v013_host_task_executor as executor
from benchmarks.release import kernel_qualification_bundle_v1 as bundle
from benchmarks.release import typed_qualification_evidence as typed
from tests.test_v013_host_task_evidence import _expected_sha, _manifest, _refresh_source_ref


def _source(root: Path, relative: str, value: Any) -> dict[str, Any]:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "relative_path": relative,
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "media_type": "application/json",
    }


def _result(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_kind": "task_result",
        "schema_version": "deeplaw.v013-host-task-result/v2",
        "task_case": "living_wiki",
        "service_source": reference,
    }


def _slot(root: Path) -> dict[str, Any]:
    service = _source(root, "sources/service.json", {"synthetic_inventory_probe": True})
    payload = {
        key: _source(
            root,
            f"sources/{key}.json",
            _result(service) if key == "continuity_source" else {},
        )
        for key in executor.TYPED_SOURCE_SLOTS
    }
    envelope = {"kind": "host_event_sequence", "payload": payload}
    _source(root, "host-event-sequence.json", envelope)
    return envelope


def test_executor_inventory_retains_only_versioned_service_source(tmp_path: Path) -> None:
    envelope = _slot(tmp_path)
    executor._validate_slot_topology(tmp_path, envelope)
    _source(tmp_path, "sources/orphan.json", {})
    with pytest.raises(executor.HostTaskExecutorError, match="inventory is not closed"):
        executor._validate_slot_topology(tmp_path, envelope)


@pytest.mark.parametrize("relative", ["C:/outside.json", "C:outside.json"])
def test_executor_rejects_drive_path_before_reopening_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str
) -> None:
    envelope = _slot(tmp_path)
    envelope["payload"]["continuity_source"]["relative_path"] = relative

    def prohibited_read(*_: Any, **__: Any) -> dict[str, Any]:
        pytest.fail("unsafe task-result path reached the file reader")

    monkeypatch.setattr(executor, "_strict_object", prohibited_read)
    with pytest.raises(executor.HostTaskExecutorError, match="path is unsafe"):
        executor._validate_slot_topology(tmp_path, envelope)


def test_typed_inventory_reopens_nested_service_bytes_before_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path, host="codex", task="living_wiki", current=True)
    envelope = json.loads(manifest.read_text())
    task_path = tmp_path / envelope["payload"]["continuity_source"]["relative_path"]
    task_result = json.loads(task_path.read_text())
    task_result.update(
        _result(_source(tmp_path, "codex/living_wiki/service.json", {"probe": True}))
    )
    task_path.write_text(json.dumps(task_result))
    _refresh_source_ref(manifest, "continuity_source", task_path)
    entered: list[bool] = []

    def closure_probe(*_: Any, **__: Any) -> dict[str, Any]:
        entered.append(True)
        return {"status": "not_executed", "synthetic_inventory_probe": True}

    monkeypatch.setitem(typed._PARSERS, "host_event_sequence", closure_probe)
    result = typed.parse_typed_evidence(
        manifest,
        root=tmp_path,
        expected_corpus_sha256=_expected_sha(tmp_path, "codex", "living_wiki"),
    )
    assert entered == [True]
    assert result["status"] == "not_executed"
    (tmp_path / "codex/living_wiki/service.json").write_bytes(b"{}")
    with pytest.raises(typed.TypedQualificationEvidenceError, match=r"source (byte size|digest)"):
        typed.parse_typed_evidence(
            manifest,
            root=tmp_path,
            expected_corpus_sha256=_expected_sha(tmp_path, "codex", "living_wiki"),
        )
    assert entered == [True]


def test_bundle_inventory_uses_typed_root_for_nested_service_reference(tmp_path: Path) -> None:
    slot_name = "host/codex/living_wiki"
    slot = tmp_path / slot_name
    envelope = _slot(slot)
    files = {
        path.relative_to(tmp_path).as_posix(): (path, path.read_bytes())
        for path in slot.rglob("*.json")
    }
    paths = bundle._typed_source_paths(
        envelope,
        manifest_relative=f"{slot_name}/host-event-sequence.json",
        files=files,
    )
    assert paths == {
        f"{slot_name}/sources/{key}.json" for key in executor.TYPED_SOURCE_SLOTS
    } | {f"{slot_name}/sources/service.json"}


def test_bundle_inventory_preserves_generic_refs_without_admitting_payload(tmp_path: Path) -> None:
    reference = _source(tmp_path, "source.json", {"synthetic_inventory_probe": True})
    paths = bundle._typed_source_paths(
        {"kind": "host_event_sequence", "payload": {"source": reference}},
        manifest_relative="manifest.json",
        files={"source.json": (tmp_path / "source.json", (tmp_path / "source.json").read_bytes())},
    )
    # Inventory extraction is not the typed consumer's closed payload admission.
    assert paths == {"source.json"}
