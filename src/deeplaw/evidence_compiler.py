from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .util import canonical_json, sha256_bytes

__all__ = [
    "EVIDENCE_COMPILER_SCHEMA",
    "MAX_CANDIDATES",
    "MAX_COMPILER_CHARS",
    "MAX_COMPILER_ITEMS",
    "MAX_DUTIES",
    "DutyWitness",
    "EvidenceCandidate",
    "EvidenceCompilation",
    "EvidenceDuty",
    "EvidenceSelection",
    "RejectionSummary",
    "compile_evidence",
]

EVIDENCE_COMPILER_SCHEMA = "deeplaw.evidence-compiler/v1"
MAX_CANDIDATES = 256
MAX_DUTIES = 32
MAX_COMPILER_ITEMS = 5
MAX_COMPILER_CHARS = 6000
_MAX_CANDIDATE_CHARS = 1_000_000
_MAX_IDENTIFIER_CHARS = 256
_MAX_ROLE_CHARS = 64

WitnessStatus = Literal["covered", "uncertain", "uncovered"]
SelectionKind = Literal["required_coverage", "optional_coverage"]
RiskStatus = Literal["admissible", "uncertain_fallback"]
RejectionReason = Literal[
    "no_duty_coverage",
    "uncertainty_gate",
    "redundant_coverage",
    "character_budget",
    "item_budget",
    "lower_priority",
]


def _validate_identifier(value: str, *, field: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty canonical string")
    if len(value) > _MAX_IDENTIFIER_CHARS:
        raise ValueError(f"{field} exceeds {_MAX_IDENTIFIER_CHARS} characters")


@dataclass(frozen=True, slots=True)
class EvidenceDuty:
    duty_id: str
    role: str
    required: bool

    def __post_init__(self) -> None:
        _validate_identifier(self.duty_id, field="duty_id")
        if not isinstance(self.role, str) or not self.role or self.role != self.role.strip():
            raise ValueError("duty role must be a non-empty canonical string")
        if len(self.role) > _MAX_ROLE_CHARS:
            raise ValueError(f"duty role exceeds {_MAX_ROLE_CHARS} characters")
        if not isinstance(self.required, bool):
            raise ValueError("duty required must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    candidate_id: str
    document_id: str
    score: float
    chars: int
    is_uncertain: bool
    duty_ids: tuple[str, ...]
    authority_rank: int = 0

    def __post_init__(self) -> None:
        _validate_identifier(self.candidate_id, field="candidate_id")
        _validate_identifier(self.document_id, field="document_id")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise ValueError("candidate score must be a finite number")
        if not math.isfinite(float(self.score)):
            raise ValueError("candidate score must be a finite number")
        if isinstance(self.chars, bool) or not isinstance(self.chars, int):
            raise ValueError("candidate chars must be an integer")
        if not 1 <= self.chars <= _MAX_CANDIDATE_CHARS:
            raise ValueError(f"candidate chars must be between 1 and {_MAX_CANDIDATE_CHARS}")
        if not isinstance(self.is_uncertain, bool):
            raise ValueError("candidate is_uncertain must be a boolean")
        if not isinstance(self.duty_ids, tuple):
            raise ValueError("candidate duty_ids must be a tuple")
        if len(set(self.duty_ids)) != len(self.duty_ids):
            raise ValueError("candidate duty_ids must be unique")
        for duty_id in self.duty_ids:
            _validate_identifier(duty_id, field="candidate duty_id")
        if isinstance(self.authority_rank, bool) or not isinstance(self.authority_rank, int):
            raise ValueError("candidate authority_rank must be an integer")
        if not 0 <= self.authority_rank <= 100:
            raise ValueError("candidate authority_rank must be between 0 and 100")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "document_id": self.document_id,
            "score": float(self.score),
            "chars": self.chars,
            "is_uncertain": self.is_uncertain,
            "duty_ids": list(self.duty_ids),
            "authority_rank": self.authority_rank,
        }


@dataclass(frozen=True, slots=True)
class EvidenceSelection:
    candidate_id: str
    selection_index: int
    kind: SelectionKind
    risk_status: RiskStatus
    incremental_required_duty_ids: tuple[str, ...]
    incremental_optional_duty_ids: tuple[str, ...]
    adds_document_diversity: bool
    chars: int
    cumulative_chars: int

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["incremental_required_duty_ids"] = list(
            self.incremental_required_duty_ids
        )
        value["incremental_optional_duty_ids"] = list(
            self.incremental_optional_duty_ids
        )
        return value


@dataclass(frozen=True, slots=True)
class DutyWitness:
    duty_id: str
    role: str
    required: bool
    status: WitnessStatus
    candidate_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RejectionSummary:
    reason: RejectionReason
    count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvidenceCompilation:
    schema_version: str
    max_items: int
    max_chars: int
    candidate_count: int
    selected_ids: tuple[str, ...]
    selections: tuple[EvidenceSelection, ...]
    duty_witnesses: tuple[DutyWitness, ...]
    uncovered_duty_ids: tuple[str, ...]
    uncertain_duty_ids: tuple[str, ...]
    total_chars: int
    candidate_digest: str
    result_digest: str
    rejected: tuple[RejectionSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "max_items": self.max_items,
            "max_chars": self.max_chars,
            "candidate_count": self.candidate_count,
            "selected_ids": list(self.selected_ids),
            "selections": [selection.to_dict() for selection in self.selections],
            "duty_witnesses": [witness.to_dict() for witness in self.duty_witnesses],
            "uncovered_duty_ids": list(self.uncovered_duty_ids),
            "uncertain_duty_ids": list(self.uncertain_duty_ids),
            "total_chars": self.total_chars,
            "candidate_digest": self.candidate_digest,
            "result_digest": self.result_digest,
            "rejected": [summary.to_dict() for summary in self.rejected],
        }


def _score_hex(score: float) -> str:
    value = float(score)
    if value == 0:
        value = 0.0
    return value.hex()


def _candidate_digest(candidates: tuple[EvidenceCandidate, ...]) -> str:
    payload = [
        {
            "candidate_id": candidate.candidate_id,
            "document_id": candidate.document_id,
            "score_hex": _score_hex(candidate.score),
            "chars": candidate.chars,
            "is_uncertain": candidate.is_uncertain,
            "duty_ids": sorted(candidate.duty_ids),
            "authority_rank": candidate.authority_rank,
        }
        for candidate in sorted(candidates, key=lambda item: item.candidate_id)
    ]
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def _quality(candidate: EvidenceCandidate) -> int:
    return 1 if candidate.is_uncertain else 2


def _incremental_duties(
    candidate: EvidenceCandidate,
    *,
    duties: tuple[EvidenceDuty, ...],
    coverage_quality: dict[str, int],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    candidate_quality = _quality(candidate)
    covered = set(candidate.duty_ids)
    required: list[str] = []
    optional: list[str] = []
    for duty in duties:
        if duty.duty_id not in covered:
            continue
        if candidate_quality <= coverage_quality[duty.duty_id]:
            continue
        (required if duty.required else optional).append(duty.duty_id)
    return tuple(required), tuple(optional)


def _candidate_rank(
    candidate: EvidenceCandidate,
    *,
    duties: tuple[EvidenceDuty, ...],
    required_gain: tuple[str, ...],
    optional_gain: tuple[str, ...],
    selected_document_ids: set[str],
) -> tuple[Any, ...]:
    required_set = set(required_gain)
    optional_set = set(optional_gain)
    required_order = tuple(
        0 if duty.duty_id in required_set else 1 for duty in duties if duty.required
    )
    optional_order = tuple(
        0 if duty.duty_id in optional_set else 1 for duty in duties if not duty.required
    )
    return (
        required_order,
        -len(required_gain),
        -len(optional_gain),
        optional_order,
        candidate.is_uncertain,
        candidate.document_id in selected_document_ids,
        -float(candidate.score),
        -candidate.authority_rank,
        candidate.chars,
        candidate.candidate_id,
    )


def _rejection_summaries(
    *,
    duties: tuple[EvidenceDuty, ...],
    candidates: tuple[EvidenceCandidate, ...],
    selected: tuple[EvidenceCandidate, ...],
    witnesses: tuple[DutyWitness, ...],
    max_items: int,
    max_chars: int,
) -> tuple[RejectionSummary, ...]:
    selected_ids = {candidate.candidate_id for candidate in selected}
    selected_chars = sum(candidate.chars for candidate in selected)
    witness_by_duty = {witness.duty_id: witness for witness in witnesses}
    known_duties = {duty.duty_id for duty in duties}
    counts: dict[RejectionReason, int] = {}
    for candidate in candidates:
        if candidate.candidate_id in selected_ids:
            continue
        candidate_duties = known_duties.intersection(candidate.duty_ids)
        reason: RejectionReason
        if not candidate_duties:
            reason = "no_duty_coverage"
        elif candidate.is_uncertain and all(
            witness_by_duty[duty_id].status == "covered"
            for duty_id in candidate_duties
        ):
            reason = "uncertainty_gate"
        elif all(
            witness_by_duty[duty_id].status
            in ({"covered", "uncertain"} if candidate.is_uncertain else {"covered"})
            for duty_id in candidate_duties
        ):
            reason = "redundant_coverage"
        elif selected_chars + candidate.chars > max_chars:
            reason = "character_budget"
        elif len(selected) >= max_items:
            reason = "item_budget"
        else:
            reason = "lower_priority"
        counts[reason] = counts.get(reason, 0) + 1

    reason_order: tuple[RejectionReason, ...] = (
        "no_duty_coverage",
        "uncertainty_gate",
        "redundant_coverage",
        "character_budget",
        "item_budget",
        "lower_priority",
    )
    return tuple(
        RejectionSummary(reason=reason, count=counts[reason])
        for reason in reason_order
        if counts.get(reason)
    )


def compile_evidence(
    duties: tuple[EvidenceDuty, ...],
    candidates: tuple[EvidenceCandidate, ...],
    *,
    max_items: int = MAX_COMPILER_ITEMS,
    max_chars: int = 3500,
) -> EvidenceCompilation:
    """Compile a bounded, replayable, coverage-first evidence set.

    Duties are considered in caller-provided order. For the earliest duty that
    can still be improved in the current required or optional phase, uncertain
    candidates are admitted only when that duty has no admissible candidate.
    Every selected candidate must add or improve a duty witness.
    """

    if not isinstance(duties, tuple):
        raise ValueError("duties must be a tuple")
    if not isinstance(candidates, tuple):
        raise ValueError("candidates must be a tuple")
    if len(duties) > MAX_DUTIES:
        raise ValueError(f"duties must not exceed {MAX_DUTIES}")
    if len(candidates) > MAX_CANDIDATES:
        raise ValueError(f"candidates must not exceed {MAX_CANDIDATES}")
    duty_ids = tuple(duty.duty_id for duty in duties)
    if len(set(duty_ids)) != len(duty_ids):
        raise ValueError("duty IDs must be unique")
    candidate_ids = tuple(candidate.candidate_id for candidate in candidates)
    if len(set(candidate_ids)) != len(candidate_ids):
        raise ValueError("candidate IDs must be unique")
    known_duty_ids = set(duty_ids)
    unknown_duties = sorted(
        {
            duty_id
            for candidate in candidates
            for duty_id in candidate.duty_ids
            if duty_id not in known_duty_ids
        }
    )
    if unknown_duties:
        raise ValueError(f"candidate references unknown duties: {', '.join(unknown_duties)}")
    if isinstance(max_items, bool) or not isinstance(max_items, int):
        raise ValueError("max_items must be an integer")
    if not 0 <= max_items <= MAX_COMPILER_ITEMS:
        raise ValueError(f"max_items must be between 0 and {MAX_COMPILER_ITEMS}")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ValueError("max_chars must be an integer")
    if not 0 <= max_chars <= MAX_COMPILER_CHARS:
        raise ValueError(f"max_chars must be between 0 and {MAX_COMPILER_CHARS}")

    ordered_candidates = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    digest = _candidate_digest(ordered_candidates)
    coverage_quality = dict.fromkeys(duty_ids, 0)
    selected: list[EvidenceCandidate] = []
    selections: list[EvidenceSelection] = []
    selected_ids: set[str] = set()
    selected_document_ids: set[str] = set()
    total_chars = 0

    while len(selected) < max_items:
        feasible: list[
            tuple[EvidenceCandidate, tuple[str, ...], tuple[str, ...]]
        ] = []
        remaining_chars = max_chars - total_chars
        for candidate in ordered_candidates:
            if candidate.candidate_id in selected_ids or candidate.chars > remaining_chars:
                continue
            required_gain, optional_gain = _incremental_duties(
                candidate,
                duties=duties,
                coverage_quality=coverage_quality,
            )
            if required_gain or optional_gain:
                feasible.append((candidate, required_gain, optional_gain))
        if not feasible:
            break

        if any(required_gain for _, required_gain, _ in feasible):
            feasible = [item for item in feasible if item[1]]
            kind: SelectionKind = "required_coverage"
            gain_index = 1
            target_required = True
        else:
            feasible = [item for item in feasible if item[2]]
            kind = "optional_coverage"
            gain_index = 2
            target_required = False

        target_duty_id = next(
            duty.duty_id
            for duty in duties
            if duty.required is target_required
            and any(duty.duty_id in item[gain_index] for item in feasible)
        )
        feasible = [
            item for item in feasible if target_duty_id in item[gain_index]
        ]

        admissible = [item for item in feasible if not item[0].is_uncertain]
        if admissible:
            feasible = admissible

        candidate, required_gain, optional_gain = min(
            feasible,
            key=lambda item: _candidate_rank(
                item[0],
                duties=duties,
                required_gain=item[1],
                optional_gain=item[2],
                selected_document_ids=selected_document_ids,
            ),
        )
        total_chars += candidate.chars
        selected.append(candidate)
        selected_ids.add(candidate.candidate_id)
        adds_document_diversity = candidate.document_id not in selected_document_ids
        selected_document_ids.add(candidate.document_id)
        candidate_quality = _quality(candidate)
        for duty_id in (*required_gain, *optional_gain):
            coverage_quality[duty_id] = candidate_quality
        selections.append(
            EvidenceSelection(
                candidate_id=candidate.candidate_id,
                selection_index=len(selected) - 1,
                kind=kind,
                risk_status=(
                    "uncertain_fallback" if candidate.is_uncertain else "admissible"
                ),
                incremental_required_duty_ids=required_gain,
                incremental_optional_duty_ids=optional_gain,
                adds_document_diversity=adds_document_diversity,
                chars=candidate.chars,
                cumulative_chars=total_chars,
            )
        )

    selected_tuple = tuple(selected)
    witnesses: list[DutyWitness] = []
    for duty in duties:
        matching = [
            candidate for candidate in selected_tuple if duty.duty_id in candidate.duty_ids
        ]
        admissible_matching = [
            candidate for candidate in matching if not candidate.is_uncertain
        ]
        if admissible_matching:
            witness_candidate = admissible_matching[0]
            status: WitnessStatus = "covered"
        elif matching:
            witness_candidate = matching[0]
            status = "uncertain"
        else:
            witness_candidate = None
            status = "uncovered"
        witnesses.append(
            DutyWitness(
                duty_id=duty.duty_id,
                role=duty.role,
                required=duty.required,
                status=status,
                candidate_id=(
                    witness_candidate.candidate_id if witness_candidate is not None else None
                ),
            )
        )
    witness_tuple = tuple(witnesses)
    uncovered = tuple(
        witness.duty_id for witness in witness_tuple if witness.status == "uncovered"
    )
    uncertain = tuple(
        witness.duty_id for witness in witness_tuple if witness.status == "uncertain"
    )
    rejected = _rejection_summaries(
        duties=duties,
        candidates=ordered_candidates,
        selected=selected_tuple,
        witnesses=witness_tuple,
        max_items=max_items,
        max_chars=max_chars,
    )
    result_payload = {
        "schema_version": EVIDENCE_COMPILER_SCHEMA,
        "duties": [duty.to_dict() for duty in duties],
        "max_items": max_items,
        "max_chars": max_chars,
        "candidate_count": len(candidates),
        "selected_ids": [candidate.candidate_id for candidate in selected_tuple],
        "selections": [selection.to_dict() for selection in selections],
        "duty_witnesses": [witness.to_dict() for witness in witness_tuple],
        "uncovered_duty_ids": list(uncovered),
        "uncertain_duty_ids": list(uncertain),
        "total_chars": total_chars,
        "candidate_digest": digest,
        "rejected": [summary.to_dict() for summary in rejected],
    }
    result_digest = sha256_bytes(canonical_json(result_payload).encode("utf-8"))
    return EvidenceCompilation(
        schema_version=EVIDENCE_COMPILER_SCHEMA,
        max_items=max_items,
        max_chars=max_chars,
        candidate_count=len(candidates),
        selected_ids=tuple(candidate.candidate_id for candidate in selected_tuple),
        selections=tuple(selections),
        duty_witnesses=witness_tuple,
        uncovered_duty_ids=uncovered,
        uncertain_duty_ids=uncertain,
        total_chars=total_chars,
        candidate_digest=digest,
        result_digest=result_digest,
        rejected=rejected,
    )
