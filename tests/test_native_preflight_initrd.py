"""Bounded, deterministic tests for the zero-model initrd assembler."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from benchmarks.hosts import build_native_preflight_initrd as builder


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _manifest_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _newc_entries(value: bytes) -> dict[str, tuple[int, int, bytes]]:
    entries: dict[str, tuple[int, int, bytes]] = {}
    offset = 0
    while True:
        header = value[offset : offset + 110]
        assert header[:6] == b"070701"
        namesize = int(header[94:102], 16)
        filesize = int(header[54:62], 16)
        mode = int(header[14:22], 16)
        mtime = int(header[46:54], 16)
        offset += 110
        name_bytes = value[offset : offset + namesize]
        assert name_bytes.endswith(b"\0")
        name = name_bytes[:-1].decode("utf-8")
        offset += namesize
        offset += (-offset) % 4
        content = value[offset : offset + filesize]
        assert len(content) == filesize
        offset += filesize
        offset += (-offset) % 4
        if name == "TRAILER!!!":
            assert filesize == 0
            assert offset == len(value)
            break
        entries[name] = (mode, mtime, content)
    return entries


@pytest.fixture
def build_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("native input freeze requires POSIX O_NOFOLLOW")
    repository = tmp_path / "repository"
    (repository / "benchmarks/hosts/native_guest").mkdir(parents=True)
    module_path = repository / "benchmarks/hosts/example.py"
    module_path.write_bytes(b"VALUE = 'example'\n")
    init_path = repository / "benchmarks/hosts/native_guest/init.sh"
    init_path.write_bytes(b"#!/bin/sh\nexit 0\n")
    monkeypatch.chdir(repository)
    monkeypatch.setattr(
        builder,
        "MODULE_REGISTRY",
        {
            "benchmarks/hosts/example.py": "opt/benchmarks/hosts/example.py",
            "benchmarks/hosts/native_guest/init.sh": "init",
        },
    )

    base = tmp_path / "base-initrd"
    base.write_bytes(b"BASE-INITRD\0")
    opencode = tmp_path / "opencode"
    opencode.write_bytes(b"#!/bin/sh\necho opencode\n")
    wheel_a = tmp_path / "wheel-a.whl"
    wheel_a.write_bytes(b"wheel-a")
    wheel_b = tmp_path / "wheel-b.whl"
    wheel_b.write_bytes(b"wheel-b")
    value: dict[str, Any] = {
        "schema": builder.SCHEMA,
        "purpose": builder.PURPOSE,
        "run_id": "run-test-1",
        "candidate_id": "candidate-test-1",
        "base_initrd": {"path": str(base), "sha256": _sha256(base.read_bytes())},
        "opencode": {"path": str(opencode), "sha256": _sha256(opencode.read_bytes())},
        "wheels": [
            {"path": str(wheel_b), "sha256": _sha256(wheel_b.read_bytes())},
            {"path": str(wheel_a), "sha256": _sha256(wheel_a.read_bytes())},
        ],
        "modules": {
            "benchmarks/hosts/native_guest/init.sh": _sha256(init_path.read_bytes()),
            "benchmarks/hosts/example.py": _sha256(module_path.read_bytes()),
        },
    }
    manifest = tmp_path / "manifest.json"
    manifest.write_bytes(_manifest_bytes(value))
    return {
        "value": value,
        "manifest": manifest,
        "manifest_sha256": _sha256(manifest.read_bytes()),
        "base": base,
        "opencode": opencode,
        "wheel_a": wheel_a,
        "wheel_b": wheel_b,
        "module": module_path,
        "init": init_path,
        "tmp": tmp_path,
    }


def _write_manifest(inputs: dict[str, Any], value: dict[str, Any]) -> tuple[Path, str]:
    manifest = inputs["tmp"] / "alternate-manifest.json"
    raw = _manifest_bytes(value)
    manifest.write_bytes(raw)
    return manifest, _sha256(raw)


def test_registry_is_the_closed_guest_module_set() -> None:
    assert set(builder.MODULE_REGISTRY) == {
        "benchmarks/hosts/linux_guest_observer.py",
        "benchmarks/hosts/linux_role_launcher.py",
        "benchmarks/hosts/linux_guest_slot_control.py",
        "benchmarks/hosts/native_slot_frames.py",
        "benchmarks/hosts/linux_proc_connector.py",
        "benchmarks/hosts/linux_process_tree_metadata.py",
        "benchmarks/hosts/linux_process_observer.py",
        "benchmarks/hosts/linux_http_route_observer.py",
        "benchmarks/hosts/linux_audit_syscall_metadata.py",
        "benchmarks/hosts/linux_role_boundary_probe.py",
        "benchmarks/hosts/native_fork_observation.py",
        "benchmarks/hosts/v013_native_event_adapter.py",
        "benchmarks/hosts/host_process_receipt_v2.py",
        "contracts/host-process-receipt.v2.schema.json",
        "adapters/opencode/plugins/deeplaw-native.ts",
        "benchmarks/hosts/linux_mcp_socket_transport.py",
        "benchmarks/hosts/maintenance_task_mcp.py",
        "benchmarks/hosts/maintenance_task_cases.py",
        "benchmarks/hosts/native_guest/bootstrap.py",
        "benchmarks/hosts/native_guest/boundary_gate.py",
        "benchmarks/hosts/native_guest/opencode_entry.py",
        "benchmarks/hosts/native_guest/mcp_entry.py",
        "benchmarks/hosts/native_guest/mcp_client.py",
        "benchmarks/hosts/native_guest/mcp_relay.py",
        "benchmarks/hosts/native_guest/init.sh",
    }


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="native input freeze requires POSIX")
def test_production_registry_stages_current_repo_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repository)
    base = tmp_path / "base-initrd"
    base.write_bytes(b"BASE")
    opencode = tmp_path / "opencode"
    opencode.write_bytes(b"OPENCODE")
    wheel = tmp_path / "runtime.whl"
    wheel.write_bytes(b"WHEEL")

    def digest(path: Path) -> str:
        return _sha256(path.read_bytes())

    value: dict[str, Any] = {
        "schema": builder.SCHEMA,
        "purpose": builder.PURPOSE,
        "run_id": "production-registry-run",
        "candidate_id": "production-registry-candidate",
        "base_initrd": {"path": str(base), "sha256": digest(base)},
        "opencode": {"path": str(opencode), "sha256": digest(opencode)},
        "wheels": [{"path": str(wheel), "sha256": digest(wheel)}],
        "modules": {key: digest(repository / key) for key in builder.MODULE_REGISTRY},
    }
    manifest = tmp_path / "manifest.json"
    raw = _manifest_bytes(value)
    manifest.write_bytes(raw)
    destination = tmp_path / "production"
    receipt = builder.build_initrd(manifest, _sha256(raw), destination)
    output = (destination / "initrd").read_bytes()
    archive = gzip.decompress(output[len(base.read_bytes()) :])
    entries = _newc_entries(archive)
    assert set(builder.MODULE_REGISTRY.values()) <= set(entries)
    assert entries["opt/opencode"][2] == opencode.read_bytes()
    assert entries["opt/python-artifacts/runtime.whl"][2] == wheel.read_bytes()
    assert receipt["formal_admission"] is False


def test_fork_route_purpose_is_bound_into_guest_and_build_receipt(build_inputs):
    value = {**build_inputs["value"], "purpose": builder.FORK_PURPOSE}
    manifest, digest = _write_manifest(build_inputs, value)
    destination = build_inputs["tmp"] / "fork-only"
    receipt = builder.build_initrd(manifest, digest, destination)
    raw = (destination / "initrd").read_bytes()
    entries = _newc_entries(gzip.decompress(raw[len(build_inputs["base"].read_bytes()):]))
    assert receipt["purpose"] == "zero_model_fork_preflight"
    assert json.loads(entries["opt/owner-input.json"][2])["purpose"] == receipt["purpose"]
    assert receipt["formal_admission"] is False


def test_build_is_reproducible_and_archive_is_path_free(build_inputs: dict[str, Any]) -> None:
    first_destination = build_inputs["tmp"] / "first"
    second_destination = build_inputs["tmp"] / "second"
    first = builder.build_initrd(
        build_inputs["manifest"], build_inputs["manifest_sha256"], first_destination
    )
    second = builder.build_initrd(
        build_inputs["manifest"], build_inputs["manifest_sha256"], second_destination
    )

    assert first == second
    first_initrd = (first_destination / "initrd").read_bytes()
    second_initrd = (second_destination / "initrd").read_bytes()
    assert first_initrd == second_initrd
    assert first_initrd.startswith(build_inputs["base"].read_bytes())
    archive = gzip.decompress(first_initrd[len(build_inputs["base"].read_bytes()) :])
    entries = _newc_entries(archive)
    assert entries["."][0] & 0o777 == 0o755
    assert entries["opt/opencode"][0] & 0o777 == 0o755
    assert entries["init"][0] & 0o777 == 0o755
    assert entries["opt/benchmarks/hosts/example.py"][0] & 0o777 == 0o644
    assert entries["opt/benchmarks/__init__.py"][2] == b""
    assert entries["opt/benchmarks/hosts/__init__.py"][2] == b""
    assert entries["opt/mcp-modules/benchmarks/__init__.py"][2] == b""
    assert entries["opt/mcp-modules/benchmarks/hosts/__init__.py"][2] == b""
    assert entries["opt/opencode"][2] == build_inputs["opencode"].read_bytes()
    assert json.loads(entries["opt/owner-input.json"][2]) == {
        "candidate_id": "candidate-test-1",
        "purpose": "zero_model_preflight",
        "run_id": "run-test-1",
    }
    assert entries["opt/owner-input.json"][2] == _manifest_bytes(
        {
            "candidate_id": "candidate-test-1",
            "purpose": "zero_model_preflight",
            "run_id": "run-test-1",
        }
    )
    assert json.loads(entries["opt/wheel-inputs.json"][2]) == [
        {"name": "wheel-a.whl", "sha256": _sha256(build_inputs["wheel_a"].read_bytes())},
        {"name": "wheel-b.whl", "sha256": _sha256(build_inputs["wheel_b"].read_bytes())},
    ]
    assert all(mtime == 0 for _, mtime, _ in entries.values())
    receipt_text = json.dumps(first, sort_keys=True)
    assert str(build_inputs["tmp"]) not in receipt_text
    assert first["formal_admission"] is False
    assert first["initrd_sha256"] == _sha256(first_initrd)
    assert first["initrd_bytes"] == len(first_initrd)
    assert first["record_sha256"] == _sha256(
        _manifest_bytes({key: value for key, value in first.items() if key != "record_sha256"})
    )
    assert {item["logical_name"] for item in first["files"]} >= {
        "base_initrd",
        "opencode",
        "wheel-inputs.json",
    }


def test_manifest_digest_and_closed_keys_fail_closed(build_inputs: dict[str, Any]) -> None:
    with pytest.raises(builder.NativeInitrdBuildError, match="manifest_digest_mismatch"):
        builder.build_initrd(
            build_inputs["manifest"], "0" * 64, build_inputs["tmp"] / "bad-digest"
        )

    unknown = {**build_inputs["value"], "unexpected": True}
    manifest, digest = _write_manifest(build_inputs, unknown)
    with pytest.raises(builder.NativeInitrdBuildError, match="manifest_keys_invalid"):
        builder.build_initrd(manifest, digest, build_inputs["tmp"] / "unknown")

    duplicate = (
        b'{"schema":"deeplaw.native-preflight-build/v1",'
        b'"schema":"deeplaw.native-preflight-build/v1"}'
    )
    manifest.write_bytes(duplicate)
    with pytest.raises(builder.NativeInitrdBuildError, match="manifest_duplicate_key"):
        builder.build_initrd(manifest, _sha256(duplicate), build_inputs["tmp"] / "duplicate")

    oversized = b"{" + b'"padding":"' + b"x" * builder.MAX_MANIFEST_BYTES + b'"}'
    manifest.write_bytes(oversized)
    with pytest.raises(builder.NativeInitrdBuildError, match="manifest_too_large"):
        builder.build_initrd(manifest, _sha256(oversized), build_inputs["tmp"] / "oversized")

    too_many_wheels = {
        **build_inputs["value"],
        "wheels": [
            {
                "path": str(build_inputs["wheel_a"]),
                "sha256": _sha256(build_inputs["wheel_a"].read_bytes()),
            }
            for _ in range(builder.MAX_WHEELS + 1)
        ],
    }
    manifest, digest = _write_manifest(build_inputs, too_many_wheels)
    with pytest.raises(builder.NativeInitrdBuildError, match="wheels_invalid"):
        builder.build_initrd(manifest, digest, build_inputs["tmp"] / "too-many-wheels")

    empty_wheels = {**build_inputs["value"], "wheels": []}
    manifest, digest = _write_manifest(build_inputs, empty_wheels)
    with pytest.raises(builder.NativeInitrdBuildError, match="wheels_invalid"):
        builder.build_initrd(manifest, digest, build_inputs["tmp"] / "empty-wheels")


@pytest.mark.parametrize("field", ["opencode", "base_initrd"])
def test_symlink_input_is_rejected_without_creating_destination(
    build_inputs: dict[str, Any], field: str
) -> None:
    source = build_inputs["base" if field == "base_initrd" else field]
    link = build_inputs["tmp"] / f"{field}-link"
    link.symlink_to(source)
    value = {
        **build_inputs["value"],
        field: {"path": str(link), "sha256": _sha256(source.read_bytes())},
    }
    manifest, digest = _write_manifest(build_inputs, value)
    destination = build_inputs["tmp"] / f"{field}-symlink-destination"
    with pytest.raises(builder.NativeInitrdBuildError, match="input_file_invalid"):
        builder.build_initrd(manifest, digest, destination)
    assert not destination.exists()


@pytest.mark.skipif(os.name != "posix", reason="FIFO input is a POSIX boundary")
def test_fifo_and_nonregular_input_are_rejected_without_waiting(
    build_inputs: dict[str, Any]
) -> None:
    fifo = build_inputs["tmp"] / "wheel-fifo.whl"
    os.mkfifo(fifo)
    value = {**build_inputs["value"], "wheels": [{"path": str(fifo), "sha256": "0" * 64}]}
    manifest, digest = _write_manifest(build_inputs, value)
    destination = build_inputs["tmp"] / "fifo-destination"
    with pytest.raises(builder.NativeInitrdBuildError, match="input_file_invalid"):
        builder.build_initrd(manifest, digest, destination)
    assert not destination.exists()

    directory = build_inputs["tmp"] / "wheel-directory.whl"
    directory.mkdir()
    value["wheels"] = [{"path": str(directory), "sha256": "0" * 64}]
    manifest, digest = _write_manifest(build_inputs, value)
    with pytest.raises(builder.NativeInitrdBuildError, match="input_file_invalid"):
        builder.build_initrd(manifest, digest, build_inputs["tmp"] / "directory-destination")


def test_wheel_basename_collision_and_destination_reuse_fail(build_inputs: dict[str, Any]) -> None:
    other = build_inputs["tmp"] / "other"
    other.mkdir()
    duplicate = other / build_inputs["wheel_a"].name
    duplicate.write_bytes(b"different")
    value = {
        **build_inputs["value"],
        "wheels": [
            {
                "path": str(build_inputs["wheel_a"]),
                "sha256": _sha256(build_inputs["wheel_a"].read_bytes()),
            },
            {"path": str(duplicate), "sha256": _sha256(duplicate.read_bytes())},
        ],
    }
    manifest, digest = _write_manifest(build_inputs, value)
    with pytest.raises(builder.NativeInitrdBuildError, match="wheel_basename_duplicate"):
        builder.build_initrd(manifest, digest, build_inputs["tmp"] / "duplicate-wheel")

    destination = build_inputs["tmp"] / "existing"
    destination.mkdir()
    with pytest.raises(builder.NativeInitrdBuildError, match="destination_exists"):
        builder.build_initrd(build_inputs["manifest"], build_inputs["manifest_sha256"], destination)


def test_file_and_total_input_bounds_are_enforced(
    build_inputs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(builder, "MAX_FILE_BYTES", 2)
    with pytest.raises(builder.NativeInitrdBuildError, match="input_file_invalid"):
        builder.build_initrd(
            build_inputs["manifest"],
            build_inputs["manifest_sha256"],
            build_inputs["tmp"] / "file-bound",
        )

    monkeypatch.setattr(builder, "MAX_FILE_BYTES", builder.MAX_FILE_BYTES * 1000)
    monkeypatch.setattr(builder, "MAX_TOTAL_INPUT_BYTES", 1)
    with pytest.raises(builder.NativeInitrdBuildError, match="total_input_size_exceeded"):
        builder.build_initrd(
            build_inputs["manifest"],
            build_inputs["manifest_sha256"],
            build_inputs["tmp"] / "total-bound",
        )
