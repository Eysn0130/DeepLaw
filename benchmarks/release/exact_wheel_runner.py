"""Install and execute exactly one retained DeepLaw wheel in a fresh venv."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import venv
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

V1_RECEIPT_CONTRACT = "candidate-full-v1"
V2_RECEIPT_CONTRACT = "external-qualification-v2"
V1_SCHEMA_VERSION = "deeplaw.exact-wheel-execution-receipt/v1"
V2_SCHEMA_VERSION = "deeplaw.exact-wheel-execution-receipt/v2"
SCHEMA_VERSION = V2_SCHEMA_VERSION
PACKAGE_NAME = "deeplaw"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_REQUIREMENT_HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})(?=\s|$)")
_REQUIREMENT_OPTION_RE = re.compile(
    r"(?:^|\s)(?:-e|--editable|-r|--requirement|-c|--constraint|-f|--find-links|"
    r"--index-url|--extra-index-url|--no-index|--trusted-host|--no-binary|"
    r"--only-binary|--config-settings)(?:[=\s]|$)",
    re.IGNORECASE,
)
_REQUIREMENT_OPTION_TOKEN_RE = re.compile(
    r"(?:^|\s)--(?!hash=)[A-Za-z][A-Za-z0-9-]*(?:=|\s|$)"
)
_LOCAL_REQUIREMENT_RE = re.compile(
    r"(?:^|[\s])(?:file://|file:|\.{1,2}/|/|~[/\\]|[A-Za-z]:[/\\])",
    re.IGNORECASE,
)
_MAX_REQUIREMENTS_BYTES = 64 * 1024 * 1024
_MAX_PUBLIC_JOURNEY_STDOUT_BYTES = 256 * 1024
_PUBLIC_JOURNEY_QUERY = "DeepLaw synthetic public journey marker"
_PUBLIC_JOURNEY_TASK = "Verify synthetic public journey evidence"
_WHEEL_RE = re.compile(
    r"^deeplaw-(?P<version>[0-9]+\.[0-9]+\.[0-9]+)-[A-Za-z0-9_.-]+\.whl$"
)


class ExactWheelExecutionError(RuntimeError):
    """Raised when exact-wheel execution cannot be proven closed."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ExactWheelExecutionError("receipt input must be a regular non-symlink file")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ExactWheelExecutionError("receipt input could not be read") from exc
    return digest.hexdigest()


def _strict_sha256(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ExactWheelExecutionError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _strict_git_sha(value: str, *, label: str) -> str:
    if not isinstance(value, str) or _GIT_SHA_RE.fullmatch(value) is None:
        raise ExactWheelExecutionError(f"{label} must be a lowercase Git SHA-1 digest")
    return value


def _strict_positive_run(value: int, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ExactWheelExecutionError(f"{label} must be a positive integer")
    return value


def _strict_json_loads(payload: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ExactWheelExecutionError("runtime probe returned duplicate JSON keys")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ExactWheelExecutionError(f"runtime probe returned non-finite JSON value: {value}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ExactWheelExecutionError("runtime probe returned invalid JSON") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExactWheelExecutionError("receipt is not canonical JSON") from exc


def _repository_root(repository: Path) -> Path:
    if repository.is_symlink() or not repository.is_dir():
        raise ExactWheelExecutionError("repository must be a regular directory")
    try:
        return repository.resolve(strict=True)
    except OSError as exc:
        raise ExactWheelExecutionError("repository could not be resolved") from exc


def _candidate_wheel(
    candidate_dir: Path,
    *,
    expected_wheel_sha256: str,
    expected_version: str,
) -> Path:
    _strict_sha256(expected_wheel_sha256, label="expected wheel SHA-256")
    if _SEMVER_RE.fullmatch(expected_version) is None:
        raise ExactWheelExecutionError("expected package version is not semver")
    if candidate_dir.is_symlink() or not candidate_dir.is_dir():
        raise ExactWheelExecutionError("Candidate Full directory must be a regular directory")
    try:
        directory = candidate_dir.resolve(strict=True)
        entries = list(directory.iterdir())
    except OSError as exc:
        raise ExactWheelExecutionError("Candidate Full directory could not be read") from exc
    wheels = [entry for entry in entries if entry.name.endswith(".whl")]
    if len(wheels) != 1:
        raise ExactWheelExecutionError("Candidate Full directory must contain exactly one wheel")
    wheel = wheels[0]
    if wheel.is_symlink() or not wheel.is_file():
        raise ExactWheelExecutionError("Candidate Full wheel must be regular and non-symlink")
    match = _WHEEL_RE.fullmatch(wheel.name)
    if match is None or match.group("version") != expected_version:
        raise ExactWheelExecutionError("Candidate Full wheel filename/version mismatch")
    observed_sha256 = _sha256_file(wheel)
    if observed_sha256 != expected_wheel_sha256:
        raise ExactWheelExecutionError("Candidate Full wheel hash mismatch")
    if wheel.stat().st_size < 1:
        raise ExactWheelExecutionError("Candidate Full wheel is empty")
    return wheel


def _candidate_requirements(
    candidate_dir: Path,
    *,
    requirements_filename: str,
    expected_requirements_sha256: str,
) -> tuple[Path, bytes]:
    _strict_sha256(expected_requirements_sha256, label="expected requirements SHA-256")
    if (
        not isinstance(requirements_filename, str)
        or _SAFE_FILENAME_RE.fullmatch(requirements_filename) is None
        or requirements_filename in {".", ".."}
    ):
        raise ExactWheelExecutionError("requirements filename must be a safe candidate file name")
    if Path(requirements_filename).name != requirements_filename:
        raise ExactWheelExecutionError("requirements file must be directly inside Candidate Full")
    selected = candidate_dir.resolve(strict=True) / requirements_filename
    if selected.is_symlink() or not selected.is_file():
        raise ExactWheelExecutionError(
            "requirements input must be a regular non-symlink file in Candidate Full"
        )
    try:
        raw = selected.read_bytes()
    except OSError as exc:
        raise ExactWheelExecutionError("requirements input could not be read") from exc
    if not 1 <= len(raw) <= _MAX_REQUIREMENTS_BYTES:
        raise ExactWheelExecutionError("requirements input exceeds its byte bound")
    if _sha256_bytes(raw) != expected_requirements_sha256:
        raise ExactWheelExecutionError("requirements hash mismatch")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExactWheelExecutionError("requirements input must be strict UTF-8") from exc
    _validate_requirements_text(text)
    return selected, raw


def _stage_verified_requirements(raw: bytes) -> tuple[Path, Path]:
    """Stage validated requirement bytes in an owner-only private directory."""

    try:
        stage_dir = Path(tempfile.mkdtemp(prefix="deeplaw-exact-wheel-requirements-"))
        staged = stage_dir / "candidate-requirements.txt"
        with staged.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        staged.chmod(0o600)
    except (OSError, ValueError) as exc:
        if "stage_dir" in locals():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise ExactWheelExecutionError(
            "validated requirements private staging failed"
        ) from exc
    return stage_dir, staged


def _verify_candidate_requirements_unchanged(
    requirements: Path,
    expected_raw: bytes,
) -> None:
    if requirements.is_symlink() or not requirements.is_file():
        raise ExactWheelExecutionError("Candidate requirements changed during installation")
    try:
        current_raw = requirements.read_bytes()
    except OSError as exc:
        raise ExactWheelExecutionError(
            "Candidate requirements could not be revalidated after installation"
        ) from exc
    if current_raw != expected_raw:
        raise ExactWheelExecutionError("Candidate requirements changed during installation")


def _validate_requirements_text(text: str) -> None:
    """Accept only hash-pinned package records from a locked requirements export."""

    if "\x00" in text:
        raise ExactWheelExecutionError("requirements input contains a NUL byte")
    logical_lines: list[str] = []
    pending = ""
    for physical in text.splitlines():
        line = physical.split("#", 1)[0].strip()
        if not line:
            continue
        if line.endswith("\\"):
            pending = f"{pending} {line[:-1].rstrip()}".strip()
            continue
        logical = f"{pending} {line}".strip() if pending else line
        pending = ""
        logical_lines.append(logical)
    if pending:
        raise ExactWheelExecutionError("requirements input has a dangling continuation")
    if not logical_lines:
        raise ExactWheelExecutionError("requirements input has no package records")

    for line in logical_lines:
        if "\\" in line:
            raise ExactWheelExecutionError("requirements input contains an unsafe continuation")
        if _REQUIREMENT_OPTION_RE.search(line):
            raise ExactWheelExecutionError(
                "requirements input contains an unsupported installer option"
            )
        if _REQUIREMENT_OPTION_TOKEN_RE.search(line):
            raise ExactWheelExecutionError(
                "requirements input contains an unsupported installer option"
            )
        if _LOCAL_REQUIREMENT_RE.search(line) or "://" in line or " @ " in line:
            raise ExactWheelExecutionError(
                "requirements input contains a local or direct URL requirement"
            )
        if "==" not in line:
            raise ExactWheelExecutionError(
                "requirements input must contain an exact pinned package version"
            )
        hash_tokens = re.findall(r"--hash=([^\s]+)", line)
        if not hash_tokens or any(
            _REQUIREMENT_HASH_RE.fullmatch(f"--hash={token}") is None
            for token in hash_tokens
        ):
            raise ExactWheelExecutionError(
                "every locked requirement must include a lowercase sha256 hash"
            )


def _candidate_wheelhouse(candidate_dir: Path) -> Path | None:
    wheelhouse = candidate_dir.resolve(strict=True) / "wheelhouse"
    if not wheelhouse.exists():
        return None
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise ExactWheelExecutionError("dependency wheelhouse must be a regular directory")
    try:
        entries = list(wheelhouse.iterdir())
    except OSError as exc:
        raise ExactWheelExecutionError("dependency wheelhouse could not be read") from exc
    wheels = []
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise ExactWheelExecutionError("dependency wheelhouse contains an unsafe file")
        if not entry.name.endswith(".whl"):
            raise ExactWheelExecutionError(
                "dependency wheelhouse may contain only regular wheel files"
            )
        wheels.append(entry)
    if not wheels:
        raise ExactWheelExecutionError("dependency wheelhouse contains no wheels")
    return wheelhouse


def _venv_python(venv_path: Path) -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    executable = venv_path / relative
    if not executable.is_file():
        raise ExactWheelExecutionError("isolated venv Python executable is unavailable")
    return executable


def _safe_environment(venv_python: Path) -> dict[str, str]:
    bin_dir = venv_python.parent
    environment = {
        "PATH": str(bin_dir),
        "PYTHONNOUSERSITE": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "LC_ALL": "C",
        "LANG": "C",
    }
    if os.name == "nt":
        for name in ("SYSTEMROOT", "WINDIR", "PATHEXT"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
    return environment


def _create_venv(venv_path: Path) -> Path:
    try:
        venv.EnvBuilder(
            with_pip=False,
            system_site_packages=False,
            clear=False,
            symlinks=os.name != "nt",
        ).create(venv_path)
        python_executable = _venv_python(venv_path)
        completed = subprocess.run(
            [
                str(python_executable),
                "-I",
                "-m",
                "ensurepip",
                "--upgrade",
                "--default-pip",
            ],
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("isolated venv creation failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("isolated venv pip bootstrap failed")
    return python_executable


def _install_requirements(
    *,
    python_executable: Path,
    requirements: Path,
    venv_path: Path,
    wheelhouse: Path | None,
) -> str:
    command = [
        str(python_executable),
        "-I",
        "-m",
        "pip",
        "install",
        "--require-hashes",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--force-reinstall",
    ]
    if wheelhouse is None:
        command.extend(("--index-url", "https://pypi.org/simple"))
        mode = "fixed_pypi_index"
    else:
        command.extend(("--no-index", "--find-links", str(wheelhouse)))
        mode = "candidate_wheelhouse"
    command.extend(("-r", str(requirements)))
    try:
        completed = subprocess.run(
            command,
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("locked requirements installation failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("locked requirements installation failed")
    return mode


def _install_wheel(*, python_executable: Path, wheel: Path, venv_path: Path) -> None:
    try:
        completed = subprocess.run(
            [
                str(python_executable),
                "-I",
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--force-reinstall",
                str(wheel),
            ],
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("exact wheel installation failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("exact wheel installation failed")


def _run_version(
    *, console_entrypoint: Path, venv_path: Path, expected_version: str, python_executable: Path
) -> dict[str, Any]:
    argv = ["deeplaw", "--version"]
    try:
        completed = subprocess.run(
            [str(console_entrypoint), "--version"],
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("isolated deeplaw --version execution failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("isolated deeplaw --version execution failed")
    try:
        stdout = completed.stdout.decode("utf-8")
        stderr = completed.stderr.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExactWheelExecutionError("isolated deeplaw --version output is not UTF-8") from exc
    expected_line = f"deeplaw {expected_version}"
    if stdout not in {f"{expected_line}\n", f"{expected_line}\r\n"} or stderr:
        raise ExactWheelExecutionError("isolated deeplaw --version output differs")
    return {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout_sha256": _sha256_bytes(completed.stdout),
        "stdout_bytes": len(completed.stdout),
        "stdout_path_class": "sanitized_stdout",
    }


def _public_journey_budget(
    value: Mapping[str, Any],
    *,
    max_characters: int,
    selected_characters: int,
    provider_payload_bytes: int,
    local_payload_bytes: int,
) -> dict[str, int]:
    budget = value.get("budget")
    if not isinstance(budget, Mapping):
        raise ExactWheelExecutionError("public journey budget is missing")
    reported_selected_characters = budget.get("selected_characters")
    observed_max_characters = budget.get("max_characters")
    max_provider_characters = budget.get("max_provider_characters")
    if any(
        isinstance(item, bool) or not isinstance(item, int)
        for item in (
            reported_selected_characters,
            observed_max_characters,
            max_provider_characters,
        )
    ):
        raise ExactWheelExecutionError("public journey budget is invalid")
    if (
        observed_max_characters != max_characters
        or reported_selected_characters != selected_characters
        or selected_characters < 0
        or selected_characters > observed_max_characters
        or max_provider_characters != 65_536
        or provider_payload_bytes < 0
        or provider_payload_bytes > max_provider_characters
        or local_payload_bytes < 0
        or local_payload_bytes > 262_144
    ):
        raise ExactWheelExecutionError("public journey budget exceeds its hard bound")
    return {
        "max_characters": observed_max_characters,
        "selected_characters": selected_characters,
        "provider_payload_bytes": provider_payload_bytes,
        "provider_hard_limit_bytes": 65_536,
        "local_payload_bytes": local_payload_bytes,
        "local_hard_limit_bytes": 262_144,
    }


def _derived_selected_characters(value: Mapping[str, Any]) -> int:
    statements = value.get("statements")
    evidence = value.get("evidence")
    if not isinstance(statements, list) or not isinstance(evidence, list):
        raise ExactWheelExecutionError("public journey selected content is missing")
    selected = 0
    for item in statements:
        if not isinstance(item, Mapping) or not isinstance(item.get("statement_text"), str):
            raise ExactWheelExecutionError("public journey statement content is invalid")
        selected += len(item["statement_text"])
    for item in evidence:
        if not isinstance(item, Mapping) or not isinstance(item.get("excerpt"), str):
            raise ExactWheelExecutionError("public journey evidence content is invalid")
        selected += len(item["excerpt"])
    return selected


def _validate_public_query(value: Mapping[str, Any]) -> dict[str, int]:
    if (
        value.get("purpose") != "verify"
        or value.get("policy_id") != "evidence-first-v1"
        or value.get("write_performed") is not False
    ):
        raise ExactWheelExecutionError("public journey query policy/status is invalid")
    plan = value.get("query_plan")
    controls = plan.get("retrieval_controls") if isinstance(plan, Mapping) else None
    if (
        not isinstance(plan, Mapping)
        or plan.get("schema_version") != "deeplaw.knowledge-query-plan/v6"
        or plan.get("policy_id") != "evidence-first-v1"
        or not isinstance(controls, Mapping)
        or controls.get("retrieval_mode") != "lexical"
    ):
        raise ExactWheelExecutionError("public journey query plan is invalid")
    capsule = value.get("capsule")
    if not isinstance(capsule, Mapping):
        raise ExactWheelExecutionError("public journey query capsule is missing")
    selected_characters = _derived_selected_characters(value)
    provider_payload_bytes = len(_canonical_json(capsule))
    local_payload_bytes = len(_canonical_json(value))
    return _public_journey_budget(
        value,
        max_characters=2_000,
        selected_characters=selected_characters,
        provider_payload_bytes=provider_payload_bytes,
        local_payload_bytes=local_payload_bytes,
    )


def _validate_public_context(value: Mapping[str, Any]) -> dict[str, int]:
    if (
        value.get("purpose") != "verify"
        or value.get("policy_id") != "evidence-first-v1"
        or value.get("write_performed") is not False
    ):
        raise ExactWheelExecutionError("public journey context policy/status is invalid")
    provider = value.get("provider_capsule")
    budget = value.get("budget")
    if not isinstance(provider, Mapping) or not isinstance(budget, Mapping):
        raise ExactWheelExecutionError("public journey context budget is missing")
    delivery = provider.get("delivery")
    provider_capsule = provider.get("capsule")
    provider_bytes = budget.get("provider_payload_bytes")
    if not isinstance(provider_capsule, Mapping):
        raise ExactWheelExecutionError("public journey context provider capsule is invalid")
    derived_provider_bytes = len(_canonical_json(provider_capsule))
    if (
        not isinstance(delivery, Mapping)
        or isinstance(provider_bytes, bool)
        or not isinstance(provider_bytes, int)
        or provider_bytes != derived_provider_bytes
        or delivery.get("provider_content_bytes") != derived_provider_bytes
    ):
        raise ExactWheelExecutionError("public journey context provider budget is invalid")
    return _public_journey_budget(
        value,
        max_characters=2_000,
        selected_characters=_derived_selected_characters(value),
        provider_payload_bytes=provider_bytes,
        local_payload_bytes=len(_canonical_json(value)),
    )


def _run_public_step(
    *,
    name: str,
    console_entrypoint: Path,
    python_executable: Path,
    journey_root: Path,
    actual_argv: list[str],
    receipt_argv: list[str],
    expected_schema_version: str,
    validator: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if any(
        value.startswith(("/", "\\")) or re.fullmatch(r"[A-Za-z]:[\\/].*", value)
        for value in receipt_argv
    ):
        raise ExactWheelExecutionError("public journey receipt argv contains a path")
    try:
        completed = subprocess.run(
            [str(console_entrypoint), *actual_argv],
            cwd=journey_root,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError(f"public journey {name} execution failed") from exc
    if completed.returncode != 0 or completed.stderr:
        raise ExactWheelExecutionError(f"public journey {name} execution failed")
    if not 1 <= len(completed.stdout) <= _MAX_PUBLIC_JOURNEY_STDOUT_BYTES:
        raise ExactWheelExecutionError(f"public journey {name} stdout is invalid or oversized")
    try:
        stdout = completed.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExactWheelExecutionError(f"public journey {name} stdout is not UTF-8") from exc
    value = _strict_json_loads(stdout)
    if not isinstance(value, dict) or value.get("schema_version") != expected_schema_version:
        raise ExactWheelExecutionError(f"public journey {name} returned an invalid JSON result")
    budget = validator(value) if validator is not None else None
    return (
        {
            "name": name,
            "status": "passed",
            "exit_code": completed.returncode,
            "argv": receipt_argv,
            "stdout_sha256": _sha256_bytes(completed.stdout),
            "stdout_bytes": len(completed.stdout),
            "stdout_path_class": "sanitized_stdout",
            "output_schema_version": value["schema_version"],
            "budget": budget,
        },
        value,
    )


def _run_public_journey(
    *,
    console_entrypoint: Path,
    python_executable: Path,
    venv_path: Path,
    repository: Path,
) -> dict[str, Any]:
    try:
        console_entrypoint.resolve(strict=True).relative_to(venv_path.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ExactWheelExecutionError(
            "public journey console executable is outside the isolated venv"
        ) from exc
    try:
        journey_root = Path(tempfile.mkdtemp(prefix="deeplaw-exact-wheel-journey-"))
    except OSError as exc:
        raise ExactWheelExecutionError("public journey root could not be created") from exc
    try:
        journey_root = journey_root.resolve(strict=True)
        if repository == journey_root or repository in journey_root.parents:
            raise ExactWheelExecutionError("public journey root must be outside the repository")
        vault = journey_root / "vault"
        source = journey_root / "source.md"
        output = journey_root / "capsule.json"
        source.write_text(
            "DeepLaw synthetic public journey marker.\n"
            "The bounded journey preserves exact source evidence.\n",
            encoding="utf-8",
        )
        common = ["knowledge", "--format", "json"]
        init_step, _ = _run_public_step(
            name="knowledge_init",
            console_entrypoint=console_entrypoint,
            python_executable=python_executable,
            journey_root=journey_root,
            actual_argv=[
                *common,
                "init",
                "--vault",
                str(vault),
                "--name",
                "public-journey",
                "--scope",
                "project",
            ],
            receipt_argv=[
                "deeplaw",
                *common,
                "init",
                "--vault",
                "<vault>",
                "--name",
                "<redacted>",
                "--scope",
                "project",
            ],
            expected_schema_version="deeplaw.knowledge-vault-initialization/v2",
        )
        source_add_step, source_add = _run_public_step(
            name="source_add",
            console_entrypoint=console_entrypoint,
            python_executable=python_executable,
            journey_root=journey_root,
            actual_argv=[
                *common,
                "source",
                "add",
                "--vault",
                str(vault),
                "--source",
                str(source),
                "--source-kind",
                "document",
                "--pdf-fallback",
                "off",
                "--typed-extraction",
                "off",
                "--confirm-no-case-data",
            ],
            receipt_argv=[
                "deeplaw",
                *common,
                "source",
                "add",
                "--vault",
                "<vault>",
                "--source",
                "<source>",
                "--source-kind",
                "document",
                "--pdf-fallback",
                "off",
                "--typed-extraction",
                "off",
                "--confirm-no-case-data",
            ],
            expected_schema_version="deeplaw.knowledge-ingest/v1",
        )
        source_value = source_add.get("source")
        source_id = source_value.get("source_id") if isinstance(source_value, Mapping) else None
        if not isinstance(source_id, str) or not source_id:
            raise ExactWheelExecutionError("public journey source add did not return a source id")
        source_verify_step, source_verify = _run_public_step(
            name="source_verify",
            console_entrypoint=console_entrypoint,
            python_executable=python_executable,
            journey_root=journey_root,
            actual_argv=[
                *common,
                "source",
                "verify",
                "--vault",
                str(vault),
                "--source-id",
                source_id,
            ],
            receipt_argv=[
                "deeplaw",
                *common,
                "source",
                "verify",
                "--vault",
                "<vault>",
                "--source-id",
                "<source>",
            ],
            expected_schema_version="deeplaw.knowledge-source-verification/v1",
        )
        if (
            source_verify.get("valid") is not True
            or source_verify.get("database_integrity_valid") is not True
            or not isinstance(source_verify.get("file"), Mapping)
            or source_verify["file"].get("valid") is not True
        ):
            raise ExactWheelExecutionError("public journey source verification did not pass")
        query_step, _ = _run_public_step(
            name="evidence_first_query",
            console_entrypoint=console_entrypoint,
            python_executable=python_executable,
            journey_root=journey_root,
            actual_argv=[
                *common,
                "query",
                "--vault",
                str(vault),
                "--query",
                _PUBLIC_JOURNEY_QUERY,
                "--purpose",
                "verify",
                "--policy",
                "evidence-first-v1",
                "--scope",
                "project",
                "--max-sensitivity",
                "private",
                "--limit",
                "2",
                "--max-chars",
                "2000",
                "--max-tokens",
                "256",
                "--max-sources",
                "2",
                "--graph-hops",
                "0",
                "--retrieval-mode",
                "lexical",
                "--query-plan-version",
                "6",
                "--capsule-projection",
                "compact",
            ],
            receipt_argv=[
                "deeplaw",
                *common,
                "query",
                "--vault",
                "<vault>",
                "--query",
                "<redacted>",
                "--purpose",
                "verify",
                "--policy",
                "evidence-first-v1",
                "--scope",
                "project",
                "--max-sensitivity",
                "private",
                "--limit",
                "2",
                "--max-chars",
                "2000",
                "--max-tokens",
                "256",
                "--max-sources",
                "2",
                "--graph-hops",
                "0",
                "--retrieval-mode",
                "lexical",
                "--query-plan-version",
                "6",
                "--capsule-projection",
                "compact",
            ],
            expected_schema_version="deeplaw.purpose-aware-retrieval/v3",
            validator=_validate_public_query,
        )
        context_step, context = _run_public_step(
            name="bounded_context",
            console_entrypoint=console_entrypoint,
            python_executable=python_executable,
            journey_root=journey_root,
            actual_argv=[
                *common,
                "context",
                "--vault",
                str(vault),
                "--task",
                _PUBLIC_JOURNEY_TASK,
                "--purpose",
                "verify",
                "--policy",
                "evidence-first-v1",
                "--scope",
                "project",
                "--max-sensitivity",
                "private",
                "--max-items",
                "2",
                "--max-chars",
                "2000",
                "--max-tokens",
                "256",
                "--max-sources",
                "2",
                "--graph-hops",
                "0",
                "--retrieval-mode",
                "lexical",
                "--query-plan-version",
                "6",
                "--capsule-projection",
                "compact",
                "--confirm-no-case-data",
                "--output",
                str(output),
            ],
            receipt_argv=[
                "deeplaw",
                *common,
                "context",
                "--vault",
                "<vault>",
                "--task",
                "<redacted>",
                "--purpose",
                "verify",
                "--policy",
                "evidence-first-v1",
                "--scope",
                "project",
                "--max-sensitivity",
                "private",
                "--max-items",
                "2",
                "--max-chars",
                "2000",
                "--max-tokens",
                "256",
                "--max-sources",
                "2",
                "--graph-hops",
                "0",
                "--retrieval-mode",
                "lexical",
                "--query-plan-version",
                "6",
                "--capsule-projection",
                "compact",
                "--confirm-no-case-data",
                "--output",
                "<output>",
            ],
            expected_schema_version="deeplaw.knowledge-capsule/v3",
            validator=_validate_public_context,
        )
        if (
            output.is_symlink()
            or not output.is_file()
            or output.stat().st_size > 262_144
        ):
            raise ExactWheelExecutionError("public journey context output is unavailable")
        try:
            output_value = _strict_json_loads(output.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise ExactWheelExecutionError("public journey context output is invalid") from exc
        if not isinstance(output_value, dict) or output_value.get("schema_version") != (
            "deeplaw.knowledge-capsule/v3"
        ):
            raise ExactWheelExecutionError("public journey context output schema is invalid")
        if output_value != context:
            raise ExactWheelExecutionError("public journey context output differs from stdout")
        return {
            "journey_status": "passed",
            "journey_root_path_class": "ephemeral_journey_root",
            "step_count": 5,
            "steps": [
                init_step,
                source_add_step,
                source_verify_step,
                query_step,
                context_step,
            ],
            "network_policy": {
                "network_access": "not_requested",
                "model_sidecar": False,
                "environment_allowlist": "minimal",
            },
        }
    finally:
        shutil.rmtree(journey_root, ignore_errors=True)


def _probe_script() -> str:
    return r'''
import importlib.metadata
import importlib.util
import json
import pathlib
import platform
import sys

import deeplaw

origin = pathlib.Path(deeplaw.__file__).resolve()
site_packages = [
    pathlib.Path(item).resolve()
    for item in sys.path
    if isinstance(item, str) and "site-packages" in pathlib.Path(item).parts
]
distribution = importlib.metadata.distribution("deeplaw")
entrypoints = [
    item
    for item in distribution.entry_points
    if item.group == "console_scripts" and item.name == "deeplaw"
]
if len(entrypoints) != 1:
    raise RuntimeError("expected one deeplaw console entrypoint")
entrypoint = entrypoints[0]
module_name, _, _ = entrypoint.value.partition(":")
entry_spec = importlib.util.find_spec(module_name)
entry_origin = (
    pathlib.Path(entry_spec.origin).resolve()
    if entry_spec and entry_spec.origin
    else None
)
print(json.dumps({
    "python_implementation": sys.implementation.name,
    "python_version": platform.python_version(),
    "python_executable": str(pathlib.Path(sys.executable)),
    "site_packages": [str(item) for item in site_packages],
    "import_file": str(origin),
    "distribution_name": distribution.metadata["Name"],
    "distribution_version": distribution.version,
    "entrypoint_name": entrypoint.name,
    "entrypoint_group": entrypoint.group,
    "entrypoint_value": entrypoint.value,
    "entrypoint_file": str(entry_origin) if entry_origin else None,
}, sort_keys=True, separators=(",", ":")))
'''


def _probe_runtime(*, python_executable: Path, venv_path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [str(python_executable), "-I", "-c", _probe_script()],
            cwd=venv_path,
            env=_safe_environment(python_executable),
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExactWheelExecutionError("isolated wheel runtime probe failed") from exc
    if completed.returncode != 0:
        raise ExactWheelExecutionError("isolated wheel runtime probe failed")
    value = _strict_json_loads(completed.stdout)
    if not isinstance(value, dict):
        raise ExactWheelExecutionError("isolated wheel runtime probe was not an object")
    return value


def _console_entrypoint(venv_path: Path) -> Path:
    relative = Path("Scripts/deeplaw.exe") if os.name == "nt" else Path("bin/deeplaw")
    selected = venv_path / relative
    if selected.is_symlink() or not selected.is_file():
        raise ExactWheelExecutionError(
            "installed deeplaw console executable is unavailable or unsafe"
        )
    return selected.resolve(strict=True)


def _relative_to_site_packages(
    path: Path, site_packages: list[Path], *, label: str
) -> tuple[Path, Path]:
    resolved = path.resolve(strict=True)
    for root in site_packages:
        try:
            return resolved.relative_to(root), root
        except ValueError:
            continue
    raise ExactWheelExecutionError(f"{label} did not resolve inside isolated site-packages")


def _validate_probe(
    observation: Mapping[str, Any],
    *,
    expected_version: str,
    repository: Path,
    venv_path: Path,
    expected_python: Path,
) -> dict[str, Any]:
    required = {
        "python_implementation",
        "python_version",
        "python_executable",
        "site_packages",
        "import_file",
        "distribution_name",
        "distribution_version",
        "entrypoint_name",
        "entrypoint_group",
        "entrypoint_value",
        "entrypoint_file",
    }
    if set(observation) != required:
        raise ExactWheelExecutionError("isolated wheel runtime probe fields are incomplete")
    if observation["distribution_name"] != PACKAGE_NAME:
        raise ExactWheelExecutionError("installed distribution name mismatch")
    if observation["distribution_version"] != expected_version:
        raise ExactWheelExecutionError("installed distribution version mismatch")
    if (
        observation["entrypoint_name"] != PACKAGE_NAME
        or observation["entrypoint_group"] != "console_scripts"
    ):
        raise ExactWheelExecutionError("installed console entrypoint mismatch")
    if (
        not isinstance(observation["entrypoint_value"], str)
        or not observation["entrypoint_value"].startswith("deeplaw.")
    ):
        raise ExactWheelExecutionError("installed console entrypoint target is invalid")
    if not isinstance(observation["site_packages"], list) or not observation["site_packages"]:
        raise ExactWheelExecutionError("isolated site-packages path is missing")
    try:
        site_packages = [Path(item).resolve(strict=True) for item in observation["site_packages"]]
        import_file = Path(observation["import_file"])
        entrypoint_file = Path(observation["entrypoint_file"])
        observed_python = Path(observation["python_executable"])
    except (TypeError, ValueError, OSError) as exc:
        raise ExactWheelExecutionError("isolated runtime probe paths are invalid") from exc
    if any(not item.is_dir() for item in site_packages):
        raise ExactWheelExecutionError("isolated site-packages path is not a directory")
    repository = repository.resolve(strict=True)
    venv_path = venv_path.resolve(strict=True)
    for path, label in (
        (import_file, "deeplaw import origin"),
        (entrypoint_file, "deeplaw console entrypoint"),
    ):
        if not path.is_absolute():
            raise ExactWheelExecutionError(f"{label} must be an absolute probe path")
        if path.is_symlink() or not path.is_file():
            raise ExactWheelExecutionError(f"{label} is not a regular file")
        if repository == path or repository in path.parents:
            raise ExactWheelExecutionError(f"{label} resolves to checkout source")
    try:
        if not observed_python.is_absolute():
            raise ExactWheelExecutionError("venv Python executable must be an absolute probe path")
        python_relative = observed_python.relative_to(venv_path)
    except ValueError as exc:
        raise ExactWheelExecutionError("Python executable is outside isolated venv") from exc
    if not python_relative.parts:
        raise ExactWheelExecutionError("Python executable path class is invalid")
    try:
        python_executable = observed_python.resolve(strict=True)
        expected_python = expected_python.resolve(strict=True)
    except OSError as exc:
        raise ExactWheelExecutionError("venv Python executable is unavailable") from exc
    if python_executable != expected_python:
        raise ExactWheelExecutionError("runtime probe used a different Python executable")
    if repository == python_executable or repository in python_executable.parents:
        raise ExactWheelExecutionError("venv Python executable resolves to checkout source")
    import_relative, import_root = _relative_to_site_packages(
        import_file, site_packages, label="deeplaw import origin"
    )
    entrypoint_relative, entrypoint_root = _relative_to_site_packages(
        entrypoint_file, site_packages, label="deeplaw console entrypoint"
    )
    if import_root != entrypoint_root:
        raise ExactWheelExecutionError("deeplaw import and console entrypoint roots differ")
    return {
        "python_implementation": observation["python_implementation"],
        "python_version": observation["python_version"],
        "python_executable": python_executable,
        "import_file": import_file,
        "import_relative": import_relative,
        "entrypoint_file": entrypoint_file,
        "entrypoint_relative": entrypoint_relative,
        "entrypoint_value": observation["entrypoint_value"],
    }


def _schema_path(repository: Path, *, receipt_contract: str) -> Path:
    schema_name = {
        V1_RECEIPT_CONTRACT: "exact-wheel-execution-receipt.v1.schema.json",
        V2_RECEIPT_CONTRACT: "exact-wheel-execution-receipt.v2.schema.json",
    }.get(receipt_contract)
    if schema_name is None:
        raise ExactWheelExecutionError("unsupported exact-wheel receipt contract")
    path = repository / "contracts" / schema_name
    if path.is_symlink() or not path.is_file():
        raise ExactWheelExecutionError("exact wheel receipt schema is unavailable")
    return path


def _validate_receipt(
    receipt: Mapping[str, Any],
    *,
    repository: Path,
    receipt_contract: str | None = None,
) -> None:
    if receipt_contract is None:
        schema_version = receipt.get("schema_version")
        receipt_contract = {
            V1_SCHEMA_VERSION: V1_RECEIPT_CONTRACT,
            V2_SCHEMA_VERSION: V2_RECEIPT_CONTRACT,
        }.get(schema_version)
    if receipt_contract is None:
        raise ExactWheelExecutionError("receipt schema version is unsupported")
    try:
        schema = json.loads(
            _schema_path(repository, receipt_contract=receipt_contract).read_text(
                encoding="utf-8"
            )
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        errors = sorted(validator.iter_errors(receipt), key=lambda error: list(error.path))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ExactWheelExecutionError("exact wheel receipt schema could not be loaded") from exc
    if errors:
        raise ExactWheelExecutionError("exact wheel receipt failed its strict schema")


def _path_digest(path: Path) -> str:
    return _sha256_bytes(str(path.resolve(strict=True)).encode("utf-8"))


def _record_sha256(receipt: Mapping[str, Any]) -> str:
    payload = dict(receipt)
    payload.pop("record_sha256", None)
    return _sha256_bytes(_canonical_json(payload))


def execute_exact_wheel(
    *,
    candidate_dir: Path,
    expected_wheel_sha256: str,
    requirements_filename: str,
    expected_requirements_sha256: str,
    expected_version: str,
    repository: Path,
    venv_path: Path,
    receipt_contract: str = V2_RECEIPT_CONTRACT,
    candidate_commit: str | None = None,
    candidate_tree: str | None = None,
    expected_lock_sha256: str | None = None,
    candidate_run_id: int | None = None,
    evidence_run_id: int | None = None,
    corpus_sha256: str | None = None,
    runner_source: Path | None = None,
) -> dict[str, Any]:
    """Install and probe one Candidate Full wheel without executing checkout source."""

    repository = _repository_root(repository)
    if receipt_contract not in {V1_RECEIPT_CONTRACT, V2_RECEIPT_CONTRACT}:
        raise ExactWheelExecutionError("unsupported exact-wheel receipt contract")
    binding_values = (
        candidate_commit,
        candidate_tree,
        expected_lock_sha256,
        candidate_run_id,
        evidence_run_id,
        corpus_sha256,
    )
    if receipt_contract == V1_RECEIPT_CONTRACT:
        if any(value is not None for value in binding_values):
            raise ExactWheelExecutionError(
                "candidate-full-v1 does not accept external qualification bindings"
            )
    else:
        if any(value is None for value in binding_values):
            raise ExactWheelExecutionError(
                "external-qualification-v2 requires candidate, run, and corpus bindings"
            )
        assert candidate_commit is not None
        assert candidate_tree is not None
        assert expected_lock_sha256 is not None
        assert candidate_run_id is not None
        assert evidence_run_id is not None
        assert corpus_sha256 is not None
        _strict_git_sha(candidate_commit, label="candidate commit")
        _strict_git_sha(candidate_tree, label="candidate tree")
        _strict_sha256(expected_lock_sha256, label="candidate lock SHA-256")
        candidate_run_id = _strict_positive_run(candidate_run_id, label="candidate run id")
        evidence_run_id = _strict_positive_run(evidence_run_id, label="evidence run id")
        if candidate_run_id == evidence_run_id:
            raise ExactWheelExecutionError("candidate and evidence run ids must be distinct")
        _strict_sha256(
            corpus_sha256,
            label="candidate-full raw inventory SHA-256",
        )
        if (
            candidate_commit == "0" * 40
            or candidate_tree == "0" * 40
            or expected_lock_sha256 == "0" * 64
            or corpus_sha256 == "0" * 64
        ):
            raise ExactWheelExecutionError("candidate or corpus identity is a placeholder digest")
    candidate_wheel = _candidate_wheel(
        candidate_dir,
        expected_wheel_sha256=expected_wheel_sha256,
        expected_version=expected_version,
    )
    candidate_requirements, requirements_raw = _candidate_requirements(
        candidate_dir,
        requirements_filename=requirements_filename,
        expected_requirements_sha256=expected_requirements_sha256,
    )
    dependency_wheelhouse = _candidate_wheelhouse(candidate_dir)
    requested_venv = Path(venv_path).expanduser()
    if requested_venv.exists() or requested_venv.is_symlink():
        raise ExactWheelExecutionError("isolated venv path must not already exist")
    try:
        resolved_venv = requested_venv.resolve(strict=False)
    except OSError as exc:
        raise ExactWheelExecutionError("isolated venv path could not be resolved") from exc
    if resolved_venv == repository or repository in resolved_venv.parents:
        raise ExactWheelExecutionError("isolated venv must be outside the repository")
    source = Path(runner_source) if runner_source is not None else Path(__file__)
    source = source.resolve(strict=True)
    runner_source_sha256 = _sha256_file(source)
    created = False
    requirements_stage_dir: Path | None = None
    try:
        requirements_stage_dir, staged_requirements = _stage_verified_requirements(
            requirements_raw
        )
        python_executable = _create_venv(resolved_venv)
        created = True
        requirements_mode = _install_requirements(
            python_executable=python_executable,
            requirements=staged_requirements,
            venv_path=resolved_venv,
            wheelhouse=dependency_wheelhouse,
        )
        _verify_candidate_requirements_unchanged(
            candidate_requirements,
            requirements_raw,
        )
        _install_wheel(
            python_executable=python_executable,
            wheel=candidate_wheel,
            venv_path=resolved_venv,
        )
        _verify_candidate_requirements_unchanged(
            candidate_requirements,
            requirements_raw,
        )
        observation = _probe_runtime(
            python_executable=python_executable,
            venv_path=resolved_venv,
        )
        runtime = _validate_probe(
            observation,
            expected_version=expected_version,
            repository=repository,
            venv_path=resolved_venv,
            expected_python=python_executable,
        )
        console_entrypoint = _console_entrypoint(resolved_venv)
        version_check = _run_version(
            console_entrypoint=console_entrypoint,
            venv_path=resolved_venv,
            expected_version=expected_version,
            python_executable=python_executable,
        )
        public_journey = _run_public_journey(
            console_entrypoint=console_entrypoint,
            python_executable=python_executable,
            venv_path=resolved_venv,
            repository=repository,
        )
        try:
            console_relative = console_entrypoint.relative_to(
                resolved_venv.resolve(strict=True)
            )
        except ValueError as exc:
            raise ExactWheelExecutionError(
                "installed deeplaw console executable is outside the isolated venv"
            ) from exc
        site_packages = runtime["import_file"].resolve(strict=True)
        import_sha256 = _sha256_file(site_packages)
        entrypoint_module_sha256 = _sha256_file(runtime["entrypoint_file"])
        console_entrypoint_sha256 = _sha256_file(console_entrypoint)
        python_sha256 = _sha256_file(runtime["python_executable"])
        receipt: dict[str, Any] = {
            "schema_version": (
                V1_SCHEMA_VERSION
                if receipt_contract == V1_RECEIPT_CONTRACT
                else V2_SCHEMA_VERSION
            ),
            "status": "exact_wheel_executed",
            "runner_source_sha256": runner_source_sha256,
            "candidate": {
                "wheel_filename": candidate_wheel.name,
                "wheel_sha256": _sha256_file(candidate_wheel),
                "wheel_size": candidate_wheel.stat().st_size,
                "path_class": "candidate_full_wheel",
            },
            "requirements": {
                "filename": candidate_requirements.name,
                "sha256": _sha256_bytes(requirements_raw),
                "bytes": len(requirements_raw),
                "path_class": "candidate_requirements",
                "hash_pinned": True,
            },
            "venv": {
                "path_class": "new_isolated_venv",
                "path_sha256": _path_digest(resolved_venv),
                "created_new": True,
                "system_site_packages": False,
                "site_packages_path_class": "venv_site_packages",
            },
            "runtime": {
                "python_implementation": runtime["python_implementation"],
                "python_version": runtime["python_version"],
                "python_executable_sha256": python_sha256,
                "python_executable_path_class": "new_isolated_venv",
                "distribution_name": PACKAGE_NAME,
                "distribution_version": expected_version,
                "import_module": PACKAGE_NAME,
                "import_file_path_class": "venv_site_packages",
                "import_file_relative_path": runtime["import_relative"].as_posix(),
                "import_file_sha256": import_sha256,
            },
            "entrypoint": {
                "name": PACKAGE_NAME,
                "group": "console_scripts",
                "value": runtime["entrypoint_value"],
                "executable_path_class": "venv_bin",
                "executable_relative_path": console_relative.as_posix(),
                "executable_sha256": console_entrypoint_sha256,
                "module_path_class": "venv_site_packages",
                "module_relative_path": runtime["entrypoint_relative"].as_posix(),
                "module_sha256": entrypoint_module_sha256,
            },
            "version_check": version_check,
            "public_journey": public_journey,
            "network_acquisition": {
                "explicit": True,
                "mode": requirements_mode,
                "hash_pinned": True,
            },
            "environment_policy": {
                "python_isolated_mode": True,
                "pythonpath_cleared": True,
                "pythonhome_cleared": True,
                "user_site_disabled": True,
                "network_disabled_for_install": requirements_mode == "candidate_wheelhouse",
                "requirements_hashes_required": True,
                "candidate_source_only": True,
            },
        }
        if receipt_contract == V2_RECEIPT_CONTRACT:
            receipt.update(
                {
                    "candidate_provenance": {
                        "commit": candidate_commit,
                        "tree": candidate_tree,
                        "lock_sha256": expected_lock_sha256,
                    },
                    "run_binding": {
                        "candidate_run_id": candidate_run_id,
                        "evidence_run_id": evidence_run_id,
                    },
                    "corpus_binding": {
                        "role": "candidate_full",
                        "sha256": corpus_sha256,
                    },
                }
            )
        receipt["record_sha256"] = _record_sha256(receipt)
        _validate_receipt(
            receipt,
            repository=repository,
            receipt_contract=receipt_contract,
        )
        return receipt
    except ExactWheelExecutionError:
        if created:
            shutil.rmtree(resolved_venv, ignore_errors=True)
        raise
    except (OSError, ValueError, TypeError) as exc:
        if created:
            shutil.rmtree(resolved_venv, ignore_errors=True)
        raise ExactWheelExecutionError("exact wheel execution failed") from exc
    finally:
        if requirements_stage_dir is not None:
            shutil.rmtree(requirements_stage_dir, ignore_errors=True)


def run_exact_wheel(**kwargs: Any) -> dict[str, Any]:
    """Compatibility alias for callers using the runner as a library."""

    return execute_exact_wheel(**kwargs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--expected-wheel-sha256", required=True)
    parser.add_argument("--requirements-filename", required=True)
    parser.add_argument("--expected-requirements-sha256", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument(
        "--receipt-contract",
        choices=(V1_RECEIPT_CONTRACT, V2_RECEIPT_CONTRACT),
        default=V2_RECEIPT_CONTRACT,
    )
    parser.add_argument("--candidate-commit")
    parser.add_argument("--candidate-tree")
    parser.add_argument("--expected-lock-sha256")
    parser.add_argument("--candidate-run-id", type=int)
    parser.add_argument("--evidence-run-id", type=int)
    parser.add_argument("--corpus-sha256")
    parser.add_argument("--receipt", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    receipt = execute_exact_wheel(
        candidate_dir=args.candidate_dir,
        expected_wheel_sha256=args.expected_wheel_sha256,
        requirements_filename=args.requirements_filename,
        expected_requirements_sha256=args.expected_requirements_sha256,
        expected_version=args.expected_version,
        repository=args.repository,
        venv_path=args.venv,
        receipt_contract=args.receipt_contract,
        candidate_commit=args.candidate_commit,
        candidate_tree=args.candidate_tree,
        expected_lock_sha256=args.expected_lock_sha256,
        candidate_run_id=args.candidate_run_id,
        evidence_run_id=args.evidence_run_id,
        corpus_sha256=args.corpus_sha256,
    )
    if args.receipt.is_symlink() or args.receipt.exists():
        raise ExactWheelExecutionError("receipt output path must be new and non-symlink")
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_bytes(_canonical_json(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
