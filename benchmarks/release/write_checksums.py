from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchmarks.release.evidence import sha256_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Write stable SHA-256 release checksums.")
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.assets_root.resolve(strict=True)
        output = args.output.resolve()
        files = [
            path
            for path in sorted(root.rglob("*"), key=lambda item: item.name)
            if path.is_file() and path.resolve() != output
        ]
        names = [path.name for path in files]
        if not files or len(names) != len(set(names)):
            raise RuntimeError("release checksum inputs must have unique basenames")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            "".join(f"{sha256_file(path)}  {path.name}\n" for path in files),
            encoding="utf-8",
        )
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
