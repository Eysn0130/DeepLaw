from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from benchmarks.release import typed_qualification_evidence as typed
from benchmarks.release.typed_qualification_evidence import (
    TypedQualificationEvidenceError,
    parse_typed_evidence,
)

COMMIT = "a" * 40
TREE = "b" * 40
LOCK = "c" * 64
WHEEL = "d" * 64
SDIST = "e" * 64
RUNNER = {"identity": "runner:synthetic", "sha256": "1" * 64}
SCORER = {"identity": "scorer:synthetic", "sha256": "2" * 64}
CASE_TYPES = (
    "exact_source_locator",
    "wrong_version_rejection",
    "false_authority_rejection",
    "effective_date_exception_proviso_cross_reference",
    "ocr_critical_token_gap",
    "wiki_exact_source_drill_down",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _source(root: Path, relative: str, raw: bytes, media_type: str) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "relative_path": relative,
        "byte_size": len(raw),
        "sha256": _sha(raw),
        "media_type": media_type,
    }


def _json_source(root: Path, relative: str, value: Any) -> dict[str, Any]:
    return _source(root, relative, _canonical(value), "application/json")


def _seal(value: dict[str, Any]) -> dict[str, Any]:
    body = {key: item for key, item in value.items() if key != "record_sha256"}
    value["record_sha256"] = _sha(_canonical(body))
    return value


def _candidate() -> dict[str, str]:
    return {
        "commit": COMMIT,
        "tree": TREE,
        "lock_sha256": LOCK,
        "wheel_sha256": WHEEL,
        "sdist_sha256": SDIST,
    }


def _evidence(case_type: str, source_id: str, version_id: str, fragment_id: str) -> dict[str, Any]:
    quote = f"quote for {source_id}"
    fragment_text = f"fragment for {source_id}: {quote}"
    value: dict[str, Any] = {
        "document": {"source_id": source_id},
        "version": {"version_id": version_id},
        "fragment": {
            "document_id": source_id,
            "version_id": version_id,
            "fragment_id": fragment_id,
            "text": fragment_text,
            "text_sha256": _sha(fragment_text.encode("utf-8")),
        },
        "locator": {"kind": "page", "value": "1"},
        "quote": {"text": quote, "sha256": _sha(quote.encode("utf-8"))},
        "effective_date": "2026-01-01",
        "exception": [],
        "proviso": [],
        "cross_reference": [],
        "ocr_critical_token": [],
        "rejection": None,
        "gap": None,
        "wiki_drill_down": None,
    }
    if case_type == "wrong_version_rejection":
        value["rejection"] = {
            "code": "wrong_version_rejected",
            "challenged_version_id": f"{version_id}-challenged",
        }
        value["exception"] = ["version mismatch was rejected"]
    elif case_type == "false_authority_rejection":
        value["rejection"] = {
            "code": "false_authority_rejected",
            "challenged_authority": "official",
            "challenged_legal_authority": True,
        }
        value["exception"] = ["unverified source was not promoted"]
    elif case_type == "effective_date_exception_proviso_cross_reference":
        value["exception"] = ["exception text"]
        value["proviso"] = ["proviso text"]
        value["cross_reference"] = ["source:related-version"]
    elif case_type == "ocr_critical_token_gap":
        value["ocr_critical_token"] = ["critical-token"]
        value["gap"] = {"code": "ocr_critical_token_gap"}
    elif case_type == "wiki_exact_source_drill_down":
        value["wiki_drill_down"] = {
            "source_id": source_id,
            "version_id": version_id,
            "fragment_id": fragment_id,
            "locator": copy.deepcopy(value["locator"]),
            "quote_sha256": value["quote"]["sha256"],
        }
    return value


def _professional_fixture(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    catalog_rows: list[dict[str, Any]] = []
    original_refs: list[dict[str, Any]] = []
    expected_rows: list[dict[str, Any]] = []
    for index, case_type in enumerate(CASE_TYPES):
        source_id = f"source-{index}"
        version_id = f"version-{index}"
        fragment_id = f"fragment-{index}"
        raw = f"original source bytes {index}\n".encode()
        source_ref = _source(
            tmp_path,
            f"sources/{source_id}.md",
            raw,
            "text/markdown",
        )
        catalog_rows.append(
            {
                "source_id": source_id,
                "version_id": version_id,
                "document_sha256": source_ref["sha256"],
                "document_byte_size": source_ref["byte_size"],
                "media_type": "text/markdown",
                "origin": "user_source" if index % 2 == 0 else "external_import",
                "authority": "source_attributed",
                "legal_authority": False,
                "effective_date": "2026-01-01",
            }
        )
        original_refs.append(
            {
                "source_id": source_id,
                "version_id": version_id,
                "source": source_ref,
            }
        )
        expected = _evidence(case_type, source_id, version_id, fragment_id)
        expected_rows.append(
            {
                "case_id": f"case-{index}",
                "case_type": case_type,
                "source_id": source_id,
                "version_id": version_id,
                "fragment_id": fragment_id,
                "expected": expected,
            }
        )
    catalog_source = _json_source(tmp_path, "professional/catalog.json", {"sources": catalog_rows})
    expected_source = _json_source(
        tmp_path,
        "professional/expected.json",
        {"rows": expected_rows},
    )
    envelope: dict[str, Any] = {
        "schema_version": typed.SCHEMA_V3_VERSION,
        "profile": "kernel_release_core",
        "reference_provenance": "deterministic_expected_evidence",
        "human_authenticity": "not_claimed",
        "kind": "professional_evidence_rows",
        "candidate_binding": _candidate(),
        "run_binding": {"run_id": "run-v013-professional", "workflow_run_id": 1},
        "corpus": {"sha256": expected_source["sha256"], "role": "professional_evidence"},
        "runner": RUNNER,
        "scorer": SCORER,
        "payload": {
            "source_catalog_source": catalog_source,
            "original_source_refs": original_refs,
            "expected_source": expected_source,
            "observed_source": {},
        },
        "record_sha256": "",
    }
    receipt = {
        "candidate": envelope["candidate_binding"],
        "run": envelope["run_binding"],
        "corpus": envelope["corpus"],
        "runner": envelope["runner"],
        "scorer": envelope["scorer"],
    }
    observed_rows = [
        {
            **{key: value for key, value in row.items() if key != "expected"},
            "observed": copy.deepcopy(row["expected"]),
        }
        for row in expected_rows
    ]
    observed_source = _json_source(
        tmp_path,
        "professional/observed.json",
        {"receipt": receipt, "rows": observed_rows},
    )
    envelope["payload"]["observed_source"] = observed_source
    manifest = _seal(envelope)
    manifest_path = tmp_path / "professional-evidence.json"
    manifest_path.write_bytes(_canonical(manifest))
    return manifest_path, {
        "manifest": manifest,
        "catalog": catalog_rows,
        "expected_rows": expected_rows,
    }


def _rewrite_manifest(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(_canonical(_seal(value)))


def _rewrite_json_source(
    root: Path,
    manifest: dict[str, Any],
    field: str,
    value: Any,
    relative: str,
) -> None:
    ref = _json_source(root, relative, value)
    manifest["payload"][field] = ref
    if field == "expected_source":
        manifest["corpus"]["sha256"] = ref["sha256"]
    _rewrite_manifest(root / "professional-evidence.json", manifest)


def test_v3_schema_is_kernel_core_and_excludes_competitive_scorer_fields(
    tmp_path: Path,
) -> None:
    manifest_path, state = _professional_fixture(tmp_path)
    schema = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts/typed-qualification-evidence.v3.schema.json"
        ).read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["profile"] == {"const": "kernel_release_core"}
    assert schema["properties"]["reference_provenance"]["enum"] == [
        "deterministic_expected_evidence",
        "not_applicable",
    ]
    role_enum = schema["$defs"]["corpus"]["properties"]["role"]["enum"]
    assert role_enum == [
        "candidate_full",
        "candidate_platform",
        "host_qualification",
        "professional_evidence",
        "living_wiki",
        "scale_10000",
        "supply_chain",
    ]
    assert "qualification_holdout" not in role_enum
    assert "final_blind" not in role_enum
    assert "machine_reference_scorer" not in schema["properties"]["kind"]["enum"]
    assert "scorer_panel" not in schema["properties"]
    assert "arbiter" not in schema["properties"]
    Draft202012Validator(schema).validate(state["manifest"])
    assert manifest_path.is_file()


def test_professional_six_cases_derive_passed_gate_metrics(tmp_path: Path) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    expected_hash = json.loads(manifest_path.read_text(encoding="utf-8"))["corpus"]["sha256"]
    result = parse_typed_evidence(
        manifest_path,
        expected_corpus_sha256=expected_hash,
    )
    assert result["schema_version"] == typed.DERIVED_V3_SCHEMA_VERSION
    assert result["kind"] == "professional_evidence_rows"
    assert result["status"] == "passed"
    assert result["metrics"]["case_count"] == 6
    assert result["metrics"]["required_case_type_count"] == 6
    assert all(
        result["metrics"][name] == 1.0
        for name in (
            "original_bytes_preservation_rate",
            "original_hash_match_rate",
            "document_identity_rate",
            "version_identity_rate",
            "fragment_identity_rate",
            "locator_validity_rate",
            "wrong_version_rejection_rate",
            "effective_date_rate",
            "exception_rate",
            "proviso_rate",
            "cross_reference_rate",
            "false_authority_zero_rate",
            "ocr_critical_token_gap_disposition_rate",
            "wiki_exact_source_drill_down_rate",
        )
    )
    assert all(value == 0 for value in result["hard_failure_counts"].values())


@pytest.mark.parametrize(
    "field",
    [
        "document",
        "version",
        "fragment",
        "locator",
        "quote",
        "effective_date",
        "exception",
        "proviso",
        "cross_reference",
        "ocr_critical_token",
        "rejection",
        "gap",
        "wiki_drill_down",
    ],
)
def test_professional_missing_duty_fails_closed(tmp_path: Path, field: str) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = json.loads(
        (manifest_path.parent / manifest["payload"]["expected_source"]["relative_path"]).read_text(
            encoding="utf-8"
        )
    )
    expected["rows"][0]["expected"].pop(field)
    _rewrite_json_source(
        tmp_path,
        manifest,
        "expected_source",
        expected,
        "professional/expected.json",
    )
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )


def test_professional_original_bytes_and_hash_tamper_fail_closed(tmp_path: Path) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_ref = manifest["payload"]["original_source_refs"][0]["source"]
    original = manifest_path.parent / original_ref["relative_path"]
    original.write_bytes(b"tampered bytes\n")
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )

    manifest_path, _state = _professional_fixture(tmp_path / "hash")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_ref = manifest["payload"]["original_source_refs"][0]["source"]
    source_path = manifest_path.parent / source_ref["relative_path"]
    source_path.write_bytes(b"new bytes with a changed hash\n")
    source_ref["byte_size"] = source_path.stat().st_size
    source_ref["sha256"] = _sha(source_path.read_bytes())
    _rewrite_manifest(manifest_path, manifest)
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )


@pytest.mark.parametrize(
    "field, value",
    [
        ("authority", "official"),
        ("authority", "human_verified"),
        ("legal_authority", True),
    ],
)
def test_professional_authority_forgery_fails_closed(
    tmp_path: Path, field: str, value: Any
) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    catalog_ref = manifest["payload"]["source_catalog_source"]
    catalog_path = manifest_path.parent / catalog_ref["relative_path"]
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["sources"][0][field] = value
    _rewrite_json_source(
        tmp_path,
        manifest,
        "source_catalog_source",
        catalog,
        "professional/catalog.json",
    )
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )


@pytest.mark.parametrize(
    "case_type, expected_code",
    [
        ("wrong_version_rejection", "wrong_version_rejected"),
        ("false_authority_rejection", "false_authority_rejected"),
    ],
)
def test_professional_negative_cases_require_explicit_rejection_code(
    tmp_path: Path, case_type: str, expected_code: str
) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    observed_path = manifest_path.parent / manifest["payload"]["observed_source"]["relative_path"]
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    for row in observed["rows"]:
        if row["case_type"] == case_type:
            row["observed"]["rejection"] = None
            break
    _rewrite_json_source(
        tmp_path,
        manifest,
        "observed_source",
        observed,
        "professional/observed.json",
    )
    with pytest.raises(TypedQualificationEvidenceError, match=expected_code):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )


@pytest.mark.parametrize(
    "case_type, mutation",
    [
        (
            "wrong_version_rejection",
            lambda evidence, row: evidence["rejection"].update(
                {"challenged_version_id": row["version_id"]}
            ),
        ),
        (
            "wrong_version_rejection",
            lambda evidence, _row: evidence["rejection"].pop("challenged_version_id"),
        ),
        (
            "false_authority_rejection",
            lambda evidence, _row: evidence["rejection"].pop("challenged_authority"),
        ),
        (
            "false_authority_rejection",
            lambda evidence, _row: evidence["rejection"].update(
                {"challenged_legal_authority": False}
            ),
        ),
    ],
)
def test_professional_rejection_payload_is_code_closed(
    tmp_path: Path, case_type: str, mutation: Any
) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    observed_path = manifest_path.parent / manifest["payload"]["observed_source"]["relative_path"]
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    for row in observed["rows"]:
        if row["case_type"] == case_type:
            mutation(row["observed"], row)
            break
    _rewrite_json_source(
        tmp_path,
        manifest,
        "observed_source",
        observed,
        "professional/observed.json",
    )
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )


@pytest.mark.parametrize(
    "case_type, mutation",
    [
        (
            "exact_source_locator",
            lambda evidence: evidence.update(
                {
                    "rejection": {
                        "code": "wrong_version_rejected",
                        "challenged_version_id": "other",
                    }
                }
            ),
        ),
        (
            "effective_date_exception_proviso_cross_reference",
            lambda evidence: evidence.update({"proviso": []}),
        ),
    ],
)
def test_professional_non_negative_and_temporal_cross_reference_duties_fail_closed(
    tmp_path: Path, case_type: str, mutation: Any
) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    observed_path = manifest_path.parent / manifest["payload"]["observed_source"]["relative_path"]
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    for row in observed["rows"]:
        if row["case_type"] == case_type:
            mutation(row["observed"])
            break
    _rewrite_json_source(
        tmp_path,
        manifest,
        "observed_source",
        observed,
        "professional/observed.json",
    )
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )


@pytest.mark.parametrize(
    "field",
    [
        "machine_reference_scorer",
        "scorer_panel",
        "arbiter",
        "scorer_a",
        "scorer_b",
        "agent_review_panel",
        "agent_consensus",
        "machine_reference",
        "machine_reference_isolation",
        "qualification_holdout",
        "qualification_comparative_holdout",
        "final_blind",
        "final_blind_comparative_holdout",
        "comparative_incremental_benefit",
        "superiority",
        "sota",
    ],
)
def test_v3_competitive_field_names_are_rejected_but_values_are_not(
    tmp_path: Path, field: str
) -> None:
    manifest_path, _state = _professional_fixture(tmp_path / "manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = {"marker": "not a scorer"}
    _rewrite_manifest(manifest_path, manifest)
    with pytest.raises(TypedQualificationEvidenceError, match="competitive scorer"):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )
    typed._reject_v3_competitive_fields({"ordinary_value": field})


def test_professional_ocr_gap_and_wiki_drilldown_are_bound(tmp_path: Path) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    observed_path = manifest_path.parent / manifest["payload"]["observed_source"]["relative_path"]
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    for row in observed["rows"]:
        if row["case_type"] == "ocr_critical_token_gap":
            row["observed"]["gap"] = None
        if row["case_type"] == "wiki_exact_source_drill_down":
            row["observed"]["wiki_drill_down"]["quote_sha256"] = "f" * 64
    _rewrite_json_source(
        tmp_path,
        manifest,
        "observed_source",
        observed,
        "professional/observed.json",
    )
    with pytest.raises(TypedQualificationEvidenceError):
        parse_typed_evidence(
            manifest_path,
            expected_corpus_sha256=manifest["corpus"]["sha256"],
        )


def test_professional_expected_observed_mismatch_is_a_derived_failure(tmp_path: Path) -> None:
    manifest_path, _state = _professional_fixture(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    observed_path = manifest_path.parent / manifest["payload"]["observed_source"]["relative_path"]
    observed = json.loads(observed_path.read_text(encoding="utf-8"))
    observed["rows"][0]["observed"]["locator"]["value"] = "2"
    _rewrite_json_source(
        tmp_path,
        manifest,
        "observed_source",
        observed,
        "professional/observed.json",
    )
    result = parse_typed_evidence(
        manifest_path,
        expected_corpus_sha256=manifest["corpus"]["sha256"],
    )
    assert result["status"] == "failed"
    assert result["hard_failure_counts"]["expected_observed_mismatch"] == 1


def test_v1_and_v2_junit_behavior_remains_available(tmp_path: Path) -> None:
    xml = (
        b'<testsuite><testcase classname="tests.test_knowledge_control" '
        b'name="test_interrupted_migration_rolls_back_and_retains_a_verified_backup"/>'
        b'<testcase classname="tests.test_v013_pass22_continuity_closure" '
        b'name="test_partial_checkpoint_recovers_after_process_exit_and_restart"/>'
        b"</testsuite>"
    )
    for version, derived in (
        (typed.SCHEMA_VERSION, typed.DERIVED_SCHEMA_VERSION),
        (typed.SCHEMA_V2_VERSION, typed.DERIVED_V2_SCHEMA_VERSION),
    ):
        legacy_root = tmp_path / version.rsplit("/", 1)[-1]
        source = _source(legacy_root, "legacy/junit.xml", xml, "application/xml")
        envelope = {
            "kind": "candidate_full_junit",
            "candidate_binding": _candidate(),
            "run_binding": {"run_id": "legacy-run", "workflow_run_id": 1},
            "corpus": {"sha256": "f" * 64, "role": "candidate_full"},
            "runner": RUNNER,
            "scorer": SCORER,
            "payload": {"source": source},
            "record_sha256": "",
        }
        envelope["schema_version"] = version
        if version == typed.SCHEMA_V2_VERSION:
            envelope.update(
                {
                    "profile": "machine_evaluated_no_human_attestation",
                    "reference_provenance": "agent_consensus",
                    "human_authenticity": "not_claimed",
                }
            )
        path = legacy_root / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_canonical(_seal(envelope)))
        result = parse_typed_evidence(path)
        assert result["schema_version"] == derived
        assert result["status"] == "passed"
