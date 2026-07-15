from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplaw import extract, vision
from deeplaw.cli import _parser
from deeplaw.util import sha256_bytes, sha256_file


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_pages: tuple[str, ...],
    ocr_pages: tuple[vision._OcrPage, ...],
) -> None:
    monkeypatch.setattr(vision, "_native_pages", lambda _path: native_pages)
    monkeypatch.setattr(vision, "_executable", lambda name, _environment: f"/fake/{name}")
    monkeypatch.setattr(vision, "_tool_version", lambda _command: "test-tool 1.0")

    def render(_path: Path, output: Path, _pdftoppm: str) -> tuple[Path, ...]:
        images = []
        for page in range(1, len(native_pages) + 1):
            image = output / f"page-{page}.png"
            image.write_bytes(f"rendered-page-{page}".encode())
            images.append(image)
        return tuple(images)

    def ocr(image: Path, _tesseract: str, _language: str) -> vision._OcrPage:
        page = vision._page_image_number(image)
        return ocr_pages[page - 1]

    monkeypatch.setattr(vision, "_render_pages", render)
    monkeypatch.setattr(vision, "_ocr_page", ocr)


def _review_payload(
    *,
    source_sha256: str,
    image_sha256: str,
    text: str = "经人工逐字核对的规范文本。",
) -> dict[str, object]:
    return {
        "schemaVersion": vision.REVIEWED_PAGES_SCHEMA,
        "sourceSha256": source_sha256,
        "reviewer": {
            "type": "human",
            "name": "复核员甲",
            "organization": "测试法源室",
            "role": "逐页视觉复核",
        },
        "reviewedAt": "2026-07-15T10:30:00+08:00",
        "attestation": "visual_page_comparison",
        "pages": [
            {
                "page": 1,
                "imageSha256": image_sha256,
                "text": text,
                "notes": "已对照整页图像。",
            }
        ],
    }


def test_native_text_is_selected_without_ocr_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "law.pdf"
    source.write_bytes(b"test-pdf")
    native = "中华人民共和国测试法第一条保护证据链完整性。" * 8
    _patch_pipeline(
        monkeypatch,
        native_pages=(native,),
        ocr_pages=(vision._OcrPage(text="不应调用", confidence=0.99),),
    )

    result = vision.extract_pdf_vision_consensus(source)

    page = result.quality.page_evidence[0]
    assert result.quality.extractor == vision.PIPELINE_NAME
    assert result.quality.source_sha256 == sha256_file(source)
    assert result.quality.review_required is False
    assert result.quality.needs_ocr is False
    assert page.selected_source == "native"
    assert page.ocr_text_sha256 is None
    assert page.image_sha256 == sha256_bytes(b"rendered-page-1")
    assert page.selected_text_sha256 == sha256_bytes(native.encode())


def test_unreviewed_ocr_remains_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    ocr_text = "第一条 扫描页经本地OCR得到的候选文本仍然需要人工复核。" * 8
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text=ocr_text, confidence=0.93),),
    )

    result = vision.extract_pdf_vision_consensus(source)

    page = result.quality.page_evidence[0]
    assert result.quality.review_required is True
    assert result.quality.needs_ocr is True
    assert page.selected_source == "ocr"
    assert page.review_status == "not_reviewed"
    assert page.review_required is True
    assert "native_empty" in page.risk_flags
    assert page.ocr_confidence == pytest.approx(0.93)


def test_native_ocr_mismatch_is_explicitly_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "mismatch.pdf"
    source.write_bytes(b"mismatch-pdf")
    native = " ".join("中央办公厅规范涉案财物处置程序" * 12)
    ocr_text = "完全不同的识别结果不得替代原始页面证据" * 12
    _patch_pipeline(
        monkeypatch,
        native_pages=(native,),
        ocr_pages=(vision._OcrPage(text=ocr_text, confidence=0.90),),
    )

    result = vision.extract_pdf_vision_consensus(source)

    page = result.quality.page_evidence[0]
    assert "native_inter_han_whitespace" in page.risk_flags
    assert "native_ocr_mismatch" in page.risk_flags
    assert page.native_ocr_consistency is not None
    assert page.native_ocr_consistency < 0.82
    assert page.review_required is True


def test_source_and_image_bound_human_review_can_override_one_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"reviewed-scan")
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text="低质量候选文本" * 8, confidence=0.31),),
    )
    candidate = vision.extract_pdf_vision_consensus(source)
    image_sha256 = candidate.quality.page_evidence[0].image_sha256
    reviewed_text = "第一条  经人工对照页面确认的正式文本。\n\n 不依赖模型自动判断。"
    review_path = tmp_path / "reviewed-pages.json"
    review_path.write_text(
        json.dumps(
            _review_payload(
                source_sha256=sha256_file(source),
                image_sha256=image_sha256,
                text=reviewed_text,
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = vision.extract_pdf_vision_consensus(source, reviewed_pages_path=review_path)

    page = result.quality.page_evidence[0]
    assert result.quality.review_required is False
    assert result.quality.needs_ocr is False
    assert result.quality.reviewed_page_count == 1
    assert page.selected_source == "reviewed"
    assert page.review_status == "human_reviewed"
    assert page.review_required is False
    assert page.reviewed_by == "复核员甲 | 测试法源室 | 逐页视觉复核"
    assert page.reviewed_at == "2026-07-15T10:30:00+08:00"
    assert page.review_file_sha256 == sha256_file(review_path)
    assert "human_reviewed_override" in page.risk_flags
    selected_text = "\n".join(block.text for block in result.blocks)
    assert selected_text == "第一条 经人工对照页面确认的正式文本。\n不依赖模型自动判断。"
    assert page.selected_text_sha256 == sha256_bytes(selected_text.encode())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update({"unexpected": True}), "closed schema"),
        (lambda value: value.update({"sourceSha256": "0" * 64}), "does not match"),
        (lambda value: value["reviewer"].update({"type": "model"}), "must be 'human'"),
    ],
)
def test_reviewed_pages_rejects_untrusted_claims(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    source_sha256 = "1" * 64
    image_sha256 = "2" * 64
    payload = _review_payload(
        source_sha256=source_sha256,
        image_sha256=image_sha256,
    )
    mutate(payload)  # type: ignore[operator]
    path = tmp_path / "reviewed-pages.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(vision.VisionExtractionError, match=message):
        vision.load_reviewed_pages(
            path,
            source_sha256=source_sha256,
            page_count=1,
            image_sha256_by_page={1: image_sha256},
        )


def test_reviewed_pages_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    source_sha256 = "1" * 64
    payload = _review_payload(
        source_sha256=source_sha256,
        image_sha256="2" * 64,
    )
    raw = json.dumps(payload, ensure_ascii=False)
    raw = raw.replace(
        '"sourceSha256":',
        f'"sourceSha256": "{"0" * 64}", "sourceSha256":',
        1,
    )
    path = tmp_path / "reviewed-pages.json"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(vision.VisionExtractionError, match="duplicate key: sourceSha256"):
        vision.load_reviewed_pages(
            path,
            source_sha256=source_sha256,
            page_count=1,
            image_sha256_by_page={1: "2" * 64},
        )


def test_low_quality_tesseract_pattern_for_document_18_is_blocked() -> None:
    native = " ".join("中共中央办公厅国务院办公厅规范涉案财物处置" * 10)
    ocr = "左14 从衡 俘 Xx 00 -- 7号" * 12

    risks = vision._page_risk_flags(native, ocr, ocr_confidence=0.38)

    assert "native_inter_han_whitespace" in risks
    assert "ocr_low_confidence" in risks
    assert "native_ocr_mismatch" in risks


def test_tesseract_tsv_preserves_lines_and_weighted_confidence() -> None:
    value = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t0\t0\t1\t1\t90\t第一条",
            "5\t1\t1\t1\t1\t2\t0\t0\t1\t1\t80\t测试",
            "5\t1\t1\t1\t2\t1\t0\t0\t1\t1\t70\t第二行",
        ]
    )

    result = vision._parse_tesseract_tsv(value)

    assert result.text == "第一条 测试\n第二行"
    assert result.confidence == pytest.approx((90 * 3 + 80 * 2 + 70 * 3) / 8 / 100)


def test_tesseract_tsv_does_not_trust_out_of_range_confidence() -> None:
    value = "\n".join(
        [
            "level\tblock_num\tpar_num\tline_num\tconf\ttext",
            "5\t1\t1\t1\t999\t异常高值",
        ]
    )

    result = vision._parse_tesseract_tsv(value)

    assert result.text == "异常高值"
    assert result.confidence is None


def test_cli_exposes_only_first_party_consensus_fallback() -> None:
    parser = _parser()

    args = parser.parse_args(
        [
            "build",
            "--source-root",
            "/tmp/source",
            "--manifest",
            "/tmp/manifest.json",
            "--pdf-fallback",
            "vision-consensus",
        ]
    )

    assert args.pdf_fallback == "vision-consensus"
    for retired in ("external-parser", "raw-ocr"):
        with pytest.raises(SystemExit):
            parser.parse_args(
                [
                    "build",
                    "--source-root",
                    "/tmp/source",
                    "--manifest",
                    "/tmp/manifest.json",
                    "--pdf-fallback",
                    retired,
                ]
            )


def test_extract_document_routes_reviewed_pages_to_consensus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    review = tmp_path / "review.json"
    expected = object()
    calls: list[tuple[Path, Path | None]] = []

    def route(path: Path, *, reviewed_pages_path: Path | None) -> object:
        calls.append((path, reviewed_pages_path))
        return expected

    monkeypatch.setattr(extract, "extract_pdf_vision_consensus", route)

    result = extract.extract_document(
        source,
        "PDF",
        pdf_fallback="vision-consensus",
        reviewed_pages_path=review,
    )

    assert result is expected
    assert calls == [(source, review)]


@pytest.mark.parametrize("fallback", ["external-parser", "raw-ocr"])
def test_extract_document_rejects_retired_unreviewed_fallback(
    tmp_path: Path, fallback: str
) -> None:
    with pytest.raises(extract.ExtractionError, match=f"unsupported PDF fallback: {fallback}"):
        extract.extract_document(
            tmp_path / "source.pdf",
            "PDF",
            pdf_fallback=fallback,
        )
