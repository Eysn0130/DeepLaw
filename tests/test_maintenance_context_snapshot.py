"""Original public context and fixture bytes can be revalidated without mutation."""

import os
import shutil
from copy import deepcopy

import pytest

from benchmarks.hosts import maintenance_context_snapshot as consumer
from benchmarks.hosts import run_maintenance_host_tasks as runner
from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

pytestmark = pytest.mark.skipif(os.name != "posix", reason="context snapshot requires POSIX")


@pytest.fixture(scope="module")
def public_context_snapshot(tmp_path_factory):
    root = tmp_path_factory.mktemp("public-context") / "run"
    root.mkdir(mode=0o700)
    vault = root / "source"
    setup = runner.prepare_context_vault(vault, "governed_maintenance")
    context = runner.capture_context(vault, "governed_maintenance", "source_update")
    receipt = runner._persist_context_snapshot(
        root, vault, configuration="governed_maintenance", scenario="source_update",
        context=context, run_id="public-run", candidate_id="public-candidate",
        expected_vault_id=setup["vault_id"],
    )
    return root, receipt


def _validate(root, receipt, **kwargs):
    return consumer.validate_context_snapshot(
        root, receipt, **{"configuration": "governed_maintenance", "scenario": "source_update",
                         "run_id": "public-run", "candidate_id": "public-candidate", **kwargs},
    )


def _state(root):
    return {path.relative_to(root).as_posix(): (
        path.lstat().st_mode, sha256_bytes(path.read_bytes()) if path.is_file() else None,
    ) for path in (root, *root.rglob("*"))}


def _rehash(receipt):
    receipt["binding_sha256"] = runner.digest({
        key: value for key, value in receipt.items() if key != "binding_sha256"
    })


def _copy(public_context_snapshot, tmp_path):
    original, receipt = public_context_snapshot
    root = tmp_path / "run"
    shutil.copytree(original, root)
    return root, deepcopy(receipt)


def _replace_json(root, receipt, field, value):
    raw = canonical_json(value).encode()
    (root / receipt[field]["path"]).write_bytes(raw)
    receipt[field].update({"sha256": sha256_bytes(raw), "size": len(raw)})
    _rehash(receipt)


def test_actual_public_context_reopens_without_changing_bytes_modes_or_receipt(
    public_context_snapshot,
):
    root, receipt = public_context_snapshot
    before, original = _state(root), deepcopy(receipt)
    result = _validate(root, receipt)
    assert result["formal_admission"] is False
    assert result["verification"]["valid"] is True
    assert result["context"] == strict_json_loads((root / receipt["context"]["path"]).read_bytes())
    assert result["snapshot_manifest"] == strict_json_loads(
        (root / receipt["snapshot_manifest"]["path"]).read_bytes()
    )
    expected_paths = {receipt[field]["path"] for field in ("context", "snapshot_manifest")}
    expected_paths.update(receipt["vault_path"] + "/" + item["path"]
                          for item in result["snapshot_manifest"]["inventory"])
    assert [item["path"] for item in result["files"]] == sorted(expected_paths)
    for reference in result["files"]:
        raw = (root / reference["path"]).read_bytes()
        assert reference == {
            "path": reference["path"], "sha256": sha256_bytes(raw), "size": len(raw),
        }
    assert result["context"]["capsule"]["task_binding"] is None
    assert result["vault_path"] == receipt["vault_path"]
    assert str(root) not in canonical_json(result)
    assert _state(root) == before and receipt == original


@pytest.mark.parametrize("field,value", [
    ("configuration", "no_memory"), ("scenario", "wrong_experience"),
    ("run_id", "other-run"), ("candidate_id", "other-candidate"),
])
def test_context_snapshot_rejects_cross_binding(public_context_snapshot, field, value):
    root, receipt = public_context_snapshot
    with pytest.raises(ValueError):
        _validate(root, receipt, **{field: value})


@pytest.mark.parametrize("tamper", [
    "extra", "missing", "schema", "phase", "input", "task_input", "context_id",
    "provider_digest", "task_binding", "owner_extra", "vault_id", "legacy_head",
    "sequence", "boolean_revision", "inventory_digest", "receipt_digest", "reference_extra",
    "reference_boolean_size", "reference_hash", "parent_path", "absolute_path", "cross_slot_path",
])
def test_context_receipt_is_closed_even_when_its_digest_is_recomputed(
    public_context_snapshot, tamper,
):
    root, original = public_context_snapshot
    receipt = deepcopy(original)
    changes = {
        "schema": ("schema_version", "other/v1"), "phase": ("capture_phase", "post_outcome"),
        "input": ("public_input_sha256", "0" * 64), "task_input": ("task_input_sha256", "0" * 64),
        "context_id": ("context_id", "capsule_" + "0" * 24),
        "provider_digest": ("provider_capsule_sha256", "0" * 64),
        "vault_id": ("vault_id", "vault_" + "0" * 24),
        "legacy_head": ("legacy_audit_head", "0" * 64), "sequence": ("autonomous_sequence", 0),
        "boolean_revision": ("legacy_revision", True),
        "inventory_digest": ("inventory_sha256", "0" * 64),
    }
    if tamper in changes:
        key, value = changes[tamper]
        receipt[key] = value
    elif tamper == "extra":
        receipt["private_key"] = "synthetic-marker"
    elif tamper == "missing":
        receipt.pop("context_id")
    elif tamper == "task_binding":
        receipt["task_binding"] = runner.build_task_context_binding("0" * 64, "1" * 64)
    elif tamper == "owner_extra":
        receipt["owner_binding"]["extra"] = "synthetic-marker"
    elif tamper == "reference_extra":
        receipt["context"]["extra"] = "synthetic-marker"
    elif tamper == "reference_boolean_size":
        receipt["context"]["size"] = True
    elif tamper == "reference_hash":
        receipt["context"]["sha256"] = "0" * 64
    elif tamper == "parent_path":
        receipt["context"]["path"] = "../context.json"
    elif tamper == "absolute_path":
        receipt["context"]["path"] = str(root / receipt["context"]["path"])
    elif tamper == "cross_slot_path":
        receipt["context"]["path"] = receipt["context"]["path"].replace(
            "source_update", "wrong_experience",
        )
    _rehash(receipt)
    if tamper == "receipt_digest":
        receipt["binding_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        _validate(root, receipt)


@pytest.mark.parametrize("tamper", [
    "task", "provider", "capsule_digest", "verification_extra", "input_head",
])
def test_original_capsule_is_reverified_instead_of_trusting_declared_validity(
    public_context_snapshot, tmp_path, tamper,
):
    root, receipt = _copy(public_context_snapshot, tmp_path)
    context = strict_json_loads((root / receipt["context"]["path"]).read_bytes())
    assert context["verification"]["valid"] is True
    if tamper == "task":
        context["capsule"]["task"] = "other public task"
    elif tamper == "provider":
        context["provider_capsule"] = {}
    elif tamper == "capsule_digest":
        context["capsule"]["capsule_digest"] = "0" * 64
    elif tamper == "verification_extra":
        context["verification"]["private_key"] = "synthetic-marker"
    else:
        context["capsule"]["query_plan"]["input_legacy_audit_head"] = "0" * 64
    _replace_json(root, receipt, "context", context)
    with pytest.raises(ValueError):
        _validate(root, receipt)


def test_declared_validity_cannot_replace_current_verifier(public_context_snapshot, monkeypatch):
    root, receipt = public_context_snapshot
    before = _state(root)
    calls = []

    def verify(capsule, *, vault):
        calls.append(capsule["capsule_id"])
        raise ValueError("synthetic private payload at " + str(root))

    monkeypatch.setattr(consumer, "verify_capsule", verify)
    with pytest.raises(ValueError) as error:
        _validate(root, receipt)
    assert calls == [receipt["context_id"]]
    assert str(error.value) == "maintenance context snapshot validation failed"
    assert _state(root) == before


@pytest.mark.parametrize("tamper", [
    "context_bytes", "manifest_extra", "slot_extra", "vault_extra", "missing_context",
])
def test_context_snapshot_rejects_drift_and_extra_material(
    public_context_snapshot, tmp_path, tamper,
):
    root, receipt = _copy(public_context_snapshot, tmp_path)
    if tamper == "context_bytes":
        path = root / receipt["context"]["path"]
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "missing_context":
        (root / receipt["context"]["path"]).unlink()
    elif tamper == "manifest_extra":
        manifest = strict_json_loads((root / receipt["snapshot_manifest"]["path"]).read_bytes())
        manifest["extra"] = "synthetic-marker"
        _replace_json(root, receipt, "snapshot_manifest", manifest)
    else:
        directory = root / receipt["vault_path"]
        if tamper == "slot_extra":
            directory = directory.parent
        path = directory / "unexpected-material"
        path.write_text("synthetic-marker")
        path.chmod(0o600)
    before = _state(root)
    with pytest.raises(ValueError):
        _validate(root, receipt)
    assert _state(root) == before


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "unsafe_mode", "unsafe_parent",
                                  "unsafe_root", "oversize", "changed_during_read"])
def test_context_snapshot_rejects_unsafe_files_without_removing_them(
    public_context_snapshot, tmp_path, monkeypatch, kind,
):
    root, receipt = _copy(public_context_snapshot, tmp_path)
    path = root / receipt["context"]["path"]
    if kind in {"symlink", "hardlink", "fifo"}:
        saved = root / "saved-context"
        path.rename(saved)
        if kind == "symlink":
            path.symlink_to(saved)
        elif kind == "hardlink":
            os.link(saved, path)
        else:
            os.mkfifo(path, 0o600)
    elif kind == "unsafe_mode":
        path.chmod(0o644)
    elif kind == "unsafe_parent":
        path.parent.chmod(0o755)
    elif kind == "unsafe_root":
        root.chmod(0o755)
    elif kind == "oversize":
        path.write_bytes(b" " * (384 * 1024 + 1))
    else:
        original_read = consumer.os.read
        changed = False

        def read(fd, size):
            nonlocal changed
            raw = original_read(fd, size)
            if not changed and b'"context_provenance"' in raw:
                changed = True
                path.write_bytes(raw + b" ")
            return raw

        monkeypatch.setattr(consumer.os, "read", read)
    with pytest.raises(ValueError) as error:
        _validate(root, receipt)
    assert path.lstat()
    assert str(root) not in str(error.value) and "synthetic-marker" not in str(error.value)
