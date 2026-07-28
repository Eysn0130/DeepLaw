from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from collections import defaultdict
from html import escape
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from .knowledge_models import SENSITIVITY_LEVELS, KnowledgeAsset, Sensitivity, utc_now
from .knowledge_store import KnowledgeVault
from .util import canonical_json, sha256_bytes, sha256_file, stable_id, strict_json_loads

KNOWLEDGE_MARKDOWN_SCHEMA = "deeplaw.knowledge-markdown/v2"
_LEGACY_SCHEMA = "deeplaw.knowledge-markdown/v1"
_SENSITIVITY_RANK = {
    "public": 0,
    "internal": 1,
    "private": 2,
    "restricted": 3,
}
_MARKDOWN_INLINE = re.compile(r"([\\`*_[\]{}()#+.!|>\-])")
_PROJECTION_DIRECTORIES = (
    "sources",
    "knowledge",
    "concepts",
    "decisions",
    "constraints",
    "procedures",
    "experiences",
    "questions",
    "capsules",
    "feedback",
    "lineage",
    "graphs",
    "canvas",
)
_KIND_DIRECTORY = {
    "decision": "decisions",
    "constraint": "constraints",
    "requirement": "constraints",
    "rule": "constraints",
    "procedure": "procedures",
    "experience": "experiences",
    "lesson": "experiences",
    "question": "questions",
    "definition": "concepts",
}


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml_optional(value: str | None) -> str:
    return "null" if value is None else _yaml_string(value)


def _yaml_list(values: list[str] | tuple[str, ...]) -> str:
    return json.dumps(list(values), ensure_ascii=False)


def _markdown_inline(value: str) -> str:
    single_line = " ".join(value.splitlines())
    return _MARKDOWN_INLINE.sub(r"\\\1", escape(single_line, quote=False))


def _literal_block(value: str) -> list[str]:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * max(4, longest + 1)
    return [f"{fence}text", value, fence]


def _wikilink(path: Path | str, label: str | None = None) -> str:
    target = Path(path).with_suffix("").as_posix()
    return f"[[{target}]]" if label is None else f"[[{target}|{_markdown_inline(label)}]]"


def _write(path: Path, text: str) -> dict[str, Any]:
    payload = text.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return {
        "path": path.as_posix(),
        "byte_size": len(payload),
        "sha256": sha256_bytes(payload),
    }


def _safe_remove_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError("Markdown export destination is unsafe")
    for child in path.rglob("*"):
        if child.is_symlink():
            raise RuntimeError("Markdown export destination contains a symbolic link")
    shutil.rmtree(path)


def _validated_file_inventory(path: Path, files: Any) -> set[str]:
    if not isinstance(files, list) or len(files) < 2:
        raise RuntimeError("refusing to replace an invalid DeepLaw export")
    expected_paths = {"manifest.json"}
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "byte_size", "sha256"}:
            raise RuntimeError("refusing to replace an invalid DeepLaw export")
        relative = item["path"]
        pure_path = PurePosixPath(relative) if isinstance(relative, str) else None
        if (
            pure_path is None
            or pure_path.is_absolute()
            or ".." in pure_path.parts
            or pure_path.as_posix() != relative
            or relative in expected_paths
            or isinstance(item["byte_size"], bool)
            or not isinstance(item["byte_size"], int)
            or item["byte_size"] < 0
            or not isinstance(item["sha256"], str)
            or len(item["sha256"]) != 64
        ):
            raise RuntimeError("refusing to replace an invalid DeepLaw export")
        target = path.joinpath(*pure_path.parts)
        if (
            target.is_symlink()
            or not target.is_file()
            or target.stat().st_size != item["byte_size"]
            or sha256_file(target) != item["sha256"]
        ):
            raise RuntimeError("refusing to replace a modified DeepLaw export")
        expected_paths.add(relative)
    return expected_paths


def _require_owned_export(path: Path) -> None:
    manifest_path = path / "manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 4 * 1024 * 1024
    ):
        raise RuntimeError("refusing to replace a directory not owned by a DeepLaw export")
    try:
        manifest = strict_json_loads(manifest_path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("refusing to replace an invalid DeepLaw export") from error
    if not isinstance(manifest, dict):
        raise RuntimeError("refusing to replace an invalid DeepLaw export")
    schema = manifest.get("schema_version")
    legacy_fields = {
        "schema_version",
        "vault_id",
        "vault_revision",
        "audit_head",
        "max_sensitivity",
        "asset_count",
        "files",
    }
    current_fields = legacy_fields | {
        "source_count",
        "concept_count",
        "projection_file_count",
    }
    expected_fields = legacy_fields if schema == _LEGACY_SCHEMA else current_fields
    if (
        schema not in {_LEGACY_SCHEMA, KNOWLEDGE_MARKDOWN_SCHEMA}
        or set(manifest) != expected_fields
    ):
        raise RuntimeError("refusing to replace an invalid DeepLaw export")
    files = manifest.get("files")
    if (
        not isinstance(manifest.get("vault_id"), str)
        or not manifest["vault_id"].startswith("vault_")
        or isinstance(manifest.get("vault_revision"), bool)
        or not isinstance(manifest.get("vault_revision"), int)
        or manifest["vault_revision"] < 0
        or not isinstance(manifest.get("audit_head"), str)
        or len(manifest["audit_head"]) != 64
        or manifest.get("max_sensitivity") not in SENSITIVITY_LEVELS
        or isinstance(manifest.get("asset_count"), bool)
        or not isinstance(manifest.get("asset_count"), int)
        or manifest["asset_count"] < 0
    ):
        raise RuntimeError("refusing to replace a directory not owned by a DeepLaw export")
    expected_paths = _validated_file_inventory(path, files)
    actual_paths = {
        child.relative_to(path).as_posix() for child in path.rglob("*") if child.is_file()
    }
    if actual_paths != expected_paths or "INDEX.md" not in expected_paths:
        raise RuntimeError("refusing to replace a DeepLaw export with untracked or missing files")
    if schema == _LEGACY_SCHEMA:
        asset_files = sum(relative != "INDEX.md" for relative in expected_paths) - 1
        if asset_files != manifest["asset_count"]:
            raise RuntimeError(
                "refusing to replace a DeepLaw export with untracked or missing files"
            )
    elif any(
        isinstance(manifest.get(field), bool)
        or not isinstance(manifest.get(field), int)
        or manifest[field] < 0
        for field in ("source_count", "concept_count", "projection_file_count")
    ) or manifest["projection_file_count"] != len(files):
        raise RuntimeError("refusing to replace an invalid DeepLaw export")


def _identity_rows(vault: KnowledgeVault) -> dict[str, dict[str, Any]]:
    if not vault.identity_v2_enabled:
        return {}
    rows = vault.connection.execute(
        """
        SELECT bindings.legacy_asset_id, bindings.asset_revision_id,
               revisions.knowledge_key, revisions.source_revision_ids_json,
               metadata.observed_at, metadata.valid_from, metadata.valid_to,
               metadata.applicability_json, metadata.warnings_json
        FROM asset_revision_bindings_v2 AS bindings
        JOIN knowledge_revisions_v2 AS revisions USING(asset_revision_id)
        LEFT JOIN proposal_metadata_v2 AS metadata
          ON metadata.proposal_set_id = bindings.proposal_set_id
         AND metadata.proposal_ordinal = bindings.proposal_ordinal
        ORDER BY bindings.legacy_asset_id
        """
    ).fetchall()
    return {
        row["legacy_asset_id"]: {
            "asset_revision_id": row["asset_revision_id"],
            "knowledge_key": row["knowledge_key"],
            "source_revision_ids": strict_json_loads(row["source_revision_ids_json"]),
            "observed_at": row["observed_at"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "applicability": (
                strict_json_loads(row["applicability_json"])
                if row["applicability_json"] is not None
                else {}
            ),
            "proposal_warnings": (
                strict_json_loads(row["warnings_json"]) if row["warnings_json"] is not None else []
            ),
        }
        for row in rows
    }


def _fallback_identity(vault: KnowledgeVault, asset: KnowledgeAsset) -> dict[str, Any]:
    knowledge_key = stable_id(
        "knowledge",
        vault.vault_id,
        asset.semantic_key or asset.asset_id,
    )
    return {
        "knowledge_key": knowledge_key,
        "asset_revision_id": stable_id(
            "assetrev", knowledge_key, asset.content_sha256, canonical_json([])
        ),
        "source_revision_ids": [],
        "observed_at": asset.created_at,
        "valid_from": None,
        "valid_to": None,
        "applicability": {},
        "proposal_warnings": [],
    }


def _category(asset: KnowledgeAsset) -> str:
    return _KIND_DIRECTORY.get(asset.kind, "knowledge")


def _asset_page_path(asset: KnowledgeAsset, identity: dict[str, Any]) -> Path:
    return Path(_category(asset)) / f"{identity['knowledge_key']}.md"


def _source_page_paths(sources: list[dict[str, Any]]) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for source in sources:
        key = source.get("canonical_source_key") or source.get("source_key") or source["source_id"]
        paths[source["source_id"]] = Path("sources") / f"{key}.md"
    return paths


def _canvas_node_id(kind: str, value: str) -> str:
    return sha256_bytes(f"{kind}\0{value}".encode())[:16]


def _canvas_file_node(path: Path, *, x: int, y: int, width: int = 360) -> dict[str, Any]:
    return {
        "id": _canvas_node_id("file", path.as_posix()),
        "type": "file",
        "file": f"../{path.as_posix()}",
        "x": x,
        "y": y,
        "width": width,
        "height": 220,
    }


def _canvas_edge(
    edge_key: str,
    from_node: str,
    to_node: str,
    *,
    label: str | None = None,
) -> dict[str, Any]:
    edge: dict[str, Any] = {
        "id": _canvas_node_id("edge", edge_key),
        "fromNode": from_node,
        "fromSide": "right",
        "toNode": to_node,
        "toSide": "left",
    }
    if label is not None:
        edge["label"] = label[:120]
    return edge


def _json_canvas(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    return (
        json.dumps(
            {"nodes": nodes, "edges": edges},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _load_last_capsule(vault: KnowledgeVault) -> dict[str, Any] | None:
    path = vault.root / "derived" / "retrieval" / "last-capsule.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        return None
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    try:
        verification = vault.verify_capsule(value)
    except (KeyError, RuntimeError, TypeError, ValueError):
        return None
    return value if verification.get("valid") is True else None


def _asset_page(
    *,
    vault: KnowledgeVault,
    asset: KnowledgeAsset,
    identity: dict[str, Any],
    source_paths: dict[str, Path],
    asset_paths: dict[str, Path],
    outgoing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    concept_paths: list[Path],
    lineage_path: Path,
) -> str:
    obsidian_uri = "obsidian://open?file=" + quote(
        asset_paths[asset.asset_id].with_suffix("").as_posix()
    )
    source_links = [
        _wikilink(source_paths[reference.source_id])
        for reference in asset.source_refs
        if reference.source_id in source_paths
    ]
    relation_lines: list[str] = []
    backlink_lines: list[str] = []
    for relation in outgoing:
        target = asset_paths[relation["object_asset_id"]]
        relation_lines.append(f"- `{relation['predicate']}` → {_wikilink(target)}")
    for relation in incoming:
        source = asset_paths[relation["subject_asset_id"]]
        backlink_lines.append(f"- ← `{relation['predicate']}` from {_wikilink(source)}")
    gaps: list[str] = list(asset.warnings)
    gaps.extend(str(item) for item in identity["proposal_warnings"])
    if not asset.source_refs:
        gaps.append("No source reference is bound to this asset.")
    if not outgoing and not incoming:
        gaps.append("No reviewed relation connects this asset.")
    supersedes_link = (
        _wikilink(asset_paths[asset.supersedes_asset_id])
        if asset.supersedes_asset_id in asset_paths
        else None
    )
    return "\n".join(
        [
            "---",
            f"schema: {_yaml_string('deeplaw.human-projection/asset-v2')}",
            f"asset_id: {_yaml_string(asset.asset_id)}",
            f"asset_revision_id: {_yaml_string(identity['asset_revision_id'])}",
            f"knowledge_key: {_yaml_string(identity['knowledge_key'])}",
            f"uri: {_yaml_string(asset.uri)}",
            f"obsidian_uri: {_yaml_string(obsidian_uri)}",
            f"title: {_yaml_string(asset.title)}",
            f"kind: {_yaml_string(asset.kind)}",
            f"memory_tier: {_yaml_string(asset.memory_tier)}",
            f"status: {_yaml_string(asset.status)}",
            f"review_status: {_yaml_string(asset.verification)}",
            f"trust: {_yaml_string(asset.trust)}",
            f"sensitivity: {_yaml_string(asset.sensitivity)}",
            "legal_authority: false",
            f"directive_mode: {_yaml_string(asset.directive_mode)}",
            f"observed_at: {_yaml_optional(identity['observed_at'])}",
            f"valid_from: {_yaml_optional(identity['valid_from'])}",
            f"valid_to: {_yaml_optional(identity['valid_to'])}",
            f"expires_at: {_yaml_optional(asset.expires_at)}",
            f"supersedes: {_yaml_optional(asset.supersedes_asset_id)}",
            f"source_links: {_yaml_list(source_links)}",
            f"tags: {_yaml_list(asset.tags)}",
            f"content_sha256: {_yaml_string(asset.content_sha256)}",
            "derived_projection: true",
            "---",
            "",
            f"# {_markdown_inline(asset.title)}",
            "",
            *_literal_block(asset.statement),
            "",
            "## Provenance",
            "",
            "```json",
            json.dumps(
                [reference.to_dict() for reference in asset.source_refs],
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
            "## Sources",
            "",
            *(
                [
                    f"- {_wikilink(source_paths[reference.source_id])} — "
                    f"`{_markdown_inline(reference.locator)}`; "
                    f"quote SHA-256 `{reference.quote_sha256}`"
                    for reference in asset.source_refs
                    if reference.source_id in source_paths
                ]
                or ["- None"]
            ),
            "",
            "## Applicability",
            "",
            "```json",
            json.dumps(identity["applicability"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Supersedes",
            "",
            f"- {supersedes_link or 'None'}",
            "",
            "## Relations",
            "",
            *(relation_lines or ["- None"]),
            "",
            "## Backlinks",
            "",
            *(backlink_lines or ["- None"]),
            *[f"- Concept: {_wikilink(path)}" for path in concept_paths],
            f"- Lineage: {_wikilink(lineage_path)}",
            "",
            "## Gaps",
            "",
            *([f"- {_markdown_inline(item)}" for item in sorted(set(gaps))] or ["- None recorded"]),
            "",
            (
                "> This page is derived. Editing it never changes the canonical "
                "vault; use the projection diff workflow to create a reviewed proposal."
            ),
            "",
        ]
    )


def export_knowledge_markdown(
    vault: KnowledgeVault,
    output: str | Path,
    *,
    max_sensitivity: Sensitivity = "private",
    replace: bool = False,
) -> dict[str, Any]:
    if max_sensitivity not in SENSITIVITY_LEVELS:
        raise ValueError("unsupported Markdown export sensitivity")
    if not vault.verify_integrity()["valid"]:
        raise RuntimeError("knowledge vault integrity is invalid; Markdown export stopped")
    destination = Path(output).expanduser().absolute()
    if destination.is_symlink():
        raise RuntimeError("Markdown export destination must not be a symbolic link")
    if destination.exists() and not replace:
        raise FileExistsError(
            "Markdown export destination already exists; use an empty new path or --replace"
        )
    if destination.exists():
        _require_owned_export(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.parent / f".{destination.name}.{secrets.token_hex(8)}.tmp"
    stage.mkdir(mode=0o700)
    try:
        for directory in _PROJECTION_DIRECTORIES:
            (stage / directory).mkdir(mode=0o700)
        allowed_rank = _SENSITIVITY_RANK[max_sensitivity]
        assets = [
            asset
            for asset in vault.all_assets(statuses=("active",))
            if _SENSITIVITY_RANK[asset.sensitivity] <= allowed_rank
            and (asset.expires_at is None or asset.expires_at > utc_now())
        ]
        source_integrity = vault.verify_source_files(
            reference.source_id for asset in assets for reference in asset.source_refs
        )
        if not source_integrity["valid"]:
            raise RuntimeError(
                "selected Knowledge Asset source evidence failed integrity verification; "
                "Markdown export stopped"
            )
        selected_ids = {asset.asset_id for asset in assets}
        all_sources = [
            source
            for source in vault.all_sources()
            if _SENSITIVITY_RANK[source["sensitivity"]] <= allowed_rank
            and source["source_id"]
            in {reference.source_id for asset in assets for reference in asset.source_refs}
        ]
        source_by_id = {source["source_id"]: source for source in all_sources}
        relations = [
            relation
            for relation in vault.all_relations()
            if relation["subject_asset_id"] in selected_ids
            and relation["object_asset_id"] in selected_ids
        ]
        for relation in relations:
            evidence_fragment_id = relation["evidence_fragment_id"]
            if evidence_fragment_id is None:
                continue
            fragment = vault.get_fragment(evidence_fragment_id)
            evidence_source = source_by_id.get(fragment["source_id"])
            if evidence_source is None:
                raise ValueError(
                    "knowledge relation evidence exceeds the Markdown sensitivity policy"
                )
        outgoing: dict[str, list[dict[str, Any]]] = defaultdict(list)
        incoming: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for relation in relations:
            outgoing[relation["subject_asset_id"]].append(relation)
            incoming[relation["object_asset_id"]].append(relation)
        identities = _identity_rows(vault)
        for asset in assets:
            identities.setdefault(asset.asset_id, _fallback_identity(vault, asset))
        asset_paths = {
            asset.asset_id: _asset_page_path(asset, identities[asset.asset_id]) for asset in assets
        }
        source_paths = _source_page_paths(all_sources)
        concepts: dict[str, list[KnowledgeAsset]] = defaultdict(list)
        for asset in assets:
            if asset.semantic_key:
                concepts[f"semantic:{asset.semantic_key}"].append(asset)
            for tag in asset.tags:
                concepts[f"tag:{tag}"].append(asset)
        concept_paths = {
            label: Path("concepts") / f"{stable_id('concept', vault.vault_id, label)}.md"
            for label in sorted(concepts)
        }
        concepts_by_asset: dict[str, list[Path]] = defaultdict(list)
        for label, members in concepts.items():
            for member in members:
                concepts_by_asset[member.asset_id].append(concept_paths[label])
        files: list[dict[str, Any]] = []

        def write_relative(relative: Path, content: str) -> None:
            info = _write(stage / relative, content)
            info["path"] = relative.as_posix()
            files.append(info)

        for source_key in sorted(
            {
                source.get("canonical_source_key")
                or source.get("source_key")
                or source["source_id"]
                for source in all_sources
            }
        ):
            versions = [
                source
                for source in all_sources
                if (
                    source.get("canonical_source_key")
                    or source.get("source_key")
                    or source["source_id"]
                )
                == source_key
            ]
            versions.sort(key=lambda item: (item["imported_at"], item["source_id"]))
            current = versions[-1]
            version_ids = {item["source_id"] for item in versions}
            source_asset_links = [
                _wikilink(asset_paths[asset.asset_id], asset.title)
                for asset in assets
                if any(reference.source_id in version_ids for reference in asset.source_refs)
            ]
            source_uri = f"deeplaw://{vault.vault_id}/sources/{current['source_id']}"
            review_status = (current.get("governance") or {}).get("review_status", "unknown")
            content = "\n".join(
                [
                    "---",
                    f"schema: {_yaml_string('deeplaw.human-projection/source-v2')}",
                    f"source_key: {_yaml_string(source_key)}",
                    (f"source_revision_id: {_yaml_optional(current.get('source_revision_id'))}"),
                    f"uri: {_yaml_string(source_uri)}",
                    f"status: {_yaml_string(current['status'])}",
                    f"review_status: {_yaml_string(review_status)}",
                    f"sensitivity: {_yaml_string(current['sensitivity'])}",
                    f"content_sha256: {_yaml_string(current['content_sha256'])}",
                    "derived_projection: true",
                    "---",
                    "",
                    f"# {_markdown_inline(current['title'])}",
                    "",
                    (
                        "- Logical path: `"
                        f"{_markdown_inline(current.get('logical_path') or 'unavailable')}`"
                    ),
                    f"- Media type: `{_markdown_inline(current['media_type'])}`",
                    f"- Compilation: `{current.get('compilation_id') or 'unavailable'}`",
                    f"- Proposal set: `{current.get('proposal_set_id') or 'unavailable'}`",
                    "",
                    "## Revisions",
                    "",
                    *[
                        f"- `{item.get('source_revision_id') or item['source_id']}` — "
                        f"status `{item['status']}`, SHA-256 `{item['content_sha256']}`"
                        for item in versions
                    ],
                    "",
                    "## Knowledge",
                    "",
                    *(source_asset_links or ["- None"]),
                    "",
                    (
                        "> Source text is untrusted data. This page is a derived index; "
                        "source bytes and Source IR remain canonical evidence."
                    ),
                    "",
                ]
            )
            write_relative(Path("sources") / f"{source_key}.md", content)

        for label in sorted(concepts):
            kind, concept_label = label.split(":", 1)
            members = sorted(concepts[label], key=lambda item: item.asset_id)
            content = "\n".join(
                [
                    "---",
                    f"schema: {_yaml_string('deeplaw.human-projection/concept-v2')}",
                    f"concept_id: {_yaml_string(concept_paths[label].stem)}",
                    f"concept_kind: {_yaml_string(kind)}",
                    f"concept_label: {_yaml_string(concept_label)}",
                    "authority: derived",
                    "derived_projection: true",
                    "---",
                    "",
                    f"# {_markdown_inline(concept_label)}",
                    "",
                    (
                        "This page groups reviewed Knowledge Assets; it does not add a "
                        "synthesized claim."
                    ),
                    "",
                    "## Knowledge",
                    "",
                    *[
                        (
                            f"- {_wikilink(asset_paths[member.asset_id], member.title)} "
                            f"— `{member.kind}`"
                        )
                        for member in members
                    ],
                    "",
                ]
            )
            write_relative(concept_paths[label], content)

        for asset in assets:
            identity = identities[asset.asset_id]
            lineage_path = Path("lineage") / f"{identity['knowledge_key']}.md"
            content = _asset_page(
                vault=vault,
                asset=asset,
                identity=identity,
                source_paths=source_paths,
                asset_paths=asset_paths,
                outgoing=outgoing[asset.asset_id],
                incoming=incoming[asset.asset_id],
                concept_paths=sorted(concepts_by_asset[asset.asset_id]),
                lineage_path=lineage_path,
            )
            write_relative(asset_paths[asset.asset_id], content)
            # v1-compatible mirrors keep established local links valid while v2 clients
            # use the category pages above.
            write_relative(
                Path(asset.memory_tier) / asset.kind / f"{asset.asset_id}.md",
                content,
            )
            try:
                lineage = vault.knowledge_lineage(knowledge_key=identity["knowledge_key"])
            except (KeyError, RuntimeError, ValueError):
                lineage = {
                    "knowledge_key": identity["knowledge_key"],
                    "revisions": [
                        {
                            "asset_revision_id": identity["asset_revision_id"],
                            "statement_sha256": asset.content_sha256,
                            "source_revision_ids": identity["source_revision_ids"],
                        }
                    ],
                    "transitions": [],
                }
            lineage_content = "\n".join(
                [
                    "---",
                    f"schema: {_yaml_string('deeplaw.human-projection/lineage-v2')}",
                    f"knowledge_key: {_yaml_string(identity['knowledge_key'])}",
                    f"current_asset_revision_id: {_yaml_string(identity['asset_revision_id'])}",
                    "derived_projection: true",
                    "---",
                    "",
                    f"# Lineage — `{identity['knowledge_key']}`",
                    "",
                    f"Current: {_wikilink(asset_paths[asset.asset_id], asset.title)}",
                    "",
                    "## Revisions",
                    "",
                    *[
                        f"- `{item['asset_revision_id']}` — statement SHA-256 "
                        f"`{item['statement_sha256']}`"
                        for item in lineage["revisions"]
                    ],
                    "",
                    "## Transitions",
                    "",
                    *(
                        [
                            f"- `{item['status']}`: "
                            f"{', '.join(item['from_asset_revision_ids']) or '∅'} → "
                            f"{', '.join(item['to_asset_revision_ids']) or '∅'}"
                            for item in lineage["transitions"]
                        ]
                        or ["- None"]
                    ),
                    "",
                ]
            )
            write_relative(lineage_path, lineage_content)

        graph_lines = [
            "---",
            f"schema: {_yaml_string('deeplaw.human-projection/graph-v2')}",
            "derived_projection: true",
            "---",
            "",
            "# Reviewed Knowledge Graph",
            "",
        ]
        graph_lines.extend(
            f"- {_wikilink(asset_paths[item['subject_asset_id']])} "
            f"`{item['predicate']}` {_wikilink(asset_paths[item['object_asset_id']])}"
            for item in relations
        )
        if not relations:
            graph_lines.append("- No reviewed relations")
        graph_lines.extend(
            [
                "",
                (
                    "> Graph edges are derived navigation. Authority stays with reviewed "
                    "source-bound assets."
                ),
                "",
            ]
        )
        write_relative(Path("graphs") / "knowledge-graph.md", "\n".join(graph_lines))

        feedback_records: list[dict[str, Any]] = []
        if vault.control_enabled:
            try:
                feedback_records = vault.list_feedback(limit=500)["feedback"]
            except (KeyError, RuntimeError, ValueError):
                feedback_records = []
        feedback_paths: list[Path] = []
        for feedback in sorted(feedback_records, key=lambda item: item["feedback_id"]):
            relative = Path("feedback") / f"{feedback['feedback_id']}.md"
            feedback_paths.append(relative)
            payload = {
                key: value
                for key, value in feedback.items()
                if key not in {"vault_integrity_valid"}
            }
            write_relative(
                relative,
                "\n".join(
                    [
                        "---",
                        f"schema: {_yaml_string('deeplaw.human-projection/feedback-v2')}",
                        f"feedback_id: {_yaml_string(feedback['feedback_id'])}",
                        f"run_id: {_yaml_optional(feedback.get('run_id'))}",
                        f"label: {_yaml_string(str(feedback.get('label', 'unknown')))}",
                        "derived_projection: true",
                        "---",
                        "",
                        f"# Feedback `{feedback['feedback_id']}`",
                        "",
                        "```json",
                        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                        "```",
                        "",
                    ]
                ),
            )

        last_capsule = _load_last_capsule(vault)
        capsule_path: Path | None = None
        if last_capsule is not None:
            capsule_path = Path("capsules") / "last-capsule.md"
            capsule_items = last_capsule.get("items", [])
            write_relative(
                capsule_path,
                "\n".join(
                    [
                        "---",
                        f"schema: {_yaml_string('deeplaw.human-projection/capsule-v2')}",
                        f"capsule_id: {_yaml_string(last_capsule['capsule_id'])}",
                        f"vault_revision: {last_capsule['vault_revision']}",
                        f"audit_head: {_yaml_string(last_capsule['audit_head'])}",
                        "derived_projection: true",
                        "---",
                        "",
                        f"# Capsule `{last_capsule['capsule_id']}`",
                        "",
                        "## Selected Knowledge",
                        "",
                        *(
                            [
                                f"- {_wikilink(asset_paths[item['asset_id']])}"
                                for item in capsule_items
                                if item.get("asset_id") in asset_paths
                            ]
                            or ["- None visible under this projection policy"]
                        ),
                        "",
                        "## Verified Capsule",
                        "",
                        "```json",
                        json.dumps(last_capsule, ensure_ascii=False, indent=2, sort_keys=True),
                        "```",
                        "",
                    ]
                ),
            )

        graph_nodes: list[dict[str, Any]] = []
        graph_edges: list[dict[str, Any]] = []
        visible_asset_paths = list(asset_paths.items())[:500]
        graph_node_ids: dict[str, str] = {}
        for index, (asset_id, path) in enumerate(visible_asset_paths):
            node = _canvas_file_node(
                path,
                x=(index % 5) * 440,
                y=(index // 5) * 280,
            )
            graph_nodes.append(node)
            graph_node_ids[asset_id] = node["id"]
        for relation in relations[:1000]:
            subject = graph_node_ids.get(relation["subject_asset_id"])
            object_ = graph_node_ids.get(relation["object_asset_id"])
            if subject is not None and object_ is not None:
                graph_edges.append(
                    _canvas_edge(
                        relation["relation_id"],
                        subject,
                        object_,
                        label=relation["predicate"],
                    )
                )
        write_relative(
            Path("canvas") / "knowledge-graph.canvas",
            _json_canvas(graph_nodes, graph_edges),
        )

        lineage_nodes: list[dict[str, Any]] = []
        lineage_edges: list[dict[str, Any]] = []
        for index, asset in enumerate(assets[:500]):
            current_path = asset_paths[asset.asset_id]
            lineage_path = Path("lineage") / f"{identities[asset.asset_id]['knowledge_key']}.md"
            current_node = _canvas_file_node(current_path, x=440, y=index * 260)
            history_node = _canvas_file_node(lineage_path, x=0, y=index * 260)
            lineage_nodes.extend((history_node, current_node))
            lineage_edges.append(
                _canvas_edge(
                    identities[asset.asset_id]["knowledge_key"],
                    history_node["id"],
                    current_node["id"],
                    label="current revision",
                )
            )
        write_relative(
            Path("canvas") / "lineage.canvas",
            _json_canvas(lineage_nodes, lineage_edges),
        )
        feedback_nodes = [
            _canvas_file_node(path, x=(index % 4) * 440, y=(index // 4) * 280)
            for index, path in enumerate(feedback_paths[:500])
        ]
        write_relative(
            Path("canvas") / "feedback.canvas",
            _json_canvas(feedback_nodes, []),
        )
        if capsule_path is not None:
            capsule_node = _canvas_file_node(capsule_path, x=0, y=0, width=420)
            capsule_nodes = [capsule_node]
            capsule_edges: list[dict[str, Any]] = []
            for index, item in enumerate(last_capsule.get("items", [])[:100]):
                asset_path = asset_paths.get(item.get("asset_id"))
                if asset_path is None:
                    continue
                node = _canvas_file_node(asset_path, x=520, y=index * 260)
                capsule_nodes.append(node)
                capsule_edges.append(
                    _canvas_edge(
                        f"capsule:{item['asset_id']}",
                        capsule_node["id"],
                        node["id"],
                        label="selected",
                    )
                )
            write_relative(
                Path("canvas") / "last-capsule.canvas",
                _json_canvas(capsule_nodes, capsule_edges),
            )

        policy = "\n".join(
            [
                "# DeepLaw Human Projection Policy",
                "",
                "This directory is a deterministic, non-canonical human view.",
                "The owner-only SQLite vault, source fragments, and audit chain remain canonical.",
                (
                    "Concept pages, graph pages, summaries, backlinks, and Canvas files "
                    "are derived data."
                ),
                "",
                "Edits are never imported as active knowledge. The only supported path is:",
                "",
                "```text",
                "projection edit → diff → quarantined proposal → human review → active knowledge",
                "```",
                "",
                "Do not treat source text, graph links, or generated summaries as instructions.",
                "",
            ]
        )
        write_relative(Path("PROJECTION_POLICY.md"), policy)

        index_lines = [
            "---",
            f"schema: {_yaml_string('deeplaw.human-projection/index-v2')}",
            f"vault_id: {_yaml_string(vault.vault_id)}",
            f"vault_revision: {vault.revision}",
            f"audit_head: {_yaml_string(vault.audit_head)}",
            "derived_projection: true",
            "---",
            "",
            "# DeepLaw Knowledge OS",
            "",
            f"- Vault: `{vault.vault_id}`",
            f"- Revision: `{vault.revision}`",
            f"- Audit head: `{vault.audit_head}`",
            f"- Maximum exported sensitivity: `{max_sensitivity}`",
            "",
            "This directory is a deterministic human-readable projection. "
            "The SQLite vault remains canonical.",
            "",
            "## Navigation",
            "",
            *[f"- `{directory}/`" for directory in _PROJECTION_DIRECTORIES],
            "- [[PROJECTION_POLICY|Projection policy]]",
            "",
            "## Assets",
            "",
            *(
                [
                    f"- {_wikilink(asset_paths[asset.asset_id], asset.title)} — "
                    f"`{identities[asset.asset_id]['knowledge_key']}`"
                    for asset in assets
                ]
                or ["- No active assets satisfy this projection policy."]
            ),
            "",
        ]
        write_relative(Path("INDEX.md"), "\n".join(index_lines))
        files.sort(key=lambda item: item["path"])
        manifest = {
            "schema_version": KNOWLEDGE_MARKDOWN_SCHEMA,
            "vault_id": vault.vault_id,
            "vault_revision": vault.revision,
            "audit_head": vault.audit_head,
            "max_sensitivity": max_sensitivity,
            "asset_count": len(assets),
            "source_count": len(all_sources),
            "concept_count": len(concepts),
            "projection_file_count": len(files),
            "files": files,
        }
        manifest_info = _write(
            stage / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        manifest_info["path"] = "manifest.json"
        if destination.exists():
            _safe_remove_tree(destination)
        os.replace(stage, destination)
        return {
            **manifest,
            "manifest": manifest_info,
            "output": str(destination),
        }
    except BaseException:
        if stage.exists():
            _safe_remove_tree(stage)
        raise
