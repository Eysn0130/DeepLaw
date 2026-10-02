"""Isolated MCP entry for the frozen zero-model native preflight."""

import json
import os
import sys
from pathlib import Path


def main() -> None:
    sys.path.insert(0, "/runtime")
    import boundary_gate
    import transport

    boundary_gate.run()
    connection = transport.mcp_stdio_endpoint("/work/mcp.sock", allowed_peer_uid=0)
    os.dup2(connection.fileno(), 0)
    os.dup2(connection.fileno(), 1)
    connection.close()
    # Keep the original owners alive and replace their cached /dev/null IO state.
    _stdio_owners = (sys.stdin, sys.stdout)
    sys.stdin = os.fdopen(os.dup(0), "r", encoding="utf-8")
    sys.stdout = os.fdopen(os.dup(1), "w", encoding="utf-8")
    sys.path[:0] = ["/runtime/site-packages", "/runtime/modules"]
    from benchmarks.hosts import maintenance_task_mcp as maintenance

    owner = json.loads(Path("/runtime/owner-input.json").read_bytes())
    model_probe = owner.get("purpose") == "native_model_probe"
    if set(owner) != {"purpose", "run_id", "candidate_id"} | (
        {"provider_nonce"} if model_probe else set()
    ):
        raise ValueError("owner_input_invalid")
    if owner["purpose"] not in {
        "zero_model_preflight", "zero_model_fork_preflight", "native_model_probe",
    }:
        raise ValueError("owner_purpose_invalid")
    capsule = {}
    binding = maintenance.make_owner_binding(
        "no_memory",
        "source_update",
        run_id=owner["run_id"],
        candidate_id=owner["candidate_id"],
        context_id="public-zero-model-context",
        capsule_digest=maintenance.provider_capsule_digest(capsule),
    )
    maintenance.run_mcp(binding, trace_path="/work/task-trace.json", provider_capsule=capsule)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        Path("/work/entry-failure.json").write_text(json.dumps({"type": type(error).__name__}))
        raise
