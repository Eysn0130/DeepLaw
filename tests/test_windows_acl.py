from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from deeplaw import windows_acl
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
