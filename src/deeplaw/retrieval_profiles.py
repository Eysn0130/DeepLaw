from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .knowledge_models import ASSET_KINDS, utc_now
from .util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads

if TYPE_CHECKING:
    from .knowledge_store import KnowledgeVault

RETRIEVAL_PROFILE_SCHEMA = "deeplaw.retrieval-profile/v1"
RETRIEVAL_PROFILE_EVALUATION_SCHEMA = "deeplaw.retrieval-profile-evaluation/v1"
RETRIEVAL_REGRESSION_SUITE_SCHEMA = "deeplaw.retrieval-regression-suite/v1"

BASE_CHANNEL_WEIGHTS = {
    "exact_id": 4.0,
    "knowledge_key": 4.0,
    "semantic_key": 3.2,
    "exact_phrase": 2.6,
    "lexical": 1.0,
    "dense": 0.9,
    "tree": 1.15,
    "graph": 0.8,
    "temporal": 1.2,
    "feedback": 0.45,
}
_MAX_PROFILE_BYTES = 256 * 1024
_MAX_SUITE_BYTES = 8 * 1024 * 1024


def _profiles_root(vault: KnowledgeVault, *, create: bool = True) -> Path:
    derived = vault.root / "derived"
    if derived.is_symlink():
        raise RuntimeError("derived data root must not be a symbolic link")
    if create:
        derived.mkdir(mode=0o700, exist_ok=True)
        os.chmod(derived, 0o700)
    root = derived / "retrieval-profiles"
    if root.is_symlink():
        raise RuntimeError("retrieval profile root must not be a symbolic link")
    if create:
        root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
    return root


def _write_owner_json(path: Path, value: dict[str, Any], *, replace: bool = False) -> None:
    if path.is_symlink() or (path.exists() and not replace):
        raise FileExistsError("retrieval profile artifact already exists or is unsafe")
    payload = (canonical_json(value) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _profile_digest(profile: dict[str, Any]) -> str:
    unsigned = {
        key: value
        for key, value in profile.items()
        if key not in {"profile_id", "profile_sha256"}
    }
    return sha256_bytes(canonical_json(unsigned).encode("utf-8"))


def _validate_profile(profile: Any, *, vault: KnowledgeVault) -> dict[str, Any]:
    expected = {
        "schema_version",
        "profile_id",
        "vault_id",
        "trained_at_revision",
        "trained_at_audit_head",
        "created_at",
        "parent_profile_id",
        "training_feedback_ids",
        "training_feedback_receipts_sha256",
        "channel_weights",
        "source_diversity_weight",
        "type_priority",
        "reranker_profile",
        "authority_effect",
        "profile_sha256",
    }
    if not isinstance(profile, dict) or set(profile) != expected:
        raise ValueError("retrieval profile does not match its closed contract")
    weights = profile["channel_weights"]
    priorities = profile["type_priority"]
    if (
        profile["schema_version"] != RETRIEVAL_PROFILE_SCHEMA
        or profile["vault_id"] != vault.vault_id
        or not isinstance(profile["trained_at_revision"], int)
        or not 0 <= profile["trained_at_revision"] <= vault.revision
        or vault.audit_hash_at(profile["trained_at_revision"])
        != profile["trained_at_audit_head"]
        or not isinstance(weights, dict)
        or set(weights) != set(BASE_CHANNEL_WEIGHTS)
        or any(
            isinstance(weight, bool)
            or not isinstance(weight, (int, float))
            or not 0.1 <= weight <= 8.0
            for weight in weights.values()
        )
        or isinstance(profile["source_diversity_weight"], bool)
        or not isinstance(profile["source_diversity_weight"], (int, float))
        or not 0.0 <= profile["source_diversity_weight"] <= 4.0
        or not isinstance(priorities, dict)
        or set(priorities) != set(ASSET_KINDS)
        or any(
            isinstance(priority, bool)
            or not isinstance(priority, (int, float))
            or not 0.5 <= priority <= 2.0
            for priority in priorities.values()
        )
        or profile["authority_effect"] != "ranking-only"
        or profile["profile_sha256"] != _profile_digest(profile)
        or profile["profile_id"]
        != stable_id("retrievalprofile", profile["profile_sha256"])
    ):
        raise ValueError("retrieval profile identity or binding is invalid")
    return profile


def load_retrieval_profile(
    vault: KnowledgeVault,
    profile_id: str,
) -> dict[str, Any]:
    if not isinstance(profile_id, str) or not profile_id.startswith("retrievalprofile_"):
        raise ValueError("retrieval profile ID is invalid")
    path = _profiles_root(vault, create=False) / f"{profile_id}.json"
    if (
        path.is_symlink()
        or not path.is_file()
        or not 1 <= path.stat().st_size <= _MAX_PROFILE_BYTES
    ):
        raise KeyError(f"retrieval profile is unavailable: {profile_id}")
    try:
        profile = strict_json_loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("retrieval profile JSON is invalid") from error
    return _validate_profile(profile, vault=vault)


def _active_pointer(vault: KnowledgeVault, *, create: bool = False) -> Path:
    return _profiles_root(vault, create=create) / "ACTIVE.json"


def _load_pointer(vault: KnowledgeVault) -> dict[str, Any] | None:
    path = _active_pointer(vault)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_PROFILE_BYTES:
        raise RuntimeError("active retrieval profile pointer is unsafe")
    pointer = strict_json_loads(path.read_bytes())
    expected = {
        "schema_version",
        "vault_id",
        "active_profile_id",
        "history",
        "updated_at",
        "sha256",
    }
    if not isinstance(pointer, dict) or set(pointer) != expected:
        raise RuntimeError("active retrieval profile pointer is invalid")
    unsigned = {key: value for key, value in pointer.items() if key != "sha256"}
    if (
        pointer["schema_version"] != "deeplaw.retrieval-profile-pointer/v1"
        or pointer["vault_id"] != vault.vault_id
        or not isinstance(pointer["history"], list)
        or len(pointer["history"]) > 100
        or pointer["sha256"]
        != sha256_bytes(canonical_json(unsigned).encode("utf-8"))
    ):
        raise RuntimeError("active retrieval profile pointer verification failed")
    return pointer


def load_active_retrieval_profile(vault: KnowledgeVault) -> dict[str, Any] | None:
    pointer = _load_pointer(vault)
    if pointer is None or pointer["active_profile_id"] is None:
        return None
    return load_retrieval_profile(vault, pointer["active_profile_id"])


def train_retrieval_profile(
    vault: KnowledgeVault,
    *,
    feedback_ids: tuple[str, ...],
) -> dict[str, Any]:
    if not feedback_ids or len(feedback_ids) > 500 or len(set(feedback_ids)) != len(feedback_ids):
        raise ValueError("profile training requires one to 500 distinct feedback records")
    feedback = [vault.get_feedback(feedback_id) for feedback_id in sorted(feedback_ids)]
    if any(not item["valid"] for item in feedback):
        raise ValueError("profile training requires valid feedback records")
    current = load_active_retrieval_profile(vault)
    weights = dict(
        current["channel_weights"] if current is not None else BASE_CHANNEL_WEIGHTS
    )
    type_priority = {kind: 1.0 for kind in ASSET_KINDS}
    if current is not None:
        type_priority.update(current["type_priority"])
    source_diversity = float(
        current["source_diversity_weight"] if current is not None else 1.0
    )
    for item in feedback:
        if item["helpful_asset_ids"]:
            weights["lexical"] += 0.02
            weights["feedback"] += 0.02
            for asset_id in item["helpful_asset_ids"]:
                kind = vault.get_asset(asset_id, include_inactive=True).kind
                type_priority[kind] += 0.02
        if item["irrelevant_asset_ids"]:
            weights["feedback"] -= 0.03
            source_diversity += 0.03
        if item["harmful_asset_ids"]:
            weights["feedback"] -= 0.05
            weights["graph"] -= 0.02
        if item["stale_asset_ids"]:
            weights["temporal"] += 0.06
        if item["incorrect_relations"]:
            weights["graph"] -= 0.05
        if item["missing_knowledge"] or item["missing_sources"]:
            weights["lexical"] += 0.03
            weights["tree"] += 0.04
        if item["budget_failures"]:
            source_diversity -= 0.03
    weights = {key: round(min(8.0, max(0.1, value)), 6) for key, value in weights.items()}
    type_priority = {
        key: round(min(2.0, max(0.5, value)), 6)
        for key, value in type_priority.items()
    }
    source_diversity = round(min(4.0, max(0.0, source_diversity)), 6)
    receipt_inventory = [
        {"feedback_id": item["feedback_id"], "receipt_sha256": item["receipt_sha256"]}
        for item in feedback
    ]
    profile: dict[str, Any] = {
        "schema_version": RETRIEVAL_PROFILE_SCHEMA,
        "profile_id": None,
        "vault_id": vault.vault_id,
        "trained_at_revision": vault.revision,
        "trained_at_audit_head": vault.audit_head,
        "created_at": utc_now(),
        "parent_profile_id": current["profile_id"] if current is not None else None,
        "training_feedback_ids": sorted(feedback_ids),
        "training_feedback_receipts_sha256": sha256_bytes(
            canonical_json(receipt_inventory).encode("utf-8")
        ),
        "channel_weights": weights,
        "source_diversity_weight": source_diversity,
        "type_priority": type_priority,
        "reranker_profile": "off",
        "authority_effect": "ranking-only",
    }
    profile["profile_sha256"] = _profile_digest(profile)
    profile["profile_id"] = stable_id("retrievalprofile", profile["profile_sha256"])
    path = _profiles_root(vault) / f"{profile['profile_id']}.json"
    _write_owner_json(path, profile)
    return _validate_profile(profile, vault=vault)
def _read_suite(path: str | Path) -> tuple[dict[str, Any], str]:
    candidate = Path(path).expanduser().absolute()
    if (
        candidate.is_symlink()
        or not candidate.is_file()
        or not 1 <= candidate.stat().st_size <= _MAX_SUITE_BYTES
    ):
        raise ValueError("retrieval regression suite is unavailable or unsafe")
    suite = strict_json_loads(candidate.read_bytes())
    if (
        not isinstance(suite, dict)
        or set(suite) != {"schema_version", "cases", "gates"}
        or suite["schema_version"] != RETRIEVAL_REGRESSION_SUITE_SCHEMA
        or not isinstance(suite["cases"], list)
        or not 1 <= len(suite["cases"]) <= 10_000
        or not isinstance(suite["gates"], dict)
    ):
        raise ValueError("retrieval regression suite contract is invalid")
    return suite, sha256_file(candidate)


def evaluate_retrieval_profile(
    vault: KnowledgeVault,
    *,
    profile_id: str,
    suite_path: str | Path,
) -> dict[str, Any]:
    profile = load_retrieval_profile(vault, profile_id)
    suite, suite_sha256 = _read_suite(suite_path)
    from .retrieval_fabric import retrieve

    results: list[dict[str, Any]] = []
    total_expected = 0
    total_recalled = 0
    total_selected = 0
    total_irrelevant = 0
    safety_failures = 0
    for case in suite["cases"]:
        expected_fields = {
            "case_id",
            "query",
            "mode",
            "expected_asset_ids",
            "forbidden_asset_ids",
            "max_items",
        }
        if not isinstance(case, dict) or set(case) != expected_fields:
            raise ValueError("retrieval regression case contract is invalid")
        response = retrieve(
            vault,
            case["query"],
            mode=case["mode"],
            limit=case["max_items"],
            ranking_profile=profile,
        )
        selected = [item["asset_id"] for item in response["results"]]
        expected = set(case["expected_asset_ids"])
        forbidden = set(case["forbidden_asset_ids"])
        recalled = expected & set(selected)
        irrelevant = set(selected) - expected
        safety = forbidden & set(selected)
        total_expected += len(expected)
        total_recalled += len(recalled)
        total_selected += len(selected)
        total_irrelevant += len(irrelevant)
        safety_failures += len(safety)
        results.append(
            {
                "case_id": case["case_id"],
                "selected_asset_ids": selected,
                "recalled_asset_ids": sorted(recalled),
                "forbidden_selected_asset_ids": sorted(safety),
                "passed": recalled == expected and not safety,
            }
        )
    recall = total_recalled / total_expected if total_expected else 1.0
    irrelevant_rate = total_irrelevant / total_selected if total_selected else 0.0
    gates = suite["gates"]
    if set(gates) != {"min_recall", "max_irrelevant_rate", "max_safety_failures"}:
        raise ValueError("retrieval regression gates are invalid")
    passed = bool(
        recall >= gates["min_recall"]
        and irrelevant_rate <= gates["max_irrelevant_rate"]
        and safety_failures <= gates["max_safety_failures"]
    )
    body = {
        "schema_version": RETRIEVAL_PROFILE_EVALUATION_SCHEMA,
        "vault_id": vault.vault_id,
        "vault_revision": vault.revision,
        "audit_head": vault.audit_head,
        "profile_id": profile_id,
        "profile_sha256": profile["profile_sha256"],
        "suite_sha256": suite_sha256,
        "evaluated_at": utc_now(),
        "metrics": {
            "recall": recall,
            "irrelevant_rate": irrelevant_rate,
            "safety_failures": safety_failures,
            "case_count": len(results),
        },
        "gates": gates,
        "passed": passed,
        "authority_changed": False,
        "results": results,
    }
    body["evaluation_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    output = _profiles_root(vault) / (
        f"{profile_id}.evaluation.{body['evaluation_sha256'][:16]}.json"
    )
    _write_owner_json(output, body)
    return {**body, "evaluation_file": output.name}


def _load_evaluation(
    vault: KnowledgeVault,
    path: str | Path,
) -> dict[str, Any]:
    candidate = Path(path).expanduser().absolute()
    root = _profiles_root(vault, create=False).resolve()
    if (
        candidate.is_symlink()
        or not candidate.is_file()
        or candidate.parent.resolve() != root
        or not 1 <= candidate.stat().st_size <= _MAX_PROFILE_BYTES
    ):
        raise ValueError("retrieval profile evaluation is unavailable or unsafe")
    evaluation = strict_json_loads(candidate.read_bytes())
    digest = evaluation.get("evaluation_sha256") if isinstance(evaluation, dict) else None
    unsigned = {
        key: value for key, value in evaluation.items() if key != "evaluation_sha256"
    }
    if (
        evaluation.get("schema_version") != RETRIEVAL_PROFILE_EVALUATION_SCHEMA
        or evaluation.get("vault_id") != vault.vault_id
        or digest != sha256_bytes(canonical_json(unsigned).encode("utf-8"))
        or not evaluation.get("passed")
    ):
        raise ValueError("retrieval profile evaluation did not pass verification")
    return evaluation


def _write_pointer(
    vault: KnowledgeVault,
    *,
    active: str | None,
    history: list[str],
) -> dict[str, Any]:
    body = {
        "schema_version": "deeplaw.retrieval-profile-pointer/v1",
        "vault_id": vault.vault_id,
        "active_profile_id": active,
        "history": history[-100:],
        "updated_at": utc_now(),
    }
    body["sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    _write_owner_json(_active_pointer(vault, create=True), body, replace=True)
    return body


def activate_retrieval_profile(
    vault: KnowledgeVault,
    *,
    profile_id: str,
    evaluation_path: str | Path,
) -> dict[str, Any]:
    profile = load_retrieval_profile(vault, profile_id)
    evaluation = _load_evaluation(vault, evaluation_path)
    if (
        evaluation["profile_id"] != profile_id
        or evaluation["profile_sha256"] != profile["profile_sha256"]
    ):
        raise ValueError("retrieval profile evaluation belongs to a different profile")
    pointer = _load_pointer(vault)
    current = pointer["active_profile_id"] if pointer is not None else None
    history = list(pointer["history"] if pointer is not None else [])
    if current is not None and current != profile_id:
        history.append(current)
    updated = _write_pointer(vault, active=profile_id, history=history)
    return {
        "schema_version": "deeplaw.retrieval-profile-activation/v1",
        "profile_id": profile_id,
        "previous_profile_id": current,
        "evaluation_sha256": evaluation["evaluation_sha256"],
        "active": True,
        "authority_changed": False,
        "pointer_sha256": updated["sha256"],
    }


def rollback_retrieval_profile(vault: KnowledgeVault) -> dict[str, Any]:
    pointer = _load_pointer(vault)
    if pointer is None or pointer["active_profile_id"] is None:
        raise ValueError("there is no active retrieval profile to roll back")
    history = list(pointer["history"])
    previous = history.pop() if history else None
    if previous is not None:
        load_retrieval_profile(vault, previous)
    updated = _write_pointer(vault, active=previous, history=history)
    return {
        "schema_version": "deeplaw.retrieval-profile-rollback/v1",
        "rolled_back_profile_id": pointer["active_profile_id"],
        "active_profile_id": previous,
        "authority_changed": False,
        "pointer_sha256": updated["sha256"],
    }
