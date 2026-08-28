from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from deeplaw import bounded_subprocess
from deeplaw.bounded_subprocess import (
    BoundedSubprocessError,
    BoundedSubprocessFailureKind,
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
    ("stream", "message", "kind"),
    (
        ("stdout", "stdout exceeded", BoundedSubprocessFailureKind.STDOUT_LIMIT),
        ("stderr", "stderr exceeded", BoundedSubprocessFailureKind.STDERR_LIMIT),
    ),
)
def test_bounded_subprocess_kills_oversized_output(
    tmp_path: Path,
    stream: str,
    message: str,
    kind: BoundedSubprocessFailureKind,
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
    assert error.kind is kind
    assert len(getattr(error, stream)) == 1_024
    assert getattr(error, f"{stream}_truncated") is True


def test_bounded_subprocess_kills_timeout(tmp_path: Path) -> None:
    sidecar = tmp_path / "slow.py"
    sidecar.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")

    with pytest.raises(BoundedSubprocessError, match="timed out") as captured:
        run_bounded_subprocess(
            [sys.executable, str(sidecar)],
            timeout_seconds=0.05,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )
    assert captured.value.kind is BoundedSubprocessFailureKind.TIMEOUT


def test_bounded_subprocess_reports_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected start failure")

    monkeypatch.setattr(bounded_subprocess.subprocess, "Popen", fail_start)

    with pytest.raises(BoundedSubprocessError, match="failed to start") as captured:
        run_bounded_subprocess(
            [sys.executable, "-c", ""],
            timeout_seconds=5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )

    assert captured.value.kind is BoundedSubprocessFailureKind.START_FAILED


def test_bounded_subprocess_reports_unavailable_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoPipesProcess:
        stdin = None
        stdout = None
        stderr = None

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    process = NoPipesProcess()
    monkeypatch.setattr(
        bounded_subprocess.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    with pytest.raises(BoundedSubprocessError, match="pipes are unavailable") as captured:
        run_bounded_subprocess(
            [sys.executable, "-c", ""],
            timeout_seconds=5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )

    assert captured.value.kind is BoundedSubprocessFailureKind.PIPES_UNAVAILABLE


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

    with pytest.raises(BoundedSubprocessError, match="timed out") as captured:
        run_bounded_subprocess(
            [sys.executable, str(parent)],
            timeout_seconds=0.5,
            max_stdout_bytes=1_024,
            max_stderr_bytes=1_024,
        )
    assert captured.value.kind is BoundedSubprocessFailureKind.TIMEOUT

    assert started.is_file()
    time.sleep(1)
    assert not survived.exists()
