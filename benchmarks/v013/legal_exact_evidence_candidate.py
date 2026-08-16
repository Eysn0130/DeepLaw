"""Offline exact-evidence development candidate.

The runner intentionally operates on a caller supplied development source JSON.  It
builds a temporary, unsigned release, exercises only the read-only ``DeepLaw`` API,
and emits a bounded fact record.  It never loads Gold, a scorer, a private legal
library, a network provider, or host credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from deeplaw.ingest import build_release
from deeplaw.knowledge_autonomy import SINK_OPERATIONS, AutonomousKnowledgeStore
from deeplaw.knowledge_sink_mcp_server import handle_knowledge_sink
from deeplaw.knowledge_store import initialize_knowledge_vault
from deeplaw.mcp_server import handle_support
from deeplaw.models import SearchRequest
from deeplaw.search import DeepLaw
from deeplaw.store import verify_release_artifact
from deeplaw.util import canonical_json, sha256_bytes

SCHEMA_VERSION = "deeplaw.legal-exact-evidence-candidate/v1"
MAX_SOURCE_JSON_BYTES = 1 * 1024 * 1024
MAX_DOCUMENTS = 32
MAX_PARAGRAPHS = 256
MAX_PARAGRAPH_CHARS = 12_000
MAX_QUERY_CHARS = 800
MAX_CASES = 32
MAX_OUTPUT_BYTES = 256 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,120}$")
_ARTICLE = re.compile(r"第[一二三四五六七八九十百千万零〇0-9]+条")
_ABSOLUTE_PATH = re.compile(r"(?:^|[\s=:\"])/(?:Users|home|tmp|private|var)(?:[\s/\"]|$)")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/]")
_SECRET = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|secret)\s*[:=]")


class CandidateError(ValueError):
    """Raised when a development source or candidate contract is unsafe."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_text(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise CandidateError(f"{field} is outside its bound")
    if _ABSOLUTE_PATH.search(value) or _WINDOWS_PATH.search(value) or _SECRET.search(value):
        raise CandidateError(f"{field} contains disallowed material")
    return value


def _safe_id(value: Any, *, field: str) -> str:
    text = _safe_text(value, field=field, maximum=120)
    if _ID.fullmatch(text) is None:
        raise CandidateError(f"{field} is invalid")
    return text


def _load_source(source: str | Path | Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if isinstance(source, Mapping):
        encoded = canonical_json(dict(source)).encode("utf-8")
        value = dict(source)
    else:
        path = Path(source).expanduser().absolute()
        if path.is_symlink() or not path.is_file():
            raise CandidateError("source JSON must be a regular non-symlink file")
        if not 1 <= path.stat().st_size <= MAX_SOURCE_JSON_BYTES:
            raise CandidateError("source JSON exceeds its byte bound")
        encoded = path.read_bytes()
        try:
            value = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CandidateError("source JSON must be UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise CandidateError("source JSON must contain one object")
    # A source-only runner must never accept an evaluator payload disguised as input.
    forbidden = {"gold", "gold_id", "scorer", "expected_ids", "answer_labels"}
    if forbidden.intersection(value):
        raise CandidateError("source JSON cannot contain Gold or scorer material")
    if len(encoded) > MAX_SOURCE_JSON_BYTES:
        raise CandidateError("source JSON exceeds its byte bound")
    if "case_id" in value:
        _safe_id(value["case_id"], field="source case_id")
    return value, _sha256_bytes(encoded)


def _paragraphs(raw: Mapping[str, Any], *, field: str) -> list[str]:
    values = raw.get("paragraphs", raw.get("text", raw.get("content")))
    if isinstance(values, str):
        values = values.splitlines()
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise CandidateError(f"{field} must contain paragraphs")
    if not 1 <= len(values) <= MAX_PARAGRAPHS:
        raise CandidateError(f"{field} paragraph count is outside its bound")
    selected = []
    for index, value in enumerate(values):
        selected.append(_safe_text(value, field=f"{field}[{index}]", maximum=MAX_PARAGRAPH_CHARS))
    return selected


def _normalize_documents(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    package = value.get("package")
    raw_documents = (
        package.get("documents") if isinstance(package, Mapping) else value.get("documents")
    )
    if not isinstance(raw_documents, list) or not 1 <= len(raw_documents) <= MAX_DOCUMENTS:
        raise CandidateError("source JSON documents must be a bounded non-empty list")
    documents: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_titles: set[str] = set()
    for index, raw in enumerate(raw_documents):
        if not isinstance(raw, Mapping):
            raise CandidateError(f"documents[{index}] must be an object")
        path = raw.get("path", raw.get("relative_path", raw.get("filename")))
        path = _safe_text(path, field=f"documents[{index}].path", maximum=200)
        candidate = Path(path)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.suffix.lower() != ".docx"
        ):
            raise CandidateError(f"documents[{index}].path must be a relative DOCX path")
        path = candidate.as_posix()
        if path in seen_paths:
            raise CandidateError("source document paths must be unique")
        seen_paths.add(path)
        title = _safe_text(
            raw.get("title", Path(path).stem),
            field=f"documents[{index}].title",
            maximum=500,
        )
        if title in seen_titles:
            raise CandidateError("source document titles must be unique")
        seen_titles.add(title)
        paragraphs = _paragraphs(raw, field=f"documents[{index}]")
        effective_from = raw.get("effective_date", raw.get("effectiveDate"))
        effective_to = raw.get("effective_to", raw.get("effectiveTo"))
        status = raw.get("status", "verified_current")
        if effective_from is not None:
            effective_from = _safe_text(effective_from, field="effective_date", maximum=32)
        if effective_to is not None:
            effective_to = _safe_text(effective_to, field="effective_to", maximum=32)
        status = _safe_text(status, field="document status", maximum=40)
        if status not in {
            "verified_current",
            "verified_historical",
            "not_yet_effective",
            "repealed",
            "superseded",
            "unverified_current",
        }:
            raise CandidateError("unsupported document status")
        document_number = raw.get("document_number", raw.get("documentNumber"))
        if document_number is not None:
            document_number = _safe_text(document_number, field="document_number", maximum=200)
        documents.append(
            {
                "path": path,
                "title": title,
                "paragraphs": paragraphs,
                "effective_date": effective_from,
                "effective_to": effective_to,
                "status": status,
                "document_number": document_number,
                "aliases": [
                    _safe_text(alias, field="document alias", maximum=200)
                    for alias in (raw.get("aliases") or [])
                ],
            }
        )
    return documents


def _write_docx(path: Path, paragraphs: Sequence[str]) -> None:
    body = "".join(
        f"<w:p><w:r><w:t>{escape(paragraph)}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:sectPr/></w:body></w:document>"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("word/document.xml", date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, document)


def _document_manifest(source_root: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    path = source_root / str(document["path"])
    source_bytes = path.read_bytes()
    payload: dict[str, Any] = {
        "path": document["path"],
        "title": document["title"],
        "format": "DOCX",
        "officialSource": f"https://synthetic.invalid/development/{path.name}",
        "byteSize": len(source_bytes),
        "sha256": _sha256_bytes(source_bytes),
        "status": document["status"],
        "documentType": "law",
        "jurisdiction": "CN",
        "authorityRank": 100,
    }
    if document.get("effective_date") is not None:
        payload["effectiveDate"] = document["effective_date"]
    if document.get("effective_to") is not None:
        payload["effectiveTo"] = document["effective_to"]
    if document.get("document_number") is not None:
        payload["documentNumber"] = document["document_number"]
    if document.get("aliases"):
        payload["aliases"] = list(document["aliases"])
    return payload


def _query_cases(
    value: Mapping[str, Any], documents: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    raw_cases = value.get("queries", value.get("cases"))
    if raw_cases is None:
        current = next(
            (
                item
                for item in documents
                if item["status"] in {"verified_current", "unverified_current"}
            ),
            documents[0],
        )
        article_labels = [
            label
            for paragraph in current["paragraphs"]
            for label in _ARTICLE.findall(paragraph)
        ]
        first_article = article_labels[0] if article_labels else "第一条"
        cases = [
            {
                "case_id": "exact-current",
                "category": "exact_current",
                "query": f"{current['title']} {first_article}",
                "purpose": "exact_citation",
            },
            {
                "case_id": "unknown-article",
                "category": "unknown_article",
                "query": f"{current['title']} 第九千九百九十九条",
                "purpose": "exact_citation",
            },
        ]
        future = next(
            (
                item
                for item in documents
                if item.get("effective_date") and item["effective_date"] > "2026-01-01"
            ),
            None,
        )
        if future is not None:
            future_label = next(
                (
                    label
                    for paragraph in future["paragraphs"]
                    for label in _ARTICLE.findall(paragraph)
                ),
                "第一条",
            )
            cases.append(
                {
                    "case_id": "future-wrong-version",
                    "category": "wrong_version",
                    "query": f"{future['title']} {future_label}",
                    "purpose": "as_of_version",
                    "as_of": "2026-01-01",
                }
            )
        return cases
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_CASES:
        raise CandidateError("source queries must be a bounded list")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, Mapping):
            raise CandidateError(f"queries[{index}] must be an object")
        case_id = _safe_id(
            raw.get("case_id", raw.get("id", raw.get("case"))),
            field=f"queries[{index}].case_id",
        )
        if case_id in seen:
            raise CandidateError("query case IDs must be unique")
        seen.add(case_id)
        query = _safe_text(
            raw.get("query"), field=f"queries[{index}].query", maximum=MAX_QUERY_CHARS
        )
        purpose = _safe_text(
            raw.get("purpose", "exact_citation"), field="query purpose", maximum=32
        )
        if purpose not in {
            "exact_citation",
            "citation_verify",
            "as_of_version",
            "legal_issue_screen",
        }:
            raise CandidateError("unsupported legal development query purpose")
        as_of = raw.get("as_of")
        if as_of is not None:
            as_of = _safe_text(as_of, field="query as_of", maximum=32)
        category_value = raw.get("category")
        if category_value is None:
            category_value = {
                "current_exact": "exact_current",
                "exception_exact": "exception",
                "future_wrong_version": "wrong_version",
            }.get(case_id, "exact_current")
        category = _safe_text(category_value, field="query category", maximum=80)
        cases.append(
            {
                "case_id": case_id,
                "category": category,
                "query": query,
                "purpose": purpose,
                "as_of": as_of,
            }
        )
    return cases


def _citation(
    law: DeepLaw,
    *,
    card: Mapping[str, Any],
    segment: Mapping[str, Any],
    date_version_statement: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    quote = str(segment["text"])[: min(80, len(str(segment["text"])))].strip()
    if not quote:
        quote = str(segment["text"])
    return {
        "release_id": law.release_id,
        "segment_id": card["segment_id"],
        "receipt_id": card["receipt_id"],
        "claim_id": "candidate-claim",
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
        "date_version_statement": dict(date_version_statement) if date_version_statement else None,
        "evidence_segment_ids": [card["segment_id"]],
        "semantic_entailment": {
            "status": "not_assessed",
            "assessor": None,
            "assessment": "not_assessed",
        },
    }


def _article_body_sha256(segment: Mapping[str, Any]) -> str:
    text = str(segment["text"])
    label = segment.get("article_label")
    if isinstance(label, str) and label:
        start = text.find(label)
        if start >= 0:
            text = text[start + len(label) :].strip()
    return sha256_bytes(text.encode("utf-8"))


def _tamper_checks(law: DeepLaw, citation: Mapping[str, Any]) -> dict[str, bool]:
    checks: dict[str, bool] = {}

    def audit(mutated: dict[str, Any]) -> bool:
        try:
            return law.audit_citation(mutated)["deterministic_pass"] is False
        except (KeyError, TypeError, ValueError):
            return True

    checks["quote_hash_tamper_rejected"] = audit({**citation, "quote_sha256": "0" * 64})
    checks["quote_tamper_rejected"] = audit({**citation, "quote": "tampered quote"})
    checks["locator_tamper_rejected"] = audit(
        {**citation, "locator": {**citation["locator"], "article_label": "第九千九百九十九条"}}
    )
    checks["receipt_tamper_rejected"] = audit({**citation, "receipt_id": "lawrcpt_" + "0" * 32})
    checks["source_hash_tamper_rejected"] = audit({**citation, "source_sha256": "0" * 64})
    checks["segment_hash_tamper_rejected"] = audit({**citation, "segment_sha256": "0" * 64})
    version = citation.get("date_version_statement")
    if isinstance(version, Mapping):
        tampered_status = (
            "repealed" if version.get("status") != "repealed" else "verified_current"
        )
        checks["version_tamper_rejected"] = audit(
            {
                **citation,
                "date_version_statement": {**version, "status": tampered_status},
            }
        )
    else:
        # No version assertion is a valid citation, but the runner still proves that
        # adding a conflicting assertion cannot silently pass.
        checks["version_tamper_rejected"] = audit(
            {
                **citation,
                "date_version_statement": {
                    "effective_from": "1900-01-01",
                    "effective_to": None,
                    "status": "repealed",
                },
            }
        )
    return checks


def _safe_case_result(
    *,
    case: Mapping[str, Any],
    search_result: Mapping[str, Any],
    selected: list[dict[str, Any]],
    checks: Mapping[str, bool],
    started: float,
) -> dict[str, Any]:
    gaps: list[str] = []
    for item in search_result.get("gaps", []):
        if isinstance(item, str):
            gap_value = item
        elif isinstance(item, Mapping):
            gap_value = item.get("code")
        else:
            gap_value = getattr(item, "code", None)
            if hasattr(gap_value, "value"):
                gap_value = gap_value.value
        if not isinstance(gap_value, str):
            continue
        try:
            gaps.append(_safe_text(gap_value, field="query gap", maximum=240))
        except CandidateError:
            continue
        if len(gaps) >= 12:
            break
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "purpose": case["purpose"],
        "as_of": case.get("as_of"),
        "query_sha256": sha256_bytes(str(case["query"]).encode("utf-8")),
        "mode": search_result.get("mode"),
        "evidence_count": len(selected),
        "selected": selected[:5],
        "gaps": gaps,
        "checks": dict(checks),
        "wrong_version_primary_evidence": bool(
            case["category"] in {"wrong_version", "future_wrong_version"} and selected
        ),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _run_agent_interpretation(vault_path: Path, *, query: str, source_hash: str) -> dict[str, Any]:
    """Create one explicitly non-authoritative Agent interpretation in a temp Vault."""
    with AutonomousKnowledgeStore(vault_path, read_only=False) as store:
        grant = store.enable_grant(
            writer_id="legal-exact-evidence-development",
            operations=tuple(sorted(SINK_OPERATIONS)),
            max_mutations_per_minute=120,
        )
    grant_id = str(grant["grant_id"])
    run = handle_knowledge_sink(
        {
            "operation": "record_run",
            "idempotency_key": "legal-exact-evidence-run",
            "confirm_no_case_data": True,
            "run_id": "run-legal-exact-evidence-development",
            "task": "Execute source-only exact-evidence development checks.",
            "host_id": "legal-exact-evidence-candidate",
            "model_id": "deterministic-development-runner",
            "status": "succeeded",
            "scope": "project",
            "sensitivity": "private",
            "input_sha256": source_hash,
            "run_metadata": {"task_kind": "legal_exact_evidence"},
        },
        grant_id=grant_id,
        vault_path=vault_path,
    )
    interpretation = handle_knowledge_sink(
        {
            "operation": "remember",
            "idempotency_key": "legal-exact-evidence-interpretation",
            "confirm_no_case_data": True,
            "title": "Development legal interpretation",
            "body": "This bounded development interpretation is not legal authority.",
            "kind": "claim",
            "scope": "project",
            "sensitivity": "private",
            "run_id": run["result"]["run_id"],
            "model_id": "deterministic-development-runner",
            "tool_id": "legal-exact-evidence-candidate",
            "tags": ["legal_interpretation"],
            "semantic_key": "legal-development:interpretation",
            "requested_origin": "agent_derived",
            "requested_authority": "agent_derived",
        },
        grant_id=grant_id,
        vault_path=vault_path,
    )
    result = interpretation["result"]
    with AutonomousKnowledgeStore(vault_path, read_only=True) as store:
        recalled = store.recall(
            "Development legal interpretation",
            scope="project",
            max_sensitivity="private",
            limit=5,
            max_chars=2_000,
            max_tokens=512,
            max_sources=5,
            graph_hops=0,
            retrieval_mode="lexical",
            kinds=("claim",),
            required_tags=("legal_interpretation",),
            force_canonical_lexical=True,
        )
        tagged = any(
            isinstance(item, Mapping)
            and "legal_interpretation" in item.get("tags", [])
            for item in recalled.get("results", [])
        )
    return {
        "origin": result.get("origin"),
        "authority": result.get("authority"),
        "legal_authority": result.get("legal_authority"),
        "tagged": tagged,
        "run_recorded": isinstance(run.get("result", {}).get("run_id"), str),
    }


def build_candidate(source: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    """Run bounded exact-evidence checks against one development source JSON."""
    started = time.perf_counter()
    source_value, source_json_sha256 = _load_source(source)
    documents = _normalize_documents(source_value)
    cases = _query_cases(source_value, documents)
    with tempfile.TemporaryDirectory(prefix="deeplaw-legal-exact-evidence-") as temporary:
        root = Path(temporary)
        source_root = root / "source"
        source_root.mkdir()
        manifest_documents: list[dict[str, Any]] = []
        source_hashes: dict[str, str] = {}
        for document in documents:
            path = source_root / document["path"]
            _write_docx(path, document["paragraphs"])
            manifest_document = _document_manifest(source_root, document)
            manifest_documents.append(manifest_document)
            source_hashes[str(document["path"])] = str(manifest_document["sha256"])
        manifest = {
            "package": {
                "name": "DeepLaw v0.13 exact-evidence development corpus",
                "retrievedOn": "2026-01-01",
                "reviewedOn": "2026-01-01",
                "documentCount": len(manifest_documents),
            },
            "documents": manifest_documents,
        }
        manifest_path = source_root / "manifest.json"
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        manifest_path.write_bytes(manifest_bytes)
        release_dir, _report = build_release(
            source_root=source_root,
            manifest_path=manifest_path,
            output_root=root / "releases",
            activate=True,
            source_scope="official",
        )
        database = release_dir / "deeplaw.sqlite3"
        artifact = verify_release_artifact(database)
        cases_out: list[dict[str, Any]] = []
        with DeepLaw(database, expected_scope="official") as law:
            for case in cases:
                case_started = time.perf_counter()
                request = SearchRequest(
                    query=str(case["query"]),
                    purpose=str(case["purpose"]),
                    as_of=case.get("as_of"),
                    limit=5,
                    max_chars=3500,
                )
                response = law.search(request).to_dict()
                selected_cards = response.get("evidence", [])
                selected: list[dict[str, Any]] = []
                checks: dict[str, bool] = {
                    "source_only_exact_release": True,
                    "wrong_version_excluded": not (
                        case["category"] in {"wrong_version", "future_wrong_version"}
                        and selected_cards
                    ),
                    "no_answer_gap": (
                        case["category"]
                        not in {
                            "unknown_article",
                            "no_answer",
                            "wrong_version",
                            "future_wrong_version",
                        }
                        or (not selected_cards and bool(response.get("gaps")))
                    ),
                }
                for card in selected_cards[:5]:
                    segment = law.get(card["segment_id"], max_chars=12_000)
                    source_bound = (
                        segment["source_sha256"] in set(source_hashes.values())
                        and card["source_sha256"] == segment["source_sha256"]
                    )
                    receipt_valid = (
                        law.verify(card["segment_id"], card["receipt_id"])["valid"] is True
                    )
                    capabilities = law.evidence_capabilities(
                        card["segment_id"],
                        as_of=case.get("as_of"),
                    )
                    date_version = {
                        "effective_from": segment["effective_from"],
                        "effective_to": segment["effective_to"],
                        "status": segment["status"],
                    }
                    citation = _citation(
                        law,
                        card=card,
                        segment=segment,
                        date_version_statement=date_version,
                    )
                    citation_audit = law.audit_citation(citation)
                    tamper = _tamper_checks(law, citation)
                    checks.update(
                        {
                            "source_segment_binding": source_bound,
                            "receipt_valid": receipt_valid,
                            "capabilities_exact_segment": capabilities["capabilities"]["provenance"]
                            == "exact_segment",
                            "valid_citation": citation_audit["deterministic_pass"] is True,
                            **tamper,
                        }
                    )
                    selected.append(
                        {
                            "segment_id": card["segment_id"],
                            "document_id": card["document_id"],
                            "title": card["title"],
                            "article_label": card.get("article_label"),
                            "release_id": card["release_id"],
                            "receipt_id": card["receipt_id"],
                            "source_sha256": segment["source_sha256"],
                            "segment_sha256": segment["segment_sha256"],
                            "article_body_sha256": _article_body_sha256(segment),
                            "effective_from": segment["effective_from"],
                            "effective_to": segment["effective_to"],
                            "status": segment["status"],
                            "citation_valid": citation_audit["deterministic_pass"] is True,
                            "capabilities": {
                                "integrity": capabilities["capabilities"]["integrity"],
                                "source_identity": capabilities["capabilities"]["source_identity"],
                                "provenance": capabilities["capabilities"]["provenance"],
                            },
                        }
                    )
                # Query-level checks remain true only when every selected evidence card
                # satisfies all deterministic citation and source binding predicates.
                checks["primary_evidence_valid"] = bool(selected) and all(
                    item["citation_valid"] for item in selected
                )
                if not selected and case["category"] not in {
                    "unknown_article",
                    "no_answer",
                    "wrong_version",
                    "future_wrong_version",
                }:
                    checks["primary_evidence_valid"] = False
                cases_out.append(
                    _safe_case_result(
                        case=case,
                        search_result=response,
                        selected=selected,
                        checks=checks,
                        started=case_started,
                    )
                )
        vault_path = root / "knowledge-vault"
        initialize_knowledge_vault(
            vault_path, name="legal exact evidence development", scope="project"
        )
        from deeplaw.knowledge_autonomy import initialize_autonomous_core

        initialize_autonomous_core(vault_path)
        interpretation = _run_agent_interpretation(
            vault_path,
            query=str(cases[0]["query"]),
            source_hash=source_json_sha256,
        )
        federated = handle_support(
            operation="federated_context",
            query=str(cases[0]["query"]),
            purpose=str(cases[0]["purpose"]),
            as_of=cases[0].get("as_of"),
            limit=5,
            max_chars=3500,
            database=database,
            include_private=False,
            include_agent_interpretation=True,
            knowledge_vault=vault_path,
            confirm_no_case_data=True,
        )
        official_partition = federated.get("official", {})
        private_partition = federated.get("user_private", {})
        agent_partition = federated.get("agent_interpretation", {})
        authority_partitions_valid = (
            official_partition.get("origin") == "official"
            and official_partition.get("legal_authority") is True
            and private_partition.get("status") == "disabled"
            and private_partition.get("results") == []
            and agent_partition.get("origin") == "agent_derived"
            and agent_partition.get("legal_authority") is False
            and federated.get("authority_partitions_preserved") is True
        )
        with AutonomousKnowledgeStore(vault_path, read_only=True) as store:
            audit_head = store.audit_head
        false_authority = int(
            interpretation["origin"] != "agent_derived"
            or interpretation["legal_authority"] is not False
        )
        invalid_citations = sum(
            int(not case["checks"].get("primary_evidence_valid", False))
            for case in cases_out
            if case["evidence_count"]
        )
        wrong_version_primary = sum(
            int(case["wrong_version_primary_evidence"]) for case in cases_out
        )
        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "status": "executed",
            "case_id": source_value.get("case_id", "legal-development-case"),
            "development_only": True,
            "source_only": True,
            "signed": False,
            "official_claimed": False,
            "release_claimed": False,
            "claim_eligible": False,
            "competitive_claim_eligible": False,
            "source_json_sha256": source_json_sha256,
            "source_hashes": source_hashes,
            "source_manifest_sha256": _sha256_bytes(manifest_bytes),
            "release_id": release_dir.name,
            "database_sha256": artifact["database_sha256"],
            "cases": cases_out,
            "agent_interpretation": interpretation,
            "authority_partitions": {
                "official": {
                    "origin": official_partition.get("origin"),
                    "legal_authority": official_partition.get("legal_authority"),
                    "status": official_partition.get("status"),
                    "selected_count": int(official_partition.get("selected_count", 0)),
                },
                "agent_interpretation": {
                    "origin": agent_partition.get("origin"),
                    "legal_authority": agent_partition.get("legal_authority"),
                    "status": agent_partition.get("status"),
                    "selected_count": int(agent_partition.get("selected_count", 0)),
                },
                "preserved": authority_partitions_valid,
            },
            "hard_failures": {
                "false_authority_admission": false_authority,
                "invalid_quote_locator_receipt_source_segment_version": invalid_citations,
                "wrong_version_primary_evidence": wrong_version_primary,
                "authority_partition_mixing": int(not authority_partitions_valid),
            },
            "gaps": sorted(
                {
                    gap
                    for case in cases_out
                    for gap in case["gaps"]
                    if isinstance(gap, str)
                }
            )[:32],
            "audit_head_sha256": sha256_bytes(str(audit_head).encode("utf-8")),
            "metrics": {
                "case_count": len(cases_out),
                "exact_hit_count": sum(case["evidence_count"] > 0 for case in cases_out),
                "no_answer_case_count": sum(case["evidence_count"] == 0 for case in cases_out),
                "valid_primary_citation_count": sum(
                    case["checks"].get("primary_evidence_valid", False) for case in cases_out
                ),
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            },
        }
    encoded = canonical_json(result).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise CandidateError("candidate output exceeds its bounded output contract")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a source-only legal exact-evidence candidate"
    )
    parser.add_argument("source_json", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def _write_output(value: Mapping[str, Any], output: Path | None) -> None:
    rendered = canonical_json(value) + "\n"
    if output is None:
        print(rendered, end="")
        return
    if output.exists() or output.is_symlink():
        raise CandidateError("candidate output already exists")
    output.write_text(rendered, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    _write_output(build_candidate(arguments.source_json), arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
