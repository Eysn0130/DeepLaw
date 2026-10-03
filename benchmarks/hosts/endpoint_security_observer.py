"""Build and probe the native observation prerequisite before any formal Host run.

This owner-side component does not grant an entitlement or infer complete process
coverage from a PID snapshot. Missing native authority blocks the dependent run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
from pathlib import Path

SOURCE = Path(__file__).with_name("endpoint_security_probe.c")
RESULT_CODES = {
    0: "available",
    1: "invalid_argument",
    2: "internal_error",
    3: "endpoint_security_entitlement_missing",
    4: "endpoint_security_tcc_not_permitted",
    5: "endpoint_security_root_required",
    6: "endpoint_security_client_limit",
}


class ObservationUnavailable(RuntimeError):
    """A required native observation facility was not established."""


def validate_probe(value: object) -> dict[str, object]:
    fields = {
        "schema_version", "client_result", "reason_code", "running_as_root",
        "descendants_api_available", "cleanup_confirmed", "event_subscription_started",
        "host_started", "formal_admission",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ObservationUnavailable("native capability result shape differs")
    if value["schema_version"] != "deeplaw.endpoint-security-capability/v1":
        raise ObservationUnavailable("native capability version differs")
    code = value["client_result"]
    if type(code) is not int or value["reason_code"] != RESULT_CODES.get(code, "unknown_result"):
        raise ObservationUnavailable("native capability result code differs")
    for field in fields - {"schema_version", "client_result", "reason_code"}:
        if type(value[field]) is not bool:
            raise ObservationUnavailable("native capability boolean differs")
    forbidden = ("event_subscription_started", "host_started", "formal_admission")
    if any(value[name] for name in forbidden):
        raise ObservationUnavailable("capability probe crossed its observation boundary")
    if value["cleanup_confirmed"] is not True:
        raise ObservationUnavailable("native capability cleanup unconfirmed")
    return dict(value)


def require_observation_capability(value: object) -> None:
    result = validate_probe(value)
    if result["client_result"] != 0:
        raise ObservationUnavailable(str(result["reason_code"]))


def build_and_probe(destination: Path) -> dict[str, object]:
    if platform.system() != "Darwin":
        raise ObservationUnavailable("macOS Endpoint Security is required")
    destination = destination.absolute()
    if destination.resolve() != destination or destination.exists():
        raise ObservationUnavailable("probe destination must be fresh and canonical")
    destination.mkdir(mode=0o700)
    source = SOURCE.read_bytes()
    staged = destination / SOURCE.name
    staged.write_bytes(source)
    staged.chmod(0o600)
    binary = destination / "endpoint-security-probe"
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C"}
    build = subprocess.run(
        ["/usr/bin/xcrun", "clang", "-fblocks", "-Wall", "-Wextra", "-Werror",
         str(staged), "-lEndpointSecurity", "-o", str(binary)],
        env=environment, cwd=destination, capture_output=True, timeout=60, check=False,
    )
    if build.returncode != 0:
        raise ObservationUnavailable("native capability probe build failed")
    binary.chmod(0o700)
    before = binary.stat()
    binary_bytes = binary.read_bytes()
    run = subprocess.run(
        [str(binary)], env=environment, cwd=destination,
        capture_output=True, timeout=10, check=False,
    )
    after = binary.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ) or not stat.S_ISREG(after.st_mode) or after.st_uid != os.getuid():
        raise ObservationUnavailable("native capability executable changed")
    if run.returncode != 0 or run.stderr or len(run.stdout) > 4096:
        raise ObservationUnavailable("native capability probe did not return bounded success")
    result = validate_probe(json.loads(run.stdout))
    return {
        "observation": result,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "binary_sha256": hashlib.sha256(binary_bytes).hexdigest(),
        "os_version": platform.mac_ver()[0],
        "formal_admission": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build_and_probe(args.destination)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
