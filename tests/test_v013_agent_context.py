from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.agent_context import (
    AgentContextError,
    build_agent_context,
    validate_agent_context,
)
from deeplaw.util import canonical_json


def _context(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "task": "Review the selected change",
        "goal": "Identify correctness risks",
        "workspace_identity": "workspace-demo",
        "repository_identity": "repo-demo",
        "commit": "a" * 40,
        "branch": "feature/context",
        "requested_purpose": "verify",
        "scope": "project",
        "max_sensitivity": "private",
        "active_files": ["src/app.py", "tests/test_app.py"],
        "selected_text": "assert value == expected",
        "open_tabs": ["src/app.py"],
        "current_note": "notes/review.md",
        "tool_result_digests": [
            {
                "tool_name": "git",
                "result_type": "status",
                "sha256": "1" * 64,
            }
        ],
        "budget": {"max_tokens": 1000, "max_selected_characters": 100},
    }
    values.update(overrides)
    return build_agent_context(**values)  # type: ignore[arg-type]


def test_contract_is_closed_and_builder_output_is_hash_bound() -> None:
    contract_path = (
        Path(__file__).parents[1]
        / "contracts"
        / "agent-context-envelope.v1.schema.json"
    )
    schema = json.loads(contract_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    envelope = _context()
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(envelope)
    body = {key: value for key, value in envelope.items() if key != "envelope_sha256"}
    assert envelope["envelope_sha256"] == hashlib.sha256(
        canonical_json(body).encode("utf-8")
    ).hexdigest()
    assert envelope["ephemeral"] is True
    assert envelope["persistence_allowed"] is False
    assert envelope["persistence_performed"] is False
    assert envelope["authority"] == "none"
    assert envelope["legal_authority"] is False


def test_builder_sorts_and_deduplicates_collection_inputs_deterministically() -> None:
    left = _context(
        active_files=["tests/test_app.py", "src/app.py"],
        open_tabs=["src/app.py"],
        tool_result_digests=[
            {"tool_name": "git", "result_type": "status", "sha256": "1" * 64}
        ],
    )
    right = _context(
        active_files=["src/app.py", "tests/test_app.py"],
        open_tabs=["src/app.py"],
        tool_result_digests=[
            {"tool_name": "git", "result_type": "status", "sha256": "1" * 64}
        ],
    )
    assert left == right


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/secret.txt",
        "../secret.txt",
        "src/../secret.txt",
        "C:\\secret.txt",
        "src\\app.py",
        "src//app.py",
    ],
)
def test_workspace_paths_fail_closed(path: str) -> None:
    with pytest.raises(AgentContextError):
        _context(active_files=[path])


def test_identity_does_not_leak_absolute_paths() -> None:
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "contracts"
            / "agent-context-envelope.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for identity in ("/Users/owner/project", "\\\\server\\share", "~/project", "C:/project"):
        candidate = _context()
        candidate["workspace_identity"] = identity
        assert list(validator.iter_errors(candidate))
        with pytest.raises(AgentContextError):
            validate_agent_context(candidate)


def test_validator_rejects_unknown_hash_tamper_secret_and_budget() -> None:
    envelope = _context()
    unknown = dict(envelope)
    unknown["chat_summary"] = "not allowed"
    with pytest.raises(AgentContextError):
        validate_agent_context(unknown)

    tampered = dict(envelope)
    tampered["goal"] = "changed"
    with pytest.raises(AgentContextError):
        validate_agent_context(tampered)

    with pytest.raises(AgentContextError):
        _context(task="use sk-live-secret-1234567890")
    with pytest.raises(AgentContextError):
        _context(budget={"max_tokens": 32, "max_selected_characters": 100})


def test_selected_text_budget_and_tool_result_opaque_digest_boundary() -> None:
    with pytest.raises(AgentContextError):
        _context(selected_text="x" * 101)
    with pytest.raises(AgentContextError):
        _context(
            tool_result_digests=[
                {
                    "tool_name": "git",
                    "result_type": "status",
                    "sha256": "1" * 64,
                    "body": "raw command output",
                }
            ]
        )


def test_multiline_selection_goal_unknown_and_restricted_policy() -> None:
    envelope = _context(
        goal=None,
        selected_text="line one\n\tline two",
        max_sensitivity="restricted",
    )
    assert envelope["goal"] is None
    assert envelope["max_sensitivity"] == "restricted"
    with pytest.raises(AgentContextError):
        _context(selected_text="line one\rline two")


def test_no_files_are_created_and_module_has_no_persistence_imports(tmp_path: Path) -> None:
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    _context()
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert before == after
    source = (Path(__file__).parents[1] / "src" / "deeplaw" / "agent_context.py").read_text()
    for forbidden in ("knowledge_store", "knowledge_autonomy", "sqlite", "requests", "urllib"):
        assert forbidden not in source


def test_cli_builds_the_same_ephemeral_agent_context_contract() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "deeplaw",
            "knowledge",
            "--format",
            "json",
            "agent-context",
            "--task",
            "Preview active note context",
            "--workspace-identity",
            "workspace-demo",
            "--repository-identity",
            "repo-demo",
            "--active-file",
            "notes/active.md",
            "--open-tab",
            "notes/active.md",
            "--current-note",
            "notes/active.md",
            "--purpose",
            "answer",
            "--max-tokens",
            "4000",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    envelope = json.loads(completed.stdout)
    assert validate_agent_context(envelope) == envelope
    assert envelope["ephemeral"] is True
    assert envelope["persistence_allowed"] is False
    assert envelope["current_note"] == "notes/active.md"
