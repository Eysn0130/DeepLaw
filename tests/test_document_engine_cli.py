from __future__ import annotations

import json
import sys
import tomllib
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import pytest

from deeplaw import document_engine_cli


def _canonical_argv(tmp_path: Path) -> list[str]:
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-1.7\n%%EOF\n")
    output = tmp_path / "output"
    output.mkdir()
    return [
        "-p",
        str(source),
        "-o",
        str(output),
        "-m",
        "auto",
        "-b",
        "pipeline",
        "-l",
        "ch",
        "-s",
        "0",
        "-e",
        "7",
    ]


def test_version_is_the_only_non_pipeline_invocation() -> None:
    document_engine_cli._validate_pipeline_argv(["--version"])

    with pytest.raises(
        document_engine_cli.DocumentEngineInvocationError,
        match="bounded DeepLaw pipeline",
    ):
        document_engine_cli._validate_pipeline_argv(["--help"])


def test_accepts_the_closed_pipeline_invocation(tmp_path: Path) -> None:
    document_engine_cli._validate_pipeline_argv(_canonical_argv(tmp_path))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda argv: argv.__setitem__(7, "vlm-engine"), "must be pipeline"),
        (lambda argv: argv.__setitem__(6, "--model-path"), "expected -b"),
        (lambda argv: argv.__setitem__(6, "-m"), "expected -b"),
        (lambda argv: argv.__setitem__(11, "-1"), "non-negative integer"),
        (lambda argv: argv.__setitem__(13, "5000"), "page range exceeds"),
    ],
)
def test_rejects_expanded_or_malformed_execution_surface(
    tmp_path: Path,
    mutation: Callable[[list[str]], None],
    message: str,
) -> None:
    argv = _canonical_argv(tmp_path)
    mutation(argv)

    with pytest.raises(document_engine_cli.DocumentEngineInvocationError, match=message):
        document_engine_cli._validate_pipeline_argv(argv)


def test_rejects_unknown_option_before_importing_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = False

    def upstream() -> object:
        nonlocal imported
        imported = True
        raise AssertionError("upstream must not be imported")

    argv = [*_canonical_argv(tmp_path), "--model", "attacker/repository"]
    monkeypatch.setattr(document_engine_cli, "_upstream_main", upstream)
    monkeypatch.setattr(sys, "argv", ["deeplaw-document-engine", *argv])

    with pytest.raises(SystemExit) as error:
        document_engine_cli.main()

    assert error.value.code == 2
    assert imported is False


def test_main_forwards_only_validated_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def upstream() -> object:
        def run() -> int:
            calls.append(list(sys.argv[1:]))
            return 0

        return run

    argv = _canonical_argv(tmp_path)
    monkeypatch.setattr(document_engine_cli, "_upstream_main", upstream)
    monkeypatch.setattr(
        document_engine_cli,
        "isolated_engine_environment",
        contextmanager(lambda: iter(({},))),
    )
    monkeypatch.setattr(sys, "argv", ["deeplaw-document-engine", *argv])

    with pytest.raises(SystemExit) as error:
        document_engine_cli.main()

    assert error.value.code == 0
    assert calls == [argv]


def test_version_does_not_import_or_require_model_files(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(document_engine_cli, "version", lambda _name: "3.4.4")
    monkeypatch.setattr(
        document_engine_cli,
        "_upstream_main",
        lambda: (_ for _ in ()).throw(AssertionError("version must not import upstream")),
    )
    monkeypatch.setattr(sys, "argv", ["deeplaw-document-engine", "--version"])

    document_engine_cli.main()

    output = capsys.readouterr().out.strip()
    assert output.startswith("deeplaw-document-engine 3.4.4 ")
    assert "model-manifest-sha256=" in output


def test_missing_models_fail_before_importing_upstream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = False

    def upstream() -> object:
        nonlocal imported
        imported = True
        raise AssertionError("unconfigured engine must not be imported")

    monkeypatch.setenv("DEEPLAW_HOME", str(tmp_path / "missing-home"))
    monkeypatch.setattr(document_engine_cli, "_upstream_main", upstream)
    monkeypatch.setattr(
        sys,
        "argv",
        ["deeplaw-document-engine", *_canonical_argv(tmp_path)],
    )

    with pytest.raises(SystemExit) as error:
        document_engine_cli.main()

    assert error.value.code == 2
    assert imported is False


def test_openvex_records_the_exact_reviewed_transformers_exceptions() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "security" / "openvex.json"
    vex = json.loads(path.read_text(encoding="utf-8"))
    statements = vex["statements"]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert vex["@context"] == "https://openvex.dev/ns/v0.2.0"
    assert (
        project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"][
            "security/openvex.json"
        ]
        == "deeplaw/security/openvex.json"
    )
    assert {statement["vulnerability"]["name"] for statement in statements} == {
        "GHSA-29pf-2h5f-8g72",
        "GHSA-69w3-r845-3855",
        "GHSA-fgcw-684q-jj6r",
        "PYSEC-2025-217",
        "PYSEC-2026-2288",
        "PYSEC-2026-2289",
        "PYSEC-2026-2290",
    }
    for statement in statements:
        assert statement["products"] == [
            {
                "@id": "pkg:pypi/deeplaw@0.9.0",
                "subcomponents": [{"@id": "pkg:pypi/transformers@4.57.6"}],
            }
        ]
        assert statement["status"] == "not_affected"
        assert statement["justification"] == "vulnerable_code_not_in_execute_path"
        assert "document_engine_cli.py" in statement["impact_statement"]
