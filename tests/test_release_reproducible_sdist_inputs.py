from __future__ import annotations

import gzip
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

import benchmarks.release.verify_reproducible_build as reproducible


def _git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "DeepLaw test"], cwd=repository, check=True)
    (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repository, check=True)
    return repository


def _tar_gz(path: Path, members: dict[str, bytes]) -> None:
    with (
        path.open("wb") as output,
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def _tree_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_tracked_source_materialization_excludes_ignored_files_and_preserves_edits(
    tmp_path: Path,
) -> None:
    repository = _git_repository(tmp_path)
    (repository / "tracked.txt").write_text("current worktree\n", encoding="utf-8")
    (repository / "ignored.txt").write_text("must never enter an sdist\n", encoding="utf-8")

    destination = tmp_path / "source"
    tracked = reproducible._tracked_source_tree(repository, destination)

    assert "tracked.txt" in tracked
    assert "ignored.txt" not in tracked
    assert (destination / "tracked.txt").read_text(encoding="utf-8") == "current worktree\n"
    assert not (destination / "ignored.txt").exists()


def test_initialized_or_absent_gitlink_is_not_rejected(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path)
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", "160000," + "a" * 40 + ",vendor/submodule"],
        cwd=repository,
        check=True,
    )

    destination = tmp_path / "source"
    tracked = reproducible._tracked_source_tree(repository, destination)

    assert "vendor/submodule" in tracked
    assert (destination / "vendor/submodule").is_dir()


def test_unmerged_index_entry_is_rejected_before_materialization(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path)
    hashes = []
    for content in (b"base\n", b"ours\n", b"theirs\n"):
        result = subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],
            cwd=repository,
            input=content,
            capture_output=True,
            check=True,
        )
        hashes.append(result.stdout.decode("ascii").strip())
    index_info = "\n".join(
        f"100644 {digest} {stage}\tconflicted.txt"
        for stage, digest in enumerate(hashes, start=1)
    )
    subprocess.run(
        ["git", "update-index", "--index-info"],
        cwd=repository,
        input=(index_info + "\n").encode("ascii"),
        check=True,
    )

    destination = tmp_path / "source"
    with pytest.raises(RuntimeError, match="stage-0"):
        reproducible._tracked_source_tree(repository, destination)
    assert not destination.exists()


def test_sdist_with_ignored_member_fails_closed(tmp_path: Path) -> None:
    repository = _git_repository(tmp_path)
    (repository / "ignored.txt").write_text("untracked build input\n", encoding="utf-8")
    artifact = tmp_path / "deeplaw-0.12.0.tar.gz"
    _tar_gz(
        artifact,
        {
            "deeplaw-0.12.0/tracked.txt": b"committed\n",
            "deeplaw-0.12.0/ignored.txt": b"untracked build input\n",
            "deeplaw-0.12.0/PKG-INFO": b"Metadata-Version: 2.4\n",
        },
    )

    inventory = reproducible.archive_inventory(artifact)
    tracked = {".gitignore", "tracked.txt"}
    with pytest.raises(RuntimeError, match="outside the tracked source tree"):
        reproducible._verify_sdist_source_members(inventory, tracked)


def test_verify_rejects_reproducible_double_build_that_contains_ignored_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _git_repository(tmp_path)
    (repository / "ignored.txt").write_text("ignored build input\n", encoding="utf-8")
    seen_repositories: list[Path] = []

    def fake_build(source: Path, output: Path, *, source_date_epoch: int) -> list[Path]:
        del source_date_epoch
        seen_repositories.append(source)
        output.mkdir(parents=True)
        wheel = output / "deeplaw-0.12.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            info = zipfile.ZipInfo("deeplaw/contracts/fixture.json")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            archive.writestr(info, b"{}")
        sdist = output / "deeplaw-0.12.0.tar.gz"
        _tar_gz(
            sdist,
            {
                "deeplaw-0.12.0/tracked.txt": b"committed\n",
                "deeplaw-0.12.0/ignored.txt": b"ignored build input\n",
                "deeplaw-0.12.0/PKG-INFO": b"Metadata-Version: 2.4\n",
            },
        )
        return [wheel, sdist]

    monkeypatch.setattr(reproducible, "_build", fake_build)
    monkeypatch.setattr(
        reproducible,
        "repository_binding",
        lambda _: {"commit": "a" * 40, "worktree_clean": True},
    )
    monkeypatch.setattr(
        reproducible,
        "_required_wheel_paths",
        lambda _: ("deeplaw/contracts/fixture.json",),
    )
    monkeypatch.setattr(reproducible, "_verify_build_inputs", lambda _: {})

    with pytest.raises(RuntimeError, match="outside the tracked source tree"):
        reproducible.verify(repository, source_date_epoch=reproducible.DEFAULT_SOURCE_DATE_EPOCH)

    assert len(seen_repositories) == 2
    assert len({path for path in seen_repositories}) == 2
    assert all(path != repository for path in seen_repositories)
    assert _tree_files(seen_repositories[0]) == _tree_files(seen_repositories[1])
    assert not (seen_repositories[0] / "ignored.txt").exists()
    assert not (seen_repositories[1] / "ignored.txt").exists()
