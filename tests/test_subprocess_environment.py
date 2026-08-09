from __future__ import annotations

import pytest

from deeplaw import subprocess_environment


def test_closed_environment_copies_only_portable_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "PATH": "/portable/path",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LC_CTYPE": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "TEMP": "/portable/tmp",
        "TMP": "/portable/tmp",
        "TMPDIR": "/portable/tmp",
        "SYSTEMROOT": r"C:\\Windows",
        "WINDIR": r"C:\\Windows",
        "COMSPEC": r"C:\\Windows\\System32\\cmd.exe",
        "PATHEXT": ".COM;.EXE",
        "DEEPLAW_TEST_AMBIENT_SECRET": "ambient-secret",
        "TEST_PROVIDER_TOKEN": "provider-secret",
        "XDG_CONFIG_HOME": "/ambient/config",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    environment = subprocess_environment._build_subprocess_environment(
        overrides={"HOME": "/isolated/home", "PYTHONPATH": "/isolated/src"}
    )

    assert environment == {
        **{name: values[name] for name in subprocess_environment._INHERITED_NAMES},
        "HOME": "/isolated/home",
        "PYTHONPATH": "/isolated/src",
    }
    assert "DEEPLAW_TEST_AMBIENT_SECRET" not in environment
    assert "TEST_PROVIDER_TOKEN" not in environment
    assert "XDG_CONFIG_HOME" not in environment


def test_closed_environment_does_not_fabricate_missing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in subprocess_environment._INHERITED_NAMES:
        monkeypatch.delenv(name, raising=False)

    environment = subprocess_environment._build_subprocess_environment()

    assert environment == {}


def test_closed_environment_rejects_non_path_overrides() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        subprocess_environment._build_subprocess_environment(
            overrides={"TEST_PROVIDER_TOKEN": "secret"}
        )


def test_closed_environment_rejects_nul_override() -> None:
    with pytest.raises(ValueError, match="text values"):
        subprocess_environment._build_subprocess_environment(
            overrides={"HOME": "bad\x00path"}
        )
