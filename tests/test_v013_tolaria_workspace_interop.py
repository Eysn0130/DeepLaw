from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.hosts import run_tolaria_workspace_interop as harness


def _git_fixture(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    checkout = tmp_path / "tolaria"
    (checkout / "mcp-server").mkdir(parents=True)
    (checkout / "LICENSE").write_text("AGPL fixture license\n", encoding="utf-8")
    (checkout / "package.json").write_text(
        json.dumps({"license": "AGPL-3.0-or-later"}),
        encoding="utf-8",
    )
    (checkout / "mcp-server" / "package.json").write_text(
        json.dumps({"type": "module"}),
        encoding="utf-8",
    )
    (checkout / "mcp-server" / "package-lock.json").write_text(
        "{\"name\":\"fixture\"}\n", encoding="utf-8"
    )
    (checkout / "mcp-server" / "tool-service.js").write_text(
        "export const fixture = true;\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.email", "fixture@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(checkout), "config", "user.name", "fixture"], check=True
    )
    subprocess.run(["git", "-C", str(checkout), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(checkout), "commit", "-qm", "fixture"], check=True
    )
    commit = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    hashes = {
        "license_sha256": hashlib.sha256(
            (checkout / "LICENSE").read_bytes()
        ).hexdigest(),
        "package_lock_sha256": hashlib.sha256(
            (checkout / "mcp-server" / "package-lock.json").read_bytes()
        ).hexdigest(),
        "tool_service_sha256": hashlib.sha256(
            (checkout / "mcp-server" / "tool-service.js").read_bytes()
        ).hexdigest(),
    }
    return checkout, commit, hashes


def _probe_fixture(tmp_path: Path) -> Path:
    checkout = tmp_path / "probe-checkout"
    (checkout / "mcp-server").mkdir(parents=True)
    (checkout / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    (checkout / "mcp-server" / "tool-service.js").write_text(
        """
import { readFile, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'

function notePath(root, value) { return path.join(root, ...value.split('/')) }
function parsed(raw, value, mtimeMs) {
  const body = raw.split('---').slice(2).join('---').trim()
  return {
    path: value,
    frontmatter: { aliases: ['tolaria-roundtrip', '往返'] },
    content: body,
    mtimeMs,
  }
}
export function createMcpToolService({ resolveVaultPaths }) {
  return {
    async readNote(args) {
      const root = resolveVaultPaths()[0]
      const target = notePath(root, args.path)
      const [bytes, info] = await Promise.all([readFile(target), stat(target)])
      return parsed(bytes.toString('utf8'), args.path, info.mtimeMs)
    },
    openNoteInEditor() { return { targetPath: '/private/fixture/absolute/path.md' } },
    async updateNote(args) {
      const root = resolveVaultPaths()[0]
      const target = notePath(root, args.path)
      const info = await stat(target)
      if (info.mtimeMs !== args.expectedMtime) throw new Error('stale')
      await writeFile(target, args.content, 'utf8')
      return { path: args.path, absolutePath: '/private/fixture/absolute/path.md' }
    },
  }
}
""",
        encoding="utf-8",
    )
    return checkout


def _report_fixture() -> dict[str, object]:
    protected = [
        {
            "relative_path": relative,
            "policy_denied": True,
            "before_sha256": "a" * 64,
            "after_sha256": "a" * 64,
            "unchanged": True,
            "probe_invoked": False,
        }
        for relative in harness._PROTECTED_PATHS
    ]
    return harness._make_report(
        tolaria={
            "commit": harness.EXPECTED_TOLARIA_COMMIT,
            "license": harness.EXPECTED_TOLARIA_LICENSE,
            **harness.EXPECTED_TOLARIA_HASHES,
            "tracked_worktree_clean": True,
            "exact_commit_verified": True,
            "exact_files_verified": True,
        },
        deeplaw={
            "command_sha256": "b" * 64,
            "observed_version": "deeplaw fixture",
            "version_verified": True,
            "editable_runtime_provenance": "not_verified",
        },
        probe={
            "status": "passed",
            "read_count": 2,
            "open_count": 1,
            "update_count": 1,
            "table_count": 3,
            "alias_count": 2,
            "fenced_block_count": 1,
            "cjk_count": 2,
        },
        protected=protected,
        note_before_sha256="d" * 64,
        note_after_sha256="c" * 64,
    )


def test_exact_commit_mismatch_is_rejected(tmp_path: Path) -> None:
    checkout, commit, hashes = _git_fixture(tmp_path)
    with pytest.raises(harness.HarnessError, match="tolaria_commit_mismatch"):
        harness.verify_tolaria_checkout(
            checkout,
            expected_commit="0" * 40,
            expected_hashes=hashes,
        )
    observed = harness.verify_tolaria_checkout(
        checkout, expected_commit=commit, expected_hashes=hashes
    )
    assert observed["exact_commit_verified"] is True


def test_dirty_tracked_checkout_is_rejected_but_untracked_node_modules_are_irrelevant(
    tmp_path: Path,
) -> None:
    checkout, commit, hashes = _git_fixture(tmp_path)
    (checkout / "LICENSE").write_text("dirty\n", encoding="utf-8")
    (checkout / "mcp-server" / "node_modules").mkdir()
    (checkout / "mcp-server" / "node_modules" / "ignored.js").write_text(
        "ignored", encoding="utf-8"
    )
    with pytest.raises(harness.HarnessError, match="tolaria_tracked_worktree_dirty"):
        harness.verify_tolaria_checkout(
            checkout, expected_commit=commit, expected_hashes=hashes
        )


def test_node_probe_output_is_relative_and_does_not_echo_absolute_ui_paths(tmp_path: Path) -> None:
    checkout = _probe_fixture(tmp_path)
    vault = tmp_path / "vault"
    note = vault / "notes" / "roundtrip.md"
    note.parent.mkdir(parents=True)
    content = harness._synthetic_note()
    note.write_bytes(harness._synthetic_seed_note())
    observed = harness._run_node_probe(checkout, vault, content)
    assert observed["status"] == "passed"
    assert observed["path"] == "notes/roundtrip.md"
    assert str(tmp_path) not in json.dumps(observed)


def test_protected_hash_mutation_is_visible_without_calling_node(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / ".deeplaw").mkdir(parents=True)
    (vault / ".deeplaw" / "ledger.sqlite3").write_bytes(b"ledger")
    canaries = harness._prepare_canaries(vault)
    (vault / "wiki" / "tolaria-read-only.md").write_bytes(b"mutated")
    observed = harness._protected_policy_result(vault, canaries)
    changed = next(item for item in observed if item["relative_path"].startswith("wiki/"))
    assert changed["probe_invoked"] is False
    assert changed["unchanged"] is False


def test_report_tamper_is_rejected_and_happy_report_is_closed() -> None:
    report = _report_fixture()
    harness.validate_report(report)
    tampered = dict(report)
    tampered["status"] = "failed"
    with pytest.raises(harness.HarnessError, match="report_tampered"):
        harness.validate_report(tampered)


def test_policy_rejects_every_protected_root() -> None:
    for relative in harness._PROTECTED_PATHS:
        with pytest.raises((PermissionError, ValueError)):
            harness.validate_editor_write_target("tolaria", relative)
