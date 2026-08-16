from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA = REPOSITORY / "contracts/obsidian-desktop-qualification.v1.schema.json"
HISTORICAL = (
    REPOSITORY
    / "benchmarks/hosts/evidence/obsidian-desktop-qualification-2026-08-11"
    / "obsidian-desktop-qualification.json"
)
CURRENT = (
    REPOSITORY
    / "benchmarks/hosts/evidence/pass11-obsidian-desktop-2026-08-11"
    / "obsidian-desktop-qualification.json"
)


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_formal_receipt_schema_accepts_retained_historical_candidate() -> None:
    _validator().validate(json.loads(HISTORICAL.read_text(encoding="utf-8")))


def test_exact_candidate_receipt_binds_real_captures_without_local_paths() -> None:
    report = json.loads(CURRENT.read_text(encoding="utf-8"))
    _validator().validate(report)
    serialized = CURRENT.read_text(encoding="utf-8")

    assert report["status"] in {"executed", "failed"}
    assert report["claim_eligible"] is False
    assert report["release_ready"] is False
    assert report["candidate"]["package_version"] == "0.12.0"
    assert report["synthetic_fixture"]["contains_case_or_customer_data"] is False
    assert report["synthetic_fixture"]["vault_path_in_report"] is False
    assert report["synthetic_fixture"]["credential_or_secret_in_report"] is False
    assert "/Users/" not in serialized
    assert "/tmp/" not in serialized
    assert "BEGIN PRIVATE KEY" not in serialized
    for capture in report["captures"]:
        payload = (CURRENT.parent / capture["path_hint"]).read_bytes()
        assert capture["byte_size"] == len(payload)
        assert capture["sha256"] == hashlib.sha256(payload).hexdigest()


def test_exact_candidate_manifest_binds_every_retained_artifact() -> None:
    manifest = json.loads(
        (CURRENT.parent / "SHA256SUMS.json").read_text(encoding="utf-8")
    )
    expected = {
        path.name: path
        for path in CURRENT.parent.iterdir()
        if path.is_file() and path.name != "SHA256SUMS.json"
    }

    assert {item["name"] for item in manifest["artifacts"]} == set(expected)
    for item in manifest["artifacts"]:
        payload = expected[item["name"]].read_bytes()
        assert item["bytes"] == len(payload)
        assert item["sha256"] == hashlib.sha256(payload).hexdigest()
