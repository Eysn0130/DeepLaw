from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def _python_files(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        candidates = [root] if root.is_file() else root.rglob("*.py")
        files.extend(path for path in candidates if "__pycache__" not in path.parts)
    return sorted(files)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(("." * node.level) + (node.module or ""))
    return imports


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_host_adapters_do_not_import_ledger_or_compilation_storage_internals() -> None:
    forbidden = {
        "deeplaw.knowledge_autonomy",
        "deeplaw.knowledge_store",
        "deeplaw.compilation.store",
        "deeplaw.store",
    }
    for path in _python_files(REPOSITORY / "adapters"):
        assert not (_imports(path) & forbidden), path


def test_projection_has_no_model_network_or_subprocess_dependency() -> None:
    forbidden_roots = {
        "anthropic",
        "openai",
        "requests",
        "httpx",
        "urllib.request",
        "subprocess",
    }
    for path in _python_files(REPOSITORY / "src/deeplaw/projection"):
        imported = _imports(path)
        assert not {
            name
            for name in imported
            if name in forbidden_roots
            or any(name.startswith(root + ".") for root in forbidden_roots)
        }, path


def test_retrieval_modules_contain_no_persistent_sql_mutation() -> None:
    roots = (
        REPOSITORY / "src/deeplaw/retrieval",
        REPOSITORY / "src/deeplaw/retrieval_fabric.py",
        REPOSITORY / "src/deeplaw/retrieval_profiles.py",
    )
    mutation_tokens = (
        "insert into ",
        "update ",
        "delete from ",
        "drop table ",
        "alter table ",
        "begin immediate",
    )
    for path in _python_files(*roots):
        literals = "\n".join(_string_literals(path)).casefold()
        assert not any(token in literals for token in mutation_tokens), path
        compact = path.read_text(encoding="utf-8").replace(" ", "").casefold()
        assert "read_only=false" not in compact


def test_read_mcp_processes_have_no_write_calls_or_cross_plane_mutation_imports() -> None:
    for name in ("knowledge_mcp_server.py", "mcp_server.py"):
        path = REPOSITORY / "src/deeplaw" / name
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        called_attributes = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert called_attributes.isdisjoint(
            {
                "enable_grant",
                "remember",
                "upsert_concept",
                "add_relation",
                "commit",
                "stage",
                "abort",
                "remove_source",
            }
        ), path
        assert "knowledge_sink_mcp_server" not in _imports(path), path


def test_editor_adapters_have_no_direct_sqlite_or_canonical_commit_path() -> None:
    adapter_roots = (REPOSITORY / "adapters/obsidian", REPOSITORY / "adapters/tolaria")
    forbidden_imports = {
        "sqlite3",
        "deeplaw.knowledge_autonomy",
        "deeplaw.knowledge_store",
        "deeplaw.compilation.store",
    }
    for path in _python_files(*adapter_roots):
        assert not (_imports(path) & forbidden_imports), path
    for root in adapter_roots:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".js", ".mjs"}:
                continue
            folded = path.read_text(encoding="utf-8").casefold()
            assert "insert into knowledge_" not in folded, path
            assert "update knowledge_" not in folded, path
            assert "deeplaw.sqlite3" not in folded, path
