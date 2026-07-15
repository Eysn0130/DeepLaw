from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

CATALOG_SIGNATURE_SCHEMA = "deeplaw.catalog-signature/v1"
CATALOG_TRUST_SCHEMA = "deeplaw.catalog-trust/v1"
CATALOG_SIGNATURE_SUFFIX = ".sig"
DEFAULT_TRUST_RESOURCE = "official-catalog-keys.v1.json"
SIGNING_KEY_ENV = "DEEPLAW_SIGNING_KEY_FILE"
_SIGNING_CONTEXT = b"deeplaw.catalog-signature/v1\x00"
_MAX_SIGNATURE_BYTES = 64 * 1024
_MAX_TRUST_BYTES = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KEY_ID = re.compile(r"^ed25519:[0-9a-f]{64}$")
_SIGNATURE_FIELDS = {
    "schemaVersion",
    "algorithm",
    "keyId",
    "catalogSha256",
    "signature",
}
_TRUST_FIELDS = {"schemaVersion", "keys"}
_TRUST_KEY_FIELDS = {"keyId", "algorithm", "publicKey", "status"}


def default_signing_key_path() -> Path:
    configured = os.environ.get(SIGNING_KEY_ENV)
    if configured is not None:
        if not configured.strip():
            raise RuntimeError(f"{SIGNING_KEY_ENV} must not be blank")
        return Path(configured).expanduser().absolute()
    return Path("~/.config/deeplaw/signing/official-catalog-ed25519.pem").expanduser()


def bundled_trust_store_path() -> Path:
    packaged = Path(__file__).resolve().parent / "trust" / DEFAULT_TRUST_RESOURCE
    if packaged.is_file():
        return packaged
    repository = Path(__file__).resolve().parents[2] / "trust" / DEFAULT_TRUST_RESOURCE
    if repository.is_file():
        return repository
    raise RuntimeError("bundled DeepLaw catalog trust store is missing")


def _public_key_bytes(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def public_key_id(public_key: Ed25519PublicKey) -> str:
    return f"ed25519:{hashlib.sha256(_public_key_bytes(public_key)).hexdigest()}"


def _secure_key_directory(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink():
        raise RuntimeError(f"signing key directory must not be a symbolic link: {parent}")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not parent.is_dir():
        raise RuntimeError(f"signing key directory is not a directory: {parent}")
    os.chmod(parent, 0o700)


def initialize_signing_key(path: str | Path | None = None) -> dict[str, Any]:
    key_path = (
        Path(path).expanduser().absolute() if path is not None else default_signing_key_path()
    )
    if key_path.is_symlink():
        raise RuntimeError(f"signing key must not be a symbolic link: {key_path}")
    _secure_key_directory(key_path)
    created = False
    if not key_path.exists():
        private_key = Ed25519PrivateKey.generate()
        payload = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            key_path.unlink(missing_ok=True)
            raise
        created = True
    if not key_path.is_file():
        raise RuntimeError(f"signing key is not a regular file: {key_path}")
    os.chmod(key_path, 0o600)
    private_key = load_signing_key(key_path)
    return {
        "created": created,
        "key_file": str(key_path),
        "key_id": public_key_id(private_key.public_key()),
        "key_file_mode": "0600",
        "key_directory_mode": "0700",
    }


def load_signing_key(path: str | Path | None = None) -> Ed25519PrivateKey:
    key_path = (
        Path(path).expanduser().absolute() if path is not None else default_signing_key_path()
    )
    if key_path.is_symlink() or not key_path.is_file():
        raise FileNotFoundError(f"DeepLaw signing key is missing or unsafe: {key_path}")
    if key_path.stat().st_size > 64 * 1024:
        raise RuntimeError("DeepLaw signing key is unexpectedly large")
    mode = stat.S_IMODE(key_path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError("DeepLaw signing key must be readable only by its owner")
    try:
        private_key = serialization.load_pem_private_key(
            key_path.read_bytes(),
            password=None,
        )
    except (TypeError, ValueError) as error:
        raise RuntimeError("DeepLaw signing key is not a valid unencrypted PEM key") from error
    if not isinstance(private_key, Ed25519PrivateKey):
        raise RuntimeError("DeepLaw signing key must be an Ed25519 private key")
    return private_key


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _atomic_public_write(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise RuntimeError(f"public signing metadata must not be a symbolic link: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.is_symlink():
        raise RuntimeError(f"temporary signing metadata must not be a symbolic link: {temporary}")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _trust_key_record(public_key: Ed25519PublicKey) -> dict[str, str]:
    return {
        "algorithm": "Ed25519",
        "keyId": public_key_id(public_key),
        "publicKey": base64.b64encode(_public_key_bytes(public_key)).decode("ascii"),
        "status": "active",
    }


def export_trust_store(
    output_path: str | Path,
    *,
    key_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(output_path).expanduser().absolute()
    private_key = load_signing_key(key_path)
    record = _trust_key_record(private_key.public_key())
    keys: list[dict[str, str]] = []
    if path.exists():
        existing = _load_trust_value(path)
        keys = [dict(item) for item in existing["keys"]]
    if not any(item["keyId"] == record["keyId"] for item in keys):
        keys.append(record)
    value = {"schemaVersion": CATALOG_TRUST_SCHEMA, "keys": keys}
    _validate_trust_value(value)
    _atomic_public_write(path, _json_bytes(value))
    return {"trust_file": str(path), "key_id": record["keyId"], "key_count": len(keys)}


def sign_catalog_bytes(
    catalog_payload: bytes,
    *,
    key_path: str | Path | None = None,
) -> bytes:
    private_key = load_signing_key(key_path)
    key_id = public_key_id(private_key.public_key())
    signature = private_key.sign(_SIGNING_CONTEXT + catalog_payload)
    value = {
        "schemaVersion": CATALOG_SIGNATURE_SCHEMA,
        "algorithm": "Ed25519",
        "keyId": key_id,
        "catalogSha256": hashlib.sha256(catalog_payload).hexdigest(),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return _json_bytes(value)


def sign_catalog_file(
    catalog_path: str | Path,
    *,
    signature_path: str | Path | None = None,
    key_path: str | Path | None = None,
) -> dict[str, Any]:
    catalog = Path(catalog_path).expanduser()
    if catalog.is_symlink():
        raise ValueError("catalog to sign must not be a symbolic link")
    catalog = catalog.resolve(strict=True)
    if not catalog.is_file():
        raise ValueError("catalog to sign must be a regular file")
    payload = catalog.read_bytes()
    output = (
        Path(signature_path).expanduser().absolute()
        if signature_path is not None
        else Path(f"{catalog}{CATALOG_SIGNATURE_SUFFIX}")
    )
    signature_payload = sign_catalog_bytes(payload, key_path=key_path)
    _atomic_public_write(output, signature_payload)
    signature = _validate_signature_value(json.loads(signature_payload))
    return {
        "catalog_file": str(catalog),
        "catalog_sha256": signature["catalogSha256"],
        "signature_file": str(output),
        "signature_sha256": hashlib.sha256(signature_payload).hexdigest(),
        "key_id": signature["keyId"],
    }


def _decode_base64(value: Any, *, field: str, length: int) -> bytes:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be base64 text")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"{field} is not valid base64") from error
    if len(decoded) != length:
        raise ValueError(f"{field} has an invalid length")
    return decoded


def _validate_signature_value(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != _SIGNATURE_FIELDS:
        raise ValueError("catalog signature does not match its closed contract")
    if value.get("schemaVersion") != CATALOG_SIGNATURE_SCHEMA:
        raise ValueError("unsupported catalog signature schema")
    if value.get("algorithm") != "Ed25519":
        raise ValueError("unsupported catalog signature algorithm")
    if not _KEY_ID.fullmatch(str(value.get("keyId", ""))):
        raise ValueError("catalog signature key ID is invalid")
    if not _SHA256.fullmatch(str(value.get("catalogSha256", ""))):
        raise ValueError("catalog signature SHA-256 is invalid")
    _decode_base64(value.get("signature"), field="catalog signature", length=64)
    return value


def _validate_trust_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _TRUST_FIELDS:
        raise ValueError("catalog trust store does not match its closed contract")
    if value.get("schemaVersion") != CATALOG_TRUST_SCHEMA:
        raise ValueError("unsupported catalog trust-store schema")
    keys = value.get("keys")
    if not isinstance(keys, list) or not keys or len(keys) > 100:
        raise ValueError("catalog trust store keys are invalid")
    seen: set[str] = set()
    for item in keys:
        if not isinstance(item, dict) or set(item) != _TRUST_KEY_FIELDS:
            raise ValueError("catalog trust-store key does not match its closed contract")
        key_id = item.get("keyId")
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id) or key_id in seen:
            raise ValueError("catalog trust-store key ID is invalid or duplicated")
        seen.add(key_id)
        if item.get("algorithm") != "Ed25519":
            raise ValueError("catalog trust-store algorithm is unsupported")
        if item.get("status") not in {"active", "revoked"}:
            raise ValueError("catalog trust-store key status is invalid")
        public_bytes = _decode_base64(
            item.get("publicKey"),
            field="catalog public key",
            length=32,
        )
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        if public_key_id(public_key) != key_id:
            raise ValueError("catalog trust-store key ID does not match its public key")
    return value


def _load_trust_value(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_TRUST_BYTES:
        raise RuntimeError("catalog trust store is missing, unsafe, or too large")
    try:
        value = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("catalog trust store is not valid UTF-8 JSON") from error
    return _validate_trust_value(value)


def verify_catalog_signature(
    catalog_payload: bytes,
    signature_payload: bytes,
    *,
    trust_store_path: str | Path | None = None,
) -> dict[str, Any]:
    if len(signature_payload) > _MAX_SIGNATURE_BYTES:
        raise ValueError("catalog signature exceeds the 64 KiB limit")
    try:
        signature = _validate_signature_value(json.loads(signature_payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("catalog signature is not valid UTF-8 JSON") from error
    catalog_sha256 = hashlib.sha256(catalog_payload).hexdigest()
    if signature["catalogSha256"] != catalog_sha256:
        raise ValueError("catalog SHA-256 does not match its detached signature")
    trust_path = (
        Path(trust_store_path).expanduser().absolute()
        if trust_store_path is not None
        else bundled_trust_store_path()
    )
    trust = _load_trust_value(trust_path)
    record = next(
        (item for item in trust["keys"] if item["keyId"] == signature["keyId"]),
        None,
    )
    if record is None:
        raise ValueError("catalog signature uses an unknown key")
    if record["status"] != "active":
        raise ValueError("catalog signature uses a revoked key")
    public_key = Ed25519PublicKey.from_public_bytes(
        _decode_base64(record["publicKey"], field="catalog public key", length=32)
    )
    raw_signature = _decode_base64(
        signature["signature"],
        field="catalog signature",
        length=64,
    )
    try:
        public_key.verify(raw_signature, _SIGNING_CONTEXT + catalog_payload)
    except InvalidSignature as error:
        raise ValueError("catalog Ed25519 signature verification failed") from error
    return {
        "verified": True,
        "algorithm": "Ed25519",
        "key_id": signature["keyId"],
        "catalog_sha256": catalog_sha256,
        "signature_sha256": hashlib.sha256(signature_payload).hexdigest(),
    }
