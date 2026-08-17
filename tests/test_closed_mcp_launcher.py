from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from deeplaw.task_context import build_task_context_binding
from deeplaw.util import canonical_json, sha256_bytes

REPOSITORY = Path(__file__).resolve().parents[1]


def _static_servers() -> list[dict[str, Any]]:
    plugin_paths = (
        REPOSITORY / "plugins/deeplaw/.mcp.json",
        REPOSITORY / "plugins/deeplaw/.claude-plugin/mcp.json",
        REPOSITORY / "plugins/deeplaw-knowledge-os/.mcp.json",
        REPOSITORY / "plugins/deeplaw-knowledge-os/.claude-plugin/mcp.json",
    )
    opencode_paths = (
        REPOSITORY / "adapters/opencode/opencode.jsonc",
        REPOSITORY / "adapters/opencode/knowledge-os.jsonc",
        REPOSITORY / "adapters/opencode/knowledge-compiler.example.jsonc",
    )
    servers = [
        server
        for path in plugin_paths
        for server in json.loads(path.read_text(encoding="utf-8"))["mcpServers"].values()
    ]
    servers.extend(
        server
        for path in opencode_paths
        for server in json.loads(path.read_text(encoding="utf-8"))["mcp"].values()
    )
    return servers


def _binding() -> dict[str, Any]:
    return build_task_context_binding(
        sha256_bytes(b"pass19-owner-registered-project"),
        sha256_bytes(b"pass19-task-line"),
        repository_sha256=sha256_bytes(b"pass19-repository"),
        worktree_sha256=sha256_bytes(b"pass19-stable-worktree"),
        base_revision="a" * 40,
        dirty_state_sha256=sha256_bytes(b"pass19-dirty-snapshot"),
    )


def test_static_host_configs_use_fixed_closed_launcher() -> None:
    for server in _static_servers():
        argv = server.get("args", server.get("command"))
        assert isinstance(argv, list)
        assert "--closed-environment" in argv
        assert "--vault" not in argv
        assert not any(Path(item).is_absolute() for item in argv if isinstance(item, str))


def test_closed_launcher_real_child_sees_only_portable_and_explicit_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment

    vault = tmp_path / "vault"
    vault.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-auth"))
    monkeypatch.setenv("OPENCODE_CONFIG", str(tmp_path / "opencode.json"))
    monkeypatch.setenv("OPENAI_API_KEY", "pass19-openai-canary")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "pass19-deepseek-canary")
    monkeypatch.setenv("DEEPLAW_TEST_AMBIENT_SECRET", "pass19-ambient-canary")
    monkeypatch.setenv("DOTENV_CONFIG_PATH", str(tmp_path / ".env"))

    with closed_mcp_environment(
        surface="knowledge_support",
        vault_path=vault,
        task_binding=_binding(),
    ) as launch:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, os; from pathlib import Path; "
                    "print(json.dumps({'environment': dict(os.environ), "
                    "'home': str(Path.home()), 'cwd': os.getcwd()}, sort_keys=True))"
                ),
            ],
            cwd=launch.cwd,
            env=launch.environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        child = json.loads(probe.stdout)
        environment = child["environment"]
        assert set(environment) <= launch.allowed_environment_names
        assert environment["DEEPLAW_KNOWLEDGE_VAULT"] == str(vault.resolve())
        assert environment["DEEPLAW_TASK_BINDING"] == canonical_json(_binding())
        assert child["home"] == environment["HOME"]
        assert child["cwd"] == launch.cwd
        assert environment["XDG_CONFIG_HOME"].startswith(environment["HOME"])
        assert environment["XDG_DATA_HOME"].startswith(environment["HOME"])
        assert environment["XDG_CACHE_HOME"].startswith(environment["HOME"])
        assert environment["XDG_STATE_HOME"].startswith(environment["HOME"])
        for blocked in (
            "CODEX_HOME",
            "OPENCODE_CONFIG",
            "OPENAI_API_KEY",
            "DEEPSEEK_API_KEY",
            "DEEPLAW_TEST_AMBIENT_SECRET",
            "DOTENV_CONFIG_PATH",
        ):
            assert blocked not in environment
        assert "pass19-openai-canary" not in probe.stdout
        assert "pass19-deepseek-canary" not in probe.stdout
        assert "pass19-ambient-canary" not in probe.stdout


def test_closed_launcher_rejects_arbitrary_surface_and_linked_vault(
    tmp_path: Path,
) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment

    vault = tmp_path / "vault"
    vault.mkdir(mode=0o700)
    linked = tmp_path / "linked-vault"
    try:
        linked.symlink_to(vault, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    with (
        pytest.raises(ValueError, match="surface is invalid"),
        closed_mcp_environment(surface="shell", vault_path=vault),
    ):
        pass
    with (
        pytest.raises(RuntimeError, match="selected Knowledge Vault is unsafe"),
        closed_mcp_environment(surface="knowledge_support", vault_path=linked),
    ):
        pass


def test_closed_launcher_passes_only_verified_host_workspace_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment

    vault = tmp_path / "vault"
    vault.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-cross-to-mcp")

    with closed_mcp_environment(
        surface="knowledge_support",
        vault_path=vault,
        workspace=workspace,
    ) as launch:
        assert launch.environment["DEEPLAW_HOST_WORKSPACE"] == str(
            workspace.resolve()
        )
        assert "DEEPSEEK_API_KEY" not in launch.environment
        assert "must-not-cross-to-mcp" not in repr(launch.environment)


def test_host_connect_and_launcher_reject_the_same_linked_vault_ancestor(
    tmp_path: Path,
) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment
    from deeplaw.host_connect import build_host_connect_plan
    from deeplaw.knowledge_autonomy import initialize_autonomous_core
    from deeplaw.knowledge_store import initialize_knowledge_vault

    real_parent = tmp_path / "real-parent"
    vault = real_parent / "vault"
    initialize_knowledge_vault(vault, name="linked-ancestor", scope="project")
    initialize_autonomous_core(vault)
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    selected_vault = linked_parent / "vault"

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


def test_closed_read_launcher_preserves_missing_vault_discovery_boundary(
    tmp_path: Path,
) -> None:
    from deeplaw.closed_mcp_launcher import closed_mcp_environment

    missing = tmp_path / "missing-vault"
    with closed_mcp_environment(
        surface="knowledge_support",
        vault_path=missing,
    ) as launch:
        assert launch.environment["DEEPLAW_KNOWLEDGE_VAULT"] == str(missing)
    with (
        pytest.raises(RuntimeError, match="selected Knowledge Vault is unavailable"),
        closed_mcp_environment(surface="knowledge_sink", vault_path=missing),
    ):
        pass


def test_closed_environment_maps_windows_home_without_codex_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw import subprocess_environment

    monkeypatch.setenv("USERPROFILE", r"C:\ambient-user")
    monkeypatch.setenv("CODEX_HOME", r"C:\ambient-codex")
    environment = subprocess_environment._build_subprocess_environment(
        overrides={
            "HOME": r"D:\isolated-home",
            "XDG_CONFIG_HOME": r"D:\isolated-home\xdg-config",
        },
        platform_name="nt",
    )

    assert environment["HOME"] == r"D:\isolated-home"
    assert environment["USERPROFILE"] == r"D:\isolated-home"
    assert environment["XDG_CONFIG_HOME"] == r"D:\isolated-home\xdg-config"
    assert "CODEX_HOME" not in environment


def test_generated_host_config_is_path_free_and_task_neutral(
    tmp_path: Path,
) -> None:
    from deeplaw.host_connect import build_host_connect_plan
    from deeplaw.knowledge_autonomy import initialize_autonomous_core
    from deeplaw.knowledge_store import initialize_knowledge_vault

    vault = tmp_path / "private-vault"
    initialize_knowledge_vault(vault, name="pass19-host", scope="project")
    initialize_autonomous_core(vault)

    plan = build_host_connect_plan(
        host="codex",
        vault_path=vault,
        owner_home=tmp_path / "owner-home",
    )

    rendered = canonical_json(plan)
    assert plan["schema_version"] == "deeplaw.host-connect-plan/v2"
    assert plan["data_binding"] == {
        "environment_variable": "DEEPLAW_KNOWLEDGE_VAULT",
        "expected_vault_id": plan["vault_id"],
        "value_included": False,
    }
    assert plan["task_binding_configured"] is False
    assert plan["task_binding_sha256"] is None
    assert plan["task_handle_configured"] is False
    assert plan["task_handle_sha256"] is None
    assert "--closed-environment" in rendered
    assert "--expected-vault-id" in rendered
    assert "--task-binding" not in rendered
    assert "--task-handle" not in rendered
    assert str(vault.resolve()) not in rendered

    with pytest.raises(ValueError, match="task-neutral"):
        build_host_connect_plan(
            host="codex",
            vault_path=vault,
            task_binding=_binding(),
            owner_home=tmp_path / "owner-home",
        )


def test_closed_launcher_revalidates_expected_vault_identity_in_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw.closed_mcp_launcher import launch_closed_mcp
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
    from deeplaw.knowledge_store import initialize_knowledge_vault

    vault = tmp_path / "vault"
    initialize_knowledge_vault(vault, name="child-revalidation", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        expected_vault_id = store.vault_id
    observed: list[str] = []

    class _Completed:
        returncode = 0

    def record_run(argv: list[str], **_: object) -> _Completed:
        observed.extend(argv)
        return _Completed()

    monkeypatch.setattr("deeplaw.closed_mcp_launcher.subprocess.run", record_run)
    launch_closed_mcp(
        surface="knowledge_support",
        vault_path=vault,
        expected_vault_id=expected_vault_id,
    )

    assert observed[-2:] == ["--expected-vault-id", expected_vault_id]


def test_closed_launcher_observes_vault_identity_without_opening_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deeplaw import host_runtime, knowledge_store
    from deeplaw.closed_mcp_launcher import closed_mcp_environment
    from deeplaw.knowledge_autonomy import initialize_autonomous_core
    from deeplaw.knowledge_store import initialize_knowledge_vault

    vault = tmp_path / "vault"
    initialized = initialize_knowledge_vault(
        vault,
        name="manifest-only-identity",
        scope="project",
    )
    initialize_autonomous_core(vault)

    class ForbiddenKnowledgeVault:
        def __init__(self, *_: object, **__: object) -> None:
            raise AssertionError("Host identity observation must not open SQLite")

    monkeypatch.setattr(
        host_runtime,
        "KnowledgeVault",
        ForbiddenKnowledgeVault,
        raising=False,
    )
    monkeypatch.setattr(knowledge_store, "KnowledgeVault", ForbiddenKnowledgeVault)

    with closed_mcp_environment(
        surface="knowledge_support",
        vault_path=vault,
        expected_vault_id=str(initialized["vault_id"]),
    ) as launch:
        assert launch.expected_vault_id == initialized["vault_id"]


def test_owner_local_vault_binding_resolves_custom_vault_without_host_path(
    tmp_path: Path,
) -> None:
    from deeplaw.host_runtime import bind_owner_vault, resolve_knowledge_vault
    from deeplaw.knowledge_autonomy import AutonomousKnowledgeStore, initialize_autonomous_core
    from deeplaw.knowledge_store import initialize_knowledge_vault

    vault = tmp_path / "custom-vault"
    owner_home = tmp_path / "owner-home"
    initialize_knowledge_vault(vault, name="owner-binding", scope="project")
    initialize_autonomous_core(vault)
    with AutonomousKnowledgeStore(vault, read_only=True) as store:
        expected_vault_id = store.vault_id

    receipt = bind_owner_vault(vault, owner_home=owner_home)
    resolved = resolve_knowledge_vault(
        None,
        expected_vault_id=expected_vault_id,
        require_existing=True,
        owner_home=owner_home,
    )

    assert receipt["vault_id"] == expected_vault_id
    assert receipt["owner_local_binding_written"] is True
    assert resolved == vault.resolve()
    assert str(vault.resolve()) not in canonical_json(receipt)
