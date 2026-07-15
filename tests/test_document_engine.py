from __future__ import annotations

import json
import os
import stat
import sys
import tomllib
from pathlib import Path

import pytest

from deeplaw import document_engine


def test_document_engine_extra_declares_runtime_compatibility_dependency() -> None:
    project = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    dependencies = project["project"]["optional-dependencies"]["document-engine"]

    assert "mineru[pipeline]==3.4.4" in dependencies
    assert "six==1.17.0" in dependencies


def _fake_engine(tmp_path: Path, payloads: dict[str, object], *, delay: float = 0) -> Path:
    executable = tmp_path / "fake-mineru"
    serialized = repr(
        {name: json.dumps(value, ensure_ascii=False) for name, value in payloads.items()}
    )
    executable.write_text(
        f"""#!{sys.executable}
import json
import pathlib
import sys
import time

if '--version' in sys.argv:
    print('fake-mineru 3.4.4')
    raise SystemExit(0)

time.sleep({delay!r})
args = sys.argv[1:]
output = pathlib.Path(args[args.index('-o') + 1]) / 'source' / 'auto'
output.mkdir(parents=True)
(output / 'invocation.json').write_text(json.dumps(args), encoding='utf-8')
for name, content in {serialized}.items():
    (output / name).write_text(content, encoding='utf-8')
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _pdf(tmp_path: Path) -> Path:
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-1.7\n%%EOF\n")
    return path


def test_extracts_v2_in_page_order_and_prefers_it_over_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    v2 = [
        [
            {
                "type": "title",
                "content": {
                    "title_content": [{"type": "text", "content": "第一章 总则"}],
                    "level": 1,
                },
                "bbox": [1, 2, 300, 40],
                "confidence": 0.98,
            }
        ],
        [
            {
                "type": "table",
                "content": {
                    "image_source": {"path": "images/table.jpg"},
                    "table_caption": [{"type": "text", "content": "数额标准"}],
                    "html": "<table><tr><th>地区</th><th>数额</th></tr>"
                    "<tr><td>甲</td><td>三万元</td></tr></table>",
                    "table_footnote": [],
                    "table_type": "simple_table",
                    "table_nest_level": 1,
                },
            }
        ],
    ]
    engine = _fake_engine(
        tmp_path,
        {
            "source_content_list_v2.json": v2,
            "source_content_list.json": [{"type": "text", "text": "不应采用", "page_idx": 0}],
        },
    )
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(engine))

    result = document_engine.extract_pdf_page_range(
        _pdf(tmp_path), start_page=3, end_page=4, timeout_seconds=3
    )

    assert result.engine_version == "fake-mineru 3.4.4"
    assert result.output_schema == "content_list_v2"
    assert [page.page for page in result.pages] == [3, 4]
    assert result.pages[0].blocks[0] == document_engine.DocumentEngineBlock(
        type="title",
        text="第一章 总则",
        page=3,
        order=1,
        bbox=(1.0, 2.0, 300.0, 40.0),
        confidence=0.98,
    )
    assert result.pages[1].blocks[0].type == "table"
    assert "数额标准" in result.pages[1].blocks[0].text
    assert "甲" in result.pages[1].blocks[0].text
    assert "三万元" in result.pages[1].blocks[0].text
    assert "images/table.jpg" not in result.pages[1].blocks[0].text
    assert "不应采用" not in "\n".join(block.text for block in result.blocks)
    assert result.configuration == (
        "method=auto",
        "backend=pipeline",
        "language=ch",
        "pages=3-4",
    )


def test_structured_allowlist_excludes_metadata_and_generated_visual_descriptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _fake_engine(
        tmp_path,
        {
            "source_content_list_v2.json": [
                [
                    {
                        "type": "text",
                        "content": {
                            "text_content": [
                                {
                                    "type": "text",
                                    "content": "第一条 法律原文。",
                                    "item_type": "must-not-leak",
                                    "code_language": "python",
                                    "math_type": "display",
                                }
                            ]
                        },
                    },
                    {
                        "type": "image",
                        "content": {
                            "content": "模型生成的图片说明不得成为法源文本",
                            "image_caption": "同样不得进入",
                        },
                    },
                ]
            ]
        },
    )
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(engine))

    result = document_engine.extract_pdf_page_range(
        _pdf(tmp_path), start_page=1, end_page=1, timeout_seconds=3
    )

    assert result.pages[0].blocks[0].text == "第一条 法律原文。"
    assert result.pages[0].blocks[1].text == ""
    joined = "\n".join(block.text for block in result.blocks)
    assert "must-not-leak" not in joined
    assert "python" not in joined
    assert "display" not in joined
    assert "图片说明" not in joined


def test_legacy_content_list_maps_zero_based_pages_and_preserves_empty_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _fake_engine(
        tmp_path,
        {
            "source_content_list.json": [
                {"type": "text", "text": "第一页", "page_idx": 0},
                {
                    "type": "table",
                    "table_body": "<table><tr><td>第二页</td></tr></table>",
                    "page_idx": 2,
                },
            ]
        },
    )
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(engine))

    result = document_engine.extract_pdf_page_range(
        _pdf(tmp_path), start_page=7, end_page=9, timeout_seconds=3
    )

    assert result.output_schema == "content_list"
    assert [block.text for block in result.pages[0].blocks] == ["第一页"]
    assert result.pages[1].blocks == ()
    assert [block.text for block in result.pages[2].blocks] == ["第二页"]


@pytest.mark.parametrize(
    ("start_page", "end_page", "message"),
    [
        (0, 1, "start_page"),
        (2, 1, "end_page"),
        (1, 5001, "page range"),
    ],
)
def test_rejects_invalid_page_ranges(
    tmp_path: Path,
    start_page: int,
    end_page: int,
    message: str,
) -> None:
    with pytest.raises(document_engine.DocumentEngineError, match=message):
        document_engine.extract_pdf_page_range(
            _pdf(tmp_path), start_page=start_page, end_page=end_page
        )


def test_reports_engine_extra_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPLAW_DOCUMENT_ENGINE", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(document_engine.sys, "executable", str(tmp_path / "python"))

    with pytest.raises(document_engine.DocumentEngineError, match="not installed"):
        document_engine.extract_pdf_page_range(_pdf(tmp_path), start_page=1, end_page=1)


def test_times_out_and_terminates_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _fake_engine(
        tmp_path,
        {"source_content_list_v2.json": [[]]},
        delay=1,
    )
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(engine))

    with pytest.raises(document_engine.DocumentEngineError, match="timed out"):
        document_engine.extract_pdf_page_range(
            _pdf(tmp_path), start_page=1, end_page=1, timeout_seconds=0.05
        )


def test_bounded_runner_rejects_unbounded_process_output(tmp_path: Path) -> None:
    executable = tmp_path / "noisy-engine"
    executable.write_text(
        f"""#!{sys.executable}
import sys
sys.stdout.write('x' * 4096)
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    with pytest.raises(document_engine.DocumentEngineError, match="output exceeded 32 bytes"):
        document_engine._run_bounded(
            [str(executable)],
            cwd=tmp_path,
            timeout_seconds=3,
            capture_limit=32,
        )


def test_live_output_tree_budget_terminates_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "oversized-engine"
    executable.write_text(
        f"""#!{sys.executable}
import pathlib
import sys
import time
args = sys.argv[1:]
if '--version' in args:
    print('oversized-engine 1.0')
    raise SystemExit(0)
output = pathlib.Path(args[args.index('-o') + 1])
output.mkdir(exist_ok=True)
(output / 'oversized.bin').write_bytes(b'x' * 4096)
time.sleep(5)
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(executable))
    monkeypatch.setattr(document_engine, "_MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(document_engine, "_OUTPUT_MONITOR_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(document_engine, "_posix_resource_limiter", lambda _timeout: None)

    with pytest.raises(document_engine.DocumentEngineError, match="size limit"):
        document_engine.extract_pdf_page_range(
            _pdf(tmp_path), start_page=1, end_page=1, timeout_seconds=3
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX resource limits only")
def test_posix_resource_limiter_applies_cpu_memory_file_and_fd_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(
        document_engine,
        "_set_posix_resource_limit",
        lambda kind, requested: calls.append((kind, requested)),
    )

    limiter = document_engine._posix_resource_limiter(12.2)

    assert limiter is not None
    limiter()
    assert calls == [
        (document_engine._resource.RLIMIT_CPU, 14),
        (
            document_engine._resource.RLIMIT_AS,
            document_engine._MAX_PROCESS_ADDRESS_SPACE_BYTES,
        ),
        (document_engine._resource.RLIMIT_FSIZE, document_engine._MAX_OUTPUT_BYTES),
        (
            document_engine._resource.RLIMIT_NOFILE,
            document_engine._MAX_PROCESS_OPEN_FILES,
        ),
    ]


def test_resource_limiter_safely_degrades_without_resource_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_engine, "_resource", None)

    assert document_engine._posix_resource_limiter(30) is None


def test_rejects_duplicate_json_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable = tmp_path / "duplicate-json-engine"
    executable.write_text(
        f"""#!{sys.executable}
import pathlib
import sys
if '--version' in sys.argv:
    print('duplicate-json-engine 1.0')
    raise SystemExit(0)
args = sys.argv[1:]
output = pathlib.Path(args[args.index('-o') + 1])
output.mkdir(exist_ok=True)
(output / 'source_content_list_v2.json').write_text(
    '[[{{"type":"text","type":"table"}}]]', encoding='utf-8'
)
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(executable))

    with pytest.raises(document_engine.DocumentEngineError, match="duplicate key: type"):
        document_engine.extract_pdf_page_range(
            _pdf(tmp_path), start_page=1, end_page=1, timeout_seconds=3
        )


def test_rejects_v2_page_count_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _fake_engine(tmp_path, {"source_content_list_v2.json": [[]]})
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(engine))

    with pytest.raises(document_engine.DocumentEngineError, match="exactly one array"):
        document_engine.extract_pdf_page_range(
            _pdf(tmp_path), start_page=2, end_page=3, timeout_seconds=3
        )


def test_rejects_oversized_content_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _fake_engine(
        tmp_path,
        {"source_content_list_v2.json": [[{"type": "text", "text": "太长"}]]},
    )
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(engine))
    monkeypatch.setattr(document_engine, "_MAX_JSON_BYTES", 2)

    with pytest.raises(document_engine.DocumentEngineError, match="JSON size limit"):
        document_engine.extract_pdf_page_range(
            _pdf(tmp_path), start_page=1, end_page=1, timeout_seconds=3
        )


def test_passes_only_the_requested_zero_based_page_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "range-engine"
    executable.write_text(
        f"""#!{sys.executable}
import json
import pathlib
import sys
if '--version' in sys.argv:
    print('range-engine 1.0')
    raise SystemExit(0)
args = sys.argv[1:]
assert args[args.index('-s') + 1] == '10'
assert args[args.index('-e') + 1] == '12'
output = pathlib.Path(args[args.index('-o') + 1])
output.mkdir(exist_ok=True)
(output / 'source_content_list_v2.json').write_text('[[], [], []]', encoding='utf-8')
""",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("DEEPLAW_DOCUMENT_ENGINE", str(executable))

    result = document_engine.extract_pdf_page_range(
        _pdf(tmp_path), start_page=11, end_page=13, timeout_seconds=3
    )

    assert [page.page for page in result.pages] == [11, 12, 13]
    assert os.environ["DEEPLAW_DOCUMENT_ENGINE"] == str(executable)


def test_long_engine_version_is_preserved_by_prefix_and_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = "engine-version-" + "x" * 300
    monkeypatch.setattr(
        document_engine,
        "_run_bounded",
        lambda *_args, **_kwargs: (0, f"{version}\n".encode()),
    )

    result = document_engine._engine_version(tmp_path / "engine")

    assert result.startswith(version[:80])
    assert ";sha256=" in result
    assert len(result) <= 160
