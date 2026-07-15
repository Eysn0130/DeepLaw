from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import deeplaw.official as official_module
from deeplaw.catalog_signing import (
    bundled_trust_store_path,
    export_trust_store,
    initialize_signing_key,
    load_signing_key,
    sign_catalog_file,
    verify_catalog_signature,
)
from deeplaw.official import (
    OFFICIAL_STATE_SCHEMA,
    bundled_catalog_path,
    bundled_catalog_signature_path,
    official_status,
    sync_official,
)

from .helpers import manifest_document, write_docx
from .test_library_scopes import _write_catalog


def _sign_test_catalog(catalog: Path, root: Path) -> tuple[Path, Path, Path]:
    key_path = root / "signing" / "catalog-key.pem"
    trust_path = root / "trust.json"
    signature_path = Path(f"{catalog}.sig")
    initialize_signing_key(key_path)
    export_trust_store(trust_path, key_path=key_path)
    sign_catalog_file(catalog, signature_path=signature_path, key_path=key_path)
    return key_path, trust_path, signature_path


def test_signing_key_is_owner_only_idempotent_and_never_committed(tmp_path: Path) -> None:
    key_path = tmp_path / "identity" / "official.pem"

    created = initialize_signing_key(key_path)
    existing = initialize_signing_key(key_path)

    assert created["created"] is True
    assert existing["created"] is False
    assert created["key_id"] == existing["key_id"]
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert "PRIVATE KEY" in key_path.read_text(encoding="ascii")
    assert key_path.name.endswith(".pem")

    key_path.chmod(0o644)
    with pytest.raises(RuntimeError, match="only by its owner"):
        load_signing_key(key_path)


def test_detached_signature_binds_exact_catalog_bytes_and_closed_contract(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_bytes(b'{"catalog":"exact bytes"}\n')
    _, trust_path, signature_path = _sign_test_catalog(catalog, tmp_path)

    verification = verify_catalog_signature(
        catalog.read_bytes(),
        signature_path.read_bytes(),
        trust_store_path=trust_path,
    )
    assert verification["verified"] is True
    assert verification["algorithm"] == "Ed25519"

    with pytest.raises(ValueError, match="SHA-256"):
        verify_catalog_signature(
            catalog.read_bytes() + b" ",
            signature_path.read_bytes(),
            trust_store_path=trust_path,
        )

    signature = json.loads(signature_path.read_text(encoding="utf-8"))
    signature["unexpected"] = True
    with pytest.raises(ValueError, match="closed contract"):
        verify_catalog_signature(
            catalog.read_bytes(),
            json.dumps(signature).encode(),
            trust_store_path=trust_path,
        )

    trust = json.loads(trust_path.read_text(encoding="utf-8"))
    trust["keys"][0]["status"] = "revoked"
    trust_path.write_text(json.dumps(trust), encoding="utf-8")
    with pytest.raises(ValueError, match="revoked key"):
        verify_catalog_signature(
            catalog.read_bytes(),
            signature_path.read_bytes(),
            trust_store_path=trust_path,
        )


def test_bundled_catalog_signature_and_public_contracts_are_valid() -> None:
    repository = Path(__file__).resolve().parents[1]
    catalog_payload = bundled_catalog_path().read_bytes()
    signature_payload = bundled_catalog_signature_path().read_bytes()
    trust_path = bundled_trust_store_path()

    result = verify_catalog_signature(catalog_payload, signature_payload)
    assert result["verified"] is True
    assert result["key_id"].startswith("ed25519:")

    for value, schema_name in (
        (json.loads(signature_payload), "catalog-signature.v1.schema.json"),
        (json.loads(trust_path.read_bytes()), "catalog-trust.v1.schema.json"),
    ):
        schema = json.loads((repository / "contracts" / schema_name).read_text())
        Draft202012Validator(schema).validate(value)


def test_official_sync_requires_signatures_except_explicit_local_development(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source = source_root / "signed.docx"
    write_docx(source, ["官方签名测试法", "第一条 目录必须先验签再构建。"])
    catalog = _write_catalog(
        tmp_path / "catalog.json",
        [manifest_document(source_root, source.name, title="官方签名测试法")],
        sequence=1,
    )

    with pytest.raises(FileNotFoundError, match=r"catalog\.json\.sig"):
        sync_official(
            catalog_source=catalog,
            source_root=source_root,
            home=tmp_path / "unsigned-home",
        )

    unsigned = sync_official(
        catalog_source=catalog,
        source_root=source_root,
        home=tmp_path / "developer-home",
        allow_unsigned_local_catalog=True,
    )
    assert unsigned["catalog"]["signature_verified"] is False

    _, trust_path, signature_path = _sign_test_catalog(catalog, tmp_path / "identity")
    signed = sync_official(
        catalog_source=catalog,
        catalog_signature_source=signature_path,
        source_root=source_root,
        home=tmp_path / "signed-home",
        trust_store_path=trust_path,
    )
    assert signed["catalog"]["signature_verified"] is True
    assert signed["catalog"]["signature_key_id"].startswith("ed25519:")

    catalog.write_bytes(catalog.read_bytes() + b" ")
    with pytest.raises(ValueError, match="SHA-256"):
        sync_official(
            catalog_source=catalog,
            catalog_signature_source=signature_path,
            source_root=source_root,
            home=tmp_path / "tampered-home",
            trust_store_path=trust_path,
            allow_unsigned_local_catalog=True,
        )

    original_payload = catalog.read_bytes()
    monkeypatch.setattr(
        official_module,
        "_download_bytes",
        lambda *_args, **_kwargs: original_payload,
    )
    with pytest.raises(ValueError, match="only an explicitly selected local"):
        official_module._read_catalog(
            "https://catalog.example/catalog.json",
            allow_unsigned_local_catalog=True,
        )


def test_legacy_official_state_is_read_as_unverified_v2(tmp_path: Path) -> None:
    official_root = tmp_path / "official"
    official_root.mkdir(parents=True)
    legacy = {
        "schema_version": "deeplaw.official-state/v1",
        "enabled": False,
        "active_release_id": None,
        "installed_release_ids": [],
        "catalog": {
            "catalog_id": "deeplaw-cn-official",
            "sequence": 1,
            "version": "2026.07.14.1",
            "published_on": "2026-07-14",
            "sha256": "1" * 64,
            "source": "bundled",
            "synced_at": "2026-07-15T16:00:00Z",
        },
    }
    (official_root / "state.json").write_text(json.dumps(legacy), encoding="utf-8")

    status = official_status(home=tmp_path)

    assert status["schema_version"] == OFFICIAL_STATE_SCHEMA
    assert status["catalog"]["signature_verified"] is False
    assert status["catalog"]["signature_key_id"] is None
    assert status["signature_required_for_official_install_and_update"] is True
