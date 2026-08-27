from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts import pass13_evidence
from benchmarks.hosts.pass13_evidence import (
    EvidenceValidationError,
    analyze_safe_read_calls,
    build_bundle_manifest,
    canonical_json,
    metric_evidence_sha256,
    write_retained_artifact,
)
from benchmarks.hosts.pass13_evidence import (
    validate_historical_host_report_consistency_v1 as validate_host_report_consistency,
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
                "gap_codes": [],
                "relevant_chars": 0,
                "context_chars": len(canonical_json(_capsule())),
                "relevant_chars_context_chars": 0.0,
                "evidence_count": 0,
                "duplicate_evidence_count": 0,
                "duplicate_evidence_rate": None,
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
    report.write_text('{"status":"failed"}\n', encoding="utf-8")
    event_paths = []
    for index in range(1, 4):
        events = tmp_path / f"codex-run-{index}-events.sanitized.jsonl"
        events.write_text('{"method":"turn/completed"}\n', encoding="utf-8")
        event_paths.append(events)

    manifest = build_bundle_manifest(
        host="codex",
        commit="a" * 40,
        tree="b" * 40,
        artifacts={
            "qualification_report": report,
            **{
                f"sanitized_events_run_{index}": path
                for index, path in enumerate(event_paths, 1)
            },
        },
        output_root=tmp_path,
    )
    assert manifest["schema_version"] == "deeplaw.host-qualification-bundle-manifest/v1"
    assert [row["name"] for row in manifest["artifacts"]] == [
        "codex-observation.json",
        "codex-run-1-events.sanitized.jsonl",
        "codex-run-2-events.sanitized.jsonl",
        "codex-run-3-events.sanitized.jsonl",
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
            output_root=tmp_path,
            forbidden_values=("qualification-secret",),
        )


def test_retained_artifact_is_scanned_before_exclusive_write(tmp_path: Path) -> None:
    target = tmp_path / "sanitized.jsonl"
    receipt = write_retained_artifact(
        target,
        b'{"method":"turn/completed"}\n',
        output_root=tmp_path,
        forbidden_values=("qualification-secret",),
    )
    assert receipt == {
        "name": "sanitized.jsonl",
        "bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    with pytest.raises(FileExistsError):
        write_retained_artifact(target, b"{}\n", output_root=tmp_path)

    blocked = tmp_path / "blocked.json"
    with pytest.raises(EvidenceValidationError, match="forbidden value"):
        write_retained_artifact(
            blocked,
            b'{"value":"qualification-secret"}\n',
            output_root=tmp_path,
            forbidden_values=("qualification-secret",),
        )
    assert not blocked.exists()


def test_artifact_scan_allows_only_false_security_leak_flags(tmp_path: Path) -> None:
    write_retained_artifact(
        tmp_path / "safe-security.json",
        (
            b'{"authentication_material_retained":false,"secret_leak":false,'
            b'"secret_values_retained":false}\n'
        ),
        output_root=tmp_path,
    )
    for unsafe in (
        b'{"secret_leak":true}\n',
        b'{"secret_values_retained":true}\n',
    ):
        target = tmp_path / f"unsafe-security-{hashlib.sha256(unsafe).hexdigest()}.json"
        with pytest.raises(EvidenceValidationError, match="credential-bearing"):
            write_retained_artifact(
                target,
                unsafe,
                output_root=tmp_path,
            )
        assert not target.exists()


def test_artifact_scan_rejects_non_home_absolute_paths(tmp_path: Path) -> None:
    with pytest.raises(EvidenceValidationError, match="absolute path"):
        write_retained_artifact(
            tmp_path / "unsafe-path.json",
            b'{"diagnostic":"/opt/private/runtime"}\n',
            output_root=tmp_path,
        )
    with pytest.raises(EvidenceValidationError, match="absolute path"):
        write_retained_artifact(
            tmp_path / "unsafe-windows-path.json",
            b'{"diagnostic":"C:\\\\private\\\\runtime"}\n',
            output_root=tmp_path,
        )


def test_artifact_scan_does_not_treat_https_as_a_windows_drive(tmp_path: Path) -> None:
    write_retained_artifact(
        tmp_path / "public-schema-uri.json",
        b'{"schema":"https://deeplaw.dev/contracts/current.schema.json"}\n',
        output_root=tmp_path,
    )


def _report_run(index: int, scenario: str) -> dict[str, object]:
    methods = {
        "cold_start": ["thread/start"],
        "resume_fork": ["thread/start", "thread/resume", "thread/fork"],
        "compaction_forget": [
            "thread/start",
            "thread/compact/start",
            "item/started",
            "item/completed",
        ],
        "projection_status": ["opencode/run"],
        "source_forget": ["opencode/run"],
        "provider_boundary": ["opencode/run"],
    }[scenario]
    turn_methods = {
        "cold_start": ["thread/start"],
        "resume_fork": ["thread/start", "thread/resume", "thread/fork"],
        "compaction_forget": [
            "thread/start",
            "thread/compact/start",
            "thread/compact/start",
        ],
        "projection_status": ["opencode/run"],
        "source_forget": ["opencode/run"],
        "provider_boundary": ["opencode/run"],
    }[scenario]
    turns = []
    for turn_index, method in enumerate(turn_methods, 1):
        thread_generation = 0 if scenario != "resume_fork" or turn_index < 3 else 1
        turns.append(
            {
                "status": "passed",
                "lifecycle_method": method,
                "thread_id_sha256": hashlib.sha256(
                    f"thread:{scenario}:{thread_generation}".encode()
                ).hexdigest(),
                "turn_id_sha256": hashlib.sha256(
                    f"turn:{scenario}:{turn_index}".encode()
                ).hexdigest(),
                "prompt_sha256": hashlib.sha256(
                    f"prompt:{scenario}:{turn_index}".encode()
                ).hexdigest(),
                "final_response_sha256": hashlib.sha256(
                    f"response:{scenario}:{turn_index}".encode()
                ).hexdigest(),
                "final_response_bytes": 40,
                "host_elapsed_ms": 5,
                "ledger_audit_head_before": "a" * 64,
                "ledger_audit_head_after": "a" * 64,
                "ledger_unchanged": True,
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 2,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 4,
                    "reasoning_output_tokens": 1,
                    "total_tokens": 17
                    if scenario in {"projection_status", "source_forget", "provider_boundary"}
                    else 14,
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
                            "provider_sha256": hashlib.sha256(
                                f"provider:{scenario}:{turn_index}".encode()
                            ).hexdigest(),
                            "structured_output_bytes": 200,
                            "structured_output_sha256": hashlib.sha256(
                                f"structured:{scenario}:{turn_index}".encode()
                            ).hexdigest(),
                            "delivery_match": True,
                            "write_performed": False,
                            "statement_count": 0 if scenario == "source_forget" else 1,
                            "gap_count": 1
                            if scenario
                            in {"compaction_forget", "projection_status", "source_forget"}
                            else 0,
                            "gap_codes": {
                                "compaction_forget": ["forgotten_knowledge"],
                                "projection_status": ["uncompiled_source"],
                                "source_forget": ["source_withdrawn"],
                            }.get(scenario, []),
                        }
                    ],
                },
                "sanitized_events": {
                    "name": f"run-{index}-turn-{turn_index}.jsonl",
                    "bytes": 20,
                    "sha256": hashlib.sha256(
                        f"events:{scenario}:{turn_index}".encode()
                    ).hexdigest(),
                },
            }
        )
    metrics = {
        "first_correct_action": True,
        "decision_preservation": True if scenario == "resume_fork" else None,
        "wrong_state_admission": 0,
        "stale_state_rejected": True,
        "forgotten_state_admission": 0
        if scenario in {"compaction_forget", "source_forget"}
        else None,
        "gap_observed": True
        if scenario in {"compaction_forget", "source_forget"}
        else None,
        "projection_state_correct": True if scenario == "projection_status" else None,
        "retention_wording_correct": True if scenario == "source_forget" else None,
        "provider_boundary_correct": True,
        "evidence_sha256": "0" * 64,
    }
    mutation_kinds = {
        "cold_start": ("seed_checkpoint",),
        "resume_fork": ("seed_checkpoint",),
        "compaction_forget": ("seed_checkpoint", "forget"),
        "projection_status": ("seed_checkpoint",),
        "source_forget": ("seed_checkpoint", "forget"),
        "provider_boundary": ("none",),
    }[scenario]
    boundaries = []
    for boundary_index, kind in enumerate(mutation_kinds, 1):
        changed = kind != "none"
        boundaries.append(
            {
                "kind": kind,
                "owner_enabled": changed,
                "read_mcp_write_performed": False,
                "audit_changed": changed,
                "audit_head_before": "b" * 64,
                "audit_head_after": "c" * 64 if changed else "b" * 64,
                "receipt_sha256": hashlib.sha256(
                    f"receipt:{scenario}:{boundary_index}".encode()
                ).hexdigest()
                if changed
                else None,
                "target_sha256": hashlib.sha256(
                    f"target:{scenario}:{boundary_index}".encode()
                ).hexdigest()
                if changed
                else None,
            }
        )
    run: dict[str, object] = {
        "run_index": index,
        "scenario": scenario,
        "status": "passed",
        "failure_codes": [],
        "task_sha256": hashlib.sha256(f"task:{scenario}".encode()).hexdigest(),
        "new_thread": True,
        "methods_observed": methods,
        "turns": turns,
        "metrics": metrics,
        "mutation_boundaries": boundaries,
    }
    metrics["evidence_sha256"] = metric_evidence_sha256(run)
    return run


def _report(host: str) -> dict[str, object]:
    scenarios = (
        ("cold_start", "resume_fork", "compaction_forget")
        if host == "codex"
        else ("projection_status", "source_forget", "provider_boundary")
    )
    runs = [_report_run(index, scenario) for index, scenario in enumerate(scenarios, 1)]
    turns = [turn for run in runs for turn in run["turns"]]  # type: ignore[index]
    report = {
        "schema_version": "deeplaw.host-continuity-qualification/v1",
        "host": host,
        "status": "executed",
        "package_version": "0.12.0",
        "release_ready": False,
        "claim_eligible": False,
        "binding": {
            "commit": "1" * 40,
            "tree": "2" * 40,
            "worktree_clean": True,
            "wheel_name": "deeplaw-0.12.0-py3-none-any.whl",
            "wheel_sha256": "3" * 64,
            "wheel_bytes": 100,
            "runtime_executable_sha256": "4" * 64,
            "import_path_class": "isolated_site_packages",
            "contract_digests": {
                "host-continuity-qualification.v1.schema.json": "5" * 64,
                "knowledge-support.output.v6.schema.json": "6" * 64,
                "provider-knowledge-capsule.v2.schema.json": "7" * 64,
            },
        },
        "environment": {
            "operating_system": "Darwin",
            "architecture": "arm64",
            "python_version": "3.13.7",
            "isolation": {
                "profile_kind": "temporary_closed",
                "home_isolated": True,
                "codex_home_isolated": host == "codex",
                "xdg_config_home_isolated": True,
                "xdg_data_home_isolated": True,
                "ambient_host_state_inherited": False,
                "ambient_plugins_inherited": False,
                "ambient_apps_inherited": False,
                "ambient_hooks_inherited": False,
                "secret_values_retained": False,
                "auth_class": "chatgpt_login" if host == "codex" else "deepseek_api_key",
            },
        },
        "host_attestation": {
            "binary_name": host,
            "binary_sha256": "8" * 64,
            "version": "current",
            "model": "gpt-5.6-luna" if host == "codex" else "deepseek/deepseek-v4-flash",
            "reasoning_effort": "max",
            "authentication": {
                "status": "existing_login_confirmed" if host == "codex" else "provider_available",
                "source": "existing_codex_login" if host == "codex" else "process_environment",
                "auth_file_read": False,
                "checked": True,
                "raw_sha256": "c" * 64,
                "raw_bytes": 50,
            },
            "model_inventory": {
                "checked": True,
                "selected_present": True,
                "raw_sha256": "9" * 64,
                "raw_bytes": 100,
            },
            "mcp_inventory": {
                "checked": True,
                "selected_present": True,
                "raw_sha256": "a" * 64,
                "raw_bytes": 100,
            },
            **(
                {
                    "availability": {
                        "status": "available",
                        "raw_sha256": "b" * 64,
                        "raw_bytes": 100,
                        "elapsed_ms": 5,
                        "input_tokens": 10,
                        "cached_input_tokens": 2,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 4,
                        "reasoning_output_tokens": 1,
                        "total_tokens": 17,
                    }
                }
                if host == "opencode"
                else {}
            ),
        },
        "lifecycle": {
            "host_owns_threads": True,
            "methods_observed": [
                "thread/start",
                "thread/resume",
                "thread/fork",
                "thread/compact/start",
                "item/started",
                "item/completed",
            ]
            if host == "codex"
            else ["not_applicable"],
            "deeplaw_session_store_created": False,
        },
        "security": {
            "mcp_child_closed_environment": True,
            "only_knowledge_support_enabled": True,
            "absolute_path_leak": False,
            "secret_leak": False,
            "raw_transcript_retained": False,
            "hidden_reasoning_retained": False,
            "authentication_material_retained": False,
        },
        "runs": runs,
        "aggregate": {
            "passed_runs": 3,
            "failed_runs": 0,
            "first_call_valid_runs": 3,
            "bounded_retry_runs": 0,
            "provider_bytes": 100 * len(turns),
            "input_tokens": 10 * len(turns),
            "cached_input_tokens": 2 * len(turns),
            "cache_write_input_tokens": 0,
            "output_tokens": 4 * len(turns),
            "reasoning_output_tokens": len(turns),
            "total_tokens": (17 if host == "opencode" else 14) * len(turns),
            "host_elapsed_ms": 5 * len(turns),
        },
        "not_executed": ["Human review"],
    }
    return report


def test_report_consistency_freezes_scenarios_reads_tokens_and_aggregates() -> None:
    report = _report("codex")
    validate_host_report_consistency(report)

    report["runs"][2]["scenario"] = "cold_start"  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="scenario matrix"):
        validate_host_report_consistency(report)


def test_v2_accepts_only_the_exact_codex_keyring_bridge_isolation_variant() -> None:
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "contracts"
            / "host-continuity-qualification.v2.schema.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema["$defs"]["codexKeyringBridgeIsolation"])
    receipt = pass13_evidence.isolation_receipt(host="codex", keyring_bridge=True)
    assert list(validator.iter_errors(receipt)) == []

    receipt["codex_home_isolated"] = False
    assert list(validator.iter_errors(receipt))


def test_historical_v1_metric_digest_ignores_absent_v2_native_receipts() -> None:
    expected = {
        "cold_start": "fcf1f79017ca545a588855606ac8f8c030b51b4a8ac5407c9eac6a5e2ca37720",
        "resume_fork": "9227b7bd8e95a9f51a3258fdeb08b19c5caa9582a88b7e34ae75d928a3c6a098",
        "compaction_forget": "d5cf5cea710e07479627f76ccc8456fb8f858f4119aba30be2dec8c351f2c825",
    }
    for index, scenario in enumerate(expected, 1):
        run = _report_run(index, scenario)
        assert "native_receipts" not in run
        assert run["metrics"]["evidence_sha256"] == expected[scenario]


def test_opencode_fork_lineage_uses_cli_predecessor_not_fabricated_get_parent() -> None:
    root = "a" * 64
    forked = "b" * 64
    receipts = [
        {
            "requested_operation": "cli.run",
            "identity_lineage": {
                "current_sha256": root,
                "parent_sha256": None,
                "root_sha256": root,
            },
        },
        {
            "requested_operation": "session.get",
            "identity_lineage": {
                "current_sha256": root,
                "parent_sha256": None,
                "root_sha256": root,
            },
        },
        {
            "requested_operation": "cli.run.fork",
            "identity_lineage": {
                "current_sha256": forked,
                "parent_sha256": root,
                "root_sha256": root,
            },
        },
        {
            "requested_operation": "session.get",
            "identity_lineage": {
                "current_sha256": forked,
                "parent_sha256": None,
                "root_sha256": root,
            },
        },
    ]
    pass13_evidence._validate_native_lineage_sequence("opencode", receipts)

    receipts[-1]["identity_lineage"]["parent_sha256"] = root
    with pytest.raises(EvidenceValidationError, match="unsupported parent lineage"):
        pass13_evidence._validate_native_lineage_sequence("opencode", receipts)


def test_legacy_compaction_notification_cannot_prove_current_qualification() -> None:
    report = _report("codex")
    run = report["runs"][2]  # type: ignore[index]
    run["methods_observed"] = [
        "thread/start",
        "thread/compact/start",
        "thread/compacted",
    ]
    report["lifecycle"]["methods_observed"] = [  # type: ignore[index]
        "thread/start",
        "thread/resume",
        "thread/fork",
        "thread/compact/start",
        "thread/compacted",
    ]
    with pytest.raises(EvidenceValidationError, match="lifecycle method"):
        validate_host_report_consistency(report)


def test_report_consistency_rejects_empty_read_and_self_reported_aggregate() -> None:
    report = _report("opencode")
    report["runs"][0]["turns"][0]["safe_read"] = {  # type: ignore[index]
        "call_count": 0,
        "first_call_valid": False,
        "bounded_retry_used": False,
        "safe_read_operations": [],
        "provider_payloads": [],
    }
    with pytest.raises(EvidenceValidationError, match="contract"):
        validate_host_report_consistency(report)

    report["runs"][0] = _report_run(1, "projection_status")  # type: ignore[index]
    report["aggregate"]["provider_bytes"] = 1  # type: ignore[index]
    with pytest.raises(EvidenceValidationError, match="scenario matrix"):
        validate_host_report_consistency(report)
