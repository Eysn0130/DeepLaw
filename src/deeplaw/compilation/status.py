from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SUCCESSFUL_COMPILATION_STATES = frozenset(
    {"committed", "projection_pending", "succeeded"}
)
BLOCKED_COMPILATION_STATES = frozenset({"failed", "aborted"})
ADMISSIBLE_SOURCE_LIFECYCLES = frozenset({"active", "pending"})


def summarize_source_compilation(
    *,
    source_revision_id: str,
    lifecycle_status: str,
    runs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize one ordered run history without rewriting prior success."""

    successful_run_ids = [
        str(run["compilation_run_id"])
        for run in runs
        if run["status"] in SUCCESSFUL_COMPILATION_STATES
    ]
    latest = runs[-1] if runs else None
    latest_status = str(latest["status"]) if latest is not None else None
    latest_attempts = (
        [
            {
                "source_revision_id": source_revision_id,
                "compilation_run_id": str(latest["compilation_run_id"]),
                "status": latest_status,
            }
        ]
        if latest is not None
        else []
    )
    canonical_committed = bool(successful_run_ids)
    lifecycle_blocked = lifecycle_status not in ADMISSIBLE_SOURCE_LIFECYCLES
    attempt_blocked = latest_status in BLOCKED_COMPILATION_STATES
    stale_or_blocked = lifecycle_blocked or attempt_blocked
    canonical_admissible = canonical_committed and not lifecycle_blocked
    if latest_status == "succeeded":
        projection_status = "ready"
    elif latest_status in {"committed", "projection_pending"}:
        projection_status = "pending"
    elif attempt_blocked:
        projection_status = "blocked"
    else:
        projection_status = "not_started"
    source_status = (
        "stale_or_blocked"
        if stale_or_blocked
        else ("compiled" if canonical_committed else "compilation_required")
    )
    return {
        "source_revision_id": source_revision_id,
        "source_status": source_status,
        "canonical_knowledge_committed": canonical_committed,
        "canonical_knowledge_admissible": canonical_admissible,
        "latest_compilation_attempts": latest_attempts,
        "successful_compilation_run_ids": successful_run_ids,
        "projection_pending_compilation_run_ids": [
            str(latest["compilation_run_id"])
        ]
        if latest_status in {"committed", "projection_pending"}
        else [],
        "wiki_projection_status": projection_status,
        "wiki_projection_pending": projection_status == "pending",
        "wiki_projection_ready": projection_status == "ready",
        "stale_or_blocked": stale_or_blocked,
    }
