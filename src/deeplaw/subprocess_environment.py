"""Small closed environment builder for local subprocess regressions.

The helper intentionally copies only process values that are required for a
portable Python invocation.  Callers may provide the isolated paths they own;
all other ambient variables stay out of the child environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

__all__: tuple[str, ...] = ()

# Keep this list deliberately small.  In particular, provider credentials,
# user configuration paths, and test canaries are never ambient inputs to a
# child process.  Windows values are included when present even on a non-
# Windows development machine so the same allowlist can be exercised there.
_INHERITED_NAMES = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "TEMP",
    "TMP",
    "TMPDIR",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
)
_OVERRIDE_NAMES = frozenset({"HOME", "PYTHONPATH"})


def _build_subprocess_environment(
    *,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a closed environment for a local Python subprocess.

    ``overrides`` is limited to isolated ``HOME`` and ``PYTHONPATH`` values;
    the caller must explicitly provide those paths when it needs them.  A
    missing inherited value remains missing rather than being fabricated.
    """

    environment = {
        name: os.environ[name]
        for name in _INHERITED_NAMES
        if name in os.environ
    }
    if overrides is None:
        return environment
    if not isinstance(overrides, Mapping):
        raise TypeError("subprocess environment overrides must be a mapping")
    unknown = set(overrides) - _OVERRIDE_NAMES
    if unknown:
        names = ", ".join(sorted(str(name) for name in unknown))
        raise ValueError(f"unsupported subprocess environment overrides: {names}")
    for name, value in overrides.items():
        if not isinstance(name, str) or not isinstance(value, str) or "\x00" in value:
            raise ValueError("subprocess environment overrides must be text values")
        environment[name] = value
        if name == "HOME" and os.name == "nt":
            # ``pathlib.Path.home`` consults USERPROFILE on Windows.  This is an
            # isolated caller-owned value, not inherited ambient profile state.
            environment["USERPROFILE"] = value
    return environment
