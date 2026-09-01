"""Shared stdlib-only safety policy for retained qualification artifacts."""

from __future__ import annotations

import re

MAX_SOURCE_BYTES = 64 * 1024 * 1024

# Sanitized receipts may contain URLs and legal locators (for example
# ``https://example.test/page/1`` and ``page:/1``), but must never retain a
# local path. Keep POSIX and Windows alternatives separate so URL syntax is
# not mistaken for a local path.
ABSOLUTE_PATH_RE = re.compile(
    r"""(?:
        (?<![A-Za-z0-9_:/])/(?:Users|home|root|private|tmp|var|etc|opt|workspace|Volumes|System|Library|bin|sbin|usr|dev|proc|sys|run|mnt)(?:/|[\s"']|$)
        |(?<![A-Za-z0-9_\\:])(?:[A-Za-z]:[\\/]|\\\\(?!u[0-9a-fA-F]{4}[\\/])[^\\/\s]+[\\/])
    )""",
    re.VERBOSE,
)

SECRET_MARKER_RE = re.compile(
    r"""(?i)(?:api[_-]?key|access[_-]?token|authorization|bearer|private[_-]?key|secret)\s*[:=]"""
)

FORBIDDEN_FILENAME_RE = re.compile(
    r"(?:^|[._-])(?:auth|authorization|credential|credentials|secret|secrets|"
    r"password|passwd|api[_-]?key|private[_-]?key|access[_-]?token|token|"
    r"transcript|chain[_-]?of[_-]?thought|hidden[_-]?reasoning|"
    r"raw[_-]?(?:events|reasoning|log)|reasoning|log)(?:$|[._-])",
    re.IGNORECASE,
)
