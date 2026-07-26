from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .store import default_home
from .util import canonical_json, sha256_bytes, sha256_file, strict_json_loads

MODEL_CONFIG_SCHEMA = "deeplaw.document-engine-models/v1"
MODEL_REPOSITORY = "opendatalab/PDF-Extract-Kit-1.0"
MODEL_REVISION = "ed6b654c018d742e65a17671e379c5e6ecc87ec9"
PINNED_ENGINE_VERSION = "3.4.4"
_MAX_CONFIG_BYTES = 64 * 1024


@dataclass(frozen=True)
class ModelFile:
    path: str
    byte_size: int
    sha256: str


_PINNED_MODEL_FILES = (
    ModelFile(
        "models/Layout/PP-DocLayoutV2/config.json",
        3_787,
        "18a696b54c64c4fa582afcd3a41407c4b65a99dc7ab187ad2fed8af8e4128ad8",
    ),
    ModelFile(
        "models/Layout/PP-DocLayoutV2/model.safetensors",
        214_798_436,
        "e60f3725aeedc88fd319416ef166bda79171a41516a301c27cab9132dc2739d2",
    ),
    ModelFile(
        "models/Layout/PP-DocLayoutV2/preprocessor_config.json",
        575,
        "56281a70c931a291dcaf653605fb4df713fd823f65e939aecd6005c26346a103",
    ),
    ModelFile(
        "models/MFR/unimernet_hf_small_2503/README.md",
        1_657,
        "96574f3857e919353024edc423b9165dbf46e902cfe97b5b7ec552283bd744f6",
    ),
    ModelFile(
        "models/MFR/unimernet_hf_small_2503/config.json",
        5_094,
        "64c02e9897410658f7668c6a334a8b306276e4697656cbe06f86d8c4f01fc040",
    ),
    ModelFile(
        "models/MFR/unimernet_hf_small_2503/generation_config.json",
        191,
        "d56ca9d5c5efa4283a2565ae42771bafd02910b56ef9c53e9b441c9c4c896d09",
    ),
    ModelFile(
        "models/MFR/unimernet_hf_small_2503/model.safetensors",
        810_036_696,
        "9244e2565585c0f89bc3a6eeeea080ef3c588375fc0d536074fe88e80b917cda",
    ),
    ModelFile(
        "models/MFR/unimernet_hf_small_2503/special_tokens_map.json",
        552,
        "358c249e2fb29060c6b73157d428853b0c48710deffc8ee670ab1013880946c9",
    ),
    ModelFile(
        "models/MFR/unimernet_hf_small_2503/tokenizer.json",
        3_581_950,
        "f8e29e3c3a8017f067b62a3d2d9211bb4cebc08a25afe58d3a6069981e3684d6",
    ),
    ModelFile(
        "models/MFR/unimernet_hf_small_2503/tokenizer_config.json",
        4_522,
        "28b99e33895e06389c26c139b1333b82b7f5d8ed5f4fd14998acfd7c20989338",
    ),
    ModelFile(
        "models/OCR/paddleocr_torch/ch_PP-OCRv6_small_det_infer.safetensors",
        9_938_124,
        "89a96a8adc4e9cd0c994098edc76022e496d35844392562b4694c8fbc583f2da",
    ),
    ModelFile(
        "models/OCR/paddleocr_torch/ch_PP-OCRv6_small_rec_infer.safetensors",
        21_204_736,
        "f65a332afe5aa663f0b9d5706f4ae8457b5b4058a842d5c1eb22df505c27d642",
    ),
    ModelFile(
        "models/TabCls/paddle_table_cls/PP-LCNet_x1_0_table_cls.onnx",
        6_776_877,
        "c84bf1d79c1c74d534b5b12adb14dd12151c42f7ae3e4be4f1042b830f80b949",
    ),
    ModelFile(
        "models/TabRec/SlanetPlus/slanet-plus.onnx",
        7_758_305,
        "d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b",
    ),
    ModelFile(
        "models/TabRec/UnetStructure/unet.onnx",
        8_335_007,
        "0ea48d3a17e35ef5c2e498a5e799566073234d39b1079ca21d9f4fafe73c6d20",
    ),
)
_CONFIG_FIELDS = {
    "schema_version",
    "engine_version",
    "repository",
    "revision",
    "model_root",
    "manifest_sha256",
    "file_count",
    "total_bytes",
    "installed_at",
}


def model_manifest_sha256() -> str:
    payload = {
        "engine_version": PINNED_ENGINE_VERSION,
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "files": [
            {
                "path": item.path,
                "byte_size": item.byte_size,
                "sha256": item.sha256,
            }
            for item in _PINNED_MODEL_FILES
        ],
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def model_config_path() -> Path:
    return default_home().expanduser().absolute() / "document-engine" / "models.json"


def _check_owner_writable_path(path: Path, *, description: str) -> None:
    metadata = path.stat()
    if os.name != "posix":
        return
    if metadata.st_uid not in {0, os.getuid()}:
        raise RuntimeError(f"{description} is not owned by the current OS user")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise RuntimeError(f"{description} must not be group- or world-writable")


def _load_model_config() -> dict[str, Any]:
    path = model_config_path()
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(
            "DeepLaw document models are not configured; run "
            "`deeplaw document-engine setup` explicitly"
        )
    if path.stat().st_size > _MAX_CONFIG_BYTES:
        raise RuntimeError("DeepLaw document model configuration exceeds 64 KiB")
    _check_owner_writable_path(path, description="DeepLaw document model configuration")
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("DeepLaw document model configuration is invalid") from error
    if not isinstance(value, dict) or set(value) != _CONFIG_FIELDS:
        raise RuntimeError("DeepLaw document model configuration has an unknown contract")
    expected = {
        "schema_version": MODEL_CONFIG_SCHEMA,
        "engine_version": PINNED_ENGINE_VERSION,
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "manifest_sha256": model_manifest_sha256(),
        "file_count": len(_PINNED_MODEL_FILES),
        "total_bytes": sum(item.byte_size for item in _PINNED_MODEL_FILES),
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            raise RuntimeError(f"DeepLaw document model configuration has invalid {field}")
    if not isinstance(value.get("installed_at"), str) or not value["installed_at"]:
        raise RuntimeError("DeepLaw document model configuration has invalid installed_at")
    model_root = value.get("model_root")
    if not isinstance(model_root, str) or not Path(model_root).is_absolute():
        raise RuntimeError("DeepLaw document model configuration has invalid model_root")
    return value


def _model_tree_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            if path.is_symlink():
                raise RuntimeError(f"document model directory must not be a symlink: {relative}")
            continue
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
            raise RuntimeError(f"document model bundle contains an unsafe entry: {relative}")
        files.add(relative)
    return files


def verify_model_root(model_root: str | Path) -> dict[str, Any]:
    root = Path(model_root).expanduser()
    if not root.is_absolute():
        raise RuntimeError("DeepLaw document model root must be absolute")
    try:
        root = root.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("DeepLaw document model root does not exist") from error
    if not root.is_dir():
        raise RuntimeError("DeepLaw document model root is not a directory")
    _check_owner_writable_path(root, description="DeepLaw document model root")

    expected_paths = {item.path for item in _PINNED_MODEL_FILES}
    if _model_tree_files(root) != expected_paths:
        raise RuntimeError("DeepLaw document model bundle file set does not match the pin")

    for item in _PINNED_MODEL_FILES:
        path = root / item.path
        if not path.is_file():
            raise RuntimeError(f"DeepLaw document model file is missing: {item.path}")
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise RuntimeError(f"DeepLaw document model file is unsafe: {item.path}")
        _check_owner_writable_path(resolved, description=f"document model file {item.path}")
        if resolved.stat().st_size != item.byte_size:
            raise RuntimeError(f"DeepLaw document model byte size mismatch: {item.path}")
        if sha256_file(resolved) != item.sha256:
            raise RuntimeError(f"DeepLaw document model SHA-256 mismatch: {item.path}")

    return {
        "configured": True,
        "engine_version": PINNED_ENGINE_VERSION,
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "model_root": str(root),
        "manifest_sha256": model_manifest_sha256(),
        "file_count": len(_PINNED_MODEL_FILES),
        "total_bytes": sum(item.byte_size for item in _PINNED_MODEL_FILES),
        "network_during_ingest": False,
    }


def verify_installed_models() -> dict[str, Any]:
    config = _load_model_config()
    verified = verify_model_root(config["model_root"])
    return {**verified, "config_path": str(model_config_path())}


def model_status() -> dict[str, Any]:
    try:
        return verify_installed_models()
    except RuntimeError as error:
        return {
            "configured": False,
            "engine_version": PINNED_ENGINE_VERSION,
            "manifest_sha256": model_manifest_sha256(),
            "config_path": str(model_config_path()),
            "error": str(error),
            "network_during_ingest": False,
        }


def _write_model_config(model_root: Path) -> Path:
    home = default_home().expanduser().absolute()
    if home.is_symlink():
        raise RuntimeError(f"DeepLaw home must not be a symbolic link: {home}")
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not home.is_dir():
        raise RuntimeError(f"DeepLaw home is not a directory: {home}")
    if os.name == "posix":
        os.chmod(home, 0o700)
    root = home / "document-engine"
    if root.is_symlink():
        raise RuntimeError(f"DeepLaw document-engine directory must not be a symlink: {root}")
    root.mkdir(mode=0o700, exist_ok=True)
    if not root.is_dir():
        raise RuntimeError(f"DeepLaw document-engine path is not a directory: {root}")
    if os.name == "posix":
        os.chmod(root, 0o700)

    path = model_config_path()
    payload = {
        "schema_version": MODEL_CONFIG_SCHEMA,
        "engine_version": PINNED_ENGINE_VERSION,
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "model_root": str(model_root),
        "manifest_sha256": model_manifest_sha256(),
        "file_count": len(_PINNED_MODEL_FILES),
        "total_bytes": sum(item.byte_size for item in _PINNED_MODEL_FILES),
        "installed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    temporary = root / f".models.json.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write((canonical_json(payload) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def install_models(*, local_files_only: bool = False) -> dict[str, Any]:
    try:
        from huggingface_hub import snapshot_download
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "DeepLaw document engine is not installed; install "
            "`deeplaw[document-engine]` before model setup"
        ) from error

    try:
        downloaded = snapshot_download(
            repo_id=MODEL_REPOSITORY,
            revision=MODEL_REVISION,
            allow_patterns=[item.path for item in _PINNED_MODEL_FILES],
            local_files_only=local_files_only,
        )
    except Exception as error:
        mode = "local cache" if local_files_only else "pinned model source"
        raise RuntimeError(f"DeepLaw could not provision models from the {mode}") from error
    verified = verify_model_root(Path(downloaded))
    path = _write_model_config(Path(verified["model_root"]))
    return {**verified, "config_path": str(path)}


@contextmanager
def isolated_engine_environment() -> Iterator[dict[str, Any]]:
    verified = verify_installed_models()
    previous = dict(os.environ)
    try:
        for name in tuple(os.environ):
            if name.startswith("MINERU_"):
                os.environ.pop(name, None)
        with TemporaryDirectory(prefix="deeplaw-engine-config-") as temporary:
            root = Path(temporary)
            if os.name == "posix":
                os.chmod(root, 0o700)
            config = root / "models.json"
            config.write_text(
                canonical_json(
                    {
                        "config_version": "1.3",
                        "models-dir": {"pipeline": verified["model_root"]},
                        "model-source": "local",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            if os.name == "posix":
                os.chmod(config, 0o600)
            os.environ.update(
                {
                    "MINERU_TOOLS_CONFIG_JSON": str(config),
                    "MINERU_MODEL_SOURCE": "local",
                    "MINERU_LOCAL_API_LAUNCH_MODE": "subprocess",
                    "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                    "DO_NOT_TRACK": "1",
                    "NO_PROXY": "127.0.0.1,localhost,::1",
                    "no_proxy": "127.0.0.1,localhost,::1",
                }
            )
            yield verified
    finally:
        os.environ.clear()
        os.environ.update(previous)
