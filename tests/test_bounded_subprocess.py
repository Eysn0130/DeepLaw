from __future__ import annotations

import sys
import time
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


def test_bounded_subprocess_kills_descendants_that_inherit_output_pipes(
    tmp_path: Path,
) -> None:
    started = tmp_path / "child-started"
    survived = tmp_path / "child-survived"
    child = (
        "import pathlib,time;"
        f"pathlib.Path({str(started)!r}).write_text('started', encoding='utf-8');"
        "time.sleep(1);"
        f"pathlib.Path({str(survived)!r}).write_text('survived', encoding='utf-8');"
        "time.sleep(10)"
    )
    parent = tmp_path / "process-tree.py"
    parent.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )

    with pytest.raises(BoundedSubprocessError, match="timed out"):
        run_bounded_subprocess(
            [sys.executable, str(parent)],
            timeout_seconds=0.5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )

    assert started.is_file()
    time.sleep(1)
    assert not survived.exists()
