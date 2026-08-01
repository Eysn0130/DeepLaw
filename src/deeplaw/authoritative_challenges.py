from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .evidence_capabilities import required_capabilities_for_duty
from .util import canonical_json, sha256_bytes, stable_id

CHALLENGE_TYPES = (
    "temporal_challenge",
    "exception_challenge",
    "definition_challenge",
    "scope_challenge",
    "cross_reference_challenge",
    "extraction_challenge",
    "conflict_challenge",
)
_CHALLENGE_DUTIES = {
    "temporal_challenge": "temporal_status_version",
    "exception_challenge": "exceptions_counterevidence",
    "definition_challenge": "elements_definitions",
    "scope_challenge": "primary_rule",
    "cross_reference_challenge": "case_reference",
    "extraction_challenge": "exact_citation",
    "conflict_challenge": "exceptions_counterevidence",
}


def _trace_body(value: dict[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("trace_hash", None)
    body.pop("trace_id", None)
    return body


def build_challenge_trace(
    *,
    release_id: str,
    search_result: dict[str, Any],
    capability_lookup: Callable[[str, str | None], dict[str, Any]],
    segment_lookup: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    plan = search_result["query_plan"]
    temporal_as_of = plan.get("temporal_reference_date")
    obligations = {item["id"]: item for item in plan["obligations"]}
    coverage = {
        item["obligation_id"]: item for item in search_result["obligation_coverage"]
    }
    cards = {
        item["segment_id"]: item
        for item in [
            *search_result["evidence"],
            *search_result["uncertain_evidence"],
        ]
    }
    challenges: list[dict[str, Any]] = []
    for challenge_type in CHALLENGE_TYPES:
        duty_id = _CHALLENGE_DUTIES[challenge_type]
        obligation = obligations.get(duty_id)
        witness = coverage.get(duty_id)
        if obligation is None:
            result = "not_applicable"
            segment_id = None
            capability = None
            evidence = None
            reason = "duty_not_requested"
            gap = None
            required_capabilities: tuple[str, ...] = ()
        else:
            segment_ids = witness["evidence_segment_ids"] if witness else []
            segment_id = segment_ids[0] if segment_ids else None
            required_capabilities = required_capabilities_for_duty(
                duty_id,
                temporal_evaluated=(duty_id == "temporal_status_version"),
            )
            capability = (
                capability_lookup(segment_id, temporal_as_of)["capabilities"]
                if segment_id is not None
                else None
            )
            capability_ids = {
                f"{name}:{capability[name]}"
                for name in (
                    "integrity",
                    "source_identity",
                    "authority_metadata",
                    "temporal",
                    "extraction",
                    "provenance",
                )
            } if capability is not None else set()
            capabilities_satisfied = set(required_capabilities).issubset(capability_ids)
            covered = witness is not None and witness["status"] == "covered"
            result = "satisfied" if covered and capabilities_satisfied else "unresolved"
            if not covered:
                reason = "duty_evidence_unavailable"
            elif not capabilities_satisfied:
                reason = "capability_predicate_failed"
            else:
                reason = "deterministic_witness_verified"
            gap = None if result == "satisfied" else reason
            if segment_id is None:
                evidence = None
            else:
                segment = segment_lookup(segment_id)
                card = cards.get(segment_id, {})
                evidence = {
                    "release_id": release_id,
                    "segment_id": segment_id,
                    "receipt_id": segment["receipt_id"],
                    "source_sha256": segment["source_sha256"],
                    "segment_sha256": segment["segment_sha256"],
                    "article_label": segment.get("article_label"),
                    "page_start": segment.get("page_start"),
                    "page_end": segment.get("page_end"),
                    "retrieval_channel": card.get("retrieval_channel"),
                }
        challenge_without_hash = {
            "challenge_type": challenge_type,
            "duty_id": duty_id,
            "required": bool(obligation and obligation["required"]),
            "candidate_segment_id": segment_id,
            "required_capabilities": list(required_capabilities),
            "capabilities": capability,
            "evidence": evidence,
            "reason": reason,
            "result": result,
            "gap": gap,
        }
        challenge_without_hash["witness_sha256"] = sha256_bytes(
            canonical_json(challenge_without_hash).encode("utf-8")
        )
        challenges.append(challenge_without_hash)
    value = {
        "schema_version": "deeplaw.authoritative-challenge-trace/v1",
        "release_id": release_id,
        "query_plan_sha256": sha256_bytes(canonical_json(plan).encode("utf-8")),
        "evidence_compilation_sha256": sha256_bytes(
            canonical_json(search_result["evidence_compilation"]).encode("utf-8")
        ),
        "challenges": challenges,
    }
    trace_hash = sha256_bytes(canonical_json(value).encode("utf-8"))
    value["trace_id"] = stable_id("lawtrace", release_id, trace_hash)
    value["trace_hash"] = trace_hash
    return value


def replay_challenge_trace(
    trace: dict[str, Any],
    *,
    expected_release_id: str,
    capability_lookup: Callable[[str, str | None], dict[str, Any]],
) -> dict[str, Any]:
    if trace.get("schema_version") != "deeplaw.authoritative-challenge-trace/v1":
        return {
            "schema_version": "deeplaw.authoritative-challenge-replay/v1",
            "trace_id": trace.get("trace_id"),
            "valid": False,
            "reason": "unsupported_trace_schema",
        }
    if trace.get("release_id") != expected_release_id:
        return {
            "schema_version": "deeplaw.authoritative-challenge-replay/v1",
            "trace_id": trace.get("trace_id"),
            "valid": False,
            "reason": "release_mismatch",
        }
    expected_hash = sha256_bytes(canonical_json(_trace_body(trace)).encode("utf-8"))
    expected_id = stable_id("lawtrace", expected_release_id, expected_hash)
    if trace.get("trace_hash") != expected_hash or trace.get("trace_id") != expected_id:
        return {
            "schema_version": "deeplaw.authoritative-challenge-replay/v1",
            "trace_id": trace.get("trace_id"),
            "valid": False,
            "reason": "trace_digest_mismatch",
        }
    for challenge in trace.get("challenges", []):
        challenge_body = dict(challenge)
        witness_sha256 = challenge_body.pop("witness_sha256", None)
        if witness_sha256 != sha256_bytes(
            canonical_json(challenge_body).encode("utf-8")
        ):
            return {
                "schema_version": "deeplaw.authoritative-challenge-replay/v1",
                "trace_id": trace["trace_id"],
                "valid": False,
                "reason": "trace_digest_mismatch",
            }
        segment_id = challenge.get("candidate_segment_id")
        capability = challenge.get("capabilities")
        if segment_id is None:
            continue
        as_of = capability.get("temporal_as_of") if isinstance(capability, dict) else None
        current = capability_lookup(segment_id, as_of)["capabilities"]
        if current != capability:
            return {
                "schema_version": "deeplaw.authoritative-challenge-replay/v1",
                "trace_id": trace["trace_id"],
                "valid": False,
                "reason": "capability_witness_changed",
            }
    return {
        "schema_version": "deeplaw.authoritative-challenge-replay/v1",
        "trace_id": trace["trace_id"],
        "valid": True,
        "reason": "verified",
    }
