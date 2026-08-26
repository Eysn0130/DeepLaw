from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from jsonschema import Draft202012Validator

from benchmarks.release import release_policy

REPOSITORY = Path(__file__).resolve().parents[1]
CLASSIFICATION = REPOSITORY / "benchmarks/release/v013-gate-classification-v9.json"
CLASSIFICATION_SCHEMA = (
    REPOSITORY / "contracts/v013-release-gate-classification.v9.schema.json"
)


def _load(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_active_classification_makes_both_declared_hosts_core() -> None:
    assert release_policy.V013_ACTIVE_CLASSIFICATION_PATH == CLASSIFICATION
    assert release_policy.V013_ACTIVE_CLASSIFICATION_SCHEMA_PATH == CLASSIFICATION_SCHEMA
    assert "opencode" in release_policy.V013_CORE_GATE_IDS
    assert "opencode" not in release_policy.V013_CAPABILITY_GATE_IDS

    schema = _load(CLASSIFICATION_SCHEMA)
    classification = _load(CLASSIFICATION)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(classification)
    gates = {item["gate_id"]: item for item in classification["gates"]}  # type: ignore[index]
    for gate_id in ("codex", "opencode"):
        assert gates[gate_id]["category"] == "Core"
        assert gates[gate_id]["required"] is True
        assert gates[gate_id]["not_claimed_only"] is False
    assert {item["gate_id"] for item in classification["gates"]} >= {  # type: ignore[index]
        "comparative_incremental_benefit",
        "superiority",
        "sota",
    }
    assert "parity" not in {item["gate_id"] for item in classification["gates"]}  # type: ignore[index]


def test_active_host_gates_use_the_shared_current_continuity_contract() -> None:
    classification = _load(CLASSIFICATION)
    gates = {item["gate_id"]: item for item in classification["gates"]}  # type: ignore[index]
    for gate_id in ("codex", "opencode"):
        assert gates[gate_id]["accepted_input_schema_versions"] == [
            "deeplaw.typed-qualification-evidence/v3"
        ]
        assert gates[gate_id]["minimum_distinct_run_count"] == 3
        assert gates[gate_id]["required_unique_dimensions"] == [
            "run_id",
            "host",
            "model",
            "platform",
            "task_case",
        ]
    assert gates["codex"]["constraints"] == {
        "host": "codex",
        "tool_version": None,
        "binary_sha256": None,
        "source_commit": None,
        "config_selector": None,
        "model_id": "gpt-5.6-luna",
        "expected_response_model_id": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "argv_prefix": ["codex", "app-server", "--stdio"],
    }
    assert gates["opencode"]["constraints"]["tool_version"] is None
    assert gates["opencode"]["constraints"]["binary_sha256"] is None
    assert gates["opencode"]["constraints"]["source_commit"] is None


def test_living_wiki_core_is_not_bundled_with_optional_graph_analytics() -> None:
    manifest = _load(REPOSITORY / "governance/product-surface-manifest.v1.json")
    surfaces = {item["surface_id"]: item for item in manifest["surfaces"]}  # type: ignore[index]
    living_wiki = surfaces["default.living_wiki"]
    assert living_wiki["product_role"] == "Core"
    assert living_wiki["lifecycle"] == "Active"
    assert {
        "deeplaw knowledge compile",
        "deeplaw knowledge reconcile",
        "deeplaw knowledge wiki page/backlinks/outlinks/browse-kind/recent",
    } <= set(living_wiki["bindings"])
    assert "knowledge_support operation=wiki" not in living_wiki["bindings"]
    assert surfaces["advanced.wiki_graph_analytics"]["bindings"] == [
        "deeplaw knowledge wiki local-graph",
        "Living Wiki Canvas",
        "community projection",
        "centrality projection",
        "visual graph analytics",
    ]
    assert "experimental.real_hosts" not in surfaces


def test_readme_en_describes_a_source_candidate_not_a_stable_core() -> None:
    readme = (REPOSITORY / "README_EN.md").read_text(encoding="utf-8")
    assert "stable CLI/MCP/Python core" not in readme
    assert "Public package/main: `0.12.0 Beta`" in readme
    assert "`release_ready=false`" in readme
    assert "Local regressions, mocks, dry-runs, old reports" in readme


def test_prd_133_uses_current_status_sources_and_freezes_kernel_scope() -> None:
    prd = (REPOSITORY / "docs/PRODUCT_REQUIREMENTS.md").read_text(encoding="utf-8")
    prose = " ".join(prd.split())
    assert "PRD revision: **1.3.3**" in prd
    assert "Reviewed: **2026-08-21**" in prd
    assert "DeepLaw Kernel distribution" in prd
    assert "A first-party GUI is not included in the v0.13 release scope" in prose
    assert "minimum ecosystem capability parity" in prd
    assert "active qualification record, current Gate classification" in prose
    assert "Historical Pass dispositions are immutable evidence snapshots" in prd
    assert "latest committed pass-specific disposition" not in prd


def test_historical_frozen_contract_bytes_are_unchanged() -> None:
    expected = {
        "benchmarks/release/v013-gate-classification-v2.json": (
            "4efbb8096f0fc57fbb8cc1ffe76e794e3bc6022b0969d1d980dfc80c112a90e2"
        ),
        "contracts/v013-release-gate-classification.v2.schema.json": (
            "050ab23c714e65e8ffd0121de975c012e1ea4ff148f294c47f77f900c0c67ef9"
        ),
        "benchmarks/v013/qualification-protocol-v1.json": (
            "95283e2d1fdd60a429941c6ab718cebd739ad414ddc38d58b3f2fcc14f4cffb5"
        ),
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((REPOSITORY / relative).read_bytes()).hexdigest() == digest
    assert (
        REPOSITORY / "benchmarks/v013/qualification-protocol-v1.sha256"
    ).read_text(encoding="utf-8") == (
        "95283e2d1fdd60a429941c6ab718cebd739ad414ddc38d58b3f2fcc14f4cffb5  "
        "qualification-protocol-v1.json\n"
    )


def test_frozen_behavior_map_claim_boundary_and_candidate_status_are_explicit() -> None:
    protocol = (REPOSITORY / "docs/V0_13_QUALIFICATION_PROTOCOL.md").read_text(
        encoding="utf-8"
    )
    traceability = (REPOSITORY / "docs/PRD_TRACEABILITY_MATRIX.md").read_text(
        encoding="utf-8"
    )
    research = (REPOSITORY / "docs/V0_13_UPSTREAM_RESEARCH.md").read_text(
        encoding="utf-8"
    )
    reuse = (REPOSITORY / "docs/UPSTREAM_REUSE.md").read_text(encoding="utf-8")
    notices = (REPOSITORY / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    protocol_prose = " ".join(protocol.split())
    for required in (
        "f078160e248f889d66ee37dc0d431854f50d3294c",
        "367a91416477c90bbfae766dc06add3de6ae75a7",
        "cc1744324150c632416857c98964f87b1574a5fc",
        "350eec8a284e159b2e4cfd068d808cbf203a6cc5",
        "Markdown/Wikilink",
        "wrong merge",
        "backlinks/outlinks",
        "Continuity",
        "Living Wiki",
        "Professional Evidence",
        "First Correct Action",
        "Decision Preservation",
        "Wrong-State Admission",
        "actual Provider bytes/tokens",
    ):
        assert required in protocol_prose
    assert "OpenWiki / `f078160e248f889d66ee37dc0d431854f50d3294c`" in protocol
    for reviewed in (research, reuse, notices):
        assert "7531d615216e8cbccf464f66cfbbae3668871c84" in reviewed
        assert "package-version-0.3.1 review snapshot" in reviewed
        assert "7531d615216e8cbccf464f66cfbbae3668871c84` (`v0.3.1`)" not in reviewed
    assert (
        "DeepLaw meets the frozen v0.13 Kernel compatibility baseline defined by the "
        "qualification protocol."
    ) in " ".join(protocol.split())
    assert "Active gate classification: `v9`" in traceability
    assert "Active qualification profile: `kernel_release_core`" in traceability
    assert "public-seam, source-free development closure runner" in traceability
    assert "knowledge-support input v7/output v6" in traceability
    assert "Caller-authored PASS values" in traceability

    pyproject = tomllib.loads((REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = _load(REPOSITORY / "governance/product-surface-manifest.v1.json")
    assert pyproject["project"]["version"] in {"0.12.0", "0.13.0"}
    assert manifest["package_version"] == pyproject["project"]["version"]
    assert manifest["lifecycle_status"] == "source_candidate"
    assert manifest["release_ready"] is False
