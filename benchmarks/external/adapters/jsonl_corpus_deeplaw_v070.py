from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, cast

from benchmarks.external.adapters.jsonl_corpus_deeplaw import (
    DeepLawJsonlCorpus,
    _read_queries,
)
from benchmarks.external.benchlib import SCHEMA_RUN
from deeplaw.knowledge_discovery import (
    DISCOVERY_MODEL_PROFILES,
    build_discovery_index,
)
from deeplaw.knowledge_store import KnowledgeVault
from deeplaw.retrieval_fabric import RetrievalMode, retrieve

ADAPTER_SCHEMA = "deeplaw.external-jsonl-corpus-adapter/v0.7-candidate"
PROFILES = {
    "lexical": "lexical",
    "hybrid": "hybrid",
    "full": "hybrid",
}


class DeepLawV070JsonlCorpus(DeepLawJsonlCorpus):
    """Candidate adapter that exercises the Evidence-Governed Retrieval Fabric."""

    def __init__(
        self,
        *,
        workspace: Path,
        suite_id: str,
        profile: str,
        max_items: int = 5,
        max_chars: int = 5_000,
        max_tokens: int = 4_096,
        discovery_profile: str | None = None,
        model_root: Path | None = None,
        threads: int | None = None,
    ) -> None:
        super().__init__(
            workspace=workspace,
            suite_id=suite_id,
            max_items=max_items,
            max_chars=max_chars,
        )
        if profile not in PROFILES:
            raise ValueError("DeepLaw candidate profile is invalid")
        if isinstance(max_tokens, bool) or not 256 <= max_tokens <= 100_000:
            raise ValueError("external adapter max_tokens must be between 256 and 100000")
        if profile != "lexical" and discovery_profile not in DISCOVERY_MODEL_PROFILES:
            raise ValueError(
                "hybrid/full candidate profiles require an explicit pinned Discovery profile"
            )
        self.profile = profile
        self.max_tokens = max_tokens
        self.discovery_profile = discovery_profile
        self.model_root = model_root
        self.threads = threads
        self.discovery_index_path = self.workspace / "discovery-index"

    def build(
        self,
        corpus: Path,
        *,
        frozen_fixture_approved: bool,
        approve_quarantined_fixture: bool = False,
    ) -> dict[str, Any]:
        receipt = super().build(
            corpus,
            frozen_fixture_approved=frozen_fixture_approved,
            approve_quarantined_fixture=approve_quarantined_fixture,
        )
        discovery: dict[str, Any] | None = None
        if self.profile != "lexical":
            with KnowledgeVault(self.vault_root, read_only=True) as vault:
                discovery = build_discovery_index(
                    vault,
                    self.discovery_index_path,
                    profile_name=cast(str, self.discovery_profile),
                    model_root=self.model_root,
                    confirm_no_case_data=True,
                    threads=self.threads,
                )
        return {
            **receipt,
            "schema_version": ADAPTER_SCHEMA,
            "candidate_profile": self.profile,
            "retrieval_mode": PROFILES[self.profile],
            "max_tokens": self.max_tokens,
            "discovery": discovery,
            "claim_eligible": False,
        }

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        if not self._asset_to_document:
            return []
        discovery = self.discovery_index_path if self.profile != "lexical" else None
        with KnowledgeVault(self.vault_root, read_only=True) as vault:
            result = retrieve(
                vault,
                query,
                mode=cast(RetrievalMode, PROFILES[self.profile]),
                limit=self.max_items,
                max_chars=self.max_chars,
                discovery_index_path=discovery,
                model_root=self.model_root,
                threads=self.threads,
                explain=True,
            )
        retrieved: list[dict[str, Any]] = []
        seen_documents: set[str] = set()
        for item in result["results"]:
            document_id = self._asset_to_document.get(item["asset_id"])
            if document_id is None:
                raise RuntimeError("candidate result is absent from its frozen ID mapping")
            if document_id in seen_documents:
                continue
            seen_documents.add(document_id)
            retrieved.append(
                {
                    "id": document_id,
                    "chars": len(item["excerpt"]),
                    "provenance_valid": bool(item["source_refs"]),
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
        description="Run the v0.7 candidate Retrieval Fabric on frozen JSONL data"
    )
    parser.add_argument("--suite-id", required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--max-items", type=int, default=5)
    parser.add_argument("--max-chars", type=int, default=5_000)
    parser.add_argument("--max-tokens", type=int, default=4_096)
    parser.add_argument("--discovery-profile", choices=sorted(DISCOVERY_MODEL_PROFILES))
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--frozen-fixture-approved", action="store_true")
    parser.add_argument("--approve-quarantined-fixture", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    adapter = DeepLawV070JsonlCorpus(
        workspace=args.workspace.expanduser().absolute(),
        suite_id=args.suite_id,
        profile=args.profile,
        max_items=args.max_items,
        max_chars=args.max_chars,
        max_tokens=args.max_tokens,
        discovery_profile=args.discovery_profile,
        model_root=(args.model_root.expanduser().absolute() if args.model_root else None),
        threads=args.threads,
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
            json.dumps(run, ensure_ascii=False, sort_keys=True) + "\n" for run in runs
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
