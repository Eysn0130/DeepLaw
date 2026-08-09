from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DOCUMENT = _ROOT / "docs/PRD_1_2_RELEASE_DISPOSITION.md"
_SIDECAR = _ROOT / "docs/PRD_1_2_RELEASE_DISPOSITION.sha256"


def _verify_sidecar(document: Path, sidecar: Path) -> None:
    fields = sidecar.read_text(encoding="ascii").split()
    if len(fields) != 2:
        raise AssertionError("sidecar must contain exactly a digest and filename")

    digest, filename = fields
    if filename != document.name:
        raise AssertionError("sidecar filename does not match the document")

    actual = hashlib.sha256(document.read_bytes()).hexdigest()
    if digest != actual:
        raise AssertionError("sidecar digest does not match document bytes")


def test_prd12_release_disposition_sidecar_binds_current_document() -> None:
    _verify_sidecar(_DOCUMENT, _SIDECAR)


def test_sidecar_rejects_changed_document_bytes(tmp_path: Path) -> None:
    document = tmp_path / _DOCUMENT.name
    document.write_bytes(_DOCUMENT.read_bytes() + b"\n")
    sidecar = tmp_path / _SIDECAR.name
    sidecar.write_bytes(_SIDECAR.read_bytes())

    with pytest.raises(AssertionError, match="digest"):
        _verify_sidecar(document, sidecar)


def test_sidecar_rejects_wrong_filename(tmp_path: Path) -> None:
    document = tmp_path / _DOCUMENT.name
    document.write_bytes(_DOCUMENT.read_bytes())
    sidecar = tmp_path / _SIDECAR.name
    sidecar.write_text(
        f"{hashlib.sha256(document.read_bytes()).hexdigest()}  wrong-disposition.md\n",
        encoding="ascii",
    )

    with pytest.raises(AssertionError, match="filename"):
        _verify_sidecar(document, sidecar)


def test_sidecar_rejects_wrong_digest(tmp_path: Path) -> None:
    document = tmp_path / _DOCUMENT.name
    document.write_bytes(_DOCUMENT.read_bytes())
    sidecar = tmp_path / _SIDECAR.name
    sidecar.write_text(f"{'0' * 64}  {document.name}\n", encoding="ascii")

    with pytest.raises(AssertionError, match="digest"):
        _verify_sidecar(document, sidecar)
