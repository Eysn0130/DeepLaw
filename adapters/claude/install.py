"""Explicit, bounded installer for the optional Claude Code hook template."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HOOK_MARKER = "deeplaw-claude-lifecycle-v1"
MAX_SETTINGS_BYTES = 1_048_576
MIN_TOKEN_BUDGET = 128
MAX_TOKEN_BUDGET = 8_000
HOOK_EVENTS = (
    "UserPromptSubmit",
    "PreCompact",
    "PostCompact",
    "PostToolUse",
    "Stop",
    "SessionEnd",
)
_ALLOWED_CONFIG_FLAGS = {
    "--vault",
    "--workspace-identity",
    "--repository-identity",
    "--scope",
    "--max-sensitivity",
    "--purpose",
    "--token-budget",
}
_ALLOWED_SCOPES = {"personal", "project", "domain"}
_ALLOWED_SENSITIVITIES = {"public", "internal", "private"}
_ALLOWED_PURPOSES = {
    "answer",
    "verify",
    "quote",
    "historical",
    "legal",
    "debug",
    "freshness_check",
}
_WINDOWS_PATH = re.compile(r"^[A-Za-z]:")
_SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN(?: [A-Z0-9 ]+)?-----"),
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{12,}\b"),
    re.compile(r"\b(?:api[_-]?key|password|client_secret)\s*[:=]", re.IGNORECASE),
)


class InstallerError(ValueError):
    """Raised when settings cannot be merged without an unsafe guess."""


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise InstallerError("settings are not canonical JSON") from error


def _settings_path(value: str | os.PathLike[str]) -> Path:
    selected = Path(value).expanduser()
    if not selected.is_absolute():
        selected = Path.cwd() / selected
    selected = selected.absolute()
    if selected.name in {"", ".", ".."} or selected.is_dir() or selected.is_symlink():
        raise InstallerError("settings must be an explicit regular file path")
    default_root = (Path.home() / ".claude").absolute()
    try:
        resolved = selected.resolve(strict=False)
    except OSError as error:
        raise InstallerError("settings path cannot be resolved safely") from error
    if resolved == default_root or default_root in resolved.parents:
        raise InstallerError("default Claude settings are never touched")
    if not selected.parent.is_dir():
        raise InstallerError("settings parent directory must already exist")
    return selected


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise InstallerError("settings must be a regular non-symlink file")
    if path.stat().st_size > MAX_SETTINGS_BYTES:
        raise InstallerError("settings exceed the bounded JSON input limit")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallerError("settings are not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise InstallerError("settings root must be a JSON object")
    return value


def _load_template() -> dict[str, Any]:
    path = Path(__file__).with_name("hooks.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InstallerError("Claude hook template is invalid") from error
    if not isinstance(value, dict) or set(value) != {"hooks"}:
        raise InstallerError("Claude hook template shape is invalid")
    hooks = value["hooks"]
    if not isinstance(hooks, dict) or set(hooks) != set(HOOK_EVENTS):
        raise InstallerError("Claude hook template event set is invalid")
    for event in HOOK_EVENTS:
        groups = hooks[event]
        if not isinstance(groups, list) or len(groups) != 1:
            raise InstallerError("Claude hook template matcher shape is invalid")
        group = groups[0]
        if not isinstance(group, dict) or set(group) != {"matcher", "hooks"}:
            raise InstallerError("Claude hook template group is invalid")
        if not isinstance(group["matcher"], str) or not isinstance(group["hooks"], list):
            raise InstallerError("Claude hook template group fields are invalid")
        if len(group["hooks"]) != 1 or not isinstance(group["hooks"][0], dict):
            raise InstallerError("Claude hook template command shape is invalid")
        command = group["hooks"][0]
        if set(command) != {"type", "command", "args"}:
            raise InstallerError("Claude hook template command fields are invalid")
        if command["type"] != "command" or not isinstance(command["command"], str):
            raise InstallerError("Claude hook template command is invalid")
        args = command["args"]
        if (
            not isinstance(args, list)
            or HOOK_MARKER not in args
            or "--event" not in args
            or args[args.index("--event") + 1] != event
        ):
            raise InstallerError("Claude hook template marker or event is invalid")
    return value


def _config_args(options: Mapping[str, Any]) -> list[str]:
    vault = options.get("vault")
    if vault is not None:
        if not isinstance(vault, (str, os.PathLike)) or not str(vault):
            raise InstallerError("explicit Vault configuration is invalid")
        if Path(vault).expanduser().is_symlink():
            raise InstallerError("explicit Vault configuration cannot be a symlink")
    for field in (
        "workspace_identity",
        "repository_identity",
        "scope",
        "max_sensitivity",
        "purpose",
    ):
        value = options.get(field)
        if value is not None and (
            not isinstance(value, str)
            or not value
            or any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)
            or (
                field in {"workspace_identity", "repository_identity"}
                and (value.startswith(("/", "\\", "~")) or _WINDOWS_PATH.match(value))
            )
            or (field == "scope" and value not in _ALLOWED_SCOPES)
            or (field == "max_sensitivity" and value not in _ALLOWED_SENSITIVITIES)
            or (field == "purpose" and value not in _ALLOWED_PURPOSES)
        ):
            raise InstallerError("explicit hook configuration is secret-shaped or invalid")
    token_budget = options.get("token_budget")
    if token_budget is not None and (
        isinstance(token_budget, bool)
        or not isinstance(token_budget, int)
        or not MIN_TOKEN_BUDGET <= token_budget <= MAX_TOKEN_BUDGET
    ):
        raise InstallerError("explicit token budget is invalid")
    values: list[str] = []
    for flag in (
        "--vault",
        "--workspace-identity",
        "--repository-identity",
        "--scope",
        "--max-sensitivity",
        "--purpose",
        "--token-budget",
    ):
        value = options.get(flag.removeprefix("--").replace("-", "_"))
        if value is None:
            continue
        if not isinstance(value, (str, int)) or isinstance(value, bool) or not str(value):
            raise InstallerError("explicit hook configuration is invalid")
        values.extend((flag, str(value)))
    return values


def _expected_hooks(options: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    template = _load_template()["hooks"]
    extra = _config_args(options)
    hook_script = Path(__file__).with_name("deeplaw_hook.py").resolve()
    if hook_script.is_symlink() or not hook_script.is_file():
        raise InstallerError("Claude hook script is unavailable")
    expected: dict[str, list[dict[str, Any]]] = {}
    for event in HOOK_EVENTS:
        group = dict(template[event][0])
        command = dict(group["hooks"][0])
        command["args"] = [str(hook_script), *command["args"][1:], *extra]
        group["hooks"] = [command]
        expected[event] = [group]
    return expected


def _hook_identity(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("args"), list)
        and HOOK_MARKER in value["args"]
    )


def _is_exact_managed_hook(value: Mapping[str, Any], *, event: str) -> bool:
    expected = _expected_hooks({})[event][0]["hooks"][0]
    if set(value) != {"type", "command", "args"}:
        return False
    if value.get("type") != expected["type"] or value.get("command") != expected["command"]:
        return False
    args = value.get("args")
    base_args = expected["args"]
    if not isinstance(args, list) or args[: len(base_args)] != base_args:
        return False
    extras = args[len(base_args) :]
    if len(extras) % 2:
        return False
    seen_flags: set[str] = set()
    for index in range(0, len(extras), 2):
        flag, value = extras[index : index + 2]
        if (
            not isinstance(flag, str)
            or flag not in _ALLOWED_CONFIG_FLAGS
            or flag in seen_flags
            or not isinstance(value, str)
            or not value
        ):
            return False
        seen_flags.add(flag)
    return True


def _validate_managed_hook(value: Mapping[str, Any], *, expected: Mapping[str, Any]) -> None:
    if set(value) != {"type", "command", "args"}:
        raise InstallerError("marked Claude hook contains unknown fields")
    if value != expected:
        args = value.get("args")
        if not isinstance(args, list) or any(
            str(item).startswith("--") and item not in _ALLOWED_CONFIG_FLAGS
            for item in args
        ):
            raise InstallerError("marked Claude hook conflicts with the managed template")
        raise InstallerError("marked Claude hook configuration conflicts")


def _merge_install(
    settings: dict[str, Any], expected: Mapping[str, list[dict[str, Any]]]
) -> tuple[dict[str, Any], bool]:
    merged = dict(settings)
    current = merged.get("hooks", {})
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise InstallerError("settings.hooks must be an object")
    current = {key: value for key, value in current.items()}
    changed = False
    for event in HOOK_EVENTS:
        groups = current.get(event, [])
        if not isinstance(groups, list):
            raise InstallerError("settings hook event must be an array")
        groups = [dict(group) if isinstance(group, Mapping) else group for group in groups]
        expected_group = expected[event][0]
        expected_command = expected_group["hooks"][0]
        found = False
        managed_count = 0
        for group in groups:
            if not isinstance(group, dict):
                continue
            group_hooks = group.get("hooks", [])
            if not isinstance(group_hooks, list):
                raise InstallerError("settings hook group must contain an array")
            for hook in group_hooks:
                if _hook_identity(hook):
                    _validate_managed_hook(hook, expected=expected_command)
                    found = True
                    managed_count += 1
            if group.get("matcher") == expected_group["matcher"] and not found:
                group["hooks"] = [*group_hooks, expected_command]
                found = True
                changed = True
        if managed_count > 1:
            raise InstallerError("duplicate managed Claude hooks conflict")
        if not found:
            groups.append(expected_group)
            changed = True
        current[event] = groups
    merged["hooks"] = current
    return merged, changed


def _merge_uninstall(settings: dict[str, Any]) -> tuple[dict[str, Any], int]:
    merged = dict(settings)
    current = merged.get("hooks", {})
    if current is None:
        return merged, 0
    if not isinstance(current, dict):
        raise InstallerError("settings.hooks must be an object")
    current = {key: value for key, value in current.items()}
    removed = 0
    for event in HOOK_EVENTS:
        groups = current.get(event)
        if groups is None:
            continue
        if not isinstance(groups, list):
            raise InstallerError("settings hook event must be an array")
        updated_groups: list[Any] = []
        for group in groups:
            if not isinstance(group, dict):
                updated_groups.append(group)
                continue
            hooks = group.get("hooks")
            if not isinstance(hooks, list):
                updated_groups.append(group)
                continue
            kept = []
            for hook in hooks:
                if _hook_identity(hook):
                    if not _is_exact_managed_hook(hook, event=event):
                        raise InstallerError(
                            "marked Claude hook conflicts with the managed template"
                        )
                    removed += 1
                    continue
                kept.append(hook)
            group_copy = dict(group)
            group_copy["hooks"] = kept
            updated_groups.append(group_copy)
        current[event] = updated_groups
    merged["hooks"] = current
    return merged, removed


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )
    if len(payload) > MAX_SETTINGS_BYTES:
        raise InstallerError("merged settings exceed the bounded JSON output limit")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.deeplaw-",
        dir=path.parent,
    )
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def install_settings(settings: str | os.PathLike[str], **options: Any) -> dict[str, Any]:
    path = _settings_path(settings)
    existing = _load_settings(path)
    merged, changed = _merge_install(existing, _expected_hooks(options))
    if changed:
        _atomic_write(path, merged)
    elif path.exists():
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return {
        "schema_version": "deeplaw.claude-hook-install-receipt/v1",
        "operation": "install",
        "changed": changed,
        "idempotent": not changed,
        "event_count": len(HOOK_EVENTS),
        "events": list(HOOK_EVENTS),
        "hook_marker": HOOK_MARKER,
        "settings_written": changed,
        "owner_only": True,
        "default_settings_touched": False,
    }


def uninstall_settings(settings: str | os.PathLike[str]) -> dict[str, Any]:
    path = _settings_path(settings)
    existing = _load_settings(path)
    merged, removed = _merge_uninstall(existing)
    if removed:
        _atomic_write(path, merged)
    elif path.exists():
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return {
        "schema_version": "deeplaw.claude-hook-install-receipt/v1",
        "operation": "uninstall",
        "removed_count": removed,
        "idempotent": removed == 0,
        "events": list(HOOK_EVENTS),
        "hook_marker": HOOK_MARKER,
        "settings_written": bool(removed),
        "owner_only": True,
        "default_settings_touched": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for operation in ("install", "uninstall"):
        command = commands.add_parser(operation)
        command.add_argument("--settings", type=Path, required=True)
        if operation == "install":
            command.add_argument("--vault")
            command.add_argument("--workspace-identity")
            command.add_argument("--repository-identity")
            command.add_argument("--scope", choices=("personal", "project", "domain"))
            command.add_argument("--max-sensitivity", choices=("public", "internal", "private"))
            command.add_argument(
                "--purpose",
                choices=(
                    "answer",
                    "verify",
                    "quote",
                    "historical",
                    "legal",
                    "debug",
                    "freshness_check",
                ),
            )
            command.add_argument("--token-budget", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.operation == "install":
            result = install_settings(
                args.settings,
                vault=args.vault,
                workspace_identity=args.workspace_identity,
                repository_identity=args.repository_identity,
                scope=args.scope,
                max_sensitivity=args.max_sensitivity,
                purpose=args.purpose,
                token_budget=args.token_budget,
            )
        else:
            result = uninstall_settings(args.settings)
    except (InstallerError, OSError, ValueError):
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
