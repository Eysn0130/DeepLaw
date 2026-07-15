from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from deeplaw import extract, vision
from deeplaw.cli import _parser
from deeplaw.document_engine import DocumentEngineError, DocumentEnginePage, DocumentEngineResult
from deeplaw.util import compact_text, sha256_bytes, sha256_file


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_pages: tuple[str, ...],
    ocr_pages: tuple[vision._OcrPage, ...],
) -> None:
    monkeypatch.setattr(vision, "_native_pages", lambda _path: native_pages)
    monkeypatch.setattr(vision, "_executable", lambda name, _environment: f"/fake/{name}")
    monkeypatch.setattr(vision, "_tool_version", lambda _command: "test-tool 1.0")

    def render(
        _path: Path,
        output: Path,
        _pdftoppm: str,
        *,
        page_count: int,
    ) -> tuple[Path, ...]:
        assert page_count == len(native_pages)
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


def test_document_engine_requires_independent_critical_token_consensus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    text = "第一条 任何单位不得隐匿三万元涉案财物，应当在2026年7月1日前登记。" * 5
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text=text, confidence=0.99),),
    )
    monkeypatch.setattr(
        vision,
        "_document_engine_candidates",
        lambda _path, _pages: (
            {
                1: vision._DocumentEngineCandidate(
                    blocks=(vision.DocumentEngineBlock("text", text, 1, 1),),
                    engine="test-engine",
                    engine_version="1.0",
                    output_schema="test-schema",
                    method="ocr",
                    backend="pipeline",
                    language="ch",
                )
            },
            ("document_engine=test",),
            (),
        ),
    )

    result = vision.extract_pdf_vision_consensus(source, use_document_engine=True)

    page = result.quality.page_evidence[0]
    assert page.selected_source == "machine_consensus"
    assert page.review_required is False
    assert page.critical_tokens_match is True
    assert page.ocr_document_engine_consistency == pytest.approx(1.0)
    assert page.document_engine_name == "test-engine"
    assert page.document_engine_method == "ocr"
    assert "machine_consensus_admitted" in page.risk_flags


def test_document_engine_disagreement_stays_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    ocr_text = "第一条 任何单位不得隐匿三万元涉案财物。" * 6
    engine_text = "第一条 任何单位可以隐匿八万元涉案财物。" * 6
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text=ocr_text, confidence=0.99),),
    )
    monkeypatch.setattr(
        vision,
        "_document_engine_candidates",
        lambda _path, _pages: (
            {
                1: vision._DocumentEngineCandidate(
                    blocks=(vision.DocumentEngineBlock("text", engine_text, 1, 1),),
                    engine="test-engine",
                    engine_version="1.0",
                    output_schema="test-schema",
                    method="ocr",
                    backend="pipeline",
                    language="ch",
                )
            },
            (),
            (),
        ),
    )

    result = vision.extract_pdf_vision_consensus(source, use_document_engine=True)

    page = result.quality.page_evidence[0]
    assert page.selected_source == "document_engine"
    assert page.review_required is True
    assert page.critical_tokens_match is False
    assert "machine_consensus_unresolved" in page.risk_flags


def test_short_document_engine_candidate_cannot_replace_complete_ocr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    compact_ocr_text = ("第一条任何单位应当完整登记涉案财物并保存原始凭证" * 30)[:375]
    # The real scanned-page failure also carries inter-Han whitespace risk.
    # That formatting risk must not let a 22-character candidate erase the page.
    ocr_text = " ".join(compact_ocr_text)
    engine_text = ("第一条仅识别到残缺页眉" * 3)[:22]
    assert len(compact_text(ocr_text)) == 375
    assert len(engine_text) == 22
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text=ocr_text, confidence=0.99),),
    )
    monkeypatch.setattr(
        vision,
        "_document_engine_candidates",
        lambda _path, _pages: (
            {
                1: vision._DocumentEngineCandidate(
                    blocks=(vision.DocumentEngineBlock("text", engine_text, 1, 1),),
                    engine="test-engine",
                    engine_version="1.0",
                    output_schema="test-schema",
                    method="ocr",
                    backend="pipeline",
                    language="ch",
                )
            },
            (),
            (),
        ),
    )

    page = vision.extract_pdf_vision_consensus(
        source, use_document_engine=True
    ).quality.page_evidence[0]

    assert page.selected_source == "ocr"
    assert page.selected_character_count == 375
    assert page.document_engine_character_count == 22
    assert page.review_required is True
    assert "document_engine_low_character_count" in page.risk_flags
    assert "machine_consensus_unresolved" in page.risk_flags
    assert "document_engine_candidate_rejected_incomplete" in page.risk_flags


def test_machine_consensus_rejects_noncritical_lexical_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    ocr_text = "第一条 任何单位应当登记涉案财物并保存原始凭证。" * 8
    engine_text = "第一条 任何单位应当登记涉案财物并保管原始凭证。" * 8
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text=ocr_text, confidence=0.99),),
    )
    monkeypatch.setattr(
        vision,
        "_document_engine_candidates",
        lambda _path, _pages: (
            {
                1: vision._DocumentEngineCandidate(
                    blocks=(vision.DocumentEngineBlock("text", engine_text, 1, 1),),
                    engine="test-engine",
                    engine_version="1.0",
                    output_schema="test-schema",
                    method="ocr",
                    backend="pipeline",
                    language="ch",
                )
            },
            (),
            (),
        ),
    )

    result = vision.extract_pdf_vision_consensus(source, use_document_engine=True)

    page = result.quality.page_evidence[0]
    assert page.critical_tokens_match is True
    assert page.ocr_document_engine_consistency is not None
    assert page.ocr_document_engine_consistency >= 0.94
    assert page.selected_source == "document_engine"
    assert page.review_required is True
    assert "machine_consensus_unresolved" in page.risk_flags


def test_machine_consensus_rejects_legal_punctuation_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    ocr_text = "第一条 甲、乙应当登记；丙应当复核。" * 8
    engine_text = "第一条 甲、乙应当登记，丙应当复核。" * 8
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text=ocr_text, confidence=0.99),),
    )
    monkeypatch.setattr(
        vision,
        "_document_engine_candidates",
        lambda _path, _pages: (
            {
                1: vision._DocumentEngineCandidate(
                    blocks=(vision.DocumentEngineBlock("text", engine_text, 1, 1),),
                    engine="test-engine",
                    engine_version="1.0",
                    output_schema="test-schema",
                    method="ocr",
                    backend="pipeline",
                    language="ch",
                )
            },
            (),
            (),
        ),
    )

    page = vision.extract_pdf_vision_consensus(
        source, use_document_engine=True
    ).quality.page_evidence[0]

    assert page.critical_tokens_match is False
    assert page.selected_source == "document_engine"
    assert page.review_required is True


def test_machine_consensus_does_not_flatten_table_structure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    text = "第一条 项目 金额 甲 三万元 乙 五万元。" * 8
    _patch_pipeline(
        monkeypatch,
        native_pages=("",),
        ocr_pages=(vision._OcrPage(text=text, confidence=0.99),),
    )
    monkeypatch.setattr(
        vision,
        "_document_engine_candidates",
        lambda _path, _pages: (
            {
                1: vision._DocumentEngineCandidate(
                    blocks=(vision.DocumentEngineBlock("table", text, 1, 1),),
                    engine="test-engine",
                    engine_version="1.0",
                    output_schema="test-schema",
                    method="ocr",
                    backend="pipeline",
                    language="ch",
                )
            },
            (),
            (),
        ),
    )

    page = vision.extract_pdf_vision_consensus(
        source, use_document_engine=True
    ).quality.page_evidence[0]

    assert page.critical_tokens_match is True
    assert page.selected_source == "document_engine"
    assert page.review_required is True
    assert "document_engine_table_requires_review" in page.risk_flags


def test_document_engine_configuration_is_bounded_for_many_page_ranges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "long.pdf"
    source.write_bytes(b"long-pdf")
    good = "中华人民共和国测试法第一条保护证据链完整性。" * 8
    calls: list[tuple[int, str]] = []
    failed_pages = {5}

    def run_engine(
        _path: Path,
        *,
        start_page: int,
        end_page: int,
        method: str,
    ) -> DocumentEngineResult:
        assert start_page == end_page
        calls.append((start_page, method))
        if start_page == 3 and method == "auto":
            raise DocumentEngineError("auto failed")
        if start_page in failed_pages:
            raise DocumentEngineError("all methods failed")
        return DocumentEngineResult(
            pages=(
                DocumentEnginePage(
                    page=start_page,
                    blocks=(vision.DocumentEngineBlock("text", good, start_page, 1),),
                ),
            ),
            engine="mineru-compatible-cli",
            engine_version="mineru, version 3.4.4",
            output_schema="content_list_v2",
            configuration=(
                f"method={method}",
                "backend=pipeline",
                "language=ch",
                f"pages={start_page}-{end_page}",
            ),
        )

    monkeypatch.setattr(vision, "extract_pdf_page_range", run_engine)

    with pytest.raises(
        vision.VisionExtractionError,
        match="one or more requested risk-page ranges: pages=5",
    ):
        vision._document_engine_candidates(source, ("", good, "", good, ""))
    assert calls == [(1, "auto"), (3, "auto"), (3, "ocr"), (5, "auto"), (5, "ocr")]

    failed_pages.clear()
    calls.clear()
    candidates, configuration, warnings = vision._document_engine_candidates(
        source, ("", good, "", good, "")
    )

    assert set(candidates) == {1, 3, 5}
    assert candidates[1].method == "auto"
    assert candidates[3].method == "ocr"
    assert candidates[5].method == "auto"
    assert configuration == (
        "document_engine=mineru-compatible-cli",
        "document_engine_version=mineru, version 3.4.4",
        "document_engine_run=schemas=content_list_v2;methods=auto,ocr;"
        "backends=pipeline;languages=ch;ranges=3",
    )
    assert warnings == ()
    assert calls == [(1, "auto"), (3, "auto"), (3, "ocr"), (5, "auto")]

    calls.clear()
    _patch_pipeline(
        monkeypatch,
        native_pages=("", good, "", good, ""),
        ocr_pages=tuple(vision._OcrPage(text=good, confidence=0.99) for _page in range(5)),
    )
    result = vision.extract_pdf_vision_consensus(source, use_document_engine=True)

    assert len(result.quality.configuration) == 8
    assert all(len(item) <= 200 for item in result.quality.configuration)
    assert result.quality.page_evidence[0].document_engine_method == "auto"
    assert result.quality.page_evidence[2].document_engine_method == "ocr"
    assert result.quality.page_evidence[4].document_engine_method == "auto"


def test_document_engine_mode_fails_when_every_risk_range_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"scan-pdf")
    monkeypatch.setattr(
        vision,
        "extract_pdf_page_range",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DocumentEngineError("failed")),
    )

    with pytest.raises(vision.VisionExtractionError, match="one or more requested"):
        vision._document_engine_candidates(source, ("",))


class _MediaBox:
    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height


class _PdfPage:
    def __init__(self, width: float, height: float, user_unit: float = 1) -> None:
        self.mediabox = _MediaBox(width, height)
        self.user_unit = user_unit

    def get(self, key: str, default: object) -> object:
        assert key == "/UserUnit"
        return self.user_unit if self.user_unit is not None else default


def test_pdf_render_budget_rejects_oversized_mediabox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vision, "_MAX_RENDER_PAGE_PIXELS", 100)

    with pytest.raises(vision.VisionExtractionError, match=r"page 1.*pixel limit"):
        vision.validate_pdf_render_budget([_PdfPage(72, 72)])


def test_pdf_render_budget_rejects_excessive_total_pixels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vision, "_MAX_RENDER_PAGE_PIXELS", 100_000)
    monkeypatch.setattr(vision, "_MAX_RENDER_TOTAL_PIXELS", 150_000)

    with pytest.raises(vision.VisionExtractionError, match="total render pixel limit"):
        vision.validate_pdf_render_budget([_PdfPage(72, 72), _PdfPage(72, 72)])


def test_renderer_uses_small_batches_and_enforces_per_page_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "fake-pdftoppm"
    executable.write_text(
        f"""#!{sys.executable}
import pathlib
import sys
args = sys.argv[1:]
start = int(args[args.index('-f') + 1])
end = int(args[args.index('-l') + 1])
prefix = pathlib.Path(args[-1])
for page in range(start, end + 1):
    prefix.with_name(prefix.name + f'-{{page}}.png').write_bytes(b'x' * 32)
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    output = tmp_path / "rendered"
    output.mkdir()
    monkeypatch.setattr(vision, "_RENDER_BATCH_PAGES", 2)

    images = vision._render_pages(source, output, str(executable), page_count=5)

    assert [image.name for image in images] == [
        "page-1.png",
        "page-2.png",
        "page-3.png",
        "page-4.png",
        "page-5.png",
    ]
    monkeypatch.setattr(vision, "_MAX_RENDER_PAGE_BYTES", 16)
    second_output = tmp_path / "oversized"
    second_output.mkdir()
    with pytest.raises(vision.VisionExtractionError, match="oversized page image"):
        vision._render_pages(source, second_output, str(executable), page_count=1)


def test_long_tool_version_is_preserved_by_prefix_and_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = "tool-version-" + "y" * 300
    monkeypatch.setattr(
        vision,
        "_run_bounded_pdf_subprocess",
        lambda *_args, **_kwargs: vision._BoundedProcessResult(
            returncode=0,
            stdout=f"{version}\n".encode(),
            stderr=b"",
        ),
    )

    result = vision._tool_version(["tool", "--version"])

    assert result.startswith(version[:80])
    assert ";sha256=" in result
    assert len(result) <= 160


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


def test_cli_exposes_only_evidence_preserving_pdf_fallbacks() -> None:
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
    advanced = parser.parse_args(
        [
            "build",
            "--source-root",
            "/tmp/source",
            "--manifest",
            "/tmp/manifest.json",
            "--pdf-fallback",
            "document-engine",
        ]
    )
    assert advanced.pdf_fallback == "document-engine"
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
    calls: list[tuple[Path, Path | None, bool]] = []

    def route(
        path: Path,
        *,
        reviewed_pages_path: Path | None,
        use_document_engine: bool,
    ) -> object:
        calls.append((path, reviewed_pages_path, use_document_engine))
        return expected

    monkeypatch.setattr(extract, "extract_pdf_vision_consensus", route)

    result = extract.extract_document(
        source,
        "PDF",
        pdf_fallback="vision-consensus",
        reviewed_pages_path=review,
    )

    assert result is expected
    assert calls == [(source, review, False)]


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
