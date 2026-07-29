from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

import benchmarks.hosts.run_codex_plugin_smoke as codex_smoke
from deeplaw.bounded_subprocess import BoundedProcessResult
from deeplaw.util import canonical_json, sha256_bytes, sha256_file

REPOSITORY = Path(__file__).resolve().parents[1]
REPORT_PATH = REPOSITORY / "benchmarks/hosts/codex-plugin-smoke-2026-07-29.json"
SCHEMA_PATH = REPOSITORY / "contracts/codex-plugin-host-smoke.v1.schema.json"


class _FakeCodex:
    def __init__(self, repository: Path, *, tamper_cache: bool = False) -> None:
        self.repository = repository
        self.tamper_cache = tamper_cache
        self.marketplace_added = False
        self.installed: set[str] = set()
        self.specs: dict[str, dict[str, str]] = {}
        marketplace = json.loads(
            (repository / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
        )
        for plugin in marketplace["plugins"]:
            root = repository / plugin["source"]["path"]
            manifest = json.loads(
                (root / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
            )
            plugin_id = f"{plugin['name']}@{marketplace['name']}"
            self.specs[plugin_id] = {
                "name": plugin["name"],
                "version": manifest["version"],
                "root": str(root.resolve()),
            }

    @staticmethod
    def _result(value: Any) -> BoundedProcessResult:
        stdout = (
            value.encode("utf-8")
            if isinstance(value, str)
            else (json.dumps(value, indent=2) + "\n").encode("utf-8")
        )
        return BoundedProcessResult(returncode=0, stdout=stdout, stderr=b"")

    def _entry(self, plugin_id: str, *, installed: bool) -> dict[str, Any]:
        spec = self.specs[plugin_id]
        return {
            "pluginId": plugin_id,
            "name": spec["name"],
            "marketplaceName": "deeplaw",
            "version": spec["version"],
            "installed": installed,
            "enabled": installed,
            "source": {"source": "local", "path": spec["root"]},
            "marketplaceSource": {
                "sourceType": "local",
                "source": str(self.repository.resolve()),
            },
            "installPolicy": "AVAILABLE",
            "authPolicy": "ON_INSTALL",
        }

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        input_bytes: bytes = b"",
        environment: dict[str, str] | None = None,
        cwd: str | Path | None = None,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
    ) -> BoundedProcessResult:
        del input_bytes, cwd, timeout_seconds, max_stdout_bytes, max_stderr_bytes
        arguments = tuple(command[1:])
        if arguments == ("rev-parse", "HEAD"):
            return self._result("a" * 40 + "\n")
        if arguments == ("status", "--porcelain=v1", "--untracked-files=all"):
            return self._result(" M README.md\n")
        if arguments == ("--version",):
            return self._result("codex-cli 0.146.0-test\n")
        assert environment is not None
        codex_home = Path(environment["CODEX_HOME"])
        if arguments[:3] == ("plugin", "marketplace", "add"):
            assert arguments[3:] == (str(self.repository.resolve()), "--json")
            self.marketplace_added = True
            return self._result(
                {
                    "marketplaceName": "deeplaw",
                    "installedRoot": str(self.repository.resolve()),
                    "alreadyAdded": False,
                }
            )
        if arguments == ("plugin", "list", "--available", "--json"):
            assert self.marketplace_added
            return self._result(
                {
                    "installed": [],
                    "available": [
                        self._entry(plugin_id, installed=False)
                        for plugin_id in sorted(self.specs)
                    ],
                }
            )
        if arguments == ("plugin", "list", "--json"):
            return self._result(
                {
                    "installed": [
                        self._entry(plugin_id, installed=True)
                        for plugin_id in sorted(self.installed)
                    ],
                    "available": [],
                }
            )
        if len(arguments) == 4 and arguments[:2] == ("plugin", "add"):
            plugin_id = arguments[2]
            assert arguments[3] == "--json"
            spec = self.specs[plugin_id]
            destination = (
                codex_home
                / "plugins"
                / "cache"
                / "deeplaw"
                / spec["name"]
                / spec["version"]
            )
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(Path(spec["root"]), destination)
            if self.tamper_cache and plugin_id == "deeplaw@deeplaw":
                (destination / ".mcp.json").write_text("{}\n", encoding="utf-8")
            self.installed.add(plugin_id)
            return self._result(
                {
                    "pluginId": plugin_id,
                    "name": spec["name"],
                    "marketplaceName": "deeplaw",
                    "version": spec["version"],
                    "installedPath": str(destination.resolve()),
                    "authPolicy": "ON_INSTALL",
                }
            )
        if len(arguments) == 4 and arguments[:2] == ("plugin", "remove"):
            plugin_id = arguments[2]
            assert arguments[3] == "--json"
            spec = self.specs[plugin_id]
            self.installed.remove(plugin_id)
            return self._result(
                {
                    "pluginId": plugin_id,
                    "name": spec["name"],
                    "marketplaceName": "deeplaw",
                }
            )
        raise AssertionError(f"unexpected subprocess arguments: {arguments!r}")


def _fake_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "codex-fake"
    executable.write_bytes(b"fake Codex executable\n")
    executable.chmod(0o700)
    return executable


def test_isolated_codex_plugin_lifecycle_and_sanitized_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCodex(REPOSITORY)
    monkeypatch.setattr(codex_smoke, "run_bounded_subprocess", fake)

    report = codex_smoke.run(
        REPOSITORY,
        codex=_fake_executable(tmp_path),
        timeout_seconds=5,
    )

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)
    assert report["scope"] == "plugin-lifecycle-only"
    assert report["claim_eligible"] is False
    assert report["full_host_acceptance"] is False
    assert report["result"] == {
        "success": True,
        "marketplace_discovery": True,
        "install_remove_readd": True,
        "plugin_lifecycle_isolation": True,
        "cache_copy_exact": True,
        "final_installed_plugin_ids": [],
    }
    assert [item["sequence"] for item in report["command_evidence"]] == list(range(1, 20))
    assert len(report["lifecycle_checks"]) == 9
    assert len(report["cache_copy_checks"]) == 4
    assert all(item["exact_match"] is True for item in report["cache_copy_checks"])
    rendered = canonical_json(report)
    assert str(REPOSITORY.resolve()) not in rendered
    assert str(tmp_path.resolve()) not in rendered


def test_codex_plugin_smoke_rejects_cache_byte_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeCodex(REPOSITORY, tamper_cache=True)
    monkeypatch.setattr(codex_smoke, "run_bounded_subprocess", fake)

    with pytest.raises(codex_smoke.HostSmokeError, match="cache bytes differ"):
        codex_smoke.run(
            REPOSITORY,
            codex=_fake_executable(tmp_path),
            timeout_seconds=5,
        )


def test_checked_in_codex_plugin_smoke_remains_source_bound_and_non_claiming() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report)

    body = {key: value for key, value in report.items() if key != "record_sha256"}
    assert report["record_sha256"] == sha256_bytes(canonical_json(body).encode("utf-8"))
    assert report["candidate"]["worktree_dirty"] is True
    assert report["claim_eligible"] is False
    assert report["full_host_acceptance"] is False
    assert set(report["unresolved_checks"]) == set(codex_smoke.UNRESOLVED_CHECKS)
    for relative, expected_sha256 in report["candidate"]["implementation_files"].items():
        assert sha256_file(REPOSITORY / relative) == expected_sha256
    expected_sources = {
        item["relative_root"]: item
        for item in report["plugin_sources"]
    }
    for relative_root, source in expected_sources.items():
        observed = codex_smoke.plugin_inventory(REPOSITORY / relative_root)
        assert observed == {
            key: source[key]
            for key in ("inventory_sha256", "file_count", "total_bytes", "files")
        }
    rendered = canonical_json(report)
    assert str(REPOSITORY.resolve()) not in rendered
    assert "/private/var/folders/" not in rendered
    assert "/Users/" not in rendered
    assert "C:\\Users\\" not in rendered
