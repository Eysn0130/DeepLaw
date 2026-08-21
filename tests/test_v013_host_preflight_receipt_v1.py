from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.hosts import host_preflight_receipt as receipt
from benchmarks.hosts import run_pass13_codex_continuity_qualification as codex
from benchmarks.hosts import run_pass13_opencode_continuity_qualification as opencode


def _host() -> dict[str, object]:
    return {
        "name": "opencode",
        "version": "1.18.16",
        "sha256": "a" * 64,
    }


def _broker() -> dict[str, object]:
    return {
        "source_kind": "repository_external_launcher",
        "repository_external": True,
        "sha256": "b" * 64,
        "bytes": 12,
        "owner_only_mode": True,
        "expected_sha256": None,
    }


def test_v1_receipt_is_strict_and_has_no_free_text_or_sensitive_surface(
    tmp_path: Path,
) -> None:
    value = receipt.build_receipt(
        host=_host(),
        broker_source=_broker(),
        status="failed",
        stage="broker",
        reason_code="broker_missing",
        check_count=1,
    )
    assert receipt.validate_receipt(value) == value
    path = receipt.write_receipt(tmp_path, value)
    assert path.name == receipt.RECEIPT_FILENAME
    assert json.loads(path.read_text(encoding="utf-8")) == value

    for key, bad_value in (
        ("path", "/private/credential/auth.json"),
        ("argv", ["opencode", "--secret"]),
        ("stdout", "raw host output"),
        ("prompt", "do not retain this"),
        ("reason", "raw exception text"),
    ):
        invalid = {**value, key: bad_value}
        with pytest.raises(receipt.ReceiptValidationError):
            receipt.validate_receipt(invalid)


@pytest.mark.parametrize(
    "message",
    [
        "arbitrary provider text",
        "untrusted server text",
        "unknown provider token text",
        "unknown model diagnostic",
        "untrusted mcp text",
        "arbitrary binary diagnostic",
    ],
)
def test_unknown_failures_close_to_internal_error(message: str) -> None:
    assert receipt.reason_code_for_exception(RuntimeError(message)) == "preflight_internal_error"
    assert receipt.stage_for_reason("preflight_internal_error") == "preflight"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Codex existing login was not confirmed", "auth_unavailable"),
        ("selected model was absent from model/list", "model_unavailable"),
        ("MCP status exposed an unexpected tool", "mcp_not_advertised"),
        ("Codex App Server failed to start", "transport_start_failed"),
        ("actual OpenCode provider token usage is missing", "usage_receipt_missing"),
    ],
)
def test_known_failure_categories_are_closed(message: str, expected: str) -> None:
    assert receipt.reason_code_for_exception(RuntimeError(message)) == expected


def test_broker_hash_is_exact_bytes_and_survives_source_deletion(tmp_path: Path) -> None:
    broker = tmp_path / "owner-broker"
    broker.write_bytes(b"owner-only broker bytes")
    broker.chmod(0o700)
    repository = tmp_path / "repository"
    repository.mkdir()
    observed = receipt.inspect_broker_source(broker, repository=repository)
    assert observed["sha256"] == receipt.sha256_file(broker)
    assert observed["bytes"] == len(b"owner-only broker bytes")
    assert observed["repository_external"] is True
    value = receipt.build_receipt(
        host=_host(),
        broker_source=observed,
        status="failed",
        stage="broker",
        reason_code="broker_hash_mismatch",
        check_count=1,
    )
    broker.unlink()
    assert receipt.validate_receipt(value)["broker_source"]["sha256"] == observed["sha256"]


def test_broker_change_during_exact_byte_read_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broker = tmp_path / "owner-broker"
    broker.write_bytes(b"owner-only broker bytes")
    broker.chmod(0o700)
    repository = tmp_path / "repository"
    repository.mkdir()

    original = receipt._sha256_file_with_bytes

    def mutate_after_read(path: Path) -> tuple[str, int]:
        observed_hash, observed_bytes = original(path)
        path.write_bytes(path.read_bytes() + b" changed")
        return observed_hash, observed_bytes

    monkeypatch.setattr(receipt, "_sha256_file_with_bytes", mutate_after_read)
    observed = receipt.inspect_broker_source(broker, repository=repository)
    assert observed["failure_reason_code"] == "broker_not_regular"
    assert observed["sha256"] is None
    assert observed["bytes"] == 0


@pytest.mark.parametrize(
    ("path_kind", "expected"),
    [("missing", "broker_missing"), ("directory", "broker_not_regular")],
)
def test_broker_missing_and_not_regular_are_typed(
    tmp_path: Path, path_kind: str, expected: str
) -> None:
    path = tmp_path / "broker"
    if path_kind == "directory":
        path.mkdir()
    observed = receipt.inspect_broker_source(path, repository=tmp_path / "repo")
    assert observed["failure_reason_code"] == expected


def test_expected_broker_hash_mismatch_is_typed(tmp_path: Path) -> None:
    broker = tmp_path / "broker"
    broker.write_bytes(b"broker")
    broker.chmod(0o700)
    observed = receipt.inspect_broker_source(
        broker,
        repository=tmp_path / "repo",
        expected_sha256="f" * 64,
    )
    assert observed["failure_reason_code"] == "broker_hash_mismatch"


def test_opencode_broker_validation_has_repository_external_containment(
    tmp_path: Path,
) -> None:
    host = tmp_path / "opencode"
    host.write_bytes(b"host")
    host.chmod(0o700)
    repository = tmp_path / "repository"
    repository.mkdir()
    inside = repository / "owner-broker"
    inside.write_bytes(b"broker")
    inside.chmod(0o700)
    with pytest.raises(opencode.QualificationError, match="outside the repository"):
        opencode._validate_owner_broker_launcher(
            inside,
            host_binary=host,
            repository=repository,
        )


def test_opencode_broker_missing_not_regular_and_hash_mismatch_are_closed(
    tmp_path: Path,
) -> None:
    host = tmp_path / "opencode"
    host.write_bytes(b"host")
    host.chmod(0o700)
    missing = tmp_path / "missing-broker"
    with pytest.raises(opencode.QualificationError) as missing_error:
        opencode._validate_owner_broker_launcher(missing, host_binary=host)
    assert receipt.reason_code_for_exception(missing_error.value) == "broker_missing"

    directory = tmp_path / "directory-broker"
    directory.mkdir()
    with pytest.raises(opencode.QualificationError) as regular_error:
        opencode._validate_owner_broker_launcher(directory, host_binary=host)
    assert receipt.reason_code_for_exception(regular_error.value) == "broker_not_regular"

    broker = tmp_path / "broker"
    broker.write_bytes(b"broker")
    broker.chmod(0o700)
    with pytest.raises(opencode.QualificationError) as hash_error:
        opencode._validate_owner_broker_launcher(
            broker,
            host_binary=host,
            expected_broker_sha256="f" * 64,
        )
    assert receipt.reason_code_for_exception(hash_error.value) == "broker_hash_mismatch"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Codex existing login was not confirmed", "auth_unavailable"),
        ("selected model was absent from model/list", "model_unavailable"),
        ("MCP inventory was empty", "mcp_not_advertised"),
        ("Codex MCP inventory failed to start", "transport_start_failed"),
    ],
)
def test_codex_preflight_categories_are_typed(message: str, expected: str) -> None:
    assert receipt.reason_code_for_exception(codex.QualificationFailure(message)) == expected


def test_opencode_availability_usage_missing_is_typed() -> None:
    with pytest.raises(opencode.QualificationError, match="usage receipt") as failure:
        opencode.parse_availability_result(
            stdout=b'{"type":"text","part":{"text":"available"}}\n',
            returncode=0,
            elapsed_ms=1,
        )
    assert receipt.reason_code_for_exception(failure.value) == "usage_receipt_missing"
