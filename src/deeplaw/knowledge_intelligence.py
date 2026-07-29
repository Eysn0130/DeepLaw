from __future__ import annotations

import hashlib
import json
import math
import os
import re
import struct
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .util import canonical_json, compact_text, search_terms, sha256_bytes, sha256_file, stable_id

LOCAL_DENSE_MODEL = "deeplaw-multilingual-hash-dense/1"
LOCAL_RERANKER_MODEL = "deeplaw-evidence-duty-reranker/1"
DENSE_INDEX_SCHEMA = "deeplaw.local-dense-index/v2"
DENSE_DIMENSIONS = 192
_VECTOR_MAGIC = b"DLV1"
_MAX_INDEX_ITEMS = 1_000_000
_MAX_VECTOR_BYTES = 10 + _MAX_INDEX_ITEMS * DENSE_DIMENSIONS
_MAX_RECORD_BYTES = 256 * 1024 * 1024
_MAX_MANIFEST_BYTES = 256 * 1024
_KNOWLEDGE_ID = re.compile(r"^knowledge_[0-9a-f]{24}$")
_REVISION_ID = re.compile(r"^knowledgerev_[0-9a-f]{24}$")
_CANONICAL_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TOKEN = re.compile(r"[\w.+#/-]+", re.UNICODE)
_SCOPES = frozenset({"personal", "project", "domain"})
_SENSITIVITY_ORDER = ("public", "internal", "private", "restricted")
_KNOWLEDGE_KINDS = frozenset(
    {
        "claim",
        "concept",
        "entity",
        "event",
        "decision",
        "procedure",
        "experience",
        "preference",
        "synthesis",
        "comparison",
        "skill",
        "memory",
    }
)
_NEGATION = re.compile(
    r"(?:\bnot\b|\bnever\b|\bno\b|\bcannot\b|\bfalse\b|不(?:是|会|能|应|得)|没有|从未|禁止)",
    re.IGNORECASE,
)
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/])")
_SECRET = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:api[_-]?key|access[_-]?token|password)\s*[:=])",
    re.IGNORECASE,
)


def normalize_identity_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", compact_text(value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def estimate_tokens(value: str) -> int:
    """Return a deterministic conservative provider-token estimate.

    CJK characters are counted individually; runs of non-CJK text use a
    four-characters-per-token floor. The estimate intentionally over-budgets
    mixed punctuation rather than depending on a remote provider tokenizer.
    """

    cjk = sum(
        1
        for character in value
        if "\u3400" <= character <= "\u9fff"
        or "\u3040" <= character <= "\u30ff"
        or "\uac00" <= character <= "\ud7af"
    )
    non_cjk = len(value) - cjk
    return cjk + max(0, math.ceil(non_cjk / 4))


def _features(value: str) -> Counter[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens = [token for token in _TOKEN.findall(normalized) if token]
    features: Counter[str] = Counter()
    for token in tokens:
        features[f"w:{token}"] += 3
        if len(token) >= 3:
            for index in range(len(token) - 2):
                features[f"c:{token[index:index + 3]}"] += 1
    compact = "".join(character for character in normalized if not character.isspace())
    for index in range(max(0, len(compact) - 1)):
        pair = compact[index : index + 2]
        if any("\u3400" <= character <= "\u9fff" for character in pair):
            features[f"z:{pair}"] += 2
    return features


def dense_vector(value: str, *, dimensions: int = DENSE_DIMENSIONS) -> tuple[int, ...]:
    if not 32 <= dimensions <= 1_024:
        raise ValueError("dense vector dimensions are invalid")
    vector = [0.0] * dimensions
    for feature, count in _features(value).items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign * (1.0 + math.log1p(count))
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return (0,) * dimensions
    return tuple(max(-127, min(127, round(value / norm * 127))) for value in vector)


def vector_cosine(left: Iterable[int], right: Iterable[int]) -> float:
    left_values = tuple(left)
    right_values = tuple(right)
    if len(left_values) != len(right_values) or not left_values:
        raise ValueError("dense vectors are incompatible")
    dot = sum(a * b for a, b in zip(left_values, right_values, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def semantic_similarity(left: str, right: str) -> float:
    return vector_cosine(dense_vector(left), dense_vector(right))


def likely_contradiction(
    left: str,
    right: str,
    *,
    left_assertion: dict[str, Any] | None = None,
    right_assertion: dict[str, Any] | None = None,
) -> bool:
    """Detect only high-precision contradictions and otherwise abstain."""

    if left_assertion and right_assertion:
        keys = ("subject", "predicate")
        if all(left_assertion.get(key) == right_assertion.get(key) for key in keys):
            left_value = left_assertion.get("object")
            right_value = right_assertion.get("object")
            left_polarity = left_assertion.get("polarity", "positive")
            right_polarity = right_assertion.get("polarity", "positive")
            return bool(
                (left_value == right_value and left_polarity != right_polarity)
                or (left_value != right_value and left_polarity == right_polarity)
            )
    left_without_negation = _NEGATION.sub("", compact_text(left)).casefold()
    right_without_negation = _NEGATION.sub("", compact_text(right)).casefold()
    return bool(
        left_without_negation
        and left_without_negation == right_without_negation
        and bool(_NEGATION.search(left)) != bool(_NEGATION.search(right))
    )


def capture_rejection_reason(item: dict[str, Any]) -> str | None:
    if item.get("durable") is not True:
        return "not_marked_durable"
    title = item.get("title")
    body = item.get("body")
    if (
        not isinstance(title, str)
        or not isinstance(body, str)
        or not title.strip()
        or not body.strip()
    ):
        return "invalid_content"
    combined = f"{title}\n{body}"
    if len(body) > 200_000:
        return "content_too_large"
    if _ABSOLUTE_PATH.search(combined):
        return "local_path_present"
    if _SECRET.search(combined):
        return "secret_like_content"
    if item.get("contains_case_data") is not False:
        return "case_data_boundary_unconfirmed"
    if item.get("reusable") is not True:
        return "not_cross_task_reusable"
    return None


def write_dense_index(
    root: Path,
    *,
    rows: list[dict[str, Any]],
    input_audit_head: str,
    legacy_audit_head: str,
) -> dict[str, Any]:
    if len(rows) > _MAX_INDEX_ITEMS:
        raise ValueError("local dense index exceeds its item bound")
    directory = root / ".deeplaw" / "derived" / "vectors"
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("local dense index directory is missing or unsafe")
    vectors = bytearray()
    records: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows):
        vector = dense_vector(f"{row['title']}\n{row['body']}\n{row.get('semantic_key', '')}")
        vectors.extend(struct.pack(f"{DENSE_DIMENSIONS}b", *vector))
        records.append(
            {
                "ordinal": ordinal,
                "knowledge_id": row["knowledge_id"],
                "revision_id": row["revision_id"],
                "scope": row["scope"],
                "sensitivity": row["sensitivity"],
                "kind": row["kind"],
                "tags": row["tags"],
                "valid_from": row["valid_from"],
                "valid_to": row["valid_to"],
                "expires_at": row["expires_at"],
            }
        )
    vector_payload = _VECTOR_MAGIC + struct.pack(">HI", DENSE_DIMENSIONS, len(records)) + vectors
    vector_path = directory / "vectors.bin"
    metadata_path = directory / "records.json"
    manifest_path = directory / "manifest.json"
    metadata_payload = (
        json.dumps(records, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")

    def atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    atomic_write(vector_path, vector_payload)
    atomic_write(metadata_path, metadata_payload)
    manifest = {
        "schema_version": DENSE_INDEX_SCHEMA,
        "model_identity": LOCAL_DENSE_MODEL,
        "model_revision": "1",
        "network_policy": "offline",
        "dimensions": DENSE_DIMENSIONS,
        "quantization": "signed-int8-l2",
        "input_audit_head": input_audit_head,
        "legacy_audit_head": legacy_audit_head,
        "revision_ids_sha256": sha256_bytes(
            canonical_json([record["revision_id"] for record in records]).encode("utf-8")
        ),
        "item_count": len(records),
        "vectors": {
            "path": "vectors.bin",
            "byte_size": len(vector_payload),
            "sha256": sha256_bytes(vector_payload),
        },
        "records": {
            "path": "records.json",
            "byte_size": len(metadata_payload),
            "sha256": sha256_bytes(metadata_payload),
        },
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(manifest).encode("utf-8"))
    manifest_payload = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    atomic_write(manifest_path, manifest_payload)
    return manifest


def search_dense_index(
    root: Path,
    *,
    query: str,
    input_audit_head: str,
    legacy_audit_head: str,
    scope: str,
    max_sensitivity: str,
    reference_time: str,
    kinds: tuple[str, ...] = (),
    required_tags: tuple[str, ...] = (),
    limit: int = 64,
) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise ValueError("local dense search limit is invalid")
    if scope not in _SCOPES or max_sensitivity not in _SENSITIVITY_ORDER:
        raise ValueError("local dense search boundary is invalid")
    if not _CANONICAL_TIMESTAMP.fullmatch(reference_time):
        raise ValueError("local dense search reference time is invalid")
    if any(kind not in _KNOWLEDGE_KINDS for kind in kinds):
        raise ValueError("local dense search kind filter is invalid")
    if any(not isinstance(tag, str) or not tag for tag in required_tags):
        raise ValueError("local dense search tag filter is invalid")
    directory = root / ".deeplaw" / "derived" / "vectors"
    manifest_path = directory / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return {"ready": False, "reason": "manifest_unavailable", "results": []}
    try:
        if not 1 <= manifest_path.stat().st_size <= _MAX_MANIFEST_BYTES:
            raise ValueError("local dense manifest size is invalid")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("local dense manifest is not an object")
        expected_hash = manifest.pop("manifest_sha256")
        vectors = manifest.get("vectors")
        metadata = manifest.get("records")
        item_count = manifest.get("item_count")
        valid = bool(
            manifest.get("schema_version") == DENSE_INDEX_SCHEMA
            and manifest.get("model_identity") == LOCAL_DENSE_MODEL
            and manifest.get("model_revision") == "1"
            and manifest.get("network_policy") == "offline"
            and manifest.get("dimensions") == DENSE_DIMENSIONS
            and manifest.get("quantization") == "signed-int8-l2"
            and manifest.get("input_audit_head") == input_audit_head
            and manifest.get("legacy_audit_head") == legacy_audit_head
            and isinstance(item_count, int)
            and not isinstance(item_count, bool)
            and 0 <= item_count <= _MAX_INDEX_ITEMS
            and isinstance(vectors, dict)
            and set(vectors) == {"path", "byte_size", "sha256"}
            and vectors.get("path") == "vectors.bin"
            and vectors.get("byte_size") == 10 + item_count * DENSE_DIMENSIONS
            and isinstance(vectors.get("sha256"), str)
            and len(vectors["sha256"]) == 64
            and isinstance(metadata, dict)
            and set(metadata) == {"path", "byte_size", "sha256"}
            and metadata.get("path") == "records.json"
            and isinstance(metadata.get("byte_size"), int)
            and not isinstance(metadata.get("byte_size"), bool)
            and 3 <= metadata["byte_size"] <= _MAX_RECORD_BYTES
            and isinstance(metadata.get("sha256"), str)
            and len(metadata["sha256"]) == 64
            and isinstance(expected_hash, str)
            and len(expected_hash) == 64
            and expected_hash == sha256_bytes(canonical_json(manifest).encode("utf-8"))
        )
        manifest["manifest_sha256"] = expected_hash
        vector_path = directory / "vectors.bin"
        records_path = directory / "records.json"
        valid = bool(
            valid
            and not vector_path.is_symlink()
            and vector_path.is_file()
            and not records_path.is_symlink()
            and records_path.is_file()
            and vector_path.stat().st_size <= _MAX_VECTOR_BYTES
            and records_path.stat().st_size <= _MAX_RECORD_BYTES
            and vector_path.stat().st_size == vectors["byte_size"]
            and records_path.stat().st_size == metadata["byte_size"]
            and sha256_file(vector_path) == manifest["vectors"]["sha256"]
            and sha256_file(records_path) == manifest["records"]["sha256"]
        )
        if not valid:
            return {"ready": False, "reason": "binding_invalid", "results": []}
        records = json.loads(records_path.read_text(encoding="utf-8"))
        if (
            not isinstance(records, list)
            or len(records) != item_count
            or any(
                not isinstance(record, dict)
                or set(record)
                != {
                    "ordinal",
                    "knowledge_id",
                    "revision_id",
                    "scope",
                    "sensitivity",
                    "kind",
                    "tags",
                    "valid_from",
                    "valid_to",
                    "expires_at",
                }
                or record.get("ordinal") != ordinal
                or not isinstance(record.get("knowledge_id"), str)
                or not _KNOWLEDGE_ID.fullmatch(record["knowledge_id"])
                or not isinstance(record.get("revision_id"), str)
                or not _REVISION_ID.fullmatch(record["revision_id"])
                or record.get("scope") not in _SCOPES
                or record.get("sensitivity") not in _SENSITIVITY_ORDER
                or record.get("kind") not in _KNOWLEDGE_KINDS
                or not isinstance(record.get("tags"), list)
                or len(record["tags"]) > 64
                or any(not isinstance(tag, str) or not tag for tag in record["tags"])
                or any(
                    value is not None
                    and (
                        not isinstance(value, str)
                        or not _CANONICAL_TIMESTAMP.fullmatch(value)
                    )
                    for value in (
                        record.get("valid_from"),
                        record.get("valid_to"),
                        record.get("expires_at"),
                    )
                )
                for ordinal, record in enumerate(records)
            )
        ):
            raise ValueError("local dense record inventory is invalid")
        payload = vector_path.read_bytes()
        if payload[:4] != _VECTOR_MAGIC or len(payload) < 10:
            raise ValueError("local dense vector header is invalid")
        dimensions, count = struct.unpack(">HI", payload[4:10])
        if (
            dimensions != DENSE_DIMENSIONS
            or count != len(records)
            or len(payload) != 10 + count * dimensions
        ):
            raise ValueError("local dense vector inventory is invalid")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, struct.error):
        return {"ready": False, "reason": "index_invalid", "results": []}
    query_vector = dense_vector(query)
    scored: list[tuple[float, str, str]] = []
    maximum_sensitivity = _SENSITIVITY_ORDER.index(max_sensitivity)
    for ordinal, record in enumerate(records):
        if (
            record["scope"] != scope
            or _SENSITIVITY_ORDER.index(record["sensitivity"]) > maximum_sensitivity
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
            {"knowledge_id": knowledge_id, "revision_id": revision_id, "score": round(score, 6)}
            for score, knowledge_id, revision_id in scored[:limit]
        ],
    }


def rerank_candidates(
    query: str,
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    query_terms = set(search_terms(query))
    query_vector = dense_vector(query)
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for candidate in candidates:
        text = " ".join(
            str(candidate.get(field, "")) for field in ("title", "body", "semantic_key")
        )
        terms = set(search_terms(text))
        coverage = len(query_terms & terms) / max(1, len(query_terms))
        dense = vector_cosine(query_vector, dense_vector(text))
        feedback = float(candidate.get("feedback_utility", 0.0))
        contradiction_bonus = 0.05 if candidate.get("epistemic_state") == "contested" else 0.0
        score = coverage * 0.55 + dense * 0.35 + max(-1.0, min(1.0, feedback)) * 0.1
        score += contradiction_bonus
        ranked.append((score, str(candidate.get("knowledge_id", "")), candidate))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    output: list[dict[str, Any]] = []
    for rank, (score, _identity, candidate) in enumerate(ranked, start=1):
        output.append(
            {
                **candidate,
                "reranker_rank": rank,
                "reranker_score": round(score, 6),
                "reranker_profile": LOCAL_RERANKER_MODEL,
            }
        )
    return output


def detect_communities(
    node_ids: Iterable[str],
    relations: Iterable[dict[str, Any]],
    semantic_keys: dict[str, str | None],
) -> list[dict[str, Any]]:
    """Deterministic weighted label propagation with semantic bridge edges."""

    nodes = sorted(set(node_ids))
    adjacency: dict[str, dict[str, float]] = {node: {} for node in nodes}
    for relation in relations:
        left = relation.get("subject_knowledge_id")
        right = relation.get("object_knowledge_id")
        if left not in adjacency or right not in adjacency or left == right:
            continue
        weight = 2.0 if relation.get("predicate") in {"same_as", "alias_of"} else 1.0
        adjacency[left][right] = adjacency[left].get(right, 0.0) + weight
        adjacency[right][left] = adjacency[right].get(left, 0.0) + weight
    buckets: dict[str, list[str]] = defaultdict(list)
    for node, key in semantic_keys.items():
        normalized = normalize_identity_text(key or "")
        if len(normalized) >= 3:
            buckets[normalized[:8]].append(node)
    for members in buckets.values():
        if len(members) > 100:
            continue
        for index, left in enumerate(sorted(members)):
            for right in sorted(members)[index + 1 :]:
                adjacency[left][right] = adjacency[left].get(right, 0.0) + 0.35
                adjacency[right][left] = adjacency[right].get(left, 0.0) + 0.35
    labels = {node: node for node in nodes}
    for _ in range(25):
        changed = False
        for node in nodes:
            scores: dict[str, float] = defaultdict(float)
            for neighbor, weight in adjacency[node].items():
                scores[labels[neighbor]] += weight
            if not scores:
                continue
            selected = min(scores, key=lambda label: (-scores[label], label))
            if selected != labels[node]:
                labels[node] = selected
                changed = True
        if not changed:
            break
    grouped: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        grouped[labels[node]].append(node)
    communities = []
    for members in grouped.values():
        sorted_members = sorted(members)
        communities.append(
            {
                "community_id": stable_id("community", *sorted_members),
                "knowledge_ids": sorted_members,
                "algorithm": "weighted-label-propagation+semantic-bridges/1",
            }
        )
    return sorted(communities, key=lambda item: item["community_id"])
