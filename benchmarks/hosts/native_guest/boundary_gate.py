"""Synchronize two fixed role probes with the external audit window."""

import json
import pathlib
import sys
import time


def run() -> None:
    sys.path.insert(0, "/runtime")
    from boundary_probe import run_boundary_probe

    root = pathlib.Path("/work")
    (root / "probe-ready").write_bytes(b"R")
    deadline = time.monotonic() + 15
    for marker in ("probe-go", "probe-resume"):
        while not (root / marker).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("boundary_gate_timeout")
            time.sleep(0.01)
        if (root / marker).read_bytes() != b"G":
            raise RuntimeError("boundary_gate_invalid")
        if marker == "probe-go":
            result = run_boundary_probe()
            temporary = root / ".probe-result.tmp"
            temporary.write_text(
                json.dumps(result, sort_keys=True, separators=(",", ":"))
            )
            temporary.replace(root / "probe-result.json")
