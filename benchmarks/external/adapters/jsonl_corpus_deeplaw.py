from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.external.benchlib import SCHEMA_RUN, strict_json_loads
from deeplaw.context_compiler import compile_context
from deeplaw.knowledge_store import (
    KnowledgeVault,
    initialize_knowledge_vault,
    knowledge_source_key,
)
from deeplaw.util import (
    canonical_json,
    has_instruction_risk,
    sha256_bytes,
    sha256_file,
)

ADAPTER_SCHEMA = "deeplaw.external-jsonl-corpus-adapter/v1"
_MAX_CORPUS_BYTES = 64 * 1024 * 1024
_MAX_CORPUS_CHARACTERS = 20 * 1024 * 1024
_MAX_RECORDS = 100_000
_MAX_FRAGMENTS = 100_000
_MAX_FRAGMENT_CHARS = 19_500
_MAX_IDENTIFIER_CHARS = 500


@dataclass(frozen=True, slots=True)
class _CorpusFragment:
    document_id: str
    title: str
    text: str
    record_number: int
    part: int
    part_count: int


def _bounded_text(value: Any, *, field: str, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise ValueError(f"{field} must be a bounded non-empty canonical string")
    return value


def _read_corpus(path: Path) -> tuple[list[_CorpusFragment], str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("external corpus must be a regular non-symlink JSONL file")
    if not 1 <= path.stat().st_size <= _MAX_CORPUS_BYTES:
        raise ValueError("external corpus is empty or exceeds 64 MiB")
    fragments: list[_CorpusFragment] = []
    seen_ids: set[str] = set()
    character_count = 0
    with path.open("r", encoding="utf-8", errors="strict") as stream:
        for record_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            if len(seen_ids) >= _MAX_RECORDS:
                raise ValueError("external corpus exceeds its record bound")
            record = strict_json_loads(raw_line)
            if not isinstance(record, dict) or set(record) != {"id", "title", "text"}:
                raise ValueError(
                    f"external corpus record {record_number} must contain id/title/text"
                )
            document_id = _bounded_text(
                record["id"],
                field=f"external corpus record {record_number} id",
                maximum=_MAX_IDENTIFIER_CHARS,
            )
            title = _bounded_text(
                record["title"],
                field=f"external corpus record {record_number} title",
                maximum=_MAX_IDENTIFIER_CHARS,
            )
            text = _bounded_text(
                record["text"],
                field=f"external corpus record {record_number} text",
                maximum=_MAX_CORPUS_CHARACTERS,
            )
            if document_id in seen_ids:
                raise ValueError(f"duplicate external corpus document id: {document_id}")
            seen_ids.add(document_id)
            character_count += len(text)
            if character_count > _MAX_CORPUS_CHARACTERS:
                raise ValueError("external corpus exceeds its character bound")
            parts = [
                text[start : start + _MAX_FRAGMENT_CHARS].strip()
                for start in range(0, len(text), _MAX_FRAGMENT_CHARS)
            ]
            parts = [part for part in parts if part]
            if len(fragments) + len(parts) > _MAX_FRAGMENTS:
                raise ValueError("external corpus exceeds its fragment bound")
            for part, fragment_text in enumerate(parts, start=1):
                fragments.append(
                    _CorpusFragment(
                        document_id=document_id,
                        title=title,
                        text=fragment_text,
                        record_number=record_number,
                        part=part,
                        part_count=len(parts),
                    )
                )
    if not fragments:
        raise ValueError("external corpus contains no records")
    return fragments, sha256_file(path)


def _read_queries(path: Path) -> list[dict[str, str]]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("external queries must be a regular non-symlink JSONL file")
    queries: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    with path.open("r", encoding="utf-8", errors="strict") as stream:
        for line_number, raw_line in enumerate(stream, start=1):
            if not raw_line.strip():
                continue
            if len(queries) >= _MAX_RECORDS:
                raise ValueError("external query file exceeds its record bound")
            record = strict_json_loads(raw_line)
            if not isinstance(record, dict) or set(record) != {"case_id", "query"}:
                raise ValueError(
                    f"external query record {line_number} must contain case_id/query"
                )
            case_id = _bounded_text(
                record["case_id"],
                field=f"external query record {line_number} case_id",
                maximum=_MAX_IDENTIFIER_CHARS,
            )
            query = _bounded_text(
                record["query"],
                field=f"external query record {line_number} query",
                maximum=4_000,
            )
            if case_id in seen_ids:
                raise ValueError(f"duplicate external query case_id: {case_id}")
            seen_ids.add(case_id)
            queries.append({"case_id": case_id, "query": query})
    if not queries:
        raise ValueError("external query file contains no records")
    return queries


class DeepLawJsonlCorpus:
    """Frozen-corpus adapter for external retrieval and end-to-end suites."""

    def __init__(
        self,
        *,
        workspace: Path,
        suite_id: str,
        max_items: int = 5,
        max_chars: int = 5_000,
    ) -> None:
        self.workspace = workspace.expanduser().absolute()
        self.suite_id = _bounded_text(
            suite_id,
            field="external suite_id",
            maximum=200,
        )
        if isinstance(max_items, bool) or not 1 <= max_items <= 20:
            raise ValueError("external adapter max_items must be between 1 and 20")
        if isinstance(max_chars, bool) or not 1 <= max_chars <= 20_000:
            raise ValueError("external adapter max_chars must be between 1 and 20000")
        self.max_items = max_items
        self.max_chars = max_chars
        self.vault_root = self.workspace / "vault"
        self._asset_to_document: dict[str, str] = {}

    def build(
        self,
        corpus: Path,
        *,
        frozen_fixture_approved: bool,
        approve_quarantined_fixture: bool = False,
    ) -> dict[str, Any]:
        if self.workspace.exists() or self.workspace.is_symlink():
            raise FileExistsError("external adapter workspace must be a new path")
        if not frozen_fixture_approved:
            raise ValueError(
                "external corpus activation requires evaluator approval of the "
                "frozen benchmark fixture"
            )
        self.workspace.mkdir(parents=True, mode=0o700)
        fragments, corpus_sha256 = _read_corpus(corpus)
        initialized = initialize_knowledge_vault(
            self.vault_root,
            name=f"External suite {self.suite_id}",
            scope="domain",
        )
        instruction_risk = any(has_instruction_risk(item.text) for item in fragments)
        origin_uri = f"benchmark://{self.suite_id}"
        source_key = knowledge_source_key(
            vault_id=initialized["vault_id"],
            source_kind="document",
            source_path=corpus,
            origin_uri=origin_uri,
        )
        compiled_sections = [
            {
                "title": (
                    item.title
                    if item.part_count == 1
                    else f"{item.title} · part {item.part}"
                ),
                "locator": (
                    f"jsonl:record:{item.record_number};"
                    f"document-sha256:"
                    f"{sha256_bytes(item.document_id.encode('utf-8'))};"
                    f"part:{item.part}/{item.part_count}"
                ),
                "text": item.text,
                "instruction_risk": has_instruction_risk(item.text),
            }
            for item in fragments
        ]
        compiler = {
            "schema_version": "deeplaw.knowledge-compiler/v1",
            "source_key": source_key,
            "adapter_schema": ADAPTER_SCHEMA,
            "format": "external-jsonl/v1",
            "source_sha256": corpus_sha256,
            "record_count": len({item.document_id for item in fragments}),
            "section_count": len(fragments),
            "compiled_fragment_sha256": sha256_bytes(
                canonical_json(compiled_sections).encode("utf-8")
            ),
            "instruction_risk": instruction_risk,
            "policy": (
                "external evaluator-owned frozen corpus; source fragments remain "
                "evidence and generated assets are benchmark-only review candidates"
            ),
        }
        with KnowledgeVault(self.vault_root, read_only=False) as vault:
            result = vault.add_compiled_source(
                source_path=corpus,
                source_key=source_key,
                expected_byte_size=corpus.stat().st_size,
                expected_content_sha256=corpus_sha256,
                source_kind="document",
                title=f"Frozen external corpus: {self.suite_id}",
                origin_uri=origin_uri,
                media_type="application/x-ndjson",
                trust="untrusted",
                sensitivity="private",
                instruction_risk=instruction_risk,
                warnings=(
                    ("external corpus contains instruction-like content",)
                    if instruction_risk
                    else ()
                ),
                compiler=compiler,
                fragments=tuple(
                    {
                        "text": section["text"],
                        "locator": section["locator"],
                        "instruction_risk": section["instruction_risk"],
                    }
                    for section in compiled_sections
                ),
                asset_specs=tuple(
                    {
                        "kind": "reference",
                        "memory_tier": "domain",
                        "title": section["title"],
                        "statement": section["text"],
                        "tags": ("external-benchmark",),
                        "warnings": (
                            ("fragment contains instruction-like content",)
                            if section["instruction_risk"]
                            else ()
                        ),
                    }
                    for section in compiled_sections
                ),
            )
            if instruction_risk and not approve_quarantined_fixture:
                approval = {
                    "approved_asset_count": 0,
                    "quarantined_asset_count": len(result["asset_ids"]),
                }
            else:
                review_manifest = vault.source_review_manifest(
                    result["source"]["source_id"]
                )
                approval = vault.approve_source_assets(
                    result["source"]["source_id"],
                    confirm_reviewed=True,
                    confirm_quarantined=instruction_risk,
                    review_manifest_sha256=review_manifest[
                        "review_manifest_sha256"
                    ],
                )
            self._asset_to_document = {
                asset_id: fragment.document_id
                for asset_id, fragment in zip(
                    result["asset_ids"],
                    fragments,
                    strict=True,
                )
            }
            integrity = vault.verify_integrity()
        return {
            "schema_version": ADAPTER_SCHEMA,
            "suite_id": self.suite_id,
            "corpus_sha256": corpus_sha256,
            "record_count": len(set(self._asset_to_document.values())),
            "fragment_count": len(fragments),
            "instruction_risk": instruction_risk,
            "approval": approval,
            "integrity_valid": integrity["valid"],
        }

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        if not self._asset_to_document:
            return []
        with KnowledgeVault(self.vault_root, read_only=True) as vault:
            capsule = compile_context(
                vault,
                task=query,
                confirm_no_case_data=True,
                max_items=self.max_items,
                max_chars=self.max_chars,
            )
        retrieved: list[dict[str, Any]] = []
        seen_documents: set[str] = set()
        for group in (
            "constraints",
            "decisions",
            "knowledge_assets",
            "experiences",
            "open_questions",
        ):
            for item in capsule[group]:
                document_id = self._asset_to_document.get(item["asset_id"])
                if document_id is None:
                    raise RuntimeError(
                        "external adapter result is absent from its frozen ID mapping"
                    )
                if document_id in seen_documents:
                    continue
                seen_documents.add(document_id)
                retrieved.append(
                    {
                        "id": document_id,
                        "chars": len(item["content"]),
                        "provenance_valid": True,
                    }
                )
        return retrieved

    def run_queries(self, queries: Path) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for record in _read_queries(queries):
            started = time.perf_counter()
            retrieved = self.retrieve(record["query"])
            runs.append(
                {
                    "schema_version": SCHEMA_RUN,
                    "case_id": record["case_id"],
                    "retrieved": retrieved,
                    "latency_ms": (time.perf_counter() - started) * 1_000,
                    "task_success": None,
                }
            )
        return runs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a frozen external JSONL corpus and query set through the real "
            "DeepLaw Knowledge Vault and Context Compiler."
        )
    )
    parser.add_argument("--suite-id", required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument("--max-chars", type=int, default=5_000)
    parser.add_argument("--frozen-fixture-approved", action="store_true")
    parser.add_argument("--approve-quarantined-fixture", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    adapter = DeepLawJsonlCorpus(
        workspace=args.workspace,
        suite_id=args.suite_id,
        max_items=args.max_items,
        max_chars=args.max_chars,
    )
    receipt = adapter.build(
        args.corpus.expanduser().absolute(),
        frozen_fixture_approved=args.frozen_fixture_approved,
        approve_quarantined_fixture=args.approve_quarantined_fixture,
    )
    runs = adapter.run_queries(args.queries.expanduser().absolute())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(
            json.dumps(run, ensure_ascii=False, sort_keys=True) + "\n"
            for run in runs
        ),
        encoding="utf-8",
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
