"""Deterministic, read-only navigation views for an Authoritative Pack.

The navigator deliberately contains identities, locators, dates, capability
states, warning/gap references and digests only.  It never opens a Pack, reads
source bytes, or creates an interpretation of source prose.  Callers provide
already validated records; this module binds those records into a closed,
reproducible derived view.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from .util import canonical_json, sha256_bytes, stable_id

SCHEMA_VERSION = "deeplaw.authoritative-navigator/v1"
VERIFICATION_SCHEMA_VERSION = "deeplaw.authoritative-navigator-verification/v1"
DISPOSITION_SCHEMA_VERSION = "deeplaw.authoritative-review-disposition/v1"
DISPOSITION_VERIFICATION_SCHEMA_VERSION = "deeplaw.authoritative-review-disposition-verification/v1"

MAX_DOCUMENTS = 100_000
MAX_SEGMENTS = 2_000_000
MAX_WARNINGS = 100_000
MAX_GAPS = 100_000
MAX_ENCODED_BYTES = 64 * 1024 * 1024
MAX_LABEL_LENGTH = 500
MAX_LOCATOR_LENGTH = 2_048
MAX_CODE_LENGTH = 200
MAX_ITEMS_PER_RECORD = 64

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PACK_ID = re.compile(r"^authpack_[0-9a-f]{24}$")
_DOCUMENT_ID = re.compile(r"^doc_[0-9a-f]{24}$")
_SEGMENT_ID = re.compile(r"^seg_[0-9a-f]{24}$")
_RELEASE_ID = re.compile(r"^lawrel_[0-9a-f]{32}$")
_RECEIPT_ID = re.compile(r"^lawrcpt_[0-9a-f]{32}$")
_CATALOG_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,199}$")
_SAFE_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,199}$")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|\s)(?:/(?:Users|home|private|var|tmp|etc|opt|usr|Volumes|Applications|Library|System|workspace|root|srv|mnt|data|dev|proc|sys|run)(?:/|$)|[A-Za-z]:[\\/]|\\\\)"
)

_NAVIGATOR_KEYS = {
    "schema_version",
    "derived_view",
    "read_only",
    "official_prose_generated",
    "authority_changed",
    "legal_authority_decision",
    "binding",
    "document_index",
    "release_timeline",
    "effective_dates",
    "segment_index",
    "definitions",
    "cross_references",
    "review_warnings",
    "evidence_gaps",
    "receipt_drill_down",
    "manifest",
    "navigator_sha256",
}

_SECTION_NAMES = (
    "document_index",
    "release_timeline",
    "effective_dates",
    "segment_index",
    "definitions",
    "cross_references",
    "review_warnings",
    "evidence_gaps",
    "receipt_drill_down",
)

_DOCUMENT_INPUT_KEYS = {
    "document_id",
    "stable_source_id",
    "id",
    "release_id",
    "catalog_id",
    "catalog_sha256",
    "source_sha256",
    "immutable_bytes_sha256",
    "title",
    "label",
    "name",
    "locator",
    "official_source",
    "source_locator",
    "document_number",
    "jurisdiction",
    "document_type",
    "promulgated_on",
    "effective_from",
    "effective_start",
    "effective_to",
    "effective_end",
    "status",
    "lifecycle",
    "capability",
    "capabilities",
}
_SEGMENT_INPUT_KEYS = _DOCUMENT_INPUT_KEYS | {
    "segment_id",
    "receipt_id",
    "segment_sha256",
    "ordinal",
    "part_index",
    "article_label",
    "heading",
    "page_start",
    "page_end",
    "paragraph_start",
    "paragraph_end",
    "warning_labels",
    "extraction_warnings",
    "text",
    "truncated",
    "extraction_method",
    "extraction_configuration",
    "extraction_review_required",
    "temporal_review_required",
}
_SIMPLE_INPUT_KEYS = {
    "id",
    "definition_id",
    "warning_id",
    "gap_id",
    "receipt_id",
    "document_id",
    "segment_id",
    "label",
    "title",
    "heading",
    "name",
    "code",
    "kind",
    "warning_code",
    "gap_code",
    "locator",
    "ref",
    "source_locator",
    "warning_count",
    "required_capability",
    "affected_capability",
}
_CROSS_REFERENCE_INPUT_KEYS = {
    "id",
    "cross_reference_id",
    "reference_id",
    "from_segment_id",
    "source_segment_id",
    "segment_id",
    "to_segment_id",
    "target_segment_id",
    "referenced_segment_id",
    "reference_type",
    "kind",
    "type",
    "label",
    "title",
    "heading",
    "name",
    "locator",
    "ref",
    "release_id",
    "catalog_id",
    "catalog_sha256",
}

_EXPECTED_MATRIX_RECORD_SHA256 = "a81913e1d2f1b82dff08986db8834c67d476037a79cf8a5eb2df57d55f508abf"
_EXPECTED_MATRIX_SOURCE_IDS = frozenset(
    {
        "doc_003bce0e629646f4798dad04",
        "doc_27744b8e4a30bea1d9e3f92f",
        "doc_60224e01894c870874c413df",
        "doc_9364963e345975e871203e53",
        "doc_d63068d170e2015069276833",
    }
)
_EXPECTED_WARNING_COUNT = 32
_EXPECTED_REVIEW_SEGMENT_COUNT = 8
_EXPECTED_MATRIX_SOURCE_COUNT = 28


def _fail(message: str) -> None:
    raise ValueError(message)


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{name} must be an object")
    result = dict(value)
    if len(result) > MAX_ITEMS_PER_RECORD * 4:
        _fail(f"{name} has too many fields")
    return result


def _records(value: Any, name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(f"{name} must be a bounded array")
    result = []
    for item in value:
        result.append(_mapping(item, name))
    return result


def _check_input_keys(record: Mapping[str, Any], allowed: set[str], *, name: str) -> None:
    unknown = set(record) - allowed
    if unknown:
        _fail(f"{name} contains an unknown field")


def _reject_string(value: Any, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        _fail(f"{name} must be a string")
    if len(value) > maximum:
        _fail(f"{name} exceeds its bounded length")
    if any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value):
        _fail(f"{name} contains a control character")
    if _ABSOLUTE_PATH.search(value) or value.startswith("file://"):
        _fail(f"{name} contains an absolute path")
    return value


def _optional_string(
    record: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    name: str,
    maximum: int = MAX_LABEL_LENGTH,
) -> str | None:
    value: Any = None
    found = False
    for key in keys:
        if key in record:
            if found and record[key] != value:
                _fail(f"{name} aliases disagree")
            value = record[key]
            found = True
    if not found or value is None:
        return None
    return _reject_string(value, name=name, maximum=maximum)


def _required_string(
    record: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    name: str,
    maximum: int = MAX_LABEL_LENGTH,
) -> str:
    value = _optional_string(record, keys, name=name, maximum=maximum)
    if value is None or not value:
        _fail(f"{name} is required")
    return value


def _hash(value: Any) -> str:
    try:
        encoded = canonical_json(value).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("input is not canonical JSON") from error
    return sha256_bytes(encoded)


def _required_hash(
    record: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    name: str,
    fallback: str | None = None,
) -> str:
    value = _optional_string(record, keys, name=name, maximum=64)
    if value is None:
        value = fallback
    if value is None or not _SHA256.fullmatch(value):
        _fail(f"{name} must be a lowercase SHA-256 digest")
    return value


def _id(
    record: Mapping[str, Any],
    keys: tuple[str, ...],
    pattern: re.Pattern[str],
    *,
    name: str,
) -> str:
    value = _required_string(record, keys, name=name, maximum=200)
    if not pattern.fullmatch(value):
        _fail(f"{name} has an invalid identity")
    return value


def _date(value: Any, *, name: str, optional: bool = True) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        _fail(f"{name} must be a canonical date")
    _reject_string(value, name=name, maximum=10)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail(f"{name} must be a canonical date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{name} is not a valid date") from error
    if parsed.isoformat() != value:
        _fail(f"{name} must be a canonical date")
    return value


def _ordered_dates(
    start: str | None, end: str | None, *, name: str
) -> tuple[str | None, str | None]:
    start = _date(start, name=f"{name}.from")
    end = _date(end, name=f"{name}.to")
    if start and end and start > end:
        _fail(f"{name} has reversed effective dates")
    return start, end


def _integer(
    value: Any,
    *,
    name: str,
    minimum: int = 0,
    maximum: int | None = None,
    optional: bool = True,
) -> int | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{name} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        _fail(f"{name} is out of bounds")
    return value


def _first(mapping: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = mapping
        found = True
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                found = False
                break
            value = value[key]
        if found and value is not None:
            return value
    return None


def _input_value(
    value: Mapping[str, Any] | None, alias: Mapping[str, Any] | None, name: str
) -> dict[str, Any]:
    if value is not None and alias is not None and dict(value) != dict(alias):
        _fail(f"{name} aliases disagree")
    source = value if value is not None else alias
    return _mapping(source, name)


def _pack_and_release_binding(
    pack: Mapping[str, Any], release: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    pack_id = _first(
        pack,
        ("pack_id",),
        ("pack", "pack_id"),
        ("authoritative_pack", "pack_id"),
    )
    if pack_id is None:
        _fail("pack_id is required")
    pack_id = _reject_string(pack_id, name="pack_id", maximum=200)
    if not _PACK_ID.fullmatch(pack_id):
        _fail("pack_id has an invalid identity")
    catalog_id = _first(
        pack,
        ("catalog_id",),
        ("trust", "catalog_id"),
        ("catalog", "catalog_id"),
        ("authoritative_pack", "catalog_id"),
    )
    catalog_sha256 = _first(
        pack,
        ("catalog_sha256",),
        ("trust", "catalog_sha256"),
        ("catalog", "catalog_sha256"),
        ("authoritative_pack", "catalog_sha256"),
    )
    catalog_sequence = _first(
        pack,
        ("catalog_sequence",),
        ("trust", "sequence"),
        ("catalog", "sequence"),
        ("authoritative_pack", "catalog_sequence"),
    )
    catalog_id = _reject_string(catalog_id, name="catalog_id", maximum=200)
    if not _CATALOG_ID.fullmatch(catalog_id):
        _fail("catalog_id has an invalid identity")
    catalog_sha256 = _required_hash({"value": catalog_sha256}, ("value",), name="catalog_sha256")
    catalog_sequence = _integer(
        catalog_sequence, name="catalog_sequence", minimum=1, optional=False
    )
    release_id = _first(
        release,
        ("release_id",),
        ("release", "release_id"),
        ("authoritative_pack", "target_release_id"),
    )
    if release_id is None:
        release_id = _first(pack, ("release_id",), ("release", "release_id"))
    release_id = _reject_string(release_id, name="release_id", maximum=200)
    if not _RELEASE_ID.fullmatch(release_id):
        _fail("release_id has an invalid identity")
    release_sha256 = _first(
        release,
        ("release_sha256",),
        ("database_sha256",),
        ("release", "release_sha256"),
        ("release", "database_sha256"),
        ("authoritative_pack", "target_database_sha256"),
    )
    if release_sha256 is None:
        release_sha256 = _first(
            pack,
            ("release_sha256",),
            ("release", "release_sha256"),
        )
    release_sha256 = _required_hash({"value": release_sha256}, ("value",), name="release_sha256")
    binding = {
        "pack_id": pack_id,
        "catalog_id": catalog_id,
        "catalog_sha256": catalog_sha256,
        "catalog_sequence": catalog_sequence,
        "release_id": release_id,
        "release_sha256": release_sha256,
        "pack_input_sha256": _hash(pack),
        "release_input_sha256": _hash(release),
    }
    return binding, {
        "release_id": release_id,
        "release_sha256": release_sha256,
        "catalog_id": catalog_id,
        "catalog_sha256": catalog_sha256,
        "catalog_sequence": catalog_sequence,
    }


def _aliases(
    primary: Sequence[Mapping[str, Any]] | None,
    alias: Sequence[Mapping[str, Any]] | None,
    *,
    name: str,
) -> list[dict[str, Any]]:
    if primary is not None and alias is not None and list(primary) != list(alias):
        _fail(f"{name} aliases disagree")
    return _records(primary if primary is not None else alias, name)


def _short_label(record: Mapping[str, Any], *, name: str) -> str:
    value = _optional_string(
        record,
        ("label", "title", "heading", "document_number", "name"),
        name=f"{name}.label",
        maximum=MAX_LABEL_LENGTH,
    )
    return value or "unlabelled"


def _binding_fields(record: Mapping[str, Any], binding: Mapping[str, Any], *, name: str) -> None:
    for field in ("release_id", "catalog_id", "catalog_sha256"):
        value = record.get(field)
        if value is not None and value != binding[field]:
            _fail(f"{name}.{field} does not match pack release binding")


def _capabilities(record: Mapping[str, Any], *, name: str) -> tuple[str, dict[str, Any] | None]:
    raw = record.get("capabilities")
    if raw is None:
        raw = record.get("capability")
    if raw is None:
        # Missing capability metadata is not evidence of exact extraction.
        return "identity_locator_only", None
    if isinstance(raw, str):
        capability = _reject_string(raw, name=f"{name}.capability", maximum=200)
        if not _SAFE_CODE.fullmatch(capability):
            _fail(f"{name}.capability is invalid")
        if capability not in {
            "identity_locator_only",
            "exact_segment",
            "derived",
            "warned",
            "native_reviewed",
            "native_unreviewed",
            "ocr_human_reviewed",
            "ocr_unreviewed",
        }:
            _fail(f"{name}.capability is unsupported")
        return ("exact_segment" if capability == "exact_segment" else "identity_locator_only"), None
    raw = _mapping(raw, f"{name}.capabilities")
    allowed = {
        "schema_version",
        "integrity",
        "source_identity",
        "authority_metadata",
        "temporal",
        "extraction",
        "provenance",
        "temporal_as_of",
        "capability_sha256",
    }
    if set(raw) - allowed:
        _fail(f"{name}.capabilities contains an unknown field")
    result: dict[str, Any] = {}
    for field, value in raw.items():
        if field == "temporal_as_of":
            result[field] = _date(value, name=f"{name}.capabilities.temporal_as_of")
        elif field == "capability_sha256":
            result[field] = _required_hash(
                raw, ("capability_sha256",), name=f"{name}.capabilities.capability_sha256"
            )
        elif isinstance(value, str):
            result[field] = _reject_string(value, name=f"{name}.capabilities.{field}", maximum=200)
        elif isinstance(value, bool):
            result[field] = value
        else:
            _fail(f"{name}.capabilities.{field} has an invalid value")
    required_fields = {
        "schema_version",
        "integrity",
        "source_identity",
        "authority_metadata",
        "temporal",
        "extraction",
        "provenance",
        "temporal_as_of",
        "capability_sha256",
    }
    if set(result) != required_fields:
        # A partial artifact can still identify a source, but cannot prove
        # exact quote/character capability.
        return "identity_locator_only", result
    allowed_values = {
        "integrity": {"verified", "failed"},
        "source_identity": {"signed_official", "reviewed", "declared", "unknown"},
        "authority_metadata": {"verified", "declared", "unknown"},
        "temporal": {"verified_at", "outside", "unknown", "not_evaluated"},
        "extraction": {
            "native_reviewed",
            "native_unreviewed",
            "ocr_human_reviewed",
            "ocr_unreviewed",
            "warned",
        },
        "provenance": {"exact_segment", "derived"},
    }
    for field, values in allowed_values.items():
        if result[field] not in values:
            _fail(f"{name}.capabilities.{field} is unsupported")
    if "temporal" in result:
        temporal = result["temporal"]
        if temporal == "verified_at" and not result.get("temporal_as_of"):
            _fail(f"{name}.capabilities.temporal_as_of is required")
        if temporal != "verified_at" and result.get("temporal_as_of") is not None:
            _fail(f"{name}.capabilities.temporal_as_of is not applicable")
    if (
        "schema_version" in result
        and result["schema_version"] != "deeplaw.evidence-capabilities/v1"
    ):
        _fail(f"{name}.capabilities schema is unsupported")
    digest_body = dict(result)
    capability_digest = digest_body.pop("capability_sha256")
    if capability_digest != _hash(digest_body):
        _fail(f"{name}.capabilities digest mismatch")
    exact = (
        result["integrity"] == "verified"
        and result["source_identity"] == "signed_official"
        and result["authority_metadata"] == "verified"
        and result["temporal"] == "verified_at"
        and result["extraction"] in {"native_reviewed", "ocr_human_reviewed"}
        and result["provenance"] == "exact_segment"
    )
    capability = "exact_segment" if exact else "identity_locator_only"
    return capability, result


def _document_index(
    records: list[dict[str, Any]], binding: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if len(records) > MAX_DOCUMENTS:
        _fail("document limit exceeded")
    output: list[dict[str, Any]] = []
    digests: dict[str, str] = {}
    seen: set[str] = set()
    for record in records:
        _check_input_keys(record, _DOCUMENT_INPUT_KEYS, name="document")
        document_id = _id(
            record, ("document_id", "stable_source_id", "id"), _DOCUMENT_ID, name="document_id"
        )
        if document_id in seen:
            _fail("duplicate document identity")
        seen.add(document_id)
        _binding_fields(record, binding, name=f"document[{document_id}]")
        source_sha256 = _required_hash(
            record,
            ("source_sha256", "immutable_bytes_sha256"),
            name=f"document[{document_id}].source_sha256",
        )
        effective_from, effective_to = _ordered_dates(
            _optional_string(
                record, ("effective_from", "effective_start"), name="effective_from", maximum=10
            ),
            _optional_string(
                record, ("effective_to", "effective_end"), name="effective_to", maximum=10
            ),
            name=f"document[{document_id}].effective",
        )
        label = _short_label(record, name=f"document[{document_id}]")
        locator = _optional_string(
            record,
            ("locator", "official_source", "source_locator"),
            name=f"document[{document_id}].locator",
            maximum=MAX_LOCATOR_LENGTH,
        )
        capability, capabilities = _capabilities(record, name=f"document[{document_id}]")
        item: dict[str, Any] = {
            "document_id": document_id,
            "release_id": binding["release_id"],
            "catalog_id": binding["catalog_id"],
            "catalog_sha256": binding["catalog_sha256"],
            "source_sha256": source_sha256,
            "label": label,
            "locator": locator,
            "document_number": _optional_string(
                record,
                ("document_number",),
                name=f"document[{document_id}].document_number",
                maximum=200,
            ),
            "jurisdiction": _optional_string(
                record, ("jurisdiction",), name=f"document[{document_id}].jurisdiction", maximum=64
            ),
            "document_type": _optional_string(
                record,
                ("document_type",),
                name=f"document[{document_id}].document_type",
                maximum=64,
            ),
            "promulgated_on": _date(
                record.get("promulgated_on"), name=f"document[{document_id}].promulgated_on"
            ),
            "effective_from": effective_from,
            "effective_to": effective_to,
            "status": _optional_string(
                record, ("status", "lifecycle"), name=f"document[{document_id}].status", maximum=64
            ),
            "capability": capability,
            "capabilities": capabilities,
            "warning_refs": [],
            "gap_refs": [],
            "segment_ids": [],
            "input_sha256": _hash(record),
        }
        output.append(item)
        digests[document_id] = item["input_sha256"]
    output.sort(key=lambda item: item["document_id"])
    return output, digests


def _segment_index(
    records: list[dict[str, Any]],
    binding: Mapping[str, Any],
    documents: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if len(records) > MAX_SEGMENTS:
        _fail("segment limit exceeded")
    output: list[dict[str, Any]] = []
    digests: dict[str, str] = {}
    seen: set[str] = set()
    for record in records:
        _check_input_keys(record, _SEGMENT_INPUT_KEYS, name="segment")
        segment_id = _id(record, ("segment_id", "id"), _SEGMENT_ID, name="segment_id")
        if segment_id in seen:
            _fail("duplicate segment identity")
        seen.add(segment_id)
        document_id = _id(
            record, ("document_id",), _DOCUMENT_ID, name=f"segment[{segment_id}].document_id"
        )
        if document_id not in documents:
            _fail("segment references an unknown document")
        _binding_fields(record, binding, name=f"segment[{segment_id}]")
        source_sha256 = _required_hash(
            record,
            ("source_sha256", "immutable_bytes_sha256"),
            name=f"segment[{segment_id}].source_sha256",
        )
        segment_sha256 = _required_hash(
            record, ("segment_sha256",), name=f"segment[{segment_id}].segment_sha256"
        )
        receipt_id = _optional_string(
            record, ("receipt_id",), name=f"segment[{segment_id}].receipt_id", maximum=200
        )
        if receipt_id is not None and not _RECEIPT_ID.fullmatch(receipt_id):
            _fail("segment receipt_id has an invalid identity")
        effective_from, effective_to = _ordered_dates(
            _optional_string(
                record,
                ("effective_from", "effective_start"),
                name=f"segment[{segment_id}].effective_from",
                maximum=10,
            ),
            _optional_string(
                record,
                ("effective_to", "effective_end"),
                name=f"segment[{segment_id}].effective_to",
                maximum=10,
            ),
            name=f"segment[{segment_id}].effective",
        )
        capability, capabilities = _capabilities(record, name=f"segment[{segment_id}]")
        warning_labels: list[str] = []
        raw_extraction_warnings = record.get("extraction_warnings")
        if raw_extraction_warnings is not None:
            if isinstance(raw_extraction_warnings, (str, bytes, bytearray)) or not isinstance(
                raw_extraction_warnings, Sequence
            ):
                _fail(f"segment[{segment_id}].extraction_warnings must be an array")
            for warning in raw_extraction_warnings:
                if isinstance(warning, Mapping):
                    warning_labels.append(
                        _short_label(warning, name=f"segment[{segment_id}].warning")
                    )
                else:
                    warning_labels.append(
                        _reject_string(
                            warning, name=f"segment[{segment_id}].warning", maximum=MAX_CODE_LENGTH
                        )
                    )
        raw_warning_labels = record.get("warning_labels")
        if raw_warning_labels is not None:
            if isinstance(raw_warning_labels, (str, bytes, bytearray)) or not isinstance(
                raw_warning_labels, Sequence
            ):
                _fail("segment warning_labels must be an array")
            for warning in raw_warning_labels:
                warning_labels.append(
                    _reject_string(
                        warning, name=f"segment[{segment_id}].warning", maximum=MAX_CODE_LENGTH
                    )
                )
        warning_labels = sorted(set(warning_labels))
        if warning_labels and capability == "exact_segment":
            capability = "identity_locator_only"
        locator = {
            "ordinal": _integer(
                record.get("ordinal"), name=f"segment[{segment_id}].ordinal", minimum=1
            ),
            "part_index": _integer(
                record.get("part_index"), name=f"segment[{segment_id}].part_index", minimum=1
            ),
            "article_label": _optional_string(
                record, ("article_label",), name=f"segment[{segment_id}].article_label", maximum=100
            ),
            "heading": _optional_string(
                record,
                ("heading",),
                name=f"segment[{segment_id}].heading",
                maximum=MAX_LABEL_LENGTH,
            ),
            "page_start": _integer(
                record.get("page_start"), name=f"segment[{segment_id}].page_start", minimum=1
            ),
            "page_end": _integer(
                record.get("page_end"), name=f"segment[{segment_id}].page_end", minimum=1
            ),
            "paragraph_start": _integer(
                record.get("paragraph_start"),
                name=f"segment[{segment_id}].paragraph_start",
                minimum=1,
            ),
            "paragraph_end": _integer(
                record.get("paragraph_end"), name=f"segment[{segment_id}].paragraph_end", minimum=1
            ),
        }
        item: dict[str, Any] = {
            "segment_id": segment_id,
            "document_id": document_id,
            "release_id": binding["release_id"],
            "catalog_id": binding["catalog_id"],
            "catalog_sha256": binding["catalog_sha256"],
            "receipt_id": receipt_id,
            "source_sha256": source_sha256,
            "segment_sha256": segment_sha256,
            "label": _short_label(record, name=f"segment[{segment_id}]"),
            "locator": locator,
            "promulgated_on": _date(
                record.get("promulgated_on"), name=f"segment[{segment_id}].promulgated_on"
            ),
            "effective_from": effective_from,
            "effective_to": effective_to,
            "status": _optional_string(
                record, ("status",), name=f"segment[{segment_id}].status", maximum=64
            ),
            "capability": capability,
            "capabilities": capabilities,
            "warning_labels": warning_labels,
            "warning_refs": [],
            "gap_refs": [],
            "input_sha256": _hash(record),
        }
        output.append(item)
        digests[segment_id] = item["input_sha256"]
    output.sort(
        key=lambda item: (item["document_id"], item["locator"]["ordinal"] or 0, item["segment_id"])
    )
    return output, digests


def _release_timeline(
    records: list[dict[str, Any]], binding: Mapping[str, Any], release: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    if not records:
        records = [dict(release)]
    output: list[dict[str, Any]] = []
    digests: list[str] = []
    seen: set[str] = set()
    for record in records:
        release_id = _id(record, ("release_id",), _RELEASE_ID, name="timeline.release_id")
        if release_id in seen:
            _fail("duplicate release timeline identity")
        seen.add(release_id)
        release_sha256 = _required_hash(
            record,
            ("release_sha256", "database_sha256"),
            name=f"timeline[{release_id}].release_sha256",
            fallback=binding["release_sha256"] if release_id == binding["release_id"] else None,
        )
        catalog_id = (
            _optional_string(
                record, ("catalog_id",), name=f"timeline[{release_id}].catalog_id", maximum=200
            )
            or binding["catalog_id"]
        )
        if catalog_id != binding["catalog_id"]:
            _fail("release timeline catalog mismatch")
        catalog_sha256 = (
            _optional_string(
                record,
                ("catalog_sha256",),
                name=f"timeline[{release_id}].catalog_sha256",
                maximum=64,
            )
            or binding["catalog_sha256"]
        )
        if catalog_sha256 != binding["catalog_sha256"]:
            _fail("release timeline catalog digest mismatch")
        published_on = _date(
            record.get("published_on"), name=f"timeline[{release_id}].published_on"
        )
        effective_from, effective_to = _ordered_dates(
            _optional_string(
                record,
                ("effective_from",),
                name=f"timeline[{release_id}].effective_from",
                maximum=10,
            ),
            _optional_string(
                record, ("effective_to",), name=f"timeline[{release_id}].effective_to", maximum=10
            ),
            name=f"timeline[{release_id}].effective",
        )
        item = {
            "release_id": release_id,
            "release_sha256": release_sha256,
            "catalog_id": catalog_id,
            "catalog_sha256": catalog_sha256,
            "catalog_sequence": _integer(
                record.get("catalog_sequence"),
                name=f"timeline[{release_id}].catalog_sequence",
                minimum=1,
            )
            or binding["catalog_sequence"],
            "version": _optional_string(
                record,
                ("version", "version_label"),
                name=f"timeline[{release_id}].version",
                maximum=100,
            ),
            "published_on": published_on,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "previous_release_id": _optional_string(
                record,
                ("previous_release_id",),
                name=f"timeline[{release_id}].previous_release_id",
                maximum=200,
            ),
            "label": _short_label(record, name=f"timeline[{release_id}]"),
            "input_sha256": _hash(record),
        }
        output.append(item)
        digests.append(item["input_sha256"])
    output.sort(key=lambda item: (item["published_on"] or "", item["release_id"]))
    return output, sorted(digests)


def _simple_ref_records(
    records: list[dict[str, Any]],
    *,
    kind: str,
    binding: Mapping[str, Any],
    documents: Mapping[str, dict[str, Any]],
    segments: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    output: list[dict[str, Any]] = []
    digests: list[str] = []
    seen: set[str] = set()
    pattern = _SAFE_CODE
    for record in records:
        _check_input_keys(record, _SIMPLE_INPUT_KEYS, name=kind)
        identifier_key = {
            "definitions": ("definition_id", "id"),
            "review_warnings": ("warning_id", "id"),
            "evidence_gaps": ("gap_id", "id"),
            "receipt_drill_down": ("receipt_id", "id"),
        }[kind]
        if kind == "receipt_drill_down":
            identifier = _id(record, identifier_key, _RECEIPT_ID, name=f"{kind}.receipt_id")
        else:
            raw_identifier = _optional_string(
                record, identifier_key, name=f"{kind}.id", maximum=200
            )
            identifier = raw_identifier or stable_id(f"auth{kind[:3]}", _hash(record))
            if not pattern.fullmatch(identifier):
                _fail(f"{kind} identity is invalid")
        if identifier in seen:
            _fail(f"duplicate {kind} identity")
        seen.add(identifier)
        _binding_fields(record, binding, name=f"{kind}[{identifier}]")
        document_id = _optional_string(
            record, ("document_id",), name=f"{kind}[{identifier}].document_id", maximum=200
        )
        segment_id = _optional_string(
            record, ("segment_id",), name=f"{kind}[{identifier}].segment_id", maximum=200
        )
        if segment_id is not None:
            if not _SEGMENT_ID.fullmatch(segment_id) or segment_id not in segments:
                _fail(f"{kind} references an unknown segment")
            if document_id is not None and document_id != segments[segment_id]["document_id"]:
                _fail(f"{kind} document/segment reference mismatch")
            document_id = segments[segment_id]["document_id"]
        if document_id is not None and (
            not _DOCUMENT_ID.fullmatch(document_id) or document_id not in documents
        ):
            _fail(f"{kind} references an unknown document")
        label = _short_label(record, name=f"{kind}[{identifier}]")
        if kind == "definitions" and segment_id is None:
            _fail("definitions require an exact segment reference")
        if (
            kind in ("review_warnings", "evidence_gaps")
            and segment_id is None
            and document_id is None
        ):
            _fail(f"{kind} require a document or segment reference")
        item: dict[str, Any] = {
            "id": identifier,
            "document_id": document_id,
            "segment_id": segment_id,
            "release_id": binding["release_id"],
            "catalog_id": binding["catalog_id"],
            "catalog_sha256": binding["catalog_sha256"],
            "label": label,
            "code": _optional_string(
                record,
                ("code", "kind", "warning_code", "gap_code"),
                name=f"{kind}[{identifier}].code",
                maximum=MAX_CODE_LENGTH,
            ),
            "locator": _optional_string(
                record,
                ("locator", "ref", "source_locator"),
                name=f"{kind}[{identifier}].locator",
                maximum=MAX_LOCATOR_LENGTH,
            ),
            "warning_count": _integer(
                record.get("warning_count"),
                name=f"{kind}[{identifier}].warning_count",
                minimum=0,
                maximum=MAX_WARNINGS,
            ),
            "required_capability": _optional_string(
                record,
                ("required_capability", "affected_capability"),
                name=f"{kind}[{identifier}].required_capability",
                maximum=200,
            ),
            "input_sha256": _hash(record),
        }
        if kind == "receipt_drill_down":
            item["receipt_id"] = identifier
        output.append(item)
        digests.append(item["input_sha256"])
    output.sort(key=lambda item: item["id"])
    return output, sorted(digests)


def _cross_references(
    records: list[dict[str, Any]],
    binding: Mapping[str, Any],
    segments: Mapping[str, dict[str, Any]],
    documents: Mapping[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    output: list[dict[str, Any]] = []
    digests: list[str] = []
    seen: set[str] = set()
    for record in records:
        _check_input_keys(record, _CROSS_REFERENCE_INPUT_KEYS, name="cross_reference")
        raw_id = _optional_string(
            record,
            ("cross_reference_id", "reference_id", "id"),
            name="cross_references.id",
            maximum=200,
        )
        identifier = raw_id or stable_id("authxref", _hash(record))
        if not _SAFE_CODE.fullmatch(identifier) or identifier in seen:
            _fail("duplicate or invalid cross-reference identity")
        seen.add(identifier)
        source_segment = _required_string(
            record,
            ("from_segment_id", "source_segment_id", "segment_id"),
            name=f"cross_reference[{identifier}].from_segment_id",
            maximum=200,
        )
        target_segment = _required_string(
            record,
            ("to_segment_id", "target_segment_id", "referenced_segment_id"),
            name=f"cross_reference[{identifier}].to_segment_id",
            maximum=200,
        )
        if not _SEGMENT_ID.fullmatch(source_segment) or source_segment not in segments:
            _fail("cross-reference source is dangling")
        if not _SEGMENT_ID.fullmatch(target_segment) or target_segment not in segments:
            _fail("cross-reference target is dangling")
        _binding_fields(record, binding, name=f"cross_reference[{identifier}]")
        output.append(
            {
                "cross_reference_id": identifier,
                "from_segment_id": source_segment,
                "to_segment_id": target_segment,
                "from_document_id": segments[source_segment]["document_id"],
                "to_document_id": segments[target_segment]["document_id"],
                "release_id": binding["release_id"],
                "catalog_id": binding["catalog_id"],
                "catalog_sha256": binding["catalog_sha256"],
                "reference_type": _optional_string(
                    record,
                    ("reference_type", "kind", "type"),
                    name=f"cross_reference[{identifier}].reference_type",
                    maximum=100,
                ),
                "label": _short_label(record, name=f"cross_reference[{identifier}]"),
                "locator": _optional_string(
                    record,
                    ("locator", "ref"),
                    name=f"cross_reference[{identifier}].locator",
                    maximum=MAX_LOCATOR_LENGTH,
                ),
                "input_sha256": _hash(record),
            }
        )
        digests.append(output[-1]["input_sha256"])
    output.sort(key=lambda item: item["cross_reference_id"])
    return output, sorted(digests)


def _effective_dates(
    documents: Sequence[Mapping[str, Any]], segments: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for document in documents:
        if document["effective_from"] is None and document["effective_to"] is None:
            continue
        output.append(
            {
                "kind": "document",
                "document_id": document["document_id"],
                "segment_id": None,
                "release_id": document["release_id"],
                "effective_from": document["effective_from"],
                "effective_to": document["effective_to"],
                "input_sha256": document["input_sha256"],
            }
        )
    for segment in segments:
        if segment["effective_from"] is None and segment["effective_to"] is None:
            continue
        output.append(
            {
                "kind": "segment",
                "document_id": segment["document_id"],
                "segment_id": segment["segment_id"],
                "release_id": segment["release_id"],
                "effective_from": segment["effective_from"],
                "effective_to": segment["effective_to"],
                "input_sha256": segment["input_sha256"],
            }
        )
    output.sort(
        key=lambda item: (
            item["effective_from"] or "",
            item["kind"],
            item["document_id"],
            item["segment_id"] or "",
        )
    )
    return output


def _attach_references(
    documents: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> None:
    doc_lookup = {item["document_id"]: item for item in documents}
    seg_lookup = {item["segment_id"]: item for item in segments}
    for item in warnings:
        if item["segment_id"]:
            seg_lookup[item["segment_id"]]["warning_refs"].append(item["id"])
            doc_lookup[item["document_id"]]["warning_refs"].append(item["id"])
            seg_lookup[item["segment_id"]]["capability"] = "identity_locator_only"
            doc_lookup[item["document_id"]]["capability"] = "identity_locator_only"
        elif item["document_id"]:
            doc_lookup[item["document_id"]]["warning_refs"].append(item["id"])
            doc_lookup[item["document_id"]]["capability"] = "identity_locator_only"
    for item in gaps:
        if item["segment_id"]:
            seg_lookup[item["segment_id"]]["gap_refs"].append(item["id"])
            doc_lookup[item["document_id"]]["gap_refs"].append(item["id"])
        elif item["document_id"]:
            doc_lookup[item["document_id"]]["gap_refs"].append(item["id"])
    for segment in segments:
        doc_lookup[segment["document_id"]]["segment_ids"].append(segment["segment_id"])
    for item in documents:
        for key in ("warning_refs", "gap_refs", "segment_ids"):
            item[key] = sorted(set(item[key]))
    for item in segments:
        for key in ("warning_refs", "gap_refs"):
            item[key] = sorted(set(item[key]))


def _ensure_limits(value: Mapping[str, Any]) -> None:
    if len(value["document_index"]) > MAX_DOCUMENTS:
        _fail("document limit exceeded")
    if len(value["segment_index"]) > MAX_SEGMENTS:
        _fail("segment limit exceeded")
    if len(value["review_warnings"]) > MAX_WARNINGS:
        _fail("warning limit exceeded")
    if len(value["evidence_gaps"]) > MAX_GAPS:
        _fail("gap limit exceeded")
    if len(canonical_json(value).encode("utf-8")) > MAX_ENCODED_BYTES:
        _fail("navigator encoded size limit exceeded")


def _scan_safe_strings(value: Any, path: str = "navigator") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            _scan_safe_strings(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan_safe_strings(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        maximum = MAX_LOCATOR_LENGTH if path.endswith(".locator") else MAX_LABEL_LENGTH
        _reject_string(value, name=path, maximum=maximum)


def _section_digests(value: Mapping[str, Any]) -> dict[str, str]:
    return {name: _hash(value[name]) for name in _SECTION_NAMES}


def _navigator_body(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("navigator_sha256", None)
    return body


def build_authoritative_navigator(
    pack: Mapping[str, Any] | None = None,
    release: Mapping[str, Any] | None = None,
    documents: Sequence[Mapping[str, Any]] | None = None,
    segments: Sequence[Mapping[str, Any]] | None = None,
    *,
    pack_identity: Mapping[str, Any] | None = None,
    release_identity: Mapping[str, Any] | None = None,
    document_records: Sequence[Mapping[str, Any]] | None = None,
    segment_records: Sequence[Mapping[str, Any]] | None = None,
    release_timeline: Sequence[Mapping[str, Any]] | None = None,
    release_records: Sequence[Mapping[str, Any]] | None = None,
    definitions: Sequence[Mapping[str, Any]] | None = None,
    definition_records: Sequence[Mapping[str, Any]] | None = None,
    cross_references: Sequence[Mapping[str, Any]] | None = None,
    cross_reference_records: Sequence[Mapping[str, Any]] | None = None,
    review_warnings: Sequence[Mapping[str, Any]] | None = None,
    warning_records: Sequence[Mapping[str, Any]] | None = None,
    evidence_gaps: Sequence[Mapping[str, Any]] | None = None,
    gap_records: Sequence[Mapping[str, Any]] | None = None,
    receipts: Sequence[Mapping[str, Any]] | None = None,
    receipt_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a closed Authoritative Navigator from caller-validated records.

    Positional arguments are kept intentionally small for the common path;
    keyword aliases make the boundary explicit for callers that call their
    records ``*_records`` or identities ``*_identity``.
    """

    pack_value = _input_value(pack, pack_identity, "pack")
    release_value = _input_value(release, release_identity, "release")
    binding, _release_binding = _pack_and_release_binding(pack_value, release_value)
    doc_records = _aliases(documents, document_records, name="documents")
    seg_records = _aliases(segments, segment_records, name="segments")
    docs, doc_digests = _document_index(doc_records, binding)
    doc_lookup = {item["document_id"]: item for item in docs}
    segs, seg_digests = _segment_index(seg_records, binding, doc_lookup)
    seg_lookup = {item["segment_id"]: item for item in segs}
    timeline_records = _aliases(release_timeline, release_records, name="release_timeline")
    timeline, timeline_digests = _release_timeline(timeline_records, binding, release_value)
    definition_records_value = _aliases(definitions, definition_records, name="definitions")
    cross_records_value = _aliases(
        cross_references, cross_reference_records, name="cross_references"
    )
    warning_records_value = _aliases(review_warnings, warning_records, name="review_warnings")
    gap_records_value = _aliases(evidence_gaps, gap_records, name="evidence_gaps")
    receipt_records_value = _aliases(receipts, receipt_records, name="receipt_drill_down")
    warning_keys = {
        (record.get("segment_id"), record.get("label"))
        for record in warning_records_value
        if isinstance(record, Mapping)
    }
    for segment in segs:
        for warning_label in segment["warning_labels"]:
            key = (segment["segment_id"], warning_label)
            if key not in warning_keys:
                warning_records_value.append(
                    {
                        "warning_id": stable_id("authwarn", segment["segment_id"], warning_label),
                        "segment_id": segment["segment_id"],
                        "label": warning_label,
                        "warning_count": 1,
                    }
                )
                warning_keys.add(key)
    definition_items, definition_digests = _simple_ref_records(
        definition_records_value,
        kind="definitions",
        binding=binding,
        documents=doc_lookup,
        segments=seg_lookup,
    )
    cross_items, cross_digests = _cross_references(
        cross_records_value, binding, seg_lookup, doc_lookup
    )
    warning_items, warning_digests = _simple_ref_records(
        warning_records_value,
        kind="review_warnings",
        binding=binding,
        documents=doc_lookup,
        segments=seg_lookup,
    )
    # A warning without an explicit gap still receives a bounded, non-prose
    # gap so that an unreviewed capability cannot be mistaken for exact quote
    # support.
    existing_gap_keys = {(item["document_id"], item["segment_id"]) for item in gap_records_value}
    for warning in warning_items:
        key = (warning["document_id"], warning["segment_id"])
        if key not in existing_gap_keys:
            gap_records_value.append(
                {
                    "gap_id": stable_id("authgap", warning["id"], warning["input_sha256"]),
                    "document_id": warning["document_id"],
                    "segment_id": warning["segment_id"],
                    "code": "review_pending",
                    "label": "independent_review_unavailable",
                    "required_capability": "identity_locator_only",
                }
            )
            existing_gap_keys.add(key)
    gap_items, gap_digests = _simple_ref_records(
        gap_records_value,
        kind="evidence_gaps",
        binding=binding,
        documents=doc_lookup,
        segments=seg_lookup,
    )
    receipt_ids = {
        record.get("receipt_id") for record in receipt_records_value if isinstance(record, Mapping)
    }
    for segment in segs:
        receipt_id = segment["receipt_id"]
        if receipt_id is not None and receipt_id not in receipt_ids:
            receipt_records_value.append(
                {
                    "receipt_id": receipt_id,
                    "segment_id": segment["segment_id"],
                    "label": "segment_receipt",
                }
            )
            receipt_ids.add(receipt_id)
    receipt_items, receipt_digests = _simple_ref_records(
        receipt_records_value,
        kind="receipt_drill_down",
        binding=binding,
        documents=doc_lookup,
        segments=seg_lookup,
    )
    _attach_references(docs, segs, warning_items, gap_items)
    warning_by_segment = {item["segment_id"] for item in warning_items if item["segment_id"]}
    gap_by_segment = {item["segment_id"] for item in gap_items if item["segment_id"]}
    for segment in segs:
        if segment["warning_refs"] or segment["segment_id"] in warning_by_segment:
            if segment["capability"] not in {"identity_locator_only", "warned", "ocr_unreviewed"}:
                segment["capability"] = "identity_locator_only"
            if segment["segment_id"] not in gap_by_segment:
                _fail("review warning is missing an evidence gap")
    effective_dates = _effective_dates(docs, segs)
    sections: dict[str, Any] = {
        "document_index": docs,
        "release_timeline": timeline,
        "effective_dates": effective_dates,
        "segment_index": segs,
        "definitions": definition_items,
        "cross_references": cross_items,
        "review_warnings": warning_items,
        "evidence_gaps": gap_items,
        "receipt_drill_down": receipt_items,
    }
    input_digests = {
        "pack": [binding["pack_input_sha256"]],
        "release": [binding["release_input_sha256"]],
        "documents": sorted(doc_digests.values()),
        "segments": sorted(seg_digests.values()),
        "release_timeline": timeline_digests,
        "definitions": sorted(definition_digests),
        "cross_references": sorted(cross_digests),
        "review_warnings": sorted(warning_digests),
        "evidence_gaps": sorted(gap_digests),
        "receipt_drill_down": sorted(receipt_digests),
    }
    manifest = {
        "binding": dict(binding),
        "input_digests": input_digests,
        "section_digests": _section_digests(sections),
        "counts": {name: len(sections[name]) for name in _SECTION_NAMES},
        "limits": {
            "document_max": MAX_DOCUMENTS,
            "segment_max": MAX_SEGMENTS,
            "warning_max": MAX_WARNINGS,
            "gap_max": MAX_GAPS,
            "encoded_max_bytes": MAX_ENCODED_BYTES,
        },
    }
    value: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "derived_view": True,
        "read_only": True,
        "official_prose_generated": False,
        "authority_changed": False,
        "legal_authority_decision": False,
        "binding": dict(binding),
        **sections,
        "manifest": manifest,
    }
    _ensure_limits(value)
    value["navigator_sha256"] = _hash(_navigator_body(value))
    if len(canonical_json(value).encode("utf-8")) > MAX_ENCODED_BYTES:
        _fail("navigator encoded size limit exceeded")
    return value


def _verify_error(value: Any, reason: str) -> dict[str, Any]:
    binding = value.get("binding") if isinstance(value, Mapping) else None
    if not isinstance(binding, Mapping):
        binding = {}
    pack_id = binding.get("pack_id")
    catalog_id = binding.get("catalog_id")
    release_id = binding.get("release_id")
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "valid": False,
        "reason": reason,
        "navigator_sha256": value.get("navigator_sha256") if isinstance(value, Mapping) else None,
        "pack_id": pack_id if isinstance(pack_id, str) and _PACK_ID.fullmatch(pack_id) else None,
        "catalog_id": catalog_id
        if isinstance(catalog_id, str) and _CATALOG_ID.fullmatch(catalog_id)
        else None,
        "release_id": release_id
        if isinstance(release_id, str) and _RELEASE_ID.fullmatch(release_id)
        else None,
    }


def _verify_section_shape(value: Mapping[str, Any]) -> None:
    if set(value) != _NAVIGATOR_KEYS:
        _fail("navigator has unknown or missing fields")
    if value.get("schema_version") != SCHEMA_VERSION:
        _fail("unsupported navigator schema")
    for flag in ("derived_view", "read_only"):
        if value.get(flag) is not True:
            _fail(f"navigator {flag} flag is invalid")
    for flag in ("official_prose_generated", "authority_changed", "legal_authority_decision"):
        if value.get(flag) is not False:
            _fail(f"navigator {flag} flag is invalid")
    for section in _SECTION_NAMES:
        if not isinstance(value.get(section), list):
            _fail(f"navigator section {section} must be an array")
    _mapping(value.get("binding"), "navigator.binding")
    manifest = _mapping(value.get("manifest"), "navigator.manifest")
    if set(manifest) != {"binding", "input_digests", "section_digests", "counts", "limits"}:
        _fail("navigator manifest has unknown or missing fields")
    if dict(manifest["binding"]) != dict(value["binding"]):
        _fail("navigator manifest binding mismatch")
    if not isinstance(value.get("navigator_sha256"), str) or not _SHA256.fullmatch(
        value["navigator_sha256"]
    ):
        _fail("navigator digest is invalid")
    _scan_safe_strings(value)
    _ensure_limits(value)


def _verify_binding(binding: Mapping[str, Any]) -> None:
    if set(binding) != {
        "pack_id",
        "catalog_id",
        "catalog_sha256",
        "catalog_sequence",
        "release_id",
        "release_sha256",
        "pack_input_sha256",
        "release_input_sha256",
    }:
        _fail("navigator binding has unknown or missing fields")
    if not _PACK_ID.fullmatch(str(binding["pack_id"])):
        _fail("navigator pack identity is invalid")
    if not _CATALOG_ID.fullmatch(str(binding["catalog_id"])):
        _fail("navigator catalog identity is invalid")
    if not _SHA256.fullmatch(str(binding["catalog_sha256"])) or not _SHA256.fullmatch(
        str(binding["release_sha256"])
    ):
        _fail("navigator binding digest is invalid")
    if not _RELEASE_ID.fullmatch(str(binding["release_id"])):
        _fail("navigator release identity is invalid")
    _integer(
        binding["catalog_sequence"], name="navigator catalog_sequence", minimum=1, optional=False
    )
    for field in ("pack_input_sha256", "release_input_sha256"):
        if not _SHA256.fullmatch(str(binding[field])):
            _fail("navigator input digest is invalid")


def _verify_navigator_items(value: Mapping[str, Any]) -> None:
    binding = value["binding"]
    docs = value["document_index"]
    segs = value["segment_index"]
    doc_ids: set[str] = set()
    seg_ids: set[str] = set()
    for item in docs:
        if not isinstance(item, Mapping):
            _fail("document index item is not an object")
        if set(item) != {
            "document_id",
            "release_id",
            "catalog_id",
            "catalog_sha256",
            "source_sha256",
            "label",
            "locator",
            "document_number",
            "jurisdiction",
            "document_type",
            "promulgated_on",
            "effective_from",
            "effective_to",
            "status",
            "capability",
            "capabilities",
            "warning_refs",
            "gap_refs",
            "segment_ids",
            "input_sha256",
        }:
            _fail("document index item has unknown or missing fields")
        document_id = item["document_id"]
        if (
            not isinstance(document_id, str)
            or not _DOCUMENT_ID.fullmatch(document_id)
            or document_id in doc_ids
        ):
            _fail("document index identity is invalid or duplicated")
        doc_ids.add(document_id)
        _binding_fields(item, binding, name=f"document[{document_id}]")
        capability, _ = _capabilities(item, name=f"document[{document_id}]")
        if capability != item["capability"] and not (
            item["warning_refs"] and item["capability"] == "identity_locator_only"
        ):
            _fail("document capability is inconsistent")
        _required_hash(item, ("source_sha256",), name=f"document[{document_id}].source_sha256")
        _reject_string(
            item["label"], name=f"document[{document_id}].label", maximum=MAX_LABEL_LENGTH
        )
        _optional_string(
            item, ("locator",), name=f"document[{document_id}].locator", maximum=MAX_LOCATOR_LENGTH
        )
        _date(item["promulgated_on"], name=f"document[{document_id}].promulgated_on")
        _ordered_dates(
            item["effective_from"], item["effective_to"], name=f"document[{document_id}].effective"
        )
        if (
            not isinstance(item["warning_refs"], list)
            or not isinstance(item["gap_refs"], list)
            or not isinstance(item["segment_ids"], list)
        ):
            _fail("document references must be arrays")
        if (
            len(set(item["warning_refs"])) != len(item["warning_refs"])
            or len(set(item["gap_refs"])) != len(item["gap_refs"])
            or len(set(item["segment_ids"])) != len(item["segment_ids"])
        ):
            _fail("document references are duplicated")
        if not isinstance(item["input_sha256"], str) or not _SHA256.fullmatch(item["input_sha256"]):
            _fail("document input digest is invalid")
    doc_lookup = {item["document_id"]: item for item in docs}
    for item in segs:
        if not isinstance(item, Mapping):
            _fail("segment index item is not an object")
        if set(item) != {
            "segment_id",
            "document_id",
            "release_id",
            "catalog_id",
            "catalog_sha256",
            "receipt_id",
            "source_sha256",
            "segment_sha256",
            "label",
            "locator",
            "promulgated_on",
            "effective_from",
            "effective_to",
            "status",
            "capability",
            "capabilities",
            "warning_labels",
            "warning_refs",
            "gap_refs",
            "input_sha256",
        }:
            _fail("segment index item has unknown or missing fields")
        segment_id = item["segment_id"]
        if (
            not isinstance(segment_id, str)
            or not _SEGMENT_ID.fullmatch(segment_id)
            or segment_id in seg_ids
        ):
            _fail("segment index identity is invalid or duplicated")
        seg_ids.add(segment_id)
        document_id = item["document_id"]
        if not isinstance(document_id, str) or document_id not in doc_lookup:
            _fail("segment references an unknown document")
        _binding_fields(item, binding, name=f"segment[{segment_id}]")
        capability, _ = _capabilities(item, name=f"segment[{segment_id}]")
        if capability != item["capability"] and not (
            item["warning_refs"] and item["capability"] == "identity_locator_only"
        ):
            _fail("segment capability is inconsistent")
        _required_hash(item, ("source_sha256",), name=f"segment[{segment_id}].source_sha256")
        _required_hash(item, ("segment_sha256",), name=f"segment[{segment_id}].segment_sha256")
        if item["receipt_id"] is not None and (
            not isinstance(item["receipt_id"], str) or not _RECEIPT_ID.fullmatch(item["receipt_id"])
        ):
            _fail("segment receipt identity is invalid")
        _ordered_dates(
            item["effective_from"], item["effective_to"], name=f"segment[{segment_id}].effective"
        )
        if not isinstance(item["warning_labels"], list) or len(set(item["warning_labels"])) != len(
            item["warning_labels"]
        ):
            _fail("segment warning labels are invalid")
        if not isinstance(item["warning_refs"], list) or not isinstance(item["gap_refs"], list):
            _fail("segment references must be arrays")
        if len(set(item["warning_refs"])) != len(item["warning_refs"] or []) or len(
            set(item["gap_refs"])
        ) != len(item["gap_refs"] or []):
            _fail("segment references are duplicated")
        if not isinstance(item["input_sha256"], str) or not _SHA256.fullmatch(item["input_sha256"]):
            _fail("segment input digest is invalid")
    seg_lookup = {item["segment_id"]: item for item in segs}
    expected_document_segments = {document_id: [] for document_id in doc_lookup}
    for segment in segs:
        expected_document_segments[segment["document_id"]].append(segment["segment_id"])
    for document_id, expected in expected_document_segments.items():
        if doc_lookup[document_id]["segment_ids"] != sorted(expected):
            _fail("document segment closure is incomplete")
    for item in value["release_timeline"]:
        if set(item) != {
            "release_id",
            "release_sha256",
            "catalog_id",
            "catalog_sha256",
            "catalog_sequence",
            "version",
            "published_on",
            "effective_from",
            "effective_to",
            "previous_release_id",
            "label",
            "input_sha256",
        }:
            _fail("release timeline item has unknown or missing fields")
        if not _RELEASE_ID.fullmatch(str(item["release_id"])):
            _fail("release timeline identity is invalid")
        if not _SHA256.fullmatch(str(item["release_sha256"])):
            _fail("release timeline digest is invalid")
        if (
            item["release_id"] == binding["release_id"]
            and item["release_sha256"] != binding["release_sha256"]
        ):
            _fail("release timeline release digest mismatch")
        if (
            item["catalog_id"] != binding["catalog_id"]
            or item["catalog_sha256"] != binding["catalog_sha256"]
        ):
            _fail("release timeline catalog mismatch")
        _date(item["published_on"], name="release timeline published_on")
        _ordered_dates(
            item["effective_from"], item["effective_to"], name="release timeline effective"
        )
    warnings = value["review_warnings"]
    gaps = value["evidence_gaps"]
    warning_ids: set[str] = set()
    gap_ids: set[str] = set()
    warning_segments: set[str] = set()
    gap_segments: set[str] = set()
    for kind, records, ids, segments_seen in (
        ("review_warnings", warnings, warning_ids, warning_segments),
        ("evidence_gaps", gaps, gap_ids, gap_segments),
    ):
        for item in records:
            required = {
                "id",
                "document_id",
                "segment_id",
                "release_id",
                "catalog_id",
                "catalog_sha256",
                "label",
                "code",
                "locator",
                "warning_count",
                "required_capability",
                "input_sha256",
            }
            if set(item) != required:
                _fail(f"{kind} item has unknown or missing fields")
            identifier = item["id"]
            if (
                not isinstance(identifier, str)
                or not _SAFE_CODE.fullmatch(identifier)
                or identifier in ids
            ):
                _fail(f"{kind} identity is invalid or duplicated")
            ids.add(identifier)
            _binding_fields(item, binding, name=f"{kind}[{identifier}]")
            if item["document_id"] is None and item["segment_id"] is None:
                _fail(f"{kind} item has no target")
            if item["document_id"] is not None and item["document_id"] not in doc_lookup:
                _fail(f"{kind} item references an unknown document")
            if item["segment_id"] is not None:
                if item["segment_id"] not in seg_ids:
                    _fail(f"{kind} item references an unknown segment")
                if item["document_id"] != seg_lookup[item["segment_id"]]["document_id"]:
                    _fail(f"{kind} item document/segment mismatch")
                segments_seen.add(item["segment_id"])
            if not isinstance(item["input_sha256"], str) or not _SHA256.fullmatch(
                item["input_sha256"]
            ):
                _fail(f"{kind} input digest is invalid")
    for item in value["definitions"]:
        if set(item) != {
            "id",
            "document_id",
            "segment_id",
            "release_id",
            "catalog_id",
            "catalog_sha256",
            "label",
            "code",
            "locator",
            "warning_count",
            "required_capability",
            "input_sha256",
        }:
            _fail("definition item has unknown or missing fields")
        if not _SAFE_CODE.fullmatch(str(item["id"])) or item["segment_id"] not in seg_ids:
            _fail("definition reference is invalid")
        _binding_fields(item, binding, name=f"definition[{item['id']}]")
        if item["document_id"] != seg_lookup[item["segment_id"]]["document_id"]:
            _fail("definition document binding mismatch")
    for item in value["cross_references"]:
        if set(item) != {
            "cross_reference_id",
            "from_segment_id",
            "to_segment_id",
            "from_document_id",
            "to_document_id",
            "release_id",
            "catalog_id",
            "catalog_sha256",
            "reference_type",
            "label",
            "locator",
            "input_sha256",
        }:
            _fail("cross-reference item has unknown or missing fields")
        if item["from_segment_id"] not in seg_ids or item["to_segment_id"] not in seg_ids:
            _fail("cross-reference is dangling")
        _binding_fields(item, binding, name=f"cross_reference[{item['cross_reference_id']}]")
        if (
            item["from_document_id"] != seg_lookup[item["from_segment_id"]]["document_id"]
            or item["to_document_id"] != seg_lookup[item["to_segment_id"]]["document_id"]
        ):
            _fail("cross-reference document binding mismatch")
    for item in value["receipt_drill_down"]:
        if set(item) != {
            "id",
            "document_id",
            "segment_id",
            "release_id",
            "catalog_id",
            "catalog_sha256",
            "label",
            "code",
            "locator",
            "warning_count",
            "required_capability",
            "input_sha256",
            "receipt_id",
        }:
            _fail("receipt item has unknown or missing fields")
        if item["receipt_id"] != item["id"] or not _RECEIPT_ID.fullmatch(str(item["receipt_id"])):
            _fail("receipt identity is invalid")
        _binding_fields(item, binding, name=f"receipt[{item['receipt_id']}]")
        if item["segment_id"] is not None and item["segment_id"] not in seg_ids:
            _fail("receipt references an unknown segment")
        if item["segment_id"] is not None:
            segment_receipt = seg_lookup[item["segment_id"]]["receipt_id"]
            if segment_receipt is not None and segment_receipt != item["receipt_id"]:
                _fail("receipt does not match segment receipt identity")
    expected_warning_refs: dict[str, list[str]] = {segment_id: [] for segment_id in seg_ids}
    expected_gap_refs: dict[str, list[str]] = {segment_id: [] for segment_id in seg_ids}
    expected_document_warning_refs: dict[str, list[str]] = {
        document_id: [] for document_id in doc_lookup
    }
    expected_document_gap_refs: dict[str, list[str]] = {
        document_id: [] for document_id in doc_lookup
    }
    for item in warnings:
        if item["segment_id"] is not None:
            expected_warning_refs[item["segment_id"]].append(item["id"])
        expected_document_warning_refs[item["document_id"]].append(item["id"])
    for item in gaps:
        if item["segment_id"] is not None:
            expected_gap_refs[item["segment_id"]].append(item["id"])
        expected_document_gap_refs[item["document_id"]].append(item["id"])
    for document_id, document in doc_lookup.items():
        if document["warning_refs"] != sorted(expected_document_warning_refs[document_id]):
            _fail("document warning references are inconsistent")
        if document["gap_refs"] != sorted(expected_document_gap_refs[document_id]):
            _fail("document gap references are inconsistent")
    for segment in segs:
        segment_id = segment["segment_id"]
        if segment["warning_refs"] != sorted(expected_warning_refs[segment_id]):
            _fail("segment warning references are inconsistent")
        if segment["gap_refs"] != sorted(expected_gap_refs[segment_id]):
            _fail("segment gap references are inconsistent")
        if segment["warning_refs"] and segment_id not in warning_segments:
            _fail("segment warning closure is incomplete")
        if segment["gap_refs"] and segment_id not in gap_segments:
            _fail("segment gap closure is incomplete")
        if segment["warning_refs"] and segment["capability"] not in {
            "identity_locator_only",
            "warned",
            "ocr_unreviewed",
        }:
            _fail("warning capability was not downgraded")
        if segment["warning_refs"] and not segment["gap_refs"]:
            _fail("warning segment has no explicit gap")
    if value["effective_dates"] != _effective_dates(docs, segs):
        _fail("effective-date section is inconsistent with document/segment indexes")


def verify_authoritative_navigator(value: Any) -> dict[str, Any]:
    """Verify a navigator without exposing source text, paths, or large payloads."""

    try:
        if not isinstance(value, Mapping):
            _fail("navigator must be an object")
        value = dict(value)
        _verify_section_shape(value)
        _verify_binding(value["binding"])
        _verify_navigator_items(value)
        expected_sections = _section_digests(value)
        manifest = value["manifest"]
        if manifest["section_digests"] != expected_sections:
            _fail("navigator section digest mismatch")
        expected_counts = {name: len(value[name]) for name in _SECTION_NAMES}
        if manifest["counts"] != expected_counts:
            _fail("navigator manifest counts mismatch")
        input_digests = manifest.get("input_digests")
        if not isinstance(input_digests, Mapping):
            _fail("navigator input digest manifest is invalid")
        expected_input_keys = {
            "pack",
            "release",
            "documents",
            "segments",
            "release_timeline",
            "definitions",
            "cross_references",
            "review_warnings",
            "evidence_gaps",
            "receipt_drill_down",
        }
        if set(input_digests) != expected_input_keys:
            _fail("navigator input digest manifest fields are invalid")
        for _key, digests in input_digests.items():
            if not isinstance(digests, list) or any(
                not isinstance(digest, str) or not _SHA256.fullmatch(digest) for digest in digests
            ):
                _fail("navigator input digest manifest value is invalid")
        expected_inputs = {
            "pack": [value["binding"]["pack_input_sha256"]],
            "release": [value["binding"]["release_input_sha256"]],
            "documents": sorted(item["input_sha256"] for item in value["document_index"]),
            "segments": sorted(item["input_sha256"] for item in value["segment_index"]),
            "release_timeline": sorted(item["input_sha256"] for item in value["release_timeline"]),
            "definitions": sorted(item["input_sha256"] for item in value["definitions"]),
            "cross_references": sorted(item["input_sha256"] for item in value["cross_references"]),
            "review_warnings": sorted(item["input_sha256"] for item in value["review_warnings"]),
            "evidence_gaps": sorted(item["input_sha256"] for item in value["evidence_gaps"]),
            "receipt_drill_down": sorted(
                item["input_sha256"] for item in value["receipt_drill_down"]
            ),
        }
        if dict(input_digests) != expected_inputs:
            _fail("navigator input digest manifest mismatch")
        if manifest["limits"] != {
            "document_max": MAX_DOCUMENTS,
            "segment_max": MAX_SEGMENTS,
            "warning_max": MAX_WARNINGS,
            "gap_max": MAX_GAPS,
            "encoded_max_bytes": MAX_ENCODED_BYTES,
        }:
            _fail("navigator limits mismatch")
        if value["navigator_sha256"] != _hash(_navigator_body(value)):
            _fail("navigator digest mismatch")
        binding = value["binding"]
        return {
            "schema_version": VERIFICATION_SCHEMA_VERSION,
            "valid": True,
            "reason": "verified",
            "navigator_sha256": value["navigator_sha256"],
            "pack_id": binding["pack_id"],
            "catalog_id": binding["catalog_id"],
            "release_id": binding["release_id"],
            "section_digests": dict(value["manifest"]["section_digests"]),
            "counts": dict(value["manifest"]["counts"]),
        }
    except (TypeError, ValueError, KeyError, IndexError):
        return _verify_error(value, "navigator_verification_failed")


def _matrix_body(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("record_sha256", None)
    return body


def _validate_matrix(value: Any) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(value, Mapping):
        _fail("decision matrix must be an object")
    matrix = dict(value)
    expected_top = {
        "active_after",
        "catalog",
        "competitive_claim_eligible",
        "decision_summary",
        "limitations",
        "record_sha256",
        "release_target",
        "reproducibility",
        "retrieval_quality",
        "schema_version",
        "snapshot",
        "sources",
        "status",
        "target_rebuild",
    }
    if set(matrix) != expected_top:
        _fail("decision matrix shape is not the public v1 shape")
    if matrix["schema_version"] != "deeplaw.authoritative-source-quality-decision-matrix/v1":
        _fail("unsupported decision matrix schema")
    if matrix["release_target"] != "0.11.0" or matrix["competitive_claim_eligible"] is not False:
        _fail("decision matrix release binding is invalid")
    record_sha256 = matrix.get("record_sha256")
    if (
        not isinstance(record_sha256, str)
        or record_sha256 != _EXPECTED_MATRIX_RECORD_SHA256
        or _hash(_matrix_body(matrix)) != record_sha256
    ):
        _fail("decision matrix record digest mismatch")
    sources = matrix.get("sources")
    if not isinstance(sources, list) or len(sources) != _EXPECTED_MATRIX_SOURCE_COUNT:
        _fail("decision matrix source count mismatch")
    source_ids: set[str] = set()
    warning_sources: list[dict[str, Any]] = []
    warning_total = 0
    review_segment_total = 0
    for source in sources:
        if not isinstance(source, Mapping):
            _fail("decision matrix source is invalid")
        source = dict(source)
        required = {
            "authoritative_pack",
            "authority",
            "byte_size",
            "compilation",
            "decision",
            "derived_state",
            "execution_status",
            "extraction_quality",
            "format",
            "fragments",
            "immutable_bytes_sha256",
            "knowledge_output",
            "lifecycle",
            "media_type",
            "origin",
            "parser",
            "reason_codes",
            "rollback",
            "scope",
            "sensitivity",
            "source_ir",
            "source_revision_id",
            "source_revision_semantics",
            "stable_source_id",
        }
        if set(source) != required:
            _fail("decision matrix source shape is not public v1")
        source_id = source.get("stable_source_id")
        if (
            not isinstance(source_id, str)
            or not _DOCUMENT_ID.fullmatch(source_id)
            or source_id in source_ids
        ):
            _fail("decision matrix source identity is invalid")
        source_ids.add(source_id)
        quality = source.get("extraction_quality")
        if not isinstance(quality, Mapping):
            _fail("decision matrix extraction quality is invalid")
        warning_count = quality.get("warning_count")
        review_required_segments = quality.get("review_required_segments")
        if (
            isinstance(warning_count, bool)
            or not isinstance(warning_count, int)
            or warning_count < 0
        ):
            _fail("decision matrix warning count is invalid")
        if (
            isinstance(review_required_segments, bool)
            or not isinstance(review_required_segments, int)
            or review_required_segments < 0
        ):
            _fail("decision matrix review segment count is invalid")
        review_required = quality.get("review_required") is True
        if review_required or warning_count > 0:
            warning_sources.append(source)
            warning_total += warning_count
            review_segment_total += review_required_segments
    if (
        source_ids & _EXPECTED_MATRIX_SOURCE_IDS != _EXPECTED_MATRIX_SOURCE_IDS
        or len(source_ids & _EXPECTED_MATRIX_SOURCE_IDS) != 5
    ):
        _fail("decision matrix warning source identities mismatch")
    if (
        len(warning_sources) != 5
        or warning_total != _EXPECTED_WARNING_COUNT
        or review_segment_total != _EXPECTED_REVIEW_SEGMENT_COUNT
    ):
        _fail("decision matrix audited warning totals mismatch")
    summary = matrix.get("decision_summary")
    if (
        not isinstance(summary, Mapping)
        or summary.get("no_action") != 13
        or summary.get("reparse_source_ir") != 15
        or any(
            summary.get(key) != 0
            for key in (
                "rebuild_derived",
                "recompile_knowledge",
                "ingest_new_source_revision",
                "blocked_invalid_evidence",
            )
        )
    ):
        _fail("decision matrix decision summary mismatch")
    catalog = matrix.get("catalog")
    if not isinstance(catalog, Mapping):
        _fail("decision matrix catalog is invalid")
    catalog_id = catalog.get("catalog_id")
    catalog_sha256 = catalog.get("sha256")
    sequence = catalog.get("sequence")
    if (
        not isinstance(catalog_id, str)
        or not _CATALOG_ID.fullmatch(catalog_id)
        or not isinstance(catalog_sha256, str)
        or not _SHA256.fullmatch(catalog_sha256)
        or sequence != 2
    ):
        _fail("decision matrix catalog binding is invalid")
    active_after = matrix.get("active_after")
    if not isinstance(active_after, Mapping):
        _fail("decision matrix active release is invalid")
    release_id = active_after.get("release_id")
    release_sha256 = active_after.get("database_sha256")
    if (
        not isinstance(release_id, str)
        or not _RELEASE_ID.fullmatch(release_id)
        or not isinstance(release_sha256, str)
        or not _SHA256.fullmatch(release_sha256)
    ):
        _fail("decision matrix release binding is invalid")
    binding = {
        "catalog_id": catalog_id,
        "catalog_sha256": catalog_sha256,
        "catalog_sequence": sequence,
        "release_id": release_id,
        "release_sha256": release_sha256,
        "matrix_record_sha256": record_sha256,
    }
    return (
        matrix,
        sorted(
            (dict(source) for source in warning_sources), key=lambda item: item["stable_source_id"]
        ),
        binding,
    )


def _disposition_body(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("disposition_sha256", None)
    return body


def derive_review_dispositions(matrix_like_value: Any) -> dict[str, Any]:
    """Derive honest pending/downgraded dispositions from the frozen matrix."""

    _matrix, sources, binding = _validate_matrix(matrix_like_value)
    dispositions: list[dict[str, Any]] = []
    for source in sources:
        quality = source["extraction_quality"]
        source_id = source["stable_source_id"]
        source_record_sha256 = _hash(source)
        source_binding = source["authoritative_pack"]
        if not isinstance(source_binding, Mapping):
            _fail("source authoritative pack binding is invalid")
        if (
            source_binding.get("catalog_id") != binding["catalog_id"]
            or source_binding.get("catalog_sha256") != binding["catalog_sha256"]
            or source_binding.get("target_release_id") != binding["release_id"]
            or source_binding.get("target_database_sha256") != binding["release_sha256"]
        ):
            _fail("source release/catalog binding mismatch")
        warning_count = quality["warning_count"]
        review_segments = quality["review_required_segments"]
        gap = {
            "gap_id": stable_id("authgap", source_id, source_record_sha256),
            "code": "critical_token_review_pending",
            "label": "independent_review_unavailable",
            "affected_capability": "identity_locator_only",
            "warning_count": warning_count,
            "review_required_segments": review_segments,
        }
        dispositions.append(
            {
                "source_id": source_id,
                "release_id": binding["release_id"],
                "catalog_id": binding["catalog_id"],
                "catalog_sha256": binding["catalog_sha256"],
                "source_sha256": source["immutable_bytes_sha256"],
                "source_record_sha256": source_record_sha256,
                "warning_count": warning_count,
                "review_required": True,
                "review_required_segments": review_segments,
                "status": "maintainer_review_pending",
                "maintainer_status": "maintainer_review_pending",
                "expert_status": "expert_review_pending",
                "human_reviewed": False,
                "expert_reviewed": False,
                "capability_downgraded": True,
                "affected_capability": "identity_locator_only",
                "capability_after": "identity_locator_only",
                "pack_mutation_performed": False,
                "release_mutation_performed": False,
                "official_prose_generated": False,
                "gap": gap,
            }
        )
    value = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "binding": binding,
        "dispositions": dispositions,
        "source_count": len(dispositions),
        "warning_count": sum(item["warning_count"] for item in dispositions),
        "review_required_segments": sum(item["review_required_segments"] for item in dispositions),
        "human_reviewed": False,
        "expert_reviewed": False,
        "capability_downgraded": True,
        "pack_mutation_performed": False,
        "release_mutation_performed": False,
        "official_prose_generated": False,
        "disposition_sha256": "",
    }
    value["disposition_sha256"] = _hash(_disposition_body(value))
    return value


def verify_review_dispositions(value: Any) -> dict[str, Any]:
    """Verify a disposition object and return a bounded receipt."""

    try:
        if not isinstance(value, Mapping):
            _fail("dispositions must be an object")
        value = dict(value)
        expected_keys = {
            "schema_version",
            "binding",
            "dispositions",
            "source_count",
            "warning_count",
            "review_required_segments",
            "human_reviewed",
            "expert_reviewed",
            "capability_downgraded",
            "pack_mutation_performed",
            "release_mutation_performed",
            "official_prose_generated",
            "disposition_sha256",
        }
        if set(value) != expected_keys or value["schema_version"] != DISPOSITION_SCHEMA_VERSION:
            _fail("disposition contract shape mismatch")
        if (
            value["human_reviewed"] is not False
            or value["expert_reviewed"] is not False
            or value["capability_downgraded"] is not True
            or value["pack_mutation_performed"] is not False
            or value["release_mutation_performed"] is not False
            or value["official_prose_generated"] is not False
        ):
            _fail("disposition policy flags are invalid")
        binding = _mapping(value["binding"], "disposition.binding")
        if set(binding) != {
            "catalog_id",
            "catalog_sha256",
            "catalog_sequence",
            "release_id",
            "release_sha256",
            "matrix_record_sha256",
        }:
            _fail("disposition binding shape mismatch")
        if (
            not _CATALOG_ID.fullmatch(str(binding["catalog_id"]))
            or not _SHA256.fullmatch(str(binding["catalog_sha256"]))
            or binding["catalog_sequence"] != 2
            or not _RELEASE_ID.fullmatch(str(binding["release_id"]))
            or not _SHA256.fullmatch(str(binding["release_sha256"]))
            or binding["matrix_record_sha256"] != _EXPECTED_MATRIX_RECORD_SHA256
        ):
            _fail("disposition binding is invalid")
        dispositions = value["dispositions"]
        if not isinstance(dispositions, list) or len(dispositions) != 5:
            _fail("disposition source count mismatch")
        ids: set[str] = set()
        warning_total = 0
        segment_total = 0
        for item in dispositions:
            required = {
                "source_id",
                "release_id",
                "catalog_id",
                "catalog_sha256",
                "source_sha256",
                "source_record_sha256",
                "warning_count",
                "review_required",
                "review_required_segments",
                "status",
                "maintainer_status",
                "expert_status",
                "human_reviewed",
                "expert_reviewed",
                "capability_downgraded",
                "affected_capability",
                "capability_after",
                "pack_mutation_performed",
                "release_mutation_performed",
                "official_prose_generated",
                "gap",
            }
            if set(item) != required:
                _fail("disposition item shape mismatch")
            source_id = item["source_id"]
            if (
                not isinstance(source_id, str)
                or not _DOCUMENT_ID.fullmatch(source_id)
                or source_id in ids
                or source_id not in _EXPECTED_MATRIX_SOURCE_IDS
            ):
                _fail("disposition source identity mismatch")
            ids.add(source_id)
            if (
                item["release_id"] != binding["release_id"]
                or item["catalog_id"] != binding["catalog_id"]
                or item["catalog_sha256"] != binding["catalog_sha256"]
            ):
                _fail("disposition release/catalog mismatch")
            if not _SHA256.fullmatch(str(item["source_sha256"])) or not _SHA256.fullmatch(
                str(item["source_record_sha256"])
            ):
                _fail("disposition source digest invalid")
            if (
                item["review_required"] is not True
                or item["status"] != "maintainer_review_pending"
                or item["maintainer_status"] != "maintainer_review_pending"
                or item["expert_status"] != "expert_review_pending"
                or item["human_reviewed"] is not False
                or item["expert_reviewed"] is not False
                or item["capability_downgraded"] is not True
                or item["affected_capability"] != "identity_locator_only"
                or item["capability_after"] != "identity_locator_only"
                or item["pack_mutation_performed"] is not False
                or item["release_mutation_performed"] is not False
                or item["official_prose_generated"] is not False
            ):
                _fail("disposition policy state is invalid")
            warning_count = _integer(
                item["warning_count"],
                name="disposition.warning_count",
                minimum=1,
                maximum=MAX_WARNINGS,
                optional=False,
            )
            review_segments = _integer(
                item["review_required_segments"],
                name="disposition.review_required_segments",
                minimum=1,
                maximum=MAX_SEGMENTS,
                optional=False,
            )
            warning_total += warning_count or 0
            segment_total += review_segments or 0
            gap = item["gap"]
            if (
                not isinstance(gap, Mapping)
                or set(gap)
                != {
                    "gap_id",
                    "code",
                    "label",
                    "affected_capability",
                    "warning_count",
                    "review_required_segments",
                }
                or gap["affected_capability"] != "identity_locator_only"
                or gap["warning_count"] != warning_count
                or gap["review_required_segments"] != review_segments
            ):
                _fail("disposition gap binding is invalid")
        if (
            ids != _EXPECTED_MATRIX_SOURCE_IDS
            or warning_total != _EXPECTED_WARNING_COUNT
            or segment_total != _EXPECTED_REVIEW_SEGMENT_COUNT
        ):
            _fail("disposition audited totals mismatch")
        if (
            value["source_count"] != 5
            or value["warning_count"] != 32
            or value["review_required_segments"] != 8
        ):
            _fail("disposition totals mismatch")
        if value["disposition_sha256"] != _hash(_disposition_body(value)):
            _fail("disposition digest mismatch")
        return {
            "schema_version": DISPOSITION_VERIFICATION_SCHEMA_VERSION,
            "valid": True,
            "reason": "verified",
            "disposition_sha256": value["disposition_sha256"],
            "matrix_record_sha256": binding["matrix_record_sha256"],
            "source_count": 5,
            "warning_count": 32,
            "review_required_segments": 8,
        }
    except (TypeError, ValueError, KeyError, IndexError):
        return {
            "schema_version": DISPOSITION_VERIFICATION_SCHEMA_VERSION,
            "valid": False,
            "reason": "disposition_verification_failed",
            "disposition_sha256": (
                value.get("disposition_sha256")
                if isinstance(value, Mapping)
                and isinstance(value.get("disposition_sha256"), str)
                and _SHA256.fullmatch(value["disposition_sha256"])
                else None
            ),
        }


verify_authoritative_review_dispositions = verify_review_dispositions
verify_authoritative_review_disposition = verify_review_dispositions


__all__ = [
    "build_authoritative_navigator",
    "derive_review_dispositions",
    "verify_authoritative_navigator",
    "verify_authoritative_review_disposition",
    "verify_authoritative_review_dispositions",
    "verify_review_dispositions",
]
