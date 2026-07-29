from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from benchmarks.release.evidence import (
    environment_manifest,
    file_record,
    repository_binding,
    sha256_bytes,
    write_report,
)

SCHEMA_VERSION = "deeplaw.oci-release-report/v1"


class OciError(RuntimeError):
    pass


def _safe_name(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise OciError(f"OCI archive contains an unsafe path: {value}")
    return path.as_posix()


def _json_bytes(value: bytes, *, field: str) -> Any:
    try:
        return json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciError(f"{field} is invalid JSON") from error


def _blob(archive: tarfile.TarFile, digest: str) -> bytes:
    algorithm, separator, value = digest.partition(":")
    if algorithm != "sha256" or separator != ":" or len(value) != 64:
        raise OciError(f"OCI descriptor has an invalid digest: {digest}")
    name = f"blobs/sha256/{value}"
    member = archive.getmember(name)
    stream = archive.extractfile(member)
    if stream is None:
        raise OciError(f"OCI blob is unreadable: {name}")
    content = stream.read()
    if sha256_bytes(content) != value:
        raise OciError(f"OCI blob digest differs: {name}")
    return content


def _oci_inventory(path: Path, *, commit: str, version: str) -> dict[str, Any]:
    with tarfile.open(path, "r:*") as archive:
        names = [_safe_name(item.name) for item in archive.getmembers()]
        if len(names) != len(set(names)):
            raise OciError("OCI archive contains duplicate paths")
        if any(item.issym() or item.islnk() for item in archive.getmembers()):
            raise OciError("OCI archive contains symbolic or hard links")
        index_stream = archive.extractfile("index.json")
        if index_stream is None:
            raise OciError("OCI archive has no index.json")
        index = _json_bytes(index_stream.read(), field="OCI index")
        manifests = index.get("manifests") if isinstance(index, dict) else None
        if not isinstance(manifests, list) or len(manifests) != 1:
            raise OciError("OCI index must identify exactly one image manifest")
        manifest_descriptor = manifests[0]
        manifest_digest = manifest_descriptor.get("digest")
        manifest_bytes = _blob(archive, manifest_digest)
        manifest = _json_bytes(manifest_bytes, field="OCI manifest")
        config_descriptor = manifest.get("config") if isinstance(manifest, dict) else None
        layers = manifest.get("layers") if isinstance(manifest, dict) else None
        if not isinstance(config_descriptor, dict) or not isinstance(layers, list) or not layers:
            raise OciError("OCI manifest has no config or layers")
        config_digest = config_descriptor.get("digest")
        config = _json_bytes(_blob(archive, config_digest), field="OCI image config")
        for layer in layers:
            if not isinstance(layer, dict):
                raise OciError("OCI manifest contains a non-object layer")
            _blob(archive, layer.get("digest"))

    runtime = config.get("config") if isinstance(config, dict) else None
    if not isinstance(runtime, dict):
        raise OciError("OCI runtime config is unavailable")
    labels = runtime.get("Labels")
    if not isinstance(labels, dict):
        raise OciError("OCI labels are unavailable")
    expected_labels = {
        "org.opencontainers.image.version": version,
        "org.opencontainers.image.revision": commit,
        "org.opencontainers.image.source": "https://github.com/Eysn0130/DeepLaw",
        "org.opencontainers.image.licenses": "Apache-2.0",
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        raise OciError("OCI labels do not bind the release commit and version")
    if runtime.get("User") != "65532:65532":
        raise OciError("OCI image does not use the fixed non-root identity")
    if runtime.get("ExposedPorts") not in (None, {}):
        raise OciError("OCI image exposes a network port")
    if runtime.get("Entrypoint") != ["deeplaw"] or runtime.get("Cmd") != ["--version"]:
        raise OciError("OCI image does not default to the bounded CLI version command")
    if runtime.get("WorkingDir") != "/data":
        raise OciError("OCI image working directory is not the local data root")
    return {
        "manifest_digest": manifest_digest,
        "config_digest": config_digest,
        "layer_digests": [item["digest"] for item in layers],
        "archive_path_count": len(names),
        "non_root_user": runtime["User"],
        "exposed_ports": [],
        "entrypoint": runtime["Entrypoint"],
        "command": runtime["Cmd"],
        "labels": expected_labels,
    }


def _container_evidence(path: Path, *, config_digest: str, version: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise OciError("container inspect evidence is invalid")
    record = payload[0]
    host = record.get("HostConfig")
    config = record.get("Config")
    image = record.get("Image")
    if not isinstance(host, dict) or not isinstance(config, dict):
        raise OciError("container inspect evidence lacks runtime configuration")
    if image != config_digest:
        raise OciError("runtime container uses different image config bytes")
    if (
        host.get("NetworkMode") != "none"
        or host.get("ReadonlyRootfs") is not True
        or "ALL" not in (host.get("CapDrop") or [])
        or not any("no-new-privileges" in item for item in (host.get("SecurityOpt") or []))
    ):
        raise OciError("runtime container is not networkless and least-privilege")
    if config.get("User") != "65532:65532" or config.get("ExposedPorts") not in (None, {}):
        raise OciError("runtime container does not preserve non-root/no-port configuration")
    runtime_output = path.with_name("runtime-output.txt")
    if runtime_output.read_text(encoding="utf-8").strip() != f"deeplaw {version}":
        raise OciError("OCI runtime CLI version check failed")
    return {
        "image_config_digest": image,
        "network_mode": host["NetworkMode"],
        "read_only_rootfs": host["ReadonlyRootfs"],
        "cap_drop_all": True,
        "no_new_privileges": True,
        "runtime_output": file_record(runtime_output, logical_name="runtime-output.txt"),
    }


def verify(
    repository: Path,
    *,
    archive: Path,
    requirements: Path,
    container_inspect: Path,
) -> dict[str, Any]:
    binding = repository_binding(repository)
    if not binding["worktree_clean"]:
        raise OciError("OCI gate requires a clean release commit")
    inventory = _oci_inventory(
        archive,
        commit=binding["commit"],
        version=binding["package_version"],
    )
    container = _container_evidence(
        container_inspect,
        config_digest=inventory["config_digest"],
        version=binding["package_version"],
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "binding": binding,
        "environment": environment_manifest(),
        "oci_archive": file_record(archive, logical_name=archive.name),
        "runtime_requirements": file_record(
            requirements, logical_name="runtime-requirements.txt"
        ),
        "inventory": inventory,
        "container_runtime": container,
        "gates": {
            "non_root": True,
            "no_exposed_ports": True,
            "default_command_does_not_listen": True,
            "runtime_network_none": True,
            "runtime_read_only_rootfs": True,
            "runtime_capabilities_dropped": True,
            "runtime_no_new_privileges": True,
            "exact_commit_and_version_labels": True,
        },
        "passed": True,
    }


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Verify the DeepLaw release OCI archive and runtime."
    )
    parser.add_argument("--repository", type=Path, default=repository)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--container-inspect", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(
            args.repository.resolve(),
            archive=args.archive.resolve(),
            requirements=args.requirements.resolve(),
            container_inspect=args.container_inspect.resolve(),
        )
        write_report(args.output.resolve(), report)
    except (OSError, RuntimeError, tarfile.TarError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
