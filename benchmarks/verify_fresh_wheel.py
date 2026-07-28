from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from deeplaw import __version__
from deeplaw.util import sha256_file


def _run(interpreter: Path, *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [str(interpreter), "-m", "deeplaw", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"fresh-wheel command failed ({completed.returncode}): "
            f"{' '.join(arguments)}\n{completed.stderr[:2000]}"
        )
    return json.loads(completed.stdout)


def verify_fresh_wheel(dist: Path) -> dict[str, Any]:
    wheels = sorted(dist.glob(f"deeplaw-{__version__}-*.whl"))
    if len(wheels) != 1 or wheels[0].is_symlink() or not wheels[0].is_file():
        raise RuntimeError("fresh-wheel verification requires exactly one current-version wheel")
    wheel = wheels[0].resolve()
    with tempfile.TemporaryDirectory(prefix="deeplaw-wheel-smoke-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(environment)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        interpreter = (
            environment / "Scripts" / "python.exe"
            if os.name == "nt"
            else environment / "bin" / "python"
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(interpreter), str(wheel)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        version = subprocess.run(
            [str(interpreter), "-m", "deeplaw", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        vault = root / "vault"
        source = root / "project.md"
        capsule = root / "capsule.json"
        source.write_text(
            "# Decision\nThe fresh wheel uses SQLite as its canonical local store.\n",
            encoding="utf-8",
        )
        initialized = _run(
            interpreter,
            "knowledge",
            "init",
            "--vault",
            str(vault),
            "--name",
            "fresh-wheel",
            "--scope",
            "project",
        )
        compiled = _run(
            interpreter,
            "knowledge",
            "source",
            "add",
            "--vault",
            str(vault),
            "--source",
            str(source),
            "--typed-extraction",
            "deterministic-v1",
            "--confirm-no-case-data",
        )
        source_id = compiled["source"]["source_id"]
        manifest = _run(
            interpreter,
            "knowledge",
            "review",
            "manifest",
            "--vault",
            str(vault),
            "--source-id",
            source_id,
        )
        approval = _run(
            interpreter,
            "knowledge",
            "review",
            "approve-source",
            "--vault",
            str(vault),
            "--source-id",
            source_id,
            "--review-manifest-sha256",
            manifest["review_manifest_sha256"],
            "--reviewer-id",
            "wheel-smoke",
            "--reason",
            "Fresh wheel exact-source acceptance.",
            "--confirm-reviewed",
        )
        https_preflight = _run(
            interpreter,
            "add",
            "--url",
            "https://example.com/source.md",
            "--dry-run",
            "--vault",
            str(vault),
            "--confirm-no-case-data",
            "--format",
            "json",
        )
        compiled_capsule = _run(
            interpreter,
            "knowledge",
            "context",
            "--vault",
            str(vault),
            "--task",
            "Which canonical local store must the fresh wheel use?",
            "--confirm-no-case-data",
            "--output",
            str(capsule),
        )
        verification = _run(
            interpreter,
            "knowledge",
            "verify-capsule",
            "--vault",
            str(vault),
            "--capsule",
            str(capsule),
        )
    return {
        "schema_version": "deeplaw.fresh-wheel-smoke/v1",
        "package_version": __version__,
        "wheel_name": wheel.name,
        "wheel_sha256": sha256_file(wheel),
        "reported_version": version,
        "vault_id": initialized["vault_id"],
        "source_id": source_id,
        "review_receipt_valid": approval["review_receipt"]["review_receipt_id"].startswith(
            "review_"
        ),
        "https_preflight_valid": (
            https_preflight["network_performed"] is False
            and https_preflight["canonical_requested_url"]
            == "https://example.com/source.md"
        ),
        "capsule_id": compiled_capsule["capsule_id"],
        "capsule_valid": verification["valid"],
        "valid": (
            verification["valid"]
            and https_preflight["network_performed"] is False
            and version == f"deeplaw {__version__}"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    result = verify_fresh_wheel(args.dist.expanduser().absolute())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
