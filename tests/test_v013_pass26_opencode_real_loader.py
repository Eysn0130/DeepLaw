from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = REPOSITORY / "adapters" / "opencode" / "plugins" / "deeplaw-native.ts"
OPENCODE = shutil.which("opencode")
EXPECTED_OPENCODE_VERSION = "1.18.16"


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_server(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(
                f"OpenCode server exited before readiness: stdout={stdout!r}; stderr={stderr!r}"
            )
        try:
            with urllib.request.urlopen(base_url + "/global/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.05)
    raise AssertionError("OpenCode server did not become ready within 15 seconds")


def _freeze_local_plugin_dependency(directory: Path) -> None:
    """Prevent a loader-only test from performing an ambient package install."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "node_modules").mkdir(exist_ok=True)
    dependency = {"@opencode-ai/plugin": EXPECTED_OPENCODE_VERSION}
    (directory / "package.json").write_text(
        json.dumps({"dependencies": dependency}),
        encoding="utf-8",
    )
    (directory / "package-lock.json").write_text(
        json.dumps({"packages": {"": {"dependencies": dependency}}}),
        encoding="utf-8",
    )


@pytest.mark.skipif(OPENCODE is None, reason="local OpenCode binary is unavailable")
def test_exact_opencode_loads_project_plugin_and_dispatches_native_session_event(
    tmp_path: Path,
) -> None:
    assert OPENCODE is not None
    version = subprocess.run(
        [OPENCODE, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if version != EXPECTED_OPENCODE_VERSION:
        pytest.skip(f"exact OpenCode {EXPECTED_OPENCODE_VERSION} is unavailable: found {version}")

    project = tmp_path / "project"
    plugin_directory = project / ".opencode" / "plugins"
    plugin_directory.mkdir(parents=True)
    shutil.copyfile(PLUGIN_SOURCE, plugin_directory / PLUGIN_SOURCE.name)
    _freeze_local_plugin_dependency(plugin_directory.parent)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    observation_path = tmp_path / "resolver-observation.json"
    fake_deeplaw = fake_bin / "deeplaw"
    resolver_script = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

argv = sys.argv[1:]
pathlib.Path(__OBSERVATION_PATH__).write_text(
    json.dumps({"argv": argv, "environment_keys": sorted(os.environ)}),
    encoding="utf-8",
)
print(json.dumps({
    "schema_version": "deeplaw.host-continuity-capsule/v1",
    "status": "gap",
    "statements": [],
    "gaps": [{"code": "route_unbound"}],
    "conflicts": [],
    "write_performed": False,
}))
"""
    fake_deeplaw.write_text(
        resolver_script.replace("__OBSERVATION_PATH__", json.dumps(str(observation_path))),
        encoding="utf-8",
    )
    fake_deeplaw.chmod(0o700)

    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    _freeze_local_plugin_dependency(tmp_path / "config" / "opencode")
    isolated_vault = tmp_path / "vault"
    isolated_vault.mkdir()
    port = _unused_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    host_environment = {
        "HOME": str(isolated_home),
        "DEEPLAW_KNOWLEDGE_VAULT": str(isolated_vault),
        "PATH": f"{fake_bin}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TMPDIR": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    process = subprocess.Popen(
        [
            OPENCODE,
            "serve",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
            "--print-logs",
            "--log-level",
            "ERROR",
        ],
        cwd=project,
        env=host_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_server(base_url, process)
        request = urllib.request.Request(
            base_url + "/session",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
            session = json.loads(response.read())
        assert isinstance(session.get("id"), str) and session["id"]

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not observation_path.exists():
            time.sleep(0.05)
        if not observation_path.exists():
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(
                "the project plugin did not dispatch session.created; "
                f"stdout={stdout!r}; stderr={stderr!r}"
            )
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        assert observation["argv"][:7] == [
            "knowledge",
            "--format",
            "jsonl",
            "task",
            "resolve-host-continuity",
            "--vault",
            str(isolated_vault),
        ]
        assert observation["argv"][7:9] == [
            "--host",
            "opencode",
        ]
        environment_keys = set(observation["environment_keys"])
        assert {"DEEPLAW_KNOWLEDGE_VAULT", "LANG", "LC_ALL", "PATH"} <= environment_keys
        assert environment_keys <= {
            "CPATH",
            "DEEPLAW_KNOWLEDGE_VAULT",
            "LANG",
            "LC_ALL",
            "LIBRARY_PATH",
            "MANPATH",
            "PATH",
            "SDKROOT",
            "__CF_USER_TEXT_ENCODING",
        }
        assert not {
            "CODEX_HOME",
            "DEEPSEEK_API_KEY",
            "HOME",
            "OPENAI_API_KEY",
        } & environment_keys
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
