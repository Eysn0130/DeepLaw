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
    validate_host_report_consistency,
    write_retained_artifact,
)


def _capsule(marker: str = "NEXT-ACTION-ALPHA") -> dict[str, object]:
    return {
        "schema_version": "deeplaw.knowledge-capsule-projection/v1",
        "projection": "standard",
        "receipt_id": "queryreceipt_" + "b" * 24,
        "hard_limit_bytes": 65_536,
        "statements": [
            {
                "statement_id": "statement_" + "a" * 24,
                "statement_text": f"NEXT_ACTION: {marker}",
                "statement_type": "factual",
                "support_status": "supported",
                "current_supported": True,
                "freshness": "fresh",
                "origin": "agent_derived",
                "authority": "agent_memory",
                "verification": "unverified",
                "legal_authority": False,
                "source_refs": [],
            }
        ],
        "gaps": [],
        "selected_statement_count": 1,
        "selected_source_count": 0,
        "evidence": [],
    }


def _tool_output(*, marker: str = "NEXT-ACTION-ALPHA") -> dict[str, object]:
    capsule = _capsule(marker)
    text = canonical_json(capsule)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {
            "schema_version": "deeplaw.knowledge-support-output/v6",
            "operation": "context",
            "authority_boundary": {
                "legal_authority": False,
                "official_legal_sources_tool": "law_support",
                "persistent_writes": "separate_explicit_knowledge_sink",
                "case_data_allowed": False,
                "authority_from_ranking": False,
            },
            "result": {
                "schema_version": "deeplaw.provider-knowledge-capsule/v2",
                "purpose": "answer",
                "policy_id": "compiled-first-v1",
                "capsule": capsule,
                "receipt": {"receipt_id": "queryreceipt_" + "b" * 24},
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
    first = _tool_output(marker="FIRST")
    first_capsule = first["structuredContent"]["result"]["capsule"]  # type: ignore[index]
    first_capsule["statements"] = []  # type: ignore[index]
    first_capsule["selected_statement_count"] = 0  # type: ignore[index]
    first_capsule["gaps"] = [  # type: ignore[index]
        {
            "gap_id": "querygap_" + "1" * 24,
            "code": "insufficient_context",
            "duty": "unresolved_gap",
            "message": "First bounded read was insufficient.",
        }
    ]
    first_text = canonical_json(first_capsule)
    first["content"][0]["text"] = first_text  # type: ignore[index]
    first["structuredContent"]["result"]["delivery"][  # type: ignore[index]
        "provider_content_bytes"
    ] = len(first_text.encode("utf-8"))
    second = _tool_output(marker="SECOND")
    result = analyze_safe_read_calls(
        [
            _call(1, output=first),
            _call(2, output=second),
        ],
        [first, second],
    )
    assert result["call_count"] == 2
    assert result["first_call_valid"] is True
    assert result["bounded_retry_used"] is True

    with pytest.raises(EvidenceValidationError, match="one or two"):
        analyze_safe_read_calls(
            [_call(1), _call(2), _call(3)], [_tool_output()] * 3
        )
    with pytest.raises(EvidenceValidationError, match="insufficient"):
        analyze_safe_read_calls(
            [
                _call(1, output=_tool_output(marker="FIRST")),
                _call(2, output=_tool_output(marker="SECOND")),
            ],
            [_tool_output(marker="FIRST"), _tool_output(marker="SECOND")],
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
    with pytest.raises(EvidenceValidationError, match=r"credential|forbidden value"):
        build_bundle_manifest(
            host="codex",
            commit="a" * 40,
            tree="b" * 40,
            artifacts={"bad": secret},
            forbidden_values=("qualification-secret",),
        )


def test_retained_artifact_is_scanned_before_exclusive_write(tmp_path: Path) -> None:
    target = tmp_path / "sanitized.jsonl"
    receipt = write_retained_artifact(
        target,
        b'{"method":"turn/completed"}\n',
        forbidden_values=("qualification-secret",),
    )
    assert receipt == {
        "name": "sanitized.jsonl",
        "bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    with pytest.raises(FileExistsError):
        write_retained_artifact(target, b"{}\n")

    blocked = tmp_path / "blocked.json"
    with pytest.raises(EvidenceValidationError, match="forbidden value"):
        write_retained_artifact(
            blocked,
            b'{"value":"qualification-secret"}\n',
            forbidden_values=("qualification-secret",),
        )
    assert not blocked.exists()


def _report_run(index: int, scenario: str) -> dict[str, object]:
    methods = {
        "cold_start": ["thread/start"],
        "resume_fork": ["thread/start", "thread/resume", "thread/fork"],
        "compaction_forget": [
            "thread/start",
            "thread/compact/start",
            "thread/compacted",
        ],
        "projection_status": ["opencode/run"],
        "source_forget": ["opencode/run"],
        "provider_boundary": ["opencode/run"],
    }[scenario]
    return {
        "run_index": index,
        "scenario": scenario,
        "status": "passed",
        "failure_codes": [],
        "methods_observed": methods,
        "turns": [
            {
                "status": "passed",
                "ledger_audit_head_before": "a" * 64,
                "ledger_audit_head_after": "a" * 64,
                "ledger_unchanged": True,
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 2,
                    "output_tokens": 4,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 14,
                },
                "safe_read": {
                    "call_count": 1,
                    "first_call_valid": True,
                    "bounded_retry_used": False,
                    "safe_read_operations": ["context"],
                    "provider_payloads": [
                        {
                            "operation": "context",
                            "provider_bytes": 100,
                        }
                    ],
                },
            }
        ],
    }


def test_report_consistency_freezes_scenarios_reads_tokens_and_aggregates() -> None:
    report = {
        "host": "codex",
        "status": "executed",
        "runs": [
            _report_run(1, "cold_start"),
            _report_run(2, "resume_fork"),
            _report_run(3, "compaction_forget"),
        ],
        "lifecycle": {
            "methods_observed": [
                "thread/start",
                "thread/resume",
                "thread/fork",
                "thread/compact/start",
                "thread/compacted",
            ]
        },
        "aggregate": {
            "passed_runs": 3,
            "failed_runs": 0,
            "first_call_valid_runs": 3,
            "bounded_retry_runs": 0,
            "provider_bytes": 300,
            "input_tokens": 30,
            "cached_input_tokens": 6,
            "output_tokens": 12,
            "reasoning_output_tokens": 3,
            "total_tokens": 42,
        },
    }
    validate_host_report_consistency(report)

    report["runs"][2]["scenario"] = "cold_start"  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="scenario matrix"):
        validate_host_report_consistency(report)


def test_report_consistency_rejects_empty_read_and_self_reported_aggregate() -> None:
    report = {
        "host": "opencode",
        "status": "executed",
        "runs": [
            _report_run(1, "projection_status"),
            _report_run(2, "source_forget"),
            _report_run(3, "provider_boundary"),
        ],
        "lifecycle": {"methods_observed": ["not_applicable"]},
        "aggregate": {
            "passed_runs": 3,
            "failed_runs": 0,
            "first_call_valid_runs": 3,
            "bounded_retry_runs": 0,
            "provider_bytes": 300,
            "input_tokens": 30,
            "cached_input_tokens": 6,
            "output_tokens": 12,
            "reasoning_output_tokens": 3,
            "total_tokens": 42,
        },
    }
    report["runs"][0]["turns"][0]["safe_read"] = {  # type: ignore[index]
        "call_count": 0,
        "first_call_valid": False,
        "bounded_retry_used": False,
        "safe_read_operations": [],
        "provider_payloads": [],
    }
    with pytest.raises(EvidenceValidationError, match="passed turn"):
        validate_host_report_consistency(report)

    report["runs"][0] = _report_run(1, "projection_status")  # type: ignore[index]
    report["aggregate"]["provider_bytes"] = 1  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="aggregate"):
        validate_host_report_consistency(report)
