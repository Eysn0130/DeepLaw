from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .bounded_subprocess import BoundedSubprocessError, run_bounded_subprocess
from .util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads

LOCAL_RERANKER_MANIFEST_SCHEMA = "deeplaw.local-reranker-manifest/v1"
LOCAL_RERANKER_REQUEST_SCHEMA = "deeplaw.local-reranker-request/v1"
LOCAL_RERANKER_OUTPUT_SCHEMA = "deeplaw.local-reranker-output/v1"

_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_CANDIDATES = 100
_MAX_INPUT_CHARS = 1_000_000
_MAX_OUTPUT_BYTES = 256 * 1024
_ASSET_ID = re.compile(r"asset_[0-9a-f]{24}")


def load_local_reranker_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser().absolute()
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or not 1 <= manifest_path.stat().st_size <= _MAX_MANIFEST_BYTES
    ):
        raise ValueError("local reranker manifest is unavailable or unsafe")
    try:
        manifest = strict_json_loads(manifest_path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("local reranker manifest is invalid") from error
    expected_fields = {
        "schema_version",
        "implementation_revision",
        "model_identity",
        "model_revision",
        "model_files",
        "command",
        "network_policy",
        "max_candidates",
        "max_input_chars",
        "max_output_bytes",
        "timeout_seconds",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise ValueError("local reranker manifest does not match its closed contract")
    if (
        manifest["schema_version"] != LOCAL_RERANKER_MANIFEST_SCHEMA
        or manifest["network_policy"] != "offline"
    ):
        raise ValueError("local reranker manifest policy is invalid")
    for field in ("implementation_revision", "model_identity", "model_revision"):
        value = manifest[field]
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 500:
            raise ValueError(f"local reranker {field} is invalid")
    for field, maximum in (
        ("max_candidates", _MAX_CANDIDATES),
        ("max_input_chars", _MAX_INPUT_CHARS),
        ("max_output_bytes", _MAX_OUTPUT_BYTES),
        ("timeout_seconds", 300),
    ):
        value = manifest[field]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"local reranker {field} is invalid")
    command = manifest["command"]
    if (
        not isinstance(command, list)
        or not 1 <= len(command) <= 32
        or any(not isinstance(value, str) or not 1 <= len(value) <= 1_000 for value in command)
    ):
        raise ValueError("local reranker command is invalid")
    executable = Path(command[0]).expanduser()
    if (
        not executable.is_absolute()
        or executable.is_symlink()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise ValueError("local reranker executable must be an exact local executable")
    model_files = manifest["model_files"]
    if not isinstance(model_files, list) or not 1 <= len(model_files) <= 1_000:
        raise ValueError("local reranker requires a bounded verified model file inventory")
    verified_files: list[dict[str, Any]] = []
    verified_paths: set[Path] = set()
    for item in model_files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("local reranker model file entry is invalid")
        model_path = Path(item["path"]).expanduser().absolute()
        expected_sha256 = item["sha256"]
        if (
            model_path.is_symlink()
            or not model_path.is_file()
            or not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or sha256_file(model_path) != expected_sha256
        ):
            raise ValueError("local reranker model file verification failed")
        verified_paths.add(model_path)
        verified_files.append(
            {
                "name": model_path.name,
                "byte_size": model_path.stat().st_size,
                "sha256": expected_sha256,
            }
        )
    for argument in command[1:]:
        argument_path = Path(argument).expanduser()
        if argument_path.is_absolute() and argument_path not in verified_paths:
            raise ValueError("absolute reranker command resources must appear in model_files")
    canonical_manifest = {
        **manifest,
        "command": {
            "executable_name": executable.name,
            "executable_sha256": sha256_file(executable),
            "argument_count": len(command) - 1,
            "arguments_sha256": sha256_bytes(canonical_json(command[1:]).encode()),
        },
        "model_files": verified_files,
    }
    manifest_sha256 = sha256_bytes(canonical_json(canonical_manifest).encode())
    return {
        "path": manifest_path,
        "manifest": manifest,
        "manifest_sha256": manifest_sha256,
        "profile_id": stable_id("rerankerprofile", manifest_sha256),
        "canonical_manifest": canonical_manifest,
    }


def run_local_reranker(
    *,
    manifest_path: str | Path,
    query: str,
    candidates: list[dict[str, str]],
    loaded_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    loaded = loaded_manifest or load_local_reranker_manifest(manifest_path)
    if loaded.get("path") != Path(manifest_path).expanduser().absolute():
        raise ValueError("loaded local reranker manifest path does not match the request")
    manifest = loaded["manifest"]
    if not candidates or len(candidates) > manifest["max_candidates"]:
        raise ValueError("local reranker candidate inventory exceeds its configured bound")
    candidate_ids = [candidate.get("asset_id") for candidate in candidates]
    if (
        len(candidate_ids) != len(set(candidate_ids))
        or any(
            not isinstance(value, str) or not _ASSET_ID.fullmatch(value)
            for value in candidate_ids
        )
        or any(
            not isinstance(candidate.get("title"), str)
            or not isinstance(candidate.get("statement"), str)
            for candidate in candidates
        )
    ):
        raise ValueError("local reranker candidates are invalid")
    request = {
        "schema_version": LOCAL_RERANKER_REQUEST_SCHEMA,
        "query": query,
        "candidates": candidates,
        "output_contract": {
            "schema_version": LOCAL_RERANKER_OUTPUT_SCHEMA,
            "must_return_exact_candidate_permutation": True,
            "numeric_confidence_forbidden": True,
        },
    }
    request_text = canonical_json(request)
    if len(request_text) > manifest["max_input_chars"]:
        raise ValueError("local reranker request exceeds its configured input bound")
    try:
        process = run_bounded_subprocess(
            manifest["command"],
            input_bytes=request_text.encode("utf-8"),
            environment={"PATH": os.defpath, "LANG": os.environ.get("LANG", "C.UTF-8")},
            timeout_seconds=manifest["timeout_seconds"],
            max_stdout_bytes=manifest["max_output_bytes"],
            max_stderr_bytes=min(manifest["max_output_bytes"], 64 * 1024),
        )
    except BoundedSubprocessError as error:
        raise RuntimeError("local reranker sidecar failed closed") from error
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()[:1_000]
        raise RuntimeError(f"local reranker sidecar failed closed: {detail or process.returncode}")
    try:
        output = strict_json_loads(process.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("local reranker returned invalid JSON") from error
    if (
        not isinstance(output, dict)
        or set(output) != {"schema_version", "ordered_asset_ids"}
        or output["schema_version"] != LOCAL_RERANKER_OUTPUT_SCHEMA
        or not isinstance(output["ordered_asset_ids"], list)
        or output["ordered_asset_ids"] != list(dict.fromkeys(output["ordered_asset_ids"]))
        or set(output["ordered_asset_ids"]) != set(candidate_ids)
        or len(output["ordered_asset_ids"]) != len(candidate_ids)
    ):
        raise RuntimeError("local reranker output does not match its closed contract")
    return {
        "profile_id": loaded["profile_id"],
        "manifest_sha256": loaded["manifest_sha256"],
        "implementation_revision": manifest["implementation_revision"],
        "model_identity": manifest["model_identity"],
        "model_revision": manifest["model_revision"],
        "network_policy": "offline",
        "ordered_asset_ids": output["ordered_asset_ids"],
        "output_sha256": sha256_bytes(process.stdout),
        "authority_effect": "ranking-only",
    }
