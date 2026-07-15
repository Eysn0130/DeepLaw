from __future__ import annotations

import sys
from collections.abc import Callable


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


def main() -> None:
    """Run the structured PDF engine through DeepLaw's pinned adapter entrypoint."""

    try:
        result = _upstream_main()()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
    if isinstance(result, int):
        raise SystemExit(result)


if __name__ == "__main__":
    main()
