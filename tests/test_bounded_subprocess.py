from __future__ import annotations

import sys
from pathlib import Path

import pytest

from deeplaw.bounded_subprocess import (
    BoundedSubprocessError,
    run_bounded_subprocess,
)


def test_bounded_subprocess_captures_exact_bounded_output(tmp_path: Path) -> None:
    sidecar = tmp_path / "echo.py"
    sidecar.write_text(
        "import sys\npayload = sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(payload[::-1])\n",
        encoding="utf-8",
    )

    result = run_bounded_subprocess(
        [sys.executable, str(sidecar)],
        input_bytes=b"deeplaw",
        timeout_seconds=5,
        max_stdout_bytes=1_024,
        max_stderr_bytes=1_024,
    )

    assert result.returncode == 0
    assert result.stdout == b"walpeed"
    assert result.stderr == b""


@pytest.mark.parametrize(
    ("stream", "message"),
    (("stdout", "stdout exceeded"), ("stderr", "stderr exceeded")),
)
def test_bounded_subprocess_kills_oversized_output(
    tmp_path: Path,
    stream: str,
    message: str,
) -> None:
    sidecar = tmp_path / "oversized.py"
    sidecar.write_text(
        "import sys, time\n"
        f"sys.{stream}.write('x' * 1000000)\n"
        f"sys.{stream}.flush()\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )

    with pytest.raises(BoundedSubprocessError, match=message) as captured:
        run_bounded_subprocess(
            [sys.executable, str(sidecar)],
            timeout_seconds=5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )
    error = captured.value
    assert len(getattr(error, stream)) == 1_024
    assert getattr(error, f"{stream}_truncated") is True


def test_bounded_subprocess_kills_timeout(tmp_path: Path) -> None:
    sidecar = tmp_path / "slow.py"
    sidecar.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")

    with pytest.raises(BoundedSubprocessError, match="timed out"):
        run_bounded_subprocess(
            [sys.executable, str(sidecar)],
            timeout_seconds=0.05,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )
