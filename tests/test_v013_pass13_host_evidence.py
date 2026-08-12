from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.pass13_evidence import (
    EvidenceValidationError,
    analyze_safe_read_calls,
    build_bundle_manifest,
    canonical_json,
)


def _capsule(marker: str = "NEXT-ACTION-ALPHA") -> dict[str, object]:
    return {
        "schema_version": "deeplaw.knowledge-capsule/v6",
        "projection": "standard",
        "statements": [
            {
                "statement_id": "statement_" + "a" * 24,
                "statement_text": f"NEXT_ACTION: {marker}",
            }
        ],
        "evidence": [],
        "gaps": [],
        "conflicts": [],
        "limitations": [],
    }


def _tool_output(*, marker: str = "NEXT-ACTION-ALPHA") -> dict[str, object]:
    capsule = _capsule(marker)
    text = canonical_json(capsule)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "schema_version": "deeplaw.knowledge-support-output/v6",
            "operation": "context",
            "authority_boundary": {"local_only": True},
            "result": {
                "schema_version": "deeplaw.provider-knowledge-capsule/v2",
                "purpose": "answer",
                "policy_id": "compiled-first-v1",
                "capsule": capsule,
                "receipt": {"receipt_id": "receipt_" + "b" * 24},
                "delivery": {
                    "hard_limit_bytes": 65_536,
                    "provider_content_bytes": len(text.encode("utf-8")),
                    "projection": "standard",
                    "write_performed": False,
                },
            },
        },
    }


def _call(
    index: int = 1, *, output: dict[str, object] | None = None
) -> dict[str, object]:
    selected = output or _tool_output()
    structured = selected["structuredContent"]
    return {
        "call_index": index,
        "call_id_sha256": hashlib.sha256(f"call-{index}".encode()).hexdigest(),
        "server": "deeplaw",
        "tool_name": "knowledge_support",
        "status": "completed",
        "arguments_sha256": "c" * 64,
        "arguments_bytes": 100,
        "result_sha256": hashlib.sha256(canonical_json(selected).encode()).hexdigest(),
        "result_bytes": len(canonical_json(selected).encode()),
        "structured_content_sha256": hashlib.sha256(
            canonical_json(structured).encode()
        ).hexdigest(),
        "structured_content_bytes": len(canonical_json(structured).encode()),
    }


def test_safe_reads_recompute_exact_provider_transport_bytes() -> None:
    output = _tool_output()
    result = analyze_safe_read_calls([_call(output=output)], [output])
    expected = canonical_json(_capsule()).encode("utf-8")
    assert result == {
        "call_count": 1,
        "first_call_valid": True,
        "bounded_retry_used": False,
        "safe_read_operations": ["context"],
        "provider_payloads": [
            {
                "operation": "context",
                "provider_bytes": len(expected),
                "provider_sha256": hashlib.sha256(expected).hexdigest(),
                "structured_output_bytes": len(
                    canonical_json(_tool_output()["structuredContent"]).encode("utf-8")
                ),
                "structured_output_sha256": hashlib.sha256(
                    canonical_json(_tool_output()["structuredContent"]).encode("utf-8")
                ).hexdigest(),
                "delivery_match": True,
                "write_performed": False,
                "statement_count": 1,
                "gap_count": 0,
            }
        ],
    }


def test_safe_reads_allow_one_retry_but_fail_closed_on_unsafe_or_third_call() -> None:
    result = analyze_safe_read_calls(
        [
            _call(1, output=_tool_output(marker="FIRST")),
            _call(2, output=_tool_output(marker="SECOND")),
        ],
        [_tool_output(marker="FIRST"), _tool_output(marker="SECOND")],
    )
    assert result["call_count"] == 2
    assert result["first_call_valid"] is True
    assert result["bounded_retry_used"] is True

    with pytest.raises(EvidenceValidationError, match="one or two"):
        analyze_safe_read_calls(
            [_call(1), _call(2), _call(3)], [_tool_output()] * 3
        )

    unsafe = _tool_output()
    unsafe["structuredContent"]["operation"] = "semantic"  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="safe read"):
        analyze_safe_read_calls([_call(output=unsafe)], [unsafe])


def test_provider_transport_mismatch_and_outer_metadata_fail_closed() -> None:
    mismatched = _tool_output()
    mismatched["content"][0]["text"] = json.dumps(_capsule())  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="canonical"):
        analyze_safe_read_calls([_call(output=mismatched)], [mismatched])

    leaked = _tool_output()
    leaked["content"][0]["text"] = canonical_json(  # type: ignore[index]
        {**_capsule(), "audit_head": "forbidden"}
    )
    with pytest.raises(EvidenceValidationError, match="canonical"):
        analyze_safe_read_calls([_call(output=leaked)], [leaked])

    wrong_count = _tool_output()
    wrong_count["structuredContent"]["result"]["delivery"][  # type: ignore[index]
        "provider_content_bytes"
    ] = 1
    with pytest.raises(EvidenceValidationError, match="byte accounting"):
        analyze_safe_read_calls([_call(output=wrong_count)], [wrong_count])


def test_bundle_manifest_is_path_free_and_binds_each_artifact(tmp_path: Path) -> None:
    report = tmp_path / "codex-observation.json"
    events = tmp_path / "codex-run-1-events.sanitized.jsonl"
    report.write_text('{"status":"failed"}\n', encoding="utf-8")
    events.write_text('{"method":"turn/completed"}\n', encoding="utf-8")

    manifest = build_bundle_manifest(
        host="codex",
        commit="a" * 40,
        tree="b" * 40,
        artifacts={"observation": report, "sanitized_events_run_1": events},
    )
    assert manifest["schema_version"] == "deeplaw.host-qualification-bundle-manifest/v1"
    assert [row["name"] for row in manifest["artifacts"]] == [
        "codex-observation.json",
        "codex-run-1-events.sanitized.jsonl",
    ]
    assert str(tmp_path) not in canonical_json(manifest)
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256(
        report.read_bytes()
    ).hexdigest()
    repository = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (repository / "contracts/host-qualification-bundle-manifest.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)

    secret = tmp_path / "bad.json"
    secret.write_text('{"token":"qualification-secret"}\n', encoding="utf-8")
    with pytest.raises(EvidenceValidationError, match="forbidden value"):
        build_bundle_manifest(
            host="codex",
            commit="a" * 40,
            tree="b" * 40,
            artifacts={"bad": secret},
            forbidden_values=("qualification-secret",),
        )
