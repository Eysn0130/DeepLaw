from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from .util import canonical_date, canonical_json, normalize_text, stable_id

__all__ = [
    "MAX_OBLIGATIONS",
    "MAX_QUERY_CHARS",
    "MAX_QUERY_CUES_PER_OBLIGATION",
    "QUERY_PLAN_SCHEMA",
    "ObligationId",
    "ObligationRole",
    "PlanPurpose",
    "PlanRoute",
    "QueryObligation",
    "QueryPlan",
    "compile_query_plan",
]

QUERY_PLAN_SCHEMA = "deeplaw.query-plan/v1"
MAX_QUERY_CHARS = 8000
MAX_OBLIGATIONS = 9
MAX_QUERY_CUES_PER_OBLIGATION = 8
MAX_QUERY_CUE_CHARS = 64

_PLAN_ID = re.compile(r"^lawplan_[0-9a-f]{32}$")
_LEGAL_LOCATOR = re.compile(
    r"第\s*[〇零一二两三四五六七八九十百千万亿0-9]+\s*(?:编|章|节|条|款|项)"
)


class PlanPurpose(StrEnum):
    AUTO = "auto"
    EXACT_CITATION = "exact_citation"
    AS_OF_VERSION = "as_of_version"
    ELEMENTS = "elements"
    LEGAL_ISSUE_SCREEN = "legal_issue_screen"
    CITATION_VERIFY = "citation_verify"
    BROAD_TOPIC = "broad_topic"


class PlanRoute(StrEnum):
    EXACT = "exact"
    NAVIGATION = "navigation"
    RESEARCH = "research"


class ObligationId(StrEnum):
    EXACT_CITATION = "exact_citation"
    PRIMARY_RULE = "primary_rule"
    TEMPORAL_STATUS_VERSION = "temporal_status_version"
    ELEMENTS_DEFINITIONS = "elements_definitions"
    INTERPRETATION = "interpretation"
    PROCEDURE = "procedure"
    THRESHOLD_STANDARD = "threshold_standard"
    EXCEPTIONS_COUNTEREVIDENCE = "exceptions_counterevidence"
    CASE_REFERENCE = "case_reference"


class ObligationRole(StrEnum):
    IDENTITY = "identity"
    SUPPORT = "support"
    TEMPORAL = "temporal"
    COUNTEREVIDENCE = "counterevidence"
    REFERENCE = "reference"


_OBLIGATION_ROLES = {
    ObligationId.EXACT_CITATION: ObligationRole.IDENTITY,
    ObligationId.PRIMARY_RULE: ObligationRole.SUPPORT,
    ObligationId.TEMPORAL_STATUS_VERSION: ObligationRole.TEMPORAL,
    ObligationId.ELEMENTS_DEFINITIONS: ObligationRole.SUPPORT,
    ObligationId.INTERPRETATION: ObligationRole.SUPPORT,
    ObligationId.PROCEDURE: ObligationRole.SUPPORT,
    ObligationId.THRESHOLD_STANDARD: ObligationRole.SUPPORT,
    ObligationId.EXCEPTIONS_COUNTEREVIDENCE: ObligationRole.COUNTEREVIDENCE,
    ObligationId.CASE_REFERENCE: ObligationRole.REFERENCE,
}
_OBLIGATION_ORDER = tuple(ObligationId)
_OBLIGATION_ORDER_INDEX = {
    obligation_id: index for index, obligation_id in enumerate(_OBLIGATION_ORDER)
}

_PRIMARY_TERMS = ("法律依据", "依据", "规则", "规定", "权利", "义务", "责任")
_TEMPORAL_TERMS = (
    "施行日期",
    "生效日期",
    "何时生效",
    "是否有效",
    "是否仍然有效",
    "仍然有效",
    "当前有效",
    "当前适用",
    "当前是否适用",
    "是否仍适用",
    "仍然适用",
    "适用时点",
    "修订前",
    "修订后",
    "修正前",
    "修正后",
    "有效期",
    "效力",
    "现行",
    "当时",
    "截至",
    "是否废止",
    "是否失效",
    "停止执行",
    "终止执行",
    "哪个版本",
    "哪一版本",
    "最新版本",
    "现行版本",
    "历史版本",
)
_TEMPORAL_PATTERNS = (
    re.compile(r"(?:当前|现在|如今|仍然?|还|继续).{0,6}(?:有效|适用|施行|执行|使用|用)"),
    re.compile(r"(?:是否|能否|还能否).{0,6}(?:有效|适用|施行|执行|使用|用)"),
    re.compile(r"不再.{0,4}(?:有效|适用|施行|执行|使用|用)"),
    re.compile(r"(?:被|由).{0,8}(?:替代|替换|取代|取而代之)"),
    re.compile(r"(?:是否|已经|现已|曾经|已|何时).{0,6}(?:废止|废除|失效|作废|撤销|生效)"),
    re.compile(r"(?:废止|废除|失效|作废|撤销).{0,4}(?:吗|没有|是否)"),
    re.compile(r"(?:修订|修正|修改).{0,4}(?:前|后|以来|之前|之后|版本|日期|时间)"),
    re.compile(r"(?:是否|还是|是).{0,3}最新"),
)
_ELEMENTS_TERMS = (
    "构成要件",
    "成立条件",
    "适用范围",
    "要件",
    "定义",
    "概念",
    "是指",
    "何为",
)
_INTERPRETATION_TERMS = ("如何理解", "怎么理解", "含义", "释义", "解释", "认定")
_PROCEDURE_TERMS = (
    "程序",
    "管辖",
    "期限",
    "时限",
    "立案",
    "受理",
    "申请",
    "审查",
    "举证",
    "执行",
)
_THRESHOLD_STANDARD_TERMS = (
    "立案追诉标准",
    "立案标准",
    "追诉标准",
    "数额标准",
    "金额标准",
    "数量标准",
    "入罪标准",
    "定罪标准",
    "量刑标准",
    "数额较大标准",
    "数额巨大标准",
    "数额特别巨大标准",
)
_COUNTEREVIDENCE_TERMS = (
    "例外",
    "除外",
    "但书",
    "不适用",
    "除非",
    "另有规定",
    "不包括",
    "但是",
    "限制",
    "排除",
    "反证",
    "冲突",
    "相反",
)
_CASE_REFERENCE_TERMS = (
    "指导性案例",
    "公报案例",
    "裁判文书",
    "类案",
    "案例",
    "判决",
    "裁判",
    "案号",
)
_EXACT_TERMS = ("精确引用", "引用核验", "核验引用", "出处", "原文")


@dataclass(frozen=True, slots=True)
class QueryObligation:
    id: ObligationId
    role: ObligationRole
    required: bool
    query_cues: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.role is not _OBLIGATION_ROLES[self.id]:
            raise ValueError(f"invalid role for obligation {self.id.value}: {self.role.value}")
        if not isinstance(self.required, bool):
            raise ValueError("obligation required must be a boolean")
        if len(self.query_cues) > MAX_QUERY_CUES_PER_OBLIGATION:
            raise ValueError("obligation query cues exceed the closed plan bound")
        if len(set(self.query_cues)) != len(self.query_cues):
            raise ValueError("obligation query cues must be unique")
        if any(not cue or len(cue) > MAX_QUERY_CUE_CHARS for cue in self.query_cues):
            raise ValueError("obligation query cue is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.value,
            "role": self.role.value,
            "required": self.required,
            "query_cues": list(self.query_cues),
        }


@dataclass(frozen=True, slots=True)
class QueryPlan:
    schema_version: str
    plan_id: str
    query: str
    purpose: PlanPurpose
    route: PlanRoute
    as_of: str | None
    obligations: tuple[QueryObligation, ...]

    max_query_chars: ClassVar[int] = MAX_QUERY_CHARS
    max_obligations: ClassVar[int] = MAX_OBLIGATIONS
    max_query_cues_per_obligation: ClassVar[int] = MAX_QUERY_CUES_PER_OBLIGATION

    def __post_init__(self) -> None:
        if self.schema_version != QUERY_PLAN_SCHEMA:
            raise ValueError(f"unsupported query plan schema: {self.schema_version}")
        if not _PLAN_ID.fullmatch(self.plan_id):
            raise ValueError("query plan ID is invalid")
        if not self.query or self.query != normalize_text(self.query):
            raise ValueError("query plan query must be non-empty normalized text")
        if len(self.query) > MAX_QUERY_CHARS:
            raise ValueError(f"query must not exceed {MAX_QUERY_CHARS} characters")
        if self.as_of is not None:
            canonical_date(self.as_of, field="as_of")
        if not self.obligations or len(self.obligations) > MAX_OBLIGATIONS:
            raise ValueError("query plan obligations exceed the closed plan bound")
        obligation_ids = tuple(obligation.id for obligation in self.obligations)
        if len(set(obligation_ids)) != len(obligation_ids):
            raise ValueError("query plan obligation IDs must be unique")
        if obligation_ids != tuple(
            sorted(obligation_ids, key=_OBLIGATION_ORDER_INDEX.__getitem__)
        ):
            raise ValueError("query plan obligations must use canonical order")
        expected_id = _make_plan_id(
            query=self.query,
            purpose=self.purpose,
            route=self.route,
            as_of=self.as_of,
            obligations=self.obligations,
        )
        if self.plan_id != expected_id:
            raise ValueError("query plan ID does not match its canonical payload")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "plan_id": self.plan_id,
            "query": self.query,
            "purpose": self.purpose.value,
            "route": self.route.value,
            "as_of": self.as_of,
            "obligations": [obligation.to_dict() for obligation in self.obligations],
            "bounds": {
                "max_query_chars": MAX_QUERY_CHARS,
                "max_obligations": MAX_OBLIGATIONS,
                "max_query_cues_per_obligation": MAX_QUERY_CUES_PER_OBLIGATION,
            },
        }


def _term_cues(query: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"text:{term}" for term in terms if term in query)


def _pattern_cues(query: str, patterns: tuple[re.Pattern[str], ...]) -> tuple[str, ...]:
    return tuple(
        f"pattern:temporal:{index}"
        for index, pattern in enumerate(patterns, start=1)
        if pattern.search(query)
    )


def _bounded_cues(*groups: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        for cue in group:
            if cue not in values:
                values.append(cue)
            if len(values) >= MAX_QUERY_CUES_PER_OBLIGATION:
                return tuple(values)
    return tuple(values)


def _obligation(
    obligation_id: ObligationId,
    *,
    required: bool,
    query_cues: tuple[str, ...],
) -> QueryObligation:
    return QueryObligation(
        id=obligation_id,
        role=_OBLIGATION_ROLES[obligation_id],
        required=required,
        query_cues=_bounded_cues(query_cues),
    )


def _plan_payload(
    *,
    query: str,
    purpose: PlanPurpose,
    route: PlanRoute,
    as_of: str | None,
    obligations: tuple[QueryObligation, ...],
) -> dict[str, Any]:
    return {
        "schema_version": QUERY_PLAN_SCHEMA,
        "query": query,
        "purpose": purpose.value,
        "route": route.value,
        "as_of": as_of,
        "obligations": [obligation.to_dict() for obligation in obligations],
    }


def _make_plan_id(
    *,
    query: str,
    purpose: PlanPurpose,
    route: PlanRoute,
    as_of: str | None,
    obligations: tuple[QueryObligation, ...],
) -> str:
    payload = _plan_payload(
        query=query,
        purpose=purpose,
        route=route,
        as_of=as_of,
        obligations=obligations,
    )
    return stable_id("lawplan", canonical_json(payload), length=32)


def _coerce_purpose(value: str | PlanPurpose) -> PlanPurpose:
    try:
        return PlanPurpose(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unsupported query plan purpose: {value}") from error


def _coerce_route(value: str | PlanRoute) -> PlanRoute:
    try:
        return PlanRoute(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"unsupported query plan route: {value}") from error


def compile_query_plan(
    query: str,
    purpose: str | PlanPurpose,
    route: str | PlanRoute,
    as_of: str | None = None,
    *,
    document_title_only: bool = False,
) -> QueryPlan:
    """Compile fixed query signals into a bounded deterministic evidence plan."""

    if not isinstance(query, str):
        raise ValueError("query must be a string")
    normalized_query = normalize_text(query)
    if not normalized_query:
        raise ValueError("query is required")
    if len(normalized_query) > MAX_QUERY_CHARS:
        raise ValueError(f"query must not exceed {MAX_QUERY_CHARS} characters")
    if not isinstance(document_title_only, bool):
        raise ValueError("document_title_only must be a boolean")

    normalized_purpose = _coerce_purpose(purpose)
    normalized_route = _coerce_route(route)
    if as_of is not None:
        if not isinstance(as_of, str):
            raise ValueError("as_of must be a string or null")
        canonical_date(as_of, field="as_of")

    obligations: list[QueryObligation] = []
    intent_query = "" if document_title_only else normalized_query

    exact_cues = _bounded_cues(
        (f"purpose:{normalized_purpose.value}",)
        if normalized_purpose
        in {
            PlanPurpose.EXACT_CITATION,
            PlanPurpose.AS_OF_VERSION,
            PlanPurpose.CITATION_VERIFY,
        }
        else (),
        ("route:exact",) if normalized_route is PlanRoute.EXACT else (),
        ("text:legal_locator",) if _LEGAL_LOCATOR.search(intent_query) else (),
        _term_cues(intent_query, _EXACT_TERMS),
    )
    if exact_cues:
        obligations.append(
            _obligation(
                ObligationId.EXACT_CITATION,
                required=True,
                query_cues=exact_cues,
            )
        )

    obligations.append(
        _obligation(
            ObligationId.PRIMARY_RULE,
            required=True,
            query_cues=_bounded_cues(
                (f"purpose:{normalized_purpose.value}", f"route:{normalized_route.value}"),
                _term_cues(intent_query, _PRIMARY_TERMS),
            ),
        )
    )

    temporal_cues = _bounded_cues(
        ("as_of",) if as_of is not None else (),
        ("purpose:as_of_version",)
        if normalized_purpose is PlanPurpose.AS_OF_VERSION
        else (),
        _term_cues(intent_query, _TEMPORAL_TERMS),
        _pattern_cues(intent_query, _TEMPORAL_PATTERNS),
    )
    if temporal_cues:
        obligations.append(
            _obligation(
                ObligationId.TEMPORAL_STATUS_VERSION,
                required=True,
                query_cues=temporal_cues,
            )
        )

    elements_cues = _bounded_cues(
        (f"purpose:{normalized_purpose.value}",)
        if normalized_purpose in {PlanPurpose.ELEMENTS, PlanPurpose.LEGAL_ISSUE_SCREEN}
        else (),
        _term_cues(intent_query, _ELEMENTS_TERMS),
    )
    if elements_cues:
        obligations.append(
            _obligation(
                ObligationId.ELEMENTS_DEFINITIONS,
                required=True,
                query_cues=elements_cues,
            )
        )

    interpretation_cues = _term_cues(intent_query, _INTERPRETATION_TERMS)
    if interpretation_cues:
        obligations.append(
            _obligation(
                ObligationId.INTERPRETATION,
                required=True,
                query_cues=interpretation_cues,
            )
        )

    threshold_standard_cues = _term_cues(intent_query, _THRESHOLD_STANDARD_TERMS)
    procedure_cues = _term_cues(intent_query, _PROCEDURE_TERMS)
    if threshold_standard_cues:
        procedure_cues = tuple(cue for cue in procedure_cues if cue != "text:立案")
    if procedure_cues:
        obligations.append(
            _obligation(
                ObligationId.PROCEDURE,
                required=True,
                query_cues=procedure_cues,
            )
        )

    if threshold_standard_cues:
        obligations.append(
            _obligation(
                ObligationId.THRESHOLD_STANDARD,
                required=True,
                query_cues=threshold_standard_cues,
            )
        )

    explicit_counterevidence_cues = _term_cues(intent_query, _COUNTEREVIDENCE_TERMS)
    counterevidence_context_cues = _bounded_cues(
        ("route:research",) if normalized_route is PlanRoute.RESEARCH else (),
        (f"purpose:{normalized_purpose.value}",)
        if normalized_purpose
        in {
            PlanPurpose.ELEMENTS,
            PlanPurpose.LEGAL_ISSUE_SCREEN,
            PlanPurpose.BROAD_TOPIC,
        }
        else (),
    )
    if explicit_counterevidence_cues or counterevidence_context_cues:
        obligations.append(
            _obligation(
                ObligationId.EXCEPTIONS_COUNTEREVIDENCE,
                required=bool(explicit_counterevidence_cues)
                or normalized_route is PlanRoute.RESEARCH
                or normalized_purpose
                in {PlanPurpose.ELEMENTS, PlanPurpose.LEGAL_ISSUE_SCREEN},
                query_cues=_bounded_cues(
                    counterevidence_context_cues,
                    explicit_counterevidence_cues,
                ),
            )
        )

    case_reference_cues = _term_cues(intent_query, _CASE_REFERENCE_TERMS)
    if case_reference_cues:
        obligations.append(
            _obligation(
                ObligationId.CASE_REFERENCE,
                required=True,
                query_cues=case_reference_cues,
            )
        )

    canonical_obligations = tuple(
        sorted(obligations, key=lambda obligation: _OBLIGATION_ORDER_INDEX[obligation.id])
    )
    plan_id = _make_plan_id(
        query=normalized_query,
        purpose=normalized_purpose,
        route=normalized_route,
        as_of=as_of,
        obligations=canonical_obligations,
    )
    return QueryPlan(
        schema_version=QUERY_PLAN_SCHEMA,
        plan_id=plan_id,
        query=normalized_query,
        purpose=normalized_purpose,
        route=normalized_route,
        as_of=as_of,
        obligations=canonical_obligations,
    )
