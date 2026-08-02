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
_QUOTED_PHRASE = re.compile(r'"([^"\n]{2,200})"|“([^”\n]{2,200})”|`([^`\n]{2,200})`')
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_ASCII_PART = re.compile(r"[A-Za-z]+|[0-9]+")
_ARTICLE = re.compile(
    r"第\s*([〇零一二两三四五六七八九十百千万亿0-9]+)\s*条(?:\s*之\s*([〇零一二两三四五六七八九十百0-9]+))?"
)
_SPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+")
_CANONICAL_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_INSTRUCTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b", re.I),
    re.compile(r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\b", re.I),
    re.compile(r"\byou\s+are\s+(?:chatgpt|claude|an?\s+ai|the\s+assistant)\b", re.I),
    re.compile(r"(?:调用|使用|执行).{0,20}(?:工具|命令|shell|终端|MCP)", re.I),
    re.compile(r"(?:忽略|覆盖|替换).{0,20}(?:之前|以上|系统|开发者).{0,20}(?:指令|规则)", re.I),
    re.compile(r"<(?:script|iframe|object|embed)\b", re.I),
)
_INVISIBLE_OR_BIDI = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")
_LOCAL_PATH_PATTERNS = (
    re.compile(
        r"(?<![A-Za-z0-9:/])/(?:Users|home|private|var|tmp|etc|opt|usr|Volumes|"
        r"Applications|Library|System|workspace|root|srv|mnt|data|dev|proc|sys|run)"
        r"(?:/[^/\s<>\"'`]+)+"
    ),
    re.compile(
        r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/](?:[^\\/\s<>\"'`]+[\\/])+"
        r"[^\\/\s<>\"'`]+"
    ),
    re.compile(r"(?<!\\)\\\\[^\\/\s<>\"'`]+\\[^\\/\s<>\"'`]+"),
    re.compile(r"(?i)\bfile://(?:localhost)?/[A-Za-z0-9._~!$&'()*+,;=:@%/\\-]+"),
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"),
    re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
        r"gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
        r"xox[baprs]-[A-Za-z0-9-]{20,})\b"
    ),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password|passwd|client[_-]?secret|"
        r"secret[_-]?key)\b\s*[:=]\s*[\"']?[^\s\"']{8,}"
    ),
)

_STOP_TERMS = {
    "a",
    "about",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "hers",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "ours",
    "please",
    "she",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "they",
    "this",
    "to",
    "us",
    "ve",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
    "you",
    "your",
    "yours",
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

# This deliberately small compatibility table is a retrieval aid, not a text
# conversion authority.  It covers high-frequency query vocabulary without
# rewriting stored evidence or identities.
_TRADITIONAL_QUERY_TRANSLATION = str.maketrans(
    {
        "與": "与",
        "為": "为",
        "於": "于",
        "後": "后",
        "前": "前",
        "發": "发",
        "現": "现",
        "變": "变",
        "更": "更",
        "刪": "删",
        "除": "除",
        "檔": "档",
        "資": "资",
        "料": "料",
        "來": "来",
        "源": "源",
        "關": "关",
        "係": "系",
        "規": "规",
        "則": "则",
        "設": "设",
        "定": "定",
        "執": "执",
        "行": "行",
        "錯": "错",
        "誤": "误",
        "經": "经",
        "驗": "验",
        "問": "问",
        "題": "题",
        "說": "说",
        "明": "明",
        "應": "应",
        "該": "该",
        "權": "权",
        "限": "限",
        "審": "审",
        "核": "核",
        "實": "实",
        "體": "体",
        "網": "网",
        "頁": "页",
        "專": "专",
        "倉": "仓",
        "庫": "库",
        "軟": "软",
        "數": "数",
        "據": "据",
        "層": "层",
        "級": "级",
        "節": "节",
        "點": "点",
        "標": "标",
        "總": "总",
        "結": "结",
        "衝": "冲",
        "突": "突",
        "過": "过",
        "期": "期",
        "當": "当",
        "歷": "历",
        "史": "史",
    }
)

_QUERY_SYNONYMS = {
    "当前": ("最新", "现行"),
    "最新": ("当前", "现行"),
    "删除": ("移除", "忘记"),
    "移除": ("删除", "忘记"),
    "更新": ("变更", "修改"),
    "变更": ("更新", "修改"),
    "配置": ("设定", "设置"),
    "错误": ("故障", "失败"),
    "故障": ("错误", "失败"),
    "步骤": ("流程", "过程"),
    "流程": ("步骤", "过程"),
    "约束": ("限制", "边界"),
    "关系": ("关联", "联系"),
    "定义": ("含义", "术语"),
    "例外": ("异常", "除外"),
    "原因": ("缘由",),
    "项目": ("工程",),
    "仓库": ("代码库",),
}

# Query-only lexical bridges are deliberately bounded and phrase based.  They
# improve deterministic cross-language discovery without translating stored
# evidence, changing source identity, or assigning Authority.  Index builders
# continue to use ``search_terms`` so a profile change does not silently alter
# durable derived-state inputs.
QUERY_EXPANSION_PROFILE = "deeplaw-deterministic-query-expansion/1"
_QUERY_CROSS_LANGUAGE_ALIASES = {
    "组织": ("organization",),
    "也称": ("known",),
    "别名": ("alias", "known"),
    "两位": ("two", "people"),
    "区分": ("distinguished",),
    "指什么": ("refer",),
    "证据准入": ("evidence", "admission"),
    "证据接纳": ("evidence", "admission"),
    "生产服务": ("production", "service"),
    "全球": ("worldwide",),
    "公共 API": ("public", "api"),
    "公共API": ("public", "api"),
    "诊断日志": ("diagnostic", "logs"),
    "保留政策": ("retention", "policies"),
    "保留期限": ("retention", "period"),
    "保留期": ("retention", "period"),
    "根据每项政策": ("according", "each", "policy"),
    "摘要": ("summarize", "summary"),
    "来源": ("source",),
    "有序步骤": ("ordered", "steps"),
    "工作流": ("workflow",),
    "时间线": ("timeline", "chronological"),
    "发生": ("happened", "event"),
    "协议修订": ("protocol", "revision"),
    "使用哪个": ("use",),
    "当前支持": ("currently", "support"),
    "当前": ("current",),
    "发布概览": ("release", "overview"),
    "引用": ("quote",),
    "精确颜色": ("exact", "color"),
    "验证徽章": ("verification", "badge"),
    "审阅完成": ("review", "completed"),
    "计划发布": ("publication", "scheduled"),
    "政策冲突": ("policy", "conflict"),
    "之间的冲突": ("conflict",),
    "冲突": ("conflict",),
    "比较": ("compare", "comparison"),
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
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def has_instruction_risk(text: str) -> bool:
    return bool(_INVISIBLE_OR_BIDI.search(text)) or any(
        pattern.search(text) for pattern in _INSTRUCTION_PATTERNS
    )


def assert_provider_output_safe(value: Any, *, interface: str) -> None:
    """Fail closed before a bounded Agent response can disclose paths or credentials."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            pending.extend(item.values())
            continue
        if isinstance(item, (list, tuple)):
            pending.extend(item)
            continue
        if not isinstance(item, str):
            continue
        if any(pattern.search(item) for pattern in _LOCAL_PATH_PATTERNS):
            raise PermissionError(f"{interface} output contains a local absolute path")
        if any(pattern.search(item) for pattern in _SECRET_PATTERNS):
            raise PermissionError(f"{interface} output contains secret-like material")
        if _INVISIBLE_OR_BIDI.search(item):
            raise PermissionError(f"{interface} output contains unsafe invisible Unicode")


def provider_safe_exception(error: Exception, *, interface: str) -> Exception:
    """Preserve useful failures while replacing messages that would cross a data boundary."""
    try:
        assert_provider_output_safe(str(error), interface=interface)
    except PermissionError:
        return RuntimeError(f"{interface} request failed closed; sensitive details omitted")
    return error


def strict_json_loads(value: str | bytes | bytearray) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = item
        return result

    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {constant}")

    return json.loads(
        value,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


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


def normalize_query_text(text: str) -> str:
    """Normalize query typography without changing stored canonical content."""
    normalized = normalize_text(text)
    for traditional, simplified in (
        ("軟體", "软件"),
        ("資料庫", "数据库"),
        ("程式碼", "代码"),
        ("設定檔", "配置文件"),
    ):
        normalized = normalized.replace(traditional, simplified)
    return normalized.translate(_TRADITIONAL_QUERY_TRANSLATION)


def query_phrases(text: str) -> list[str]:
    """Return bounded explicit phrases from quotes and inline-code markers."""
    normalized = normalize_query_text(text)
    phrases: list[str] = []
    seen: set[str] = set()
    for match in _QUOTED_PHRASE.finditer(normalized):
        phrase = normalize_text(next(value for value in match.groups() if value is not None))
        key = phrase.casefold()
        if key not in seen:
            seen.add(key)
            phrases.append(phrase)
        if len(phrases) == 8:
            break
    return phrases


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


def search_terms(
    text: str,
    *,
    limit: int | None = None,
    cover_tail: bool = False,
) -> list[str]:
    normalized_source = normalize_query_text(text)
    normalized = normalized_source.lower()
    terms: list[str] = []
    for run in _CJK_RUN.findall(normalized):
        terms.extend(cjk_ngrams(run))
    ascii_tokens = _ASCII_TOKEN.findall(normalized_source)
    for token in ascii_tokens:
        lowered = token.lower()
        terms.append(lowered)
        separated = re.sub(r"[-_.]+", " ", token)
        camel_parts = [
            part.lower()
            for component in separated.split()
            for part in _CAMEL_BOUNDARY.sub(" ", component).split()
        ]
        terms.extend(camel_parts)
        for part in camel_parts:
            terms.extend(_english_stems(part))
        # Version numbers, error codes and symbol paths remain searchable both
        # as an exact token and as their meaningful components.
        if any(separator in token for separator in "-_."):
            terms.extend(part.lower() for part in _ASCII_PART.findall(token))

    for phrase, synonyms in _QUERY_SYNONYMS.items():
        if phrase in normalized:
            terms.extend(synonyms)

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
    if limit is None or len(unique) <= limit:
        return unique
    if limit <= 0:
        return []
    if not cover_tail:
        return unique[:limit]
    if limit == 1:
        return [unique[0]]

    # Long Agent tasks often put the actual entity or acceptance condition
    # after a sizeable setup paragraph. Taking only the first N terms makes
    # those tail constraints undiscoverable. Sample the complete ordered term
    # stream instead, retaining both boundaries and deterministic coverage of
    # the middle without expanding the FTS query bound.
    last = len(unique) - 1
    indexes = [round(index * last / (limit - 1)) for index in range(limit)]
    return [unique[index] for index in indexes]


def query_expansion_terms(text: str) -> list[str]:
    """Return bounded deterministic cross-language aliases for a query only."""

    normalized = normalize_query_text(text).casefold()
    terms: list[str] = []
    seen: set[str] = set()
    for phrase, aliases in _QUERY_CROSS_LANGUAGE_ALIASES.items():
        if phrase not in normalized:
            continue
        for alias in aliases:
            if alias not in seen:
                seen.add(alias)
                terms.append(alias)
    return terms[:24]


def query_search_terms(
    text: str,
    *,
    limit: int | None = None,
    cover_tail: bool = False,
) -> list[str]:
    """Tokenize a query and reserve bounded capacity for explicit aliases."""

    expansions = query_expansion_terms(text)
    if limit is None:
        return list(dict.fromkeys((*search_terms(text), *expansions)))
    if limit <= 0:
        return []
    expansion_budget = min(len(expansions), max(1, limit // 3))
    base = search_terms(
        text,
        limit=max(0, limit - expansion_budget),
        cover_tail=cover_tail,
    )
    return list(dict.fromkeys((*base, *expansions[:expansion_budget])))[:limit]


def search_terms_v1(text: str) -> list[str]:
    """Reproduce the v0.6 tokenizer solely for additive migration verification."""
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
    return unique


def _english_stems(token: str) -> list[str]:
    """Return conservative search expansions, never a canonical word form."""
    if not token.isascii() or not token.isalpha() or len(token) < 5:
        return []
    values: list[str] = []
    if token.endswith("ies") and len(token) > 5:
        values.append(f"{token[:-3]}y")
    elif token.endswith("ing") and len(token) > 6:
        base = token[:-3]
        values.append(base)
        if len(base) > 2 and base[-1] == base[-2]:
            values.append(base[:-1])
    elif token.endswith(("ed", "es")) and len(token) > 5:
        values.append(token[:-2])
    elif token.endswith("s") and not token.endswith("ss") and len(token) > 4:
        values.append(token[:-1])
    return values


def fts_query(terms: Iterable[str]) -> str:
    safe = [term.replace('"', '""') for term in terms if term]
    return " OR ".join(f'"{term}"' for term in safe)


def excerpt(
    text: str,
    query: str,
    max_chars: int = 700,
    *,
    cover_query_tail: bool = False,
) -> str:
    text = normalize_text(text)
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    anchors = [
        term
        for term in search_terms(
            query,
            limit=12,
            cover_tail=cover_query_tail,
        )
        if len(term) >= 2
    ]
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
    if value.startswith("…") and not value.endswith("…") and max_chars > 1:
        return "…" + text[-(max_chars - 1) :]
    if value.endswith("…") and max_chars > 1:
        return value[: max_chars - 1] + "…"
    return value[:max_chars]
