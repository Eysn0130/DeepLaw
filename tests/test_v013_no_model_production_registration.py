from __future__ import annotations

import shutil
from pathlib import Path


def test_no_model_registration_starts_production_launcher_and_lists_tools() -> None:
    from benchmarks.hosts.run_production_launcher_registration import run_registration

    executable_text = shutil.which("deeplaw")
    executable = Path(executable_text) if executable_text else Path(".venv/bin/deeplaw")
    report = run_registration(
        deeplaw_executable=executable,
        codex_command="deeplaw-missing-codex-for-unit-test",
        opencode_command="deeplaw-missing-opencode-for-unit-test",
    )

    assert report["production_launcher"]["status"] == "executed"
    assert report["production_launcher"]["tools_list"] == "passed"
    assert report["production_launcher"]["tool_names"] == ["knowledge_support"]
    assert report["codex"]["status"] == "not_executed"
    assert report["opencode"]["status"] == "not_executed"
    assert report["vault_path_absent_from_configuration"] is True
    assert report["knowledge_sink_registered"] is False
    assert report["model_turn_executed"] is False
