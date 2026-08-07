from __future__ import annotations

from typing import Final

COMPILATION_CORE_SCHEMA: Final = "deeplaw.source-compilation-core/v1"
SEMANTIC_COMPILATION_CORE_SCHEMA: Final = "deeplaw.semantic-compilation-core/v1"
STATEMENT_EVIDENCE_CORE_SCHEMA: Final = "deeplaw.statement-evidence-core/v1"
COMPILATION_RUN_SCHEMA: Final = "deeplaw.source-compilation-run/v1"
COMPILATION_PACKET_SCHEMA: Final = "deeplaw.source-compilation-packet/v1"
COMPILATION_PLAN_SCHEMA: Final = "deeplaw.source-compilation-plan/v1"
COMPILATION_BATCH_SCHEMA: Final = "deeplaw.source-compilation-batch/v1"
COMPILATION_RECEIPT_SCHEMA: Final = "deeplaw.source-compilation-receipt/v1"
SOURCE_FRESHNESS_REPORT_SCHEMA: Final = "deeplaw.source-freshness-report/v1"

COMPILER_GRANT_OPERATIONS: Final = (
    "abort_compilation",
    "begin_compilation",
    "commit_compilation",
    "refresh_compilation",
    "resume_compilation",
    "stage_compilation_batch",
    "validate_compilation",
)

SEMANTIC_COMPILER_GRANT_OPERATIONS: Final = tuple(
    sorted(
        (
            *COMPILER_GRANT_OPERATIONS,
            "finalize_semantic_compilation",
            "freeze_semantic_inventory",
            "stage_semantic_observations",
            "abort_synthesis_refresh",
            "begin_synthesis_refresh",
            "commit_synthesis_refresh",
            "resume_synthesis_refresh",
            "stage_synthesis_refresh",
            "validate_synthesis_refresh",
        )
    )
)

BACKFILL_GRANT_OPERATIONS: Final = (
    "promote_knowledge_draft",
    "propose_knowledge_backfill",
)

COMPILATION_RUN_STATES: Final = frozenset(
    {
        "planned",
        "staging",
        "validating",
        "ready_to_commit",
        "committed",
        "projection_pending",
        "succeeded",
        "failed",
        "aborted",
    }
)

TERMINAL_COMPILATION_RUN_STATES: Final = frozenset({"succeeded", "failed", "aborted"})

MAX_PACKET_FRAGMENTS: Final = 128
MAX_PACKET_PROVIDER_BYTES: Final = 48 * 1024
MAX_COMPILATION_CONTEXT_BYTES: Final = 12 * 1024
MAX_PACKETS_PER_RUN: Final = 10_000
MAX_ACTIONS_PER_PACKET: Final = 512
MAX_ACTIONS_PER_RUN: Final = 100_000
MAX_COMPILATION_REQUEST_BYTES: Final = 320 * 1024
