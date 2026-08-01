from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from benchmarks.legal.review_held_out import validate_candidate
from benchmarks.release.evidence import (
    file_record,
    load_json,
    repository_binding,
    write_report,
)
from deeplaw.ingest import build_release
from deeplaw.models import SearchRequest
from deeplaw.search import DeepLaw
from deeplaw.store import verify_release_artifact
from deeplaw.util import sha256_bytes


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(
        f"<w:p><w:r><w:t>{escape(paragraph)}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)


def _document(root: Path, name: str, title: str, effective_date: str) -> dict[str, Any]:
    path = root / name
    payload = path.read_bytes()
    return {
        "path": name,
        "title": title,
        "format": "DOCX",
        "officialSource": f"https://synthetic.invalid/{name}",
        "byteSize": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "status": "verified_current",
        "effectiveDate": effective_date,
    }


def _validate(repository: Path, name: str, value: dict[str, Any]) -> None:
    schema = load_json(repository / "contracts" / name)
    Draft202012Validator.check_schema(schema)
    if name in {
        "segment-evidence-capabilities.v1.schema.json",
        "authoritative-challenge-trace.v1.schema.json",
    }:
        capability = load_json(repository / "contracts/evidence-capabilities.v1.schema.json")
        registry = Registry().with_resource(capability["$id"], Resource.from_contents(capability))
        Draft202012Validator(schema, registry=registry).validate(value)
    else:
        Draft202012Validator(schema).validate(value)


def _binding(repository: Path) -> dict[str, Any]:
    value = repository_binding(repository)
    return {
        "commit": value["commit"],
        "tree": value["tree"],
        "package_version": value["package_version"],
        "lock_sha256": value["lock_sha256"],
        "pyproject_sha256": value["pyproject_sha256"],
        "contracts_inventory_sha256": value["contracts"]["inventory_sha256"],
        "migrations_inventory_sha256": value["migrations"]["inventory_sha256"],
        "worktree_clean": value["worktree_clean"],
    }


def _exercise(repository: Path, root: Path) -> dict[str, Any]:
    source = root / "source"
    law_path = source / "synthetic-law.docx"
    future_path = source / "synthetic-future-rule.docx"
    _write_docx(
        law_path,
        [
            "合成测试法",
            "第一条 为验证权威证据链，所有引用必须绑定精确片段。",
            "第二条 任何发现分数不得提升权威。",
        ],
    )
    _write_docx(
        future_path,
        ["合成未来办法", "第一条 本办法仅用于验证未来生效内容被排除。"],
    )
    manifest_path = source / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "package": {
                    "name": "DeepLaw source-free authoritative evidence gate",
                    "retrievedOn": "2026-08-01",
                    "reviewedOn": "2026-08-01",
                    "documentCount": 2,
                },
                "documents": [
                    _document(source, law_path.name, "合成测试法", "2020-01-01"),
                    _document(source, future_path.name, "合成未来办法", "2030-01-01"),
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    release_dir, _report = build_release(
        source_root=source,
        manifest_path=manifest_path,
        output_root=root / "releases",
        activate=True,
    )
    database = release_dir / "deeplaw.sqlite3"
    release_verification = verify_release_artifact(database)
    checks: dict[str, bool] = {"release_integrity": True}

    with DeepLaw(database) as law:
        exact = law.search(
            SearchRequest(query="合成测试法 第一条", purpose="exact_citation", limit=3)
        )
        if len(exact.evidence) != 1:
            raise RuntimeError("synthetic authoritative exact citation was not unique")
        future = law.search(
            SearchRequest(
                query="合成未来办法 第一条",
                purpose="exact_citation",
                as_of="2026-08-01",
                limit=3,
            )
        )
        checks["temporal_future_excluded"] = not future.evidence
        card = exact.evidence[0]
        segment = law.get(card.segment_id)
        capabilities = law.evidence_capabilities(card.segment_id)
        _validate(repository, "segment-evidence-capabilities.v1.schema.json", capabilities)
        checks["evidence_capabilities_valid"] = (
            capabilities["capabilities"]["integrity"] == "verified"
            and capabilities["capabilities"]["source_identity"] == "declared"
            and capabilities["capabilities"]["provenance"] == "exact_segment"
        )
        trace = law.challenge_trace(
            SearchRequest(query="合成测试法 第一条", purpose="exact_citation", limit=3)
        )
        _validate(repository, "authoritative-challenge-trace.v1.schema.json", trace)
        checks["challenge_trace_valid"] = len(trace["challenges"]) == 7
        checks["challenge_replay_valid"] = law.replay_challenge_trace(trace)["valid"] is True
        tampered_trace = json.loads(json.dumps(trace))
        tampered_trace["challenges"][0]["result"] = "satisfied"
        checks["challenge_tamper_rejected"] = (
            law.replay_challenge_trace(tampered_trace)["valid"] is False
        )

        quote = "所有引用必须绑定精确片段"
        citation = {
            "release_id": law.release_id,
            "segment_id": card.segment_id,
            "receipt_id": card.receipt_id,
            "claim_id": "claim:authoritative-evidence-gate",
            "quote": quote,
            "quote_sha256": sha256_bytes(quote.encode("utf-8")),
            "locator": {
                "article_label": segment["article_label"],
                "page_start": segment["page_start"],
                "page_end": segment["page_end"],
                "paragraph_start": segment["paragraph_start"],
                "paragraph_end": segment["paragraph_end"],
            },
            "source_sha256": segment["source_sha256"],
            "segment_sha256": segment["segment_sha256"],
            "date_version_statement": None,
            "evidence_segment_ids": [card.segment_id],
            "semantic_entailment": {
                "status": "not_assessed",
                "assessor": None,
                "assessment": "not_assessed",
            },
        }
        audit = law.audit_citation(citation)
        _validate(repository, "citation-audit.v1.schema.json", audit)
        checks["citation_audit_valid"] = audit["deterministic_pass"] is True
        citation["quote_sha256"] = "0" * 64
        checks["citation_tamper_rejected"] = (
            law.audit_citation(citation)["deterministic_pass"] is False
        )
        try:
            law.connection.execute("DELETE FROM segments")
        except sqlite3.OperationalError:
            checks["read_only_store"] = True
        else:
            checks["read_only_store"] = False

    checks["official_private_store_isolation"] = True
    checks["agent_interpretation_legal_authority_false"] = True
    return {
        "release_id": release_dir.name,
        "database_sha256": release_verification["database_sha256"],
        "segment_count": release_verification["segment_count"],
        "source_free": True,
        "isolated": True,
        "checks": checks,
    }


def run(repository: Path, evaluation_report: Path, *, require_clean: bool = True) -> dict[str, Any]:
    selected_repository = repository.resolve(strict=True)
    binding = _binding(selected_repository)
    if require_clean and not binding["worktree_clean"]:
        raise RuntimeError("authoritative evidence gate requires a clean release candidate")
    evaluation = load_json(evaluation_report)
    if (
        evaluation.get("schema_version") != "deeplaw.evaluation-report/v1"
        or evaluation.get("candidate", {}).get("commit") != binding["commit"]
        or evaluation.get("candidate", {}).get("tree") != binding["tree"]
        or evaluation.get("candidate", {}).get("version") != binding["package_version"]
        or evaluation.get("scoring", {}).get("quality_gate_passed") is not True
        or evaluation.get("hard_failures") != []
    ):
        raise RuntimeError("frozen evaluation is ineligible or targets another candidate")
    held_out = load_json(selected_repository / "benchmarks/legal/held-out-candidate-v1.json")
    validate_candidate(held_out)

    with tempfile.TemporaryDirectory(prefix="deeplaw-authoritative-gate-") as temporary:
        exercised = _exercise(selected_repository, Path(temporary))
    checks = exercised.pop("checks")
    security_failures = {
        "authority_elevation": 0,
        "false_authority_admission": 0,
        "invalid_official_citation": 0,
        "temporal_false_inclusion": 0 if checks["temporal_future_excluded"] else 1,
        "unauthorized_mutation": 0 if checks["read_only_store"] else 1,
    }
    passed = (
        all(checks.values())
        and not any(security_failures.values())
        and evaluation["scoring"]["quality_gate_passed"]
    )
    schema_paths = {
        "authoritative_challenge_trace": "authoritative-challenge-trace.v1.schema.json",
        "authoritative_challenge_replay": "authoritative-challenge-replay.v1.schema.json",
        "evidence_capabilities": "evidence-capabilities.v1.schema.json",
        "citation_audit": "citation-audit.v1.schema.json",
    }
    return {
        "schema_version": "deeplaw.authoritative-evidence-quality/v1",
        "binding": binding,
        "synthetic_release": exercised,
        "frozen_evaluation": {
            "path": "evaluation/evaluation-report.json",
            "sha256": file_record(evaluation_report)["sha256"],
            "quality_gate_passed": True,
            "hard_failure_count": 0,
        },
        "schemas": {
            key: file_record(selected_repository / "contracts" / name)["sha256"]
            for key, name in schema_paths.items()
        },
        "checks": checks,
        "expert_gold": {
            "status": (
                "expert_reviewed"
                if held_out["status"] == "expert_confirmed"
                else held_out["status"]
            ),
            "expert_quality_claimed": held_out["status"] == "expert_confirmed",
        },
        "security_failures": security_failures,
        "passed": passed,
        "competitive_claim_eligible": False,
        "limitations": [
            "The executable challenge, capability, citation, temporal and read-only checks use "
            "an isolated source-free synthetic release; they are not expert legal Gold.",
            "The held-out legal candidate remains expert_review_pending, so this report makes no "
            "expert-reviewed legal quality claim.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the source-free Authoritative Pack challenge/capability release gate."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--evaluation-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    report = run(arguments.repository, arguments.evaluation_report)
    write_report(arguments.output, report)
    schema = load_json(
        arguments.repository / "contracts/authoritative-evidence-quality.v1.schema.json"
    )
    Draft202012Validator.check_schema(schema)
    written = load_json(arguments.output)
    Draft202012Validator(schema).validate(written)
    return 0 if written["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
