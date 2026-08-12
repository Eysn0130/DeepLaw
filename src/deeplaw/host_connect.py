from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator

from .api import KnowledgeOS
from .knowledge_autonomy import AutonomousKnowledgeStore
from .knowledge_maintenance import knowledge_doctor
from .util import strict_json_loads

HostName = Literal["codex", "claude-code", "opencode"]


def _permission_error_categories(permission_report: dict[str, Any]) -> list[str]:
    native = permission_report.get("native_windows_acl")
    errors = native.get("errors") if isinstance(native, dict) else None
    if not isinstance(errors, list):
        return []
    return sorted(
        {
            error.split(":", 1)[0]
            for error in errors
            if isinstance(error, str) and error
        }
    )[:16]


def _contract() -> dict[str, Any]:
    name = "host-connect-plan.v1.schema.json"
    packaged = Path(__file__).resolve().parent / "contracts" / name
    repository = Path(__file__).resolve().parents[2] / "contracts" / name
    path = packaged if packaged.is_file() else repository
    value = strict_json_loads(path.read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError("Host Connect Plan contract is invalid")
    return value


def _server_argv(vault_path: Path) -> list[str]:
    return [
        "deeplaw",
        "knowledge",
        "mcp",
        "--stdio",
        "--vault",
        str(vault_path),
    ]


def build_host_connect_plan(
    *, host: str, vault_path: str | Path
) -> dict[str, Any]:
    """Build one read-only MCP configuration without changing Host state."""

    if host not in {"codex", "claude-code", "opencode"}:
        raise ValueError("host must be codex, claude-code, or opencode")
    selected_host = cast(HostName, host)
    selected_vault = Path(vault_path).expanduser().absolute()
    doctor = knowledge_doctor(selected_vault)
    autonomous = doctor.get("checks", {}).get("autonomous_core", {})
    schema_core_installed = autonomous.get("installed") is True
    canonical_valid = doctor.get("canonical_valid") is True
    doctor_ready = doctor.get("ready") is True
    if not (doctor_ready and canonical_valid and schema_core_installed):
        permission_report = doctor.get("permissions", {})
        checks = doctor.get("checks", {})
        diagnostic = {
            "canonical_valid": canonical_valid,
            "schema_core_installed": schema_core_installed,
            "permissions_verified": permission_report.get("permissions_verified") is True,
            "permission_status": permission_report.get("status"),
            "permission_error_categories": _permission_error_categories(permission_report),
            "job_records_valid": checks.get("job_records_valid") is True,
            "invalid_inbox_artifact_count": len(
                checks.get("invalid_inbox_artifact_ids", [])
            ),
            "doctor_error_count": len(doctor.get("errors", [])),
        }
        raise RuntimeError(
            "Host connect requires a ready autonomous Knowledge vault: "
            f"{diagnostic}"
        )

    with AutonomousKnowledgeStore(selected_vault, read_only=True) as store:
        audit_before = store.audit_head
        scope = store.vault_scope
        compiled_knowledge_available = bool(
            store.connection.execute(
                """
                SELECT COUNT(*)
                FROM knowledge_objects_v3
                JOIN knowledge_revisions_v3
                  ON knowledge_revisions_v3.revision_id =
                     knowledge_objects_v3.current_revision_id
                WHERE knowledge_revisions_v3.lifecycle = 'active'
                  AND knowledge_revisions_v3.scope = ?
                  AND knowledge_revisions_v3.sensitivity != 'restricted'
                """,
                (scope,),
            ).fetchone()[0]
        )
        source_revision_count = int(
            store.connection.execute(
                """
                SELECT COUNT(DISTINCT source_revisions_v2.source_revision_id)
                FROM source_revisions_v2
                JOIN source_revision_bindings_v2 USING(source_revision_id)
                JOIN source_lifecycle
                  ON source_lifecycle.source_id =
                     source_revision_bindings_v2.legacy_source_id
                JOIN sources
                  ON sources.source_id = source_revision_bindings_v2.legacy_source_id
                WHERE source_lifecycle.status IN ('active', 'pending')
                  AND sources.sensitivity != 'restricted'
                """
            ).fetchone()[0]
        )
    context_error: Exception | None = None
    try:
        with KnowledgeOS.open(selected_vault) as knowledge_os:
            context = knowledge_os.context.compile(
                task="Verify the bounded read-only Host knowledge seam.",
                purpose="verify",
                scope=scope,
                max_sensitivity="private",
                limit=1,
                max_chars=200,
                max_tokens=128,
                max_sources=1,
                graph_hops=0,
                retrieval_mode="lexical",
                projection="compact",
                confirm_no_case_data=True,
            )
        with AutonomousKnowledgeStore(selected_vault, read_only=True) as store:
            audit_head_unchanged = store.audit_head == audit_before
        provider = context.get("provider_capsule", {})
        provider_delivery = provider.get("delivery", {})
        gaps = context.get("gaps", [])
        gap_codes = sorted(
            {
                str(gap["code"])
                for gap in gaps
                if isinstance(gap, dict) and isinstance(gap.get("code"), str)
            }
        )
        provider_payload_bytes = provider_delivery.get("provider_content_bytes")
        read_seam_callable = bool(
            context.get("schema_version") == "deeplaw.knowledge-capsule/v3"
            and provider.get("schema_version")
            == "deeplaw.provider-knowledge-capsule/v2"
            and context.get("write_performed") is False
            and provider_delivery.get("write_performed") is False
            and isinstance(provider_payload_bytes, int)
            and not isinstance(provider_payload_bytes, bool)
            and provider_payload_bytes <= 65_536
            and audit_head_unchanged
        )
    except Exception as error:
        context_error = error
        read_seam_callable = False
        audit_head_unchanged = False
        provider_payload_bytes = 0
        gap_codes = []
    source_only_honest_gap_available = bool(
        read_seam_callable
        and not compiled_knowledge_available
        and source_revision_count > 0
        and "uncompiled_source" in gap_codes
    )
    blocked = not read_seam_callable
    preflight = {
        "vault_ready": bool(doctor_ready and not blocked),
        "canonical_valid": canonical_valid,
        "autonomous_core_installed": schema_core_installed,
        "schema_core_installed": schema_core_installed,
        "read_seam_callable": read_seam_callable,
        "compiled_knowledge_available": compiled_knowledge_available,
        "source_only_honest_gap_available": source_only_honest_gap_available,
        "blocked": blocked,
    }
    if blocked:
        raise RuntimeError(
            "Host connect blocked: bounded read-only Context seam is not callable"
        ) from context_error
    context_status = (
        "compiled_knowledge"
        if compiled_knowledge_available
        else ("source_only_gap" if source_only_honest_gap_available else "empty_honest_gap")
    )
    context_preflight = {
        "status": context_status,
        "gap_codes": gap_codes,
        "provider_payload_bytes": provider_payload_bytes,
        "write_performed": False,
        "audit_head_unchanged": audit_head_unchanged,
        "attestation_scope": "fixed_internal_health_probe_only",
        "future_task_attested": False,
        "real_host_observed": False,
        "real_mcp_registration_observed": False,
        "caller_confirmation_required_for_future_context": True,
    }

    argv = _server_argv(selected_vault)
    plugin_manifest: dict[str, Any] | None = None
    if selected_host == "codex":
        configuration_kind = "codex_direct_config"
        configuration_format = "toml"
        merge_targets = ["~/.codex/config.toml", ".codex/config.toml"]
        configuration = {
            "mcp_servers": {
                "deeplaw-knowledge": {
                    "command": argv[0],
                    "args": argv[1:],
                }
            }
        }
        equivalent_command = [
            "codex",
            "mcp",
            "add",
            "deeplaw-knowledge",
            "--",
            *argv,
        ]
        verification_command = ["codex", "mcp", "list"]
        plugin_manifest = {
            "configuration_kind": "codex_plugin_manifest",
            "configuration_format": "json",
            "merge_target": ".codex-plugin/mcp.json",
            "configuration": {
                "deeplaw-knowledge": {
                    "command": argv[0],
                    "args": argv[1:],
                }
            },
        }
    elif selected_host == "claude-code":
        configuration_kind = "claude_code_project_config"
        configuration_format = "json"
        merge_targets = [".mcp.json"]
        configuration = {
            "mcpServers": {
                "deeplaw-knowledge": {
                    "command": argv[0],
                    "args": argv[1:],
                }
            }
        }
        equivalent_command = [
            "claude",
            "mcp",
            "add",
            "--transport",
            "stdio",
            "deeplaw-knowledge",
            "--",
            *argv,
        ]
        verification_command = ["claude", "mcp", "list"]
    else:
        configuration_kind = "opencode_project_config"
        configuration_format = "jsonc"
        merge_targets = ["opencode.json", "opencode.jsonc"]
        configuration: dict[str, Any] = {
            "mcp": {
                "deeplaw_knowledge": {
                    "type": "local",
                    "command": argv,
                    "enabled": True,
                    "timeout": 5000,
                }
            },
            "permission": {
                "*": "deny",
                "deeplaw_knowledge_knowledge_support": "allow",
            },
        }
        equivalent_command = []
        verification_command = ["opencode", "mcp", "list"]
    plan = {
        "schema_version": "deeplaw.host-connect-plan/v1",
        "host": selected_host,
        "server_leaf": "knowledge_support",
        "read_only": True,
        "configuration_kind": configuration_kind,
        "configuration_format": configuration_format,
        "merge_targets": merge_targets,
        "configuration": configuration,
        "equivalent_command": equivalent_command,
        "verification_command": verification_command,
        "preflight": preflight,
        "context_preflight": context_preflight,
        "merge_required": True,
        "authentication_managed": False,
        "host_runtime_managed": False,
        "install_performed": False,
        "write_performed": False,
    }
    if plugin_manifest is not None:
        plan["codex_plugin_manifest"] = plugin_manifest
    schema = _contract()
    Draft202012Validator.check_schema(schema)
    error = next(Draft202012Validator(schema).iter_errors(plan), None)
    if error is not None:
        raise RuntimeError(f"Host Connect Plan is invalid: {error.message}")
    return plan
