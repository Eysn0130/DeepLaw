from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import EvidenceCard, SearchRequest, SearchResponse
from .store import connect_readonly, release_info, resolve_active_database, verify_release_artifact
from .util import (
    article_pattern,
    canonical_date,
    compact_text,
    excerpt,
    fts_query,
    normalize_article_label,
    normalize_text,
    search_terms,
    sha256_bytes,
)

SEARCH_RESPONSE_SCHEMA = "deeplaw.search-response/v1"
EVIDENCE_CARD_SCHEMA = "deeplaw.legal-evidence-card/v1"
_TITLE_QUALIFIER = re.compile(r"[（(][^）)]{1,40}[）)]")
_QUERY_VERSION_SUFFIX = re.compile(
    r"(?:"
    r"\d{4}年?(?:修正|修订|修改|施行|版)?|"
    r"现行(?:有效|整合文本|版本)?|"
    r"最新版本"
    r")$"
)


def _simplify_document_key(value: str) -> str:
    return value.replace("关于", "").replace("的", "")


def _document_query_keys(title: str) -> tuple[tuple[str, int], ...]:
    raw = compact_text(title)
    core = compact_text(_TITLE_QUALIFIER.sub("", title))
    keys: dict[str, int] = {raw: 3, core: 3}
    prefix = compact_text("中华人民共和国")
    for key in (raw, core):
        if key.startswith(prefix) and len(key) > len(prefix):
            keys[key[len(prefix) :]] = max(keys.get(key[len(prefix) :], 0), 2)
    for suffix in (
        "实施细则",
        "管理办法",
        "办法",
        "条例",
        "规定",
        "解释",
        "决定",
        "意见",
        "通知",
        "公告",
    ):
        if core.endswith(suffix) and len(core) - len(suffix) >= 4:
            keys[core[: -len(suffix)]] = max(keys.get(core[: -len(suffix)], 0), 1)
    for key, priority in tuple(keys.items()):
        simplified = _simplify_document_key(key)
        if len(simplified) >= 2:
            keys[simplified] = max(keys.get(simplified, 0), min(priority, 2))
    return tuple((key, priority) for key, priority in keys.items() if len(key) >= 2)


def _target_query_forms(query: str) -> tuple[str, ...]:
    forms: dict[str, None] = {}
    compact = compact_text(query)
    for value in (compact, _simplify_document_key(compact)):
        if len(value) >= 2:
            forms[value] = None
        without_version = _QUERY_VERSION_SUFFIX.sub("", value)
        if len(without_version) >= 4:
            forms[without_version] = None
    return tuple(forms)


class DeepLaw:
    def __init__(self, database: str | Path | None = None, *, home: str | Path | None = None):
        self.database = resolve_active_database(explicit_db=database, home=home)
        self.artifact = verify_release_artifact(self.database)
        self.connection = connect_readonly(self.database)
        self.info = release_info(self.connection)
        if self.info.get("schema_version") != "deeplaw.release/v1":
            raise RuntimeError(
                f"unsupported DeepLaw release schema: {self.info.get('schema_version')}"
            )
        self.release_id = str(self.info["release_id"])
        if self.artifact.get("release_id") != self.release_id:
            self.connection.close()
            raise RuntimeError("release database metadata does not match release.json")
        release = self.info.get("release", {})
        artifact_release = {
            key: value for key, value in self.artifact.items() if key != "database_sha256"
        }
        if (
            not isinstance(release, dict)
            or release != artifact_release
            or self.info.get("document_count") != self.artifact.get("document_count")
            or self.info.get("segment_count") != self.artifact.get("segment_count")
        ):
            self.connection.close()
            raise RuntimeError("release database metadata does not match release.json")
        self.temporal_metadata_verified = release.get("temporal_status") == "verified"
        self.document_identifiers = self._load_document_identifiers()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DeepLaw:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def release_info(self) -> dict[str, Any]:
        value = dict(self.info)
        value["release"] = dict(self.artifact)
        value["database_sha256"] = self.artifact["database_sha256"]
        return value

    def search(self, request: SearchRequest) -> SearchResponse:
        request = request.normalized()
        if not request.query:
            raise ValueError("query is required")
        if request.as_of:
            canonical_date(request.as_of, field="as_of")

        route = self._route(request)
        candidates = self._candidate_rows(request, route)
        evidence: list[EvidenceCard] = []
        used_characters = 0
        seen: set[tuple[str, str | None]] = set()
        result_limit = min(request.limit, 3) if route in {"navigation", "exact"} else request.limit
        for row in candidates:
            dedupe_key = (
                (row["document_id"], None)
                if route == "navigation"
                else (row["document_id"], row["article_label"] or row["segment_id"])
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            budget = min(800 if route != "navigation" else 320, request.max_chars - used_characters)
            if budget < 100:
                break
            card = self._card_from_row(row, request, route=route, max_excerpt_chars=budget)
            used_characters += len(card.excerpt)
            evidence.append(card)
            if len(evidence) >= result_limit:
                break

        notices: list[str] = [
            "检索结果是研究证据候选，不等同于本案法律适用结论。",
            "DeepLaw 未使用模型记忆、自动 Web 回退或向量 top-k 注入。",
        ]
        if any(card.temporal_review_required for card in evidence):
            notices.append("至少一项法源缺少完整效力元数据，正式引用前必须复核时效。")
        if any(card.extraction_review_required for card in evidence):
            notices.append("至少一项证据来自 OCR 或存在解析警告，引用前必须对照原件。")
        if not evidence:
            notices.append("当前 release 未找到足够证据；这不表示相关法律不存在。")

        next_questions: tuple[str, ...] = ()
        if route == "navigation":
            next_questions = (
                "请指定法条、文号或行为发生日期。",
                "可继续选择构成要件、立案追诉、程序证据或资金监管规则。",
            )

        return SearchResponse(
            schema_version=SEARCH_RESPONSE_SCHEMA,
            release_id=self.release_id,
            mode=route,
            query_plan={
                "purpose": request.purpose,
                "route": route,
                "channels": ["exact_metadata", "article_locator", "chinese_fts"],
                "as_of": request.as_of,
                "document_types": list(request.document_types),
                "max_evidence": result_limit,
                "max_chars": request.max_chars,
                "vector_used": False,
                "wiki_used": False,
            },
            evidence=tuple(evidence),
            notices=tuple(notices),
            next_questions=next_questions,
            total_excerpt_chars=used_characters,
        )

    def get(self, segment_id: str, *, max_chars: int = 6000) -> dict[str, Any]:
        max_chars = max(500, min(max_chars, 12000))
        row = self.connection.execute(
            """
            SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                   d.document_number, d.jurisdiction, d.promulgated_on,
                   d.official_source, d.source_sha256, d.effective_from, d.effective_to,
                   d.status, d.note, d.extraction_method, d.extraction_version,
                   d.extraction_configuration_json, d.extraction_review_required,
                   d.extraction_warnings_json
            FROM segments s JOIN documents d USING(document_id)
            WHERE s.segment_id = ?
            """,
            (segment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown segment_id: {segment_id}")
        text = row["text"]
        truncated = len(text) > max_chars
        return {
            "schema_version": "deeplaw.segment/v1",
            "release_id": self.release_id,
            "receipt_id": self._receipt_id(row),
            "segment_id": row["segment_id"],
            "document_id": row["document_id"],
            "title": row["title"],
            "document_number": row["document_number"],
            "jurisdiction": row["jurisdiction"],
            "promulgated_on": row["promulgated_on"],
            "document_type": row["document_type"],
            "issuer": row["issuer"],
            "authority_rank": row["authority_rank"],
            "official_source": row["official_source"],
            "source_sha256": row["source_sha256"],
            "segment_sha256": row["text_sha256"],
            "ordinal": row["ordinal"],
            "kind": row["kind"],
            "heading": row["heading"],
            "article_label": row["article_label"],
            "part_index": row["part_index"],
            "page_start": row["page_start"],
            "page_end": row["page_end"],
            "paragraph_start": row["paragraph_start"],
            "paragraph_end": row["paragraph_end"],
            "text": text[:max_chars],
            "truncated": truncated,
            "effective_from": row["effective_from"],
            "effective_to": row["effective_to"],
            "status": row["status"],
            "temporal_review_required": self._temporal_review_required(row),
            "extraction_method": row["extraction_method"],
            "extraction_version": row["extraction_version"],
            "extraction_configuration": json.loads(row["extraction_configuration_json"]),
            "extraction_review_required": bool(row["extraction_review_required"]),
            "extraction_warnings": json.loads(row["extraction_warnings_json"]),
        }

    def verify(self, segment_id: str, receipt_id: str) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT s.*, d.source_sha256, d.title, d.official_source
            FROM segments s JOIN documents d USING(document_id)
            WHERE s.segment_id = ?
            """,
            (segment_id,),
        ).fetchone()
        if row is None:
            return {"valid": False, "reason": "unknown_segment", "release_id": self.release_id}
        actual_text_hash = sha256_bytes(row["text"].encode("utf-8"))
        if actual_text_hash != row["text_sha256"]:
            return {
                "valid": False,
                "reason": "segment_hash_mismatch",
                "release_id": self.release_id,
            }
        expected = self._receipt_id(row)
        return {
            "valid": expected == receipt_id,
            "reason": "verified" if expected == receipt_id else "receipt_mismatch",
            "release_id": self.release_id,
            "segment_id": segment_id,
            "source_sha256": row["source_sha256"],
            "segment_sha256": row["text_sha256"],
        }

    def _route(self, request: SearchRequest) -> str:
        if request.purpose != "auto":
            if request.purpose == "broad_topic":
                return "navigation"
            if request.purpose in {"exact_citation", "citation_verify", "as_of_version"}:
                return "exact"
            return "research"
        if normalize_article_label(request.query):
            return "exact"
        compact = compact_text(request.query)
        if 1 < len(compact) <= 8 and not any(
            token in request.query for token in ("如何", "是否", "为什么", "构成", "依据", "适用")
        ):
            return "navigation"
        return "research"

    def _load_document_identifiers(
        self,
    ) -> tuple[tuple[str, tuple[tuple[str, int], ...]], ...]:
        values: list[tuple[str, tuple[tuple[str, int], ...]]] = []
        rows = self.connection.execute(
            "SELECT document_id, title, document_number, aliases_json FROM documents"
        ).fetchall()
        for row in rows:
            identifiers = dict(_document_query_keys(row["title"]))
            if row["document_number"]:
                identifiers[compact_text(row["document_number"])] = 3
            for alias in json.loads(row["aliases_json"]):
                if normalized := compact_text(alias):
                    identifiers[normalized] = 3
            values.append(
                (
                    row["document_id"],
                    tuple(
                        sorted(
                            (identifier, priority)
                            for identifier, priority in identifiers.items()
                            if len(identifier) >= 2
                        )
                    ),
                )
            )
        return tuple(values)

    def _target_document_ids(self, query: str) -> tuple[str, ...]:
        normalized_query = normalize_text(query)
        article_match = article_pattern().search(normalized_query)
        target_query = (
            normalized_query[: article_match.start()] if article_match else normalized_query
        )
        query_forms = set(_target_query_forms(target_query))
        matches: dict[str, int] = {}
        for document_id, identifiers in self.document_identifiers:
            priority = max(
                (
                    identifier_priority
                    for identifier, identifier_priority in identifiers
                    if identifier in query_forms
                ),
                default=0,
            )
            if priority:
                matches[document_id] = priority
        if not matches:
            return ()
        best_priority = max(matches.values())
        return tuple(
            sorted(
                document_id
                for document_id, priority in matches.items()
                if priority == best_priority
            )[:100]
        )

    def _temporal_review_required(self, row: sqlite3.Row) -> bool:
        status = str(row["status"])
        return (
            not self.temporal_metadata_verified
            or not status.startswith("verified_")
            or not row["effective_from"]
        )

    def _candidate_rows(self, request: SearchRequest, route: str) -> list[sqlite3.Row]:
        terms = search_terms(request.query, limit=36)
        query = fts_query(terms)
        filters: list[str] = []
        parameters: list[Any] = []
        resolved_targets = self._target_document_ids(request.query) if route == "exact" else ()
        if route == "exact" and not resolved_targets:
            return []
        if resolved_targets:
            placeholders = ",".join("?" for _ in resolved_targets)
            filters.append(f"d.document_id IN ({placeholders})")
            parameters.extend(resolved_targets)
        if request.document_types:
            placeholders = ",".join("?" for _ in request.document_types)
            filters.append(f"d.document_type IN ({placeholders})")
            parameters.extend(request.document_types)
        if request.as_of:
            filters.append("(d.effective_from IS NULL OR d.effective_from <= ?)")
            parameters.append(request.as_of)
            filters.append("(d.effective_to IS NULL OR d.effective_to > ?)")
            parameters.append(request.as_of)
        where_suffix = "" if not filters else " AND " + " AND ".join(filters)

        rows: list[sqlite3.Row] = []
        if query:
            rows.extend(
                self.connection.execute(
                    f"""
                    SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                           d.document_number, d.jurisdiction, d.promulgated_on,
                           d.official_source, d.source_sha256, d.effective_from,
                           d.effective_to, d.status, d.note,
                           d.extraction_method, d.extraction_version,
                           d.extraction_configuration_json,
                           d.extraction_review_required, d.extraction_warnings_json,
                           bm25(segment_search, 0.0, 8.0, 2.0, 5.0) AS fts_rank,
                           'chinese_fts' AS channel
                    FROM segment_search
                    JOIN segments s ON s.segment_id = segment_search.segment_id
                    JOIN documents d USING(document_id)
                    WHERE segment_search MATCH ? {where_suffix}
                    ORDER BY fts_rank ASC, d.authority_rank DESC, s.ordinal ASC
                    LIMIT 100
                    """,
                    (query, *parameters),
                ).fetchall()
            )

        article = normalize_article_label(request.query)
        exact_rows: list[sqlite3.Row] = []
        if article:
            article_filters = list(filters)
            article_filters.append("REPLACE(s.article_label, ' ', '') = REPLACE(?, ' ', '')")
            article_params = [*parameters, article]
            exact_suffix = " AND ".join(article_filters)
            exact_rows = self.connection.execute(
                f"""
                SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                       d.document_number, d.jurisdiction, d.promulgated_on,
                       d.official_source, d.source_sha256, d.effective_from,
                       d.effective_to, d.status, d.note,
                       d.extraction_method, d.extraction_version,
                       d.extraction_configuration_json,
                       d.extraction_review_required, d.extraction_warnings_json,
                       -1000.0 AS fts_rank, 'article_exact' AS channel
                FROM segments s JOIN documents d USING(document_id)
                WHERE {exact_suffix}
                ORDER BY d.authority_rank DESC, s.ordinal ASC
                LIMIT 50
                """,
                tuple(article_params),
            ).fetchall()

        title_compact = compact_text(request.query)
        title_rows: list[sqlite3.Row] = []
        if len(title_compact) >= 4:
            title_where = "(d.normalized_title LIKE ? OR d.normalized_names LIKE ?)"
            title_parameters: list[Any] = [f"%{title_compact}%", f"%{title_compact}%"]
            document_filter = "" if not filters else " AND " + " AND ".join(filters)
            title_rows = self.connection.execute(
                f"""
                SELECT s.*, d.title, d.document_type, d.issuer, d.authority_rank,
                       d.document_number, d.jurisdiction, d.promulgated_on,
                       d.official_source, d.source_sha256, d.effective_from,
                       d.effective_to, d.status, d.note,
                       d.extraction_method, d.extraction_version,
                       d.extraction_configuration_json,
                       d.extraction_review_required, d.extraction_warnings_json,
                       -500.0 AS fts_rank, 'title_exact' AS channel
                FROM segments s
                JOIN documents d USING(document_id)
                WHERE {title_where} {document_filter}
                ORDER BY d.authority_rank DESC, s.ordinal ASC
                LIMIT 50
                """,
                (*title_parameters, *parameters),
            ).fetchall()

        merged: dict[str, sqlite3.Row] = {}
        for row in [*exact_rows, *title_rows, *rows]:
            merged.setdefault(row["segment_id"], row)
        query_term_set = set(terms)
        compact_query = compact_text(request.query)

        def score(row: sqlite3.Row) -> tuple[float, int, int]:
            text_terms = set(
                search_terms(f"{row['title']} {row['article_label'] or ''} {row['text']}")
            )
            title_terms = set(search_terms(row["title"]))
            coverage = len(query_term_set & text_terms) / max(1, len(query_term_set))
            title_coverage = len(query_term_set & title_terms) / max(1, len(query_term_set))
            explicit_title_match = any(
                key in compact_query for key, _ in _document_query_keys(row["title"])
            )
            document_type_match = (
                4.0
                if row["document_type"] == "judicial_interpretation" and "解释" in request.query
                else 0.0
            )
            channel_boost = {
                "article_exact": 5.0,
                "title_exact": 3.0,
                "chinese_fts": 0.0,
            }.get(row["channel"], 0.0)
            authority = row["authority_rank"] / 100.0
            raw_rank = float(row["fts_rank"])
            fts_component = min(1.5, max(0.0, -raw_rank) * 100_000)
            total = (
                channel_boost
                + coverage * 4.0
                + title_coverage * 2.0
                + (4.0 if explicit_title_match else 0.0)
                + document_type_match
                + authority * 0.4
                + fts_component
            )
            return (total, row["authority_rank"], -row["ordinal"])

        ranked = sorted(merged.values(), key=score, reverse=True)
        document_matches: dict[str, int] = {}
        for row in ranked:
            matched_length = max(
                (
                    len(key)
                    for key, _ in _document_query_keys(row["title"])
                    if key in compact_query
                ),
                default=0,
            )
            if matched_length:
                document_matches[row["document_id"]] = max(
                    document_matches.get(row["document_id"], 0), matched_length
                )
        target_documents: set[str] = set(resolved_targets)
        if not target_documents and route == "exact" and title_rows:
            target_documents = {row["document_id"] for row in title_rows}
        elif not target_documents and document_matches:
            best_length = max(document_matches.values())
            target_documents = {
                document_id
                for document_id, matched_length in document_matches.items()
                if matched_length == best_length
            }
        if route == "exact" and target_documents:
            ranked = [row for row in ranked if row["document_id"] in target_documents]
        if route == "exact" and article:
            ranked = [
                row
                for row in ranked
                if compact_text(row["article_label"] or "") == compact_text(article)
            ]
        return ranked[:100]

    def _card_from_row(
        self,
        row: sqlite3.Row,
        request: SearchRequest,
        *,
        route: str,
        max_excerpt_chars: int,
    ) -> EvidenceCard:
        hit_reason = {
            "article_exact": "精确命中条款编号",
            "title_exact": "精确命中文件题名",
            "chinese_fts": "中文词元召回后经权威等级与覆盖率重排",
        }.get(row["channel"], "结构化检索命中")
        if route == "navigation":
            locator = row["article_label"] or row["heading"] or "文档导航"
            prefix = f"{locator}："
            if len(prefix) >= max_excerpt_chars:
                text_excerpt = prefix[:max_excerpt_chars]
            else:
                text_excerpt = prefix + excerpt(
                    row["text"],
                    request.query,
                    max_chars=max_excerpt_chars - len(prefix),
                )
        else:
            text_excerpt = excerpt(row["text"], request.query, max_chars=max_excerpt_chars)
        rank = float(row["fts_rank"])
        channel_score = {"article_exact": 1.0, "title_exact": 0.9}.get(
            row["channel"], min(0.8, max(0.0, -rank) * 100_000)
        )
        score = round(channel_score + row["authority_rank"] / 1000.0, 6)
        return EvidenceCard(
            schema_version=EVIDENCE_CARD_SCHEMA,
            release_id=self.release_id,
            receipt_id=self._receipt_id(row),
            segment_id=row["segment_id"],
            document_id=row["document_id"],
            title=row["title"],
            document_number=row["document_number"],
            jurisdiction=row["jurisdiction"],
            promulgated_on=row["promulgated_on"],
            document_type=row["document_type"],
            issuer=row["issuer"],
            authority_rank=row["authority_rank"],
            official_source=row["official_source"],
            source_sha256=row["source_sha256"],
            segment_sha256=row["text_sha256"],
            score=score,
            hit_reason=hit_reason,
            excerpt=text_excerpt,
            article_label=row["article_label"],
            heading=row["heading"],
            effective_from=row["effective_from"],
            effective_to=row["effective_to"],
            status=row["status"],
            page_start=row["page_start"],
            page_end=row["page_end"],
            paragraph_start=row["paragraph_start"],
            paragraph_end=row["paragraph_end"],
            temporal_review_required=self._temporal_review_required(row),
            extraction_method=row["extraction_method"],
            extraction_configuration=tuple(
                json.loads(row["extraction_configuration_json"])
            ),
            extraction_review_required=bool(row["extraction_review_required"]),
            extraction_warnings=tuple(json.loads(row["extraction_warnings_json"])),
        )

    def _receipt_id(self, row: sqlite3.Row) -> str:
        payload = {
            "release_id": self.release_id,
            "document_id": row["document_id"],
            "segment_id": row["segment_id"],
            "source_sha256": row["source_sha256"],
            "segment_sha256": row["text_sha256"],
        }
        digest = sha256_bytes(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
        return f"lawrcpt_{digest[:32]}"


def response_json(value: Any) -> str:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
