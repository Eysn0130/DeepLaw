from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from benchmarks.hosts import run_pass13_codex_continuity_qualification as qualification


def _profile(tmp_path: Path) -> Path:
    profile = tmp_path / "owner-codex-qualification"
    profile.mkdir()
    return profile


def test_execute_and_cli_require_an_explicit_profile_root(tmp_path: Path) -> None:
    parameter = inspect.signature(qualification.execute).parameters["profile_root"]
    assert parameter.default is inspect.Parameter.empty
    gold_parameter = inspect.signature(qualification.execute).parameters["human_gold_path"]
    assert gold_parameter.default is inspect.Parameter.empty

    parser = qualification.build_parser()
    required = [
        "--candidate-wheel",
        "candidate.whl",
        "--deeplaw-executable",
        "deeplaw",
        "--output-dir",
        "evidence",
    ]
    with pytest.raises(SystemExit):
        parser.parse_args(required)
    parsed = parser.parse_args(
        [
            *required,
            "--profile-root",
            str(_profile(tmp_path)),
            "--codex-binary",
            "/opt/codex",
            "--codex-launcher",
            "/opt/codex-owner-broker",
        ]
    )
    assert parsed.profile_root
    assert parsed.human_gold is None


def test_candidate_runner_rejects_human_gold_before_candidate_or_codex_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    preflight_called = False
    candidate_called = False

    def run_preflight(*args: object, **kwargs: object) -> object:
        nonlocal preflight_called
        preflight_called = True
        raise AssertionError("owner-external preflight must not start before Human Gold rejection")

    def prepare_candidate(*args: object, **kwargs: object) -> object:
        nonlocal candidate_called
        candidate_called = True
        raise AssertionError("candidate preparation must not receive Human Gold")

    monkeypatch.setattr(
        qualification,
        "run_codex_owner_external_zero_model_preflight",
        run_preflight,
    )
    monkeypatch.setattr(
        qualification.QualificationOrchestrator,
        "prepare_candidate",
        prepare_candidate,
    )
    profile = _profile(tmp_path)
    with pytest.raises(qualification.QualificationFailure, match="must not receive Human Gold"):
        qualification.execute(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "output",
            profile_root=profile,
            human_gold_path=tmp_path / "missing-human-gold.json",
            codex_binary=tmp_path / "codex",
            codex_launcher=tmp_path / "codex-owner-broker",
            host_identity_input=tmp_path / "host-identity.json",
            expected_broker_sha256="a" * 64,
            candidate_binding_input=tmp_path / "candidate-binding.json",
            run_id="pass16-human-gold-ordering",
            evidence_run_id=16,
            qualification_run_id=16,
            keyring_home=tmp_path / "keyring-home",
        )
    assert preflight_called is False
    assert candidate_called is False

    with pytest.raises(
        qualification.QualificationFailure,
        match=r"control input is unavailable.*not_executed",
    ):
        qualification.execute(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "missing-control-output",
            profile_root=profile,
            human_gold_path=None,
            codex_binary=tmp_path / "codex",
            codex_launcher=tmp_path / "codex-owner-broker",
        )
    assert preflight_called is False
    assert candidate_called is False


@pytest.mark.parametrize("ambient_name", ["HOME", "USERPROFILE", "CODEX_HOME"])
def test_explicit_profile_validation_does_not_consult_ambient_login_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ambient_name: str,
) -> None:
    ambient = tmp_path / ambient_name.lower()
    ambient.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv(ambient_name, str(ambient))
    profile = _profile(tmp_path)

    def reject_home(cls: type[Path]) -> Path:
        raise AssertionError("candidate runner consulted the ambient home")

    monkeypatch.setattr(Path, "home", classmethod(reject_home))
    assert qualification._validate_profile_root(
        profile, repository=repository
    ) == profile.resolve()


def test_profile_inside_repository_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    profile = repository / "qualification-profile"
    profile.mkdir(parents=True)
    with pytest.raises(qualification.QualificationFailure, match="outside the repository"):
        qualification._validate_profile_root(profile, repository=repository)


def test_profile_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real-profile"
    target.mkdir()
    profile = tmp_path / "profile-link"
    try:
        profile.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    repository = tmp_path / "repository"
    repository.mkdir()
    with pytest.raises(qualification.QualificationFailure, match="must not be a symlink"):
        qualification._validate_profile_root(profile, repository=repository)


def test_host_environment_uses_only_owner_profile_roots_without_reading_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ambient_home = tmp_path / "ambient-home"
    ambient_codex = tmp_path / "ambient-codex"
    ambient_home.mkdir()
    ambient_codex.mkdir()
    monkeypatch.setenv("HOME", str(ambient_home))
    monkeypatch.setenv("CODEX_HOME", str(ambient_codex))

    profile = _profile(tmp_path)
    auth_file = profile / "codex" / "auth.json"
    auth_file.parent.mkdir()
    auth_file.write_text("owner login material", encoding="utf-8")

    original_read_text = Path.read_text

    def reject_auth_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == auth_file:
            raise AssertionError("qualification runner must not read CODEX_HOME files")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_auth_read)
    environment = qualification._host_environment(Path("/opt/codex"), profile)

    assert Path(environment["HOME"]) == profile / "home"
    assert Path(environment["CODEX_HOME"]) == profile / "codex"
    assert Path(environment["XDG_CONFIG_HOME"]) == profile / "xdg-config"
    assert Path(environment["XDG_DATA_HOME"]) == profile / "xdg-data"
    assert profile.is_dir()
    assert auth_file.is_file()
    assert environment["HOME"] != os.environ["HOME"]
    assert environment["CODEX_HOME"] != os.environ["CODEX_HOME"]


class _VersionProcess:
    def __init__(self, stdout: str, *, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_exact_codex_version_is_accepted() -> None:
    process = _VersionProcess(qualification.HISTORICAL_CODEX_VERSION_FIXTURE + "\n")
    assert (
        qualification._validate_codex_version(process)
        == qualification.HISTORICAL_CODEX_VERSION_FIXTURE
    )


@pytest.mark.parametrize(
    "stdout",
    [
        "codex-cli 0.147.0-alpha.1.1\n",
        "codex-cli 0.147.0-alpha.1.2\nextra\n",
        "codex-cli 0.147.0-alpha.1.2 ",
    ],
)
def test_codex_version_drift_is_rejected(stdout: str) -> None:
    with pytest.raises(qualification.QualificationFailure, match="version preflight"):
        qualification._validate_codex_version(_VersionProcess(stdout))
