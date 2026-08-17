"""Frozen Pass 16 Host continuity task cases and path-free task bindings.

The case file is qualification input, not model output.  This module keeps the
loader deliberately small and deterministic so both Host harnesses consume the
same prompts, checkpoint markers, and wrong-state challenges without copying the
fixture into prompt-building code.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from deeplaw.task_context import build_task_context_binding, normalize_task_context_binding

_CASE_FILE = Path(__file__).with_name("pass16-continuity-task-cases-v1.json")
_SCHEMA_FILE = Path(__file__).resolve().parents[2] / "contracts" / (
    "host-continuity-task-cases.v1.schema.json"
)
SCENARIOS = ("cold_start", "resume_fork", "compaction_forget")
_MARKER = re.compile(r"^PASS16-[A-Z0-9-]{16,100}$")
_OID = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ContinuityCaseError(ValueError):
    """The frozen task-case input or a Host binding is invalid."""


class _LazyCandidatePrompts(Mapping[str, str]):
    """Expose the historical prompt map without reading qualification input on import."""

    def __getitem__(self, scenario: str) -> str:
        if scenario not in SCENARIOS:
            raise KeyError(scenario)
        return candidate_prompt(task_case(scenario))

    def __iter__(self) -> Iterator[str]:
        return iter(SCENARIOS)

    def __len__(self) -> int:
        return len(SCENARIOS)


def lazy_candidate_prompts() -> Mapping[str, str]:
    """Return a mapping that loads qualification cases only when a prompt is requested."""

    return _LazyCandidatePrompts()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_cases(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the immutable qualification input exactly once per call."""

    selected = _CASE_FILE if path is None else Path(path)
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
        schema = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ContinuityCaseError("frozen Pass 16 task cases are unavailable") from exc
    if not isinstance(value, Mapping):
        raise ContinuityCaseError("frozen Pass 16 task cases are not an object")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: tuple(error.path))
    if errors:
        raise ContinuityCaseError("frozen Pass 16 task cases do not satisfy their schema")
    if (
        value.get("status") != "qualification_input_frozen"
        or value.get("model_outputs_seen_before_freeze") is not False
        or value.get("development_tuning_material") is not False
        or value.get("hosts") != ["codex", "opencode"]
    ):
        raise ContinuityCaseError("Pass 16 task-case provenance is not frozen")
    rows = value.get("task_cases")
    if not isinstance(rows, list) or tuple(row.get("scenario") for row in rows) != SCENARIOS:
        raise ContinuityCaseError("Pass 16 task-case scenario order is invalid")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ContinuityCaseError("Pass 16 task case is invalid")
        prompt = row.get("task_prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ContinuityCaseError("Pass 16 task prompt is invalid")
        # Human Gold and score thresholds belong to the evaluator, never to the
        # candidate prompt.  The current frozen prompts intentionally contain
        # neither term; reject them if a future fixture accidentally does.
        lowered = prompt.casefold()
        if "gold" in lowered or "score" in lowered or "expected" in lowered:
            raise ContinuityCaseError("candidate task prompt contains evaluator language")
        for checkpoint_name in ("current_checkpoint", "stale_checkpoint"):
            checkpoint = row.get(checkpoint_name)
            if not isinstance(checkpoint, Mapping) or not _MARKER.fullmatch(
                str(checkpoint.get("marker", ""))
            ):
                raise ContinuityCaseError("checkpoint marker is invalid")
        challenges = row.get("wrong_state_challenges")
        if not isinstance(challenges, list) or {
            item.get("challenge") for item in challenges if isinstance(item, Mapping)
        } != {"stale_checkpoint", "wrong_task_line", "wrong_worktree"}:
            raise ContinuityCaseError("wrong-state challenge set is incomplete")
    return json.loads(json.dumps(value, ensure_ascii=False))


def cases_by_scenario(path: Path | None = None) -> dict[str, dict[str, Any]]:
    rows = load_cases(path).get("task_cases")
    if not isinstance(rows, list):
        raise ContinuityCaseError("Pass 16 task cases are missing")
    result = {str(row["scenario"]): dict(row) for row in rows if isinstance(row, Mapping)}
    if tuple(result) != SCENARIOS:
        raise ContinuityCaseError("Pass 16 task-case scenario mapping is invalid")
    return result


def task_case(scenario: str, path: Path | None = None) -> dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ContinuityCaseError(f"unsupported continuity scenario: {scenario}")
    return cases_by_scenario(path)[scenario]


def marker_values(case: Mapping[str, Any]) -> dict[str, str]:
    """Return every marker used for seeding, without exposing checkpoint text."""

    current = case.get("current_checkpoint")
    stale = case.get("stale_checkpoint")
    challenges = case.get("wrong_state_challenges")
    forgotten = case.get("post_forget_requirement")
    values: dict[str, str] = {}
    if isinstance(current, Mapping):
        values["current"] = str(current["marker"])
    if isinstance(stale, Mapping):
        values["stale"] = str(stale["marker"])
    if isinstance(challenges, list):
        for challenge in challenges:
            if isinstance(challenge, Mapping):
                values[str(challenge["challenge"])] = str(challenge["marker"])
    if isinstance(forgotten, Mapping):
        values["forgotten"] = str(forgotten["forgotten_marker"])
    if not all(_MARKER.fullmatch(value) for value in values.values()):
        raise ContinuityCaseError("Pass 16 marker set is invalid")
    return values


def candidate_prompt(
    case: Mapping[str, Any],
    *,
    phase: str = "current",
    native_host: bool = False,
) -> str:
    """Build a model prompt from the frozen neutral task text.

    Checkpoint bodies and expected scores are deliberately not copied into the
    candidate prompt; they are seeded into governed DeepLaw state and evaluated
    from the returned Provider Capsule.
    """

    prompt = case.get("task_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ContinuityCaseError("task case omitted task_prompt")
    if phase not in {"current", "post_forget"}:
        raise ContinuityCaseError("unsupported candidate prompt phase")
    suffix = (
        " After the owner-directed forget, report the resulting gap explicitly."
        if phase == "post_forget"
        else ""
    )
    context_instruction = (
        "Use only the continuity capsule supplied by the native Host context; do not invoke "
        "any tool or request additional context."
        if native_host
        else (
            "Use exactly one safe read-only knowledge_support context call; retry at most once "
            "only when the first bounded Provider Capsule is insufficient. Set "
            "confirm_no_case_data=true and do not invoke any other tool."
        )
    )
    result = (
        f"{prompt.strip()} {context_instruction} Return exactly one "
        "JSON object and no Markdown: "
        '{"summary":"string","next_step":"string","preserved_decisions":["string"],'
        '"open_gaps":["string"]}. Use no other keys. Keep every string non-empty and at most '
        "200 characters; keep each array to one through three items."
        f"{suffix}"
    )
    lowered = result.casefold()
    if any(term in lowered for term in ("gold", "expected score", "score threshold")):
        raise ContinuityCaseError("candidate prompt contains evaluator language")
    return result


def _git(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContinuityCaseError("temporary Git task binding failed") from exc
    if completed.returncode != 0:
        raise ContinuityCaseError("temporary Git task binding failed")
    return completed.stdout.strip()


def git_binding(
    repository: Path, *, task_line: str, worktree: Path | None = None
) -> dict[str, Any]:
    """Derive a path-free binding from actual Git facts for one task/worktree."""

    repo = Path(repository).resolve(strict=True)
    selected = repo if worktree is None else Path(worktree).resolve(strict=True)
    common_dir_text = _git(selected, "rev-parse", "--git-common-dir")
    base_revision = _git(selected, "rev-parse", "HEAD")
    index = _git(selected, "rev-parse", "--git-path", "index")
    status = _git(selected, "status", "--porcelain=v1", "--untracked-files=all")
    tracked = _git(selected, "ls-files", "-s")
    try:
        git_dir = _git(selected, "rev-parse", "--git-dir")
    except ContinuityCaseError:
        git_dir = ""
    if not _OID.fullmatch(base_revision):
        raise ContinuityCaseError("temporary Git base revision is not an object id")
    common_dir = Path(common_dir_text)
    if not common_dir.is_absolute():
        common_dir = selected / common_dir
    try:
        repository_digest = _sha256(str(common_dir.resolve(strict=True)).encode("utf-8"))
    except (OSError, RuntimeError) as exc:
        raise ContinuityCaseError("temporary Git common directory is unavailable") from exc
    worktree_digest = _sha256(
        _canonical({"git_dir": git_dir, "index": index, "tracked": tracked}).encode("utf-8")
    )
    dirty_digest = _sha256(status.encode("utf-8"))
    value = build_task_context_binding(
        _sha256(f"deeplaw-pass16:{repository_digest}".encode()),
        _sha256(task_line.encode("utf-8")),
        repository_sha256=repository_digest,
        worktree_sha256=worktree_digest,
        base_revision=base_revision,
        dirty_state_sha256=dirty_digest,
    )
    normalized = normalize_task_context_binding(value, allow_none=False)
    if normalized is None:
        raise ContinuityCaseError("Git task binding normalization failed")
    value = normalized
    for field in (
        "task_lineage_sha256",
        "repository_sha256",
        "worktree_sha256",
        "dirty_state_sha256",
        "binding_sha256",
    ):
        if not _DIGEST.fullmatch(str(value[field])):
            raise ContinuityCaseError("Git task binding digest is invalid")
    return value


def binding_sha256(binding: Mapping[str, Any]) -> str:
    try:
        normalized = normalize_task_context_binding(binding, allow_none=False)
    except ValueError as exc:
        raise ContinuityCaseError("task binding digest is inconsistent") from exc
    if normalized is None:
        raise ContinuityCaseError("task binding digest is missing")
    return str(normalized["binding_sha256"])


def forbidden_markers(case: Mapping[str, Any]) -> tuple[str, ...]:
    markers = marker_values(case)
    return tuple(markers[key] for key in ("stale", "wrong_task_line", "wrong_worktree"))
