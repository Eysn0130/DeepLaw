"""Statement-level evidence primitives.

The evidence package intentionally contains no model, network, or mutation entry
point.  Compilation owns the write transaction; this package provides the
deterministic statement contract, digest helpers, and a bounded read-only view.
"""

from .statements import (
    MAX_GAPS_PER_STATEMENT,
    MAX_REFS_PER_STATEMENT,
    MAX_STATEMENT_TEXT_CHARS,
    MAX_STATEMENTS_PER_REVISION,
    StatementEvidenceStore,
    build_input_set_sha256,
    statement_id,
    statement_sha256,
    validate_statement,
    validate_statement_plans,
)

__all__ = [
    "MAX_GAPS_PER_STATEMENT",
    "MAX_REFS_PER_STATEMENT",
    "MAX_STATEMENTS_PER_REVISION",
    "MAX_STATEMENT_TEXT_CHARS",
    "StatementEvidenceStore",
    "build_input_set_sha256",
    "statement_id",
    "statement_sha256",
    "validate_statement",
    "validate_statement_plans",
]
