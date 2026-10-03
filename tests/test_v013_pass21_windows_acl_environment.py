from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_windows_acl_powershell_subprocess_uses_minimal_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw import windows_acl

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    monkeypatch.setenv("DEEPLAW_TEST_PROVIDER_TOKEN", "must-not-be-inherited")
    observed: dict[str, str] = {}

    def capture(
        _command: list[str],
        *,
        environment: dict[str, str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        observed.update(environment)
        return subprocess.CompletedProcess([], 0, b"{}", b"")

    monkeypatch.setattr(windows_acl, "_powershell", lambda: "powershell.exe")
    monkeypatch.setattr(windows_acl, "run_bounded_subprocess", capture)
    windows_acl._run_encoded_script(
        "Write-Output '{}'",
        operation="query",
        environment={"DEEPLAW_ACL_TARGET": str(tmp_path / "target")},
    )

    assert "CODEX_HOME" not in observed
    assert "OPENAI_API_KEY" not in observed
    assert "DEEPLAW_TEST_PROVIDER_TOKEN" not in observed
    assert observed["DEEPLAW_ACL_TARGET"] == str(tmp_path / "target")
