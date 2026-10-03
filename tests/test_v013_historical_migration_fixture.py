from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import stat
import zipfile
from pathlib import Path

import pytest

from benchmarks.release import historical_migration_fixture as fixture


def _wheel(path: Path) -> None:
    files = {
        "deeplaw/__init__.py": b'__version__ = "0.6.0"\n',
        "deeplaw-0.6.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: deeplaw\nVersion: 0.6.0\n"
        ),
        "deeplaw-0.6.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
    }
    rows = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        rows.append(f"{name},sha256={digest},{len(content)}")
    rows.append("deeplaw-0.6.0.dist-info/RECORD,,")
    files["deeplaw-0.6.0.dist-info/RECORD"] = ("\n".join(rows) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    path.write_bytes(buffer.getvalue())


def _fixture(tmp_path: Path) -> Path:
    root = tmp_path / "fixture"
    root.mkdir()
    wheel = root / "deeplaw-0.6.0-py3-none-any.whl"
    _wheel(wheel)
    source = {
        "commit": fixture.SOURCE_COMMIT,
        "tree": fixture.SOURCE_TREE,
        "version": fixture.HISTORICAL_VERSION,
        "source_date_epoch": fixture.SOURCE_DATE_EPOCH,
    }
    body = fixture._provenance_body(source, fixture._inspect_wheel(wheel))
    fixture._write_provenance(root, body)
    return root


def _rewrite_provenance(root: Path, update: dict[str, object]) -> None:
    path = root / fixture.PROVENANCE_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in payload.items() if key != "record_sha256"}
    for key, value in update.items():
        body[key] = value
    path.write_text(
        json.dumps(fixture._with_digest(body), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_verify_synthetic_fixture_writes_env_only_after_full_validation(tmp_path: Path) -> None:
    root = _fixture(tmp_path)
    env = tmp_path / "github.env"
    env.write_text("existing=value\n", encoding="utf-8")

    assert (
        fixture.main(
            ["verify", "--fixture-dir", str(root), "--github-env", str(env)]
        )
        == 0
    )
    lines = env.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "existing=value"
    assert lines[1] == ""
    assert lines[2].startswith("DEEPLAW_V060_WHEEL=")
    assert lines[3].startswith("DEEPLAW_V060_WHEEL_SHA256=")
    assert lines[4] == "DEEPLAW_V060_FIXTURE_VERIFIED=true"


def test_build_discards_uv_gitignore_marker_before_retaining_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = {
        "commit": fixture.SOURCE_COMMIT,
        "tree": fixture.SOURCE_TREE,
        "version": fixture.HISTORICAL_VERSION,
        "source_date_epoch": fixture.SOURCE_DATE_EPOCH,
    }
    monkeypatch.setattr(fixture, "_verify_source", lambda _repository: source)
    monkeypatch.setattr(fixture, "_archive_source", lambda _repository, destination: destination)

    def fake_uv_build(_source: Path, output: Path) -> None:
        _wheel(output / "deeplaw-0.6.0-py3-none-any.whl")
        (output / ".gitignore").write_bytes(b"*")

    monkeypatch.setattr(fixture, "_run_uv_build", fake_uv_build)
    output = tmp_path / "retained"
    fixture.build_fixture(repository, output)
    assert sorted(item.name for item in output.iterdir()) == [
        "deeplaw-0.6.0-py3-none-any.whl",
        fixture.PROVENANCE_FILENAME,
    ]
    assert fixture.verify_fixture(output)["version"] == "0.6.0"


@pytest.mark.parametrize(
    "case", ["missing", "extra", "symlink", "symlink_unavailable", "digest", "source", "record"]
)
def test_verify_rejects_closed_fixture_failures_without_writing_env(
    tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture(tmp_path)
    wheel = root / "deeplaw-0.6.0-py3-none-any.whl"
    if case == "missing":
        wheel.unlink()
    elif case == "extra":
        shutil.copy2(wheel, root / "deeplaw-0.6.0-py3-none-manylinux.whl")
    elif case in {"symlink", "symlink_unavailable"}:
        if case == "symlink_unavailable":
            def unavailable(*_args: object, **_kwargs: object) -> None:
                raise OSError("synthetic symlink unavailable")

            monkeypatch.setattr(os, "symlink", unavailable)
        target = tmp_path / "real-wheel.bin"
        shutil.copy2(wheel, target)
        wheel.unlink()
        try:
            os.symlink(target, wheel)
        except (NotImplementedError, OSError):
            shutil.copy2(target, wheel)
            original_lstat = Path.lstat
            original_mode = list(original_lstat(wheel))
            original_mode[0] = stat.S_IFLNK | stat.S_IRUSR
            symlink_stat = os.stat_result(original_mode)

            def fake_lstat(path: Path) -> os.stat_result:
                if path == wheel:
                    return symlink_stat
                return original_lstat(path)

            monkeypatch.setattr(Path, "lstat", fake_lstat)
    elif case == "digest":
        payload = json.loads((root / fixture.PROVENANCE_FILENAME).read_text(encoding="utf-8"))
        wheel_record = dict(payload["wheel"])
        wheel_record["sha256"] = "0" * 64
        _rewrite_provenance(root, {"wheel": wheel_record})
    elif case == "source":
        source_record = {"commit": "0" * 40, "tree": fixture.SOURCE_TREE, "version": "0.6.0"}
        _rewrite_provenance(root, {"source": source_record})
    else:
        payload = json.loads((root / fixture.PROVENANCE_FILENAME).read_text(encoding="utf-8"))
        payload["record_sha256"] = "0" * 64
        (root / fixture.PROVENANCE_FILENAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    env = tmp_path / "github.env"
    env.write_text("before=1\n", encoding="utf-8")
    assert (
        fixture.main(
            ["verify", "--fixture-dir", str(root), "--github-env", str(env)]
        )
        == 1
    )
    assert env.read_text(encoding="utf-8") == "before=1\n"


def test_source_preflight_rejects_unpinned_build_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    source_replies = {
        ("rev-parse", "--verify", f"{fixture.SOURCE_COMMIT}^{{commit}}"): (
            fixture.SOURCE_COMMIT.encode()
        ),
        ("rev-parse", "--verify", f"{fixture.SOURCE_COMMIT}^{{tree}}"): (
            fixture.SOURCE_TREE.encode()
        ),
        ("show", f"{fixture.SOURCE_COMMIT}:pyproject.toml"): (
            b'[project]\nname = "deeplaw"\nversion = "0.6.0"\n'
        ),
    }
    monkeypatch.setattr(fixture, "_git", lambda _repository, *args: source_replies[args])
    monkeypatch.setattr(fixture, "BUILD_CONSTRAINTS_SHA256", "0" * 64)
    with pytest.raises(fixture.FixtureError, match="build constraints"):
        fixture._verify_source(repository)
