from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_ASCII_TOKEN = re.compile(r"[a-zA-Z0-9]+(?:[-_.][a-zA-Z0-9]+)*")
_ARTICLE = re.compile(
    r"第\s*([〇零一二两三四五六七八九十百千万亿0-9]+)\s*条(?:\s*之\s*([〇零一二两三四五六七八九十百0-9]+))?"
)
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+")
_CANONICAL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_STOP_TERMS = {
    "什么",
    "如何",
    "是否",
    "哪些",
    "有关",
    "相关",
    "法律",
    "法规",
    "案件",
    "规定",
    "问题",
    "进行",
    "可以",
    "应当",
    "需要",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix: str, *parts: str, length: int = 24) -> str:
    payload = "\x00".join(parts).encode("utf-8")
    return f"{prefix}_{sha256_bytes(payload)[:length]}"


def canonical_date(value: str, *, field: str) -> str:
    if not _CANONICAL_DATE.fullmatch(value):
        raise ValueError(f"{field} must use canonical YYYY-MM-DD format: {value}")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"invalid {field}: {value}") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must use canonical YYYY-MM-DD format: {value}")
    return value


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00a0", " ")
    return _SPACE.sub(" ", text).strip()


def compact_text(text: str) -> str:
    return _PUNCT.sub("", normalize_text(text).lower())


def normalize_article_label(text: str) -> str | None:
    match = _ARTICLE.search(normalize_text(text))
    if not match:
        return None
    main = match.group(1)
    suffix = match.group(2)
    return f"第{main}条" + (f"之{suffix}" if suffix else "")


def article_pattern() -> re.Pattern[str]:
    return _ARTICLE


def cjk_ngrams(run: str, sizes: Iterable[int] = (2, 3)) -> list[str]:
    values: list[str] = []
    for size in sizes:
        if len(run) < size:
            continue
        values.extend(run[index : index + size] for index in range(len(run) - size + 1))
    if 1 < len(run) <= 12:
        values.append(run)
    return values


def search_terms(text: str, *, limit: int | None = None) -> list[str]:
    normalized = normalize_text(text).lower()
    terms: list[str] = []
    for run in _CJK_RUN.findall(normalized):
        terms.extend(cjk_ngrams(run))
    terms.extend(_ASCII_TOKEN.findall(normalized))

    article = normalize_article_label(normalized)
    if article:
        terms.append(compact_text(article))

    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if len(term) < 2 or term in _STOP_TERMS or term in seen:
            continue
        seen.add(term)
        unique.append(term)
        if limit is not None and len(unique) >= limit:
            break
    return unique


def fts_query(terms: Iterable[str]) -> str:
    safe = [term.replace('"', '""') for term in terms if term]
    return " OR ".join(f'"{term}"' for term in safe)


def excerpt(text: str, query: str, max_chars: int = 700) -> str:
    text = normalize_text(text)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    anchors = [term for term in search_terms(query, limit=12) if len(term) >= 2]
    offset = 0
    compact_characters: list[str] = []
    source_offsets: list[int] = []
    for source_offset, character in enumerate(text.lower()):
        if not _PUNCT.fullmatch(character):
            compact_characters.append(character)
            source_offsets.append(source_offset)
    compact = "".join(compact_characters)
    for anchor in anchors:
        found = compact.find(compact_text(anchor))
        if found >= 0:
            offset = source_offsets[found]
            break
    start = max(0, offset - max_chars // 4)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    value = ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
    if len(value) <= max_chars:
        return value
    if value.endswith("…") and max_chars > 1:
        return value[: max_chars - 1] + "…"
    return value[:max_chars]
