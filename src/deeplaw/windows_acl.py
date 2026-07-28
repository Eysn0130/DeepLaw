from __future__ import annotations

import base64
import json
import os
import shutil
from pathlib import Path
from typing import Any

from .bounded_subprocess import BoundedSubprocessError, run_bounded_subprocess
from .util import strict_json_loads

WINDOWS_ACL_SCHEMA = "deeplaw.windows-acl-report/v1"
_EVERYONE_SID = "S-1-1-0"
_CREATOR_OWNER_SID = "S-1-3-0"
_AUTHENTICATED_USERS_SID = "S-1-5-11"
_SYSTEM_SID = "S-1-5-18"
_ADMINISTRATORS_SID = "S-1-5-32-544"
_USERS_SID = "S-1-5-32-545"
_MAX_ACL_PATHS = 100_000
_BATCH_SIZE = 96

_ACL_QUERY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$raw = [Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String($env:DEEPLAW_ACL_PATHS_B64)
)
$paths = ConvertFrom-Json -InputObject $raw
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$entries = @()
foreach ($path in $paths) {
  $item = Get-Item -LiteralPath $path -Force
  $acl = Get-Acl -LiteralPath $path
  try {
    $ownerSid = ([Security.Principal.NTAccount]$acl.Owner).Translate(
      [Security.Principal.SecurityIdentifier]
    ).Value
  } catch {
    $ownerSid = $acl.Owner
  }
  $rules = @()
  foreach ($rule in $acl.Access) {
    try {
      $sid = $rule.IdentityReference.Translate(
        [Security.Principal.SecurityIdentifier]
      ).Value
    } catch {
      $sid = $rule.IdentityReference.Value
    }
    $rules += [ordered]@{
      sid = $sid
      access_type = $rule.AccessControlType.ToString()
      rights_mask = [int64]$rule.FileSystemRights.value__
      inherited = [bool]$rule.IsInherited
      inheritance_flags = $rule.InheritanceFlags.ToString()
      propagation_flags = $rule.PropagationFlags.ToString()
    }
  }
  $entries += [ordered]@{
    path = $item.FullName
    kind = $(if ($item.PSIsContainer) { 'directory' } else { 'file' })
    owner_sid = $ownerSid
    reparse_point = [bool](($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
    inherited_acl = [bool](-not $acl.AreAccessRulesProtected)
    access = $rules
  }
}
[ordered]@{current_user_sid = $current; entries = $entries} |
  ConvertTo-Json -Depth 8 -Compress
"""

_ACL_HARDEN_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$root = [Text.Encoding]::UTF8.GetString(
  [Convert]::FromBase64String($env:DEEPLAW_ACL_ROOT_B64)
)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$items = @((Get-Item -LiteralPath $root -Force))
$items += @(Get-ChildItem -LiteralPath $root -Force -Recurse)
foreach ($item in $items) {
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing to harden a reparse point: $($item.FullName)"
  }
  if ($item.PSIsContainer) {
    $acl = [Security.AccessControl.DirectorySecurity]::new()
    $acl.SetOwner($identity)
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
      $identity,
      [Security.AccessControl.FileSystemRights]::FullControl,
      ([Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
       [Security.AccessControl.InheritanceFlags]::ObjectInherit),
      [Security.AccessControl.PropagationFlags]::None,
      [Security.AccessControl.AccessControlType]::Allow
    )
  } else {
    $acl = [Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($identity)
    $rule = [Security.AccessControl.FileSystemAccessRule]::new(
      $identity,
      [Security.AccessControl.FileSystemRights]::FullControl,
      [Security.AccessControl.AccessControlType]::Allow
    )
  }
  $acl.SetAccessRuleProtection($true, $false)
  $acl.SetAccessRule($rule)
  Set-Acl -LiteralPath $item.FullName -AclObject $acl
}
[ordered]@{hardened = $true; current_user_sid = $identity.Value; item_count = $items.Count} |
  ConvertTo-Json -Compress
"""


def _powershell() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if executable is None:
        raise RuntimeError("native Windows ACL verification requires PowerShell")
    return executable


def _run_encoded_script(script: str, *, environment: dict[str, str]) -> dict[str, Any]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        process = run_bounded_subprocess(
            [
                _powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            timeout_seconds=120,
            max_stdout_bytes=16 * 1024 * 1024,
            max_stderr_bytes=1024 * 1024,
            environment={**os.environ, **environment},
        )
    except BoundedSubprocessError as error:
        raise RuntimeError("native Windows ACL command failed closed") from error
    if process.returncode != 0:
        error = process.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"native Windows ACL command failed: {error[:1000]}")
    value = strict_json_loads(process.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("native Windows ACL command returned an invalid payload")
    return value


def evaluate_windows_acl_payload(payload: Any) -> dict[str, Any]:
    errors: list[str] = []
    details: list[dict[str, Any]] = []
    if not isinstance(payload, dict) or set(payload) != {"current_user_sid", "entries"}:
        return {
            "schema_version": WINDOWS_ACL_SCHEMA,
            "permissions_verified": False,
            "errors": ["acl_payload_invalid"],
            "entries": [],
        }
    current_sid = payload.get("current_user_sid")
    entries = payload.get("entries")
    if not isinstance(current_sid, str) or not current_sid.startswith("S-"):
        errors.append("current_user_sid_invalid")
    if not isinstance(entries, list) or not entries:
        errors.append("acl_entries_missing")
        entries = []
    allowed_sids = {
        current_sid,
        _SYSTEM_SID,
        _ADMINISTRATORS_SID,
        _CREATOR_OWNER_SID,
    }
    forbidden_sids = {_EVERYONE_SID, _USERS_SID, _AUTHENTICATED_USERS_SID}
    for index, entry in enumerate(entries):
        entry_errors: list[str] = []
        if not isinstance(entry, dict) or set(entry) != {
            "path",
            "kind",
            "owner_sid",
            "reparse_point",
            "inherited_acl",
            "access",
        }:
            errors.append(f"entry_invalid:{index}")
            continue
        path = str(entry.get("path", ""))
        if entry.get("kind") not in {"file", "directory"}:
            entry_errors.append("kind_invalid")
        if entry.get("owner_sid") != current_sid:
            entry_errors.append("owner_sid_mismatch")
        if entry.get("reparse_point") is not False:
            entry_errors.append("reparse_point_present")
        rules = entry.get("access")
        owner_full_control = False
        everyone_rules = 0
        users_rules = 0
        inherited_rules = 0
        if not isinstance(rules, list):
            entry_errors.append("access_rules_invalid")
            rules = []
        for rule in rules:
            if (
                not isinstance(rule, dict)
                or set(rule)
                != {
                    "sid",
                    "access_type",
                    "rights_mask",
                    "inherited",
                    "inheritance_flags",
                    "propagation_flags",
                }
                or not isinstance(rule.get("sid"), str)
                or rule.get("access_type") not in {"Allow", "Deny"}
                or isinstance(rule.get("rights_mask"), bool)
                or not isinstance(rule.get("rights_mask"), int)
                or not isinstance(rule.get("inherited"), bool)
            ):
                entry_errors.append("access_rule_invalid")
                continue
            sid = rule["sid"]
            if rule["inherited"]:
                inherited_rules += 1
            if sid == _EVERYONE_SID:
                everyone_rules += 1
            if sid == _USERS_SID:
                users_rules += 1
            if rule["access_type"] == "Allow" and sid not in allowed_sids:
                entry_errors.append(f"unauthorized_allow:{sid}")
            if rule["access_type"] == "Allow" and sid in forbidden_sids:
                entry_errors.append(f"broad_principal_allow:{sid}")
            if (
                rule["access_type"] == "Allow"
                and sid == current_sid
                and rule["rights_mask"] & 0x1F01FF == 0x1F01FF
            ):
                owner_full_control = True
        if not owner_full_control:
            entry_errors.append("owner_full_control_missing")
        if entry_errors:
            errors.extend(f"{error}:{path}" for error in sorted(set(entry_errors)))
        details.append(
            {
                "path": path,
                "kind": entry.get("kind"),
                "owner_sid": entry.get("owner_sid"),
                "owner_matches_current_user": entry.get("owner_sid") == current_sid,
                "reparse_point": entry.get("reparse_point"),
                "acl_inheritance_enabled": entry.get("inherited_acl"),
                "inherited_rule_count": inherited_rules,
                "users_rule_count": users_rules,
                "everyone_rule_count": everyone_rules,
                "valid": not entry_errors,
            }
        )
    return {
        "schema_version": WINDOWS_ACL_SCHEMA,
        "current_user_sid": current_sid,
        "entry_count": len(entries),
        "owner_sid_verified": bool(entries)
        and all(item["owner_matches_current_user"] for item in details),
        "users_principal_sid": _USERS_SID,
        "everyone_principal_sid": _EVERYONE_SID,
        "reparse_points_absent": bool(entries)
        and all(item["reparse_point"] is False for item in details),
        "permissions_verified": not errors,
        "errors": errors[:1000],
        "errors_truncated": len(errors) > 1000,
        "entries": details[:1000],
        "entries_truncated": len(details) > 1000,
    }


def _protected_paths(root: Path) -> tuple[list[Path], bool]:
    paths: list[Path] = []
    scan_complete = True
    for path in [root, *root.rglob("*")]:
        if len(paths) >= _MAX_ACL_PATHS:
            scan_complete = False
            break
        paths.append(path)
    return paths, scan_complete


def native_windows_acl_report(root: str | Path) -> dict[str, Any]:
    vault_root = Path(root).expanduser().absolute()
    if os.name != "nt":
        return {
            "schema_version": WINDOWS_ACL_SCHEMA,
            "platform": os.name,
            "status": "not_applicable",
            "permissions_verified": False,
            "errors": ["windows_acl_not_applicable"],
            "entries": [],
        }
    if vault_root.is_symlink() or not vault_root.is_dir():
        raise RuntimeError("Windows ACL report requires a regular vault directory")
    paths, scan_complete = _protected_paths(vault_root)
    combined: dict[str, Any] = {"current_user_sid": None, "entries": []}
    for offset in range(0, len(paths), _BATCH_SIZE):
        batch = [str(path) for path in paths[offset : offset + _BATCH_SIZE]]
        encoded_paths = base64.b64encode(json.dumps(batch, ensure_ascii=False).encode()).decode(
            "ascii"
        )
        value = _run_encoded_script(
            _ACL_QUERY_SCRIPT,
            environment={"DEEPLAW_ACL_PATHS_B64": encoded_paths},
        )
        if combined["current_user_sid"] is None:
            combined["current_user_sid"] = value.get("current_user_sid")
        elif combined["current_user_sid"] != value.get("current_user_sid"):
            raise RuntimeError("Windows ACL identity changed during verification")
        batch_entries = value.get("entries")
        if isinstance(batch_entries, dict):
            batch_entries = [batch_entries]
        if not isinstance(batch_entries, list):
            raise RuntimeError("Windows ACL entry payload is invalid")
        combined["entries"].extend(batch_entries)
    report = evaluate_windows_acl_payload(combined)
    if not scan_complete:
        report["errors"].append("acl_scan_bound_exceeded")
        report["permissions_verified"] = False
    return {
        **report,
        "platform": "nt",
        "status": "verified" if report["permissions_verified"] else "failed",
        "scan_complete": scan_complete,
        "files_and_directories_checked": len(paths),
    }


def harden_windows_vault(root: str | Path) -> dict[str, Any]:
    vault_root = Path(root).expanduser().absolute()
    if os.name != "nt":
        return {
            "schema_version": "deeplaw.windows-acl-hardening/v1",
            "platform": os.name,
            "applied": False,
            "reason": "not_applicable",
        }
    if vault_root.is_symlink() or not vault_root.is_dir():
        raise RuntimeError("Windows ACL hardening requires a regular vault directory")
    encoded_root = base64.b64encode(str(vault_root).encode()).decode("ascii")
    result = _run_encoded_script(
        _ACL_HARDEN_SCRIPT,
        environment={"DEEPLAW_ACL_ROOT_B64": encoded_root},
    )
    verification = native_windows_acl_report(vault_root)
    if not verification["permissions_verified"]:
        raise RuntimeError("Windows vault ACL hardening did not pass native verification")
    return {
        "schema_version": "deeplaw.windows-acl-hardening/v1",
        "platform": "nt",
        "applied": True,
        "item_count": result.get("item_count"),
        "current_user_sid": result.get("current_user_sid"),
        "verification": verification,
    }
