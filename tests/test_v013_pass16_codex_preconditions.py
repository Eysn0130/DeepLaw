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
            "--human-gold",
            str(tmp_path / "human-gold.json"),
        ]
    )
    assert parsed.profile_root
    assert parsed.human_gold


def test_missing_external_human_gold_blocks_before_candidate_or_codex_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def prepare_candidate(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("candidate preparation must not run before Human Gold")

    monkeypatch.setattr(
        qualification.QualificationOrchestrator,
        "prepare_candidate",
        prepare_candidate,
    )
    with pytest.raises(qualification.QualificationFailure, match="frozen external Human Gold"):
        qualification.execute(
            candidate_wheel=tmp_path / "candidate.whl",
            deeplaw_executable=tmp_path / "deeplaw",
            output_dir=tmp_path / "output",
            profile_root=_profile(tmp_path),
            human_gold_path=tmp_path / "missing-human-gold.json",
            codex_command="codex",
        )
    assert called is False


@pytest.mark.parametrize("ambient_name", ["HOME", "USERPROFILE", "CODEX_HOME"])
def test_default_or_ambient_profile_roots_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    ambient_name: str,
) -> None:
    ambient = tmp_path / ambient_name.lower()
    ambient.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv(ambient_name, str(ambient))
    with pytest.raises(qualification.QualificationFailure, match="ambient HOME/CODEX_HOME"):
        qualification._validate_profile_root(ambient, repository=repository)


def test_default_codex_home_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    default_codex = home / ".codex"
    default_codex.mkdir(parents=True)
    platform_home = tmp_path / "platform-home"
    platform_home.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: platform_home))
    with pytest.raises(qualification.QualificationFailure, match="ambient HOME/CODEX_HOME"):
        qualification._validate_profile_root(default_codex, repository=repository)


@pytest.mark.parametrize("default_codex", [False, True])
def test_path_home_and_its_default_codex_home_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    default_codex: bool,
) -> None:
    platform_home = tmp_path / "platform-home"
    candidate = platform_home / ".codex" if default_codex else platform_home
    candidate.mkdir(parents=True)
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: platform_home))
    with pytest.raises(qualification.QualificationFailure, match="ambient HOME/CODEX_HOME"):
        qualification._validate_profile_root(candidate, repository=repository)


def test_userprofile_default_codex_home_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    userprofile = tmp_path / "userprofile"
    default_codex = userprofile / ".codex"
    default_codex.mkdir(parents=True)
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("USERPROFILE", str(userprofile))
    with pytest.raises(qualification.QualificationFailure, match="ambient HOME/CODEX_HOME"):
        qualification._validate_profile_root(default_codex, repository=repository)


def test_default_codex_home_symlink_target_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    default_codex_target = tmp_path / "ambient-codex"
    default_codex_target.mkdir()
    try:
        (home / ".codex").symlink_to(default_codex_target, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    platform_home = tmp_path / "platform-home"
    platform_home.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: platform_home))
    with pytest.raises(qualification.QualificationFailure, match="ambient HOME/CODEX_HOME"):
        qualification._validate_profile_root(default_codex_target, repository=repository)


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
    process = _VersionProcess(qualification.CODEX_VERSION + "\n")
    assert qualification._validate_codex_version(process) == qualification.CODEX_VERSION


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
