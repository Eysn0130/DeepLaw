from __future__ import annotations

import json
import math
import os
import re
import secrets
import shutil
import stat
import struct
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from .knowledge_models import KnowledgeAsset, utc_now
from .knowledge_store import KnowledgeVault
from .store import default_home
from .util import (
    canonical_json,
    normalize_text,
    sha256_bytes,
    sha256_file,
    stable_id,
    strict_json_loads,
)

DISCOVERY_INDEX_SCHEMA = "deeplaw.knowledge-discovery-index/v1"
DISCOVERY_PROJECTION_SCHEMA = "deeplaw.knowledge-discovery-projection/v1"
DISCOVERY_MODEL_SCHEMA = "deeplaw.knowledge-discovery-model/v1"
DISCOVERY_RUNTIME = "deeplaw-onnx-text/1"
DISCOVERY_MODEL_ENV = "DEEPLAW_DISCOVERY_MODEL_ROOT"

_MAX_INDEX_ASSETS = 100_000
_MAX_PROJECTION_CHARS = 2_500
_MAX_QUERY_CHARS = 4_000
_MAX_DISCOVERY_LIMIT = 64
_MAX_RECORDS_BYTES = 64 * 1024 * 1024
_MAX_RECORD_LINE_BYTES = 2_048
_VECTOR_DTYPE = "float16-le"
_VECTOR_BYTES = 2
_MODEL_MANIFEST = "model.json"
_INDEX_MANIFEST = "index.json"
_INDEX_RECORDS = "assets.jsonl"
_INDEX_VECTORS = "vectors.f16"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ASSET_ID = re.compile(r"^asset_[0-9a-f]{24}$")
_DIVERSITY_ID = re.compile(r"^discovery-group_[0-9a-f]{24}$")
_PART_SUFFIX = re.compile(r"\s+· part \d+\Z")
_ROLE_BLOCK = re.compile(
    r"(?:^|\n)(?P<role>USER|ASSISTANT):\n"
    r"(?P<content>.*?)(?=\n(?:USER|ASSISTANT):\n|\Z)",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class DiscoveryModelFile:
    path: str
    byte_size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DiscoveryModelProfile:
    profile: str
    model_id: str
    source_repository: str
    source_revision: str
    dimension: int
    max_tokens: int
    pooling: str
    license: str
    files: tuple[DiscoveryModelFile, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DISCOVERY_MODEL_SCHEMA,
            "profile": self.profile,
            "model_id": self.model_id,
            "source_repository": self.source_repository,
            "source_revision": self.source_revision,
            "dimension": self.dimension,
            "max_tokens": self.max_tokens,
            "pooling": self.pooling,
            "license": self.license,
            "runtime": DISCOVERY_RUNTIME,
            "files": [
                {
                    "path": item.path,
                    "byte_size": item.byte_size,
                    "sha256": item.sha256,
                }
                for item in self.files
            ],
        }


_ENGLISH_PROFILE = DiscoveryModelProfile(
    profile="english",
    model_id="jinaai/jina-embeddings-v2-small-en",
    source_repository="xenova/jina-embeddings-v2-small-en",
    source_revision="523cadcb9c2e71c7153fc46016e1fe79acb4f58f",
    dimension=512,
    max_tokens=512,
    pooling="attention-mask-mean",
    license="Apache-2.0",
    files=(
        DiscoveryModelFile(
            "config.json",
            1_150,
            "7472dfdbfafa39df639cc5e8a23a15f2bd7d26adfde3eb2d6da3612435c39f8e",
        ),
        DiscoveryModelFile(
            "onnx/model.onnx",
            129_799_236,
            "8daf59cab0f24c7e0231a1e3ff9c348a97f6662e9a763dd4f15d2ca2f1614e05",
        ),
        DiscoveryModelFile(
            "special_tokens_map.json",
            125,
            "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
        ),
        DiscoveryModelFile(
            "tokenizer.json",
            711_573,
            "e9f999ac74497843ed9f4303246a8f43d9f100ee8aab8e133667903f447ceb48",
        ),
        DiscoveryModelFile(
            "tokenizer_config.json",
            367,
            "63d41f24b6076d8f189a9f6e7017655a8596f4f536a06bac5cc4da2c79f2b49d",
        ),
    ),
)

_CHINESE_PROFILE = DiscoveryModelProfile(
    profile="chinese-english",
    model_id="jinaai/jina-embeddings-v2-base-zh",
    source_repository="jinaai/jina-embeddings-v2-base-zh",
    source_revision="c1ff9086a89a1123d7b5eff58055a665db4fb4b9",
    dimension=768,
    max_tokens=512,
    pooling="attention-mask-mean",
    license="Apache-2.0",
    files=(
        DiscoveryModelFile(
            "config.json",
            1_441,
            "85a9da5b54ecc2922656a8db6b9628c9123afe0a880062be891e0e4db60d10ff",
        ),
        DiscoveryModelFile(
            "onnx/model.onnx",
            641_212_851,
            "4b0e9fa6e5c77cff56e0c9c673ba1aad61e793e592fdd4b05690b68826b7d3a2",
        ),
        DiscoveryModelFile(
            "special_tokens_map.json",
            280,
            "06e405a36dfe4b9604f484f6a1e619af1a7f7d09e34a8555eb0b77b66318067f",
        ),
        DiscoveryModelFile(
            "tokenizer.json",
            2_030_772,
            "0046da43cc8c424b317f56b092b0512aaaa65c4f925d2f16af9d9eeb4d0ef902",
        ),
        DiscoveryModelFile(
            "tokenizer_config.json",
            1_215,
            "d291c6652d96d56ffdbcf1ea19d9bae5ed79003f7648c627e725a619227ce8fa",
        ),
    ),
)

DISCOVERY_MODEL_PROFILES = {
    profile.profile: profile
    for profile in (
        _ENGLISH_PROFILE,
        _CHINESE_PROFILE,
    )
}


def default_discovery_model_root() -> Path:
    configured = os.environ.get(DISCOVERY_MODEL_ENV)
    if configured is not None:
        if not configured.strip():
            raise RuntimeError(f"{DISCOVERY_MODEL_ENV} must not be blank")
        return Path(configured).expanduser().absolute()
    return default_home() / "models" / "discovery"


def _profile(name: str) -> DiscoveryModelProfile:
    try:
        return DISCOVERY_MODEL_PROFILES[name]
    except KeyError as error:
        raise ValueError(f"unsupported discovery model profile: {name}") from error


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and not value.startswith("/")
        and not value.endswith("/")
        and "\\" not in value
        and ".." not in path.parts
        and path.as_posix() == value
    )


def _owner_directory(path: Path) -> Path:
    if path.is_symlink():
        raise RuntimeError("discovery directory must not be a symbolic link")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise RuntimeError("discovery path is not a directory")
    os.chmod(path, 0o700)
    return path


def _write_owner_file(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise RuntimeError("discovery output must not be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _model_directory(
    profile: DiscoveryModelProfile,
    *,
    model_root: str | Path | None,
) -> Path:
    root = (
        Path(model_root).expanduser().absolute()
        if model_root is not None
        else default_discovery_model_root()
    )
    return root / profile.profile / profile.source_revision


def _validate_model_manifest(
    value: Any,
    *,
    profile: DiscoveryModelProfile,
) -> None:
    if value != profile.to_dict():
        raise RuntimeError("discovery model manifest does not match the pinned profile")


def verify_discovery_model(
    profile_name: str,
    *,
    model_root: str | Path | None = None,
) -> dict[str, Any]:
    profile = _profile(profile_name)
    directory = _model_directory(profile, model_root=model_root)
    if directory.is_symlink() or not directory.is_dir():
        raise RuntimeError("pinned discovery model directory is missing or unsafe")
    if os.name != "nt" and stat.S_IMODE(directory.stat().st_mode) & 0o077:
        raise RuntimeError("pinned discovery model directory must be owner-only")
    if os.name == "nt":
        from .windows_acl import native_windows_acl_report

        acl = native_windows_acl_report(directory)
        if not acl["permissions_verified"]:
            raise RuntimeError("pinned discovery model Windows ACL is not owner-only")
    expected_paths = {_MODEL_MANIFEST, *(item.path for item in profile.files)}
    actual_paths: set[str] = set()
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("pinned discovery model contains a symbolic link")
        if stat.S_ISDIR(metadata.st_mode):
            if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
                raise RuntimeError(
                    "pinned discovery model directories must be owner-only"
                )
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("pinned discovery model contains an unsafe entry")
        actual_paths.add(relative)
    if actual_paths != expected_paths:
        raise RuntimeError("pinned discovery model file inventory is invalid")
    manifest_path = directory / _MODEL_MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError("pinned discovery model manifest is missing or unsafe")
    if os.name != "nt" and stat.S_IMODE(manifest_path.stat().st_mode) & 0o077:
        raise RuntimeError("pinned discovery model files must be owner-only")
    try:
        manifest = strict_json_loads(manifest_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("pinned discovery model manifest is invalid") from error
    _validate_model_manifest(manifest, profile=profile)
    checks: list[dict[str, Any]] = []
    for item in profile.files:
        path = directory / item.path
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("pinned discovery model contains a missing or unsafe file")
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError("pinned discovery model files must be owner-only")
        valid = (
            path.stat().st_size == item.byte_size
            and sha256_file(path) == item.sha256
        )
        checks.append(
            {
                "path": item.path,
                "byte_size": item.byte_size,
                "sha256": item.sha256,
                "valid": valid,
            }
        )
    if not all(check["valid"] for check in checks):
        raise RuntimeError("pinned discovery model failed size or SHA-256 verification")
    return {
        "schema_version": "deeplaw.knowledge-discovery-model-status/v1",
        "installed": True,
        "profile": profile.profile,
        "model_id": profile.model_id,
        "source_repository": profile.source_repository,
        "source_revision": profile.source_revision,
        "dimension": profile.dimension,
        "license": profile.license,
        "runtime": DISCOVERY_RUNTIME,
        "model_path": str(directory),
        "files": checks,
    }


def setup_discovery_model(
    profile_name: str,
    *,
    model_root: str | Path | None = None,
    local_files_only: bool = False,
) -> dict[str, Any]:
    profile = _profile(profile_name)
    destination = _model_directory(profile, model_root=model_root)
    if destination.exists():
        result = verify_discovery_model(profile_name, model_root=model_root)
        result["created"] = False
        return result
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as error:
        raise RuntimeError(
            "discovery model setup requires the optional 'deeplaw[discovery]' "
            "dependencies"
        ) from error
    root = _owner_directory(destination.parent)
    temporary = root / f".{profile.source_revision}.{secrets.token_hex(8)}.tmp"
    temporary.mkdir(mode=0o700)
    try:
        for item in profile.files:
            downloaded = Path(
                hf_hub_download(
                    repo_id=profile.source_repository,
                    filename=item.path,
                    revision=profile.source_revision,
                    local_files_only=local_files_only,
                )
            )
            if (
                downloaded.is_symlink()
                and not downloaded.resolve(strict=True).is_file()
            ):
                raise RuntimeError("downloaded discovery model file is unsafe")
            target = temporary / item.path
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(downloaded, target)
            os.chmod(target, 0o600)
            if (
                target.stat().st_size != item.byte_size
                or sha256_file(target) != item.sha256
            ):
                raise RuntimeError(
                    "downloaded discovery model file does not match its pinned identity"
                )
        _write_owner_file(
            temporary / _MODEL_MANIFEST,
            (json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        os.replace(temporary, destination)
        os.chmod(destination, 0o700)
        if os.name == "nt":
            from .windows_acl import harden_windows_vault

            harden_windows_vault(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    result = verify_discovery_model(profile_name, model_root=model_root)
    result["created"] = True
    return result


def _validated_onnx_input_names(values: Iterable[str]) -> frozenset[str]:
    names = frozenset(values)
    if names not in (
        frozenset({"input_ids", "attention_mask"}),
        frozenset({"input_ids", "attention_mask", "token_type_ids"}),
    ):
        raise RuntimeError("pinned discovery model has an unsupported input contract")
    return names


class OnnxDiscoveryModel:
    def __init__(
        self,
        profile_name: str,
        *,
        model_root: str | Path | None = None,
        threads: int | None = None,
    ) -> None:
        self.profile = _profile(profile_name)
        status = verify_discovery_model(profile_name, model_root=model_root)
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise RuntimeError(
                "discovery execution requires the optional 'deeplaw[discovery]' "
                "dependencies"
            ) from error
        if (
            threads is not None
            and (
                isinstance(threads, bool)
                or not isinstance(threads, int)
                or not 1 <= threads <= 256
            )
        ):
            raise ValueError("discovery threads must be between 1 and 256")
        self._numpy = np
        tokenizer_path = Path(status["model_path"]) / "tokenizer.json"
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_truncation(max_length=self.profile.max_tokens)
        self._tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        if threads is not None:
            options.intra_op_num_threads = threads
            options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(Path(status["model_path"]) / "onnx" / "model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._input_names = _validated_onnx_input_names(
            item.name for item in self._session.get_inputs()
        )
        outputs = self._session.get_outputs()
        if len(outputs) != 1 or outputs[0].name != "last_hidden_state":
            raise RuntimeError("pinned discovery model has an unsupported output contract")

    def _embed(self, values: Sequence[str]) -> list[Sequence[float]]:
        if not values:
            return []
        encodings = self._tokenizer.encode_batch(list(values))
        inputs = {
            "input_ids": self._numpy.asarray(
                [encoding.ids for encoding in encodings],
                dtype=self._numpy.int64,
            ),
            "attention_mask": self._numpy.asarray(
                [encoding.attention_mask for encoding in encodings],
                dtype=self._numpy.int64,
            ),
        }
        if "token_type_ids" in self._input_names:
            inputs["token_type_ids"] = self._numpy.asarray(
                [encoding.type_ids for encoding in encodings],
                dtype=self._numpy.int64,
            )
        hidden = self._session.run(["last_hidden_state"], inputs)[0]
        if (
            hidden.ndim != 3
            or hidden.shape[0] != len(values)
            or hidden.shape[2] != self.profile.dimension
        ):
            raise RuntimeError("pinned discovery model returned an invalid output shape")
        attention = inputs["attention_mask"][..., None].astype(
            self._numpy.float64
        )
        pooled = (
            hidden.astype(self._numpy.float64) * attention
        ).sum(axis=1) / self._numpy.maximum(attention.sum(axis=1), 1.0)
        if not self._numpy.isfinite(pooled).all():
            raise RuntimeError("pinned discovery model returned non-finite output")
        return [row for row in pooled]

    def embed_documents(
        self,
        values: Sequence[str],
    ) -> Iterable[Sequence[float]]:
        return self._embed(values)

    def embed_query(self, value: str) -> Sequence[float]:
        return self._embed((value,))[0]


def _covered_text(text: str, *, limit: int = _MAX_PROJECTION_CHARS) -> str:
    normalized = normalize_text(text)
    if len(normalized) <= limit:
        return normalized
    separator = "\n[…]\n"
    head = limit * 2 // 5
    middle = limit // 5
    tail = limit - head - middle - 2 * len(separator)
    center = len(normalized) // 2
    middle_start = max(head, center - middle // 2)
    return (
        normalized[:head]
        + separator
        + normalized[middle_start : middle_start + middle]
        + separator
        + normalized[-tail:]
    )


def discovery_projection(asset: KnowledgeAsset) -> str:
    statement = asset.statement
    if "conversation" in asset.tags:
        user_blocks = [
            match.group("content").strip()
            for match in _ROLE_BLOCK.finditer(statement)
            if match.group("role") == "USER" and match.group("content").strip()
        ]
        if user_blocks:
            statement = "\n".join(user_blocks)
    return _covered_text(f"{asset.title}\n{statement}")


def discovery_diversity_key(asset: KnowledgeAsset) -> str:
    if asset.semantic_key is not None:
        basis = f"semantic\0{asset.semantic_key}"
    elif asset.source_refs:
        title = _PART_SUFFIX.sub("", normalize_text(asset.title))
        basis = f"source\0{asset.source_refs[0].source_id}\0{title}"
    else:
        basis = f"asset\0{asset.asset_id}"
    return stable_id(
        "discovery-group",
        sha256_bytes(basis.encode("utf-8")),
        length=24,
    )


def _index_basis(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in manifest.items()
        if key not in {"index_id"}
    }


def _validate_vector(
    value: Sequence[float],
    *,
    dimension: int,
) -> list[float]:
    if len(value) != dimension:
        raise ValueError("discovery embedder returned an unexpected vector dimension")
    vector = [float(item) for item in value]
    if not all(math.isfinite(item) for item in vector):
        raise ValueError("discovery embedder returned a non-finite vector")
    norm = math.sqrt(sum(item * item for item in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("discovery embedder returned a zero or invalid vector")
    return [item / norm for item in vector]


def _write_index_with_embedder(
    vault: KnowledgeVault,
    output: str | Path,
    *,
    profile: DiscoveryModelProfile,
    embed_documents: Callable[[Sequence[str]], Iterable[Sequence[float]]],
    confirm_no_case_data: bool,
) -> dict[str, Any]:
    if not confirm_no_case_data:
        raise ValueError(
            "discovery indexing requires confirmation that the vault contains no "
            "Analytix case material"
        )
    if not vault.verify_integrity()["valid"]:
        raise RuntimeError("knowledge vault integrity is invalid; discovery build stopped")
    now = utc_now()
    assets = [
        asset
        for asset in vault.all_assets(statuses=("active",))
        if asset.verification == "human_verified"
        and asset.sensitivity != "restricted"
        and (asset.expires_at is None or asset.expires_at > now)
    ]
    if not assets:
        raise ValueError("no Agent-readable active assets are available for discovery")
    if len(assets) > _MAX_INDEX_ASSETS:
        raise ValueError("discovery asset count exceeds the 100000-record bound")
    assets.sort(key=lambda asset: asset.asset_id)
    source_integrity = vault.verify_source_files(
        reference.source_id
        for asset in assets
        for reference in asset.source_refs
    )
    if not source_integrity["valid"]:
        raise RuntimeError("discovery source evidence failed integrity verification")

    output_path = Path(output).expanduser().absolute()
    if output_path.is_symlink():
        raise RuntimeError("discovery index output must not be a symbolic link")
    if output_path.exists():
        raise FileExistsError("discovery index output already exists")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.parent.is_symlink() or not output_path.parent.is_dir():
        raise RuntimeError("discovery index parent must be a real directory")
    temporary = output_path.with_name(
        f".{output_path.name}.{secrets.token_hex(8)}.tmp"
    )
    temporary.mkdir(mode=0o700)
    records_path = temporary / _INDEX_RECORDS
    vectors_path = temporary / _INDEX_VECTORS
    record_descriptor = os.open(
        records_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    vector_descriptor = os.open(
        vectors_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with (
            os.fdopen(record_descriptor, "wb") as record_stream,
            os.fdopen(vector_descriptor, "wb") as vector_stream,
        ):
            for start in range(0, len(assets), 16):
                batch = assets[start : start + 16]
                projections = [discovery_projection(asset) for asset in batch]
                vectors = list(embed_documents(projections))
                if len(vectors) != len(batch):
                    raise ValueError(
                        "discovery embedder returned an unexpected vector count"
                    )
                for offset, (asset, projection, vector) in enumerate(
                    zip(batch, projections, vectors, strict=True)
                ):
                    normalized = _validate_vector(
                        vector,
                        dimension=profile.dimension,
                    )
                    vector_stream.write(
                        struct.pack(
                            f"<{profile.dimension}e",
                            *normalized,
                        )
                    )
                    record = {
                        "ordinal": start + offset,
                        "asset_id": asset.asset_id,
                        "diversity_key": discovery_diversity_key(asset),
                        "content_sha256": asset.content_sha256,
                        "projection_sha256": sha256_bytes(
                            projection.encode("utf-8")
                        ),
                        "projection_chars": len(projection),
                    }
                    record_stream.write(
                        (canonical_json(record) + "\n").encode("utf-8")
                    )
            record_stream.flush()
            vector_stream.flush()
            os.fsync(record_stream.fileno())
            os.fsync(vector_stream.fileno())
        records_sha256 = sha256_file(records_path)
        vectors_sha256 = sha256_file(vectors_path)
        manifest: dict[str, Any] = {
            "schema_version": DISCOVERY_INDEX_SCHEMA,
            "created_at": now,
            "vault": {
                "vault_id": vault.vault_id,
                "revision": vault.revision,
                "audit_head": vault.audit_head,
            },
            "model": profile.to_dict(),
            "projection": {
                "schema_version": DISCOVERY_PROJECTION_SCHEMA,
                "max_chars": _MAX_PROJECTION_CHARS,
                "coverage": "head-middle-tail",
                "conversation_signal": "user-role-first-with-source-preserved",
            },
            "asset_count": len(assets),
            "records": {
                "path": _INDEX_RECORDS,
                "byte_size": records_path.stat().st_size,
                "sha256": records_sha256,
            },
            "vectors": {
                "path": _INDEX_VECTORS,
                "dtype": _VECTOR_DTYPE,
                "dimension": profile.dimension,
                "row_bytes": profile.dimension * _VECTOR_BYTES,
                "byte_size": vectors_path.stat().st_size,
                "sha256": vectors_sha256,
            },
            "policy": {
                "derived": True,
                "authoritative": False,
                "legal_authority": False,
                "case_data_allowed": False,
                "active_human_verified_only": True,
                "restricted_assets_indexed": False,
                "default_runtime_enabled": False,
            },
        }
        manifest["index_id"] = stable_id(
            "discovery",
            sha256_bytes(
                canonical_json(_index_basis(manifest)).encode("utf-8")
            ),
            length=32,
        )
        _write_owner_file(
            temporary / _INDEX_MANIFEST,
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        os.replace(temporary, output_path)
        os.chmod(output_path, 0o700)
        if os.name == "nt":
            from .windows_acl import harden_windows_vault

            harden_windows_vault(output_path)
    except BaseException:
        with suppress(OSError):
            os.close(record_descriptor)
        with suppress(OSError):
            os.close(vector_descriptor)
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    verification = verify_discovery_index(output_path, vault=vault)
    if not verification["valid"]:
        shutil.rmtree(output_path, ignore_errors=True)
        raise RuntimeError("exported discovery index failed its own verification")
    return {
        **manifest,
        "path": str(output_path),
        "verification": verification,
    }


def build_discovery_index(
    vault: KnowledgeVault,
    output: str | Path,
    *,
    profile_name: str,
    model_root: str | Path | None = None,
    confirm_no_case_data: bool,
    threads: int | None = None,
) -> dict[str, Any]:
    model = OnnxDiscoveryModel(
        profile_name,
        model_root=model_root,
        threads=threads,
    )
    return _write_index_with_embedder(
        vault,
        output,
        profile=model.profile,
        embed_documents=model.embed_documents,
        confirm_no_case_data=confirm_no_case_data,
    )


def _load_index_manifest(path: Path) -> dict[str, Any]:
    manifest_path = path / _INDEX_MANIFEST
    if (
        path.is_symlink()
        or not path.is_dir()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 128 * 1024
    ):
        raise RuntimeError("discovery index is missing or unsafe")
    if os.name != "nt":
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError("discovery index directory must be owner-only")
        if stat.S_IMODE(manifest_path.stat().st_mode) & 0o077:
            raise RuntimeError("discovery index files must be owner-only")
    else:
        from .windows_acl import native_windows_acl_report

        acl = native_windows_acl_report(path)
        if not acl["permissions_verified"]:
            raise RuntimeError("discovery index Windows ACL is not owner-only")
    try:
        value = strict_json_loads(manifest_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("discovery index manifest is invalid") from error
    contract = (
        Path(__file__).resolve().parent
        / "contracts"
        / "knowledge-discovery-index.v1.schema.json"
    )
    if not contract.is_file():
        contract = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "knowledge-discovery-index.v1.schema.json"
        )
    schema = strict_json_loads(contract.read_bytes())
    if next(Draft202012Validator(schema).iter_errors(value), None) is not None:
        raise RuntimeError("discovery index manifest violates its closed contract")
    return value


def _load_index_records(path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records_path = path / manifest["records"]["path"]
    if (
        records_path.is_symlink()
        or not records_path.is_file()
        or records_path.stat().st_size > _MAX_RECORDS_BYTES
    ):
        raise RuntimeError("discovery index records are missing or unsafe")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        with records_path.open("r", encoding="utf-8", errors="strict") as stream:
            for line_number, line in enumerate(stream, start=1):
                if len(records) >= _MAX_INDEX_ASSETS:
                    raise RuntimeError("discovery index record count exceeds the bound")
                if len(line.encode("utf-8")) > _MAX_RECORD_LINE_BYTES:
                    raise RuntimeError(
                        f"discovery index record {line_number} exceeds the bound"
                    )
                value = strict_json_loads(line)
                expected = {
                    "ordinal",
                    "asset_id",
                    "diversity_key",
                    "content_sha256",
                    "projection_sha256",
                    "projection_chars",
                }
                if (
                    not isinstance(value, dict)
                    or set(value) != expected
                    or value["ordinal"] != len(records)
                    or not isinstance(value["asset_id"], str)
                    or not _ASSET_ID.fullmatch(value["asset_id"])
                    or value["asset_id"] in seen
                    or not isinstance(value["diversity_key"], str)
                    or not _DIVERSITY_ID.fullmatch(value["diversity_key"])
                    or not isinstance(value["content_sha256"], str)
                    or not _SHA256.fullmatch(value["content_sha256"])
                    or not isinstance(value["projection_sha256"], str)
                    or not _SHA256.fullmatch(value["projection_sha256"])
                    or isinstance(value["projection_chars"], bool)
                    or not isinstance(value["projection_chars"], int)
                    or not 1 <= value["projection_chars"] <= _MAX_PROJECTION_CHARS
                ):
                    raise RuntimeError(
                        f"discovery index record {line_number} is invalid"
                    )
                seen.add(value["asset_id"])
                records.append(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("discovery index records are invalid") from error
    return records


def verify_discovery_index(
    path: str | Path,
    *,
    vault: KnowledgeVault | None = None,
) -> dict[str, Any]:
    index_path = Path(path).expanduser().absolute()
    manifest = _load_index_manifest(index_path)
    expected_files = {
        _INDEX_MANIFEST,
        manifest["records"]["path"],
        manifest["vectors"]["path"],
    }
    actual_files = {
        item.relative_to(index_path).as_posix()
        for item in index_path.rglob("*")
        if item.is_file() or item.is_symlink()
    }
    inventory_valid = actual_files == expected_files
    checks: list[dict[str, Any]] = []
    for field in ("records", "vectors"):
        entry = manifest[field]
        relative = entry["path"]
        target = index_path / relative
        valid = (
            _safe_relative_path(relative)
            and not target.is_symlink()
            and target.is_file()
            and target.stat().st_size == entry["byte_size"]
            and sha256_file(target) == entry["sha256"]
        )
        if (
            valid
            and os.name != "nt"
            and stat.S_IMODE(target.stat().st_mode) & 0o077
        ):
            valid = False
        checks.append({"path": relative, "valid": valid})
    records = _load_index_records(index_path, manifest)
    record_count_valid = len(records) == manifest["asset_count"]
    profile = _profile(manifest["model"]["profile"])
    vector_contract_valid = (
        manifest["vectors"]["dimension"] == profile.dimension
        and manifest["vectors"]["row_bytes"]
        == profile.dimension * _VECTOR_BYTES
    )
    vector_size_valid = (
        vector_contract_valid
        and manifest["vectors"]["byte_size"]
        == manifest["asset_count"] * manifest["vectors"]["row_bytes"]
    )
    expected_id = stable_id(
        "discovery",
        sha256_bytes(
            canonical_json(_index_basis(manifest)).encode("utf-8")
        ),
        length=32,
    )
    index_id_valid = manifest["index_id"] == expected_id
    model_identity_valid = manifest["model"] == profile.to_dict()
    vault_binding_valid: bool | None = None
    assets_valid: bool | None = None
    source_files_valid: bool | None = None
    if vault is not None:
        vault_binding_valid = (
            vault.verify_integrity()["valid"]
            and manifest["vault"]
            == {
                "vault_id": vault.vault_id,
                "revision": vault.revision,
                "audit_head": vault.audit_head,
            }
        )
        assets_valid = vault_binding_valid
        source_ids: list[str] = []
        now = utc_now()
        if assets_valid:
            for record in records:
                try:
                    asset = vault.get_asset(record["asset_id"])
                except (KeyError, ValueError):
                    assets_valid = False
                    break
                projection = discovery_projection(asset)
                if (
                    asset.status != "active"
                    or asset.verification != "human_verified"
                    or asset.sensitivity == "restricted"
                    or (asset.expires_at is not None and asset.expires_at <= now)
                    or asset.content_sha256 != record["content_sha256"]
                    or discovery_diversity_key(asset) != record["diversity_key"]
                    or len(projection) != record["projection_chars"]
                    or sha256_bytes(projection.encode("utf-8"))
                    != record["projection_sha256"]
                ):
                    assets_valid = False
                    break
                source_ids.extend(
                    reference.source_id for reference in asset.source_refs
                )
        if assets_valid:
            source_files_valid = vault.verify_source_files(source_ids)["valid"]
        else:
            source_files_valid = False
    valid = (
        inventory_valid
        and all(check["valid"] for check in checks)
        and record_count_valid
        and vector_size_valid
        and index_id_valid
        and model_identity_valid
        and (vault_binding_valid is not False)
        and (assets_valid is not False)
        and (source_files_valid is not False)
    )
    return {
        "schema_version": "deeplaw.knowledge-discovery-verification/v1",
        "index_id": manifest["index_id"],
        "model_profile": manifest["model"]["profile"],
        "asset_count": manifest["asset_count"],
        "inventory_valid": inventory_valid,
        "file_checks": checks,
        "record_count_valid": record_count_valid,
        "vector_contract_valid": vector_contract_valid,
        "vector_size_valid": vector_size_valid,
        "index_id_valid": index_id_valid,
        "model_identity_valid": model_identity_valid,
        "vault_binding_valid": vault_binding_valid,
        "assets_valid": assets_valid,
        "source_files_valid": source_files_valid,
        "derived": True,
        "authoritative": False,
        "legal_authority": False,
        "valid": valid,
    }


class DiscoveryIndex:
    def __init__(
        self,
        path: str | Path,
        *,
        vault: KnowledgeVault,
        model_root: str | Path | None = None,
        threads: int | None = None,
    ) -> None:
        self.path = Path(path).expanduser().absolute()
        verification = verify_discovery_index(self.path, vault=vault)
        if not verification["valid"]:
            raise RuntimeError("discovery index verification failed")
        self.manifest = _load_index_manifest(self.path)
        self.records = _load_index_records(self.path, self.manifest)
        self._vectors_path = self.path / self.manifest["vectors"]["path"]
        self._fingerprint = self._file_fingerprint()
        self._model = OnnxDiscoveryModel(
            self.manifest["model"]["profile"],
            model_root=model_root,
            threads=threads,
        )
        if self._model.profile.to_dict() != self.manifest["model"]:
            raise RuntimeError("discovery index model identity is unavailable")

    def _file_fingerprint(self) -> tuple[tuple[int, int, int, int], ...]:
        values = []
        for path in (
            self.path / _INDEX_MANIFEST,
            self.path / _INDEX_RECORDS,
            self.path / _INDEX_VECTORS,
        ):
            stat_result = path.stat()
            values.append(
                (
                    stat_result.st_ino,
                    stat_result.st_size,
                    stat_result.st_mtime_ns,
                    stat_result.st_ctime_ns,
                )
            )
        return tuple(values)

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        query = query.strip()
        if not query or len(query) > _MAX_QUERY_CHARS:
            raise ValueError("discovery query must be between 1 and 4000 characters")
        if isinstance(limit, bool) or not 1 <= limit <= _MAX_DISCOVERY_LIMIT:
            raise ValueError(
                f"discovery limit must be between 1 and {_MAX_DISCOVERY_LIMIT}"
            )
        if self._file_fingerprint() != self._fingerprint:
            raise RuntimeError("discovery index changed after verification")
        try:
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                "discovery search requires the optional 'deeplaw[discovery]' "
                "dependencies"
            ) from error
        dimension = self.manifest["vectors"]["dimension"]
        query_vector = np.asarray(
            _validate_vector(
                self._model.embed_query(query),
                dimension=dimension,
            ),
            dtype=np.float32,
        )
        matrix = np.memmap(
            self._vectors_path,
            dtype="<f2",
            mode="r",
            shape=(len(self.records), dimension),
        )
        candidates: list[tuple[float, int]] = []
        per_chunk = min(_MAX_DISCOVERY_LIMIT * 4, max(limit * 4, limit))
        for start in range(0, len(self.records), 4_096):
            stop = min(len(self.records), start + 4_096)
            scores = np.einsum(
                "ij,j->i",
                np.asarray(matrix[start:stop], dtype=np.float32),
                query_vector,
                optimize=True,
            )
            if not np.isfinite(scores).all():
                raise RuntimeError("discovery index produced non-finite scores")
            take = min(per_chunk, len(scores))
            if not take:
                continue
            local = np.argpartition(scores, -take)[-take:]
            candidates.extend(
                (float(scores[index]), start + int(index))
                for index in local
            )
        candidates.sort(key=lambda item: (-item[0], self.records[item[1]]["asset_id"]))
        selected: list[int] = []
        seen_groups: set[str] = set()
        for _, index in candidates:
            diversity_key = self.records[index]["diversity_key"]
            if diversity_key in seen_groups:
                continue
            seen_groups.add(diversity_key)
            selected.append(index)
            if len(selected) == limit:
                break
        return [
            {
                "rank": rank,
                "asset_id": self.records[index]["asset_id"],
                "hit_reason": "derived_semantic_discovery",
            }
            for rank, index in enumerate(selected, start=1)
        ]
