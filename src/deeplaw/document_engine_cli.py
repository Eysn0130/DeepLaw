from __future__ import annotations

import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .document_engine_models import (
    PINNED_ENGINE_VERSION,
    isolated_engine_environment,
    model_manifest_sha256,
)

_PIPELINE_FLAGS = ("-p", "-o", "-m", "-b", "-l", "-s", "-e")
_PIPELINE_METHODS = frozenset({"auto", "txt", "ocr"})
_MAX_PAGE_RANGE = 5_000


class DocumentEngineInvocationError(ValueError):
    """Raised before upstream code is imported for an unsafe invocation."""


def _parse_page_index(value: str, *, field: str) -> int:
    if not value.isascii() or not value.isdecimal():
        raise DocumentEngineInvocationError(f"{field} must be a non-negative integer")
    parsed = int(value)
    if parsed > 2_147_483_647:
        raise DocumentEngineInvocationError(f"{field} is too large")
    return parsed


def _validate_pipeline_argv(argv: list[str]) -> None:
    """Accept only the closed command emitted by DeepLaw's bounded adapter."""

    if argv == ["--version"]:
        return
    if len(argv) != len(_PIPELINE_FLAGS) * 2:
        raise DocumentEngineInvocationError(
            "only the bounded DeepLaw pipeline invocation is supported"
        )

    values: dict[str, str] = {}
    for index, expected_flag in enumerate(_PIPELINE_FLAGS):
        flag = argv[index * 2]
        value = argv[index * 2 + 1]
        if flag != expected_flag:
            raise DocumentEngineInvocationError(
                f"expected {expected_flag} at argument {index * 2 + 1}; "
                "unknown, reordered, or duplicate options are forbidden"
            )
        if not value:
            raise DocumentEngineInvocationError(f"{flag} must not be blank")
        values[flag] = value

    source = Path(values["-p"])
    output = Path(values["-o"])
    if not source.is_absolute() or source.suffix.lower() != ".pdf" or not source.is_file():
        raise DocumentEngineInvocationError("-p must be an existing absolute PDF path")
    if not output.is_absolute() or not output.is_dir():
        raise DocumentEngineInvocationError("-o must be an existing absolute directory")
    if values["-m"] not in _PIPELINE_METHODS:
        raise DocumentEngineInvocationError("-m must be one of auto, txt, or ocr")
    if values["-b"] != "pipeline":
        raise DocumentEngineInvocationError(
            "-b must be pipeline; VLM, hybrid, remote-model, and custom backends are forbidden"
        )

    language = values["-l"]
    if len(language) > 32 or not language.replace("-", "").isalnum():
        raise DocumentEngineInvocationError("-l must be a short language identifier")

    start = _parse_page_index(values["-s"], field="-s")
    end = _parse_page_index(values["-e"], field="-e")
    if end < start:
        raise DocumentEngineInvocationError("-e must not be smaller than -s")
    if end - start + 1 > _MAX_PAGE_RANGE:
        raise DocumentEngineInvocationError(
            f"requested page range exceeds {_MAX_PAGE_RANGE} pages"
        )


def _upstream_main() -> Callable[[], object]:
    try:
        from mineru.cli.client import main
    except ModuleNotFoundError as error:
        if error.name == "mineru" or (error.name and error.name.startswith("mineru.")):
            raise RuntimeError(
                "DeepLaw document engine is not installed; install "
                "deeplaw[document-engine]"
            ) from error
        raise
    return main


def _version_line() -> str:
    try:
        installed = version("mineru")
    except PackageNotFoundError as error:
        raise RuntimeError(
            "DeepLaw document engine is not installed; install "
            "deeplaw[document-engine]"
        ) from error
    if installed != PINNED_ENGINE_VERSION:
        raise RuntimeError(
            "DeepLaw document engine version mismatch: "
            f"expected {PINNED_ENGINE_VERSION}, found {installed}"
        )
    return (
        f"deeplaw-document-engine {installed} "
        f"model-manifest-sha256={model_manifest_sha256()}"
    )


def main() -> None:
    """Run the structured PDF engine through DeepLaw's pinned adapter entrypoint."""

    try:
        argv = sys.argv[1:]
        _validate_pipeline_argv(argv)
        if argv == ["--version"]:
            print(_version_line())
            return
        with isolated_engine_environment():
            result = _upstream_main()()
    except (DocumentEngineInvocationError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
    if isinstance(result, int):
        raise SystemExit(result)


if __name__ == "__main__":
    main()
