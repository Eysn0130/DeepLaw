from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any, BinaryIO

from benchmarks.baselines.registry import (
    default_registry_path,
    load_registry,
    registry_sha256,
)
from benchmarks.external.benchlib import SCHEMA_RUN, strict_json_loads
from deeplaw.bounded_subprocess import (
    BoundedSubprocessError,
    run_bounded_subprocess,
)
from deeplaw.util import canonical_json, sha256_bytes, sha256_file

EXECUTION_PLAN_SCHEMA = "deeplaw.official-baseline-execution-plan/v2"
EXECUTION_RECEIPT_SCHEMA = "deeplaw.official-baseline-execution-receipt/v2"
EVALUATION_ENVIRONMENT_SCHEMA = "deeplaw.baseline-evaluation-environment/v1"
RESOURCE_RECORD_SCHEMA = "deeplaw.official-baseline-resource-record/v1"
_CLAIM_INELIGIBILITY_REASON = (
    "a local adapter receipt is not an independently attested held-out comparison"
)
_MAX_REGISTRY_BYTES = 4 * 1024 * 1024
_MAX_EVIDENCE_JSON_BYTES = 4 * 1024 * 1024
_MAX_INPUT_BYTES = 16 * 1024 * 1024 * 1024
_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_JSONL_LINE_BYTES = 2 * 1024 * 1024
_MAX_CASES = 1_000_000
_MAX_RETRIEVED_ITEMS = 1_000
_MAX_PROCESS_STDOUT_BYTES = 4 * 1024 * 1024
_MAX_PROCESS_STDERR_BYTES = 4 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_DEFAULT_INHERITED_ENVIRONMENT = (
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "PATHEXT",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "WINDIR",
)


def _bounded_string(value: Any, *, field: str, maximum: int = 4_096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be a bounded canonical string")
    return value


def _closed_dict(value: Any, *, field: str, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{field} does not match its closed contract")
    return value


def _absolute(path: Path, *, field: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = expanded.absolute()
    value = str(expanded)
    _bounded_string(value, field=field)
    return expanded


def _path_hint(value: Any, *, field: str) -> Path:
    path = Path(_bounded_string(value, field=field))
    if not path.is_absolute():
        raise ValueError(f"{field} must be an absolute path hint")
    return path


def _regular_file(
    path: Path,
    *,
    field: str,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> Path:
    selected = _absolute(path, field=field)
    if selected.is_symlink() or not selected.is_file():
        raise ValueError(f"{field} must be a regular non-symlink file")
    size = selected.stat().st_size
    minimum = 0 if allow_empty else 1
    if not minimum <= size <= maximum_bytes:
        raise ValueError(f"{field} violates its byte bound")
    return selected


def _file_binding(
    path: Path,
    *,
    field: str,
    allow_symlink: bool,
    require_executable: bool = False,
) -> dict[str, Any]:
    hinted = _absolute(path, field=field)
    if hinted.is_symlink() and not allow_symlink:
        raise ValueError(f"{field} must not be a symlink")
    try:
        resolved = hinted.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError(f"{field} cannot be resolved") from error
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"{field} must resolve to a regular file")
    if require_executable and os.name != "nt" and not os.access(hinted, os.X_OK):
        raise ValueError(f"{field} is not executable")
    return {
        "path_hint": str(hinted),
        "resolved_path": str(resolved),
        "sha256": sha256_file(resolved),
        "byte_size": resolved.stat().st_size,
    }


def _validate_file_binding(value: Any, *, field: str) -> dict[str, Any]:
    binding = _closed_dict(
        value,
        field=field,
        keys={"path_hint", "resolved_path", "sha256", "byte_size"},
    )
    _path_hint(binding.get("path_hint"), field=f"{field}.path_hint")
    _path_hint(binding.get("resolved_path"), field=f"{field}.resolved_path")
    if not _SHA256.fullmatch(str(binding.get("sha256"))):
        raise ValueError(f"{field}.sha256 is invalid")
    if (
        isinstance(binding.get("byte_size"), bool)
        or not isinstance(binding.get("byte_size"), int)
        or binding["byte_size"] < 1
    ):
        raise ValueError(f"{field}.byte_size is invalid")
    return binding


def _system(registry: dict[str, Any], system_id: str) -> dict[str, Any]:
    selected = next(
        (item for item in registry["systems"] if item["system_id"] == system_id),
        None,
    )
    if selected is None:
        raise ValueError(f"baseline system is not registered: {system_id}")
    if selected["adapter"]["kind"] not in {"closed-subprocess", "deeplaw-jsonl"}:
        raise ValueError(
            "this runner accepts only subprocess-capable baseline adapters"
        )
    return selected


def _git_output(checkout: Path, arguments: list[str], *, maximum: int = 65_536) -> bytes:
    completed = run_bounded_subprocess(
        ["git", *arguments],
        cwd=checkout,
        timeout_seconds=30,
        max_stdout_bytes=maximum,
        max_stderr_bytes=16_384,
    )
    if completed.returncode != 0:
        raise RuntimeError("baseline checkout is not a readable Git worktree")
    return completed.stdout


def _checkout_binding(checkout: Path) -> dict[str, Any]:
    selected = _absolute(checkout, field="baseline checkout")
    if selected.is_symlink() or not selected.is_dir():
        raise ValueError("baseline checkout must be a regular non-symlink directory")
    root = _git_output(selected, ["rev-parse", "--show-toplevel"]).decode(
        "utf-8", errors="strict"
    ).strip()
    try:
        if not os.path.samefile(selected, root):
            raise ValueError("baseline checkout must be the Git worktree root")
    except OSError as error:
        raise ValueError("baseline checkout root cannot be verified") from error
    revision = _git_output(selected, ["rev-parse", "HEAD"]).decode(
        "ascii", errors="strict"
    ).strip()
    status = _git_output(
        selected,
        ["status", "--porcelain=v1", "--untracked-files=all"],
        maximum=4 * 1024 * 1024,
    )
    if status:
        raise RuntimeError("baseline checkout must be clean, including untracked files")
    submodules = _git_output(
        selected,
        ["submodule", "status", "--recursive"],
        maximum=4 * 1024 * 1024,
    )
    lines = [line for line in submodules.splitlines() if line]
    if any(line[:1] in {b"-", b"+", b"U"} for line in lines):
        raise RuntimeError("baseline checkout has an uninitialized or drifting submodule")
    return {
        "path_hint": str(selected),
        "revision": revision,
        "clean": True,
        "submodule_count": len(lines),
        "submodule_status_sha256": sha256_bytes(submodules),
    }


def _registry_state(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    selected = _regular_file(
        path,
        field="baseline registry",
        maximum_bytes=_MAX_REGISTRY_BYTES,
    )
    registry = load_registry(selected)
    return registry, {
        "path_hint": str(selected),
        "file_sha256": sha256_file(selected),
        "canonical_sha256": registry_sha256(registry),
    }


def _read_query_case_ids(path: Path) -> list[str]:
    selected = _regular_file(
        path,
        field="baseline queries",
        maximum_bytes=_MAX_INPUT_BYTES,
    )
    case_ids: list[str] = []
    seen: set[str] = set()
    with selected.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if len(raw_line) > _MAX_JSONL_LINE_BYTES:
                raise ValueError(f"baseline query record {line_number} exceeds its byte bound")
            if not raw_line.strip():
                continue
            if len(case_ids) >= _MAX_CASES:
                raise ValueError("baseline queries exceed their case bound")
            try:
                record = strict_json_loads(raw_line)
            except (UnicodeDecodeError, ValueError) as error:
                raise ValueError(
                    f"baseline query record {line_number} is not strict JSON"
                ) from error
            if not isinstance(record, dict) or set(record) != {"case_id", "query"}:
                raise ValueError(
                    f"baseline query record {line_number} must contain case_id/query"
                )
            case_id = _bounded_string(
                record.get("case_id"),
                field=f"baseline query record {line_number} case_id",
                maximum=500,
            )
            _bounded_string(
                record.get("query"),
                field=f"baseline query record {line_number} query",
                maximum=4_000,
            )
            if case_id in seen:
                raise ValueError(f"duplicate baseline query case_id: {case_id}")
            seen.add(case_id)
            case_ids.append(case_id)
    if not case_ids:
        raise ValueError("baseline queries contain no cases")
    return case_ids


def _case_ids_sha256(case_ids: list[str] | set[str]) -> str:
    return sha256_bytes(canonical_json(sorted(case_ids)).encode("utf-8"))


def _input_binding(path: Path, *, field: str) -> dict[str, Any]:
    selected = _regular_file(path, field=field, maximum_bytes=_MAX_INPUT_BYTES)
    return {
        "path_hint": str(selected),
        "sha256": sha256_file(selected),
        "byte_size": selected.stat().st_size,
    }


def _queries_binding(path: Path) -> dict[str, Any]:
    binding = _input_binding(path, field="baseline queries")
    case_ids = _read_query_case_ids(path)
    return {
        **binding,
        "case_count": len(case_ids),
        "case_ids_sha256": _case_ids_sha256(case_ids),
    }


def _record_sha256(record: dict[str, Any], *, field: str) -> str:
    digest = record.get("record_sha256")
    body = {key: value for key, value in record.items() if key != "record_sha256"}
    if not _SHA256.fullmatch(str(digest)) or digest != sha256_bytes(
        canonical_json(body).encode("utf-8")
    ):
        raise ValueError(f"{field} record_sha256 is invalid")
    return digest


def _read_evidence_json(path: Path, *, field: str) -> tuple[Path, dict[str, Any]]:
    selected = _regular_file(
        path,
        field=field,
        maximum_bytes=_MAX_EVIDENCE_JSON_BYTES,
    )
    try:
        value = strict_json_loads(selected.read_bytes())
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{field} must contain strict JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{field} must contain a JSON object")
    return selected, value


def _validate_evaluation_environment(
    path: Path,
    *,
    registry: dict[str, Any],
    system: dict[str, Any],
) -> dict[str, Any]:
    selected, record = _read_evidence_json(path, field="baseline evaluation environment")
    _closed_dict(
        record,
        field="baseline evaluation environment",
        keys={
            "schema_version",
            "evaluator_run_id",
            "system_id",
            "implementation_revision",
            "hardware",
            "software",
            "models",
            "reader",
            "network",
            "measurement",
            "record_sha256",
        },
    )
    if (
        record.get("schema_version") != EVALUATION_ENVIRONMENT_SCHEMA
        or record.get("system_id") != system["system_id"]
        or record.get("implementation_revision")
        != system["implementation"]["revision"]
    ):
        raise ValueError("baseline evaluation environment identity is invalid")
    _bounded_string(record.get("evaluator_run_id"), field="evaluator_run_id", maximum=200)
    hardware = _closed_dict(
        record.get("hardware"),
        field="baseline evaluation hardware",
        keys={
            "host_id",
            "os_name",
            "os_version",
            "architecture",
            "cpu_model",
            "logical_cpu_count",
            "memory_bytes",
            "accelerator",
            "storage",
        },
    )
    for field in (
        "host_id",
        "os_name",
        "os_version",
        "architecture",
        "cpu_model",
        "storage",
    ):
        _bounded_string(hardware.get(field), field=f"hardware.{field}", maximum=500)
    accelerator = hardware.get("accelerator")
    if accelerator is not None:
        _bounded_string(accelerator, field="hardware.accelerator", maximum=500)
    for field, maximum in (("logical_cpu_count", 1_000_000), ("memory_bytes", 2**63 - 1)):
        value = hardware.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"hardware.{field} is invalid")
    software = record.get("software")
    if not isinstance(software, list) or not 1 <= len(software) <= 128:
        raise ValueError("baseline evaluation software inventory is invalid")
    software_names: set[str] = set()
    for index, item in enumerate(software):
        _closed_dict(
            item,
            field=f"software[{index}]",
            keys={"name", "version", "role", "artifact_sha256"},
        )
        name = _bounded_string(item.get("name"), field=f"software[{index}].name", maximum=200)
        _bounded_string(item.get("version"), field=f"software[{index}].version", maximum=500)
        if name in software_names or item.get("role") not in {
            "runtime",
            "container",
            "database",
            "model-server",
        } or not _SHA256.fullmatch(str(item.get("artifact_sha256"))):
            raise ValueError(f"software[{index}] is invalid or duplicated")
        software_names.add(name)
    models = record.get("models")
    expected_aliases = set(system["model_aliases"])
    if not isinstance(models, list) or len(models) > 8:
        raise ValueError("baseline evaluation model inventory is invalid")
    observed_aliases: set[str] = set()
    for index, model in enumerate(models):
        _closed_dict(
            model,
            field=f"models[{index}]",
            keys={
                "alias",
                "model_id",
                "revision",
                "artifact_manifest_sha256",
                "loopback_only",
            },
        )
        alias = _bounded_string(model.get("alias"), field=f"models[{index}].alias", maximum=80)
        expected_model = registry["shared_models"].get(alias)
        if (
            alias in observed_aliases
            or alias not in expected_aliases
            or not isinstance(expected_model, dict)
            or model.get("model_id") != expected_model.get("model_id")
            or model.get("revision") != expected_model.get("revision")
            or not _SHA256.fullmatch(str(model.get("artifact_manifest_sha256")))
            or model.get("loopback_only") is not True
        ):
            raise ValueError(f"models[{index}] differs from the pinned registry")
        observed_aliases.add(alias)
    if observed_aliases != expected_aliases:
        raise ValueError("baseline evaluation models do not cover the pinned aliases")
    reader = _closed_dict(
        record.get("reader"),
        field="baseline evaluation reader",
        keys={
            "alias",
            "model_id",
            "revision",
            "artifact_manifest_sha256",
            "loopback_only",
        },
    )
    expected_reader = registry["shared_models"].get("generation-reader")
    if (
        not isinstance(expected_reader, dict)
        or reader.get("alias") != "generation-reader"
        or reader.get("model_id") != expected_reader.get("model_id")
        or reader.get("revision") != expected_reader.get("revision")
        or not _SHA256.fullmatch(str(reader.get("artifact_manifest_sha256")))
        or reader.get("loopback_only") is not True
    ):
        raise ValueError("baseline evaluation reader differs from the pinned registry")
    reader_model = next(
        (item for item in models if item["alias"] == "generation-reader"),
        None,
    )
    if reader_model is not None and reader_model != reader:
        raise ValueError("system and common reader model bindings differ")
    network = _closed_dict(
        record.get("network"),
        field="baseline evaluation network",
        keys={
            "policy",
            "enforcement_method",
            "build_network_used",
            "build_network_record_sha256",
            "query_network_disabled",
            "loopback_only_model_services",
        },
    )
    if (
        network.get("policy") != system["network_policy"]
        or network.get("query_network_disabled") is not True
        or network.get("loopback_only_model_services") is not True
        or not isinstance(network.get("build_network_used"), bool)
    ):
        raise ValueError("baseline evaluation network policy is invalid")
    _bounded_string(
        network.get("enforcement_method"),
        field="network.enforcement_method",
        maximum=500,
    )
    build_network_digest = network.get("build_network_record_sha256")
    if (network["build_network_used"] and not _SHA256.fullmatch(str(build_network_digest))) or (
        not network["build_network_used"] and build_network_digest is not None
    ):
        raise ValueError("baseline build-network record binding is invalid")
    measurement = _closed_dict(
        record.get("measurement"),
        field="baseline evaluation measurement",
        keys={"clock", "peak_memory", "disk", "model_cost"},
    )
    if measurement != {
        "clock": "evaluator-monotonic-wall-clock-v1",
        "peak_memory": "evaluator-process-tree-peak-rss-v1",
        "disk": "evaluator-workspace-apparent-bytes-v1",
        "model_cost": "local-model-call-token-ledger-v1",
    }:
        raise ValueError("baseline evaluation measurement protocol is invalid")
    return {
        "path_hint": str(selected),
        "sha256": sha256_file(selected),
        "byte_size": selected.stat().st_size,
        "record_sha256": _record_sha256(
            record,
            field="baseline evaluation environment",
        ),
    }


def _validate_record_binding(value: Any, *, field: str) -> dict[str, Any]:
    binding = _closed_dict(
        value,
        field=field,
        keys={"path_hint", "sha256", "byte_size", "record_sha256"},
    )
    _path_hint(binding.get("path_hint"), field=f"{field}.path_hint")
    if not _SHA256.fullmatch(str(binding.get("sha256"))) or not _SHA256.fullmatch(
        str(binding.get("record_sha256"))
    ):
        raise ValueError(f"{field} digests are invalid")
    if (
        isinstance(binding.get("byte_size"), bool)
        or not isinstance(binding.get("byte_size"), int)
        or not 1 <= binding["byte_size"] <= _MAX_EVIDENCE_JSON_BYTES
    ):
        raise ValueError(f"{field}.byte_size is invalid")
    return binding


def _nonnegative_number(value: Any, *, field: str) -> float | int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{field} must be a finite non-negative number")
    return value


def _nonnegative_integer(value: Any, *, field: str, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= maximum
    ):
        raise ValueError(f"{field} must be a bounded non-negative integer")
    return value


def _validate_resource_record(path: Path, *, plan: dict[str, Any]) -> dict[str, Any]:
    selected, record = _read_evidence_json(
        path,
        field="official baseline resource record",
    )
    _closed_dict(
        record,
        field="official baseline resource record",
        keys={
            "schema_version",
            "system_id",
            "implementation_revision",
            "registry_sha256",
            "corpus_sha256",
            "queries_sha256",
            "query_case_ids_sha256",
            "evaluation_environment_record_sha256",
            "case_count",
            "build_seconds",
            "query_seconds",
            "peak_memory_bytes",
            "index_bytes",
            "workspace_bytes",
            "model_calls",
            "model_input_tokens",
            "model_output_tokens",
            "model_cost_usd",
            "failure_count",
            "failures",
            "record_sha256",
        },
    )
    expected = {
        "schema_version": RESOURCE_RECORD_SCHEMA,
        "system_id": plan["system"]["system_id"],
        "implementation_revision": (
            plan["checkout"]["revision"]
            if "checkout" in plan
            else plan["implementation_revision"]
        ),
        "registry_sha256": plan["registry"]["canonical_sha256"],
        "corpus_sha256": plan["corpus"]["sha256"],
        "queries_sha256": plan["queries"]["sha256"],
        "query_case_ids_sha256": plan["queries"]["case_ids_sha256"],
        "evaluation_environment_record_sha256": plan["evaluation_environment"][
            "record_sha256"
        ],
        "case_count": plan["queries"]["case_count"],
    }
    if any(record.get(field) != value for field, value in expected.items()):
        raise ValueError("official baseline resource record binding is invalid")
    for field in ("build_seconds", "query_seconds", "model_cost_usd"):
        _nonnegative_number(record.get(field), field=field)
    for field, maximum in (
        ("peak_memory_bytes", 2**63 - 1),
        ("index_bytes", 2**63 - 1),
        ("workspace_bytes", 2**63 - 1),
        ("model_calls", 2**63 - 1),
        ("model_input_tokens", 2**63 - 1),
        ("model_output_tokens", 2**63 - 1),
        ("failure_count", _MAX_CASES),
    ):
        _nonnegative_integer(record.get(field), field=field, maximum=maximum)
    failures = record.get("failures")
    if (
        not isinstance(failures, list)
        or len(failures) > _MAX_CASES
        or record["failure_count"] != len(failures)
    ):
        raise ValueError("official baseline failure inventory is invalid")
    expected_case_ids = set(
        _read_query_case_ids(Path(plan["queries"]["path_hint"]))
    )
    for index, failure in enumerate(failures):
        _closed_dict(
            failure,
            field=f"resource failures[{index}]",
            keys={"case_id", "phase", "kind", "message_sha256"},
        )
        case_id = failure.get("case_id")
        if case_id is not None:
            _bounded_string(
                case_id,
                field=f"resource failures[{index}].case_id",
                maximum=500,
            )
            if case_id not in expected_case_ids:
                raise ValueError("resource failure case_id is outside the frozen queries")
        if failure.get("phase") not in {"setup", "build", "query", "teardown"}:
            raise ValueError(f"resource failures[{index}].phase is invalid")
        if failure.get("kind") not in {
            "error",
            "timeout",
            "abstention",
            "retry",
        }:
            raise ValueError(f"resource failures[{index}].kind is invalid")
        if not _SHA256.fullmatch(str(failure.get("message_sha256"))):
            raise ValueError(f"resource failures[{index}].message_sha256 is invalid")
    return {
        "artifact": {
            "path_hint": str(selected),
            "sha256": sha256_file(selected),
            "byte_size": selected.stat().st_size,
        },
        "record_sha256": _record_sha256(
            record,
            field="official baseline resource record",
        ),
        "failure_case_ids": sorted(
            {
                failure["case_id"]
                for failure in failures
                if failure["case_id"] is not None
            }
        ),
    }


def _validate_command(command: list[str], *, wrapper: Path) -> list[str]:
    if (
        not isinstance(command, list)
        or not command
        or len(command) > 64
        or any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\x00" in value
            or len(value) > 4_096
            for value in command
        )
    ):
        raise ValueError("baseline adapter command must be a bounded canonical argv array")
    executable = Path(command[0])
    if not executable.is_absolute():
        raise ValueError("baseline adapter executable must use an absolute path")
    if str(wrapper) not in command:
        raise ValueError("the exact absolute wrapper path must be present in the command")
    return list(command)


def _environment_contract(
    fixed: dict[str, str],
    *,
    inherit_environment: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    requested = list(_DEFAULT_INHERITED_ENVIRONMENT) + list(inherit_environment)
    names: list[str] = []
    for name in requested:
        if not isinstance(name, str) or not _ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError("inherited environment names must use a closed identifier grammar")
        if name.startswith("DEEPLAW_BASELINE_"):
            raise ValueError("fixed baseline environment variables cannot be inherited")
        if name not in names and name in os.environ:
            value = os.environ[name]
            if len(value) > 32_768 or "\x00" in value:
                raise ValueError(f"inherited environment value is oversized: {name}")
            names.append(name)
        elif name in inherit_environment and name not in os.environ:
            raise ValueError(f"requested inherited environment variable is absent: {name}")
    names.sort()
    if len(names) > 64:
        raise ValueError("too many inherited environment variables")
    inherited = {name: os.environ[name] for name in names}
    return {
        "fixed": fixed,
        "inherited_names": names,
        "inherited_values_sha256": sha256_bytes(
            canonical_json(inherited).encode("utf-8")
        ),
    }


def _artifact_paths(
    *,
    output: Path,
    resource_record: Path,
    stdout_log: Path,
    stderr_log: Path,
    receipt: Path,
) -> dict[str, str]:
    values = {
        "raw_output_path_hint": str(_absolute(output, field="baseline output")),
        "resource_record_path_hint": str(
            _absolute(resource_record, field="baseline resource record")
        ),
        "stdout_path_hint": str(_absolute(stdout_log, field="baseline stdout log")),
        "stderr_path_hint": str(_absolute(stderr_log, field="baseline stderr log")),
        "receipt_path_hint": str(_absolute(receipt, field="baseline receipt")),
    }
    paths = [Path(value) for value in values.values()]
    if len(set(paths)) != len(paths):
        raise ValueError(
            "baseline output, resource record, logs, and receipt must use distinct paths"
        )
    for path in paths:
        if path.exists() or path.is_symlink():
            raise FileExistsError(
                "baseline output, resource record, logs, and receipt must use new paths"
            )
        if path.parent == Path(path.anchor):
            raise ValueError(
                "baseline artifacts must not be written directly under a filesystem root"
            )
    return values


def _fixed_environment(
    *,
    system: dict[str, Any],
    registry_sha256_value: str,
    checkout: Path,
    checkout_revision: str,
    corpus: dict[str, Any],
    queries: dict[str, Any],
    evaluation_environment: dict[str, Any],
    output: Path,
    resource_record: Path,
) -> dict[str, str]:
    return {
        "DEEPLAW_BASELINE_CHECKOUT": str(checkout),
        "DEEPLAW_BASELINE_CONFIG_JSON": canonical_json(system["configuration"]),
        "DEEPLAW_BASELINE_CORPUS": corpus["path_hint"],
        "DEEPLAW_BASELINE_CORPUS_SHA256": corpus["sha256"],
        "DEEPLAW_BASELINE_EVALUATION_ENVIRONMENT": evaluation_environment[
            "path_hint"
        ],
        "DEEPLAW_BASELINE_EVALUATION_ENVIRONMENT_RECORD_SHA256": (
            evaluation_environment["record_sha256"]
        ),
        "DEEPLAW_BASELINE_IMPLEMENTATION_REVISION": checkout_revision,
        "DEEPLAW_BASELINE_NETWORK_POLICY": system["network_policy"],
        "DEEPLAW_BASELINE_OUTPUT": str(output),
        "DEEPLAW_BASELINE_QUERIES": queries["path_hint"],
        "DEEPLAW_BASELINE_QUERIES_SHA256": queries["sha256"],
        "DEEPLAW_BASELINE_QUERY_CASE_IDS_SHA256": queries["case_ids_sha256"],
        "DEEPLAW_BASELINE_REGISTRY_SHA256": registry_sha256_value,
        "DEEPLAW_BASELINE_RESOURCE_RECORD": str(resource_record),
        "DEEPLAW_BASELINE_SYSTEM_ID": system["system_id"],
    }


def build_execution_plan(
    *,
    registry: dict[str, Any] | None = None,
    registry_path: Path | None = None,
    system_id: str,
    checkout: Path,
    corpus: Path,
    queries: Path,
    evaluation_environment: Path,
    output: Path,
    resource_record: Path,
    stdout_log: Path,
    stderr_log: Path,
    receipt: Path,
    wrapper: Path,
    command: list[str],
    inherit_environment: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    selected_registry_path = _absolute(
        registry_path or default_registry_path(), field="baseline registry"
    )
    observed_registry, registry_binding = _registry_state(selected_registry_path)
    if registry is not None and canonical_json(registry) != canonical_json(observed_registry):
        raise RuntimeError("provided baseline registry differs from its exact registry path")
    system = _system(observed_registry, system_id)
    checkout_binding = _checkout_binding(checkout)
    if checkout_binding["revision"] != system["implementation"]["revision"]:
        raise RuntimeError("baseline checkout does not match the pinned official revision")
    corpus_binding = _input_binding(corpus, field="baseline corpus")
    queries_binding = _queries_binding(queries)
    evaluation_environment_binding = _validate_evaluation_environment(
        evaluation_environment,
        registry=observed_registry,
        system=system,
    )
    wrapper_path = _absolute(wrapper, field="baseline wrapper")
    argv = _validate_command(command, wrapper=wrapper_path)
    executable_binding = _file_binding(
        Path(argv[0]),
        field="baseline command executable",
        allow_symlink=True,
        require_executable=True,
    )
    wrapper_binding = _file_binding(
        wrapper_path,
        field="baseline wrapper",
        allow_symlink=False,
    )
    artifacts = _artifact_paths(
        output=output,
        resource_record=resource_record,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        receipt=receipt,
    )
    protected_paths = {
        Path(registry_binding["path_hint"]),
        Path(checkout_binding["path_hint"]),
        Path(corpus_binding["path_hint"]),
        Path(queries_binding["path_hint"]),
        Path(evaluation_environment_binding["path_hint"]),
        Path(wrapper_binding["path_hint"]),
        Path(executable_binding["path_hint"]),
    }
    if protected_paths.intersection(Path(value) for value in artifacts.values()):
        raise ValueError("baseline artifacts must not overlap frozen inputs or executables")
    fixed_environment = _fixed_environment(
        system=system,
        registry_sha256_value=registry_binding["canonical_sha256"],
        checkout=Path(checkout_binding["path_hint"]),
        checkout_revision=checkout_binding["revision"],
        corpus=corpus_binding,
        queries=queries_binding,
        evaluation_environment=evaluation_environment_binding,
        output=Path(artifacts["raw_output_path_hint"]),
        resource_record=Path(artifacts["resource_record_path_hint"]),
    )
    body = {
        "schema_version": EXECUTION_PLAN_SCHEMA,
        "registry": registry_binding,
        "system": system,
        "checkout": checkout_binding,
        "corpus": corpus_binding,
        "queries": queries_binding,
        "evaluation_environment": evaluation_environment_binding,
        "artifacts": artifacts,
        "command": argv,
        "executable": executable_binding,
        "wrapper": wrapper_binding,
        "environment_contract": _environment_contract(
            fixed_environment,
            inherit_environment=inherit_environment,
        ),
        "network_control": {
            "policy": system["network_policy"],
            "runner_enforces_network_isolation": False,
            "required_external_enforcement": "evaluator-provided-os-sandbox",
        },
        "result_status": "planned_not_executed",
    }
    return {
        **body,
        "plan_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def _validate_input_binding(value: Any, *, field: str, queries: bool) -> dict[str, Any]:
    keys = {"path_hint", "sha256", "byte_size"}
    if queries:
        keys |= {"case_count", "case_ids_sha256"}
    binding = _closed_dict(value, field=field, keys=keys)
    _path_hint(binding.get("path_hint"), field=f"{field}.path_hint")
    if not _SHA256.fullmatch(str(binding.get("sha256"))):
        raise ValueError(f"{field}.sha256 is invalid")
    if (
        isinstance(binding.get("byte_size"), bool)
        or not isinstance(binding.get("byte_size"), int)
        or not 1 <= binding["byte_size"] <= _MAX_INPUT_BYTES
    ):
        raise ValueError(f"{field}.byte_size is invalid")
    if queries and (
        isinstance(binding.get("case_count"), bool)
        or not isinstance(binding.get("case_count"), int)
        or not 1 <= binding["case_count"] <= _MAX_CASES
        or not _SHA256.fullmatch(str(binding.get("case_ids_sha256")))
    ):
        raise ValueError("queries case inventory is invalid")
    return binding


def _validate_plan(plan: Any) -> dict[str, Any]:
    value = _closed_dict(
        plan,
        field="official baseline execution plan",
        keys={
            "schema_version",
            "registry",
            "system",
            "checkout",
            "corpus",
            "queries",
            "evaluation_environment",
            "artifacts",
            "command",
            "executable",
            "wrapper",
            "environment_contract",
            "network_control",
            "result_status",
            "plan_sha256",
        },
    )
    if (
        value.get("schema_version") != EXECUTION_PLAN_SCHEMA
        or value.get("result_status") != "planned_not_executed"
    ):
        raise ValueError("official baseline execution plan is invalid")
    body = {key: item for key, item in value.items() if key != "plan_sha256"}
    if value.get("plan_sha256") != sha256_bytes(canonical_json(body).encode("utf-8")):
        raise ValueError("official baseline execution plan digest is invalid")
    registry = _closed_dict(
        value.get("registry"),
        field="plan registry",
        keys={"path_hint", "file_sha256", "canonical_sha256"},
    )
    _path_hint(registry.get("path_hint"), field="plan registry.path_hint")
    if not all(
        _SHA256.fullmatch(str(registry.get(field)))
        for field in ("file_sha256", "canonical_sha256")
    ):
        raise ValueError("plan registry digests are invalid")
    if not isinstance(value.get("system"), dict):
        raise ValueError("plan system entry is invalid")
    checkout = _closed_dict(
        value.get("checkout"),
        field="plan checkout",
        keys={
            "path_hint",
            "revision",
            "clean",
            "submodule_count",
            "submodule_status_sha256",
        },
    )
    _path_hint(checkout.get("path_hint"), field="plan checkout.path_hint")
    if checkout.get("clean") is not True or not _SHA256.fullmatch(
        str(checkout.get("submodule_status_sha256"))
    ):
        raise ValueError("plan checkout state is invalid")
    if (
        not isinstance(checkout.get("revision"), str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", checkout["revision"])
        or isinstance(checkout.get("submodule_count"), bool)
        or not isinstance(checkout.get("submodule_count"), int)
        or not 0 <= checkout["submodule_count"] <= 10_000
    ):
        raise ValueError("plan checkout inventory is invalid")
    _validate_input_binding(value.get("corpus"), field="plan corpus", queries=False)
    _validate_input_binding(value.get("queries"), field="plan queries", queries=True)
    _validate_record_binding(
        value.get("evaluation_environment"),
        field="plan evaluation environment",
    )
    artifacts = _closed_dict(
        value.get("artifacts"),
        field="plan artifacts",
        keys={
            "raw_output_path_hint",
            "resource_record_path_hint",
            "stdout_path_hint",
            "stderr_path_hint",
            "receipt_path_hint",
        },
    )
    artifact_paths = [
        _path_hint(item, field=f"plan artifacts.{field}")
        for field, item in artifacts.items()
    ]
    if len(set(artifact_paths)) != len(artifact_paths):
        raise ValueError("plan artifact paths must be distinct")
    wrapper = _validate_file_binding(value.get("wrapper"), field="plan wrapper")
    executable = _validate_file_binding(value.get("executable"), field="plan executable")
    command = _validate_command(
        value.get("command"),
        wrapper=Path(wrapper["path_hint"]),
    )
    if command[0] != executable["path_hint"]:
        raise ValueError("plan command executable does not match its binding")
    environment = _closed_dict(
        value.get("environment_contract"),
        field="plan environment contract",
        keys={"fixed", "inherited_names", "inherited_values_sha256"},
    )
    fixed = environment.get("fixed")
    if not isinstance(fixed, dict) or set(fixed) != {
        "DEEPLAW_BASELINE_CHECKOUT",
        "DEEPLAW_BASELINE_CONFIG_JSON",
        "DEEPLAW_BASELINE_CORPUS",
        "DEEPLAW_BASELINE_CORPUS_SHA256",
        "DEEPLAW_BASELINE_EVALUATION_ENVIRONMENT",
        "DEEPLAW_BASELINE_EVALUATION_ENVIRONMENT_RECORD_SHA256",
        "DEEPLAW_BASELINE_IMPLEMENTATION_REVISION",
        "DEEPLAW_BASELINE_NETWORK_POLICY",
        "DEEPLAW_BASELINE_OUTPUT",
        "DEEPLAW_BASELINE_QUERIES",
        "DEEPLAW_BASELINE_QUERIES_SHA256",
        "DEEPLAW_BASELINE_QUERY_CASE_IDS_SHA256",
        "DEEPLAW_BASELINE_REGISTRY_SHA256",
        "DEEPLAW_BASELINE_RESOURCE_RECORD",
        "DEEPLAW_BASELINE_SYSTEM_ID",
    } or any(not isinstance(item, str) or len(item) > 32_768 for item in fixed.values()):
        raise ValueError("plan fixed environment is invalid")
    names = environment.get("inherited_names")
    if (
        not isinstance(names, list)
        or names != sorted(set(names))
        or len(names) > 64
        or any(not isinstance(name, str) or not _ENVIRONMENT_NAME.fullmatch(name) for name in names)
        or not _SHA256.fullmatch(str(environment.get("inherited_values_sha256")))
    ):
        raise ValueError("plan inherited environment contract is invalid")
    network = _closed_dict(
        value.get("network_control"),
        field="plan network control",
        keys={
            "policy",
            "runner_enforces_network_isolation",
            "required_external_enforcement",
        },
    )
    if (
        network.get("runner_enforces_network_isolation") is not False
        or network.get("required_external_enforcement") != "evaluator-provided-os-sandbox"
    ):
        raise ValueError("plan network control overstates runner enforcement")
    return value


def validate_execution_plan(plan: Any) -> dict[str, Any]:
    """Validate a content-bound execution plan without launching it."""

    return _validate_plan(plan)


def _verify_live_plan_bindings(plan: dict[str, Any]) -> tuple[Path, list[str]]:
    registry_path = Path(plan["registry"]["path_hint"])
    registry, registry_binding = _registry_state(registry_path)
    if registry_binding != plan["registry"]:
        raise RuntimeError("baseline registry bytes or canonical content changed after planning")
    system_id = _bounded_string(plan["system"].get("system_id"), field="plan system_id")
    if canonical_json(_system(registry, system_id)) != canonical_json(plan["system"]):
        raise RuntimeError("baseline system registry entry changed after planning")
    checkout_path = Path(plan["checkout"]["path_hint"])
    if _checkout_binding(checkout_path) != plan["checkout"]:
        raise RuntimeError("baseline checkout changed after planning")
    corpus = Path(plan["corpus"]["path_hint"])
    if _input_binding(corpus, field="baseline corpus") != plan["corpus"]:
        raise RuntimeError("baseline corpus changed after planning")
    queries = Path(plan["queries"]["path_hint"])
    if _queries_binding(queries) != plan["queries"]:
        raise RuntimeError("baseline queries changed after planning")
    evaluation_environment = Path(plan["evaluation_environment"]["path_hint"])
    if _validate_evaluation_environment(
        evaluation_environment,
        registry=registry,
        system=plan["system"],
    ) != plan["evaluation_environment"]:
        raise RuntimeError(
            "baseline evaluation environment changed after planning"
        )
    if _file_binding(
        Path(plan["executable"]["path_hint"]),
        field="baseline command executable",
        allow_symlink=True,
        require_executable=True,
    ) != plan["executable"]:
        raise RuntimeError("baseline command executable changed after planning")
    if _file_binding(
        Path(plan["wrapper"]["path_hint"]),
        field="baseline wrapper",
        allow_symlink=False,
    ) != plan["wrapper"]:
        raise RuntimeError("baseline wrapper changed after planning")
    fixed = _fixed_environment(
        system=plan["system"],
        registry_sha256_value=plan["registry"]["canonical_sha256"],
        checkout=checkout_path,
        checkout_revision=plan["checkout"]["revision"],
        corpus=plan["corpus"],
        queries=plan["queries"],
        evaluation_environment=plan["evaluation_environment"],
        output=Path(plan["artifacts"]["raw_output_path_hint"]),
        resource_record=Path(plan["artifacts"]["resource_record_path_hint"]),
    )
    if fixed != plan["environment_contract"]["fixed"]:
        raise RuntimeError("baseline fixed environment contract changed after planning")
    names = plan["environment_contract"]["inherited_names"]
    missing = [name for name in names if name not in os.environ]
    if missing:
        raise RuntimeError("baseline inherited environment changed after planning")
    inherited = {name: os.environ[name] for name in names}
    if sha256_bytes(canonical_json(inherited).encode("utf-8")) != plan[
        "environment_contract"
    ]["inherited_values_sha256"]:
        raise RuntimeError("baseline inherited environment changed after planning")
    if plan["network_control"]["policy"] != plan["system"]["network_policy"]:
        raise RuntimeError("baseline network policy changed after planning")
    return checkout_path, names


def _ensure_artifact_parent(path: Path) -> None:
    missing: list[Path] = []
    current = path.parent
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError("baseline artifact parent must descend from a non-symlink directory")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    current = path.parent
    while current != Path(current.anchor):
        if current.is_symlink() or not current.is_dir():
            raise ValueError("baseline artifact parent contains a symlink or non-directory")
        current = current.parent


def _reserve_binary(path: Path) -> BinaryIO:
    _ensure_artifact_parent(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb")


def _write_reserved(handle: BinaryIO, payload: bytes) -> None:
    handle.write(payload)
    handle.flush()
    os.fsync(handle.fileno())
    handle.close()


def _validate_retrieved(value: Any, *, line_number: int) -> None:
    if not isinstance(value, list) or len(value) > _MAX_RETRIEVED_ITEMS:
        raise RuntimeError(
            f"official baseline output record {line_number} has invalid retrieved items"
        )
    for item in value:
        if not isinstance(item, dict) or set(item) != {
            "id",
            "chars",
            "provenance_valid",
        }:
            raise RuntimeError(
                f"official baseline output record {line_number} has an invalid retrieved item"
            )
        _bounded_string(
            item.get("id"),
            field=f"official baseline output record {line_number} retrieved id",
            maximum=1_000,
        )
        if (
            isinstance(item.get("chars"), bool)
            or not isinstance(item.get("chars"), int)
            or not 0 <= item["chars"] <= 1_000_000_000
            or not isinstance(item.get("provenance_valid"), bool)
        ):
            raise RuntimeError(
                f"official baseline output record {line_number} has invalid retrieved fields"
            )


def _validate_output(path: Path, *, expected_case_ids: list[str]) -> dict[str, Any]:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= _MAX_OUTPUT_BYTES
    ):
        raise RuntimeError("official baseline output is missing, unsafe, empty, or oversized")
    case_ids: set[str] = set()
    failed_case_ids: set[str] = set()
    count = 0
    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if len(raw_line) > _MAX_JSONL_LINE_BYTES:
                raise RuntimeError(
                    f"official baseline output record {line_number} exceeds its byte bound"
                )
            if not raw_line.strip():
                continue
            count += 1
            if count > _MAX_CASES:
                raise RuntimeError("official baseline output exceeds its case bound")
            try:
                value = strict_json_loads(raw_line)
            except (UnicodeDecodeError, ValueError) as error:
                raise RuntimeError(
                    f"official baseline output record {line_number} is not strict JSON"
                ) from error
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "schema_version",
                    "case_id",
                    "retrieved",
                    "latency_ms",
                    "task_success",
                }
                or value.get("schema_version") != SCHEMA_RUN
            ):
                raise RuntimeError(
                    f"official baseline output record {line_number} violates the JSONL protocol"
                )
            case_id = _bounded_string(
                value.get("case_id"),
                field=f"official baseline output record {line_number} case_id",
                maximum=500,
            )
            if case_id in case_ids:
                raise RuntimeError(f"official baseline output duplicates case_id: {case_id}")
            _validate_retrieved(value.get("retrieved"), line_number=line_number)
            latency = value.get("latency_ms")
            if (
                isinstance(latency, bool)
                or not isinstance(latency, (int, float))
                or not math.isfinite(latency)
                or latency < 0
            ):
                raise RuntimeError(
                    f"official baseline output record {line_number} has invalid latency_ms"
                )
            if value.get("task_success") is not None and not isinstance(
                value.get("task_success"), bool
            ):
                raise RuntimeError(
                    f"official baseline output record {line_number} has invalid task_success"
                )
            if value.get("task_success") is False:
                failed_case_ids.add(case_id)
            case_ids.add(case_id)
    expected = set(expected_case_ids)
    missing_count = len(expected - case_ids)
    extra_count = len(case_ids - expected)
    if missing_count or extra_count:
        raise RuntimeError(
            "official baseline output coverage mismatch: "
            f"missing_count={missing_count}, extra_count={extra_count}"
        )
    return {
        "raw_output": {
            "path_hint": str(path),
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
        },
        "case_count": count,
        "case_ids_sha256": _case_ids_sha256(case_ids),
        "failed_case_ids": sorted(failed_case_ids),
    }


def _safe_raw_output_artifact(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    size = path.stat().st_size
    if not 0 <= size <= _MAX_OUTPUT_BYTES:
        return None
    return {"path_hint": str(path), "sha256": sha256_file(path), "byte_size": size}


def _safe_resource_record_artifact(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    size = path.stat().st_size
    if not 0 <= size <= _MAX_EVIDENCE_JSON_BYTES:
        return None
    return {"path_hint": str(path), "sha256": sha256_file(path), "byte_size": size}


def _log_artifact(path: Path, payload: bytes, *, truncated: bool) -> dict[str, Any]:
    return {
        "path_hint": str(path),
        "sha256": sha256_bytes(payload),
        "byte_size": len(payload),
        "truncated": truncated,
    }


def _validate_receipt_artifact(
    value: Any,
    *,
    field: str,
    maximum_bytes: int,
    nullable: bool,
    log: bool = False,
) -> dict[str, Any] | None:
    if value is None and nullable:
        return None
    keys = {"path_hint", "sha256", "byte_size"}
    if log:
        keys.add("truncated")
    artifact = _closed_dict(value, field=field, keys=keys)
    _path_hint(artifact.get("path_hint"), field=f"{field}.path_hint")
    if not _SHA256.fullmatch(str(artifact.get("sha256"))):
        raise ValueError(f"{field}.sha256 is invalid")
    if (
        isinstance(artifact.get("byte_size"), bool)
        or not isinstance(artifact.get("byte_size"), int)
        or not 0 <= artifact["byte_size"] <= maximum_bytes
    ):
        raise ValueError(f"{field}.byte_size is invalid")
    if log and not isinstance(artifact.get("truncated"), bool):
        raise ValueError(f"{field}.truncated is invalid")
    return artifact


def validate_execution_receipt(
    receipt: Any,
    *,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a receipt's closed semantics and optional plan binding."""

    value = _closed_dict(
        receipt,
        field="official baseline execution receipt",
        keys={
            "schema_version",
            "plan_sha256",
            "registry_sha256",
            "system_id",
            "implementation_revision",
            "evaluation_environment_record_sha256",
            "execution_status",
            "elapsed_seconds",
            "exit_code",
            "stdout",
            "stderr",
            "raw_output",
            "resource_record",
            "resource_record_sha256",
            "query_case_count",
            "query_case_ids_sha256",
            "output_case_count",
            "output_case_ids_sha256",
            "output_validation",
            "resource_validation",
            "failure_reason",
            "claim_eligible",
            "claim_ineligibility_reason",
            "receipt_sha256",
        },
    )
    if value.get("schema_version") != EXECUTION_RECEIPT_SCHEMA:
        raise ValueError("official baseline receipt schema is invalid")
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if value.get("receipt_sha256") != sha256_bytes(
        canonical_json(body).encode("utf-8")
    ):
        raise ValueError("official baseline receipt digest is invalid")
    for field in (
        "plan_sha256",
        "registry_sha256",
        "evaluation_environment_record_sha256",
        "query_case_ids_sha256",
        "receipt_sha256",
    ):
        if not _SHA256.fullmatch(str(value.get(field))):
            raise ValueError(f"official baseline receipt {field} is invalid")
    _bounded_string(value.get("system_id"), field="receipt system_id", maximum=100)
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(value.get("implementation_revision"))):
        raise ValueError("official baseline receipt implementation revision is invalid")
    status = value.get("execution_status")
    if status not in {
        "succeeded",
        "command_failed",
        "bounded_subprocess_failed",
        "output_invalid",
        "resource_invalid",
    }:
        raise ValueError("official baseline receipt status is invalid")
    _nonnegative_number(value.get("elapsed_seconds"), field="elapsed_seconds")
    exit_code = value.get("exit_code")
    if exit_code is not None and (
        isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        raise ValueError("official baseline receipt exit_code is invalid")
    _validate_receipt_artifact(
        value.get("stdout"),
        field="receipt stdout",
        maximum_bytes=_MAX_PROCESS_STDOUT_BYTES,
        nullable=False,
        log=True,
    )
    _validate_receipt_artifact(
        value.get("stderr"),
        field="receipt stderr",
        maximum_bytes=_MAX_PROCESS_STDERR_BYTES,
        nullable=False,
        log=True,
    )
    raw_output = _validate_receipt_artifact(
        value.get("raw_output"),
        field="receipt raw_output",
        maximum_bytes=_MAX_OUTPUT_BYTES,
        nullable=True,
    )
    resource_record = _validate_receipt_artifact(
        value.get("resource_record"),
        field="receipt resource_record",
        maximum_bytes=_MAX_EVIDENCE_JSON_BYTES,
        nullable=True,
    )
    query_count = value.get("query_case_count")
    if (
        isinstance(query_count, bool)
        or not isinstance(query_count, int)
        or not 1 <= query_count <= _MAX_CASES
    ):
        raise ValueError("official baseline receipt query case count is invalid")
    output_count = value.get("output_case_count")
    output_digest = value.get("output_case_ids_sha256")
    if output_count is not None and (
        isinstance(output_count, bool)
        or not isinstance(output_count, int)
        or not 1 <= output_count <= _MAX_CASES
    ):
        raise ValueError("official baseline receipt output case count is invalid")
    if output_digest is not None and not _SHA256.fullmatch(str(output_digest)):
        raise ValueError("official baseline receipt output case digest is invalid")
    output_validation = value.get("output_validation")
    resource_validation = value.get("resource_validation")
    if output_validation not in {"passed", "failed", "not_run"} or (
        resource_validation not in {"passed", "failed", "not_run"}
    ):
        raise ValueError("official baseline receipt validation state is invalid")
    resource_digest = value.get("resource_record_sha256")
    if resource_digest is not None and not _SHA256.fullmatch(str(resource_digest)):
        raise ValueError("official baseline resource record digest is invalid")
    failure_reason = value.get("failure_reason")
    if failure_reason is not None:
        _bounded_string(failure_reason, field="receipt failure_reason", maximum=1_000)
    if (
        value.get("claim_eligible") is not False
        or value.get("claim_ineligibility_reason") != _CLAIM_INELIGIBILITY_REASON
    ):
        raise ValueError("official baseline receipt overstates claim eligibility")
    if output_validation == "passed":
        if (
            raw_output is None
            or output_count != query_count
            or output_digest != value["query_case_ids_sha256"]
        ):
            raise ValueError("passed output validation has inconsistent coverage")
    elif output_count is not None or output_digest is not None:
        raise ValueError("unvalidated output must not report validated coverage")
    if resource_validation == "passed":
        if resource_record is None or resource_digest is None:
            raise ValueError("passed resource validation is missing its record binding")
    elif resource_digest is not None:
        raise ValueError("unvalidated resource record must not expose a canonical digest")
    if status == "succeeded":
        if (
            exit_code != 0
            or output_validation != "passed"
            or resource_validation != "passed"
            or failure_reason is not None
        ):
            raise ValueError("successful receipt has inconsistent validation state")
    else:
        if failure_reason is None:
            raise ValueError("failed official baseline receipt lacks a failure reason")
        if status == "command_failed" and (
            not isinstance(exit_code, int) or exit_code == 0
        ):
            raise ValueError("command failure receipt has an invalid exit code")
        if status in {"command_failed", "bounded_subprocess_failed"} and (
            output_validation != "not_run" or resource_validation != "not_run"
        ):
            raise ValueError("subprocess failure receipt overstates artifact validation")
        if status == "output_invalid" and (
            exit_code != 0 or output_validation != "failed"
        ):
            raise ValueError("output-invalid receipt has inconsistent state")
        if status == "resource_invalid" and (
            exit_code != 0
            or output_validation != "passed"
            or resource_validation != "failed"
        ):
            raise ValueError("resource-invalid receipt has inconsistent state")
    if plan is not None:
        validated_plan = _validate_plan(plan)
        expected = {
            "plan_sha256": validated_plan["plan_sha256"],
            "registry_sha256": validated_plan["registry"]["canonical_sha256"],
            "system_id": validated_plan["system"]["system_id"],
            "implementation_revision": validated_plan["checkout"]["revision"],
            "evaluation_environment_record_sha256": validated_plan[
                "evaluation_environment"
            ]["record_sha256"],
            "query_case_count": validated_plan["queries"]["case_count"],
            "query_case_ids_sha256": validated_plan["queries"]["case_ids_sha256"],
        }
        if any(value.get(field) != expected_value for field, expected_value in expected.items()):
            raise ValueError("official baseline receipt does not match its execution plan")
        artifact_paths = validated_plan["artifacts"]
        expected_paths = {
            "stdout": artifact_paths["stdout_path_hint"],
            "stderr": artifact_paths["stderr_path_hint"],
            "raw_output": artifact_paths["raw_output_path_hint"],
            "resource_record": artifact_paths["resource_record_path_hint"],
        }
        for field, expected_path in expected_paths.items():
            artifact = value.get(field)
            if artifact is not None and artifact["path_hint"] != expected_path:
                raise ValueError(f"receipt {field} path differs from its execution plan")
    return value


def _receipt(
    *,
    plan: dict[str, Any],
    status: str,
    elapsed_seconds: float,
    exit_code: int | None,
    stdout: dict[str, Any],
    stderr: dict[str, Any],
    raw_output: dict[str, Any] | None,
    resource_record: dict[str, Any] | None,
    resource_record_sha256: str | None,
    case_count: int | None,
    case_ids_sha256: str | None,
    output_validation: str,
    resource_validation: str,
    failure_reason: str | None,
) -> dict[str, Any]:
    body = {
        "schema_version": EXECUTION_RECEIPT_SCHEMA,
        "plan_sha256": plan["plan_sha256"],
        "registry_sha256": plan["registry"]["canonical_sha256"],
        "system_id": plan["system"]["system_id"],
        "implementation_revision": plan["checkout"]["revision"],
        "evaluation_environment_record_sha256": plan["evaluation_environment"][
            "record_sha256"
        ],
        "execution_status": status,
        "elapsed_seconds": elapsed_seconds,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "raw_output": raw_output,
        "resource_record": resource_record,
        "resource_record_sha256": resource_record_sha256,
        "query_case_count": plan["queries"]["case_count"],
        "query_case_ids_sha256": plan["queries"]["case_ids_sha256"],
        "output_case_count": case_count,
        "output_case_ids_sha256": case_ids_sha256,
        "output_validation": output_validation,
        "resource_validation": resource_validation,
        "failure_reason": failure_reason,
        "claim_eligible": False,
        "claim_ineligibility_reason": _CLAIM_INELIGIBILITY_REASON,
    }
    return {
        **body,
        "receipt_sha256": sha256_bytes(canonical_json(body).encode("utf-8")),
    }


def execute_plan(plan: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    validated = _validate_plan(plan)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= 7 * 24 * 60 * 60
    ):
        raise ValueError("official baseline timeout must be between one second and seven days")
    checkout, inherited_names = _verify_live_plan_bindings(validated)
    artifacts = validated["artifacts"]
    output = Path(artifacts["raw_output_path_hint"])
    resource_path = Path(artifacts["resource_record_path_hint"])
    stdout_path = Path(artifacts["stdout_path_hint"])
    stderr_path = Path(artifacts["stderr_path_hint"])
    receipt_path = Path(artifacts["receipt_path_hint"])
    for path in (output, resource_path, stdout_path, stderr_path, receipt_path):
        if path.exists() or path.is_symlink():
            raise FileExistsError("official baseline artifact paths must still be new")
    _ensure_artifact_parent(output)
    _ensure_artifact_parent(resource_path)
    stdout_handle = _reserve_binary(stdout_path)
    stderr_handle = _reserve_binary(stderr_path)
    receipt_handle = _reserve_binary(receipt_path)
    environment = {name: os.environ[name] for name in inherited_names}
    environment.update(validated["environment_contract"]["fixed"])
    stdout_bytes = b""
    stderr_bytes = b""
    stdout_truncated = False
    stderr_truncated = False
    exit_code: int | None = None
    status = "succeeded"
    failure_reason: str | None = None
    started = time.perf_counter()
    try:
        completed = run_bounded_subprocess(
            validated["command"],
            environment=environment,
            cwd=checkout,
            timeout_seconds=timeout_seconds,
            max_stdout_bytes=_MAX_PROCESS_STDOUT_BYTES,
            max_stderr_bytes=_MAX_PROCESS_STDERR_BYTES,
        )
        stdout_bytes = completed.stdout
        stderr_bytes = completed.stderr
        exit_code = completed.returncode
        if exit_code != 0:
            status = "command_failed"
            failure_reason = f"official baseline command exited with code {exit_code}"
    except BoundedSubprocessError as error:
        stdout_bytes = error.stdout
        stderr_bytes = error.stderr
        stdout_truncated = error.stdout_truncated
        stderr_truncated = error.stderr_truncated
        exit_code = error.returncode
        status = "bounded_subprocess_failed"
        failure_reason = str(error)[:1_000]
    elapsed = time.perf_counter() - started
    _write_reserved(stdout_handle, stdout_bytes)
    _write_reserved(stderr_handle, stderr_bytes)
    output_report: dict[str, Any] | None = None
    resource_report: dict[str, Any] | None = None
    output_validation = "not_run"
    resource_validation = "not_run"
    if status == "succeeded":
        output_error: str | None = None
        resource_error: str | None = None
        try:
            expected_case_ids = _read_query_case_ids(Path(validated["queries"]["path_hint"]))
            output_report = _validate_output(output, expected_case_ids=expected_case_ids)
            output_validation = "passed"
        except (RuntimeError, ValueError, UnicodeDecodeError) as error:
            output_validation = "failed"
            output_error = str(error)[:1_000]
        try:
            resource_report = _validate_resource_record(resource_path, plan=validated)
            resource_validation = "passed"
        except (RuntimeError, ValueError, UnicodeDecodeError) as error:
            resource_validation = "failed"
            resource_error = str(error)[:1_000]
        if (
            output_report is not None
            and resource_report is not None
            and not set(output_report["failed_case_ids"]) <= set(
                resource_report["failure_case_ids"]
            )
        ):
            resource_report = None
            resource_validation = "failed"
            resource_error = (
                "task failures in raw output are absent from the resource record"
            )
        if output_error is not None:
            status = "output_invalid"
            failure_reason = output_error
            if resource_error is not None:
                suffix = f"; resource record invalid: {resource_error}"
                failure_reason = (failure_reason + suffix)[:1_000]
        elif resource_error is not None:
            status = "resource_invalid"
            failure_reason = resource_error
    raw_output = (
        output_report["raw_output"]
        if output_report is not None
        else _safe_raw_output_artifact(output)
    )
    resource_record = (
        resource_report["artifact"]
        if resource_report is not None
        else _safe_resource_record_artifact(resource_path)
    )
    receipt = _receipt(
        plan=validated,
        status=status,
        elapsed_seconds=elapsed,
        exit_code=exit_code,
        stdout=_log_artifact(stdout_path, stdout_bytes, truncated=stdout_truncated),
        stderr=_log_artifact(stderr_path, stderr_bytes, truncated=stderr_truncated),
        raw_output=raw_output,
        resource_record=resource_record,
        resource_record_sha256=(
            resource_report["record_sha256"] if resource_report is not None else None
        ),
        case_count=output_report["case_count"] if output_report is not None else None,
        case_ids_sha256=(
            output_report["case_ids_sha256"] if output_report is not None else None
        ),
        output_validation=output_validation,
        resource_validation=resource_validation,
        failure_reason=failure_reason,
    )
    validate_execution_receipt(receipt, plan=validated)
    _write_reserved(
        receipt_handle,
        (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return receipt


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    selected = _absolute(path, field="baseline execution plan output")
    if selected.exists() or selected.is_symlink():
        raise FileExistsError("baseline execution plan must be written to a new path")
    handle = _reserve_binary(selected)
    _write_reserved(
        handle,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan or execute a pinned official baseline JSONL subprocess"
    )
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--system-id", required=True)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--evaluation-environment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resource-record", type=Path, required=True)
    parser.add_argument("--stdout-log", type=Path, required=True)
    parser.add_argument("--stderr-log", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--inherit-env", action="append", default=[])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=86_400)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command:
        raise ValueError("official baseline adapter requires a command after --")
    command = args.command[1:] if args.command[0] == "--" else args.command
    registry_path = args.registry.expanduser().absolute()
    registry = load_registry(registry_path)
    plan = build_execution_plan(
        registry=registry,
        registry_path=registry_path,
        system_id=args.system_id,
        checkout=args.checkout.expanduser().absolute(),
        corpus=args.corpus.expanduser().absolute(),
        queries=args.queries.expanduser().absolute(),
        evaluation_environment=args.evaluation_environment.expanduser().absolute(),
        output=args.output.expanduser().absolute(),
        resource_record=args.resource_record.expanduser().absolute(),
        stdout_log=args.stdout_log.expanduser().absolute(),
        stderr_log=args.stderr_log.expanduser().absolute(),
        receipt=args.receipt.expanduser().absolute(),
        wrapper=args.wrapper.expanduser().absolute(),
        command=command,
        inherit_environment=args.inherit_env,
    )
    _write_json_exclusive(args.plan, plan)
    result: dict[str, Any] = plan
    exit_code = 0
    if args.execute:
        result = execute_plan(plan, timeout_seconds=args.timeout_seconds)
        exit_code = int(result["execution_status"] != "succeeded")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
