from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .util import sha256_bytes

if TYPE_CHECKING:
    from .search import DeepLaw


def _check(passed: bool, passed_reason: str, failed_reason: str) -> dict[str, Any]:
    return {"passed": passed, "reason": passed_reason if passed else failed_reason}


def audit_citation(law: DeepLaw, citation: dict[str, Any]) -> dict[str, Any]:
    """Verify citation identity and bytes without making a semantic-entailment claim."""
    segment_id = citation["segment_id"]
    row = law.connection.execute(
        """
        SELECT segments.*, documents.source_sha256, documents.effective_from,
               documents.effective_to, documents.status
        FROM segments JOIN documents USING(document_id)
        WHERE segments.segment_id = ?
        """,
        (segment_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown segment_id: {segment_id}")
    quote = citation["quote"]
    quote_hash = sha256_bytes(quote.encode("utf-8"))
    locator = citation["locator"]
    expected_locator = {
        "article_label": row["article_label"],
        "page_start": row["page_start"],
        "page_end": row["page_end"],
        "paragraph_start": row["paragraph_start"],
        "paragraph_end": row["paragraph_end"],
    }
    version = citation.get("date_version_statement")
    if version is None:
        date_version_pass = True
        date_version_reason = "no date/version assertion was made"
    else:
        date_version_pass = (
            version.get("effective_from") == row["effective_from"]
            and version.get("effective_to") == row["effective_to"]
            and version.get("status") == row["status"]
        )
        date_version_reason = (
            "date/version metadata exactly matches the immutable release"
            if date_version_pass
            else "date/version metadata does not match the immutable release"
        )
    verification = law.verify(segment_id, citation["receipt_id"])
    mapping = citation["evidence_segment_ids"]
    checks = {
        "release_id": _check(
            citation["release_id"] == law.release_id,
            "release identity matches",
            "release identity mismatch",
        ),
        "segment_id": _check(True, "segment identity exists", "segment identity missing"),
        "quote_exact": _check(
            quote in row["text"],
            "quote is an exact contiguous substring",
            "quote is not present verbatim in the segment",
        ),
        "quote_sha256": _check(
            citation["quote_sha256"] == quote_hash,
            "quote digest matches",
            "quote digest mismatch",
        ),
        "locator": _check(
            locator == expected_locator,
            "locator exactly matches",
            "locator mismatch",
        ),
        "source_sha256": _check(
            citation["source_sha256"] == row["source_sha256"],
            "source digest matches",
            "source digest mismatch",
        ),
        "segment_sha256": _check(
            citation["segment_sha256"] == row["text_sha256"],
            "segment digest matches",
            "segment digest mismatch",
        ),
        "receipt": _check(
            verification.get("valid") is True,
            "receipt verifies",
            f"receipt failed: {verification.get('reason', 'unknown')}",
        ),
        "date_version": _check(
            date_version_pass,
            date_version_reason,
            date_version_reason,
        ),
        "claim_evidence_mapping": _check(
            segment_id in mapping and len(mapping) == len(set(mapping)),
            "claim explicitly maps to this unique evidence segment",
            "claim-to-evidence mapping is missing or contains duplicates",
        ),
    }
    semantic = citation.get("semantic_entailment") or {
        "status": "not_assessed",
        "assessor": None,
        "assessment": "not_assessed",
    }
    if semantic["status"] == "model_assessed" and not semantic.get("assessor"):
        raise ValueError("model_assessed entailment requires an assessor identity")
    if semantic["status"] == "not_assessed":
        semantic = {
            "status": "not_assessed",
            "assessor": None,
            "assessment": "not_assessed",
        }
    return {
        "schema_version": "deeplaw.citation-audit/v1",
        "release_id": law.release_id,
        "segment_id": segment_id,
        "claim_id": citation["claim_id"],
        "checks": checks,
        "deterministic_pass": all(item["passed"] for item in checks.values()),
        "semantic_entailment": semantic,
    }
