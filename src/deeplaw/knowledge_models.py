from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from .util import canonical_json, sha256_bytes, stable_id

KNOWLEDGE_ASSET_SCHEMA = "deeplaw.knowledge-asset/v1"
KNOWLEDGE_CAPSULE_SCHEMA = "deeplaw.knowledge-capsule/v1"
KNOWLEDGE_SEARCH_SCHEMA = "deeplaw.knowledge-search/v1"
KNOWLEDGE_VERIFICATION_SCHEMA = "deeplaw.knowledge-verification/v1"

AssetKind = Literal[
    "constraint",
    "decision",
    "fact",
    "procedure",
    "rule",
    "experience",
    "lesson",
    "question",
    "reference",
]
MemoryTier = Literal["working", "project", "experience", "wisdom", "domain"]
AssetStatus = Literal["proposed", "active", "superseded", "revoked", "quarantined"]
VerificationLevel = Literal["unverified", "source_bound", "human_verified"]
TrustLevel = Literal["untrusted", "user_provided", "verified_source"]
Sensitivity = Literal["public", "internal", "private", "restricted"]
SourceKind = Literal[
    "document",
    "conversation",
    "tool_result",
    "code",
    "web",
    "database",
    "manual",
    "package",
]

ASSET_KINDS = frozenset(AssetKind.__args__)
MEMORY_TIERS = frozenset(MemoryTier.__args__)
ASSET_STATUSES = frozenset(AssetStatus.__args__)
VERIFICATION_LEVELS = frozenset(VerificationLevel.__args__)
TRUST_LEVELS = frozenset(TrustLevel.__args__)
USER_SETTABLE_TRUST_LEVELS = frozenset({"untrusted", "user_provided"})
SENSITIVITY_LEVELS = frozenset(Sensitivity.__args__)
SOURCE_KINDS = frozenset(SourceKind.__args__)

_MAX_IDENTIFIER_CHARS = 256
_MAX_TITLE_CHARS = 500
_MAX_STATEMENT_CHARS = 20_000
_MAX_LOCATOR_CHARS = 2_000
_MAX_TAGS = 32
_MAX_TAG_CHARS = 64
_MAX_SOURCE_REFS = 100
_MAX_WARNINGS = 32
_MAX_WARNING_CHARS = 500


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_timestamp(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise ValueError(f"{field} must be a bounded ISO-8601 timestamp")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_string(value: str, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized or normalized != value or len(normalized) > maximum:
        raise ValueError(f"{field} must be a non-empty canonical string")
    return normalized


def _canonical_optional_string(
    value: str | None,
    *,
    field: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    return _canonical_string(value, field=field, maximum=maximum)


def _stable_identifier(value: str, *, prefix: str, field: str) -> None:
    expected_prefix = f"{prefix}_"
    suffix = value[len(expected_prefix) :] if value.startswith(expected_prefix) else ""
    if len(suffix) != 24 or any(character not in "0123456789abcdef" for character in suffix):
        raise ValueError(f"{field} must be a canonical DeepLaw identifier")


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_id: str
    fragment_id: str
    locator: str
    quote_sha256: str

    def __post_init__(self) -> None:
        _canonical_string(
            self.source_id,
            field="source reference source_id",
            maximum=_MAX_IDENTIFIER_CHARS,
        )
        _stable_identifier(
            self.source_id,
            prefix="source",
            field="source reference source_id",
        )
        _canonical_string(
            self.fragment_id,
            field="source reference fragment_id",
            maximum=_MAX_IDENTIFIER_CHARS,
        )
        _stable_identifier(
            self.fragment_id,
            prefix="fragment",
            field="source reference fragment_id",
        )
        _canonical_string(
            self.locator,
            field="source reference locator",
            maximum=_MAX_LOCATOR_CHARS,
        )
        if len(self.quote_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.quote_sha256
        ):
            raise ValueError("source reference quote_sha256 must be lowercase SHA-256")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class KnowledgeAsset:
    asset_id: str
    vault_id: str
    kind: AssetKind
    memory_tier: MemoryTier
    title: str
    statement: str
    semantic_key: str | None
    status: AssetStatus
    verification: VerificationLevel
    trust: TrustLevel
    sensitivity: Sensitivity
    source_refs: tuple[SourceReference, ...]
    tags: tuple[str, ...]
    warnings: tuple[str, ...]
    created_at: str
    activated_at: str | None
    expires_at: str | None
    supersedes_asset_id: str | None
    origin_uri: str | None
    content_sha256: str

    def __post_init__(self) -> None:
        _canonical_string(self.asset_id, field="asset_id", maximum=_MAX_IDENTIFIER_CHARS)
        _canonical_string(self.vault_id, field="vault_id", maximum=_MAX_IDENTIFIER_CHARS)
        _stable_identifier(self.asset_id, prefix="asset", field="asset_id")
        _stable_identifier(self.vault_id, prefix="vault", field="vault_id")
        if self.kind not in ASSET_KINDS:
            raise ValueError("unsupported knowledge asset kind")
        if self.memory_tier not in MEMORY_TIERS:
            raise ValueError("unsupported memory tier")
        _canonical_string(self.title, field="asset title", maximum=_MAX_TITLE_CHARS)
        _canonical_string(
            self.statement,
            field="asset statement",
            maximum=_MAX_STATEMENT_CHARS,
        )
        _canonical_optional_string(
            self.semantic_key,
            field="asset semantic_key",
            maximum=_MAX_IDENTIFIER_CHARS,
        )
        if self.status not in ASSET_STATUSES:
            raise ValueError("unsupported knowledge asset status")
        if self.verification not in VERIFICATION_LEVELS:
            raise ValueError("unsupported verification level")
        if self.trust not in TRUST_LEVELS:
            raise ValueError("unsupported trust level")
        if self.sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError("unsupported sensitivity")
        if not isinstance(self.source_refs, tuple):
            raise ValueError("source_refs must be a tuple")
        if len(self.source_refs) > _MAX_SOURCE_REFS:
            raise ValueError("source_refs exceed the bound")
        if len({reference.fragment_id for reference in self.source_refs}) != len(
            self.source_refs
        ):
            raise ValueError("source_refs must not repeat a fragment")
        if self.verification == "source_bound" and not self.source_refs:
            raise ValueError("source_bound assets require source references")
        if self.verification == "human_verified" and self.status in {
            "proposed",
            "quarantined",
        }:
            raise ValueError("unreviewed asset states cannot claim human verification")
        if self.status == "active" and self.verification != "human_verified":
            raise ValueError("active assets require explicit human verification")
        if self.status == "active" and self.activated_at is None:
            raise ValueError("active assets require activated_at")
        if self.status in {"proposed", "quarantined"} and self.activated_at is not None:
            raise ValueError("unreviewed assets cannot have activated_at")
        if self.memory_tier == "working" and self.expires_at is None:
            raise ValueError("working-memory assets require expires_at")
        if len(self.tags) > _MAX_TAGS or len(set(self.tags)) != len(self.tags):
            raise ValueError("asset tags are duplicated or exceed the bound")
        for tag in self.tags:
            _canonical_string(tag, field="asset tag", maximum=_MAX_TAG_CHARS)
        if len(self.warnings) > _MAX_WARNINGS:
            raise ValueError("asset warnings exceed the bound")
        for warning in self.warnings:
            _canonical_string(
                warning,
                field="asset warning",
                maximum=_MAX_WARNING_CHARS,
            )
        canonical_timestamp(self.created_at, field="asset created_at")
        if self.activated_at is not None:
            canonical_timestamp(self.activated_at, field="asset activated_at")
        if self.expires_at is not None:
            canonical_timestamp(self.expires_at, field="asset expires_at")
        _canonical_optional_string(
            self.supersedes_asset_id,
            field="supersedes_asset_id",
            maximum=_MAX_IDENTIFIER_CHARS,
        )
        if self.supersedes_asset_id is not None:
            _stable_identifier(
                self.supersedes_asset_id,
                prefix="asset",
                field="supersedes_asset_id",
            )
        _canonical_optional_string(
            self.origin_uri,
            field="origin_uri",
            maximum=_MAX_LOCATOR_CHARS,
        )
        if len(self.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.content_sha256
        ):
            raise ValueError("asset content_sha256 must be lowercase SHA-256")
        expected_hash = asset_content_sha256(
            kind=self.kind,
            memory_tier=self.memory_tier,
            title=self.title,
            statement=self.statement,
            semantic_key=self.semantic_key,
            trust=self.trust,
            sensitivity=self.sensitivity,
            source_refs=self.source_refs,
            tags=self.tags,
            warnings=self.warnings,
            expires_at=self.expires_at,
            supersedes_asset_id=self.supersedes_asset_id,
            origin_uri=self.origin_uri,
        )
        if expected_hash != self.content_sha256:
            raise ValueError("asset content hash does not match canonical content")
        if stable_id("asset", self.vault_id, expected_hash) != self.asset_id:
            raise ValueError("asset ID does not match vault and canonical content")

    @property
    def uri(self) -> str:
        return f"deeplaw://{self.vault_id}/assets/{self.asset_id}"

    @property
    def directive_mode(self) -> str:
        if (
            self.status == "active"
            and self.verification == "human_verified"
            and self.kind in {"constraint", "procedure", "rule"}
        ):
            return "reviewed_instruction"
        return "data_only"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": KNOWLEDGE_ASSET_SCHEMA,
            "asset_id": self.asset_id,
            "uri": self.uri,
            "vault_id": self.vault_id,
            "kind": self.kind,
            "memory_tier": self.memory_tier,
            "title": self.title,
            "statement": self.statement,
            "semantic_key": self.semantic_key,
            "status": self.status,
            "verification": self.verification,
            "trust": self.trust,
            "sensitivity": self.sensitivity,
            "legal_authority": False,
            "directive_mode": self.directive_mode,
            "source_refs": [reference.to_dict() for reference in self.source_refs],
            "tags": list(self.tags),
            "warnings": list(self.warnings),
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "expires_at": self.expires_at,
            "supersedes_asset_id": self.supersedes_asset_id,
            "origin_uri": self.origin_uri,
            "content_sha256": self.content_sha256,
        }


def asset_content_sha256(
    *,
    kind: AssetKind,
    memory_tier: MemoryTier,
    title: str,
    statement: str,
    semantic_key: str | None,
    trust: TrustLevel,
    sensitivity: Sensitivity,
    source_refs: tuple[SourceReference, ...],
    tags: tuple[str, ...],
    warnings: tuple[str, ...],
    expires_at: str | None,
    supersedes_asset_id: str | None,
    origin_uri: str | None,
) -> str:
    payload = {
        "kind": kind,
        "memory_tier": memory_tier,
        "title": title,
        "statement": statement,
        "semantic_key": semantic_key,
        "trust": trust,
        "sensitivity": sensitivity,
        "source_refs": [reference.to_dict() for reference in source_refs],
        "tags": list(tags),
        "warnings": list(warnings),
        "expires_at": expires_at,
        "supersedes_asset_id": supersedes_asset_id,
        "origin_uri": origin_uri,
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


@dataclass(frozen=True, slots=True)
class KnowledgeCard:
    asset_id: str
    uri: str
    kind: AssetKind
    memory_tier: MemoryTier
    title: str
    excerpt: str
    semantic_key: str | None
    verification: VerificationLevel
    trust: TrustLevel
    sensitivity: Sensitivity
    directive_mode: str
    source_refs: tuple[SourceReference, ...]
    tags: tuple[str, ...]
    content_sha256: str
    score: float
    hit_reason: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("score")
        value["legal_authority"] = False
        value["source_refs"] = [reference.to_dict() for reference in self.source_refs]
        value["tags"] = list(self.tags)
        return value


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResponse:
    vault_id: str
    vault_revision: int
    query: str
    results: tuple[KnowledgeCard, ...]
    gaps: tuple[str, ...]
    total_excerpt_chars: int

    def to_dict(self) -> dict[str, Any]:
        results = []
        for rank, card in enumerate(self.results, start=1):
            result = card.to_dict()
            result["rank"] = rank
            results.append(result)
        return {
            "schema_version": KNOWLEDGE_SEARCH_SCHEMA,
            "vault_id": self.vault_id,
            "vault_revision": self.vault_revision,
            "query": self.query,
            "results": results,
            "ranking": {
                "method": "deterministic_lexical",
                "numeric_confidence_exposed": False,
            },
            "gaps": list(self.gaps),
            "total_excerpt_chars": self.total_excerpt_chars,
        }
