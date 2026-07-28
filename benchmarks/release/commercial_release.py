from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from benchmarks.release.evidence import (
    environment_manifest,
    file_record,
    load_json,
    repository_binding,
    verify_record_digest,
    write_report,
)

SCHEMA_VERSION = "deeplaw.commercial-release-manifest/v1"
VERSION = "0.7.0"
TAG = "v0.7.0"
COMPETITIVE_EVIDENCE_MISSING = [
    "real_model_task_e2e",
    "named_baseline_results_17",
    "secret_held_out_results",
    "independent_evaluator_signatures",
]


class CommercialReleaseError(RuntimeError):
    pass


def _same_binding(expected: dict[str, Any], observed: dict[str, Any]) -> bool:
    return all(
        expected.get(field) == observed.get(field)
        for field in ("commit", "tree", "package_version", "lock_sha256", "pyproject_sha256")
    ) and expected.get("contracts", {}).get("inventory_sha256") == observed.get(
        "contracts", {}
    ).get("inventory_sha256")


def _require_report(
    path: Path,
    *,
    schema_version: str,
    binding: dict[str, Any],
    field: str,
) -> dict[str, Any]:
    report = load_json(path)
    verify_record_digest(report, field=field)
    if report.get("schema_version") != schema_version:
        raise CommercialReleaseError(f"{field} schema is unsupported")
    if not _same_binding(binding, report.get("binding", {})):
        raise CommercialReleaseError(f"{field} targets a different release commit")
    if report.get("passed") is not True:
        raise CommercialReleaseError(f"{field} did not pass")
    return report


def _unified_versions(repository: Path) -> dict[str, str]:
    project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    values = {
        "package": project["project"]["version"],
        "python": __import__("deeplaw").__version__,
        "claude_marketplace": load_json(repository / ".claude-plugin/marketplace.json")[
            "version"
        ],
        "claude_legal": load_json(
            repository / "plugins/deeplaw/.claude-plugin/plugin.json"
        )["version"],
        "claude_knowledge": load_json(
            repository / "plugins/deeplaw-knowledge-os/.claude-plugin/plugin.json"
        )["version"],
        "codex_legal": load_json(repository / "plugins/deeplaw/.codex-plugin/plugin.json")[
            "version"
        ],
        "codex_knowledge": load_json(
            repository / "plugins/deeplaw-knowledge-os/.codex-plugin/plugin.json"
        )["version"],
        "opencode_adapter": load_json(repository / "adapters/opencode/manifest.json")[
            "version"
        ],
    }
    if set(values.values()) != {VERSION}:
        raise CommercialReleaseError(f"release versions are not unified: {values}")
    marketplace = load_json(repository / ".claude-plugin/marketplace.json")
    if {item.get("version") for item in marketplace.get("plugins", [])} != {VERSION}:
        raise CommercialReleaseError("Claude marketplace entries are not version 0.7.0")
    return values


def _artifact_inventory(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise CommercialReleaseError("release assets contain a symbolic link")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == "commercial-release-manifest.json":
            continue
        records.append(file_record(path, logical_name=relative))
        records[-1]["path"] = records[-1].pop("logical_name")
    if len(records) < 18:
        raise CommercialReleaseError("release asset inventory is incomplete")
    return records


def _sbom(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("bomFormat") != "CycloneDX" or payload.get("specVersion") not in {
        "1.5",
        "1.6",
    }:
        raise CommercialReleaseError("release SBOM is not CycloneDX 1.5/1.6")
    components = payload.get("components")
    metadata_component = payload.get("metadata", {}).get("component")
    candidates = [metadata_component, *(components if isinstance(components, list) else [])]
    if not any(
        isinstance(item, dict)
        and item.get("name") == "deeplaw"
        and item.get("version") == VERSION
        for item in candidates
    ):
        raise CommercialReleaseError("release SBOM does not bind deeplaw 0.7.0")
    return {
        "format": "CycloneDX",
        "spec_version": payload["specVersion"],
        "component_count": len(components) if isinstance(components, list) else 0,
    }


def _licenses(path: Path, *, binding: dict[str, Any]) -> dict[str, Any]:
    payload = load_json(path)
    verify_record_digest(payload, field="installed license inventory")
    if (
        payload.get("schema_version") != "deeplaw.installed-license-inventory/v1"
        or payload.get("status") != "passed"
        or payload.get("blocked") != []
        or payload.get("review_required") != []
        or not _same_binding(binding, payload.get("binding", {}))
    ):
        raise CommercialReleaseError("installed license inventory did not pass")
    return {"status": "passed", "package_count": payload.get("package_count")}


def _openvex(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    statements = payload.get("statements")
    if not isinstance(statements, list) or not statements:
        raise CommercialReleaseError("OpenVEX has no statements")
    expected = f"pkg:pypi/deeplaw@{VERSION}"
    for statement in statements:
        products = statement.get("products") if isinstance(statement, dict) else None
        if not isinstance(products, list) or expected not in {
            item.get("@id") for item in products if isinstance(item, dict)
        }:
            raise CommercialReleaseError("OpenVEX statement is not bound to deeplaw 0.7.0")
    return {"statement_count": len(statements), "product": expected}


def _docs(repository: Path) -> dict[str, bool]:
    required = {
        "README.md": ("本地单用户 Agent Knowledge OS", "Knowledge Capsule", "v0.7.0"),
        "README_EN.md": (
            "Local single-user Agent Knowledge OS",
            "Knowledge Capsule",
            "v0.7.0",
        ),
        "CHANGELOG.md": ("0.7.0", "competitive_claim_eligible=false"),
        "SECURITY.md": ("v0.7.0", "commercial_release_eligible=true"),
        "docs/INSTALL_UPGRADE_ROLLBACK.md": ("0.6.0", "0.7.0"),
        "docs/V0_7_ACCEPTANCE_MATRIX.md": (
            "commercial_release_eligible=true",
            "competitive_claim_eligible=false",
        ),
        "docs/RELEASE_NOTES_v0.7.0.md": (
            "commercial_release_eligible=true",
            "competitive_claim_eligible=false",
        ),
    }
    result: dict[str, bool] = {}
    for relative, markers in required.items():
        text = (repository / relative).read_text(encoding="utf-8")
        if any(marker not in text for marker in markers):
            raise CommercialReleaseError(f"release documentation is incomplete: {relative}")
        if relative in {"README.md", "README_EN.md"} and (
            "商业" in text or "commercial" in text.casefold()
        ):
            raise CommercialReleaseError(
                f"public repository homepage contains release-positioning copy: {relative}"
            )
        result[relative] = True
    return result


def assemble(
    repository: Path,
    *,
    assets_root: Path,
    platform_paths: list[Path],
    host_path: Path,
    reproducible_path: Path,
    oci_report_path: Path,
    audit_paths: list[Path],
    sbom_path: Path,
    licenses_path: Path,
    openvex_path: Path,
    source_date_epoch: int,
) -> dict[str, Any]:
    binding = repository_binding(repository)
    if binding["package_version"] != VERSION or not binding["worktree_clean"]:
        raise CommercialReleaseError("commercial manifest requires a clean 0.7.0 commit")
    versions = _unified_versions(repository)
    platform_reports = [
        _require_report(
            path,
            schema_version="deeplaw.platform-release-gate/v1",
            binding=binding,
            field=f"platform report {path.name}",
        )
        for path in platform_paths
    ]
    systems = sorted(report["environment"]["platform_system"] for report in platform_reports)
    if systems != ["Darwin", "Linux", "Windows"]:
        raise CommercialReleaseError(f"platform reports are incomplete: {systems}")
    wheel_hashes = {
        report["distribution_lifecycle"]["wheel_sha256"] for report in platform_reports
    }
    sdist_hashes = {
        report["distribution_lifecycle"]["sdist_sha256"] for report in platform_reports
    }
    if len(wheel_hashes) != 1 or len(sdist_hashes) != 1:
        raise CommercialReleaseError("operating systems did not install identical distributions")

    host = _require_report(
        host_path,
        schema_version="deeplaw.no-model-host-acceptance/v1",
        binding=binding,
        field="no-model host acceptance",
    )
    if (
        host.get("model_task_acceptance") is not False
        or host.get("model_task_results_claimed") is not False
        or host.get("isolation", {}).get("model_or_api_call_attempted") is not False
        or set(host.get("hosts", {})) != {"codex", "claude_code", "opencode"}
    ):
        raise CommercialReleaseError("host acceptance scope is overstated or incomplete")

    reproducible = load_json(reproducible_path)
    verify_record_digest(reproducible, field="reproducible distribution report")
    if (
        reproducible.get("schema_version") != "deeplaw.reproducible-build-report/v2"
        or not _same_binding(binding, reproducible.get("binding", {}))
        or reproducible.get("repository_commit") != binding["commit"]
        or reproducible.get("lock_sha256") != binding["lock_sha256"]
        or reproducible.get("working_tree_dirty") is not False
        or reproducible.get("reproducible") is not True
        or reproducible.get("artifact_release_eligible") is not True
        or reproducible.get("artifact_release_blockers") != []
    ):
        raise CommercialReleaseError("reproducible distribution report did not pass")
    distribution_hashes = {item["sha256"] for item in reproducible.get("artifacts", [])}
    if distribution_hashes != wheel_hashes | sdist_hashes:
        raise CommercialReleaseError("platform artifacts differ from reproducible build bytes")

    oci = _require_report(
        oci_report_path,
        schema_version="deeplaw.oci-release-report/v1",
        binding=binding,
        field="OCI release report",
    )
    if not all(oci.get("gates", {}).values()):
        raise CommercialReleaseError("OCI gate is incomplete")

    expected_profiles = {"default", "build", "discovery", "document-engine"}
    audit_reports = [load_json(path) for path in audit_paths]
    for report in audit_reports:
        verify_record_digest(report, field="dependency audit")
        if (
            report.get("schema_version") != "deeplaw.dependency-audit/v1"
            or report.get("status") != "passed"
            or not _same_binding(binding, report.get("binding", {}))
        ):
            raise CommercialReleaseError("dependency audit report did not pass")
    profiles = {report.get("profile") for report in audit_reports}
    if profiles != expected_profiles:
        raise CommercialReleaseError(f"dependency audit profiles are incomplete: {profiles}")

    sbom = _sbom(sbom_path)
    licenses = _licenses(licenses_path, binding=binding)
    openvex = _openvex(openvex_path)
    docs = _docs(repository)
    artifacts = _artifact_inventory(assets_root)
    artifact_by_path = {item["path"]: item for item in artifacts}
    expected_dist = {
        f"dist/{item['name']}": item["sha256"] for item in reproducible["artifacts"]
    }
    for relative, digest in expected_dist.items():
        if artifact_by_path.get(relative, {}).get("sha256") != digest:
            raise CommercialReleaseError(f"verified distribution bytes are absent: {relative}")
    if artifact_by_path.get("oci/deeplaw-0.7.0-linux-amd64.oci.tar", {}).get(
        "sha256"
    ) != oci["oci_archive"]["sha256"]:
        raise CommercialReleaseError("verified OCI bytes are absent from release assets")

    mandatory_tests = sum(report["mandatory_suite"]["tests"] for report in platform_reports)
    mandatory_skips = sum(report["mandatory_suite"]["skipped"] for report in platform_reports)
    if mandatory_tests < 1740 or mandatory_skips != 0:
        raise CommercialReleaseError("mandatory test total is incomplete or includes skips")
    return {
        "schema_version": SCHEMA_VERSION,
        "environment": environment_manifest(),
        "release": {
            "repository": "Eysn0130/DeepLaw",
            "version": VERSION,
            "tag": TAG,
            "commit": binding["commit"],
            "tree": binding["tree"],
            "source_date_epoch": source_date_epoch,
        },
        "bindings": {
            "lock_sha256": binding["lock_sha256"],
            "pyproject_sha256": binding["pyproject_sha256"],
            "contracts_inventory_sha256": binding["contracts"]["inventory_sha256"],
            "contracts_count": binding["contracts"]["count"],
            "versions": versions,
        },
        "artifacts": artifacts,
        "platform_gates": {
            "systems": systems,
            "mandatory_tests": mandatory_tests,
            "mandatory_skips": mandatory_skips,
            "windows_native_acl_junction_reparse": True,
            "identical_wheel_sha256": next(iter(wheel_hashes)),
            "identical_sdist_sha256": next(iter(sdist_hashes)),
            "passed": True,
        },
        "host_acceptance": {
            "hosts": sorted(host["hosts"]),
            "scope": host["acceptance_scope"],
            "model_or_api_call_attempted": False,
            "model_task_acceptance": False,
            "passed": True,
        },
        "supply_chain": {
            "reproducible_wheel_sdist": True,
            "oci_manifest_digest": oci["inventory"]["manifest_digest"],
            "oci_non_root_no_listener": True,
            "dependency_audit_profiles": sorted(profiles),
            "sbom": sbom,
            "licenses": licenses,
            "openvex": openvex,
            "sigstore_oidc_required_by_release_workflow": True,
            "github_provenance_required_by_release_workflow": True,
            "exact_verified_bytes_required_by_release_workflow": True,
        },
        "commercial_gates": {
            "clean_final_commit": True,
            "version_0_7_0_unified": True,
            "three_os_no_skip_mandatory_suite": True,
            "distribution_install_upgrade_uninstall": True,
            "cli_migration_rollback_snapshot_restore": True,
            "mcp_stdio_and_read_only_tools": True,
            "security_corruption_lock_permissions": True,
            "windows_acl_junction_reparse": True,
            "real_no_model_host_lifecycle": True,
            "byte_reproducible_wheel_sdist": True,
            "non_root_networkless_oci": True,
            "sbom_license_audit_openvex": True,
            "documentation": docs,
        },
        "commercial_release_eligible": True,
        "competitive_claim_eligible": False,
        "competitive_evidence_missing": COMPETITIVE_EVIDENCE_MISSING,
        "claim_policy": {
            "commercial_ga_is_independent_from_competitive_leadership": True,
            "model_task_e2e_counted_as_completed": False,
            "static_or_lifecycle_checks_counted_as_model_acceptance": False,
            "best_sota_or_overall_leadership_claims_permitted": False,
        },
    }


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Assemble the DeepLaw commercial GA manifest.")
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--assets-root", type=Path, required=True)
    parser.add_argument("--platform", type=Path, action="append", required=True)
    parser.add_argument("--host", type=Path, required=True)
    parser.add_argument("--reproducible", type=Path, required=True)
    parser.add_argument("--oci-report", type=Path, required=True)
    parser.add_argument("--audit", type=Path, action="append", required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--licenses", type=Path, required=True)
    parser.add_argument("--openvex", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, default=946684800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = assemble(
            args.repository.resolve(),
            assets_root=args.assets_root.resolve(),
            platform_paths=[item.resolve() for item in args.platform],
            host_path=args.host.resolve(),
            reproducible_path=args.reproducible.resolve(),
            oci_report_path=args.oci_report.resolve(),
            audit_paths=[item.resolve() for item in args.audit],
            sbom_path=args.sbom.resolve(),
            licenses_path=args.licenses.resolve(),
            openvex_path=args.openvex.resolve(),
            source_date_epoch=args.source_date_epoch,
        )
        schema = load_json(
            args.repository.resolve()
            / "contracts/commercial-release-manifest.v1.schema.json"
        )
        Draft202012Validator.check_schema(schema)
        write_report(args.output.resolve(), report)
        Draft202012Validator(schema).validate(load_json(args.output.resolve()))
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
