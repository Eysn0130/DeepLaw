from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from deeplaw import document_engine_models
from deeplaw.util import sha256_bytes


def _fake_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    files = (
        document_engine_models.ModelFile(
            "models/layout/config.json",
            6,
            sha256_bytes(b"layout"),
        ),
        document_engine_models.ModelFile(
            "models/ocr/model.safetensors",
            3,
            sha256_bytes(b"ocr"),
        ),
    )
    monkeypatch.setattr(document_engine_models, "_PINNED_MODEL_FILES", files)
    root = tmp_path / "model-root"
    (root / "models/layout").mkdir(parents=True)
    (root / "models/ocr").mkdir(parents=True)
    (root / files[0].path).write_bytes(b"layout")
    (root / files[1].path).write_bytes(b"ocr")
    return root


def test_model_manifest_is_a_closed_content_pin() -> None:
    assert len(document_engine_models._PINNED_MODEL_FILES) == 15
    assert (
        sum(item.byte_size for item in document_engine_models._PINNED_MODEL_FILES)
        == 1_082_446_509
    )
    assert len(document_engine_models.model_manifest_sha256()) == 64
    assert all(len(item.sha256) == 64 for item in document_engine_models._PINNED_MODEL_FILES)


def test_verifies_exact_model_file_set_size_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fake_bundle(tmp_path, monkeypatch)

    verified = document_engine_models.verify_model_root(root)

    assert verified["configured"] is True
    assert verified["file_count"] == 2
    assert verified["total_bytes"] == 9
    assert verified["network_during_ingest"] is False


def test_rejects_extra_or_tampered_model_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fake_bundle(tmp_path, monkeypatch)
    (root / "unexpected.py").write_text("raise RuntimeError()", encoding="utf-8")

    with pytest.raises(RuntimeError, match="file set"):
        document_engine_models.verify_model_root(root)

    (root / "unexpected.py").unlink()
    (root / "models/layout/config.json").write_bytes(b"tamper")
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        document_engine_models.verify_model_root(root)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission policy")
def test_rejects_a_group_writable_model_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fake_bundle(tmp_path, monkeypatch)
    root.chmod(0o770)

    with pytest.raises(RuntimeError, match="group- or world-writable"):
        document_engine_models.verify_model_root(root)


def test_setup_is_explicit_and_writes_an_owner_only_verified_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fake_bundle(tmp_path, monkeypatch)
    home = tmp_path / "home"
    calls: list[dict[str, object]] = []

    def snapshot_download(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(root)

    monkeypatch.setenv("DEEPLAW_HOME", str(home))
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=snapshot_download),
    )

    result = document_engine_models.install_models(local_files_only=True)

    assert calls == [
        {
            "repo_id": document_engine_models.MODEL_REPOSITORY,
            "revision": document_engine_models.MODEL_REVISION,
            "allow_patterns": [item.path for item in document_engine_models._PINNED_MODEL_FILES],
            "local_files_only": True,
        }
    ]
    assert result["configured"] is True
    config = Path(result["config_path"])
    assert config.stat().st_mode & 0o777 == 0o600
    assert document_engine_models.verify_installed_models()["model_root"] == str(root.resolve())


def test_ingest_environment_ignores_upstream_overrides_and_forces_local_offline_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fake_bundle(tmp_path, monkeypatch)
    monkeypatch.setenv("DEEPLAW_HOME", str(tmp_path / "home"))
    document_engine_models._write_model_config(root.resolve())
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "attacker/repository")
    monkeypatch.setenv("MINERU_TOOLS_CONFIG_JSON", "/tmp/attacker.json")
    monkeypatch.setenv("MINERU_DEVICE_MODE", "remote")
    before = dict(os.environ)

    with document_engine_models.isolated_engine_environment() as verified:
        assert verified["model_root"] == str(root.resolve())
        assert os.environ["MINERU_MODEL_SOURCE"] == "local"
        assert os.environ["MINERU_LOCAL_API_LAUNCH_MODE"] == "subprocess"
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        assert "MINERU_DEVICE_MODE" not in os.environ
        config = json.loads(Path(os.environ["MINERU_TOOLS_CONFIG_JSON"]).read_text())
        assert config == {
            "config_version": "1.3",
            "models-dir": {"pipeline": str(root.resolve())},
            "model-source": "local",
        }

    assert dict(os.environ) == before


def test_status_fails_closed_without_a_model_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPLAW_HOME", str(tmp_path / "missing"))

    status = document_engine_models.model_status()

    assert status["configured"] is False
    assert "document-engine setup" in status["error"]


def test_rejects_a_symlinked_model_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    root = home / "document-engine"
    root.mkdir(parents=True)
    target = tmp_path / "outside.json"
    target.write_text("{}\n", encoding="utf-8")
    (root / "models.json").symlink_to(target)
    monkeypatch.setenv("DEEPLAW_HOME", str(home))

    status = document_engine_models.model_status()

    assert status["configured"] is False
