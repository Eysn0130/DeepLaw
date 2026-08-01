from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from deeplaw.editor_bridge import (
    merge_standard_mcp_config,
    tolaria_context_envelope,
    tolaria_mcp_servers,
    tolaria_open_note_request,
)
from deeplaw.util import strict_json_loads

MAX_INPUT_BYTES = 256 * 1024


def _json_object(path: Path) -> dict[str, Any]:
    selected = path.expanduser().absolute()
    if (
        selected.is_symlink()
        or not selected.is_file()
        or not 1 <= selected.stat().st_size <= MAX_INPUT_BYTES
    ):
        raise ValueError("input must be a bounded regular JSON file")
    value = strict_json_loads(selected.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("input must contain one JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate bounded DeepLaw/Tolaria adapter artifacts without modifying Tolaria"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("merge-mcp")
    setup.add_argument("--existing", type=Path, required=True)
    setup.add_argument("--output", type=Path, required=True)
    setup.add_argument("--deeplaw-executable", default="deeplaw")
    setup.add_argument("--vault", type=Path, required=True)
    setup.add_argument("--compiler-grant-id")
    setup.add_argument("--include-law-support", action="store_true")

    context = commands.add_parser("context-envelope")
    context.add_argument("--snapshot", type=Path, required=True)
    context.add_argument("--vault-id", required=True)
    context.add_argument("--intent", required=True)
    context.add_argument("--tolaria-version", required=True)
    context.add_argument("--scope", choices=("personal", "project", "domain"), default="project")
    context.add_argument(
        "--max-sensitivity",
        choices=("public", "internal", "private"),
        default="private",
    )

    open_note = commands.add_parser("open-note")
    open_note.add_argument("--vault", type=Path, required=True)
    open_note.add_argument("--path", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "merge-mcp":
        merged = merge_standard_mcp_config(
            _json_object(args.existing),
            tolaria_mcp_servers(
                deeplaw_executable=args.deeplaw_executable,
                vault_path=args.vault,
                compiler_grant_id=args.compiler_grant_id,
                include_law_support=args.include_law_support,
            ),
        )
        output = args.output.expanduser().absolute()
        if output.exists() or output.is_symlink():
            raise FileExistsError("output already exists; existing settings are never overwritten")
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(merged, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            output.unlink(missing_ok=True)
            raise
        result = {
            "schema_version": "deeplaw.tolaria-mcp-merge-receipt/v1",
            "existing_settings_preserved": True,
            "output_created": True,
            "output_mode": "0600",
            "mcp_server_names": sorted(
                name for name in merged["mcpServers"] if name.startswith("deeplaw_")
            ),
        }
    elif args.command == "context-envelope":
        result = tolaria_context_envelope(
            _json_object(args.snapshot),
            vault_identity=args.vault_id,
            user_intent=args.intent,
            frontend_version=args.tolaria_version,
            scope=args.scope,
            max_sensitivity=args.max_sensitivity,
        )
    else:
        result = tolaria_open_note_request(args.path, vault_path=args.vault)
    result_payload = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(result_payload.encode("utf-8")) > 65_536:
        raise RuntimeError("adapter result exceeds the provider-visible hard limit")
    print(result_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
