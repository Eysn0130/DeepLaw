from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .bounded_subprocess import BoundedSubprocessError, run_bounded_subprocess
from .knowledge_models import ASSET_KINDS, canonical_timestamp
from .util import canonical_json, sha256_bytes, sha256_file, strict_json_loads

TYPED_EXTRACTOR_MANIFEST_SCHEMA = "deeplaw.typed-extractor-manifest/v1"
TYPED_EXTRACTOR_REQUEST_SCHEMA = "deeplaw.typed-extractor-request/v1"
TYPED_EXTRACTOR_OUTPUT_SCHEMA = "deeplaw.typed-extractor-output/v1"

_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_INPUT_CHARS = 2_000_000
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
_MAX_PROPOSALS = 100_000
_ENVIRONMENT_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def load_typed_extractor_manifest(
    path: str | Path,
    *,
    expected_mode: str,
) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().absolute()
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > _MAX_MANIFEST_BYTES
    ):
        raise ValueError("typed extractor manifest is unavailable or unsafe")
    try:
        manifest = strict_json_loads(manifest_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("typed extractor manifest is invalid") from error
    expected_fields = {
        "schema_version",
        "mode",
        "extractor",
        "extractor_revision",
        "command",
        "model_identity",
        "model_revision",
        "model_files",
        "prompt_config_sha256",
        "network_policy",
        "environment",
        "max_input_chars",
        "max_output_bytes",
        "timeout_seconds",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise ValueError("typed extractor manifest does not match its closed contract")
    if (
        manifest["schema_version"] != TYPED_EXTRACTOR_MANIFEST_SCHEMA
        or manifest["mode"] != expected_mode
        or expected_mode not in {"local-model-v1", "external-model-explicit"}
    ):
        raise ValueError("typed extractor manifest mode is invalid")
    for field in ("extractor", "extractor_revision", "model_identity", "model_revision"):
        value = manifest[field]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
            raise ValueError(f"typed extractor {field} is invalid")
    prompt_hash = manifest["prompt_config_sha256"]
    if not isinstance(prompt_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", prompt_hash):
        raise ValueError("typed extractor prompt/config hash is invalid")
    command = manifest["command"]
    if (
        not isinstance(command, list)
        or not 1 <= len(command) <= 32
        or any(not isinstance(value, str) or not 1 <= len(value) <= 1_000 for value in command)
    ):
        raise ValueError("typed extractor command is invalid")
    executable = Path(command[0]).expanduser()
    if (
        not executable.is_absolute()
        or executable.is_symlink()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise ValueError("typed extractor executable must be an exact local executable")
    environment = manifest["environment"]
    if (
        not isinstance(environment, list)
        or len(environment) > 16
        or len(environment) != len(set(environment))
        or any(
            not isinstance(name, str) or not _ENVIRONMENT_NAME.fullmatch(name)
            for name in environment
        )
    ):
        raise ValueError("typed extractor environment allowlist is invalid")
    expected_network = "offline" if expected_mode == "local-model-v1" else "explicit-external"
    if manifest["network_policy"] != expected_network:
        raise ValueError("typed extractor network policy does not match its mode")
    if expected_mode == "local-model-v1" and environment:
        raise ValueError("local typed extractor must not receive host environment secrets")
    for field, maximum in (
        ("max_input_chars", _MAX_INPUT_CHARS),
        ("max_output_bytes", _MAX_OUTPUT_BYTES),
        ("timeout_seconds", 600),
    ):
        value = manifest[field]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"typed extractor {field} is invalid")
    model_files = manifest["model_files"]
    if not isinstance(model_files, list) or len(model_files) > 1_000:
        raise ValueError("typed extractor model file inventory is invalid")
    verified_files: list[dict[str, Any]] = []
    verified_paths: set[Path] = set()
    for item in model_files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("typed extractor model file entry is invalid")
        model_path = Path(item["path"]).expanduser().absolute()
        expected_sha256 = item["sha256"]
        if (
            model_path.is_symlink()
            or not model_path.is_file()
            or not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or sha256_file(model_path) != expected_sha256
        ):
            raise ValueError("typed extractor model file verification failed")
        verified_files.append(
            {
                "name": model_path.name,
                "byte_size": model_path.stat().st_size,
                "sha256": expected_sha256,
            }
        )
        verified_paths.add(model_path)
    if expected_mode == "local-model-v1" and not verified_files:
        raise ValueError("local typed extractor requires a verified model file inventory")
    for argument in command[1:]:
        argument_path = Path(argument).expanduser()
        if argument_path.is_absolute() and argument_path not in verified_paths:
            raise ValueError(
                "absolute typed extractor command resources must appear in model_files"
            )
    canonical_manifest = {
        **manifest,
        # Bind the exact invocation without persisting local paths.  The
        # executable and argument commitments are enough to detect a changed
        # sidecar configuration while remaining portable between machines.
        "command": {
            "executable_name": executable.name,
            "executable_sha256": sha256_file(executable),
            "argument_count": len(command) - 1,
            "arguments_sha256": sha256_bytes(
                canonical_json(command[1:]).encode("utf-8")
            ),
        },
        "model_files": verified_files,
    }
    return {
        "path": manifest_path,
        "manifest": manifest,
        "canonical_manifest": canonical_manifest,
        "manifest_sha256": sha256_bytes(
            canonical_json(canonical_manifest).encode("utf-8")
        ),
    }


def run_typed_extractor(
    *,
    manifest_path: str | Path,
    mode: str,
    source_revision_hint: dict[str, Any],
    sections: list[dict[str, Any]],
    confirm_external_disclosure: bool,
) -> dict[str, Any]:
    loaded = load_typed_extractor_manifest(manifest_path, expected_mode=mode)
    manifest = loaded["manifest"]
    if mode == "external-model-explicit" and not confirm_external_disclosure:
        raise ValueError(
            "external typed extraction requires explicit confirmation that section text "
            "and locators will be disclosed to the configured provider"
        )
    request = {
        "schema_version": TYPED_EXTRACTOR_REQUEST_SCHEMA,
        "mode": mode,
        "source": source_revision_hint,
        "sections": sections,
        "output_contract": {
            "schema_version": TYPED_EXTRACTOR_OUTPUT_SCHEMA,
            "authority": "proposal-only",
            "source_ref_indexes_are_zero_based": True,
        },
    }
    request_text = canonical_json(request)
    if len(request_text) > manifest["max_input_chars"]:
        raise ValueError("typed extractor request exceeds the configured input bound")
    process_environment = {
        "PATH": os.defpath,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for name in manifest["environment"]:
        if name not in os.environ:
            raise ValueError(f"typed extractor required environment value is unavailable: {name}")
        process_environment[name] = os.environ[name]
    try:
        process = run_bounded_subprocess(
            manifest["command"],
            input_bytes=request_text.encode("utf-8"),
            environment=process_environment,
            timeout_seconds=manifest["timeout_seconds"],
            max_stdout_bytes=manifest["max_output_bytes"],
            max_stderr_bytes=min(manifest["max_output_bytes"], 64 * 1024),
        )
    except BoundedSubprocessError as error:
        raise RuntimeError("typed extractor sidecar failed closed") from error
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()[:1_000]
        raise RuntimeError(f"typed extractor sidecar failed closed: {detail or process.returncode}")
    try:
        output = strict_json_loads(process.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("typed extractor returned invalid JSON") from error
    proposals = _validate_output(output, section_count=len(sections))
    return {
        "proposals": proposals,
        "extractor": manifest["extractor"],
        "extractor_revision": manifest["extractor_revision"],
        "model_identity": f"{manifest['model_identity']}@{manifest['model_revision']}",
        "prompt_config_sha256": manifest["prompt_config_sha256"],
        "manifest_sha256": loaded["manifest_sha256"],
        "network_policy": manifest["network_policy"],
        "disclosure": (
            "section text and locators disclosed to explicit external provider"
            if mode == "external-model-explicit"
            else "local subprocess only"
        ),
        "output_sha256": sha256_bytes(process.stdout),
    }


def _validate_output(output: Any, *, section_count: int) -> list[dict[str, Any]]:
    if (
        not isinstance(output, dict)
        or set(output) != {"schema_version", "proposals"}
        or output["schema_version"] != TYPED_EXTRACTOR_OUTPUT_SCHEMA
        or not isinstance(output["proposals"], list)
        or len(output["proposals"]) > _MAX_PROPOSALS
    ):
        raise RuntimeError("typed extractor output does not match its closed contract")
    expected = {
        "kind",
        "title",
        "statement",
        "source_ref_indexes",
        "semantic_key_hint",
        "applicability",
        "observed_at",
        "valid_from",
        "valid_to",
        "expires_at",
        "project_scope",
        "repository_scope",
        "branch_scope",
        "version_scope",
        "environment_scope",
        "warnings",
    }
    values: list[dict[str, Any]] = []
    for proposal in output["proposals"]:
        if not isinstance(proposal, dict) or set(proposal) != expected:
            raise RuntimeError("typed extractor proposal does not match its closed contract")
        if proposal["kind"] not in ASSET_KINDS - {"reference"}:
            raise RuntimeError("typed extractor proposal kind is invalid")
        for field, maximum in (("title", 500), ("statement", 20_000)):
            value = proposal[field]
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
                raise RuntimeError(f"typed extractor proposal {field} is invalid")
            proposal[field] = value.strip()
        indexes = proposal["source_ref_indexes"]
        if (
            not isinstance(indexes, list)
            or not indexes
            or len(indexes) > 100
            or len(indexes) != len(set(indexes))
            or any(
                isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < section_count
                for index in indexes
            )
        ):
            raise RuntimeError("typed extractor proposal source references are invalid")
        if not isinstance(proposal["applicability"], dict):
            raise RuntimeError("typed extractor proposal applicability is invalid")
        hint = proposal["semantic_key_hint"]
        if hint is not None and (
            not isinstance(hint, str) or not 1 <= len(hint.strip()) <= 500
        ):
            raise RuntimeError("typed extractor semantic key hint is invalid")
        warnings = proposal["warnings"]
        if (
            not isinstance(warnings, list)
            or len(warnings) > 32
            or any(
                not isinstance(value, str) or not 1 <= len(value.strip()) <= 500
                for value in warnings
            )
        ):
            raise RuntimeError("typed extractor proposal warnings are invalid")
        for field in ("observed_at", "valid_from", "valid_to", "expires_at"):
            value = proposal[field]
            if value is not None:
                try:
                    proposal[field] = canonical_timestamp(value, field=f"proposal {field}")
                except ValueError as error:
                    raise RuntimeError(f"typed extractor proposal {field} is invalid") from error
        for field in (
            "project_scope",
            "repository_scope",
            "branch_scope",
            "version_scope",
            "environment_scope",
        ):
            value = proposal[field]
            if value is not None and (
                not isinstance(value, str) or not 1 <= len(value.strip()) <= 500
            ):
                raise RuntimeError(f"typed extractor proposal {field} is invalid")
        values.append(proposal)
    return values
