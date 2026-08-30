"""Host-neutral candidate, report, and retained-bundle orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tomllib
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
from typing import Any

from jsonschema import (
    Draft202012Validator,
    FormatChecker,
    SchemaError,
    ValidationError,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from benchmarks.hosts import pass13_evidence

HISTORICAL_PACKAGE_VERSION = pass13_evidence.HISTORICAL_PACKAGE_VERSION
CURRENT_PACKAGE_VERSION = pass13_evidence.CURRENT_PACKAGE_VERSION
SUPPORTED_PACKAGE_VERSIONS = pass13_evidence.SUPPORTED_PACKAGE_VERSIONS
# Kept as a compatibility export for callers that only need the current target;
# all candidate/report binding decisions below use an observed exact version.
PACKAGE_VERSION = CURRENT_PACKAGE_VERSION
REPORT_SCHEMA_VERSION = "deeplaw.host-continuity-qualification/v2"
RUNTIME_CONTRACT_NAMES = (
    "host-preflight-receipt.v1.schema.json",
    "host-continuity-qualification.v1.schema.json",
    "host-continuity-qualification.v2.schema.json",
    "host-continuity-development-diagnostic.v1.schema.json",
    "host-continuity-human-gold.v2.schema.json",
    "host-continuity-pass17-blind-review.v2.schema.json",
    "host-continuity-pass17-run-score.v2.schema.json",
    "host-continuity-capsule.v1.schema.json",
    "host-session-route-result.v2.schema.json",
    "host-qualification-bundle-manifest.v1.schema.json",
    "knowledge-support.input.v6.schema.json",
    "knowledge-support.output.v6.schema.json",
    "knowledge-sink.input.v2.schema.json",
    "provider-knowledge-capsule.v2.schema.json",
)


class QualificationOrchestrationError(RuntimeError):
    """The common Host candidate or retained bundle failed closed."""


def _supported_package_version(value: object, *, label: str) -> str:
    try:
        return pass13_evidence.supported_package_version(value, label=label)
    except pass13_evidence.EvidenceValidationError as exc:
        raise QualificationOrchestrationError(str(exc)) from exc


def _wheel_package_version(name: object) -> str:
    try:
        return pass13_evidence.wheel_package_version(name)
    except pass13_evidence.EvidenceValidationError as exc:
        raise QualificationOrchestrationError(str(exc)) from exc


def _binding_package_version(binding: Mapping[str, Any]) -> str:
    declared_version = binding.get("package_version")
    if declared_version is None:
        declared_version = _wheel_package_version(binding.get("wheel_name"))
    try:
        return pass13_evidence.validate_package_version_binding(
            declared_version,
            binding.get("wheel_name"),
        )
    except pass13_evidence.EvidenceValidationError as exc:
        raise QualificationOrchestrationError(str(exc)) from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise QualificationOrchestrationError("candidate binding requires a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            env={"PATH": os.defpath, "LC_ALL": "C"},
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationOrchestrationError("Git candidate binding failed") from exc
    if completed.returncode != 0:
        raise QualificationOrchestrationError("Git candidate binding failed")
    return completed.stdout.strip()


def repository_binding(repository: Path) -> dict[str, Any]:
    try:
        project = tomllib.loads(
            (repository / "pyproject.toml").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise QualificationOrchestrationError("project metadata is unavailable") from exc
    project_metadata = project.get("project")
    version = (
        project_metadata.get("version")
        if isinstance(project_metadata, Mapping)
        else None
    )
    version = _supported_package_version(version, label="package version")
    status = _git(repository, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise QualificationOrchestrationError(
            "qualification requires a clean exact worktree"
        )
    return {
        "commit": _git(repository, "rev-parse", "HEAD"),
        "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "worktree_clean": True,
        "package_version": version,
    }


def candidate_output_directory(path: Path, *, repository: Path) -> Path:
    if path.is_symlink():
        raise QualificationOrchestrationError(
            "qualification output directory must not be a symlink"
        )
    selected = path.resolve(strict=False)
    repository = repository.resolve(strict=True)
    if selected == repository or repository in selected.parents:
        raise QualificationOrchestrationError(
            "qualification output must be outside the repository"
        )
    if selected.exists() or selected.is_symlink():
        raise QualificationOrchestrationError(
            "qualification output directory must not already exist"
        )
    return selected


def _runtime_contract_script(contract_names: Sequence[str]) -> str:
    return (
        "import hashlib, importlib.metadata, importlib.resources, importlib.util, json, pathlib\n"
        "spec = importlib.util.find_spec('deeplaw')\n"
        "origin = getattr(spec, 'origin', None) if spec else None\n"
        "if not isinstance(origin, str) or 'site-packages' not in origin:\n"
        "    raise SystemExit(4)\n"
        f"names = {list(contract_names)!r}\n"
        "root = importlib.resources.files('deeplaw').joinpath('contracts')\n"
        "digests = {name: hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest() "
        "for name in names}\n"
        "package_root = pathlib.Path(origin).parent\n"
        "files = {str(path.relative_to(package_root)): "
        "hashlib.sha256(path.read_bytes()).hexdigest() "
        "for path in package_root.rglob('*') if path.is_file() and '__pycache__' not in path.parts "
        "and path.suffix != '.pyc'}\n"
        "print(json.dumps({'version': importlib.metadata.version('deeplaw'), "
        "'import_path_class': 'isolated_site_packages', 'contracts': digests, 'files': files}, "
        "sort_keys=True, separators=(',', ':')))\n"
    )


def installed_runtime_binding(
    *,
    candidate_wheel: Path,
    deeplaw_executable: Path,
    repository: Path,
    expected_package_version: str,
) -> dict[str, Any]:
    expected_version = _supported_package_version(
        expected_package_version,
        label="expected package version",
    )
    if candidate_wheel.is_symlink() or not candidate_wheel.is_file():
        raise QualificationOrchestrationError("candidate wheel is not one regular file")
    wheel = candidate_wheel.resolve(strict=True)
    if _wheel_package_version(wheel.name) != expected_version:
        raise QualificationOrchestrationError("candidate wheel name is invalid")
    if deeplaw_executable.is_symlink() or not deeplaw_executable.is_file():
        raise QualificationOrchestrationError(
            "installed DeepLaw executable is not regular"
        )
    executable = deeplaw_executable.resolve(strict=True)
    runtime_python = executable.parent / "python"
    if not runtime_python.exists() or not runtime_python.resolve(strict=True).is_file():
        raise QualificationOrchestrationError(
            "installed DeepLaw executable has no adjacent Python"
        )
    try:
        completed = subprocess.run(
            [
                str(runtime_python),
                "-I",
                "-c",
                _runtime_contract_script(RUNTIME_CONTRACT_NAMES),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
            env={"PATH": str(runtime_python.parent), "PYTHONNOUSERSITE": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QualificationOrchestrationError(
            "installed wheel import verification failed"
        ) from exc
    if completed.returncode != 0:
        raise QualificationOrchestrationError(
            "installed wheel did not provide isolated DeepLaw contracts"
        )
    try:
        observed = json.loads(completed.stdout)
    except (TypeError, ValueError) as exc:
        raise QualificationOrchestrationError(
            "installed wheel contract receipt was invalid"
        ) from exc
    source_digests = {
        name: sha256_file(repository / "contracts" / name)
        for name in RUNTIME_CONTRACT_NAMES
    }
    if (
        not isinstance(observed, Mapping)
        or observed.get("version") != expected_version
        or observed.get("import_path_class") != "isolated_site_packages"
        or observed.get("contracts") != source_digests
        or not isinstance(observed.get("files"), Mapping)
    ):
        raise QualificationOrchestrationError(
            "installed wheel contract identity was incomplete"
        )
    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = f"deeplaw-{expected_version}.dist-info/METADATA"
            metadata_names = [
                name
                for name in archive.namelist()
                if name == metadata_name
            ]
            if len(metadata_names) != 1:
                raise QualificationOrchestrationError(
                    "candidate wheel metadata is missing or ambiguous"
                )
            try:
                metadata = Parser().parsestr(
                    archive.read(metadata_name).decode("utf-8")
                )
            except (UnicodeDecodeError, KeyError) as exc:
                raise QualificationOrchestrationError(
                    "candidate wheel metadata is invalid"
                ) from exc
            if (
                metadata.get_all("Name") != ["deeplaw"]
                or metadata.get_all("Version") != [expected_version]
            ):
                raise QualificationOrchestrationError(
                    "candidate wheel metadata version is invalid"
                )
            wheel_files = {
                name.removeprefix("deeplaw/"): sha256_bytes(archive.read(name))
                for name in archive.namelist()
                if name.startswith("deeplaw/") and not name.endswith("/")
            }
    except (OSError, zipfile.BadZipFile) as exc:
        raise QualificationOrchestrationError(
            "candidate wheel package inventory is invalid"
        ) from exc
    if not wheel_files or dict(observed["files"]) != wheel_files:
        raise QualificationOrchestrationError(
            "installed DeepLaw package does not match the candidate wheel"
        )
    return {
        "package_version": expected_version,
        "wheel_name": wheel.name,
        "wheel_sha256": sha256_file(wheel),
        "wheel_bytes": wheel.stat().st_size,
        "runtime_executable_sha256": sha256_file(executable),
        "import_path_class": "isolated_site_packages",
        "contract_digests": source_digests,
        "_executable": executable,
        "_runtime_python": runtime_python.resolve(strict=True),
    }


async def _observe_knowledge_support_tools_list(
    *,
    command: Path,
    args: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=str(command),
        args=list(args),
        cwd=cwd,
        env=dict(environment),
    )
    try:
        async with asyncio.timeout(30):
            async with (
                stdio_client(parameters) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                listed = await session.list_tools()
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise QualificationOrchestrationError("MCP tools/list observation failed") from exc
    return pass13_evidence.knowledge_support_tool_schema_receipt(listed.tools)


def observe_knowledge_support_tools_list(
    *,
    command: Path,
    args: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    """Observe the actual candidate MCP tools/list response in a closed child."""

    try:
        return asyncio.run(
            _observe_knowledge_support_tools_list(
                command=command,
                args=args,
                cwd=cwd,
                environment=environment,
            )
        )
    except pass13_evidence.EvidenceValidationError as exc:
        raise QualificationOrchestrationError(str(exc)) from exc


def _token_aggregate(runs: Sequence[Mapping[str, Any]], field: str) -> int | str:
    values = [
        turn.get("usage", {}).get(field)
        for run in runs
        for turn in run.get("turns", [])
        if isinstance(turn, Mapping)
    ]
    if values and all(
        isinstance(value, int) and not isinstance(value, bool) for value in values
    ):
        return sum(values)
    return "unreported"


def build_host_report(
    *,
    host: str,
    binding: Mapping[str, Any],
    environment: Mapping[str, Any],
    host_attestation: Mapping[str, Any],
    tool_schema: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    lifecycle: Mapping[str, Any],
    security: Mapping[str, Any],
    not_executed: Sequence[str],
    execution_mode: str = "qualification",
) -> dict[str, Any]:
    if host not in {"codex", "opencode"}:
        raise QualificationOrchestrationError("Host report identity is invalid")
    if execution_mode not in {"qualification", "diagnostic"}:
        raise QualificationOrchestrationError("Host execution mode is invalid")
    package_version = _binding_package_version(binding)
    expected_run_count = 3 if execution_mode == "qualification" else 1
    run_rows = [dict(run) for run in runs]
    passed = sum(run.get("status") == "passed" for run in run_rows)
    failed = len(run_rows) - passed
    if len(run_rows) != expected_run_count:
        raise QualificationOrchestrationError("Host execution mode has an invalid run count")
    if passed and failed:
        report_status = "partial"
    elif passed == expected_run_count:
        report_status = "executed"
    else:
        report_status = "failed"
    provider_bytes = sum(
        payload.get("provider_bytes", 0)
        for run in run_rows
        for turn in run.get("turns", [])
        for payload in turn.get("safe_read", {}).get("provider_payloads", [])
        if isinstance(payload, Mapping)
        and isinstance(payload.get("provider_bytes"), int)
    )
    elapsed = sum(
        turn.get("host_elapsed_ms", 0)
        for run in run_rows
        for turn in run.get("turns", [])
        if isinstance(turn.get("host_elapsed_ms"), int)
    )
    token_fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    aggregate = {
        "passed_runs": passed,
        "failed_runs": failed,
        "first_call_valid_runs": sum(
            bool(run.get("turns"))
            and run["turns"][0].get("safe_read", {}).get("first_call_valid") is True
            for run in run_rows
        ),
        "bounded_retry_runs": sum(
            any(
                turn.get("safe_read", {}).get("bounded_retry_used") is True
                for turn in run.get("turns", [])
            )
            for run in run_rows
        ),
        "provider_bytes": provider_bytes,
        **{field: _token_aggregate(run_rows, field) for field in token_fields},
        "host_elapsed_ms": elapsed,
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "execution_mode": execution_mode,
        "qualification_status": (
            "not_applicable"
            if execution_mode == "diagnostic"
            else "passed"
            if report_status == "executed"
            else "failed"
            if report_status == "failed"
            else "partial"
        ),
        "evidence_class": (
            "qualification_holdout"
            if execution_mode == "qualification"
            else "development_diagnostic"
        ),
        "host": host,
        "status": report_status,
        "package_version": package_version,
        "release_ready": False,
        "claim_eligible": False,
        "binding": dict(binding),
        "environment": dict(environment),
        "host_attestation": dict(host_attestation),
        "tool_schema": dict(tool_schema),
        "lifecycle": dict(lifecycle),
        "security": dict(security),
        "runs": run_rows,
        "aggregate": aggregate,
        "not_executed": list(dict.fromkeys(not_executed)),
    }


@dataclass(frozen=True, slots=True)
class QualificationOrchestrator:
    host: str
    repository: Path
    candidate_wheel: Path
    deeplaw_executable: Path
    output_dir: Path
    error_type: type[Exception] = QualificationOrchestrationError
    execution_mode: str = "qualification"

    def _translate(self, error: BaseException) -> Exception:
        return self.error_type(str(error))

    def prepare_candidate(self) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        try:
            output = candidate_output_directory(
                self.output_dir,
                repository=self.repository,
            )
            binding = repository_binding(self.repository)
            runtime = installed_runtime_binding(
                candidate_wheel=self.candidate_wheel,
                deeplaw_executable=self.deeplaw_executable,
                repository=self.repository,
                expected_package_version=binding["package_version"],
            )
        except QualificationOrchestrationError as exc:
            raise self._translate(exc) from exc
        return output, binding, runtime

    def build_report(
        self,
        *,
        binding: Mapping[str, Any],
        environment: Mapping[str, Any],
        host_attestation: Mapping[str, Any],
        tool_schema: Mapping[str, Any],
        runs: Sequence[Mapping[str, Any]],
        lifecycle: Mapping[str, Any],
        security: Mapping[str, Any],
        not_executed: Sequence[str],
    ) -> dict[str, Any]:
        try:
            report = build_host_report(
                host=self.host,
                binding=binding,
                environment=environment,
                host_attestation=host_attestation,
                tool_schema=tool_schema,
                runs=runs,
                lifecycle=lifecycle,
                security=security,
                not_executed=not_executed,
                execution_mode=self.execution_mode,
            )
            pass13_evidence.validate_host_report_consistency(report)
            schema = json.loads(
                (
                    self.repository
                    / "contracts"
                    / "host-continuity-qualification.v2.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).validate(report)
        except (
            OSError,
            ValueError,
            QualificationOrchestrationError,
            pass13_evidence.EvidenceValidationError,
            SchemaError,
            ValidationError,
        ) as exc:
            raise self._translate(exc) from exc
        return report

    def retain_json(
        self,
        name: str,
        value: Mapping[str, Any],
        *,
        forbidden_values: Sequence[str] = (),
    ) -> Path:
        path = self.output_dir / name
        try:
            pass13_evidence.write_retained_artifact(
                path,
                (pass13_evidence.canonical_json(value) + "\n").encode("utf-8"),
                output_root=self.output_dir,
                forbidden_values=forbidden_values,
            )
        except pass13_evidence.EvidenceValidationError as exc:
            raise self._translate(exc) from exc
        return path

    def finalize_bundle(
        self,
        *,
        commit: str,
        tree: str,
        artifacts: Mapping[str, Path],
        forbidden_values: Sequence[str] = (),
    ) -> dict[str, Any]:
        try:
            manifest = pass13_evidence.build_bundle_manifest(
                host=self.host,
                commit=commit,
                tree=tree,
                artifacts=artifacts,
                output_root=self.output_dir,
                forbidden_values=forbidden_values,
            )
            schema = json.loads(
                (
                    self.repository
                    / "contracts"
                    / "host-qualification-bundle-manifest.v1.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).validate(manifest)
            self.retain_json(
                "host-qualification-bundle-manifest.json",
                manifest,
                forbidden_values=forbidden_values,
            )
            self.retain_json(
                "SHA256SUMS.json",
                manifest,
                forbidden_values=forbidden_values,
            )
        except (
            OSError,
            ValueError,
            pass13_evidence.EvidenceValidationError,
            SchemaError,
            ValidationError,
        ) as exc:
            raise self._translate(exc) from exc
        return manifest
