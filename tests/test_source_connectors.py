from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

import deeplaw.knowledge_jobs as knowledge_jobs
import deeplaw.source_connectors as connectors
from deeplaw.knowledge_jobs import (
    create_snapshot_ingest_job,
    load_ingest_job,
    run_ingest_job,
)
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes, sha256_file


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="connector tests", scope="project")
    return root


def _git(repository: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("git executable is unavailable")
    result = subprocess.run(
        [executable, "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "DeepLaw Tests")
    _git(repository, "config", "user.email", "tests@deeplaw.invalid")
    (repository / "guide.md").write_text(
        "# Committed guide\nVerify the exact source revision.\n",
        encoding="utf-8",
    )
    (repository / "check.py").write_text(
        "def exact_revision(value: str) -> bool:\n    return bool(value)\n",
        encoding="utf-8",
    )
    (repository / "ignored.bin").write_bytes(b"not a supported source")
    _git(repository, "add", "guide.md", "check.py", "ignored.bin")
    _git(repository, "commit", "--quiet", "-m", "source fixture")
    revision = _git(repository, "rev-parse", "HEAD")
    return repository, revision


@pytest.mark.parametrize(
    "value",
    (
        "http://example.com/source.md",
        "https://127.0.0.1/source.md",
        "https://8.8.8.8/source.md",
        "https://user@example.com/source.md",
        "https://example.com:444/source.md",
        "https://example.com/source.md?token=secret",
        "https://example.com/source.md#fragment",
        "https://example.com/%5cadmin.md",
        "https://example.com./source.md",
    ),
)
def test_https_url_contract_rejects_unsafe_or_ambiguous_locators(value: str) -> None:
    with pytest.raises(ValueError):
        connectors.canonical_https_url(value)


def test_https_preflight_is_network_free_and_normalizes_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolution(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("preflight must not resolve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", fail_resolution)
    expected = "a" * 64
    plan = connectors.plan_https_source(
        "HTTPS://Example.COM:443/source.md",
        expected_sha256=expected.upper(),
        maximum_bytes=1024,
        timeout_seconds=5,
    )

    assert plan["canonical_requested_url"] == "https://example.com/source.md"
    assert plan["expected_sha256"] == expected
    assert plan["network_performed"] is False
    assert plan["constraints"]["maximum_bytes"] == 1024


def test_https_resolution_fails_closed_for_mixed_public_and_private_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
    ]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: rows)

    with pytest.raises(RuntimeError, match="non-public"):
        connectors._resolve_public_addresses("example.com", 443)


def test_https_resolution_has_a_wall_clock_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def stalled_resolution(*_args: object, **_kwargs: object) -> list[object]:
        release.wait(1)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", stalled_resolution)
    try:
        with pytest.raises(RuntimeError, match="timed out"):
            connectors._resolve_public_addresses(
                "example.com",
                443,
                timeout_seconds=0.01,
            )
    finally:
        release.set()


def test_https_request_pins_the_resolved_endpoint_and_verifies_the_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: dict[str, Any] = {}

    class FakeSocket:
        def settimeout(self, value: float) -> None:
            events["socket_timeout"] = value

        def connect(self, endpoint: tuple[str, int]) -> None:
            events["endpoint"] = endpoint

        def close(self) -> None:
            events["raw_closed"] = True

    class FakeTlsSocket:
        def settimeout(self, value: float) -> None:
            events["tls_timeout"] = value

    class FakeContext:
        def wrap_socket(self, raw: FakeSocket, *, server_hostname: str) -> FakeTlsSocket:
            assert isinstance(raw, FakeSocket)
            events["server_hostname"] = server_hostname
            return FakeTlsSocket()

    class FakeResponse:
        status = 200

        def __init__(self) -> None:
            self.body = bytearray(b"verified response")

        def getheaders(self) -> list[tuple[str, str]]:
            return [
                ("Content-Type", "text/plain"),
                ("Content-Length", str(len(self.body))),
            ]

        def read(self, maximum: int) -> bytes:
            chunk = bytes(self.body[:maximum])
            del self.body[:maximum]
            return chunk

    class FakeConnection:
        def __init__(self, host: str, **_kwargs: object) -> None:
            events["connection_host"] = host
            self.sock: FakeTlsSocket | None = None

        def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
            events["request"] = (method, target, headers)

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            events["connection_closed"] = True

    monkeypatch.setattr(
        connectors,
        "_resolve_public_addresses",
        lambda *_args, **_kwargs: ((socket.AF_INET, "93.184.216.34"),),
    )
    monkeypatch.setattr(socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(connectors.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(connectors.http.client, "HTTPSConnection", FakeConnection)

    status, headers, content, endpoint = connectors._request_https_once(
        "https://example.com/source.txt",
        maximum_bytes=1024,
        timeout_seconds=7,
    )

    assert status == 200
    assert headers["content-type"] == "text/plain"
    assert content == b"verified response"
    assert endpoint == "93.184.216.34"
    assert events["endpoint"] == ("93.184.216.34", 443)
    assert events["server_hostname"] == "example.com"
    assert events["request"][0:2] == ("GET", "/source.txt")
    assert events["request"][2]["Accept-Encoding"] == "identity"
    assert events["connection_closed"] is True


def test_https_request_enforces_one_wall_clock_deadline_while_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]

    class FakeSocket:
        def settimeout(self, _value: float) -> None:
            return None

        def connect(self, _endpoint: tuple[str, int]) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeTlsSocket(FakeSocket):
        pass

    class FakeContext:
        def wrap_socket(self, _raw: FakeSocket, *, server_hostname: str) -> FakeTlsSocket:
            assert server_hostname == "example.com"
            return FakeTlsSocket()

    class FakeResponse:
        status = 200

        def getheaders(self) -> list[tuple[str, str]]:
            return [("Content-Type", "text/plain")]

        def read(self, _maximum: int) -> bytes:
            clock[0] = 2.0
            return b"a"

    class FakeConnection:
        def __init__(self, _host: str, **_kwargs: object) -> None:
            self.sock: FakeTlsSocket | None = None

        def request(self, *_args: object, **_kwargs: object) -> None:
            return None

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(connectors.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        connectors,
        "_resolve_public_addresses",
        lambda *_args, **_kwargs: ((socket.AF_INET, "93.184.216.34"),),
    )
    monkeypatch.setattr(socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(connectors.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(connectors.http.client, "HTTPSConnection", FakeConnection)

    with pytest.raises(RuntimeError, match="request timed out"):
        connectors._request_https_once(
            "https://example.com/source.txt",
            maximum_bytes=1024,
            timeout_seconds=1,
        )


@pytest.mark.parametrize(
    ("response_headers", "message"),
    (
        (
            [("Content-Length", "1"), ("Content-Length", "1")],
            "duplicate content-length",
        ),
        (
            [("Content-Length", "1"), ("Transfer-Encoding", "chunked")],
            "ambiguous length",
        ),
        (
            [("Transfer-Encoding", "gzip")],
            "unsupported transfer encoding",
        ),
        (
            [("Content-Length", "+1")],
            "Content-Length is invalid",
        ),
    ),
)
def test_https_request_rejects_ambiguous_response_framing(
    monkeypatch: pytest.MonkeyPatch,
    response_headers: list[tuple[str, str]],
    message: str,
) -> None:
    class FakeSocket:
        def settimeout(self, _value: float) -> None:
            return None

        def connect(self, _endpoint: tuple[str, int]) -> None:
            return None

        def close(self) -> None:
            return None

    class FakeContext:
        def wrap_socket(self, raw: FakeSocket, *, server_hostname: str) -> FakeSocket:
            assert server_hostname == "example.com"
            return raw

    class FakeResponse:
        status = 200

        def getheaders(self) -> list[tuple[str, str]]:
            return response_headers

        def read(self, _maximum: int) -> bytes:
            return b"x"

    class FakeConnection:
        def __init__(self, _host: str, **_kwargs: object) -> None:
            self.sock: FakeSocket | None = None

        def request(self, *_args: object, **_kwargs: object) -> None:
            return None

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        connectors,
        "_resolve_public_addresses",
        lambda *_args, **_kwargs: ((socket.AF_INET, "93.184.216.34"),),
    )
    monkeypatch.setattr(socket, "socket", lambda *_args: FakeSocket())
    monkeypatch.setattr(connectors.ssl, "create_default_context", lambda: FakeContext())
    monkeypatch.setattr(connectors.http.client, "HTTPSConnection", FakeConnection)

    with pytest.raises(RuntimeError, match=message):
        connectors._request_https_once(
            "https://example.com/source.txt",
            maximum_bytes=1024,
            timeout_seconds=1,
        )


def test_https_snapshot_is_hash_bound_untrusted_and_review_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    content = b"# Remote rule\nVerify the downloaded artifact digest.\n"
    requested = "https://example.com/source.md"
    final = "https://cdn.example.com/source.md"

    monkeypatch.setattr(
        connectors,
        "_download_https",
        lambda *_args, **_kwargs: (
            final,
            content,
            "text/markdown",
            [requested, final],
            ["93.184.216.34", "93.184.216.35"],
        ),
    )
    with KnowledgeVault(root, read_only=False) as vault:
        with pytest.raises(ValueError, match="confirm-network"):
            connectors.capture_https_source(vault, requested, confirm_network=False)
        snapshot = connectors.capture_https_source(
            vault,
            requested,
            confirm_network=True,
            expected_sha256=sha256_bytes(content),
        )
        verified = connectors.verify_source_snapshot(vault, snapshot["snapshot_id"])
        assert verified["valid"] is True
        assert verified["network_used"] is True
        assert verified["canonical_origin_uri"] == final
        assert Path(verified["path_hint"]).read_bytes() == content

        with pytest.raises(ValueError, match="web/untrusted"):
            create_snapshot_ingest_job(
                vault,
                (snapshot,),
                source_kind="web",
                trust="user_provided",
                sensitivity="private",
            )
        job = create_snapshot_ingest_job(
            vault,
            (snapshot,),
            source_kind="web",
            trust="untrusted",
            sensitivity="private",
            typed_extraction="deterministic-v2",
        )
        completed = run_ingest_job(vault, job["job_id"])
        assert completed["state"] == "completed"
        source = vault.source_info(completed["items"][0]["source_id"])
        assert source["origin_uri"] == final
        assert source["trust"] == "untrusted"
        assert source["status"] == "pending"
        assert vault.review_queue(limit=100)["total"] > 0
        assert vault.verify_integrity()["valid"] is True


def test_invalid_expected_https_hash_is_rejected_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)

    def fail_download(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid expected hash must fail before network")

    monkeypatch.setattr(connectors, "_download_https", fail_download)
    with (
        KnowledgeVault(root, read_only=False) as vault,
        pytest.raises(ValueError, match="SHA-256"),
    ):
        connectors.capture_https_source(
            vault,
            "https://example.com/source.md",
            confirm_network=True,
            expected_sha256="invalid",
        )


def test_rehashed_job_cannot_elevate_https_snapshot_trust(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    requested = "https://example.com/governance.md"
    content = b"# Governance\nRemote bytes remain untrusted.\n"
    monkeypatch.setattr(
        connectors,
        "_download_https",
        lambda *_args, **_kwargs: (
            requested,
            content,
            "text/markdown",
            [requested],
            ["93.184.216.34"],
        ),
    )
    with KnowledgeVault(root, read_only=False) as vault:
        snapshot = connectors.capture_https_source(
            vault,
            requested,
            confirm_network=True,
        )
        job = create_snapshot_ingest_job(
            vault,
            (snapshot,),
            source_kind="web",
            trust="untrusted",
            sensitivity="private",
        )
        path = root / "operations" / "jobs" / f"{job['job_id']}.json"
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["configuration"]["trust"] = "user_provided"
        tampered["record_sha256"] = knowledge_jobs._job_digest(tampered)
        path.write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            os.chmod(path, 0o600)

        with pytest.raises(RuntimeError, match="connector identity"):
            load_ingest_job(vault, job["job_id"])


def test_local_git_snapshot_uses_exact_commit_without_checkout_or_network(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    repository, revision = _repository(tmp_path)
    (repository / "guide.md").write_text(
        "# Working tree change\nThis text must not enter the exact snapshot.\n",
        encoding="utf-8",
    )

    plan = connectors.plan_git_source(repository, revision, "product-docs")
    assert plan["commit"] == revision
    assert plan["network_performed"] is False
    assert plan["checkout_performed"] is False
    assert plan["file_count"] == 2
    assert plan["skipped_count"] == 1

    with KnowledgeVault(root, read_only=False) as vault:
        with pytest.raises(ValueError, match="confirm-local-repository"):
            connectors.capture_git_sources(
                vault,
                repository,
                revision,
                "product-docs",
                confirm_local_repository=False,
            )
        snapshots = connectors.capture_git_sources(
            vault,
            repository,
            revision,
            "product-docs",
            confirm_local_repository=True,
        )
        assert {item["logical_path"] for item in snapshots} == {"check.py", "guide.md"}
        guide = next(item for item in snapshots if item["logical_path"] == "guide.md")
        assert Path(guide["path_hint"]).read_text(encoding="utf-8").startswith(
            "# Committed guide"
        )
        assert str(repository) not in guide["canonical_origin_uri"]
        assert guide["canonical_origin_uri"].startswith(
            f"deeplaw-git://product-docs/{revision}/"
        )

        job = create_snapshot_ingest_job(
            vault,
            snapshots,
            source_kind="code",
            trust="user_provided",
            sensitivity="private",
            typed_extraction="deterministic-v2",
        )
        completed = run_ingest_job(vault, job["job_id"])
        assert completed["state"] == "completed"
        assert completed["summary"]["succeeded"] == 2
        for item in completed["items"]:
            source = vault.source_info(item["source_id"])
            assert source["origin_uri"].startswith("deeplaw-git://product-docs/")
            assert str(repository) not in source["origin_uri"]
            assert source["status"] == "pending"
        assert vault.verify_integrity()["valid"] is True


def test_local_git_requires_a_full_exact_commit_and_bounded_patterns(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)

    with pytest.raises(ValueError, match="exact full commit"):
        connectors.plan_git_source(repository, revision[:12], "product-docs")
    with pytest.raises(ValueError, match="patterns exceed"):
        connectors.plan_git_source(
            repository,
            revision,
            "product-docs",
            include=tuple(f"path-{index}" for index in range(33)),
        )


def test_local_git_capture_deadline_includes_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _vault(tmp_path)
    clock = [0.0]
    revision = "a" * 40

    def inventory(
        repository: str | Path,
        selected_revision: str,
        repository_id: str,
        *,
        include: tuple[str, ...],
        exclude: tuple[str, ...],
        deadline: float | None = None,
    ) -> tuple[Path, str, str, list[dict[str, Any]], int]:
        assert Path(repository) == tmp_path
        assert selected_revision == revision
        assert repository_id == "deadline-test"
        assert include == exclude == ()
        assert deadline == 1.0
        clock[0] = 2.0
        return (
            tmp_path,
            revision,
            repository_id,
            [
                {
                    "logical_path": "guide.md",
                    "git_object_id": "b" * 40,
                    "git_mode": "100644",
                    "byte_size": 1,
                }
            ],
            0,
        )

    monkeypatch.setattr(connectors.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(connectors, "_git_inventory", inventory)
    with (
        KnowledgeVault(root, read_only=False) as vault,
        pytest.raises(RuntimeError, match="capture timed out"),
    ):
        connectors.capture_git_sources(
            vault,
            tmp_path,
            revision,
            "deadline-test",
            confirm_local_repository=True,
            timeout_seconds=1,
        )

    assert not (root / "operations" / "source-snapshots").exists()


def test_local_git_commits_share_identity_but_updates_remain_review_gated(
    tmp_path: Path,
) -> None:
    root = _vault(tmp_path)
    repository, first_revision = _repository(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        first_snapshot = connectors.capture_git_sources(
            vault,
            repository,
            first_revision,
            "stable-repository",
            include=("guide.md",),
            confirm_local_repository=True,
        )
        first_job = create_snapshot_ingest_job(
            vault,
            first_snapshot,
            source_kind="document",
            trust="user_provided",
            sensitivity="private",
        )
        first_result = run_ingest_job(vault, first_job["job_id"])
        first_source_id = first_result["items"][0]["source_id"]
        first_source = vault.source_info(first_source_id)
        manifest = vault.source_review_manifest(first_source_id)
        vault.approve_source_assets(
            first_source_id,
            confirm_reviewed=True,
            review_manifest_sha256=manifest["review_manifest_sha256"],
        )

        (repository / "guide.md").write_text(
            "# Updated guide\nReview the new exact commit before activation.\n",
            encoding="utf-8",
        )
        _git(repository, "add", "guide.md")
        _git(repository, "commit", "--quiet", "-m", "update guide")
        second_revision = _git(repository, "rev-parse", "HEAD")
        second_snapshot = connectors.capture_git_sources(
            vault,
            repository,
            second_revision,
            "stable-repository",
            include=("guide.md",),
            confirm_local_repository=True,
        )
        second_job = create_snapshot_ingest_job(
            vault,
            second_snapshot,
            source_kind="document",
            trust="user_provided",
            sensitivity="private",
        )
        assert second_job["items"][0]["action"] == "update"
        second_result = run_ingest_job(vault, second_job["job_id"])
        second_source = vault.source_info(second_result["items"][0]["source_id"])

        assert second_source["canonical_source_key"] == first_source["canonical_source_key"]
        assert second_source["source_id"] != first_source_id
        assert second_source["status"] == "pending"
        assert vault.source_info(first_source_id)["status"] == "active"
        assert vault.active_source_for_key(first_source["canonical_source_key"])[
            "source_id"
        ] == first_source_id
        assert first_revision in first_source["origin_uri"]
        assert second_revision in second_source["origin_uri"]


def test_snapshot_tampering_is_detected_before_job_compilation(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    repository, revision = _repository(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        snapshots = connectors.capture_git_sources(
            vault,
            repository,
            revision,
            "tamper-test",
            include=("guide.md",),
            confirm_local_repository=True,
        )
        job = create_snapshot_ingest_job(
            vault,
            snapshots,
            source_kind="document",
            trust="user_provided",
            sensitivity="private",
        )
        Path(snapshots[0]["path_hint"]).write_bytes(b"tampered")

        with pytest.raises(RuntimeError, match="verification"):
            connectors.verify_source_snapshot(vault, snapshots[0]["snapshot_id"])
        failed = run_ingest_job(vault, job["job_id"])
        assert failed["state"] == "interrupted"
        assert failed["summary"]["failed"] == 1
        assert vault.all_sources() == ()


def test_rehashed_snapshot_manifest_cannot_change_connector_identity(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    repository, revision = _repository(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        snapshot = connectors.capture_git_sources(
            vault,
            repository,
            revision,
            "identity-test",
            include=("guide.md",),
            confirm_local_repository=True,
        )[0]
        manifest_path = Path(snapshot["path_hint"]).parent / "snapshot.json"
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["collection_id"] = "collection_" + "0" * 24
        body = {key: value for key, value in manifest.items() if key != "record_sha256"}
        manifest["record_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            os.chmod(manifest_path, 0o600)

        with pytest.raises(RuntimeError, match="connector identity"):
            connectors.verify_source_snapshot(vault, snapshot["snapshot_id"])


def test_read_only_snapshot_verification_does_not_create_directories(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    operations = root / "operations"
    assert not operations.exists()

    with (
        KnowledgeVault(root, read_only=True) as vault,
        pytest.raises(RuntimeError, match="missing or unsafe"),
    ):
        connectors.verify_source_snapshot(vault, "sourcesnapshot_" + "0" * 24)

    assert not operations.exists()


def test_snapshot_manifest_and_bytes_match_their_recorded_hash(tmp_path: Path) -> None:
    root = _vault(tmp_path)
    repository, revision = _repository(tmp_path)
    with KnowledgeVault(root, read_only=False) as vault:
        snapshot = connectors.capture_git_sources(
            vault,
            repository,
            revision,
            "hash-test",
            include=("guide.md",),
            confirm_local_repository=True,
        )[0]
        assert sha256_file(Path(snapshot["path_hint"])) == snapshot["content_sha256"]
        manifest = Path(snapshot["path_hint"]).parent / "snapshot.json"
        assert manifest.is_file()
        assert connectors.verify_source_snapshot(vault, snapshot["snapshot_id"])["valid"]
