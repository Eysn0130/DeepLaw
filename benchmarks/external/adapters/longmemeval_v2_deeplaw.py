from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Any

from deeplaw.context_compiler import compile_context
from deeplaw.knowledge_compiler import compile_source
from deeplaw.knowledge_store import KnowledgeVault, initialize_knowledge_vault
from deeplaw.util import canonical_json, sha256_bytes

try:
    from memory_modules.memory import (
        Memory,
        MemoryConfig,
        MemoryContextItem,
        register_memory,
        require,
    )
except ModuleNotFoundError as error:
    if error.name not in {"memory_modules", "memory_modules.memory"}:
        raise

    MemoryConfig = dict[str, Any]
    MemoryContextItem = dict[str, str]

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise RuntimeError(message)

    def register_memory(memory_class: type[Memory]) -> type[Memory]:
        return memory_class

    class Memory:
        memory_type = ""

        def __init__(self, memory_params: dict[str, object]) -> None:
            self.memory_params = dict(memory_params)


def _text(value: Any, *, fallback: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return fallback
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _trajectory_markdown(trajectory: dict[str, object]) -> tuple[str, str]:
    trajectory_id = _text(trajectory.get("id"))
    require(bool(trajectory_id), "LongMemEval-V2 trajectory id must be non-empty")
    lines = [
        f"# Trajectory {trajectory_id}",
        "",
        "## Goal",
        "",
        _text(trajectory.get("goal"), fallback="<goal unavailable>"),
        "",
        "## Outcome",
        "",
        _text(trajectory.get("outcome"), fallback="<outcome unavailable>"),
        "",
        "## Start URL",
        "",
        _text(trajectory.get("start_url"), fallback="<start URL unavailable>"),
    ]
    states = trajectory.get("states")
    if isinstance(states, list):
        for index, state in enumerate(states):
            if not isinstance(state, dict):
                continue
            lines.extend(
                [
                    "",
                    f"## State {index:04d}",
                    "",
                    f"URL: {_text(state.get('url'), fallback='<URL unavailable>')}",
                    "",
                    f"Action: {_text(state.get('action'), fallback='<none>')}",
                    "",
                    "Thought: "
                    + _text(
                        state.get("thought", state.get("thoughts")),
                        fallback="<none>",
                    ),
                    "",
                    _text(
                        state.get("accessibility_tree", state.get("text")),
                        fallback="<state text unavailable>",
                    ),
                    "",
                    f"Screenshot: {_text(state.get('screenshot'), fallback='<none>')}",
                ]
            )
    else:
        content = trajectory.get("content")
        lines.extend(["", "## Content", "", _text(content, fallback="<content unavailable>")])
    return trajectory_id, "\n".join(lines).strip() + "\n"


@register_memory
class DeepLawMemory(Memory):
    """Text operating point for the official LongMemEval-V2 memory interface."""

    memory_type = "deeplaw"

    def __init__(self, memory_params: dict[str, object]) -> None:
        super().__init__(memory_params)
        allowed = {
            "workspace_dir",
            "max_items",
            "max_chars",
            "frozen_fixture_approved",
        }
        unexpected = sorted(set(memory_params) - allowed)
        require(not unexpected, f"deeplaw memory has unexpected parameters: {unexpected}")
        workspace_value = memory_params.get("workspace_dir")
        require(
            isinstance(workspace_value, str) and bool(workspace_value.strip()),
            "deeplaw memory requires a non-empty workspace_dir",
        )
        require(
            memory_params.get("frozen_fixture_approved") is True,
            "deeplaw memory requires explicit frozen benchmark fixture approval",
        )
        self.workspace_dir = Path(workspace_value).expanduser().absolute()
        self.vault_root = self.workspace_dir / "vault"
        self.source_root = self.workspace_dir / "sources"
        self.max_items = int(memory_params.get("max_items", 8))
        self.max_chars = int(memory_params.get("max_chars", 6_000))
        require(1 <= self.max_items <= 12, "deeplaw max_items must be between 1 and 12")
        require(1 <= self.max_chars <= 12_000, "deeplaw max_chars must be between 1 and 12000")
        self.workspace_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.source_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        initialize_knowledge_vault(
            self.vault_root,
            name="LongMemEval-V2 frozen fixture",
            scope="project",
        )
        self._query_state = threading.local()

    def insert(self, trajectory: dict[str, object]) -> None:
        require(isinstance(trajectory, dict), "trajectory must be an object")
        trajectory_id, markdown = _trajectory_markdown(trajectory)
        filename = f"{sha256_bytes(trajectory_id.encode('utf-8'))}.md"
        source_path = self.source_root / filename
        encoded = markdown.encode("utf-8")
        if source_path.exists():
            require(
                source_path.is_file()
                and not source_path.is_symlink()
                and source_path.read_bytes() == encoded,
                f"trajectory {trajectory_id} changed after insertion",
            )
        else:
            source_path.write_bytes(encoded)
            source_path.chmod(0o600)
        with KnowledgeVault(self.vault_root, read_only=False) as vault:
            result = compile_source(
                vault,
                source_path,
                source_kind="tool_result",
                title=f"Trajectory {trajectory_id}",
                origin_uri=f"benchmark://longmemeval-v2/trajectory/{trajectory_id}",
                trust="untrusted",
                sensitivity="private",
                confirm_no_case_data=True,
            )
            review_manifest = vault.source_review_manifest(
                result["source"]["source_id"]
            )
            vault.approve_source_assets(
                result["source"]["source_id"],
                confirm_reviewed=True,
                confirm_quarantined=bool(result["source"]["instruction_risk"]),
                review_manifest_sha256=review_manifest["review_manifest_sha256"],
            )

    def query(
        self,
        query: str,
        query_image: str | None = None,
    ) -> list[MemoryContextItem]:
        require(isinstance(query, str) and bool(query.strip()), "query must be non-empty")
        with KnowledgeVault(self.vault_root, read_only=True) as vault:
            capsule = compile_context(
                vault,
                task=query,
                confirm_no_case_data=True,
                max_items=self.max_items,
                max_chars=self.max_chars,
            )
        self._query_state.metadata = {
            "capsule_id": capsule["capsule_id"],
            "selected_items": capsule["budget"]["selected_items"],
            "selected_chars": capsule["budget"]["selected_chars"],
            "query_image_ignored": query_image is not None,
        }
        return [{"type": "text", "value": canonical_json(capsule)}]

    def post_query_hook(
        self,
        *,
        query: str,
        query_image: str | None,
        memory_context: list[MemoryContextItem],
    ) -> dict[str, object] | None:
        return dict(getattr(self._query_state, "metadata", {}))

    def _save_backend(self, output_dir: Path) -> None:
        destination = output_dir / "deeplaw_vault"
        require(not destination.exists(), f"saved DeepLaw vault already exists: {destination}")
        shutil.copytree(self.vault_root, destination, symlinks=False)

    def _load_backend(self, input_dir: Path) -> None:
        source = input_dir / "deeplaw_vault"
        require(source.is_dir() and not source.is_symlink(), "saved DeepLaw vault is missing")
        if self.vault_root.exists():
            shutil.rmtree(self.vault_root)
        shutil.copytree(source, self.vault_root, symlinks=False)
        with KnowledgeVault(self.vault_root, read_only=True) as vault:
            require(vault.verify_integrity()["valid"], "loaded DeepLaw vault failed integrity")
