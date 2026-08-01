from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from benchmarks.hosts.run_living_wiki_host_harness import _safe_command
from benchmarks.semantic.review_gold import validate_candidate
from deeplaw.util import canonical_json, stable_id


def _repository() -> Path:
    return Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _validate(name: str, value: dict[str, Any]) -> None:
    schema = _load(_repository() / "contracts" / name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _run_cli(prefix: list[str], *arguments: str) -> dict[str, Any]:
    completed = subprocess.run(
        [*prefix, "knowledge", "--format", "json", *arguments],
        cwd=_repository(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        summary = completed.stderr.decode("utf-8", errors="replace")[-2_000:]
        raise RuntimeError(f"DeepLaw CLI corpus preparation failed: {summary}")
    if len(completed.stdout) > 4 * 1024 * 1024:
        raise RuntimeError("DeepLaw CLI corpus preparation output exceeded 4 MiB")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("DeepLaw CLI corpus preparation returned a non-object")
    return value


def _review_and_activate(
    prefix: list[str],
    *,
    vault: Path,
    source_id: str,
    allow_quarantine: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _run_cli(
        prefix,
        "review",
        "manifest",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
    )
    arguments = [
        "review",
        "approve-source",
        "--vault",
        str(vault),
        "--source-id",
        source_id,
        "--review-manifest-sha256",
        manifest["review_manifest_sha256"],
        "--reviewer-id",
        "semantic-benchmark-maintainer",
        "--reason",
        "Frozen public Semantic Gold fixture reviewed for lifecycle evaluation.",
        "--confirm-reviewed",
    ]
    if allow_quarantine:
        arguments.append("--confirm-quarantine")
    approval = _run_cli(prefix, *arguments)
    if approval.get("source_activated") is not True:
        raise RuntimeError("baseline Semantic Gold source did not become active")
    return manifest, approval


def prepare(
    *,
    gold: dict[str, Any],
    vault: Path,
    snapshot: Path,
    command: dict[str, Any],
    allow_review_pending: bool,
) -> dict[str, Any]:
    validate_candidate(gold, repository=_repository())
    if gold["status"] != "maintainer_confirmed" and not allow_review_pending:
        raise RuntimeError(
            "formal real-host corpus preparation requires maintainer-confirmed Semantic Gold"
        )
    if vault.is_symlink() or vault.exists():
        raise FileExistsError("semantic host corpus requires a new non-symlink Vault path")
    if snapshot.is_symlink() or snapshot.exists():
        raise FileExistsError("semantic host corpus requires a new non-symlink snapshot path")
    prefix = _safe_command(command)
    initialization = _run_cli(
        prefix,
        "init",
        "--vault",
        str(vault),
        "--name",
        "DeepLaw frozen semantic host corpus",
        "--scope",
        "personal",
    )
    fixture_stage = vault.parent / f".{vault.name}-fixtures"
    if fixture_stage.exists() or fixture_stage.is_symlink():
        raise FileExistsError("semantic fixture stage already exists")
    fixture_stage.mkdir(mode=0o700, parents=True)
    sources: list[dict[str, Any]] = []
    atlas_predecessor: dict[str, Any] | None = None
    try:
        for fixture in gold["sources"]:
            if fixture["source_key"] == "update-v2":
                continue
            fixture_path = (_repository() / fixture["relative_path"]).resolve(strict=True)
            staged_name = (
                "atlas-release.md"
                if fixture["source_key"] == "update-v1"
                else fixture_path.name
            )
            staged_path = fixture_stage / staged_name
            shutil.copyfile(fixture_path, staged_path)
            ingest = _run_cli(
                prefix,
                "source",
                "add",
                "--vault",
                str(vault),
                "--source",
                str(staged_path),
                "--source-kind",
                "document",
                "--title",
                fixture["source_key"],
                "--trust",
                "user_provided",
                "--sensitivity",
                fixture["sensitivity"],
                "--confirm-no-case-data",
            )
            source = ingest["source"]
            manifest, _approval = _review_and_activate(
                prefix,
                vault=vault,
                source_id=source["source_id"],
                allow_quarantine=bool(source["instruction_risk"]),
            )
            binding = {
                "source_key": fixture["source_key"],
                "canonical_source_key": source["source_key"],
                "source_id": source["source_id"],
                "source_revision_id": source["source_revision_id"],
                "phase": "baseline",
                "initial_lifecycle_status": "active",
                "review_manifest_sha256": manifest["review_manifest_sha256"],
                "sensitivity": fixture["sensitivity"],
            }
            sources.append(binding)
            if fixture["source_key"] == "update-v1":
                atlas_predecessor = binding
        if atlas_predecessor is None:
            raise RuntimeError("Semantic Gold omits the required update-v1 fixture")
        successor_fixture = next(
            item for item in gold["sources"] if item["source_key"] == "update-v2"
        )
        successor_path = (_repository() / successor_fixture["relative_path"]).resolve(
            strict=True
        )
        staged_successor = fixture_stage / "atlas-release.md"
        shutil.copyfile(successor_path, staged_successor)
        successor_ingest = _run_cli(
            prefix,
            "source",
            "update",
            "--vault",
            str(vault),
            "--source-key",
            atlas_predecessor["canonical_source_key"],
            "--source",
            str(staged_successor),
            "--source-kind",
            "document",
            "--title",
            "update-v2",
            "--trust",
            "user_provided",
            "--sensitivity",
            successor_fixture["sensitivity"],
            "--confirm-no-case-data",
        )
        successor = successor_ingest["source"]
        successor_manifest = _run_cli(
            prefix,
            "review",
            "manifest",
            "--vault",
            str(vault),
            "--source-id",
            successor["source_id"],
        )
        sources.append(
            {
                "source_key": "update-v2",
                "canonical_source_key": successor["source_key"],
                "source_id": successor["source_id"],
                "source_revision_id": successor["source_revision_id"],
                "phase": "successor",
                "initial_lifecycle_status": "pending",
                "review_manifest_sha256": successor_manifest[
                    "review_manifest_sha256"
                ],
                "sensitivity": successor_fixture["sensitivity"],
            }
        )
        grant = _run_cli(
            prefix,
            "sink",
            "enable",
            "--vault",
            str(vault),
            "--writer-id",
            "semantic-real-host",
            "--profile",
            "semantic-compiler",
            "--scope",
            "personal",
            "--max-sensitivity",
            "restricted",
            "--max-mutations-per-minute",
            "120",
            "--max-objects",
            "100000",
        )
        snapshot_result = _run_cli(
            prefix,
            "snapshot",
            "create",
            "--vault",
            str(vault),
            "--output",
            str(snapshot),
        )
    finally:
        shutil.rmtree(fixture_stage, ignore_errors=True)
    sources.sort(key=lambda item: item["source_key"])
    body = {
        "schema_version": "deeplaw.semantic-host-corpus/v2",
        "gold_id": gold["gold_id"],
        "fixture_manifest_sha256": gold["fixture_manifest_sha256"],
        "vault_id": initialization["vault_id"],
        "snapshot_sha256": snapshot_result["snapshot_sha256"],
        "grant_id": grant["grant_id"],
        "sources": sources,
        "transitions": [
            {
                "operation": "activate_successor",
                "predecessor_source_key": "update-v1",
                "successor_source_key": "update-v2",
            },
            {"operation": "withdraw_source", "source_key": "retention-a"},
        ],
    }
    corpus = {
        **body,
        "corpus_id": stable_id("semanticcorpus", canonical_json(body)),
    }
    _validate("semantic-host-corpus.v2.schema.json", corpus)
    return corpus


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a phased public Semantic Gold Vault through the first-party CLI."
    )
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--deeplaw-command", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-review-pending", action="store_true")
    arguments = parser.parse_args()
    output = arguments.output.expanduser().absolute()
    if output.is_symlink() or output.exists():
        raise FileExistsError("semantic host corpus output must be a new non-symlink file")
    corpus = prepare(
        gold=_load(arguments.gold),
        vault=arguments.vault.expanduser().absolute(),
        snapshot=arguments.snapshot.expanduser().absolute(),
        command=_load(arguments.deeplaw_command),
        allow_review_pending=arguments.allow_review_pending,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_json(corpus) + "\n", encoding="utf-8")
    print(canonical_json(corpus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
