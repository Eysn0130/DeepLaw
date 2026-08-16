"""External Tolaria workspace interoperability probe.

This module is deliberately a benchmark harness rather than a DeepLaw runtime
adapter.  It pins one already checked-out Tolaria tree, creates a disposable
synthetic workspace, and asks Tolaria's own MCP tool service to perform one
ordinary Markdown note round-trip.  DeepLaw's editor policy is applied before
the external process is called; protected roots are never sent to Tolaria.

The report is intentionally non-release evidence.  In particular, the
``expectedMtime`` passed to Tolaria is only an editor conflict probe and never
stands in for a DeepLaw Revision or Ledger write.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.editor_bridge import validate_editor_write_target
from deeplaw.subprocess_environment import _build_subprocess_environment

EXPECTED_TOLARIA_COMMIT = "ab01faa6773136a58285d04cb81e2587c11bac85"
EXPECTED_TOLARIA_LICENSE = "AGPL-3.0-or-later"
EXPECTED_TOLARIA_HASHES = {
    "license_sha256": "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0",
    "package_lock_sha256": "c5b92024430bccbc812b4db8f5a30f37ca74995da482c5b75ce18672c2033776",
    "tool_service_sha256": "e8e0ffeca3746ef796e5aeafb738ff14425ce908460032265ce92eb7718bfd3c",
}
REPORT_SCHEMA_VERSION = "deeplaw.tolaria-workspace-interop-report/v1"
REPORT_SCHEMA_NAME = "tolaria-workspace-interop-report.v1.schema.json"
DEPENDENCY_SECURITY_STATUS = "known_high_findings_external_not_redistributed"
HIGH_DEPENDENCY_FINDING_COUNT = 6
RIGHTS_BASIS_STATUS = "owner_declared"

_PROTECTED_PATHS = (
    ".deeplaw/ledger.sqlite3",
    "sources/tolaria-read-only.md",
    "knowledge/tolaria-read-only.md",
    "memory/tolaria-read-only.md",
    "wiki/tolaria-read-only.md",
    "canvas/tolaria-read-only.canvas",
)
_NOTE_PATH = "notes/roundtrip.md"
_MARKERS = (
    "TOLARIA_INTEROP_TABLE",
    "tolaria-roundtrip",
    "TOLARIA_FENCED",
    "跨界",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])/(?:Users|home|private|var|tmp|etc|opt|usr|Volumes|"
    r"Applications|Library|System|workspace|root|srv|mnt|data|dev|proc|sys|run)"
    r"(?:/[^/\s<>\"'`]+)+"
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/](?:[^\\/\s<>\"'`]+[\\/])+"
    r"[^\\/\s<>\"'`]+"
)
_FILE_URI = re.compile(r"(?i)\bfile://(?:localhost)?/[A-Za-z0-9._~!$&'()*+,;=:@%/\\-]+")
_SECRET = re.compile(
    r"(?i)(?:-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----|\b(?:api[_-]?key|access[_-]?token|"
    r"password|passwd|client[_-]?secret|secret[_-]?key)\b\s*[:=]\s*[^\s,}]+|"
    r"\b(?:sk-|github_pat_|gh[pousr]_)[A-Za-z0-9_-]{20,})"
)


class HarnessError(RuntimeError):
    """A bounded, non-path-bearing harness failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise HarnessError("canary_missing") from exc


def _has_absolute_path(value: str) -> bool:
    return bool(
        _POSIX_ABSOLUTE_PATH.search(value)
        or _WINDOWS_ABSOLUTE_PATH.search(value)
        or _FILE_URI.search(value)
    )


def _contains_secret(value: str) -> bool:
    return bool(_SECRET.search(value))


def _assert_safe_text(value: str) -> None:
    if _has_absolute_path(value):
        raise HarnessError("absolute_path_leak")
    if _contains_secret(value):
        raise HarnessError("secret_leak")


def _assert_safe_report(report: dict[str, Any]) -> None:
    rendered = _canonical_json(report)
    _assert_safe_text(rendered)


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _schema() -> dict[str, Any]:
    path = _repository() / "contracts" / REPORT_SCHEMA_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError("report_schema_missing") from exc
    if not isinstance(value, dict):
        raise HarnessError("report_schema_invalid")
    Draft202012Validator.check_schema(value)
    return value


def validate_report(report: dict[str, Any]) -> None:
    """Validate structure and the self-addressed report digest."""

    if not isinstance(report, dict):
        raise HarnessError("report_not_object")
    try:
        Draft202012Validator(_schema(), format_checker=FormatChecker()).validate(report)
    except Exception as exc:  # jsonschema uses several concrete exception classes
        raise HarnessError("report_schema_mismatch") from exc
    digest = report.get("report_sha256")
    body = dict(report)
    body.pop("report_sha256", None)
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise HarnessError("report_digest_invalid")
    if digest != _sha256_bytes(_canonical_json(body).encode("utf-8")):
        raise HarnessError("report_tampered")
    _assert_safe_report(report)


def write_report(path: Path, report: dict[str, Any]) -> None:
    """Write one canonical report, refusing to write a malformed/tampered value."""

    validate_report(report)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical_json(report) + "\n", encoding="utf-8")
    except OSError as exc:
        raise HarnessError("report_write_failed") from exc


def _run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float = 90.0,
    max_output_bytes: int = 2 * 1024 * 1024,
) -> subprocess.CompletedProcess[bytes]:
    """Run an external command with a deliberately small, secret-free env."""

    if not argv:
        raise HarnessError("empty_command")
    environment = _build_subprocess_environment()
    environment.setdefault("PATH", os.defpath)
    locale = environment.get("LC_ALL") or environment.get("LANG") or "C.UTF-8"
    environment.setdefault("LANG", locale)
    environment.setdefault("LC_ALL", locale)
    environment.setdefault("LC_CTYPE", locale)
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "CI": "true",
            "NO_COLOR": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError("external_command_failed") from exc
    if len(completed.stdout) > max_output_bytes or len(completed.stderr) > max_output_bytes:
        raise HarnessError("external_output_exceeded")
    return completed


def _git(checkout: Path, *arguments: str) -> bytes:
    result = _run_process(["git", "-C", str(checkout), *arguments], cwd=_repository())
    if result.returncode != 0:
        raise HarnessError("tolaria_git_failed")
    return result.stdout.strip()


def verify_tolaria_checkout(
    checkout: Path,
    *,
    expected_commit: str = EXPECTED_TOLARIA_COMMIT,
    expected_hashes: dict[str, str] = EXPECTED_TOLARIA_HASHES,
) -> dict[str, Any]:
    """Verify the exact frozen Tolaria source and reject tracked dirt."""

    checkout = checkout.resolve()
    if not checkout.is_dir():
        raise HarnessError("tolaria_checkout_missing")
    head = _git(checkout, "rev-parse", "HEAD").decode("ascii", "replace")
    if head != expected_commit:
        raise HarnessError("tolaria_commit_mismatch")
    dirty = _git(checkout, "status", "--porcelain=v1", "--untracked-files=no")
    if dirty:
        raise HarnessError("tolaria_tracked_worktree_dirty")

    files = {
        "license_sha256": checkout / "LICENSE",
        "package_lock_sha256": checkout / "mcp-server" / "package-lock.json",
        "tool_service_sha256": checkout / "mcp-server" / "tool-service.js",
    }
    observed_hashes = {name: _sha256_file(path) for name, path in files.items()}
    if observed_hashes != expected_hashes:
        raise HarnessError("tolaria_source_hash_mismatch")
    package_path = checkout / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError("tolaria_package_invalid") from exc
    if not isinstance(package, dict) or package.get("license") != EXPECTED_TOLARIA_LICENSE:
        raise HarnessError("tolaria_license_mismatch")
    return {
        "commit": head,
        "license": EXPECTED_TOLARIA_LICENSE,
        **observed_hashes,
        "tracked_worktree_clean": True,
        "exact_commit_verified": True,
        "exact_files_verified": True,
    }


def _command_tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise HarnessError("deeplaw_command_invalid") from exc
    if not tokens:
        raise HarnessError("deeplaw_command_empty")
    return tokens


def verify_deeplaw_command(command: str) -> tuple[list[str], dict[str, str]]:
    """Observe ``--version`` without making an editable tree a release claim."""

    tokens = _command_tokens(command)
    result = _run_process([*tokens, "--version"], cwd=_repository())
    if result.returncode != 0:
        raise HarnessError("deeplaw_version_failed")
    output = result.stdout.decode("utf-8", "replace")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        raise HarnessError("deeplaw_version_missing")
    observed_version = lines[0][:200]
    _assert_safe_text(observed_version)
    if not observed_version:
        raise HarnessError("deeplaw_version_missing")
    digest = _sha256_bytes(_canonical_json(tokens).encode("utf-8"))
    return tokens, {
        "command_sha256": digest,
        "observed_version": observed_version,
        "version_verified": True,
        "editable_runtime_provenance": "not_verified",
    }


def _init_vault(tokens: list[str], vault: Path) -> None:
    result = _run_process(
        [
            *tokens,
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "tolaria-interop-synthetic",
            "--scope",
            "project",
        ],
        cwd=_repository(),
    )
    if result.returncode != 0:
        raise HarnessError("deeplaw_workspace_init_failed")
    if not (vault / ".deeplaw" / "ledger.sqlite3").is_file():
        raise HarnessError("deeplaw_ledger_missing")


def _synthetic_note() -> bytes:
    return (
        "---\n"
        "type: \"interop-note\"\n"
        "aliases: [\"tolaria-roundtrip\", \"往返\"]\n"
        "---\n\n"
        "# Tolaria Workspace Round-trip\n\n"
        "TOLARIA_INTEROP_TABLE\n\n"
        "| key | value |\n"
        "| --- | --- |\n"
        "| path | notes/roundtrip.md |\n\n"
        "```python\n"
        "TOLARIA_FENCED = '跨界'\n"
        "```\n\n"
        "中文标记：跨界。\n"
    ).encode()


def _synthetic_seed_note() -> bytes:
    """A distinct, valid user-authored note that the external editor replaces."""

    return (
        b"---\n"
        b"type: \"interop-seed\"\n"
        b"aliases: [\"seed-note\"]\n"
        b"---\n\n"
        b"# Tolaria Seed Note\n\n"
        b"This public synthetic seed is intentionally replaced by the round-trip fixture.\n"
    )


def _prepare_canaries(vault: Path) -> list[dict[str, Any]]:
    marker = b"DeepLaw Tolaria protected synthetic canary\n"
    for relative in _PROTECTED_PATHS:
        path = vault / PurePosixPath(relative)
        if relative == ".deeplaw/ledger.sqlite3":
            if not path.is_file():
                raise HarnessError("deeplaw_ledger_missing")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(marker + relative.encode("utf-8") + b"\n")
    return [
        {
            "relative_path": relative,
            "before_sha256": _sha256_file(vault / PurePosixPath(relative)),
        }
        for relative in _PROTECTED_PATHS
    ]


def _protected_policy_result(vault: Path, canaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the editor policy once per protected target; never call Node here."""

    result: list[dict[str, Any]] = []
    for item in canaries:
        relative = item["relative_path"]
        denied = False
        try:
            validate_editor_write_target("tolaria", relative)
        except (PermissionError, ValueError):
            denied = True
        if not denied:
            raise HarnessError("protected_policy_allowed_write")
        after = _sha256_file(vault / PurePosixPath(relative))
        result.append(
            {
                "relative_path": relative,
                "policy_denied": True,
                "before_sha256": item["before_sha256"],
                "after_sha256": after,
                "unchanged": item["before_sha256"] == after,
                "probe_invoked": False,
            }
        )
    return result


def _probe_path() -> Path:
    return Path(__file__).with_name("tolaria_workspace_probe.mjs")


def _run_node_probe(checkout: Path, vault: Path, note: bytes) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        raise HarnessError("node_unavailable")
    expected_hash = _sha256_bytes(note)
    encoded_note = base64.b64encode(note).decode("ascii")
    encoded_markers = base64.b64encode(
        _canonical_json(list(_MARKERS)).encode("utf-8")
    ).decode("ascii")
    result = _run_process(
        [
            node,
            str(_probe_path()),
            "--tolaria-checkout",
            str(checkout),
            "--vault",
            str(vault),
            "--path",
            _NOTE_PATH,
            "--content-base64",
            encoded_note,
            "--expected-sha256",
            expected_hash,
            "--markers-base64",
            encoded_markers,
        ],
        cwd=_repository(),
        timeout=90,
    )
    stdout = result.stdout.decode("utf-8", "replace")
    stderr = result.stderr.decode("utf-8", "replace")
    # The probe deliberately emits a tiny JSON object.  Treat any path or
    # secret in either stream as a hard failure instead of copying it into the
    # report.
    _assert_safe_text(stdout)
    _assert_safe_text(stderr)
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("node_probe_output_invalid") from exc
    if not isinstance(parsed, dict):
        raise HarnessError("node_probe_output_invalid")
    if result.returncode != 0 or parsed.get("status") != "passed":
        raise HarnessError("node_probe_failed")
    if parsed.get("path") != _NOTE_PATH:
        raise HarnessError("node_probe_path_invalid")
    if parsed.get("after_sha256") != expected_hash:
        raise HarnessError("node_probe_roundtrip_mismatch")
    if parsed.get("before_sha256") == expected_hash:
        raise HarnessError("node_probe_edit_not_observed")
    for field in (
        "before_sha256",
        "after_sha256",
    ):
        if not isinstance(parsed.get(field), str) or not _SHA256.fullmatch(parsed[field]):
            raise HarnessError("node_probe_hash_invalid")
    for field in (
        "read_count",
        "open_count",
        "update_count",
        "table_count",
        "alias_count",
        "fenced_block_count",
        "cjk_count",
    ):
        if not isinstance(parsed.get(field), int) or parsed[field] < 0:
            raise HarnessError("node_probe_count_invalid")
    if parsed["read_count"] != 2 or parsed["open_count"] != 1 or parsed["update_count"] != 1:
        raise HarnessError("node_probe_sequence_invalid")
    if (
        parsed["table_count"] < 2
        or parsed["alias_count"] < 2
        or parsed["fenced_block_count"] < 1
        or parsed["cjk_count"] < 1
    ):
        raise HarnessError("node_probe_fixture_missing")
    return parsed


def _report_id(*parts: str) -> str:
    digest = _sha256_bytes("\n".join(parts).encode("utf-8"))
    return f"tolaria_interop_{digest[:24]}"


def _make_report(
    *,
    tolaria: dict[str, Any],
    deeplaw: dict[str, Any],
    probe: dict[str, Any],
    protected: list[dict[str, Any]],
    note_before_sha256: str,
    note_after_sha256: str,
) -> dict[str, Any]:
    if note_before_sha256 == note_after_sha256:
        raise HarnessError("note_edit_not_observed")
    protected_denial = all(
        item["policy_denied"] and not item["probe_invoked"] for item in protected
    )
    protected_hashes = all(item["unchanged"] for item in protected)
    functional_status = "passed" if protected_denial and protected_hashes else "failed"
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_id": _report_id(
            tolaria["commit"],
            tolaria["tool_service_sha256"],
            deeplaw["command_sha256"],
            note_after_sha256,
        ),
        "status": "passed" if functional_status == "passed" else "failed",
        "tolaria": tolaria,
        "deeplaw": deeplaw,
        "functional_status": functional_status,
        "allowed_note": {
            "relative_path": _NOTE_PATH,
            "policy_status": "passed",
            "node_probe_status": probe["status"],
            "read_count": probe["read_count"],
            "open_count": probe["open_count"],
            "update_count": probe["update_count"],
            "table_count": probe["table_count"],
            "alias_count": probe["alias_count"],
            "fenced_block_count": probe["fenced_block_count"],
            "cjk_count": probe["cjk_count"],
            "before_sha256": note_before_sha256,
            "after_sha256": note_after_sha256,
            "write_performed": True,
        },
        "protected_targets": protected,
        "protected_denial_status": "passed" if protected_denial else "failed",
        "protected_hash_status": "passed" if protected_hashes else "failed",
        "write_performed": True,
        "canonical_ledger_write_performed": False,
        "dependency_security_status": DEPENDENCY_SECURITY_STATUS,
        "high_count": HIGH_DEPENDENCY_FINDING_COUNT,
        "formal_release_evidence_ready": False,
        "qualification_eligible": False,
        "rights_basis_status": RIGHTS_BASIS_STATUS,
        "rights_basis": {
            "status": RIGHTS_BASIS_STATUS,
            "owner_authorization": "Owner-declared same-team authorization",
            "release_file_confirmation": "pending",
            "contributor_confirmation": "pending",
            "summary": "Owner-declared; release file/contributor confirmation pending",
        },
        "no_absolute_paths": True,
        "no_absolute_path_leaks": True,
        "no_secrets": True,
        "no_secret_leaks": True,
        "os_sandbox_proven": False,
        "limitations": [
            "ordinary host flow only; this is not an owner OS sandbox proof",
            "expectedMtime is an external editor conflict probe, not a DeepLaw Revision",
            "no GUI click, canonical reconciliation, security qualification, or release pass "
            "was executed",
        ],
    }
    body = dict(report)
    report["report_sha256"] = _sha256_bytes(_canonical_json(body).encode("utf-8"))
    validate_report(report)
    return report


def run_harness(
    *,
    tolaria_checkout: Path,
    deeplaw_command: str,
) -> dict[str, Any]:
    """Run one isolated synthetic workspace exercise and return its report."""

    tolaria = verify_tolaria_checkout(tolaria_checkout)
    tokens, deeplaw = verify_deeplaw_command(deeplaw_command)
    note = _synthetic_note()
    seed = _synthetic_seed_note()
    with tempfile.TemporaryDirectory(prefix="deeplaw-tolaria-interop-") as temporary:
        workspace = Path(temporary)
        vault = workspace / "vault"
        _init_vault(tokens, vault)
        validate_editor_write_target("tolaria", _NOTE_PATH)
        note_path = vault / PurePosixPath(_NOTE_PATH)
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_bytes(seed)
        note_before = _sha256_file(note_path)
        canaries = _prepare_canaries(vault)
        protected = _protected_policy_result(vault, canaries)
        probe = _run_node_probe(tolaria_checkout.resolve(), vault, note)
        note_after = _sha256_file(note_path)
        if note_after != _sha256_bytes(note):
            raise HarnessError("note_bytes_changed")
        if note_before == note_after:
            raise HarnessError("note_edit_not_observed")
        # Recheck after the external process: protected targets were never
        # handed to Tolaria and must remain byte-for-byte identical.
        protected = _protected_policy_result(vault, canaries)
        report = _make_report(
            tolaria=tolaria,
            deeplaw=deeplaw,
            probe=probe,
            protected=protected,
            note_before_sha256=note_before,
            note_after_sha256=note_after,
        )
    return report


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tolaria-checkout", type=Path, required=True)
    parser.add_argument("--deeplaw-command", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        report = run_harness(
            tolaria_checkout=args.tolaria_checkout,
            deeplaw_command=args.deeplaw_command,
        )
        write_report(args.output, report)
    except HarnessError as exc:
        # Do not echo subprocess stderr, checkout paths, or command arguments.
        print(f"tolaria workspace interop harness failed: {exc.code}", file=sys.stderr)
        return 2
    print(_canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
