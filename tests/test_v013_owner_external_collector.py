"""Focused tests for exact owner-external collector freezing."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from benchmarks.hosts import owner_external_collector as collector

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="the formal Kernel collector workflow is POSIX/macOS only",
)

ORIGINAL = b"#!/bin/sh\nprintf 'original\\n'\n"
REPLACEMENT = b"#!/bin/sh\nprintf 'replacement\\n'\n"


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    source = tmp_path / "owner-collector"
    source.write_bytes(ORIGINAL)
    source.chmod(0o700)
    return repository, source, private / "frozen-collector", private / "identity.json"


def _freeze(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repository, source, frozen, identity = _paths(tmp_path)
    collector.freeze_collector(
        source,
        frozen,
        identity,
        expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),
        candidate_run_id=101,
        evidence_run_id=202,
        repository=repository,
    )
    return repository, source, frozen, identity


def test_frozen_collector_survives_ambient_path_replacement(tmp_path: Path) -> None:
    repository, source, frozen, identity = _freeze(tmp_path)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(REPLACEMENT)
    replacement.chmod(0o700)
    os.replace(replacement, source)

    completed = subprocess.run([frozen], check=True, capture_output=True)

    assert completed.stdout == b"original\n"
    assert source.read_bytes() == REPLACEMENT
    admitted = collector.validate_frozen_collector(
        frozen,
        identity,
        candidate_run_id=101,
        evidence_run_id=202,
        repository=repository,
    )
    assert admitted["frozen_sha256"] == hashlib.sha256(ORIGINAL).hexdigest()
    assert admitted["formal_authority"] is False
    assert not any("path" in key or "env" in key for key in admitted)


def test_tampered_frozen_collector_fails_closed(tmp_path: Path) -> None:
    repository, _, frozen, identity = _freeze(tmp_path)
    frozen.chmod(0o700)
    frozen.write_bytes(REPLACEMENT)
    frozen.chmod(0o500)

    with pytest.raises(
        collector.OwnerExternalCollectorError,
        match="identity differs from exact frozen bytes",
    ):
        collector.validate_frozen_collector(
            frozen,
            identity,
            candidate_run_id=101,
            evidence_run_id=202,
            repository=repository,
        )


def test_wrong_run_binding_and_identity_tamper_fail_closed(tmp_path: Path) -> None:
    repository, _, frozen, identity = _freeze(tmp_path)
    with pytest.raises(
        collector.OwnerExternalCollectorError,
        match="identity differs from exact frozen bytes or run binding",
    ):
        collector.validate_frozen_collector(
            frozen,
            identity,
            candidate_run_id=101,
            evidence_run_id=203,
            repository=repository,
        )

    value = json.loads(identity.read_text(encoding="utf-8"))
    value["formal_authority"] = True
    value["record_sha256"] = collector.record_sha256(value)
    identity.chmod(0o600)
    identity.write_bytes(collector.canonical_json(value) + b"\n")
    identity.chmod(0o400)
    with pytest.raises(
        collector.OwnerExternalCollectorError,
        match="identity differs from exact frozen bytes or run binding",
    ):
        collector.validate_frozen_collector(
            frozen,
            identity,
            candidate_run_id=101,
            evidence_run_id=202,
            repository=repository,
        )


def test_source_must_be_owner_only_and_credential_free(tmp_path: Path) -> None:
    repository, source, frozen, identity = _paths(tmp_path)
    source.chmod(0o755)
    with pytest.raises(
        collector.OwnerExternalCollectorError,
        match="source must be owner-only",
    ):
        collector.freeze_collector(
            source,
            frozen,
            identity,
            expected_sha256=hashlib.sha256(ORIGINAL).hexdigest(),
            candidate_run_id=101,
            evidence_run_id=202,
            repository=repository,
        )

    source.write_bytes(
        b"#!/bin/sh\nAPI_KEY='abcdefghijklmnopqrstuvwxyz123456'\n"
    )
    source.chmod(0o700)
    with pytest.raises(
        collector.OwnerExternalCollectorError,
        match="contains a credential literal",
    ):
        collector.freeze_collector(
            source,
            frozen,
            identity,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            candidate_run_id=101,
            evidence_run_id=202,
            repository=repository,
        )
