from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

import benchmarks.release.exact_wheel_runner as exact_wheel_runner
from benchmarks.release.exact_wheel_runner import (
    ExactWheelExecutionError,
    _validate_probe,
    _validate_receipt,
)
from benchmarks.release.exact_wheel_runner import (
    execute_exact_wheel as _execute_exact_wheel,
)

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY / "contracts/exact-wheel-execution-receipt.v2.schema.json"
EXPECTED_VERSION = "0.12.0"
CANDIDATE_COMMIT = "a" * 40
CANDIDATE_TREE = "b" * 40
CANDIDATE_LOCK_SHA256 = "c" * 64
CANDIDATE_RUN_ID = 101
EVIDENCE_RUN_ID = 202
CANDIDATE_FULL_INVENTORY_SHA256 = "d" * 64


def execute_exact_wheel(**kwargs: Any) -> dict[str, Any]:
    receipt_contract = kwargs.pop("receipt_contract", "external-qualification-v2")
    if receipt_contract == "candidate-full-v1":
        return _execute_exact_wheel(receipt_contract=receipt_contract, **kwargs)
    return _execute_exact_wheel(
        receipt_contract=receipt_contract,
        candidate_commit=CANDIDATE_COMMIT,
        candidate_tree=CANDIDATE_TREE,
        expected_lock_sha256=CANDIDATE_LOCK_SHA256,
        candidate_run_id=CANDIDATE_RUN_ID,
        evidence_run_id=EVIDENCE_RUN_ID,
        corpus_sha256=CANDIDATE_FULL_INVENTORY_SHA256,
        **kwargs,
    )


def _fake_wheel(
    candidate_dir: Path,
    *,
    name: str = "deeplaw-0.12.0-py3-none-any.whl",
    version_output: str | None = None,
    version_exit: int = 0,
    journey_failure_step: str | None = None,
) -> Path:
    candidate_dir.mkdir(parents=True, exist_ok=True)
    wheel = candidate_dir / name
    version = name.split("-")[1]
    dist_info = f"deeplaw-{version}.dist-info"
    output = version_output or f"deeplaw {version}"
    cli_source = (
        "import json\n"
        "from pathlib import Path\n"
        "import sys\n"
        "import dependency_pkg\n"
        "\n"
        f"VERSION_OUTPUT = {output!r}\n"
        f"VERSION_EXIT = {version_exit!r}\n"
        f"JOURNEY_FAILURE = {journey_failure_step!r}\n"
        "\n"
        "def _emit(value):\n"
        "    print(json.dumps(value, sort_keys=True, separators=(\",\", \":\")))\n"
        "\n"
        "def _arg(name):\n"
        "    if name not in sys.argv:\n"
        "        return None\n"
        "    return sys.argv[sys.argv.index(name) + 1]\n"
        "\n"
        "def main():\n"
        "    if \"--version\" in sys.argv:\n"
        "        print(VERSION_OUTPUT)\n"
        "        raise SystemExit(VERSION_EXIT)\n"
        "    if JOURNEY_FAILURE and JOURNEY_FAILURE in sys.argv:\n"
        "        raise SystemExit(9)\n"
        "    if \"init\" in sys.argv:\n"
        "        _emit({\n"
        "            \"schema_version\": \"deeplaw.knowledge-vault-initialization/v2\",\n"
        "            \"vault_id\": \"vault_aaaaaaaaaaaaaaaaaaaaaaaa\",\n"
        "        })\n"
        "        return 0\n"
        "    if \"source\" in sys.argv and \"add\" in sys.argv:\n"
        "        _emit({\n"
        "            \"schema_version\": \"deeplaw.knowledge-ingest/v1\",\n"
        "            \"source\": {\"source_id\": \"source_aaaaaaaaaaaaaaaaaaaaaaaa\"},\n"
        "        })\n"
        "        return 0\n"
        "    if \"source\" in sys.argv and \"verify\" in sys.argv:\n"
        "        _emit({\n"
        "            \"schema_version\": \"deeplaw.knowledge-source-verification/v1\",\n"
        "            \"valid\": True,\n"
        "            \"database_integrity_valid\": True,\n"
        "            \"file\": {\"valid\": True},\n"
        "        })\n"
        "        return 0\n"
        "    if \"query\" in sys.argv:\n"
        "        _emit({\n"
        "            \"schema_version\": \"deeplaw.purpose-aware-retrieval/v3\",\n"
        "            \"purpose\": \"verify\",\n"
        "            \"policy_id\": \"evidence-first-v1\",\n"
        "            \"write_performed\": False,\n"
        "            \"query_plan\": {\n"
        "                \"schema_version\": \"deeplaw.knowledge-query-plan/v6\",\n"
        "                \"policy_id\": \"evidence-first-v1\",\n"
        "                \"retrieval_controls\": {\"retrieval_mode\": \"lexical\"},\n"
        "            },\n"
        "            \"capsule\": {},\n"
        "            \"statements\": [],\n"
        "            \"evidence\": [],\n"
        "            \"budget\": {\n"
        "                \"max_characters\": 2000,\n"
        "                \"selected_characters\": 0,\n"
        "                \"max_provider_characters\": 65536,\n"
        "            },\n"
        "        })\n"
        "        return 0\n"
        "    if \"context\" in sys.argv:\n"
        "        value = {\n"
        "            \"schema_version\": \"deeplaw.knowledge-capsule/v3\",\n"
        "            \"purpose\": \"verify\",\n"
        "            \"policy_id\": \"evidence-first-v1\",\n"
        "            \"write_performed\": False,\n"
        "            \"statements\": [],\n"
        "            \"evidence\": [],\n"
        "            \"provider_capsule\": {\n"
        "                \"capsule\": {},\n"
        "                \"delivery\": {\"provider_content_bytes\": 2},\n"
        "            },\n"
        "            \"budget\": {\n"
        "                \"max_characters\": 2000,\n"
        "                \"selected_characters\": 0,\n"
        "                \"max_provider_characters\": 65536,\n"
        "                \"provider_payload_bytes\": 2,\n"
        "            },\n"
        "        }\n"
        "        output_path = _arg(\"--output\")\n"
        "        if output_path:\n"
        "            Path(output_path).write_text(\n"
        "                json.dumps(value, sort_keys=True), encoding=\"utf-8\"\n"
        "            )\n"
        "        _emit(value)\n"
        "        return 0\n"
        "    raise SystemExit(2)\n"
    )
    files = {
        "deeplaw/__init__.py": f"__version__ = {version!r}\n",
        "deeplaw/cli.py": cli_source,
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: deeplaw\n"
            f"Version: {version}\n"
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: pass24-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
        f"{dist_info}/entry_points.txt": (
            "[console_scripts]\n"
            "deeplaw = deeplaw.cli:main\n"
        ),
        f"{dist_info}/RECORD": "",
    }
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, payload in sorted(files.items()):
            archive.writestr(path, payload)
    return wheel


def _fake_dependency_wheel(candidate_dir: Path) -> Path:
    wheelhouse = candidate_dir / "wheelhouse"
    wheelhouse.mkdir(parents=True, exist_ok=True)
    wheel = wheelhouse / "dependency_pkg-1.0-py3-none-any.whl"
    files = {
        "dependency_pkg/__init__.py": "VALUE = 'installed'\n",
        "dependency_pkg-1.0.dist-info/METADATA": (
            "Metadata-Version: 2.1\n"
            "Name: dependency-pkg\n"
            "Version: 1.0\n"
        ),
        "dependency_pkg-1.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: pass24-test\n"
            "Root-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        ),
        "dependency_pkg-1.0.dist-info/RECORD": "",
    }
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, payload in sorted(files.items()):
            archive.writestr(path, payload)
    return wheel


def _candidate_inputs(candidate_dir: Path) -> tuple[str, str]:
    dependency_wheel = _fake_dependency_wheel(candidate_dir)
    requirements = candidate_dir / "requirements.txt"
    dependency_sha = _sha256(dependency_wheel)
    requirements.write_text(
        f"dependency-pkg==1.0 --hash=sha256:{dependency_sha}\n",
        encoding="utf-8",
    )
    return requirements.name, _sha256(requirements)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_unique_wheel_is_installed_and_receipt_is_path_free(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    requirements_filename, requirements_sha256 = _candidate_inputs(candidate_dir)
    venv_path = tmp_path / "isolated-venv"

    receipt = execute_exact_wheel(
        candidate_dir=candidate_dir,
        expected_wheel_sha256=_sha256(wheel),
        requirements_filename=requirements_filename,
        expected_requirements_sha256=requirements_sha256,
        expected_version=EXPECTED_VERSION,
        repository=REPOSITORY,
        venv_path=venv_path,
    )

    assert receipt["schema_version"] == "deeplaw.exact-wheel-execution-receipt/v2"
    assert receipt["status"] == "exact_wheel_executed"
    assert receipt["candidate_provenance"] == {
        "commit": CANDIDATE_COMMIT,
        "tree": CANDIDATE_TREE,
        "lock_sha256": CANDIDATE_LOCK_SHA256,
    }
    assert receipt["run_binding"] == {
        "candidate_run_id": CANDIDATE_RUN_ID,
        "evidence_run_id": EVIDENCE_RUN_ID,
    }
    assert receipt["corpus_binding"] == {
        "role": "candidate_full",
        "sha256": CANDIDATE_FULL_INVENTORY_SHA256,
    }
    assert receipt["candidate"] == {
        "wheel_filename": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "wheel_size": wheel.stat().st_size,
        "path_class": "candidate_full_wheel",
    }
    assert receipt["runtime"]["distribution_version"] == EXPECTED_VERSION
    assert receipt["requirements"] == {
        "filename": "requirements.txt",
        "sha256": requirements_sha256,
        "bytes": (candidate_dir / requirements_filename).stat().st_size,
        "path_class": "candidate_requirements",
        "hash_pinned": True,
    }
    assert receipt["runtime"]["import_file_path_class"] == "venv_site_packages"
    assert receipt["entrypoint"]["value"] == "deeplaw.cli:main"
    assert receipt["entrypoint"]["executable_path_class"] == "venv_bin"
    expected_entrypoint = "Scripts/deeplaw.exe" if os.name == "nt" else "bin/deeplaw"
    assert receipt["entrypoint"]["executable_relative_path"] == expected_entrypoint
    assert receipt["entrypoint"]["module_path_class"] == "venv_site_packages"
    assert receipt["entrypoint"]["module_relative_path"] == "deeplaw/cli.py"
    assert receipt["version_check"]["argv"] == ["deeplaw", "--version"]
    assert receipt["version_check"]["exit_code"] == 0
    assert receipt["version_check"]["stdout_bytes"] == len(
        f"deeplaw {EXPECTED_VERSION}{os.linesep}".encode()
    )
    assert receipt["network_acquisition"] == {
        "explicit": True,
        "mode": "candidate_wheelhouse",
        "hash_pinned": True,
    }
    assert receipt["public_journey"]["journey_status"] == "passed"
    assert receipt["public_journey"]["step_count"] == 5
    assert [
        step["name"] for step in receipt["public_journey"]["steps"]
    ] == [
        "knowledge_init",
        "source_add",
        "source_verify",
        "evidence_first_query",
        "bounded_context",
    ]
    assert all(step["exit_code"] == 0 for step in receipt["public_journey"]["steps"])
    assert receipt["public_journey"]["steps"][3]["budget"]["selected_characters"] <= 2_000
    assert receipt["public_journey"]["steps"][4]["budget"]["selected_characters"] <= 2_000
    assert receipt["public_journey"]["network_policy"] == {
        "network_access": "not_requested",
        "model_sidecar": False,
        "environment_allowlist": "minimal",
    }
    assert receipt["environment_policy"] == {
        "python_isolated_mode": True,
        "pythonpath_cleared": True,
        "pythonhome_cleared": True,
        "user_site_disabled": True,
        "network_disabled_for_install": True,
        "requirements_hashes_required": True,
        "candidate_source_only": True,
    }
    rendered_receipt = json.dumps(receipt, sort_keys=True)
    assert str(tmp_path) not in rendered_receipt
    assert "DeepLaw synthetic public journey marker" not in rendered_receipt
    assert "Verify synthetic public journey evidence" not in rendered_receipt
    assert "raw_stdout" not in rendered_receipt
    assert "raw_stderr" not in rendered_receipt
    assert "<vault>" in rendered_receipt
    assert "<source>" in rendered_receipt
    assert "<output>" in rendered_receipt
    assert "junit" not in receipt
    assert "platform" not in receipt
    assert "scorer" not in receipt
    record = dict(receipt)
    record.pop("record_sha256")
    canonical = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert receipt["record_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert list(_schema_validator().iter_errors(receipt)) == []


def test_candidate_full_v1_receipt_contract_is_path_free_and_uses_v1_schema(
    tmp_path: Path,
) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    requirements_filename, requirements_sha256 = _candidate_inputs(candidate_dir)

    receipt = execute_exact_wheel(
        candidate_dir=candidate_dir,
        expected_wheel_sha256=_sha256(wheel),
        requirements_filename=requirements_filename,
        expected_requirements_sha256=requirements_sha256,
        expected_version=EXPECTED_VERSION,
        repository=REPOSITORY,
        venv_path=tmp_path / "isolated-venv",
        receipt_contract="candidate-full-v1",
    )

    assert receipt["schema_version"] == "deeplaw.exact-wheel-execution-receipt/v1"
    assert "candidate_provenance" not in receipt
    assert "run_binding" not in receipt
    assert "corpus_binding" not in receipt
    v1_schema = json.loads(
        (REPOSITORY / "contracts/exact-wheel-execution-receipt.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(v1_schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(receipt)) == []


def test_external_v2_requires_every_provenance_binding_before_execution(
    tmp_path: Path,
) -> None:
    with pytest.raises(ExactWheelExecutionError, match="requires candidate, run, and corpus"):
        _execute_exact_wheel(
            candidate_dir=tmp_path / "missing-candidate",
            expected_wheel_sha256="a" * 64,
            requirements_filename="candidate-requirements.txt",
            expected_requirements_sha256="b" * 64,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=tmp_path / "isolated-venv",
            receipt_contract="external-qualification-v2",
        )


def test_requirements_drift_after_private_staging_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    requirements_filename, requirements_sha256 = _candidate_inputs(candidate_dir)
    original_install = exact_wheel_runner._install_requirements

    def mutate_original_before_install(**kwargs: Any) -> str:
        assert kwargs["requirements"] != candidate_dir / requirements_filename
        (candidate_dir / requirements_filename).write_text(
            "dependency-pkg==9.9 --hash=sha256:" + "0" * 64 + "\n",
            encoding="utf-8",
        )
        return original_install(**kwargs)

    monkeypatch.setattr(
        exact_wheel_runner,
        "_install_requirements",
        mutate_original_before_install,
    )
    venv_path = tmp_path / "isolated-venv"
    with pytest.raises(ExactWheelExecutionError, match="changed during installation"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            requirements_filename=requirements_filename,
            expected_requirements_sha256=requirements_sha256,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
        )
    assert not venv_path.exists()


def test_candidate_full_v1_rejects_external_binding_arguments(tmp_path: Path) -> None:
    with pytest.raises(ExactWheelExecutionError, match="does not accept external qualification"):
        _execute_exact_wheel(
            candidate_dir=tmp_path / "missing-candidate",
            expected_wheel_sha256="a" * 64,
            requirements_filename="candidate-requirements.txt",
            expected_requirements_sha256="b" * 64,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=tmp_path / "isolated-venv",
            receipt_contract="candidate-full-v1",
            candidate_commit=CANDIDATE_COMMIT,
            candidate_tree=CANDIDATE_TREE,
            expected_lock_sha256=CANDIDATE_LOCK_SHA256,
            candidate_run_id=CANDIDATE_RUN_ID,
            evidence_run_id=EVIDENCE_RUN_ID,
            corpus_sha256=CANDIDATE_FULL_INVENTORY_SHA256,
        )


def test_candidate_directory_requires_one_regular_wheel(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    _fake_wheel(candidate_dir, name="deeplaw-0.12.0-py3-none-manylinux.whl")

    with pytest.raises(ExactWheelExecutionError, match="exactly one wheel"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            requirements_filename="requirements.txt",
            expected_requirements_sha256="0" * 64,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=tmp_path / "isolated-venv",
        )


def test_candidate_wheel_hash_mismatch_fails_before_venv_creation(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    venv_path = tmp_path / "isolated-venv"

    with pytest.raises(ExactWheelExecutionError, match="hash mismatch"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256="f" * 64,
            requirements_filename="requirements.txt",
            expected_requirements_sha256="f" * 64,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
        )
    assert not venv_path.exists()
    assert wheel.is_file()


def test_venv_inside_checkout_is_rejected(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    requirements_filename, requirements_sha256 = _candidate_inputs(candidate_dir)
    inside_repository = REPOSITORY / ".pass24-exact-wheel-venv"

    with pytest.raises(ExactWheelExecutionError, match="outside the repository"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            requirements_filename=requirements_filename,
            expected_requirements_sha256=requirements_sha256,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=inside_repository,
        )
    assert not inside_repository.exists()


def test_requirements_hash_mismatch_fails_before_venv_creation(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    requirements_filename, _ = _candidate_inputs(candidate_dir)
    venv_path = tmp_path / "isolated-venv"

    with pytest.raises(ExactWheelExecutionError, match="requirements hash mismatch"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            requirements_filename=requirements_filename,
            expected_requirements_sha256="f" * 64,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
        )
    assert not venv_path.exists()


@pytest.mark.parametrize(
    "requirements_text",
    (
        "-e .\n",
        "--index-url https://example.invalid/simple\n",
        "dependency-pkg @ file:///tmp/dependency.whl --hash=sha256:" + "0" * 64 + "\n",
    ),
)
def test_requirements_reject_unbound_or_local_inputs(
    tmp_path: Path, requirements_text: str
) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    _candidate_inputs(candidate_dir)
    requirements = candidate_dir / "requirements.txt"
    requirements.write_text(requirements_text, encoding="utf-8")

    with pytest.raises(ExactWheelExecutionError, match="requirements input"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            requirements_filename=requirements.name,
            expected_requirements_sha256=_sha256(requirements),
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=tmp_path / "isolated-venv",
        )


def test_console_version_execution_failure_cleans_venv(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir, version_exit=1)
    requirements_filename, requirements_sha256 = _candidate_inputs(candidate_dir)
    venv_path = tmp_path / "isolated-venv"

    with pytest.raises(ExactWheelExecutionError, match="--version execution failed"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            requirements_filename=requirements_filename,
            expected_requirements_sha256=requirements_sha256,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
        )
    assert not venv_path.exists()


def test_version_probe_accepts_one_native_line_and_rejects_other_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Completed:
        returncode = 0

        def __init__(self, stdout: bytes, stderr: bytes = b"") -> None:
            self.stdout = stdout
            self.stderr = stderr

    def probe(stdout: bytes, stderr: bytes = b"") -> dict[str, Any]:
        def run(*_args: object, **_kwargs: object) -> Completed:
            return Completed(stdout, stderr)

        monkeypatch.setattr(exact_wheel_runner.subprocess, "run", run)
        return exact_wheel_runner._run_version(
            console_entrypoint=tmp_path / "deeplaw",
            venv_path=tmp_path,
            expected_version=EXPECTED_VERSION,
            python_executable=tmp_path / "python",
        )

    expected_line = f"deeplaw {EXPECTED_VERSION}".encode()
    for output in (expected_line + b"\n", expected_line + b"\r\n"):
        receipt = probe(output)
        assert receipt["stdout_sha256"] == hashlib.sha256(output).hexdigest()
        assert receipt["stdout_bytes"] == len(output)

    for stdout, stderr in (
        (expected_line, b""),
        (expected_line + b"\nextra\n", b""),
        (expected_line + b"\n", b"warning\n"),
    ):
        with pytest.raises(ExactWheelExecutionError, match="output differs"):
            probe(stdout, stderr)


def test_public_journey_step_failure_cleans_venv(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir, journey_failure_step="query")
    requirements_filename, requirements_sha256 = _candidate_inputs(candidate_dir)
    venv_path = tmp_path / "isolated-venv"

    with pytest.raises(ExactWheelExecutionError, match="public journey"):
        execute_exact_wheel(
            candidate_dir=candidate_dir,
            expected_wheel_sha256=_sha256(wheel),
            requirements_filename=requirements_filename,
            expected_requirements_sha256=requirements_sha256,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
        )
    assert not venv_path.exists()


def test_public_journey_receipt_rejects_extra_raw_fields(tmp_path: Path) -> None:
    candidate_dir = tmp_path / "candidate-full"
    wheel = _fake_wheel(candidate_dir)
    requirements_filename, requirements_sha256 = _candidate_inputs(candidate_dir)
    receipt = execute_exact_wheel(
        candidate_dir=candidate_dir,
        expected_wheel_sha256=_sha256(wheel),
        requirements_filename=requirements_filename,
        expected_requirements_sha256=requirements_sha256,
        expected_version=EXPECTED_VERSION,
        repository=REPOSITORY,
        venv_path=tmp_path / "isolated-venv",
    )
    receipt["public_journey"]["steps"][0]["raw_stdout"] = "untrusted"
    with pytest.raises(ExactWheelExecutionError, match="receipt failed"):
        _validate_receipt(receipt, repository=REPOSITORY)


def _probe(
    *,
    venv_path: Path,
    site_packages: Path,
    import_file: Path,
    entrypoint_file: Path,
    python_executable: Path,
) -> dict[str, Any]:
    return {
        "python_implementation": "cpython",
        "python_version": "3.11.0",
        "python_executable": str(python_executable),
        "site_packages": [str(site_packages)],
        "import_file": str(import_file),
        "distribution_name": "deeplaw",
        "distribution_version": EXPECTED_VERSION,
        "entrypoint_name": "deeplaw",
        "entrypoint_group": "console_scripts",
        "entrypoint_value": "deeplaw.cli:main",
        "entrypoint_file": str(entrypoint_file),
    }


def test_checkout_source_import_origin_is_rejected(tmp_path: Path) -> None:
    venv_path = tmp_path / "isolated-venv"
    site_packages = venv_path / "lib" / "python3.11" / "site-packages"
    site_packages.mkdir(parents=True)
    entrypoint_file = site_packages / "deeplaw" / "cli.py"
    entrypoint_file.parent.mkdir()
    entrypoint_file.write_text("def main(): return 0\n", encoding="utf-8")
    python_executable = venv_path / "bin" / "python"
    python_executable.parent.mkdir()
    python_executable.write_bytes(b"python")
    observation = _probe(
        venv_path=venv_path,
        site_packages=site_packages,
        import_file=REPOSITORY / "src/deeplaw/__init__.py",
        entrypoint_file=entrypoint_file,
        python_executable=python_executable,
    )

    with pytest.raises(ExactWheelExecutionError, match="checkout source"):
        _validate_probe(
            observation,
            expected_version=EXPECTED_VERSION,
            repository=REPOSITORY,
            venv_path=venv_path,
            expected_python=python_executable,
        )
