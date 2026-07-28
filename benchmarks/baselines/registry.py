from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

from deeplaw.util import canonical_json, sha256_bytes, strict_json_loads

REGISTRY_SCHEMA = "deeplaw.baseline-adapter-registry/v1"
UNRELEASED_CANDIDATE_LINE = "0.7.0-unreleased"
FROZEN_EVALUATION_CANDIDATE_LINE = "0.7.0-frozen-evaluation-candidate"
REQUIRED_SYSTEM_IDS = frozenset(
    {
        "baseline/bm25",
        "baseline/dense",
        "baseline/bm25-dense-reranker",
        "ragflow",
        "microsoft-graphrag",
        "lightrag",
        "graphiti",
        "mem0",
        "cognee",
        "memos",
        "pageindex",
        "openkb",
        "wikigraph",
        "obsidian-native",
        "deeplaw/lexical",
        "deeplaw/hybrid",
        "deeplaw/full",
    }
)
EXECUTION_STATES = frozenset(
    {
        "configuration_ready_execution_pending",
        "manual_protocol_ready_execution_pending",
        "adapter_ready_candidate_freeze_pending",
        "candidate_frozen_execution_pending",
    }
)
_SHA = re.compile(r"^[0-9a-f]{40,64}$")
_SYSTEM_ID = re.compile(r"^[a-z0-9][a-z0-9./-]{0,99}$")


def default_registry_path() -> Path:
    return Path(__file__).with_name("registry-v0.7.json")


def registry_sha256(registry: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(registry).encode("utf-8"))


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    selected = Path(path) if path is not None else default_registry_path()
    value = strict_json_loads(selected.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("baseline registry must be a JSON object")
    validate_registry(value)
    return value


def _bounded_string(value: Any, *, field: str, maximum: int = 1000) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be a bounded canonical string")
    return value


def validate_registry(registry: dict[str, Any]) -> None:
    expected_root = {
        "schema_version",
        "candidate_line",
        "reviewed_at",
        "purpose",
        "shared_models",
        "fairness",
        "systems",
        "result_policy",
    }
    if set(registry) != expected_root or registry.get("schema_version") != REGISTRY_SCHEMA:
        raise ValueError("baseline registry does not match its closed root contract")
    candidate_line = registry.get("candidate_line")
    if candidate_line not in {
        UNRELEASED_CANDIDATE_LINE,
        FROZEN_EVALUATION_CANDIDATE_LINE,
    }:
        raise ValueError("baseline registry candidate line is invalid")
    _bounded_string(registry.get("reviewed_at"), field="reviewed_at", maximum=32)
    _bounded_string(registry.get("purpose"), field="purpose", maximum=2000)
    models = registry.get("shared_models")
    if not isinstance(models, dict) or not models or len(models) > 16:
        raise ValueError("baseline registry shared models are invalid")
    for alias, model in models.items():
        _bounded_string(alias, field="model alias", maximum=80)
        if not isinstance(model, dict) or set(model) != {
            "model_id",
            "revision",
            "role",
            "local_runtime",
        }:
            raise ValueError(f"shared model {alias} does not match its closed contract")
        _bounded_string(model.get("model_id"), field=f"{alias}.model_id", maximum=200)
        if not _SHA.fullmatch(str(model.get("revision"))):
            raise ValueError(f"shared model {alias} revision is not immutable")
        if model.get("role") not in {"embedding", "reranker", "generation"}:
            raise ValueError(f"shared model {alias} role is invalid")
        if model.get("local_runtime") is not True:
            raise ValueError(f"shared model {alias} must use a local runtime")

    fairness = registry.get("fairness")
    required_fairness = {
        "same_corpus",
        "same_queries",
        "same_reader",
        "same_context_token_budget",
        "same_hardware",
        "same_query_network_policy",
        "record_build_and_query_cost",
        "retain_failures_and_raw_outputs",
        "no_toy_substitution",
    }
    if not isinstance(fairness, dict) or set(fairness) != required_fairness:
        raise ValueError("baseline fairness policy does not match its closed contract")
    if any(value is not True for value in fairness.values()):
        raise ValueError("every baseline fairness control must be enabled")

    systems = registry.get("systems")
    if not isinstance(systems, list) or not systems or len(systems) > 64:
        raise ValueError("baseline registry systems are invalid")
    observed: set[str] = set()
    for system in systems:
        _validate_system(
            system,
            model_aliases=set(models),
            candidate_line=candidate_line,
        )
        system_id = system["system_id"]
        if system_id in observed:
            raise ValueError(f"duplicate baseline system ID: {system_id}")
        observed.add(system_id)
    missing = REQUIRED_SYSTEM_IDS - observed
    if missing:
        raise ValueError(f"baseline registry is missing required systems: {sorted(missing)}")

    result_policy = registry.get("result_policy")
    if not isinstance(result_policy, dict) or set(result_policy) != {
        "current_status",
        "claim_eligible",
        "required_before_claim",
    }:
        raise ValueError("baseline result policy does not match its closed contract")
    if (
        result_policy.get("current_status") != "pending_execution"
        or result_policy.get("claim_eligible") is not False
        or not isinstance(result_policy.get("required_before_claim"), list)
        or not result_policy["required_before_claim"]
    ):
        raise ValueError("baseline result policy would overstate current evidence")


def _validate_system(
    system: Any,
    *,
    model_aliases: set[str],
    candidate_line: str,
) -> None:
    expected = {
        "system_id",
        "display_name",
        "category",
        "implementation",
        "adapter",
        "configuration",
        "model_aliases",
        "runtime_dependencies",
        "network_policy",
        "data_boundary",
        "official_evidence",
        "execution_state",
        "results_status",
        "raw_output_required",
    }
    if not isinstance(system, dict) or set(system) != expected:
        raise ValueError("baseline system does not match its closed contract")
    system_id = _bounded_string(system.get("system_id"), field="system_id", maximum=100)
    if not _SYSTEM_ID.fullmatch(system_id):
        raise ValueError(f"baseline system ID is invalid: {system_id}")
    _bounded_string(system.get("display_name"), field=f"{system_id}.display_name", maximum=200)
    if system.get("category") not in {
        "retrieval",
        "knowledge-system",
        "human-workflow",
        "candidate",
    }:
        raise ValueError(f"baseline {system_id} category is invalid")
    implementation = system.get("implementation")
    if not isinstance(implementation, dict) or set(implementation) != {
        "repository",
        "revision",
        "release",
        "license",
    }:
        raise ValueError(f"baseline {system_id} implementation is invalid")
    for field in ("repository", "release", "license"):
        _bounded_string(
            implementation.get(field), field=f"{system_id}.{field}", maximum=500
        )
    revision = implementation.get("revision")
    if system_id.startswith("deeplaw/"):
        if candidate_line == UNRELEASED_CANDIDATE_LINE:
            if revision != "FINAL_CANDIDATE_COMMIT_REQUIRED":
                raise ValueError(
                    "unreleased DeepLaw baseline must remain explicitly unfrozen"
                )
        elif not _SHA.fullmatch(str(revision)):
            raise ValueError("frozen DeepLaw baseline revision must be an exact commit")
    elif not _SHA.fullmatch(str(revision)):
        raise ValueError(f"baseline {system_id} implementation revision is not immutable")
    adapter = system.get("adapter")
    if not isinstance(adapter, dict) or set(adapter) != {
        "kind",
        "protocol",
        "entrypoint",
    }:
        raise ValueError(f"baseline {system_id} adapter is invalid")
    if adapter.get("kind") not in {
        "closed-subprocess",
        "scripted-human",
        "deeplaw-jsonl",
    }:
        raise ValueError(f"baseline {system_id} adapter kind is invalid")
    for field in ("protocol", "entrypoint"):
        _bounded_string(adapter.get(field), field=f"{system_id}.adapter.{field}", maximum=500)
    configuration = system.get("configuration")
    if not isinstance(configuration, dict) or set(configuration) != {
        "profile",
        "top_k",
        "context_token_budget",
        "query_mode",
        "official_defaults_documented",
    }:
        raise ValueError(f"baseline {system_id} configuration is invalid")
    if (
        not isinstance(configuration.get("top_k"), int)
        or isinstance(configuration.get("top_k"), bool)
        or not 1 <= configuration["top_k"] <= 100
        or not isinstance(configuration.get("context_token_budget"), int)
        or isinstance(configuration.get("context_token_budget"), bool)
        or not 256 <= configuration["context_token_budget"] <= 100_000
        or configuration.get("official_defaults_documented") is not True
    ):
        raise ValueError(f"baseline {system_id} configuration bounds are invalid")
    _bounded_string(configuration.get("profile"), field=f"{system_id}.profile", maximum=300)
    _bounded_string(
        configuration.get("query_mode"), field=f"{system_id}.query_mode", maximum=100
    )
    aliases = system.get("model_aliases")
    if (
        not isinstance(aliases, list)
        or len(aliases) > 8
        or len(set(aliases)) != len(aliases)
        or not set(aliases) <= model_aliases
    ):
        raise ValueError(f"baseline {system_id} model aliases are invalid")
    dependencies = system.get("runtime_dependencies")
    if (
        not isinstance(dependencies, list)
        or not dependencies
        or len(dependencies) > 32
        or any(not isinstance(value, str) or not value for value in dependencies)
    ):
        raise ValueError(f"baseline {system_id} runtime dependencies are invalid")
    if system.get("network_policy") not in {
        "build-network-recorded-query-offline",
        "query-offline-loopback-models-only",
        "manual-local-offline",
    }:
        raise ValueError(f"baseline {system_id} network policy is invalid")
    _bounded_string(system.get("data_boundary"), field=f"{system_id}.data_boundary", maximum=1000)
    _bounded_string(
        system.get("official_evidence"), field=f"{system_id}.official_evidence", maximum=500
    )
    execution_state = system.get("execution_state")
    if execution_state not in EXECUTION_STATES:
        raise ValueError(f"baseline {system_id} execution state is invalid")
    if system_id.startswith("deeplaw/"):
        expected_state = (
            "adapter_ready_candidate_freeze_pending"
            if candidate_line == UNRELEASED_CANDIDATE_LINE
            else "candidate_frozen_execution_pending"
        )
        if execution_state != expected_state:
            raise ValueError(
                f"baseline {system_id} execution state differs from candidate state"
            )
    elif execution_state == "candidate_frozen_execution_pending":
        raise ValueError("third-party baseline cannot use the DeepLaw candidate state")
    if system.get("results_status") != "pending_execution":
        raise ValueError(f"baseline {system_id} must not claim an unexecuted result")
    if system.get("raw_output_required") is not True:
        raise ValueError(f"baseline {system_id} must retain raw output")


def registry_report(registry: dict[str, Any]) -> dict[str, Any]:
    systems = registry["systems"]
    candidate_frozen = (
        registry["candidate_line"] == FROZEN_EVALUATION_CANDIDATE_LINE
    )
    blockers = [
        "official baseline runs and raw outputs are absent",
        "secret held-out commitments and two independent attestations are absent",
    ]
    if not candidate_frozen:
        blockers.insert(0, "final DeepLaw candidate commit and artifacts are not frozen")
    return {
        "schema_version": "deeplaw.baseline-adapter-registry-report/v1",
        "registry_sha256": registry_sha256(registry),
        "candidate_line": registry["candidate_line"],
        "system_count": len(systems),
        "required_system_count": len(REQUIRED_SYSTEM_IDS),
        "execution_states": {
            state: sum(item["execution_state"] == state for item in systems)
            for state in sorted(EXECUTION_STATES)
        },
        "results_status": registry["result_policy"]["current_status"],
        "claim_eligible": False,
        "ready_for_external_execution": candidate_frozen,
        "blockers": blockers,
    }


def freeze_candidate_registry(
    registry: dict[str, Any],
    *,
    candidate_commit: str,
    reviewed_at: str,
) -> dict[str, Any]:
    """Create an evaluator-owned registry copy bound to a frozen candidate commit."""

    validate_registry(registry)
    if registry["candidate_line"] != UNRELEASED_CANDIDATE_LINE:
        raise ValueError("only an unreleased registry can enter the frozen candidate state")
    if not _SHA.fullmatch(candidate_commit):
        raise ValueError("candidate commit must be an exact 40-64 character Git revision")
    try:
        parsed_date = dt.date.fromisoformat(reviewed_at)
    except (TypeError, ValueError) as error:
        raise ValueError("reviewed_at must be an ISO calendar date") from error
    if parsed_date.isoformat() != reviewed_at:
        raise ValueError("reviewed_at must use canonical YYYY-MM-DD form")
    frozen = copy.deepcopy(registry)
    frozen["candidate_line"] = FROZEN_EVALUATION_CANDIDATE_LINE
    frozen["reviewed_at"] = reviewed_at
    candidate_count = 0
    for system in frozen["systems"]:
        if system["system_id"].startswith("deeplaw/"):
            system["implementation"]["revision"] = candidate_commit
            system["execution_state"] = "candidate_frozen_execution_pending"
            candidate_count += 1
    if candidate_count != 3:
        raise ValueError("frozen registry must bind all three DeepLaw candidate profiles")
    validate_registry(frozen)
    return frozen


def _write_registry_exclusive(path: Path, registry: dict[str, Any]) -> None:
    selected = path.expanduser().absolute()
    if selected.exists() or selected.is_symlink():
        raise FileExistsError("frozen registry output path must be new")
    parent = selected.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("frozen registry output parent must be an existing safe directory")
    payload = (
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor = os.open(selected, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the closed v0.7 baseline registry")
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--freeze-candidate-commit")
    parser.add_argument("--reviewed-at")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    registry = load_registry(args.registry)
    freeze_requested = any(
        value is not None
        for value in (
            args.freeze_candidate_commit,
            args.reviewed_at,
            args.output,
        )
    )
    if freeze_requested:
        if not all(
            value is not None
            for value in (
                args.freeze_candidate_commit,
                args.reviewed_at,
                args.output,
            )
        ):
            raise ValueError(
                "candidate freeze requires commit, reviewed_at, and a new output path"
            )
        registry = freeze_candidate_registry(
            registry,
            candidate_commit=args.freeze_candidate_commit,
            reviewed_at=args.reviewed_at,
        )
        _write_registry_exclusive(args.output, registry)
    print(json.dumps(registry_report(registry), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
