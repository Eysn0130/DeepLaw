from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from deeplaw.knowledge_intelligence import (
    DENSE_DIMENSIONS,
    LOCAL_DENSE_MODEL,
    dense_vector,
    search_dense_index,
    vector_cosine,
    write_dense_index,
)

ZERO = "0" * 64
REFERENCE_TIME = "2026-08-29T00:00:00Z"
SENSITIVITY_ORDER = ("public", "internal", "private", "restricted")


def _knowledge_id(suffix: str) -> str:
    return f"knowledge_{suffix * 24}"


def _revision_id(suffix: str) -> str:
    return f"knowledgerev_{suffix * 24}"


def _row(
    suffix: str,
    *,
    title: str = "governed dense phrase",
    body: str = "same body",
    semantic_key: str = "shared",
    scope: str = "project",
    sensitivity: str = "public",
    kind: str = "concept",
    tags: list[str] | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    return {
        "knowledge_id": _knowledge_id(suffix),
        "revision_id": _revision_id(suffix),
        "title": title,
        "body": body,
        "semantic_key": semantic_key,
        "scope": scope,
        "sensitivity": sensitivity,
        "kind": kind,
        "tags": tags if tags is not None else ["shared"],
        "valid_from": valid_from,
        "valid_to": valid_to,
        "expires_at": expires_at,
    }


def _rows() -> list[dict[str, Any]]:
    return [
        _row("0"),
        _row("a"),
        _row("f", scope="personal"),
        _row("e", sensitivity="private"),
        _row("d", kind="procedure", tags=["procedure"]),
        _row("c", tags=["other"]),
        _row(
            "b",
            tags=["future"],
            valid_from="2026-08-30T00:00:00Z",
        ),
        _row(
            "9",
            tags=["past"],
            valid_to=REFERENCE_TIME,
        ),
        _row(
            "8",
            tags=["expired"],
            expires_at=REFERENCE_TIME,
        ),
        _row(
            "6",
            tags=["valid"],
            valid_from="2026-08-28T00:00:00Z",
            valid_to="2026-08-30T00:00:00Z",
            expires_at="2026-08-30T00:00:00Z",
        ),
        _row("5", body="different body", semantic_key="variant", tags=["variant"]),
        _row("7", title="", body="", semantic_key="", tags=["zero"]),
    ]


def _write_index(root: Path, rows: list[dict[str, Any]] | None = None) -> None:
    (root / ".deeplaw" / "derived" / "vectors").mkdir(parents=True)
    write_dense_index(
        root,
        rows=_rows() if rows is None else rows,
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
    )


def _matches(
    record: dict[str, Any],
    *,
    scope: str,
    max_sensitivity: str,
    reference_time: str,
    kinds: tuple[str, ...],
    required_tags: tuple[str, ...],
) -> bool:
    maximum_sensitivity = SENSITIVITY_ORDER.index(max_sensitivity)
    return not (
        record["scope"] != scope
        or SENSITIVITY_ORDER.index(record["sensitivity"]) > maximum_sensitivity
        or (kinds and record["kind"] not in kinds)
        or any(tag not in record["tags"] for tag in required_tags)
        or (
            record["valid_from"] is not None
            and record["valid_from"] > reference_time
        )
        or (record["valid_to"] is not None and record["valid_to"] <= reference_time)
        or (
            record["expires_at"] is not None
            and record["expires_at"] <= reference_time
        )
    )


def _reference_search(
    root: Path,
    *,
    query: str,
    scope: str,
    max_sensitivity: str,
    reference_time: str,
    kinds: tuple[str, ...] = (),
    required_tags: tuple[str, ...] = (),
    limit: int = 64,
) -> dict[str, Any]:
    directory = root / ".deeplaw" / "derived" / "vectors"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    records = json.loads((directory / "records.json").read_text(encoding="utf-8"))
    payload = (directory / "vectors.bin").read_bytes()
    query_vector = dense_vector(query)
    scored: list[tuple[float, str, str]] = []
    for ordinal, record in enumerate(records):
        if not _matches(
            record,
            scope=scope,
            max_sensitivity=max_sensitivity,
            reference_time=reference_time,
            kinds=kinds,
            required_tags=required_tags,
        ):
            continue
        offset = 10 + ordinal * DENSE_DIMENSIONS
        vector = struct.unpack(
            f"{DENSE_DIMENSIONS}b", payload[offset : offset + DENSE_DIMENSIONS]
        )
        score = vector_cosine(query_vector, vector)
        if score > 0:
            scored.append((score, record["knowledge_id"], record["revision_id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return {
        "ready": True,
        "model_identity": LOCAL_DENSE_MODEL,
        "manifest_sha256": manifest["manifest_sha256"],
        "results": [
            {
                "knowledge_id": knowledge_id,
                "revision_id": revision_id,
                "score": round(score, 6),
            }
            for score, knowledge_id, revision_id in scored[:limit]
        ],
    }


def test_dense_search_matches_vector_cosine_reference_and_filters(tmp_path: Path) -> None:
    _write_index(tmp_path)
    query = "governed dense phrase\nsame body\nshared"
    cases = (
        (
            {},
            [_knowledge_id(suffix) for suffix in ("0", "6", "a", "c", "d", "5")],
        ),
        (
            {"scope": "personal"},
            [_knowledge_id("f")],
        ),
        (
            {"max_sensitivity": "private"},
            [_knowledge_id(suffix) for suffix in ("0", "6", "a", "c", "d", "e", "5")],
        ),
        (
            {"kinds": ("procedure",)},
            [_knowledge_id("d")],
        ),
        (
            {"required_tags": ("shared",)},
            [_knowledge_id(suffix) for suffix in ("0", "a")],
        ),
        (
            {"required_tags": ("valid",)},
            [_knowledge_id("6")],
        ),
        (
            {"required_tags": ("variant",)},
            [_knowledge_id("5")],
        ),
        (
            {"required_tags": ("future",)},
            [],
        ),
        (
            {"required_tags": ("past",)},
            [],
        ),
        (
            {"required_tags": ("expired",)},
            [],
        ),
    )
    for overrides, expected_ids in cases:
        arguments = {
            "scope": "project",
            "max_sensitivity": "public",
            "reference_time": REFERENCE_TIME,
            "kinds": (),
            "required_tags": (),
            **overrides,
        }
        actual = search_dense_index(
            tmp_path,
            query=query,
            input_audit_head=ZERO,
            legacy_audit_head=ZERO,
            **arguments,
        )
        reference = _reference_search(tmp_path, query=query, **arguments)
        assert actual == reference
        assert [item["knowledge_id"] for item in actual["results"]] == expected_ids
        if _knowledge_id("5") in expected_ids:
            assert any(item["score"] != 1.0 for item in actual["results"])
        else:
            assert all(item["score"] == 1.0 for item in actual["results"])

    zero_query = search_dense_index(
        tmp_path,
        query="",
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        scope="project",
        max_sensitivity="public",
        reference_time=REFERENCE_TIME,
    )
    assert zero_query == _reference_search(
        tmp_path,
        query="",
        scope="project",
        max_sensitivity="public",
        reference_time=REFERENCE_TIME,
    )
    assert zero_query["results"] == []
    assert _knowledge_id("7") not in {
        item["knowledge_id"] for item in search_dense_index(
            tmp_path,
            query=query,
            input_audit_head=ZERO,
            legacy_audit_head=ZERO,
            scope="project",
            max_sensitivity="public",
            reference_time=REFERENCE_TIME,
        )["results"]
    }


def test_dense_search_rejects_direct_vector_file_tamper(tmp_path: Path) -> None:
    _write_index(tmp_path, [_row("a")])
    vector_path = tmp_path / ".deeplaw" / "derived" / "vectors" / "vectors.bin"
    vector_path.write_bytes(vector_path.read_bytes() + b"tamper")

    result = search_dense_index(
        tmp_path,
        query="governed dense phrase\nsame body\nshared",
        input_audit_head=ZERO,
        legacy_audit_head=ZERO,
        scope="project",
        max_sensitivity="public",
        reference_time=REFERENCE_TIME,
    )

    assert result == {"ready": False, "reason": "binding_invalid", "results": []}
