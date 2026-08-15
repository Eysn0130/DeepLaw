from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from deeplaw import knowledge_autonomy, knowledge_store, windows_acl
from deeplaw.knowledge_autonomy import (
    AutonomousKnowledgeStore,
    initialize_autonomous_core,
)
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import (
    KnowledgeVault,
    initialize_knowledge_vault,
    knowledge_vault_permission_report,
)
from deeplaw.windows_acl import (
    evaluate_windows_acl_payload,
    harden_windows_vault,
    native_windows_acl_report,
)


def _rule(sid: str, *, inherited: bool = False) -> dict[str, object]:
    return {
        "sid": sid,
        "access_type": "Allow",
        "rights_mask": 0x1F01FF,
        "inherited": inherited,
        "inheritance_flags": "None",
        "propagation_flags": "None",
    }


def test_windows_acl_prefers_the_matching_pwsh_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def fake_which(name: str) -> str | None:
        observed.append(name)
        return f"C:\\tools\\{name}"

    monkeypatch.setattr(windows_acl.shutil, "which", fake_which)

    assert windows_acl._powershell() == "C:\\tools\\pwsh.exe"
    assert observed == ["pwsh.exe"]


def test_windows_acl_uses_the_fixed_system_powershell_when_path_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = (
        tmp_path
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixed-system-powershell")
    monkeypatch.setattr(windows_acl.shutil, "which", lambda _name: None)
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    monkeypatch.delenv("WINDIR", raising=False)

    assert windows_acl._powershell() == str(executable)


def test_read_stores_harden_only_their_sqlite_sidecars_on_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="windows-read-sidecars", scope="project")
    initialize_autonomous_core(root)
    observed: list[Path] = []
    monkeypatch.setattr(
        windows_acl,
        "harden_windows_sqlite_sidecars",
        lambda database, **_kwargs: observed.append(Path(database)),
    )

    with KnowledgeVault(root, read_only=True):
        pass
    with AutonomousKnowledgeStore(root, read_only=True):
        pass

    assert observed == [
        root / ".deeplaw" / "ledger.sqlite3",
        root / ".deeplaw" / "ledger.sqlite3",
        root / ".deeplaw" / "ledger.sqlite3",
    ]


def test_windows_writable_autonomous_store_restores_the_whole_vault_on_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="windows-write-boundary", scope="project")
    initialize_autonomous_core(root)
    store = AutonomousKnowledgeStore(root, read_only=False)
    store.enable_grant(writer_id="windows-ledger-writer")
    hardened_vaults: list[Path] = []
    hardened_sidecars: list[Path] = []
    monkeypatch.setattr(knowledge_autonomy, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        windows_acl,
        "harden_windows_vault",
        lambda vault: hardened_vaults.append(Path(vault)),
    )
    monkeypatch.setattr(
        windows_acl,
        "harden_windows_sqlite_sidecars",
        lambda database, **_kwargs: hardened_sidecars.append(Path(database)),
    )

    store.close()

    assert hardened_vaults == [root]
    assert hardened_sidecars == []


def test_sqlite_sidecar_hardening_targets_only_new_file_identities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "ledger.sqlite3"
    wal = Path(f"{database}-wal")
    shm = Path(f"{database}-shm")
    wal.write_bytes(b"existing-safe-sidecar")
    wal_info = wal.lstat()
    previous = {"-wal": (int(wal_info.st_dev), int(wal_info.st_ino))}
    shm.write_bytes(b"new-sidecar")
    observed: list[Path] = []
    monkeypatch.setattr(windows_acl, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        windows_acl,
        "harden_windows_private_file",
        lambda path: observed.append(Path(path)),
    )

    windows_acl.harden_windows_sqlite_sidecars(
        database,
        previous_identities=previous,
    )

    assert observed == [shm]


def test_windows_acl_evaluator_requires_owner_only_native_acl() -> None:
    current = "S-1-5-21-1000"
    report = evaluate_windows_acl_payload(
        {
            "current_user_sid": current,
            "entries": [
                {
                    "path": "C:\\DeepLaw",
                    "kind": "directory",
                    "owner_sid": current,
                    "reparse_point": False,
                    "inherited_acl": False,
                    "access": [_rule(current)],
                },
                {
                    "path": "C:\\DeepLaw\\sources\\source.bin",
                    "kind": "file",
                    "owner_sid": current,
                    "reparse_point": False,
                    "inherited_acl": True,
                    "access": [_rule(current, inherited=True)],
                },
            ],
        }
    )

    assert report["permissions_verified"] is True
    assert report["owner_sid_verified"] is True
    assert report["reparse_points_absent"] is True
    assert report["entries"][1]["inherited_rule_count"] == 1


def test_windows_acl_evaluator_rejects_users_everyone_and_reparse_points() -> None:
    current = "S-1-5-21-1000"
    report = evaluate_windows_acl_payload(
        {
            "current_user_sid": current,
            "entries": [
                {
                    "path": "C:\\DeepLaw",
                    "kind": "directory",
                    "owner_sid": current,
                    "reparse_point": True,
                    "inherited_acl": True,
                    "access": [
                        _rule(current),
                        _rule("S-1-5-32-545", inherited=True),
                        _rule("S-1-1-0", inherited=True),
                    ],
                }
            ],
        }
    )

    assert report["permissions_verified"] is False
    assert report["reparse_points_absent"] is False
    assert report["entries"][0]["users_rule_count"] == 1
    assert report["entries"][0]["everyone_rule_count"] == 1
    assert any("broad_principal_allow" in item for item in report["errors"])


def test_source_ingest_reapplies_native_acl_for_new_and_idempotent_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    source = tmp_path / "source.md"
    source.write_text("# Decision\nKeep immutable Source bytes private.\n", encoding="utf-8")
    initialize_knowledge_vault(root, name="windows-source-boundary", scope="project")
    observed: list[Path] = []
    monkeypatch.setattr(
        knowledge_store,
        "_harden_stored_source_if_windows",
        lambda path: observed.append(path),
    )

    with KnowledgeVault(root, read_only=False) as vault:
        first = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        second = compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
        stored = vault.source_file_path(first["source"]["source_id"])

    assert second["idempotent"] is True
    assert observed == [stored, stored]


def test_writable_vault_close_reapplies_native_acl_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="windows-write-boundary", scope="project")
    observed: list[Path] = []
    monkeypatch.setattr(
        knowledge_store,
        "_harden_vault_after_write_if_windows",
        lambda path: observed.append(path),
    )

    with KnowledgeVault(root, read_only=True):
        pass
    with KnowledgeVault(root, read_only=False):
        pass

    assert observed == [root]


@pytest.mark.windows_native
@pytest.mark.skipif(os.name != "nt", reason="requires native Windows ACLs")
def test_native_windows_vault_acl_is_owner_only_after_real_ingest(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="windows-native", scope="project")
    source = tmp_path / "source.md"
    source.write_text("# Decision\nUse owner-only local storage.\n", encoding="utf-8")

    with KnowledgeVault(root, read_only=False) as vault:
        compile_source(
            vault,
            source,
            source_kind="document",
            confirm_no_case_data=True,
        )
    native_after_ingest = native_windows_acl_report(root)
    permissions_after_ingest = knowledge_vault_permission_report(root)
    assert native_after_ingest["status"] == "verified", native_after_ingest
    assert native_after_ingest["permissions_verified"] is True
    assert permissions_after_ingest["status"] == "verified"
    assert permissions_after_ingest["permissions_verified"] is True

    model_file = root / "models" / "fixture-model.bin"
    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"pinned-model-fixture")
    index_file = root / "derived" / "indexes" / "fixture-index.bin"
    index_file.parent.mkdir(parents=True)
    index_file.write_bytes(b"derived-index-fixture")
    hardening = harden_windows_vault(root)

    native = native_windows_acl_report(root)
    permissions = knowledge_vault_permission_report(root)

    assert hardening["applied"] is True
    assert native["status"] == "verified", native
    assert native["permissions_verified"] is True
    assert native["owner_sid_verified"] is True
    assert native["reparse_points_absent"] is True
    assert native["scan_complete"] is True
    assert all(item["users_rule_count"] == 0 for item in native["entries"])
    assert all(item["everyone_rule_count"] == 0 for item in native["entries"])
    checked_paths = {Path(item["path"]).name for item in native["entries"]}
    assert {"fixture-model.bin", "fixture-index.bin"} <= checked_paths
    assert permissions["status"] == "verified"
    assert permissions["permissions_verified"] is True


@pytest.mark.windows_native
@pytest.mark.skipif(os.name != "nt", reason="requires a native Windows junction")
def test_native_windows_acl_rejects_directory_junction(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    initialize_knowledge_vault(root, name="windows-junction", scope="project")
    target = tmp_path / "junction-target"
    target.mkdir()
    junction = root / "junction"
    process = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr

    report = native_windows_acl_report(root)

    assert report["status"] == "failed"
    assert report["permissions_verified"] is False
    assert report["reparse_points_absent"] is False
    assert any("reparse_point_present" in item for item in report["errors"])


@pytest.mark.windows_native
@pytest.mark.skipif(os.name != "nt", reason="requires a native Windows junction")
def test_host_connect_and_launcher_reject_junction_ancestor(tmp_path: Path) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment
    from deeplaw.host_connect import build_host_connect_plan

    real_parent = tmp_path / "real-parent"
    vault = real_parent / "vault"
    initialize_knowledge_vault(vault, name="windows-host-junction", scope="project")
    initialize_autonomous_core(vault)
    junction_parent = tmp_path / "junction-parent"
    process = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction_parent),
            str(real_parent),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    selected_vault = junction_parent / "vault"

    with (
        pytest.raises(RuntimeError, match="selected Knowledge Vault is unsafe"),
        closed_mcp_environment(
            surface="knowledge_support",
            vault_path=selected_vault,
        ),
    ):
        pass
    with pytest.raises(RuntimeError, match="selected Knowledge Vault is unsafe"):
        build_host_connect_plan(
            host="codex",
            vault_path=selected_vault,
            owner_home=tmp_path / "owner-home",
        )
